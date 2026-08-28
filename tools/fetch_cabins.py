#!/usr/bin/env python3
"""Fetch each departure's cabin ladder: what every berth costs and how many are left.

The dataset stores one price per sailing, the figure the vessel page
advertises. This reads what that figure is the bottom of -- every cabin's
price, its list price where it is discounted, how many berths the operator
claims remain, and what a solo diver is charged to have the cabin alone. See
``liveaboard.scrape.cabins`` for the markup and ``docs/sources/liveaboard.com``
for how the page was found.

Written as its own tool rather than folded into the crawl, and unlike
``fetch_itineraries.py`` it is **not incremental**:

**It needs no crawl to know what to ask for.** ``data/archive.json`` is
committed and every ``Event`` carries an ``@id`` of the form
``LA-{x}-{boatID}-{tourID}``. Both ids come straight out of the repository.

**Every departure must be re-read, every run.** A trip's reefs do not change
overnight, so the itinerary book is fetched once per trip and never again. A
berth count changes the moment somebody books, and a discount ends. That makes
this one request per *departure* -- about 890 a night -- which is a real cost
rather than a free one, and the reason it is capped by default in CI and run
deliberately here.

**A capped run merges, it never replaces.** ``--limit N`` visits N departures
and leaves the rest of the book alone: a run knows nothing about the sailings
it did not visit. Same rule as ``scrape_fees.py --limit`` and for the same
reason -- an earlier version of the itinerary fetcher rewrote its whole book
from a six-trip run and dropped 309 records.

**What it reads is a claim with a date on it.** "only 2 spaces left!" is red
marketing text as much as inventory, and even the attribute behind it is the
operator's word. Every record carries the day it was read, and anything
rendering it has to say so.

    python3 tools/fetch_cabins.py [--limit N] [--delay 2] [--tours 6240:415714]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from liveaboard.scrape.base import PoliteFetcher  # noqa: E402
from liveaboard.scrape.cabins import parse_cabins  # noqa: E402
from liveaboard.scrape.liveaboard_com import HOST  # noqa: E402

EVENT_ID = re.compile(r"^LA-(\d+)-(\d+)-(\d+)$")


def endpoint(boat_id: str, tour_id: str) -> str:
    return f"https://{HOST}/BookingStep1?tourid={tour_id}&boatid={boat_id}"


def wanted(archive: dict[str, Any]) -> dict[str, dict[str, str]]:
    """``{tour_id: {...}}``, one entry per sailing.

    Keyed by tour id because that is what the booking page takes and what the
    archive states; a departure id is built later, in promote, from the boat
    and the date.
    """
    out: dict[str, dict[str, str]] = {}
    for page in archive.get("pages", []):
        slug = page["url"].split("?")[0].rstrip("/").rsplit("/", 1)[-1]
        for node in page.get("nodes", []):
            if node.get("@type") != "Event":
                continue
            match = EVENT_ID.match(node.get("@id") or "")
            if not match:
                continue
            _, boat_id, tour_id = match.groups()
            offer = node.get("offers") or {}
            if isinstance(offer, list):
                offer = offer[0] if offer else {}
            out.setdefault(tour_id, {
                "boat": slug,
                "boat_id": boat_id,
                "tour_id": tour_id,
                "name": " ".join((node.get("name") or "").split()),
                "start": (node.get("startDate") or "")[:10],
                # What the vessel page advertised, so the cheapest cabin can be
                # checked against it rather than assumed to match.
                "advertised": offer.get("price"),
                # The currency the page was asked for. Never re-derived from
                # the glyph beside the price: "$" is four currencies this site
                # sells in and the booking page renders the session's.
                "currency": offer.get("priceCurrency") or "USD",
            })
    return out


def load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("departures") or {}
    except (OSError, ValueError) as exc:
        print(f"could not read {path} ({exc}); starting fresh", file=sys.stderr)
        return {}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", default=Path("data/archive.json"), type=Path)
    parser.add_argument("--out", default=Path("data/cabins.json"), type=Path)
    parser.add_argument("--snapshots", default=Path("data/snapshots"), type=Path)
    parser.add_argument("--limit", type=int, default=0, help="cap fetches (0 = all)")
    parser.add_argument("--delay", type=float, default=2.0)
    parser.add_argument("--tours", default="",
                        help="explicit boatid:tourid pairs, for proving a change")
    args = parser.parse_args()

    if not args.archive.exists():
        print(f"no {args.archive}; run a scrape first", file=sys.stderr)
        return 1

    sailings = wanted(json.loads(args.archive.read_text(encoding="utf-8")))
    book = load(args.out)

    if args.tours:
        todo = []
        for pair in args.tours.split(","):
            boat, _, tour = pair.strip().partition(":")
            todo.append(tour)
            sailings.setdefault(tour, {
                "boat": "(named on the command line)", "boat_id": boat,
                "tour_id": tour, "name": "", "start": "",
                "advertised": None, "currency": "USD",
            })
    else:
        # Soonest first: a berth count matters most on the sailings people are
        # booking now, and a capped run should spend its requests there.
        todo = sorted(sailings, key=lambda t: (sailings[t]["start"], t))
        if args.limit:
            todo = todo[: args.limit]

    print(f"{len(sailings)} sailing(s) in the archive, {len(book)} already read, "
          f"{len(todo)} to fetch")
    if not todo:
        print("nothing to do")
        return 0

    fetcher = PoliteFetcher(snapshot_dir=args.snapshots, delay=args.delay)
    today = date.today().isoformat()
    read = failed = nothing = disagreed = 0

    for index, tour in enumerate(todo, 1):
        entry = sailings[tour]
        url = endpoint(entry["boat_id"], tour)
        try:
            result = fetcher.get(url)
        except Exception as exc:  # noqa: BLE001 - one bad page must not end the run
            print(f"  [{index}/{len(todo)}] {entry['boat']}: {exc}", flush=True)
            failed += 1
            continue

        reading = parse_cabins(result.body, entry["currency"])
        for warning in reading.warnings:
            print(f"      ! {warning}", flush=True)
            disagreed += 1

        if not reading.cabins:
            # Two different silences, and they must not be written the same
            # way. A page that listed its cabins and offered none is a full
            # boat, which is an answer; a page with no cabin markup at all
            # answers nothing, and writing it as "no berths" would publish a
            # sold-out sign for a page that merely failed.
            if reading.listed_only:
                book[tour] = {
                    "boat": entry["boat"], "tour_id": tour,
                    "start": entry["start"], "name": entry["name"],
                    "collected": today, "currency": entry["currency"],
                    "cabins": [], "nothing_bookable": True,
                    "source_url": url,
                }
                print(f"  [{index}/{len(todo)}] {entry['boat']:22.22} "
                      f"{entry['start']}  nothing bookable "
                      f"({reading.listed_only} cabin(s) listed)", flush=True)
            else:
                print(f"  [{index}/{len(todo)}] {entry['boat']:22.22} "
                      f"{entry['start']}  no cabin markup; left as it was",
                      flush=True)
                nothing += 1
            continue

        cheapest = reading.cheapest
        advertised = entry.get("advertised")
        note = ""
        if advertised and cheapest and cheapest.price is not None:
            # The claim this whole tool rests on: the advertised price is the
            # cheapest cabin's. Checked on every sailing rather than assumed
            # from the two it was established on.
            if abs(float(advertised) - cheapest.price) > 0.5:
                note = f"  (advertised {advertised}, cheapest {cheapest.price:g})"

        book[tour] = {
            "boat": entry["boat"],
            "tour_id": tour,
            "start": entry["start"],
            "name": entry["name"],
            "collected": today,
            "currency": reading.currency,
            "advertised": advertised,
            "cabins": [c.as_dict() for c in reading.cabins],
            "source_url": url,
        }
        read += 1
        print(f"  [{index}/{len(todo)}] {entry['boat']:22.22} {entry['start']}  "
              f"{len(reading.cabins)} cabin(s), from {cheapest.price:g} "
              f"{reading.currency}, {reading.berths_at_cheapest} berth(s) "
              f"at it{note}", flush=True)

    if not read and not any(v.get("nothing_bookable") for v in book.values()):
        # A run that read nothing must not rewrite the file. The only thing
        # that would change is the collected date, which would report the book
        # as fresh on the strength of a few hundred failed requests.
        print(f"\nnothing read; {args.out} left as it was ({failed} failed)")
        return 1 if failed else 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "collected": today,
                "source": "liveaboard.com",
                "note": (
                    "One cabin ladder per departure, from /BookingStep1. Every "
                    "price, the list price where the cabin is discounted, the "
                    "berths the operator claims remain (data-allocation, not "
                    "the red banner, which only appears at four or fewer) and "
                    "the stated single-occupancy surcharge. Berth counts are "
                    "the operator's claim on the day in `collected`, not "
                    "verified inventory, and they go stale within hours. Not "
                    "incremental: every departure is re-read every run."
                ),
                "departures": dict(sorted(book.items())),
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\nwrote {args.out}: {len(book)} departure(s) ({read} read this run), "
          f"{failed} failed, {nothing} unreadable, {disagreed} warning(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
