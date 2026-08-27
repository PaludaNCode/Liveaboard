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
from collections import defaultdict
from datetime import date
from typing import Any

from .classify import normalise

UNKNOWN_OPERATOR = {
    "id": "unknown-operator",
    "name": "Operator not captured",
}
"""liveaboard.com is an agency listing: it names the vessel, not who runs it.

Inventing an operator would be worse than admitting we do not know one.
"""

MIN_NIGHTS = 1
MAX_NIGHTS = 30
"""Sanity bounds. A departure outside these is a parsing error, not a cruise."""


def slugify(value: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", value.lower())).strip("-")


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


def _split_title(name: str) -> tuple[str, str | None, tuple[str, str] | None]:
    """Split a scraped trip title into route, promotion and ports."""
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
        if not _sites_from_name(f"{first} {second}"):
            ports = (first, second)
    return name.strip(), promotion, ports


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
    if fees:
        for slug, entry in (fees.get("vessels") or {}).items():
            if entry.get("fees"):
                fee_book[slug] = entry["fees"]

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

    boats: dict[str, dict[str, Any]] = {}
    itineraries: list[dict[str, Any]] = []
    departures: list[dict[str, Any]] = []

    for (slug, name), group in sorted(grouped.items()):
        source = scraped_boats.get(slug, {})
        boat_name = source.get("boat") or source.get("name") or slug.replace("-", " ").title()
        boats.setdefault(
            slug,
            {"id": slug, "name": boat_name, "operator_id": UNKNOWN_OPERATOR["id"]},
        )

        itinerary_id = f"{slug}--{slugify(name)}"[:96]
        nights = _most_common(d["nights"] for d in group)

        # The title's port pair beats the Event location, which is the country.
        _, _, titled_ports = _split_title(name)
        located = [d.get("location") for d in group if d.get("location")]
        port_from, port_to = titled_ports or (
            (located[0], located[0]) if located else ("Unknown", "Unknown")
        )

        itineraries.append(
            {
                "id": itinerary_id,
                "name": name,
                "operator_id": UNKNOWN_OPERATOR["id"],
                "boat_id": slug,
                "nights": nights,
                "dives": 0,
                "port_from": port_from,
                "port_to": port_to,
                # Left empty on purpose. Route and theme are derived from dive
                # sites, which this source does not publish; the classifier
                # falls back to the trip name, which usually carries them.
                "dive_sites": _sites_from_name(name),
                "summary": source.get("summary"),
                "source_url": source.get("source_url"),
                # Empty when the fee run has not covered this vessel. The
                # renderer shows that as unknown, never as zero.
                "fees": fee_book.get(slug, source.get("fees") or []),
            }
        )

        for item in group:
            entry = {
                "id": item["id"],
                "itinerary_id": itinerary_id,
                "start": item["start"],
                "end": item["end"],
                "price": item["price"],
                "booking_url": item.get("booking_url"),
                "provenance": item["provenance"],
            }
            # Carried on the departure, not the itinerary: the operator
            # discounts specific dates, and the price shown is already the
            # discounted one. This says why it is lower than its neighbours.
            if item.get("promotion"):
                entry["promotion"] = item["promotion"]
            departures.append(entry)

    payload: dict[str, Any] = {
        "schema_version": 1,
        "generated": candidate.get("scraped_at") or date.today().isoformat(),
        "default_currency": "EUR",
        "notes": notes
        or (
            "Prices scraped from liveaboard.com. Fees are not yet captured, so "
            "true cost is shown as unknown rather than as the advertised price."
        ),
        "fx": fx or _default_fx(),
        "operators": [UNKNOWN_OPERATOR],
        "boats": sorted(boats.values(), key=lambda b: b["name"]),
        "itineraries": itineraries,
        "departures": departures,
    }
    if skipped:
        payload["promotion_skipped"] = skipped
    return payload


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
    "gota kebir", "sha'ab", "shaab", "elphinstone reef", "big brother",
    "little brother", "numidia", "aida", "carnatic", "giannis d",
)
"""Dive-site names that routinely appear in itinerary titles.

liveaboard.com does not publish a per-trip site list, but operators name their
routes after the sites — "Brothers, Daedalus & Elphinstone" says exactly where
it goes. Pulling the names back out of the title lets the existing classifier
work unchanged instead of leaving every scraped trip unclassified.
"""

SITE_ALIASES: dict[str, str] = {
    "deadalus": "daedalus",
    "rocky": "rocky island",
    "st john": "st johns",
    "saint johns": "st johns",
}
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
        "source": "placeholder — replace with a real rate source",
        "rates": {"USD": 0.92, "GBP": 1.17, "EGP": 0.019},
    }
