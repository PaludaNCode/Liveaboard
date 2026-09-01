#!/usr/bin/env python3
"""Generate the seed dataset for the May-August 2027 Egypt season.

This exists so the engine, the classification rules and the site can be built
and tested before the two source sites are reachable. Every price it emits is
marked ``seed_estimate`` and the site renders that stamp visibly.

Two deliberate choices:

* **Fee structures are researched, not invented.** The amounts come from
  published Red Sea operator terms (park and port charges of roughly USD
  100-145 per week, park-port-fuel bundles of EUR 280-340, gear at EUR 170 per
  trip, St John's park fees near EUR 200) and are the part of this file worth
  keeping once real data lands.

* **Operator and boat identities are explicit placeholders.** Attaching an
  invented price to a real business on a public site would be a small lie of
  exactly the kind this project is meant to expose. The names go away when the
  scrapers run; the fee taxonomy stays.

Usage::

    python3 tools/make_seed.py [--out data/seed/egypt-2027.json]
"""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

SEASON_START = date(2027, 5, 1)
SEASON_END = date(2027, 8, 31)

SEED_PROVENANCE: dict[str, Any] = {
    "kind": "seed_estimate",
    "source_id": "seed:research-2026-08",
    "retrieved": "2026-08-27",
    "note": "Researched placeholder, not a quote. Replaced once padi.com and liveaboard.com are reachable.",
}


def prov(note: str | None = None) -> dict[str, Any]:
    payload = dict(SEED_PROVENANCE)
    if note:
        payload["note"] = f"{payload['note']} {note}"
    return payload


def fee(
    code: str,
    tier: str,
    amount: float,
    currency: str = "EUR",
    *,
    basis: str = "per_trip",
    included: bool = False,
    note: str | None = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "tier": tier,
        "amount": {"amount": amount, "currency": currency},
        "basis": basis,
        "included": included,
        "note": note,
        "provenance": prov(),
    }


def standard_extras(
    *,
    nitrox: float = 120.0,
    gear: float = 170.0,
    gratuities: float = 120.0,
    transfers: float = 40.0,
    insurance: float = 35.0,
    nitrox_included: bool = False,
) -> list[dict[str, Any]]:
    """The conditional and customary lines almost every Egyptian boat carries."""
    return [
        fee(
            "nitrox",
            "conditional",
            0.0 if nitrox_included else nitrox,
            included=nitrox_included,
            note="Free nitrox for certified divers" if nitrox_included else None,
        ),
        fee("gear_rental", "conditional", gear, note="BCD, regulator, wetsuit, computer"),
        fee("dive_insurance", "conditional", insurance, note="Mandatory cover, own policy accepted"),
        fee("airport_transfer", "conditional", transfers, note="Airport to marina, return"),
        fee("gratuities", "customary", gratuities, note="Suggested crew tip, not contractual"),
        fee("visa", "mandatory", 25.0, "USD", note="Egypt visa on arrival"),
    ]


OPERATORS: list[dict[str, Any]] = [
    {"id": "op-a", "name": "Sample Operator A", "website": None},
    {"id": "op-b", "name": "Sample Operator B", "website": None},
    {"id": "op-c", "name": "Sample Operator C", "website": None},
    {"id": "op-d", "name": "Sample Operator D", "website": None},
]

BOATS: list[dict[str, Any]] = [
    {"id": "boat-1", "name": "Sample Vessel I", "operator_id": "op-a", "cabins": 11, "guests": 22, "length_m": 38.0},
    {"id": "boat-2", "name": "Sample Vessel II", "operator_id": "op-a", "cabins": 10, "guests": 20, "length_m": 36.0},
    {"id": "boat-3", "name": "Sample Vessel III", "operator_id": "op-b", "cabins": 12, "guests": 24, "length_m": 40.0},
    {"id": "boat-4", "name": "Sample Vessel IV", "operator_id": "op-c", "cabins": 9, "guests": 18, "length_m": 34.0},
    {"id": "boat-5", "name": "Sample Vessel V", "operator_id": "op-d", "cabins": 11, "guests": 22, "length_m": 39.0},
]

ITINERARIES: list[dict[str, Any]] = [
    {
        "id": "itin-north-wrecks",
        "name": "Northern Wrecks & Reefs",
        "operator_id": "op-a",
        "boat_id": "boat-1",
        "nights": 7,
        "dives": 20,
        "port_from": "Hurghada",
        "port_to": "Hurghada",
        "dive_sites": [
            "Abu Nuhas", "Giannis D", "Carnatic", "Chrisoula K",
            "SS Thistlegorm", "Rosalie Moller", "Bluff Point", "Small Crack",
        ],
        "requirements": {
            "min_level": "advanced",
            "min_logged_dives": 20,
            "max_depth_m": 30,
            "nitrox_recommended": True,
            "notes": "Thistlegorm sits at 30 m; repeated deep profiles make nitrox worthwhile.",
        },
        "summary": "The classic wreck week out of Hurghada, built around the Thistlegorm and the four Abu Nuhas casualties.",
        "fees": [
            fee("marine_park", "mandatory", 90.0, note="Northern parks, per week"),
            fee("port_fees", "mandatory", 45.0),
            fee("fuel_surcharge", "mandatory", 50.0),
            *standard_extras(),
        ],
    },
    {
        "id": "itin-bde",
        "name": "Brothers, Daedalus & Elphinstone",
        "operator_id": "op-b",
        "boat_id": "boat-3",
        "nights": 7,
        "dives": 19,
        "port_from": "Port Ghalib",
        "port_to": "Port Ghalib",
        "dive_sites": [
            "Big Brother", "Little Brother", "Numidia", "Aida",
            "Daedalus Reef", "Elphinstone Reef",
        ],
        "requirements": {
            "min_level": "advanced_50",
            "min_logged_dives": 50,
            "max_depth_m": 40,
            "nitrox_recommended": True,
            "strong_current": True,
            "notes": "Open Water divers not accepted. Blue-water and drift experience expected; no decompression diving.",
        },
        "summary": "Egypt's offshore shark run. Three exposed reefs, real current, and the reason most people book a Red Sea summer.",
        "fees": [
            fee("marine_park", "mandatory", 140.0, note="Offshore marine park, per week"),
            fee("port_fees", "mandatory", 45.0),
            fee("fuel_surcharge", "mandatory", 60.0),
            *standard_extras(nitrox=140.0, gratuities=140.0),
        ],
    },
    {
        "id": "itin-deep-south",
        "name": "Deep South — St John's, Rocky & Zabargad",
        "operator_id": "op-b",
        "boat_id": "boat-3",
        "nights": 7,
        "dives": 20,
        "port_from": "Port Ghalib",
        "port_to": "Port Ghalib",
        "dive_sites": [
            "Rocky Island", "Zabargad", "St John's", "Habili Ali",
            "Gota Kebir", "Umm Kharerim", "Dangerous Reef",
        ],
        "requirements": {
            "min_level": "advanced_50",
            "min_logged_dives": 50,
            "max_depth_m": 40,
            "strong_current": True,
            "notes": "Long crossings and exposed pinnacles; the southern parks carry the highest fees in Egypt.",
        },
        "summary": "The far south: caverns at St John's, hard coral at Zabargad, and Rocky Island's wall when the weather allows it.",
        "fees": [
            fee("marine_park", "mandatory", 200.0, note="Southern marine parks, highest band"),
            fee("port_fees", "mandatory", 45.0),
            fee("fuel_surcharge", "mandatory", 65.0, note="Long southern crossings"),
            *standard_extras(nitrox=140.0),
        ],
    },
    {
        "id": "itin-fury-shoal",
        "name": "Fury Shoal & Sataya",
        "operator_id": "op-c",
        "boat_id": "boat-4",
        "nights": 7,
        "dives": 20,
        "port_from": "Marsa Alam",
        "port_to": "Marsa Alam",
        "dive_sites": [
            "Fury Shoal", "Sataya", "Abu Galawa", "Claudia", "Malahi",
            "Sha'ab Maksur", "Dolphin House",
        ],
        "requirements": {
            "min_level": "open_water",
            "min_logged_dives": 0,
            "max_depth_m": 25,
            "notes": "Sheltered, forgiving and shallow. The one summer route that genuinely suits fresh Open Water divers.",
        },
        "summary": "Gentle southern reef week: swim-throughs, hard coral gardens and a resident spinner dolphin pod at Sataya.",
        "fees": [
            fee("marine_park", "mandatory", 110.0),
            fee("port_fees", "mandatory", 45.0),
            fee("fuel_surcharge", "mandatory", 45.0),
            *standard_extras(nitrox=100.0, gear=150.0, gratuities=100.0),
        ],
    },
    {
        "id": "itin-all-inclusive-bde",
        "name": "Brothers & Daedalus, all fees included",
        "operator_id": "op-d",
        "boat_id": "boat-5",
        "nights": 7,
        "dives": 19,
        "port_from": "Hurghada",
        "port_to": "Hurghada",
        "dive_sites": ["Big Brother", "Little Brother", "Numidia", "Aida", "Daedalus Reef"],
        "requirements": {
            "min_level": "advanced_50",
            "min_logged_dives": 50,
            "max_depth_m": 40,
            "strong_current": True,
        },
        "summary": "Same offshore reefs as the standard BDE week, priced with park, port, fuel and nitrox already inside the headline number.",
        "fees": [
            fee("marine_park", "mandatory", 0.0, included=True, note="Bundled into the fare"),
            fee("port_fees", "mandatory", 0.0, included=True, note="Bundled into the fare"),
            fee("fuel_surcharge", "mandatory", 0.0, included=True, note="Bundled into the fare"),
            *standard_extras(nitrox_included=True, transfers=0.0, gratuities=140.0),
        ],
    },
    {
        "id": "itin-ultimate",
        "name": "Ultimate Red Sea — North to Deep South",
        "operator_id": "op-a",
        "boat_id": "boat-2",
        "nights": 10,
        "dives": 28,
        "port_from": "Hurghada",
        "port_to": "Port Ghalib",
        "dive_sites": [
            "SS Thistlegorm", "Abu Nuhas", "Big Brother", "Little Brother",
            "Daedalus Reef", "Elphinstone Reef", "Rocky Island", "Zabargad",
        ],
        "requirements": {
            "min_level": "experienced_100",
            "min_logged_dives": 100,
            "max_depth_m": 40,
            "nitrox_recommended": True,
            "strong_current": True,
            "notes": "Ten nights, one-way, wrecks through to the southern pinnacles. The most demanding schedule sold in Egypt.",
        },
        "summary": "One-way marathon from the northern wrecks to the far south, taking in the offshore sharks on the way through.",
        "fees": [
            fee("marine_park", "mandatory", 220.0, note="Crosses three fee bands"),
            fee("port_fees", "mandatory", 70.0, note="Two ports, one-way itinerary"),
            fee("fuel_surcharge", "mandatory", 90.0),
            *standard_extras(nitrox=190.0, gear=240.0, gratuities=180.0, transfers=70.0),
        ],
    },
]

# Base fare per itinerary, and the multiplier applied in each month. July and
# August are peak: school holidays plus the hammerhead window.
BASE_FARES: dict[str, tuple[float, str]] = {
    "itin-north-wrecks": (1150.0, "EUR"),
    "itin-bde": (1450.0, "EUR"),
    "itin-deep-south": (1390.0, "EUR"),
    "itin-fury-shoal": (1050.0, "EUR"),
    "itin-all-inclusive-bde": (1890.0, "EUR"),
    "itin-ultimate": (2250.0, "USD"),
}

MONTH_FACTOR: dict[int, float] = {5: 0.92, 6: 1.0, 7: 1.12, 8: 1.15}

# Which weeks each itinerary sails, as an offset cycle across the season.
CADENCE: dict[str, int] = {
    "itin-north-wrecks": 2,
    "itin-bde": 2,
    "itin-deep-south": 2,
    "itin-fury-shoal": 1,
    "itin-all-inclusive-bde": 2,
    "itin-ultimate": 4,
}

OFFSET: dict[str, int] = {
    "itin-north-wrecks": 0,
    "itin-bde": 0,
    "itin-deep-south": 1,
    "itin-fury-shoal": 0,
    "itin-all-inclusive-bde": 1,
    "itin-ultimate": 2,
}


def saturdays(start: date, end: date) -> list[date]:
    """Every Saturday in the window: the standard Red Sea turnaround day."""
    first = start + timedelta(days=(5 - start.weekday()) % 7)
    out: list[date] = []
    cursor = first
    while cursor <= end:
        out.append(cursor)
        cursor += timedelta(days=7)
    return out


def build_departures() -> list[dict[str, Any]]:
    """Lay departures across the season according to each itinerary's cadence."""
    itineraries = {i["id"]: i for i in ITINERARIES}
    weeks = saturdays(SEASON_START, SEASON_END)
    departures: list[dict[str, Any]] = []

    for itin_id, (base, currency) in BASE_FARES.items():
        itinerary = itineraries[itin_id]
        nights = int(itinerary["nights"])
        cadence = CADENCE[itin_id]
        offset = OFFSET[itin_id]

        for index, saturday in enumerate(weeks):
            if (index - offset) % cadence != 0 or index < offset:
                continue
            end = saturday + timedelta(days=nights)
            if end > SEASON_END + timedelta(days=10):
                continue

            factor = MONTH_FACTOR.get(saturday.month, 1.0)
            price = round(base * factor / 5) * 5

            departures.append(
                {
                    "id": f"{itin_id}-{saturday.isoformat()}",
                    "itinerary_id": itin_id,
                    "start": saturday.isoformat(),
                    "end": end.isoformat(),
                    "price": {"amount": price, "currency": currency},
                    "provenance": prov(),
                    "fees": [],
                }
            )
    return departures


def build() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generated": date.today().isoformat(),
        "default_currency": "EUR",
        "season": {"start": SEASON_START.isoformat(), "end": SEASON_END.isoformat()},
        "notes": (
            "Seed dataset. Prices are researched placeholders, not quotes, and operator "
            "and boat names are placeholders. Fee structures reflect published Red Sea "
            "operator terms and are the part worth keeping."
        ),
        "fx": {
            "display_currency": "EUR",
            "as_of": "2026-08-27",
            "source": "seed:placeholder-rate",
            "rates": {"USD": 0.92, "GBP": 1.17, "EGP": 0.019},
        },
        "operators": OPERATORS,
        "boats": BOATS,
        "itineraries": ITINERARIES,
        "departures": build_departures(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="data/seed/egypt-2027.json", type=Path)
    args = parser.parse_args()

    payload = build()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        f"wrote {args.out}: "
        f"{len(payload['itineraries'])} itineraries, {len(payload['departures'])} departures"
    )


if __name__ == "__main__":
    main()
