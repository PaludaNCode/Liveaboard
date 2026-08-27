"""Deriving classification from the data rather than hand-tagging it.

Operators describe the same route in a dozen different ways — "Simply the Best",
"Ultimate Red Sea", "BDE", "Southern Sharks" — but they all list their dive
sites, and the sites don't lie. So route, theme and entry bar are inferred from
the site list, with any explicit value in the dataset taking precedence.

This is what makes the site a genuine reclassification rather than a reprint of
somebody else's categories.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .models import Itinerary
from .taxonomy import DiverLevel, Route, Theme


def normalise(name: str) -> str:
    """Fold a dive-site name to a comparable key.

    Egyptian site names arrive transliterated a dozen ways — "Sha'ab", "Shaab",
    "Shaʿb"; "St John's", "St. Johns", "Saint Johns" — so punctuation and
    accents are stripped rather than trusted.
    """
    folded = unicodedata.normalize("NFKD", name)
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    folded = folded.lower().replace("&", " and ")
    # Apostrophes are dropped rather than spaced, so "Sha'ab" and "Shaab" agree
    # and "St John's" matches "St Johns". Spacing them apart would split one
    # word into two and quietly break every signature that contains one.
    #
    # The acute accent and the left single quote are in this class because
    # operators type them for an apostrophe: a live title read "St. John´s"
    # (U+00B4) and folded to "st john s", so the St John's route went
    # unrecognised on two of four vessels.
    folded = re.sub(r"['’‘ʿʼ`´]", "", folded)
    folded = re.sub(r"[^a-z0-9]+", " ", folded)
    return re.sub(r"\s+", " ", folded).strip()


ROUTE_SIGNATURES: dict[Route, tuple[str, ...]] = {
    Route.NORTH_WRECKS_REEFS: (
        "thistlegorm", "abu nuhas", "giannis d", "carnatic", "chrisoula k",
        "kimon m", "rosalie moller", "ulysses", "dunraven", "shaab ali",
        "small crack", "bluff point", "gubal",
    ),
    Route.RAS_MOHAMMED_TIRAN: (
        "ras mohammed", "shark reef", "yolanda", "jackson", "woodhouse",
        "thomas reef", "gordon reef", "tiran", "shark and yolanda",
    ),
    Route.BDE: (
        "big brother", "little brother", "brothers", "daedalus", "elphinstone",
        "numidia", "aida",
    ),
    Route.ST_JOHNS: (
        "st johns", "saint johns", "gota kebir", "gota soraya", "umm kharerim",
        "habili ali", "habili gaffar", "dangerous reef", "cave reef",
    ),
    Route.DEEP_SOUTH: (
        "rocky island", "zabargad", "st johns", "habili ali", "dangerous reef",
    ),
    Route.FURY_SHOAL: (
        "fury shoal", "sataya", "malahi", "claudia", "abu galawa",
        "shaab maksur", "dolphin house", "samadai",
    ),
}

ROUTE_FAMILY: dict[Route, str] = {
    Route.NORTH_WRECKS_REEFS: "north",
    Route.RAS_MOHAMMED_TIRAN: "sinai",
    Route.BDE: "offshore",
    Route.DEEP_SOUTH: "south",
    Route.ST_JOHNS: "south",
    Route.FURY_SHOAL: "south",
}
"""Which cruising ground each route belongs to.

Route names overlap heavily in the south — nearly every St John's week also
touches Habili Ali and Dangerous Reef — so the families exist to stop two
labels for the same stretch of water from looking like two destinations.
"""

FAMILY_PRECEDENCE: tuple[Route, ...] = (
    Route.DEEP_SOUTH,
    Route.ST_JOHNS,
    Route.FURY_SHOAL,
    Route.BDE,
    Route.NORTH_WRECKS_REEFS,
    Route.RAS_MOHAMMED_TIRAN,
)
"""Tie-break order within a family, most general first.

"Deep South" is the umbrella the industry actually uses for the Rocky Island,
Zabargad and St John's region, so it wins a tie against the narrower St John's.
"""

ROUTE_PILLARS: dict[Route, tuple[tuple[str, ...], ...]] = {
    Route.BDE: (
        # Numidia and Aida are wrecks on Big Brother, so naming either is
        # naming the Brothers.
        ("big brother", "little brother", "brothers", "numidia", "aida"),
        ("daedalus",),
        ("elphinstone",),
    ),
}
"""Routes that are a named set of places, not a score.

BDE *is* Brothers, Daedalus and Elphinstone. A week reaching two of the three
is not a weaker BDE, it is a different trip -- so every pillar has to be
present or the route is not a candidate at all.

Counting instead of requiring got this wrong in a way that flipped on a single
word. "Daedalus & St. John's" read as deep south, correctly; adding Elphinstone
to the same trip gave the offshore family two hits against the south's one and
tipped it to BDE, even though St John's is 150 nautical miles further south and
is what the week is actually for.

Eighteen itineraries name two pillars. Those that also name a southern site now
read as southern, which is what they are; the handful naming only Daedalus and
Elphinstone come out unclassified, because there is no honest label for an
offshore pair and the dive-site column already says exactly where they go.
"""

MIN_FAMILY_MATCHES = 2
"""Site hits before a family counts as genuinely visited rather than passed."""

MIN_FAMILIES_FOR_COMBINATION = 3
"""Distinct cruising grounds that make a trip a combination itinerary.

Two is ordinary — almost every northern week adds a Ras Mohammed day. Three is
a different product: the ten-night one-way runs that cross the whole coast.
"""

THEME_SIGNATURES: dict[Theme, tuple[str, ...]] = {
    Theme.SHARKS_PELAGIC: (
        "daedalus", "big brother", "little brother", "brothers", "elphinstone",
        "rocky island", "habili ali", "dangerous reef",
    ),
    Theme.WRECKS: (
        "thistlegorm", "abu nuhas", "giannis d", "carnatic", "chrisoula k",
        "kimon m", "rosalie moller", "numidia", "aida", "salem express",
        "dunraven", "ulysses",
    ),
    Theme.HAMMERHEADS: ("daedalus", "big brother", "little brother", "brothers"),
    Theme.OCEANIC_WHITETIP: ("elphinstone", "daedalus", "big brother", "little brother"),
    Theme.DOLPHINS: ("sataya", "samadai", "dolphin house", "fury shoal", "abu galawa"),
    Theme.CURRENT: (
        "daedalus", "big brother", "little brother", "elphinstone", "tiran",
        "jackson", "woodhouse", "rocky island", "habili ali",
    ),
    Theme.MACRO: ("fury shoal", "malahi", "claudia", "abu galawa", "marsa"),
    Theme.REEF: (
        "fury shoal", "st johns", "gota kebir", "sataya", "shaab",
        "elphinstone", "daedalus",
    ),
}

SHARK_THEMES = frozenset({Theme.HAMMERHEADS, Theme.OCEANIC_WHITETIP})

DEMANDING_ROUTES = frozenset({Route.BDE, Route.DEEP_SOUTH, Route.ST_JOHNS})
"""Routes whose sites are offshore, deep and current-swept as a matter of course."""


SEASONAL_PEAKS: dict[Theme, tuple[int, ...]] = {
    Theme.HAMMERHEADS: (6, 7, 8),
    Theme.OCEANIC_WHITETIP: (5, 6, 10, 11),
    Theme.DOLPHINS: (5, 6, 7, 8, 9),
}
"""Months when a theme is actually at its best, so a May trip is not sold on
August's hammerheads.

Only themes listed here can ever be flagged "in season". A theme with no entry
has no season worth announcing — wrecks and reefs are there all year, and
badging them would drown the two windows that genuinely move."""


def _matches(sites: list[str], signatures: tuple[str, ...]) -> int:
    keys = [normalise(s) for s in sites]
    return sum(1 for sig in signatures if any(sig in key for key in keys))


def infer_route(itinerary: Itinerary) -> Route | None:
    """Pick the route the dive-site list actually describes.

    Scoring happens per route, but the combination test happens per *family*:
    counting routes directly would call every southern week a combination,
    because St John's and Deep South share most of their sites.
    """
    if itinerary.route is not None:
        return itinerary.route
    if not itinerary.dive_sites:
        return None

    scores = {
        route: _matches(itinerary.dive_sites, sigs)
        for route, sigs in ROUTE_SIGNATURES.items()
    }
    if not any(scores.values()):
        return None

    def best(among: dict[Route, int]) -> dict[str, tuple[int, Route]]:
        chosen: dict[str, tuple[int, Route]] = {}
        for route, score in among.items():
            if not score:
                continue
            family = ROUTE_FAMILY[route]
            rank = -FAMILY_PRECEDENCE.index(route)
            current = chosen.get(family)
            if current is None or (score, rank) > (
                current[0], -FAMILY_PRECEDENCE.index(current[1])
            ):
                chosen[family] = (score, route)
        return chosen

    # Where a trip has *been* and what it should be *called* are different
    # questions, and the pillar rule only answers the second. A week naming
    # Brothers and Daedalus was offshore whether or not it earns the BDE label,
    # so the combination count is taken before the rule is applied -- otherwise
    # a genuine north-offshore-south crossing loses a whole cruising ground and
    # stops reading as a combination.
    visited = [f for f, (score, _) in best(scores).items() if score >= MIN_FAMILY_MATCHES]
    if len(visited) >= MIN_FAMILIES_FOR_COMBINATION:
        return Route.COMBINATION

    # A route defined by a named set is all-or-nothing. Scored like the others,
    # two of BDE's three places outrank one southern site and a St John's week
    # gets badged as an offshore one. See ROUTE_PILLARS.
    eligible = dict(scores)
    for route, pillars in ROUTE_PILLARS.items():
        if not all(_matches(itinerary.dive_sites, pillar) for pillar in pillars):
            eligible[route] = 0
    if not any(eligible.values()):
        return None

    _, winner = max(
        best(eligible).values(),
        key=lambda pair: (pair[0], -FAMILY_PRECEDENCE.index(pair[1])),
    )
    return winner


def infer_themes(itinerary: Itinerary, route: Route | None) -> list[Theme]:
    """Derive themes from sites, then add the umbrella shark tag if earned."""
    if itinerary.themes:
        return list(itinerary.themes)

    found = {
        theme
        for theme, sigs in THEME_SIGNATURES.items()
        if _matches(itinerary.dive_sites, sigs)
    }
    if found & SHARK_THEMES:
        found.add(Theme.SHARKS_PELAGIC)
    if route is Route.NORTH_WRECKS_REEFS:
        found.add(Theme.WRECKS)
    return sorted(found, key=lambda t: t.value)


def infer_level(itinerary: Itinerary, route: Route | None) -> DiverLevel:
    """The realistic entry bar.

    An explicit requirement in the dataset always wins. Otherwise the offshore
    routes carry the industry-standard bar of Advanced plus fifty logged dives.
    """
    stated = itinerary.requirements
    if stated.min_logged_dives >= 100:
        return DiverLevel.EXPERIENCED_100
    if stated.min_logged_dives >= 50:
        return DiverLevel.ADVANCED_50
    if stated.min_level is not DiverLevel.OPEN_WATER:
        return stated.min_level
    if route in DEMANDING_ROUTES:
        return DiverLevel.ADVANCED_50
    return DiverLevel.OPEN_WATER


def themes_in_season(themes: list[Theme], month: int) -> list[Theme]:
    """Themes genuinely peaking in the given month.

    Only themes with a defined season are eligible. A wreck is a wreck in
    February, so labelling one "in season" would be noise dressed up as
    information — and would bury the hammerhead window that does matter.
    """
    return [t for t in themes if month in SEASONAL_PEAKS.get(t, ())]


@dataclass(frozen=True, slots=True)
class Classification:
    """The derived labels for one itinerary."""

    route: Route | None
    themes: list[Theme]
    level: DiverLevel

    def as_dict(self) -> dict[str, object]:
        return {
            "route": self.route.value if self.route else None,
            "themes": [t.value for t in self.themes],
            "level": self.level.value,
        }


def classify(itinerary: Itinerary) -> Classification:
    """Run the full derivation for one itinerary."""
    route = infer_route(itinerary)
    return Classification(
        route=route,
        themes=infer_themes(itinerary, route),
        level=infer_level(itinerary, route),
    )
