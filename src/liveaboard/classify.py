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
    folded = re.sub(r"['’ʿʼ`]", "", folded)
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

    best_per_family: dict[str, tuple[int, Route]] = {}
    for route, score in scores.items():
        if not score:
            continue
        family = ROUTE_FAMILY[route]
        rank = -FAMILY_PRECEDENCE.index(route)
        current = best_per_family.get(family)
        if current is None or (score, rank) > (current[0], -FAMILY_PRECEDENCE.index(current[1])):
            best_per_family[family] = (score, route)

    visited = [f for f, (score, _) in best_per_family.items() if score >= MIN_FAMILY_MATCHES]
    if len(visited) >= MIN_FAMILIES_FOR_COMBINATION:
        return Route.COMBINATION

    _, winner = max(
        best_per_family.values(),
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
