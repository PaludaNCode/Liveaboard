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
) -> dict[str, Any]:
    """Build a dataset payload from a scrape candidate.

    Departures are grouped into itineraries by (vessel, trip name): the same
    boat sells several routes through a season, and lumping them together would
    average away the difference between a wreck week and a shark week.
    """
    scraped_boats = {i.get("id"): i for i in candidate.get("itineraries", []) if i.get("id")}

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
        name = (departure.get("name") or "Unnamed itinerary").strip()
        grouped[(slug, name)].append({**departure, "nights": nights})

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
        ports = [d.get("location") for d in group if d.get("location")]

        itineraries.append(
            {
                "id": itinerary_id,
                "name": name,
                "operator_id": UNKNOWN_OPERATOR["id"],
                "boat_id": slug,
                "nights": nights,
                "dives": 0,
                "port_from": ports[0] if ports else "Unknown",
                "port_to": ports[0] if ports else "Unknown",
                # Left empty on purpose. Route and theme are derived from dive
                # sites, which this source does not publish; the classifier
                # falls back to the trip name, which usually carries them.
                "dive_sites": _sites_from_name(name),
                "summary": source.get("summary"),
                "source_url": source.get("source_url"),
                # No fee data was scraped. The renderer must show this as
                # unknown rather than as zero.
                "fees": [],
            }
        )

        for item in group:
            departures.append(
                {
                    "id": item["id"],
                    "itinerary_id": itinerary_id,
                    "start": item["start"],
                    "end": item["end"],
                    "price": item["price"],
                    "booking_url": item.get("booking_url"),
                    "provenance": item["provenance"],
                }
            )

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


def _sites_from_name(name: str) -> list[str]:
    """Recover dive-site names from an itinerary title."""
    lowered = name.lower()
    return [hint for hint in SITE_HINTS if hint in lowered]


def _default_fx() -> dict[str, Any]:
    return {
        "display_currency": "EUR",
        "as_of": date.today().isoformat(),
        "source": "placeholder — replace with a real rate source",
        "rates": {"USD": 0.92, "GBP": 1.17, "EGP": 0.019},
    }
