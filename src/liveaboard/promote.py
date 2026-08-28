"""Turn a candidate scrape into a dataset the site can render.

Promotion is deliberately a separate step from scraping. A source site changing
its markup should degrade into "yesterday's prices plus a warning", never into
a published page full of blanks, and that only holds if something has to
actively decide the new data is good enough.

What the scrape gives us is boats, dates and prices. What it does not give us
is fees — and that gap is the whole reason this module is careful. An itinerary
with no fee lines is not an itinerary with no fees; it is one we have not
looked at yet. Those two must never render the same way, or a site built to
expose hidden costs would be hiding them itself.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date
from typing import Any, Mapping, Sequence

from .classify import normalise
from .taxonomy import DiverLevel, FeeBasis, FeeCode, FeeTier, SourceKind

UNKNOWN_OPERATOR = {
    "id": "unknown-operator",
    "name": "Operator not captured",
}
"""For a departure whose source published no organizer.

This used to be every departure in the dataset -- 317 of 317 -- on the belief
that an agency listing names the vessel and not who runs it. It names both:
every ``Event`` node carries ``organizer.name``. The field was parsed and
dropped, so the fallback became the only answer.

It stays, because a source that says nothing must not be given an invented
company. On current evidence it is never reached.
"""

OPERATOR_ALIASES: dict[str, str] = {
    # A missing space, in the operator's own listing. Both halves are real
    # fleets under one company, and "Aggressor Fleet& Dancer Fleet" is 50
    # departures filed under a typo.
    "aggressor fleet& dancer fleet": "Aggressor Fleet & Dancer Fleet",
}
"""Operator names that are one company under more than one spelling.

Folded the way :data:`PORT_ALIASES` folds harbours: deliberately, in a table,
with a comment saying why. Not by fuzzy matching -- "Red Sea Explorers" and
"Red Sea Relax" are two companies four characters apart, and a similarity
threshold that merges the Aggressor typo also merges those.

Names are otherwise kept **verbatim**. "XPLORER AQUARIUS Safari" is shouted and
"MV Legends II" is a vessel name doing duty as a company, but both are what the
operator publishes, and tidying someone's capitalisation is a short step from
deciding what they are called.

Keys are matched lowercased with whitespace already collapsed by the scraper.
"""


MIN_NIGHTS = 1
MAX_NIGHTS = 30
"""Sanity bounds. A departure outside these is a parsing error, not a cruise."""


def slugify(value: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", value.lower())).strip("-")


def operator_record(name: str) -> dict[str, str]:
    """One operator, as the dataset stores it.

    The id is a slug of the folded name, so two spellings of one company reach
    one record and one page grouping.
    """
    folded = OPERATOR_ALIASES.get(name.lower(), name)
    return {"id": slugify(folded) or UNKNOWN_OPERATOR["id"], "name": folded}


PROMOTION = re.compile(r"^\s*(\d{1,2}\s*%\s*off)\s*:\s*", re.I)
"""A discount banner the operator glues to the front of a trip title.

Every scraped title carrying one has a twin without it — "20% Off: North &
Tiran (Hurghada - Hurghada)" and "North & Tiran (Hurghada - Hurghada)" are the
same week on the same boat, differing only in which departures were on offer.
Grouping on the raw title split them into two cards, so a vessel appeared to
sell routes it does not and the discounted departures sat under a separate
heading from the rest.

The discount is real price information, so it moves to the departures it
applies to rather than being dropped. The route name stays a route name:
carrying an operator's marketing into our own headline is the opposite of what
this site is for.
"""

PORTS = re.compile(r"\(([^()]{2,60}?)\s+[-–]\s+([^()]{2,60}?)\)\s*$")
"""The departure and return ports, which the title states and the data does not.

liveaboard.com's Event location is the country — every itinerary promoted as
"Egypt → Egypt", which tells a visitor nothing. The title ends with the real
pair: "(Port Ghalib - Safaga/Soma Bay)". That distinction decides which
airport someone flies into and whether they need two of them, so it is worth
reading off the only place the source puts it.
"""


PORT_ALIASES: dict[str, str] = {
    # Port Ghalib's marina is written three ways across the fleet. "Marsa" is
    # Arabic for the harbour itself, and Ras Galep is the headland it sits on.
    "marsa ghalib": "Port Ghalib",
    "ras galep | port ghalib": "Port Ghalib",
    # A hotel pickup point, not a port.
    "hurghada, marriott": "Hurghada",
    # Soma Bay is a resort bay ten kilometres up the coast from Safaga, and the
    # operator names both because it uses whichever berth it is given.
    "safaga/soma bay": "Safaga",
}
"""Ports that are one place under several spellings.

Left unmerged they made ten filter chips out of six real harbours, and split
the departures leaving from one marina across three of them -- which is worse
than cosmetic on a filter whose whole job is "which airport do I fly into".

Deliberately narrow. Marsa Alam is sixty kilometres south of Port Ghalib and
stays its own port, however similar the names look.
"""


def _port(name: str | None) -> str:
    """Fold an operator's spelling of a harbour onto one name."""
    if not name:
        return "Unknown"
    cleaned = " ".join(name.split())
    return PORT_ALIASES.get(cleaned.lower(), cleaned)


TIDY = (
    # Tabs and runs of spaces. Four titles carry a tab, which renders as a
    # ragged gap mid-name and breaks the column's alignment.
    (re.compile(r"\s+"), " "),
    # A space wedged before the closing bracket: "(Safaga - Safaga )".
    (re.compile(r"\s+\)"), ")"),
    (re.compile(r"\(\s+"), "("),
    # One dash. Three titles use an en dash where 314 use a hyphen, so the
    # same route reads as two different names down the column.
    (re.compile(r"\s*[–—]\s*"), " - "),
    # Spacing around the separators operators are inconsistent about.
    (re.compile(r"\s*&\s*"), " & "),
    (re.compile(r"\s*,\s*"), ", "),
    (re.compile(r"\s*:\s*"), ": "),
)
"""Whitespace and punctuation fixes, applied before anything reads the title.

Purely presentational: none of these change a word. They exist because the
titles arrive from dozens of operators typing freely, and a column of trip
names only reads as a column when the separators are the same shape.
"""


def _tidy(name: str) -> str:
    """Even out an operator's spacing without touching their wording."""
    for pattern, replacement in TIDY:
        name = pattern.sub(replacement, name)
    return name.strip(" -,:")


def _split_title(name: str) -> tuple[str, str | None, tuple[str, str] | None]:
    """Split a scraped trip title into route, promotion and ports."""
    name = _tidy(name)
    promotion = None
    match = PROMOTION.match(name)
    if match:
        promotion = " ".join(match.group(1).split())
        name = name[match.end():].strip()

    ports = None
    match = PORTS.search(name)
    if match:
        first, second = (" ".join(p.split()) for p in match.groups())
        # A parenthetical is only a port pair when it reads like one. "(Brothers
        # - Daedalus)" is a route, and filing Daedalus as a harbour would put a
        # reef on the page as somewhere to fly into.
        #
        # Some names are both. Safaga and Dahab are harbours you sail from and
        # stretches of reef you dive, so a plain "does this contain a dive
        # site" test rejected "(Port Ghalib - Safaga/Soma Bay)" as a route and
        # threw away a real port pair. Names that are ports in their own right
        # are set aside before the question is asked.
        if not _sites_from_name(_without_ports(f"{first} {second}")):
            ports = (first, second)
    return name.strip(), promotion, ports


def itinerary_key(slug: str, name: str) -> str:
    """One itinerary, identified as ``promote`` groups them: vessel + trip name.

    Departures are grouped by ``(slug, name)`` below, so anything read per
    *trip* rather than per sailing -- the itinerary book that
    ``tools/fetch_itineraries.py`` writes -- has to key on the same pair or it
    finds nothing.

    Put through :func:`_split_title` rather than compared raw, because the two
    sides do not spell a trip the same way. The book is built from archived
    ``Event`` names, which carry the operator's discount banner -- "20% Off:
    Ultimate Red Sea (Port Ghalib - Hurghada)" -- and the promoted name has
    already had it removed, because a week on sale is the same week. Keyed on
    the raw string, 71 of 314 itineraries matched nothing and the fetcher spent
    97 requests re-reading trips under their banner spellings.

    Exported so the fetcher imports it rather than reimplementing it. Two
    copies of this rule drifting apart is a book that silently matches nothing,
    and every field it fills has a fallback, so nothing would fail loudly.
    """
    trip, _, _ = _split_title(name)
    return f"{slug}::{trip}"


BDE = re.compile(
    r"""^\s*
        brother(?:s)?(?:\s+islands?)?   # Brother / Brothers / Brother Islands
        \s*(?:,|&|and|-|–|—|\+|\s)\s*
        daedalus
        \s*(?:,|&|and|-|–|—|\+|\s)\s*
        elphinstone
        \s*$""",
    re.I | re.X,
)
"""The one route the fleet writes seven different ways.

    Brother - Daedalus - Elphinstone
    Brother Islands - Daedalus - Elphinstone
    Brother Islands, Daedalus & Elphinstone
    Brothers - Daedalus - Elphinstone
    Brothers, Daedalus & Elphinstone
    Brothers, Daedalus and Elphinstone
    Brothers, Daedalus, Elphinstone

Seven spellings of one week, sitting next to each other in the widest column
on the page, and a visitor comparing them has to notice that they are the same
trip before they can compare anything else.

Deliberately only this route. Twelve other groups differ the same way -- North
& Brothers against North - Brothers, and so on -- and are left exactly as their
operators wrote them. A rule that rewrote every title would be a house style
imposed on somebody else's words, and it would eventually merge two trips that
only look alike. This matches one route, in one order, and rewrites nothing
else: the pattern is anchored at both ends, so "Brothers, Daedalus &
Elphinstone + Safaga" does not match and is left alone.

Never touches ``Itinerary.name``. The id is built from the name and
``data/itineraries.json`` keys on it, so the operator's own wording stays the
identity and this is presentation only.
"""

BDE_TITLE = "Brothers, Daedalus & Elphinstone"
"""House style: commas, then an ampersand before the last."""


TITLE_FIXES = (
    # Zero-width spaces, pasted in from wherever the title was written. Two
    # titles carry them -- "Red Sea Charm[]:" and "Sataya[][] (Fury Shoals)" --
    # invisible on the page and not invisible to anything else: they defeat a
    # search for the words either side and sort as though they were there.
    # Removed rather than spaced, because they are not spaces.
    (re.compile(r"[​‌‍﻿]"), ""),
    # One apostrophe. The fleet writes St John's with a typewriter quote, a
    # curly quote and an acute accent -- three characters for one reef, so the
    # same saint sorts in three places and matches in one.
    (re.compile(r"[‘’´]"), "'"),
    # Daedalus, twice misspelled: "Daedulus" and "Deadalus". Listed rather
    # than matched loosely, the same way PORT_ALIASES folds harbours -- a
    # near-miss rule that catches these also catches a reef that only looks
    # like another. Both trips already carry `daedalus` in their dive sites,
    # read from the operator's own description, so this corrects the spelling
    # of something the dataset has independently confirmed.
    (re.compile(r"\b(?:daedulus|deadalus)\b", re.I), "Daedalus"),
)
"""Errors in a title, as opposed to a style we happen not to share.

The distinction is the whole reason this is a short list. Separators, word
order and the fleet's several spellings of a reef are the operators' own and
are left alone -- see the note on ``BDE``. These three are things nobody
intended: an invisible control character, three characters doing one
apostrophe's job, and a reef with its letters swapped.

Applied to the display title only. ``Itinerary.name`` keeps the operator's
text verbatim because the id is built from it and ``itinerary_key`` matches
the per-trip book on it, so correcting a name would silently re-key the trips
it corrected -- and what the operator published is, after all, the identity.
"""


def _fix_title_errors(title: str) -> str:
    """Correct what is wrong in a title, never what is merely different."""
    for pattern, replacement in TITLE_FIXES:
        title = pattern.sub(replacement, title)
    return title


def _settle_title_case(itineraries: list[dict[str, Any]]) -> None:
    """One spelling per title, where the only difference is capitalisation.

    Emperor Divers sells "Simply The Best" and "Simply the Best" -- two real
    trips with different ports, written two ways, and the page prints both a
    row apart as though the difference meant something.

    The chosen spelling is always **one the operator actually used**: the most
    common of them, alphabetical on a tie so the dataset is reproducible.
    Nothing is title-cased and no word is changed, which is what keeps this
    safe on names full of things a casing rule would ruin -- "MY Odyssey",
    "St. John's", "SS Turkia".

    Case only. A title differing by a word is a different title and is left
    alone, the same way the route rewriting is confined to one route.
    """
    spellings: dict[str, Counter[str]] = defaultdict(Counter)
    for itinerary in itineraries:
        spellings[itinerary["title"].casefold()][itinerary["title"]] += 1
    settled = {
        folded: min(counts.most_common(), key=lambda kv: (-kv[1], kv[0]))[0]
        for folded, counts in spellings.items()
    }
    for itinerary in itineraries:
        itinerary["title"] = settled[itinerary["title"].casefold()]


def _display_title(name: str) -> str:
    """The trip name with its port pair removed, for the column that shows it.

    Titles end with the ports -- "North & Tiran (Hurghada - Hurghada)" -- and
    From and To are columns of their own, so printing the brackets is the same
    fact twice in the widest column on the page.

    Cut here rather than in the browser. The page used to strip the suffix by
    checking the bracket text against ``port_from``, which held while the two
    were the same string and would have broken the moment ``PORT_ALIASES``
    started folding "Ras Galep | Port Ghalib" down to "Port Ghalib": the
    comparison fails, and seven titles quietly get their ports back. Deciding
    it beside the alias table, where the raw pair is still in hand, is the
    difference between a rule and a coincidence.

    Only a real port pair goes. "(Brothers - Daedalus)" is a route, and cutting
    it would delete what the trip actually is.
    """
    stripped, _, ports = _split_title(name)
    if ports is not None:
        stripped = PORTS.sub("", stripped).strip(" -,:") or stripped
    stripped = _fix_title_errors(stripped)
    return BDE_TITLE if BDE.match(stripped) else stripped


GUESTS = (
    re.compile(r"\bfor\s+(\d{1,3})\s+guests?\b", re.I),
    re.compile(r"\b(\d{1,3})\s+guests?\b", re.I),
    re.compile(r"\b(\d{1,3})\s+passengers?\b", re.I),
    re.compile(r"\baccommodat\w*\s+(?:up\s+to\s+)?(\d{1,3})\b", re.I),
)
"""How a vessel description states how many people it carries.

Berth price is per person, so the guest count is what tells a diver whether
they are buying into a boat of twelve or of thirty-four -- a difference in
group size, dive-deck crowding and how the same reef feels.

Read from the vessel description, which is the only place the scrape currently
has it. That covers about half the fleet: the number also sits in the page's
specification table, which nothing parses yet, so a vessel without one here has
not been asked rather than declined to say.
"""

MAX_GUESTS = 60
"""Above this the match is not a guest count.

Liveaboards in this fleet run from eight to thirty-four berths. A larger number
in the same sentence is a length in feet, a year, or a price.
"""


def _guests(summary: str | None) -> int | None:
    """Pull a guest count out of a vessel description, or admit there is none."""
    if not summary:
        return None
    for pattern in GUESTS:
        match = pattern.search(summary)
        if match:
            value = int(match.group(1))
            if 0 < value <= MAX_GUESTS:
                return value
    return None


def _dives(
    stated: int | None = None,
    *,
    nights: int | None = None,
    for_nights: int | None = None,
) -> int:
    """The operator's own dive count, or ``0`` when it does not publish one.

    This used to work the count out from nights at three dives a full day, and
    the arithmetic was defensible -- eighteen for a seven-night week is what the
    vessels that do state a figure publish. The problem was never accuracy.

    Price per dive is ``total / dives``, and at a fixed rate per night that is
    ``total / (3 * (nights - 1))`` -- a constant multiple of price per night.
    292 of 317 itineraries run seven nights, so across almost the whole table
    the column ranked trips in exactly the same order as the column beside it,
    while looking like an independent measurement. A number whose denominator
    was invented cannot say anything its numerator did not already say.

    Checking the formula against the counts operators do publish settled it.
    For a seven-night week they state anything from 15 to 21 while the formula
    says 18 for all of them -- a six-dive spread, a third of the figure, and
    the whole of what price per dive is meant to distinguish.

    Ten vessels publish a count, and a probe established the rest is not on the
    page at all. So the honest answer for the others is that nobody has said --
    the same answer this project gives for a dive site it cannot name and a fee
    nobody has read.

    ``for_nights`` guards the second half of the problem. A published count is
    the figure for that vessel's standard week, and applying it to every trip
    the boat sells put seventeen dives on a three-night mini-safari. A count is
    used only for the trip length it was quoted for.
    """
    if not stated or stated <= 0:
        return 0
    if for_nights is not None and nights is not None and nights != for_nights:
        return 0
    return stated


ABOVE_OPEN_WATER = re.compile(
    r"\b(?:advanced|experienced|technical|tec|tech)\b", re.I
)
"""Words that put an experience line above the entry level.

The line is free text -- "Advanced Open Water - 50 minimum logged dives
required." on the one trip a probe has read, and the spread across the fleet is
unknown -- so it is matched rather than mapped from a fixed vocabulary, and
anything unrecognised stays Open Water. Nothing is lost either way: the
operator's sentence is kept verbatim beside the level.

Deliberately no keyword reaches ``EXPERIENCED_100``. That level's label is
"Advanced + 100 dives", so reading it out of the word "experienced" would put a
hundred-dive bar on a trip whose operator named no number at all. The count
comes from the count.
"""


def _requirements(trip: dict[str, Any]) -> dict[str, Any] | None:
    """The entry bar a trip states, or ``None`` when none was read.

    ``None`` means nobody has looked, not that the operator asks for nothing --
    the same distinction this module makes about fees. It is the safer way
    round for a safety requirement: an unread trip shows no bar rather than a
    bar somebody invented.

    The operator's sentence is kept verbatim in ``notes``. The level and the
    logged-dive count are only ever read *out* of it, never added to it: a line
    naming a certification and no number gets ``min_logged_dives`` of zero,
    because filling in a plausible one would soften a stated requirement.
    """
    experience = (trip.get("experience") or "").strip()
    logged = int(trip.get("min_logged_dives") or 0)
    if not experience and not logged:
        return None

    if logged >= 100:
        level = DiverLevel.EXPERIENCED_100
    elif logged >= 50:
        level = DiverLevel.ADVANCED_50
    elif ABOVE_OPEN_WATER.search(experience):
        level = DiverLevel.ADVANCED
    else:
        level = DiverLevel.OPEN_WATER

    return {
        "min_level": level.value,
        "min_logged_dives": logged,
        "max_depth_m": None,
        "nitrox_recommended": False,
        "strong_current": False,
        "notes": experience or None,
    }


AVAILABILITY = {
    "soldout": "sold_out",
    "outofstock": "sold_out",
    "limitedavailability": "limited",
    "instock": "available",
    "onlineonly": "available",
    "instoreonly": "available",
    "preorder": "available",
}
"""schema.org availability, folded to what a diver needs to know.

The scrape has carried this from the start and promote threw it away, so 127
sold-out departures sat on the page priced exactly like the 746 bookable ones.
On a page whose whole job is comparison that is worse than a missing column:
sorting by cheapest could put a trip nobody can buy at the top of the list.

Unknown stays unknown. A source that says nothing about availability has not
said the trip is full.
"""


def _availability(raw: str | None) -> str | None:
    """Read schema.org's availability URL, or admit the source was silent."""
    if not raw:
        return None
    token = str(raw).rstrip("/").rsplit("/", 1)[-1].replace("_", "").lower()
    return AVAILABILITY.get(token)


PORTS_THAT_ARE_ALSO_SITES = (
    "safaga", "dahab", "soma bay", "hurghada", "marsa alam", "port ghalib",
    "sharm el sheikh", "hamata", "marsa ghalib",
)
"""Names that are a harbour and a dive area at once.

Egypt sails from the same places it dives. Treating these as evidence that a
bracketed pair is a route rather than a port pair loses the port pair, which is
the more useful reading: a diver books flights off it.
"""


def _without_ports(text: str) -> str:
    """Drop names that are harbours before asking whether text names a reef.

    Substituted on word boundaries rather than by replacing " name ": adjacent
    repeats share the space between them, so "Safaga - Safaga" lost only the
    first and the leftover looked like a reef.
    """
    lowered = normalise(text)
    for port in PORTS_THAT_ARE_ALSO_SITES:
        lowered = re.sub(rf"\b{re.escape(normalise(port))}\b", " ", lowered)
    return lowered


def _nights(start: str, end: str) -> int | None:
    try:
        delta = (date.fromisoformat(end) - date.fromisoformat(start)).days
    except ValueError:
        return None
    return delta if MIN_NIGHTS <= delta <= MAX_NIGHTS else None


def promote(
    candidate: dict[str, Any],
    *,
    season: tuple[date, date] | None = None,
    fx: dict[str, Any] | None = None,
    notes: str | None = None,
    fees: dict[str, Any] | None = None,
    facts: dict[str, Any] | None = None,
    trips: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a dataset payload from a scrape candidate.

    Departures are grouped into itineraries by (vessel, trip name): the same
    boat sells several routes through a season, and lumping them together would
    average away the difference between a wreck week and a shark week.
    """
    scraped_boats = {i.get("id"): i for i in candidate.get("itineraries", []) if i.get("id")}

    # Fees come from a separate, slower browser-driven run because the source
    # renders them client-side. They are keyed by vessel and reused across
    # every itinerary that vessel sells, which is what the operator does too:
    # the extras do not change with the month.
    fee_book: dict[str, list[dict[str, Any]]] = {}
    # The same run reads the vessel's specification table and diving amenities,
    # which are structured and were previously being worked around: the guest
    # count was mined out of marketing prose and missed half the fleet, and
    # nitrox inclusion was not read at all.
    spec_book: dict[str, dict[str, Any]] = {}
    if fees:
        for slug, entry in (fees.get("vessels") or {}).items():
            if entry.get("fees"):
                fee_book[slug] = entry["fees"]
            if entry.get("specs"):
                spec_book[slug] = entry["specs"]

    # What the operator says about *one trip*: the reefs it visits, how many
    # dives it fits in, how many people are aboard and who it will take. Read
    # from the itinerary fragment by ``tools/fetch_itineraries.py`` and merged
    # here the way the fee book is, because it is a separate crawl with its own
    # cadence -- a trip's reefs do not change from night to night.
    #
    # Keyed on vessel plus trip name, which is what an itinerary is here.
    trip_book: dict[str, dict[str, Any]] = {}
    for trip in ((trips or {}).get("trips") or {}).values():
        if trip.get("boat") and trip.get("name"):
            trip_book[itinerary_key(trip["boat"], trip["name"])] = trip

    # A vessel's guest count, as its own trips state it. Fallback only: the
    # specification table's "Max guests" is the hull's number and this is one
    # sailing's, so it is used where the table is missing the row. Taken as the
    # most common answer across that boat's trips rather than whichever trip
    # sorts first, so the figure does not depend on the order of the file.
    trip_guests: dict[str, int] = {}
    stated_guests: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for trip in trip_book.values():
        if trip.get("guests"):
            stated_guests[trip["boat"]][int(trip["guests"])] += 1
    for boat_slug, counts in stated_guests.items():
        trip_guests[boat_slug] = max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0]

    # Figures read off a vessel page by hand, for the boats whose extras block
    # names a charge without a number. They win per fee code: a page that says
    # "Rental Gear EUR 135" is a better answer than the same page's summary
    # line saying "Rental Gear" and stopping, and both are the same source.
    #
    # Merged per code rather than wholesale, so a vessel covered here keeps
    # every scraped fee this file does not mention.
    hand: dict[str, dict[str, Any]] = (facts or {}).get("vessels") or {}
    hand_read_on = (facts or {}).get("collected") or ""
    superseded: list[str] = []
    for slug, entry in hand.items():
        if not entry.get("fees"):
            continue
        merged = {f["code"]: f for f in fee_book.get(slug, [])}
        # The scrape's own date, per vessel, falling back to the book's.
        scraped_on = (
            (fees or {}).get("vessels", {}).get(slug, {}).get("collected")
            or (fees or {}).get("scraped_at")
            or ""
        )
        for fee in entry["fees"]:
            existing = merged.get(fee["code"])
            # A hand-read figure fills a gap; it does not outlive the scrape
            # that replaced it. Once the browser run reads a real price for a
            # code, a typed-in number collected earlier is last month's answer
            # winning over this week's, and nothing would ever say so.
            if (
                existing is not None
                and existing.get("amount") is not None
                and scraped_on
                and hand_read_on
                and scraped_on > hand_read_on
            ):
                superseded.append(f"{slug}/{fee['code']}")
                continue
            merged[fee["code"]] = fee
        fee_book[slug] = list(merged.values())

    # "Free Nitrox" in the vessel's diving amenities is the operator saying it
    # does not charge for fills. Note what it does *not* do: overwrite a stated
    # nitrox price. Where a vessel both ticks the box and quotes a figure, the
    # figure wins -- turning a stated cost into "free" is the one direction of
    # error this site must never make, and a marketing tick is weaker evidence
    # than a number the operator typed.
    for slug, spec in spec_book.items():
        if not spec.get("nitrox_free"):
            continue
        existing = {f["code"]: f for f in fee_book.get(slug, [])}
        nitrox = existing.get(FeeCode.NITROX.value)
        if nitrox is not None and nitrox.get("amount") is not None:
            continue
        existing[FeeCode.NITROX.value] = {
            "code": FeeCode.NITROX.value,
            "tier": FeeTier.CONDITIONAL.value,
            "included": True,
            "amount": None,
            "basis": FeeBasis.PER_TRIP.value,
            "note": "Vessel lists nitrox as free",
            "provenance": {
                "kind": SourceKind.SCRAPED.value,
                "source_id": "liveaboard.com",
                "retrieved": (fees or {}).get("scraped_at") or date.today().isoformat(),
            },
        }
        fee_book[slug] = list(existing.values())

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    skipped: list[str] = []

    for departure in candidate.get("departures", []):
        slug = departure.get("boat_slug")
        if not slug:
            skipped.append(f"{departure.get('id')}: no boat")
            continue
        nights = _nights(departure["start"], departure["end"])
        if nights is None:
            skipped.append(f"{departure.get('id')}: implausible dates")
            continue
        if season and not (season[0] <= date.fromisoformat(departure["start"]) <= season[1]):
            continue
        name, promotion, _ = _split_title(departure.get("name") or "Unnamed itinerary")
        grouped[(slug, name or "Unnamed itinerary")].append(
            {**departure, "nights": nights, "promotion": promotion}
        )

    # Who runs each boat, from the organizer its own departures name.
    #
    # Resolved per vessel rather than per departure because that is how the
    # source states it: across 878 archived events no boat's departures ever
    # disagreed about their operator, and one boat under two companies would be
    # a fact worth noticing rather than averaging. A disagreement therefore
    # warns and takes the most common answer.
    operators: dict[str, dict[str, str]] = {}
    boat_operator: dict[str, str] = {}
    for (slug, _), group in grouped.items():
        stated = [d["operator"] for d in group if d.get("operator")]
        if not stated:
            continue
        counts: dict[str, int] = defaultdict(int)
        for value in stated:
            counts[value] += 1
        if len(counts) > 1:
            listed = ", ".join(f"{n}x {v!r}" for v, n in sorted(counts.items()))
            skipped.append(f"{slug}: departures name more than one operator ({listed})")
        winner = max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0]
        record = operator_record(winner)
        operators.setdefault(record["id"], record)
        boat_operator[slug] = record["id"]

    boats: dict[str, dict[str, Any]] = {}
    itineraries: list[dict[str, Any]] = []
    departures: list[dict[str, Any]] = []

    for (slug, name), group in sorted(grouped.items()):
        source = scraped_boats.get(slug, {})
        boat_name = source.get("boat") or source.get("name") or slug.replace("-", " ").title()
        # Guests belong to the vessel, not the sailing: the same boat carries
        # the same number of people whichever week you book.
        boats.setdefault(
            slug,
            {
                "id": slug,
                "name": boat_name,
                "operator_id": boat_operator.get(slug, UNKNOWN_OPERATOR["id"]),
                # The specification table first: it is the operator stating a
                # number in a field labelled "Max guests", which beats both a
                # hand-typed figure and a regex over the marketing copy. The
                # prose match stays as the fallback for vessels whose table is
                # missing the row.
                "guests": (spec_book.get(slug, {}).get("guests")
                           or hand.get(slug, {}).get("guests")
                           or trip_guests.get(slug)
                           or _guests(source.get("summary"))),
                "cabins": spec_book.get(slug, {}).get("cabins"),
            },
        )

        itinerary_id = f"{slug}--{slugify(name)}"[:96]
        nights = _most_common(d["nights"] for d in group)
        trip = trip_book.get(itinerary_key(slug, name), {})

        # The operator's own list of reefs for this trip, when it has been
        # read. It beats the title outright rather than being merged with it:
        # a title is branding on 23 itineraries and actively wrong on some --
        # a St John's week matched two of BDE's three reefs and was badged
        # accordingly -- so unioning the two would reimport exactly the error
        # this source exists to remove. The title stays as the fallback, so a
        # trip the fetcher has not reached keeps the sites it already had.
        #
        # Folded here, from the operator's own words, rather than read out of
        # the book's `dive_sites`. That field is the same fold done by the
        # fetcher at crawl time, which made it a cache of whatever SITE_HINTS
        # knew that day: teaching the parser four Sinai reefs recovered 86
        # mentions in the parser and changed nothing in the dataset, because
        # promote was reading yesterday's answer. Re-crawling 315 trips to
        # re-read words already in the repository is the same slow and rude
        # thing `reparse_candidate.py` exists to avoid. `dive_sites` stays as
        # the fallback for a book written before regions were kept.
        # One region at a time, for the reason `_sites_from_prose` explains:
        # `normalise` eats any separator, so matching the joined string lets a
        # site be assembled across two entries that neither one names.
        # The description, then the operator's region list, then the title.
        # The regions are last-resort only, never merged: they are what this
        # ordering exists to stop trusting, but a trip whose description names
        # no reef at all -- "Famous Five", "Get Wrecked" -- would otherwise
        # publish an empty cell where the operator did say something.
        sites = (_sites_from_description(trip)
                 or _sites_from_regions(trip.get("regions") or [])
                 or _sites_from_name(name))

        # The title's port pair beats the Event location, which is the country.
        _, _, titled_ports = _split_title(name)
        located = [d.get("location") for d in group if d.get("location")]
        port_from, port_to = titled_ports or (
            (located[0], located[0]) if located else ("Unknown", "Unknown")
        )
        port_from, port_to = _port(port_from), _port(port_to)

        itineraries.append(
            {
                "id": itinerary_id,
                "name": name,
                # The name minus its port suffix. The full name stays: two
                # trips differing only by port are different trips, and the
                # itinerary id is built from it.
                "title": _display_title(name),
                "operator_id": boat_operator.get(slug, UNKNOWN_OPERATOR["id"]),
                "boat_id": slug,
                "nights": nights,
                # Zero means the operator does not publish one, and the page
                # says so rather than dividing by a number nobody stated.
                #
                # The itinerary fragment states a count for *this trip*, which
                # is what the column has always wanted: the vessel-level
                # figures behind ``_dives`` are a standard week's, and had to
                # be withheld from every other trip length that boat sells to
                # stay honest. A per-trip figure needs no such guard, so it is
                # taken as stated and the vessel figure remains the fallback.
                "dives": trip.get("dives") or _dives(
                    hand.get(slug, {}).get("dives") or source.get("dives"),
                    nights=nights,
                    for_nights=hand.get(slug, {}).get("dives_for_nights"),
                ),
                "port_from": port_from,
                "port_to": port_to,
                # Where the trip goes: the operator's own "Key regions" list
                # when the fragment has been read, otherwise the trip title,
                # which usually names the reefs. This is what the page filters
                # on -- there is no route label over the top of it any more,
                # because a name for a set of sites could be wrong about a
                # trip in a way the sites themselves cannot.
                "dive_sites": sites,
                # Only when nothing names a reef at all, so the column says
                # something true rather than sitting empty.
                "region": None if sites else _region_from_name(name),
                "summary": source.get("summary"),
                "source_url": source.get("source_url"),
                # Empty when the fee run has not covered this vessel. The
                # renderer shows that as unknown, never as zero.
                "fees": fee_book.get(slug, source.get("fees") or []),
            }
        )

        # Written only when the operator has actually stated one, rather than
        # as 314 nulls. A key appearing in a dataset diff then means somebody
        # read a safety requirement, which is the only reason to look at it.
        # An absent key loads as the default bar, which asks for nothing --
        # and that is the safe way round: an unread trip must not carry a
        # requirement nobody stated.
        bar = _requirements(trip)
        if bar:
            itineraries[-1]["requirements"] = bar

        for item in group:
            entry = {
                "id": item["id"],
                "itinerary_id": itinerary_id,
                "start": item["start"],
                "end": item["end"],
                "price": item["price"],
                "booking_url": item.get("booking_url"),
                "availability": _availability(item.get("availability")),
                "provenance": item["provenance"],
            }
            # Carried on the departure, not the itinerary: the operator
            # discounts specific dates, and the price scraped is already the
            # discounted one.
            #
            # Recorded, not rendered. "20% Off" is the operator's claim about a
            # list price we have never seen, so putting it on the page would
            # repeat a number we cannot check — on a site whose whole argument
            # is that advertised prices should be checked. It stays in the
            # dataset to explain why two departures of one trip cost different
            # amounts, and why a title once differed from its twin.
            if item.get("promotion"):
                entry["promotion"] = item["promotion"]
            departures.append(entry)

    _settle_title_case(itineraries)

    payload: dict[str, Any] = {
        "schema_version": 1,
        "generated": candidate.get("scraped_at") or date.today().isoformat(),
        "default_currency": "EUR",
        "notes": notes or _notes_for(itineraries),
        "fx": fx or _default_fx(),
        # Only the operators actually named. UNKNOWN_OPERATOR joins the list
        # only when something is filed under it -- carrying an unused "Operator
        # not captured" row would put a company on the page that does not exist.
        "operators": _operators_for(operators, itineraries),
        "boats": sorted(boats.values(), key=lambda b: b["name"]),
        "itineraries": itineraries,
        "departures": departures,
    }
    if skipped:
        payload["promotion_skipped"] = skipped
    if superseded:
        # Reported rather than silent: every entry here is a hand-typed figure
        # the scrape has since read for itself, and the honest response is to
        # delete it from data/operator_facts.json rather than leave it as a
        # redundant override that will one day be wrong.
        payload["facts_superseded"] = sorted(superseded)
    return payload


def _operators_for(
    named: dict[str, dict[str, str]], itineraries: list[dict[str, Any]]
) -> list[dict[str, str]]:
    """The operator rows the dataset needs, and no others.

    Every ``operator_id`` an itinerary references must resolve -- ``dataset``
    validates that and refuses to load otherwise -- so the fallback row is
    included exactly when something is filed under it. Carrying it
    unconditionally would put "Operator not captured" on a page where every
    trip has a real company behind it.
    """
    used = {i["operator_id"] for i in itineraries}
    rows = [record for key, record in sorted(named.items()) if key in used]
    if UNKNOWN_OPERATOR["id"] in used:
        rows.append(dict(UNKNOWN_OPERATOR))
    return rows or [dict(UNKNOWN_OPERATOR)]


def _notes_for(itineraries: list[dict[str, Any]]) -> str:
    """Describe what this run actually captured.

    The note used to be a constant reading "Fees are not yet captured, so true
    cost is shown as unknown". It outlived the fee run by a week: the page
    carried a full breakdown for every trip while telling its visitors it had
    none. A site that exists to catch operators describing their prices
    inaccurately cannot describe its own data inaccurately.
    """
    total = len(itineraries)
    with_fees = sum(1 for i in itineraries if i["fees"])
    if not total or not with_fees:
        return (
            "Prices scraped from liveaboard.com. Fees are not yet captured, so "
            "true cost is shown as unknown rather than as the advertised price."
        )
    if with_fees == total:
        return (
            "Prices and fee disclosures scraped from liveaboard.com. True cost "
            "adds every fee the operator lists, including the ones it states "
            "without a price."
        )
    return (
        f"Prices scraped from liveaboard.com. Fee disclosures captured for "
        f"{with_fees} of {total} itineraries; the rest show true cost as "
        f"unknown rather than as the advertised price."
    )


def _most_common(values) -> int:
    counts: dict[int, int] = defaultdict(int)
    for value in values:
        counts[value] += 1
    return max(counts.items(), key=lambda kv: (kv[1], -kv[0]))[0]


SITE_HINTS = (
    "brothers", "daedalus", "elphinstone", "thistlegorm", "abu nuhas",
    "rocky island", "zabargad", "st johns", "st john's", "fury shoal",
    "sataya", "ras mohammed", "tiran", "salem express", "rosalie moller",
    "gubal", "abu dabab", "samadai", "habili ali", "dangerous reef",
    "gota kebir",
    # Named on titles that previously yielded nothing at all.
    "dahab", "safaga", "elba reef", "turkia", "sataya reef",
    "marsa shouna", "gota abu ramada", "panorama reef", "middle reef",
    "small giftun", "shaab sheer", "umm gamar", "ras disha", "tobia arbaa",
    # Named in the operators' own prose. "Sha'ab el Erg" is where the resident
    # pod is, and is asked for as Dolphin House -- which is an alias below,
    # because the two are one reef.
    "sha'ab el erg",
)
"""Dive-site names operators actually write, in a title or in their own prose.

liveaboard.com does not publish a per-trip site list, but operators name their
routes after the sites — "Brothers, Daedalus & Elphinstone" says exactly where
it goes. Pulling the names back out of the title lets the existing classifier
work unchanged instead of leaving every scraped trip unclassified.

**A hint is a destination; a dive on one is an alias.** Thistlegorm and the
Salem Express are here because a week is sold to reach them and nothing else
on this list contains them. The Giannis D is not, because it is one of four
wrecks on Abu Nuhas, which is here — and a diver who wants it filters on Abu
Nuhas. Reading the prose is what forced the distinction: it names the
individual dives, so without the fold a Brothers week grew five chips
(Brothers, Big Brother, Little Brother, Numidia, Aida) for one place.

"sha'ab" and "shaab" were hints and are gone. It is Arabic for *reef*, so it
matched inside Sha'ab Sheer, Sha'ab Abu Nuhas and Sha'ab el Erg alike and put
a chip reading "reef" on **113 of 315** trips. No title's site list depended on
it.
"""

SITE_ALIASES: dict[str, str] = {
    "deadalus": "daedalus",
    "rocky": "rocky island",
    "st john": "st johns",
    "saint johns": "st johns",
    # Plurals. The match is on whole words, so "Fury Shoals" misses a hint
    # spelled "fury shoal" -- and the plural is what most titles use.
    "fury shoals": "fury shoal",
    "brother islands": "brothers",
    "brother island": "brothers",
    # The individual reefs in the Straits of Tiran, and the signature dive at
    # Ras Mohammed. Operators name these on their own per-trip region lists,
    # where nothing here knew the words at all: 86 mentions across 61 trips
    # went into the parser and came out empty, and Topaz's dolphin safari --
    # whose only region is Gordon Reef -- lost its site list entirely.
    #
    # Folded to the area rather than carried as their own chips. A filter is
    # only useful if a diver recognises what is in it, and "Tiran" and "Ras
    # Mohammed" are how these dives are asked for; four more chips naming
    # reefs inside them is precision nobody was looking for. 55 of the 61
    # trips name the area as well, so almost all of this is a fold onto a
    # chip that was already there.
    #
    # `normalise` turns "&" into "and", so one spelling covers "Shark &
    # Yolanda" too.
    "jackson reef": "tiran",
    "gordon reef": "tiran",
    "woodhouse reef": "tiran",
    # Thomas Reef completes the four; no trip names it yet, and the set is
    # only meaningful whole.
    "thomas reef": "tiran",
    "shark and yolanda": "ras mohammed",
    "the brothers": "brothers",
    # One m or two, both spellings are on the listing.
    "ras mohamed": "ras mohammed",
    "ras muhammad": "ras mohammed",
    "ras mohamad": "ras mohammed",
    "shaab abu nuhas": "abu nuhas",
    "ss thistlegorm": "thistlegorm",
    "elba borders": "elba reef",
    # Same fold, forced by the operators' own prose rather than by titles. A
    # day plan names the dive, not the destination -- "Dive 2: Giannis D" --
    # so on 267 trips the prose offered sites the region list did not, and
    # most of them were parts of a place already on the row. Left unfolded, an
    # Abu Nuhas week showed five chips for one reef and a Brothers week five
    # for one island pair, which is the precision nobody asked for that
    # Tiran's reefs were folded away for.
    #
    # The four wrecks on Abu Nuhas.
    "giannis d": "abu nuhas",
    "carnatic": "abu nuhas",
    "chrisoula k": "abu nuhas",
    "kimon m": "abu nuhas",
    # The two islands, and the two wrecks down Big Brother's wall.
    "big brother": "brothers",
    "little brother": "brothers",
    "numidia": "brothers",
    "aida": "brothers",
    # Ulysses lies on Gubal Seghir.
    "ulysses": "gubal",
    # Ras Mohammed's own dives. "Shark & Yolanda" is one dive over two reefs
    # and operators name either half; `normalise` turns "&" into "and", so the
    # spelling above covers the pair.
    "yolanda": "ras mohammed",
    "yolanda reef": "ras mohammed",
    "shark reef": "ras mohammed",
    # Beacon Rock, inside the national park.
    "dunraven": "ras mohammed",
    # Elphinstone is on the list under its bare name; the prose adds "Reef".
    "elphinstone reef": "elphinstone",
    # ---- Read out of the operators' own descriptions, not their titles ----
    #
    # A description names the dive; a key-regions list names the destination.
    # Until these were here, comparing the two measured our ignorance as much
    # as the operator's mistakes: "Daedalus & Fury Shoal" appeared to visit no
    # Fury Shoal because its week is spent at Shaab Claudio, Abu Galawa and
    # Shilineat, and nothing here knew those are Fury Shoal.
    #
    # One spelling variant, and the single most expensive one: the descriptions
    # write Abu Dabbab with two b's and the region lists write Abu Dabab with
    # one, so the same reef read as two different places on 16 trips.
    "abu dabbab": "abu dabab",
    # A typo in the operator's own day plan, on three trips.
    "gota abu ramad": "gota abu ramada",
    # The Fury Shoals, reef by reef.
    "shaab maksur": "fury shoal",
    "shaab claudio": "fury shoal",
    "shaab claudia": "fury shoal",
    "abu galawa": "fury shoal",
    "gotat abu galawa": "fury shoal",
    "shaab hamam": "fury shoal",
    "el malahi": "fury shoal",
    "malahi": "fury shoal",
    "shilineat": "fury shoal",
    "abu fendera": "fury shoal",
    "abu fandira": "fury shoal",
    # St John's, reef by reef.
    "umm aruk": "st johns",
    "cave reef": "st johns",
    "small gota": "st johns",
    "habili gafaar": "st johns",
    # The Straits of Gubal. Shag Rock carries the Kingston, and the Barge and
    # Bluff Point are Small Gubal's two best-known dives.
    "shag rock": "gubal",
    "kingston": "gubal",
    "bluff point": "gubal",
    "small gubal": "gubal",
    "big gubal": "gubal",
    # Ras Mohammed's park, which reaches past the headland: Shaab Mahmoud and
    # the Alternatives are dived on the same day as Shark and Yolanda, and
    # Jolanda is how half the fleet spells Yolanda.
    "shaab mahmoud": "ras mohammed",
    "the alternatives": "ras mohammed",
    "jolanda": "ras mohammed",
    "beacon rock": "ras mohammed",
    # Safaga's own house reef, which its region lists name and its
    # descriptions do not.
    "ras abu soma": "safaga",
    # The Abu Nuhas wrecks again, as the day plans abbreviate them: "Dive 4 at
    # Abu Nuhas - Giannis D" is matched by the full name above, but the prose
    # elsewhere drops the letter.
    "giannis": "abu nuhas",
    "chrisoula": "abu nuhas",
    "kimon": "abu nuhas",
}
# "Dolphin House" is deliberately absent. It is two reefs 400 km apart --
# Sha'ab el Erg off Hurghada and Sha'ab Samadai off Marsa Alam -- and both are
# sold under the name. A southern trip's own prose lists it beside Sataya and
# Fury Shoal, so folding it to the northern reef would have moved nine deep
# south itineraries to Hurghada. A nickname that needs the region to
# disambiguate cannot be resolved by a table that has no region.
"""How operators actually spell a site in a trip title.

Titles are marketing copy, not a gazetteer. Two of four vessels sell
"Deadalus, St. John´s & Elphinstone" — one transposition and one acute accent
— and a third abbreviates Rocky Island to "Rocky". Matching only the correct
spelling dropped Daedalus and St John's from those routes, and since
classification derives from dive sites, it filed a southern shark itinerary as
a single-site trip.

Keys are matched after :func:`~liveaboard.classify.normalise`, so accents and
apostrophes are already folded; only genuine misspellings and short forms
belong here.
"""


REGIONS = (
    (re.compile(r"\bdeep south\b|\bsouthern\b|\bsouth\b", re.I), "southern route"),
    (re.compile(r"\bsinai\b|\btiran\b", re.I), "Sinai and Tiran"),
    (re.compile(r"\bnorthern\b|\bnorth\b", re.I), "northern route"),
    (re.compile(r"\bwreck\w*\b", re.I), "wreck route"),
)
"""What a title says about where it goes when it names no reef.

Fifty-one trips are sold as "North", "Deep South" or "Get Wrecked" and never
name a site. This transcribes the operator's own word rather than classifying
anything -- a title saying "North" is evidence it goes north, and nothing more
is claimed.

The vessel description was the tempting alternative and is unusable: it is the
boat's brochure, listing everywhere it sails all year. Aphrodite's names
St John's, so its "North Wrecks" week would have been tagged with a site
six hundred kilometres from where it goes.
"""


def _region_from_name(name: str) -> str | None:
    """Transcribe the region a site-less title states, or admit it states none."""
    for pattern, label in REGIONS:
        if pattern.search(name):
            return label
    return None


def _sites_from_regions(regions: Sequence[str]) -> list[str]:
    """The operator's curated place list, one entry at a time.

    Exported as its own function so the fetcher matches it exactly rather than
    keeping a second copy of the rule -- the same reason `itinerary_key` is
    shared.
    """
    sites: list[str] = []
    for region in regions:
        sites = _also(sites, _sites_from_name(region))
    return sites


def _sites_from_description(trip: Mapping[str, Any]) -> list[str]:
    """Every reef the operator names in its own description of the trip.

    The whole description -- its lead paragraph, its section headings and its
    day plan -- and **not** the curated "Key regions" list beside it. That list
    is a summary somebody typed once and it is demonstrably wrong: All Star Red
    Sea sells a "North & Brothers" week whose regions name Daedalus, 180 km
    from anywhere its own day plan goes, and an "Ultimate Red Sea" fortnight
    whose regions name St John's while its description lists nine sites and
    omits it. Across 293 trips the regions claim a site the description never
    mentions on 42 of them.

    The description wins because it is what the buyer reads. A diver books on
    the sentences, not on the sidebar, so those sentences are the operator's
    actual claim -- which is the only thing this site ever reports.

    The fragment's fourth heading, "Route", is not read and cannot be: it is a
    single ``<figure>`` holding a map image, with zero characters of text on
    every vessel probed. Recorded in ``docs/sources/liveaboard.com.md``.

    Read one section at a time rather than from the lot joined together.
    Joining and matching once looks equivalent and is not: :func:`normalise`
    reduces every punctuation mark to a space, so a separator disappears into
    the text and a section headed "Ras" followed by one headed "Mohammed"
    would invent a reef neither of them names.
    """
    sites = _sites_from_name(trip.get("intro") or "")
    for section in trip.get("sections") or ():
        sites = _also(sites, _sites_from_name(section.get("heading") or ""))
        # A day says what you dive; a heading names a place; the body of a
        # place section is an essay about it and will mention anywhere.
        # All Star Red Sea describes Daedalus as "Much like the Brothers
        # Islands, Daedalus also sits in open water" -- a comparison, on a
        # trip that goes nowhere near the Brothers. Reading it put the
        # Brothers on that row and on two others.
        if section.get("is_day"):
            sites = _also(sites, _sites_from_name(section.get("text") or ""))
    return sites


def _also(sites: list[str], extra: list[str]) -> list[str]:
    """``sites`` with anything genuinely new appended, order kept.

    Deduplicated on the folded key rather than the string, so "Elphinstone"
    from a region list and "Elphinstone Reef" from a heading stay one chip.
    """
    if not extra:
        return sites
    out = list(sites)
    seen = {normalise(site) for site in out}
    for site in extra:
        key = normalise(site)
        if key not in seen:
            seen.add(key)
            out.append(site)
    return out


def _sites_from_name(name: str) -> list[str]:
    """Recover dive-site names from an itinerary title.

    Both sides are folded through the classifier's own :func:`normalise`, so
    "St. John´s" in a title reaches the same key as "St Johns" in the hints
    rather than missing it over punctuation.
    """
    folded = f" {normalise(name)} "
    found: list[str] = []
    # Keyed on the folded form: SITE_HINTS lists "st johns" and "st john's"
    # separately for readability, and they are one site.
    seen: set[str] = set()

    def add(site: str) -> None:
        key = normalise(site)
        if key not in seen:
            seen.add(key)
            found.append(site)

    for hint in SITE_HINTS:
        if f" {normalise(hint)} " in folded:
            add(hint)
    for alias, canonical in SITE_ALIASES.items():
        if f" {normalise(alias)} " in folded:
            add(canonical)
    return found


def _default_fx() -> dict[str, Any]:
    return {
        "display_currency": "EUR",
        "as_of": date.today().isoformat(),
        # Named a placeholder on purpose: money.PLACEHOLDER_SOURCE matches it,
        # so the page warns instead of passing this off as a looked-up rate.
        # tools/fetch_fx.py replaces the whole table with ECB reference rates;
        # this only applies when that has never run.
        "source": "placeholder — replace with a real rate source",
        "rates": {"USD": 0.92, "GBP": 1.17},
    }
