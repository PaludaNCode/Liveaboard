#!/usr/bin/env python3
"""Read the seller's own search, which is the fleet this project mis-scoped.

`fetch_divebooker.py` discovers hulls from the Egypt country page, which links
**ten**. The seller's search says 75 for one month. The country page is a
landing page with a carousel on it, and reading a carousel as an inventory is
the same error as reading liveaboard.com's featured strip as its fleet.

`/boatsearch` is open to us. The `Disallow: /boatsearch` in robots.txt sits in
the file's `turnitinbot` record, not the `*` one — `docs/sources/
divebooker.com.md` carries the wrong sentence this project wrote about that,
and why.

The URL is the site's own: `?et=2&e=3881&ym=YYYYMM` — an entity type, the
Egypt id the country page already carries in its slug (`egypt-daz3881`), and
a year-month. So this asks, per month:

* how many hulls it links, and how that compares with the country page's ten;
* whether the count the page states agrees with the links it carries, because
  a number in prose and a list of links are two claims;
* whether the boats arrive in the streamed payload with their own fields
  (`minPrice`, `minPriceDay`, `currencyId`) rather than only as links.

Writes nothing.

    python3 tools/probe_divebooker_search.py --months 202705,202706
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from liveaboard.scrape import divebooker_com as db  # noqa: E402
from liveaboard.scrape.base import FetchBlocked, PoliteFetcher  # noqa: E402
from probe_divebooker import repair_robots  # noqa: E402

SEARCH = "/boatsearch?et={et}&e={entity}&ym={ym}"

#: A count the page prints about itself, however it words it.
STATED = re.compile(r"(\d{1,4})\s*(?:boats?|liveaboards?|results?|found)", re.I)
FLIGHT = re.compile(r'self\.__next_f\.push\(\[\d+,\s*(".*?")\]\)', re.S)
BOAT_FIELD = re.compile(r'"(minPrice|minPriceDay|currencyId|countReviews)"')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--et", default="2", help="entity type the site's own URL uses")
    parser.add_argument("--entity", default="3881", help="Egypt, from egypt-daz3881")
    parser.add_argument("--months", default="202705,202706,202707")
    parser.add_argument("--delay", type=float, default=5.0)
    parser.add_argument("--book", default=Path("data/divebooker.json"), type=Path)
    parser.add_argument("--snapshots", default=Path("data/snapshots"), type=Path)
    args = parser.parse_args()

    fetcher = PoliteFetcher(snapshot_dir=args.snapshots, delay=args.delay)
    for host in (db.HOST, f"www.{db.HOST}"):
        repair_robots(fetcher, host)

    known = set()
    if args.book.exists():
        known = set(json.loads(args.book.read_text(encoding="utf-8")).get("vessels") or {})
    print(f"the book holds {len(known)} hull(s) from the country page\n")

    every: set[str] = set()
    for ym in args.months.split(","):
        url = f"https://{db.HOST}" + SEARCH.format(et=args.et, entity=args.entity, ym=ym.strip())
        try:
            result = fetcher.get(url)
        except FetchBlocked as exc:
            print(f"BLOCKED {url}: {exc}")
            continue
        html = result.body
        hulls = db.hull_links(html)
        slugs = {db.split_slug(h)[0] for h in hulls}
        every |= slugs
        payload = "".join(json.loads(c) for c in FLIGHT.findall(html))
        stated = STATED.findall(html) + STATED.findall(payload)

        print(f"== {ym.strip()} ==")
        print(f"  {len(html) / 1024:.0f} KB · {len(hulls)} hull link(s)")
        print(f"  counts the page states: {sorted(set(stated), key=int, reverse=True)[:5] or 'none'}")
        print(f"  boat fields in the payload: "
              f"{sorted(set(BOAT_FIELD.findall(payload))) or 'none'}")
        new = sorted(slugs - known)
        print(f"  hulls the book does not have: {len(new)}")
        for slug in new[:20]:
            print(f"    {slug}")
        if len(new) > 20:
            print(f"    … and {len(new) - 20} more")

    print(f"\nacross every month asked: {len(every)} distinct hull(s), "
          f"{len(every - known)} of them new to the book")
    print("  If that is materially more than ten, the country page is a "
          "carousel and\n  the fetcher's discovery is what has to change.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
