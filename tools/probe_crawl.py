#!/usr/bin/env python3
"""Answer two questions about the crawl that a sandbox cannot ask.

A full run is 320 requests at five seconds apiece -- about half an hour -- and
roughly a third of those come back with nothing useful. Both the fixes depend
on facts only a live fetch can settle:

1. **What does the site actually ask for?** ``DEFAULT_DELAY_SECONDS`` is five
   seconds of our own choosing, and ``crawl_delay`` takes the larger of that
   and whatever robots.txt states. If the site states nothing, the pace is a
   courtesy we picked and may reconsider. If it states a number, that is the
   answer and there is nothing to discuss.

2. **Are the month listings actually filtered by month?** The crawler unions
   the boat links from all four season listings and then fetches every vessel
   for every month. If May's listing carries only vessels sailing in May, that
   is 96 requests thrown away each run. But if a listing is capped -- showing
   the first N of many -- then a vessel could be absent from May's page while
   still sailing in May, and skipping its May fetch would silently lose
   departures. Overlap between the four listings distinguishes the two.

Run from CI; a development sandbox cannot reach the host.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from liveaboard.scrape.base import DEFAULT_DELAY_SECONDS, PoliteFetcher  # noqa: E402
from liveaboard.scrape.liveaboard_com import (  # noqa: E402
    HOST,
    LiveaboardComAdapter,
    search_paths,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshots", default=Path("data/snapshots"), type=Path)
    args = parser.parse_args()

    fetcher = PoliteFetcher(snapshot_dir=args.snapshots)
    robots_url = f"https://{HOST}/robots.txt"

    print("== what the site asks for ==")
    stated = fetcher._robots_for(robots_url).crawl_delay(fetcher.user_agent)
    print(f"  robots.txt Crawl-delay : {stated if stated else 'not stated'}")
    print(f"  our default            : {DEFAULT_DELAY_SECONDS}s")
    print(f"  delay in force         : {fetcher.crawl_delay(robots_url)}s")
    print(f"  may we fetch a vessel  : {fetcher.allowed(f'https://{HOST}/diving/egypt/alia-soul')}")

    print("\n== are the month listings filtered by month ==")
    per_month: dict[str, set[str]] = {}
    for path in search_paths():
        result = fetcher.get(f"https://{HOST}{path}")
        links = LiveaboardComAdapter.boat_links(result.body)
        per_month[path] = links
        print(f"  {path:<40} {len(links):>3} vessels")

    everything: set[str] = set()
    for links in per_month.values():
        everything |= links
    print(f"\n  union across all four                    {len(everything):>3} vessels")

    shared = set.intersection(*per_month.values()) if per_month else set()
    print(f"  present on every listing                 {len(shared):>3} vessels")

    if len(shared) == len(everything):
        print(
            "\n  VERDICT: identical sets. The listings are NOT month-filtered, so\n"
            "  a vessel's absence proves nothing and per-month fetching cannot be\n"
            "  narrowed this way."
        )
    else:
        print(
            f"\n  VERDICT: the listings differ, so they are filtered by month.\n"
            f"  Fetching each vessel only for the months that list it would cut\n"
            f"  {len(everything) * len(per_month) - sum(len(v) for v in per_month.values())}"
            f" of {len(everything) * len(per_month)} vessel-month requests."
        )
        print(
            "\n  Before acting on that, check the counts above against the size of\n"
            "  the fleet. If every listing returns the same round number, it is a\n"
            "  page cap rather than a filter, and skipping fetches would drop real\n"
            "  departures."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
