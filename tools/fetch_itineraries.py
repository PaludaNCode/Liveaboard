#!/usr/bin/env python3
"""Fetch each trip's itinerary fragment and build the itinerary book.

The vessel pages say where a boat sails all year. This says where *one trip*
goes, in the operator's own words, and it also states that trip's dive count,
its group size and its entry bar. See ``liveaboard.scrape.itinerary`` for the
markup and ``docs/sources/liveaboard.com.md`` for how the endpoint was found.

Written as its own tool rather than folded into the crawl, for three reasons:

**It needs no crawl to know what to ask for.** ``data/archive.json`` is
committed and holds every ``Event`` node, and each one carries an ``@id`` of
the form ``LA-{x}-{boatID}-{tourID}``. Both ids come straight out of the
repository.

**It is incremental.** A trip's sites and dive count do not change from night
to night, so a fragment already in the book is not fetched again. The first run
costs about 340 requests; the steady state costs one per genuinely new trip,
which is the difference between a polite tool and a rude one.

**It is one request per itinerary, not per departure.** 878 tour ids exist, one
per sailing, but every departure of one trip returns the same answer. Fetching
all of them would spend 538 requests re-reading what was already in hand.

    python3 tools/fetch_itineraries.py [--limit N] [--refresh] [--delay 2]
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

from liveaboard.promote import _sites_from_name, itinerary_key  # noqa: E402
from liveaboard.scrape.base import PoliteFetcher  # noqa: E402
from liveaboard.scrape.itinerary import min_logged_dives, parse_trip  # noqa: E402
from liveaboard.scrape.liveaboard_com import HOST  # noqa: E402

EVENT_ID = re.compile(r"^LA-(\d+)-(\d+)-(\d+)$")


def endpoint(boat_id: str, tour_id: str) -> str:
    return (
        f"https://{HOST}/itinerary/getpopupv2"
        f"?boatID={boat_id}&tourID={tour_id}&languageID=1&curr=USD&showPrices=false"
    )


def wanted(archive: dict[str, Any]) -> dict[str, tuple[str, str, str, str]]:
    """``{key: (slug, boatID, tourID, name)}``, one tour per itinerary.

    Keyed by :func:`liveaboard.promote.itinerary_key`, so the book this writes
    is looked up by promote with the identical rule rather than a copy of it.
    """
    out: dict[str, tuple[str, str, str, str]] = {}
    for page in archive.get("pages", []):
        slug = page["url"].split("?")[0].rstrip("/").rsplit("/", 1)[-1]
        for node in page.get("nodes", []):
            if node.get("@type") != "Event":
                continue
            match = EVENT_ID.match(node.get("@id") or "")
            if not match:
                continue
            _, boat_id, tour_id = match.groups()
            name = " ".join((node.get("name") or "").split())
            if not name:
                continue
            out.setdefault(itinerary_key(slug, name), (slug, boat_id, tour_id, name))
    return out


def load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("trips") or {}
    except (OSError, ValueError) as exc:
        print(f"could not read {path} ({exc}); starting fresh", file=sys.stderr)
        return {}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", default=Path("data/archive.json"), type=Path)
    parser.add_argument("--out", default=Path("data/itineraries.json"), type=Path)
    parser.add_argument("--snapshots", default=Path("data/snapshots"), type=Path)
    parser.add_argument("--limit", type=int, default=0, help="cap fetches (0 = all)")
    parser.add_argument("--delay", type=float, default=2.0)
    parser.add_argument(
        "--refresh", action="store_true",
        help="re-fetch trips already in the book, rather than only the new ones",
    )
    args = parser.parse_args()

    if not args.archive.exists():
        print(f"no {args.archive}; run a scrape first", file=sys.stderr)
        return 1

    archive = json.loads(args.archive.read_text(encoding="utf-8"))
    trips = wanted(archive)
    book = {} if args.refresh else load(args.out)

    todo = [k for k in trips if k not in book]
    if args.limit:
        todo = todo[: args.limit]
    print(f"{len(trips)} itineraries in the archive, {len(book)} already read, "
          f"{len(todo)} to fetch")
    if not todo:
        print("nothing to do")
        return 0

    fetcher = PoliteFetcher(snapshot_dir=args.snapshots, delay=args.delay)
    unknown: dict[str, int] = {}
    failed = 0
    added = 0

    for index, entry in enumerate(todo, 1):
        slug, boat_id, tour_id, name = trips[entry]
        try:
            result = fetcher.get(endpoint(boat_id, tour_id))
        except Exception as exc:  # noqa: BLE001 - one bad trip must not end the run
            print(f"  [{index}/{len(todo)}] {slug}: {exc}", flush=True)
            failed += 1
            continue

        detail = parse_trip(result.body)
        if not detail:
            print(f"  [{index}/{len(todo)}] {slug}: {name[:40]}: nothing parsed", flush=True)
            failed += 1
            continue

        # The regions are proper names -- "The Brothers" -- and the page filters
        # on canonical site keys. Folding them through the same recogniser the
        # titles use keeps one vocabulary on the page; anything it does not know
        # is reported rather than dropped silently, because a reef the operator
        # names and we cannot is a gap in SITE_HINTS worth closing deliberately.
        sites = _sites_from_name(" , ".join(detail.regions))
        for region in detail.regions:
            if not _sites_from_name(region):
                unknown[region] = unknown.get(region, 0) + 1

        book[entry] = {
            "boat": slug,
            "name": name,
            "tour_id": tour_id,
            "collected": date.today().isoformat(),
            "regions": list(detail.regions),
            "dive_sites": sites,
            "dives": detail.dives,
            "guests": detail.guests,
            "experience": detail.experience,
            "min_logged_dives": min_logged_dives(detail.experience),
            "source_url": endpoint(boat_id, tour_id),
        }
        added += 1
        print(
            f"  [{index}/{len(todo)}] {slug:22.22} {name[:34]:34} "
            f"sites={sites or '-'} dives={detail.dives or '-'}",
            flush=True,
        )

    if not added:
        # A run that read nothing must not rewrite the file: the only thing
        # that would change is the collected date, which would report the book
        # as fresh on the strength of 340 failed requests.
        print(f"\nnothing read; {args.out} left as it was ({failed} failed)")
        return 1 if failed else 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "collected": date.today().isoformat(),
                "source": "liveaboard.com",
                "note": (
                    "One itinerary fragment per trip, from /itinerary/getpopupv2. "
                    "Regions, dive count, group size and entry bar are the "
                    "operator's own words about this trip rather than the boat. "
                    "Incremental: a trip already here is not re-fetched."
                ),
                "trips": dict(sorted(book.items())),
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\nwrote {args.out}: {len(book)} trips ({added} new), {failed} failed")

    if unknown:
        # Not an error. A region the operator names and SITE_HINTS does not is
        # a reef worth adding by hand, and printing it is how that gets noticed.
        print(f"\n{len(unknown)} region(s) no site hint recognises:")
        for region, count in sorted(unknown.items(), key=lambda kv: -kv[1])[:20]:
            print(f"    {count:3}  {region}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
