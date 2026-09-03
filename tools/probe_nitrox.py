#!/usr/bin/env python3
"""Where the two sellers contradict each other about nitrox, and whether they do.

`fees_from_payload` resolves an inclusion against a priced entry by letting the
amount win — "a charge cannot be billed and bundled on one trip" — and the
comment beside that rule cites nitrox as the case it was written for. This
checks the citation rather than trusting it, because the resolution decides
what a diver is told a fill costs on the one extra this site puts a toggle on.

It reads PADI's itinerary detail, the same endpoint `fetch_padi.py` reads, and
prints per trip:

* every ``whatsIncludedNew`` entry naming nitrox — the "it is free" claim;
* every optional or mandatory entry naming it with a figure — the "it costs"
  claim, with the field it sat in, its price and its ``generalInformation``;
* what liveaboard.com's own book says for the same vessel, from
  ``data/fees.json``, so a disagreement between sellers is visible beside a
  disagreement inside one.

**Read the billed entry's title, not only the inclusion.** That is the whole
lesson of the first run: every clash carries the identical inclusion text,
*"Free nitrox (for certified nitrox divers)"*, so a rule looking there finds
nothing to tell the cases apart. What separates them is on the other side —
*"15 LITER tank nitrox (only 12 liter is free of chanrge)"* is a tank upgrade
and *"Nitrox"* at 50 EUR is the gas.

Writes nothing. See docs/sources/padi.com.md, *Nitrox, and the two claims*.

    python3 tools/probe_nitrox.py egypt/my-discovery-i egypt/my-seawolf-dominator
    python3 tools/probe_nitrox.py --clashing        # every vessel that can clash
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from liveaboard.scrape.fees import classify_label  # noqa: E402
from liveaboard.scrape.padi_com import ITINERARY_DETAIL  # noqa: E402
from liveaboard.taxonomy import FeeCode  # noqa: E402

TRIP_LIST = "https://travel.padi.com/api/v2/travel/shop/{vessel}/trips/"
UA = {"User-Agent": "Mozilla/5.0 (compatible; liveaboard-probe/1.0; "
                    "+price-transparency research)"}

NITROX = re.compile(r"nitrox|enriched\s*air", re.I)
"""Anything naming the gas, before the classifier is asked what it is.

Deliberately wider than `LABEL_PATTERNS`: the question here is which entries a
reader would call nitrox, and the answer includes the ones the parser files
elsewhere. *PADI Enriched Air Diver (Nitrox)* is a certification and
`classify_label` already sends it to `NITROX_COURSE`; this prints it anyway,
under the code it resolves to, because "the course is excluded" is a claim
worth seeing hold rather than assuming.
"""

TANK = re.compile(r"\d{1,2}\s*(?:l\b|lt\b|liters?|litres?)", re.I)
"""A tank size in the title. What tells an upgrade from the gas."""

OPTIONAL_FIELDS = ("optionalOnBoard", "optionalInAdvance",
                   "optionalBookableAdvancePaidOnBoard")
MANDATORY_FIELDS = ("mandatoryOnBoard", "mandatoryInAdvance")


def get(url: str) -> dict | None:
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, headers=UA), timeout=40
        ) as response:
            return json.loads(response.read().decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001 - one bad page must not end the probe
        print(f"    ! {url.rsplit('/', 3)[-1]}: {exc}", file=sys.stderr)
        return None


def ours(slug: str, fees: dict) -> str:
    """What liveaboard.com's committed book says about nitrox for this vessel."""
    entry = (fees.get("vessels") or {}).get(slug)
    if entry is None:
        return "no vessel panel"
    line = next((f for f in entry.get("fees") or []
                 if f["code"] == FeeCode.NITROX.value), None)
    if line is None:
        return "not named"
    if line.get("included"):
        return "included"
    amount = (line.get("amount") or {}).get("amount")
    return f"{amount:g}" if amount is not None else "listed, no price"


def clashing_vessels(padi: dict) -> list[tuple[str, str, str]]:
    """`(country, padi slug, our boat id)` for vessels whose PADI book prices
    nitrox — the only population where a "free *and* priced" pair can exist."""
    vessels = padi.get("vessels") or {}
    priced = {
        trip["boat"]
        for trip in (padi.get("trips") or {}).values()
        for line in (trip.get("fees") or {}).get("lines") or []
        if line["code"] == FeeCode.NITROX.value and line.get("amount") is not None
    }
    return sorted(
        (v.get("country") or "egypt", v["slug"], boat)
        for boat, v in vessels.items() if boat in priced and v.get("slug")
    )


def read(country: str, slug: str, delay: float) -> list[dict]:
    """One record per *itinerary* — the listing repeats a trip per departure."""
    listing = get(TRIP_LIST.format(vessel=slug)) or {}
    time.sleep(delay)
    out, seen = [], set()
    for row in listing.get("results") or []:
        trip = (row.get("itinerary") or {}).get("slug")
        if not trip or trip in seen:
            continue
        seen.add(trip)
        detail = get(ITINERARY_DETAIL.format(country=country, vessel=slug, slug=trip))
        time.sleep(delay)
        if not detail:
            continue

        included = [str(e.get("title")) for e in detail.get("whatsIncludedNew") or []
                    if isinstance(e, dict) and NITROX.search(str(e.get("title") or ""))]
        billed = []
        for field in OPTIONAL_FIELDS + MANDATORY_FIELDS:
            for entry in detail.get(field) or []:
                if not isinstance(entry, dict):
                    continue
                title = str(entry.get("title") or "")
                if not NITROX.search(title):
                    continue
                money = entry.get("price")
                money = entry.get("extraValue") if money is None else money
                billed.append({
                    "field": field, "title": title, "money": money,
                    "code": classify_label(title, prose=False),
                    "note": (entry.get("generalInformation") or "").strip(),
                })
        if included or billed:
            out.append({"trip": trip, "included": included, "billed": billed})
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("vessels", nargs="*", metavar="country/slug")
    parser.add_argument("--clashing", action="store_true",
                        help="every vessel whose PADI book prices nitrox")
    parser.add_argument("--padi", default=Path("data/padi.json"), type=Path)
    parser.add_argument("--fees", default=Path("data/fees.json"), type=Path)
    parser.add_argument("--delay", type=float, default=1.0)
    args = parser.parse_args()

    padi = json.loads(args.padi.read_text(encoding="utf-8"))
    fees = json.loads(args.fees.read_text(encoding="utf-8"))
    aliases = {v["slug"]: boat for boat, v in (padi.get("vessels") or {}).items()}

    targets = [(c, s, aliases.get(s, s)) for c, s in
               (a.split("/", 1) for a in args.vessels)]
    if args.clashing:
        targets = clashing_vessels(padi)
    if not targets:
        parser.error("name a vessel as country/slug, or pass --clashing")

    both = bare = upgrade = 0
    for country, slug, boat in targets:
        records = read(country, slug, args.delay)
        pairs = [r for r in records if r["included"] and r["billed"]]
        if not pairs:
            continue
        print(f"\n{boat}  ({country}/{slug})   liveaboard.com: {ours(boat, fees)}")
        for record in pairs:
            gas = [b for b in record["billed"] if b["code"] is FeeCode.NITROX]
            if not gas:
                continue
            both += 1
            for line in gas:
                # The distinction the first run had to be re-fetched to see: a
                # title naming a tank size is an upgrade priced beside free
                # fills, not the seller contradicting itself about the gas.
                kind = "UPGRADE" if TANK.search(line["title"]) else "BARE"
                bare += kind == "BARE"
                upgrade += kind == "UPGRADE"
                print(f"  {kind:8} {str(line['money']):>9}  {line['title'][:52]:54}"
                      f" <- {record['included'][0][:40]}")
                if line["note"]:
                    print(f"           note: {line['note'][:100]}")

    print(f"\ntrips stating both: {both}   priced upgrade: {upgrade}   "
          f"unexplained: {bare}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
