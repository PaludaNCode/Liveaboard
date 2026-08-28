#!/usr/bin/env python3
"""Import what PADI states per trip: the entry bar, and a stated dive count.

Reads `/api/v2/travel/shop/...` for every boat in `data/padi_aliases.json` and
writes two files:

`data/padi_raw.json` -- committed
    Every field each response published, less two. Same principle as
    `data/archive.json`, and committed for the same reason: re-parsing must never
    need a re-crawl, and a field we start caring about next month would otherwise
    arrive attached to next month's data.

    It is large -- around 12 MB at full fleet, against archive.json's 1.8 MB --
    and rewritten whole on every refresh. That cost was weighed and accepted:
    290 requests against somebody else's server is the thing being avoided, and
    a parser fix that cannot be tested offline does not get tested.

    `photos` and `marineLife` are dropped on the way in. They are 17% of the
    payload and consist of CDN thumbnail URLs, and this site loads nothing
    external by invariant -- there is no version of it that can render them.
    Every textual field is kept, the fee structures included.

`data/padi.json`
    The book `promote` can merge, keyed on `promote.itinerary_key(boat_id,
    name)` -- the same key `data/itineraries.json` uses, so the two books match
    or fail together rather than each inventing a rule.

**Incremental and crash-safe.** An itinerary already in the raw store is not
re-fetched, and both files are written after every boat, so a run killed
halfway keeps what it got. `--limit N` merges rather than replaces, like
`scrape_fees.py --limit`.

    python3 tools/fetch_padi.py [--limit N] [--refresh]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import http.client
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from liveaboard.promote import itinerary_key  # noqa: E402
from liveaboard.scrape.padi_com import (  # noqa: E402
    ITINERARY_DETAIL,
    ITINERARY_LIST,
    PadiComAdapter,
)

UA = {"User-Agent": "Mozilla/5.0 (compatible; liveaboard-import/1.0; +price-transparency research)"}

DROP = ("photos", "marineLife")
"""Media arrays. See the module docstring: nothing here can ever render them."""
RAW = Path("data/padi_raw.json")
BOOK = Path("data/padi.json")


def get(url: str, timeout: int = 40) -> dict | None:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
            return json.loads(r.read())
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError,
            http.client.HTTPException, ConnectionError, json.JSONDecodeError):
        # http.client.RemoteDisconnected is not a URLError and killed a run at
        # boat 34 of 38, after 13 MB of responses were already on disk. One
        # dropped connection must cost the itinerary, not the run.
        return None


def load(path: Path, default: dict) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return dict(default)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--aliases", default="data/padi_aliases.json")
    parser.add_argument("--country", default="egypt")
    parser.add_argument("--limit", type=int, default=0, help="boats this run, 0 for all")
    parser.add_argument("--delay", type=float, default=1.2,
                        help="seconds between requests; the edge rate-limits AI agents at 30/min")
    parser.add_argument("--refresh", action="store_true",
                        help="re-fetch itineraries already stored")
    args = parser.parse_args()

    aliases = json.loads(Path(args.aliases).read_text())["aliases"]
    raw = load(RAW, {"fetched": "", "country": args.country, "itineraries": {}})
    book = load(BOOK, {"collected": "", "source": "padi.com", "trips": {}})
    stored: dict = raw["itineraries"]

    boats = sorted(aliases.items())
    if args.limit:
        boats = boats[: args.limit]
    print(f"{len(boats)} boats, {len(stored)} itineraries already stored\n")

    fetched = skipped = failed = 0
    for boat_id, slug in boats:
        listing = get(ITINERARY_LIST.format(vessel=slug))
        time.sleep(args.delay)
        if not listing or not listing.get("count"):
            print(f"{boat_id:<24} {slug:<28} no itineraries")
            continue

        print(f"{boat_id:<24} {slug:<28} {listing['count']} itineraries")
        for row in listing["results"]:
            store_key = f"{slug}::{row['slug']}"
            if store_key in stored and not args.refresh:
                skipped += 1
                continue
            detail = get(ITINERARY_DETAIL.format(
                country=args.country, vessel=slug, slug=row["slug"]))
            time.sleep(args.delay)
            if not detail:
                failed += 1
                print(f"    FAILED {row['slug']}")
                continue
            stored[store_key] = {k: v for k, v in detail.items() if k not in DROP}
            fetched += 1

        # Written per boat, so a killed run keeps what it got.
        raw["fetched"] = time.strftime("%Y-%m-%d")
        RAW.write_text(json.dumps(raw, indent=1, sort_keys=True) + "\n")

    # The book is rebuilt from the raw store every time, so it is always exactly
    # what the current parser makes of the archive -- the same relationship
    # `promote --check` enforces between the dataset and its inputs.
    trips: dict[str, dict] = {}
    for store_key, detail in stored.items():
        slug = store_key.split("::", 1)[0]
        boat_id = next((b for b, s in aliases.items() if s == slug), None)
        if not boat_id:
            continue
        record = PadiComAdapter.itinerary_from_payload(detail)
        name = record.get("name")
        if not name:
            continue
        record["boat"] = boat_id
        trips[itinerary_key(boat_id, str(name))] = record
    book["trips"] = trips
    book["collected"] = raw["fetched"]
    BOOK.write_text(json.dumps(book, indent=1, sort_keys=True) + "\n")

    with_bar = sum(1 for t in trips.values() if t.get("requirements"))
    with_dives = sum(1 for t in trips.values() if t.get("dives"))
    print(f"\nfetched {fetched}, skipped {skipped}, failed {failed}")
    print(f"{RAW}: {len(stored)} itineraries")
    print(f"{BOOK}: {len(trips)} keyed trips, {with_bar} with an entry bar, "
          f"{with_dives} with a stated dive count")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
