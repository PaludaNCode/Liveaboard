#!/usr/bin/env python3
"""Find out what happened to the vessels the Egypt listings stopped linking.

``data/barren.json`` holds vessels that published no departure when last
fetched. Thirteen of its fifteen entries are not linked from any Egypt listing
any more, so the crawl never reaches them at all -- and a vessel the crawl
cannot see is indistinguishable, from inside the dataset, from one that does
not exist. Three very different things look identical from here:

1. **Gone from the site.** The vessel page 404s. Nothing to do; the skip list
   is carrying dead names and can say so.
2. **Still sold, no longer linked from Egypt.** The page is alive and carries
   ``Event`` nodes for the season. This is the one that matters: the crawl is
   *losing real, bookable trips* because discovery cannot see them, and the
   fix is in ``discover()`` rather than anywhere near the skip list.
3. **Alive and genuinely empty.** The page is up, carries a ``Product`` and no
   ``Event`` for any season month. The skip list is right and the delisting is
   the site agreeing with it.

Bounded on purpose: the bare vessel page is fetched first, and the four season
months only if that page exists. Thirteen dead vessels cost thirteen requests
rather than fifty-two.

Writes nothing and parses nothing into the dataset.

    python3 tools/probe_delisted.py [--barren data/barren.json] [--vessels a,b]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from liveaboard.scrape import jsonld  # noqa: E402
from liveaboard.scrape.base import FetchBlocked, PoliteFetcher  # noqa: E402
from liveaboard.scrape.liveaboard_com import (  # noqa: E402
    DESTINATION,
    DESTINATION_PATHS,
    HOST,
    SEASON_QUERIES,
    LiveaboardComAdapter,
    search_paths,
)


def linked_now(fetcher: PoliteFetcher) -> set[str]:
    """Vessel slugs the listings link today, by the crawl's own rule."""
    slugs: set[str] = set()
    for path in search_paths() + DESTINATION_PATHS:
        try:
            listing = fetcher.get(f"https://{HOST}{path}")
        except Exception as exc:  # noqa: BLE001 - one dead listing is not the answer
            print(f"  ! {path}: {exc}")
            continue
        for link in LiveaboardComAdapter.boat_links(listing.body):
            slugs.add(link.rstrip("/").rsplit("/", 1)[-1])
    return slugs


def count_nodes(body: str) -> tuple[int, int]:
    try:
        return (len(jsonld.of_type(body, "Event", "TouristTrip", "Trip")),
                len(jsonld.of_type(body, "Product")))
    except Exception:  # noqa: BLE001 - a probe must not die on one odd body
        return (0, 0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--barren", default=Path("data/barren.json"), type=Path)
    parser.add_argument("--snapshots", default=Path("data/snapshots"), type=Path)
    parser.add_argument("--vessels", default="",
                        help="comma-separated slugs, instead of the unlinked ones")
    args = parser.parse_args()

    fetcher = PoliteFetcher(snapshot_dir=args.snapshots)

    if args.vessels:
        targets = sorted({v.strip() for v in args.vessels.split(",") if v.strip()})
        linked: set[str] = set()
    else:
        if not args.barren.exists():
            print(f"no {args.barren}; nothing to probe")
            return 0
        skipped = set(json.loads(args.barren.read_text(encoding="utf-8"))["vessels"])
        print(f"== what the listings link today ==")
        linked = linked_now(fetcher)
        print(f"  {len(linked)} vessel(s) linked from {len(search_paths()) + len(DESTINATION_PATHS)} listing pages")
        targets = sorted(skipped - linked)
        still = sorted(skipped & linked)
        if still:
            print(f"  {len(still)} skip-listed vessel(s) ARE still linked: {', '.join(still)}")
        print(f"  {len(targets)} skip-listed vessel(s) are not linked at all\n")

    if not targets:
        print("every skip-listed vessel is still linked; nothing is being lost")
        return 0

    gone, empty, lost, odd = [], [], [], []

    for n, slug in enumerate(targets, 1):
        url = f"https://{HOST}/diving/{DESTINATION}/{slug}"
        print("=" * 78)
        print(f"[{n}/{len(targets)}] {slug}")
        try:
            result = fetcher.get(url)
        except FetchBlocked as exc:
            print(f"    page: unreachable — {exc}")
            gone.append(slug)
            continue
        except Exception as exc:  # noqa: BLE001
            print(f"    page: failed — {exc}")
            odd.append(slug)
            continue

        events, products = count_nodes(result.body)
        print(f"    page: {result.status}, {len(result.body):,} bytes, "
              f"{events} Event, {products} Product")
        if not products and not events:
            print("    -> no structured data; the vessel page is not a vessel page")
            odd.append(slug)
            continue

        # Only now the four months, and only for a page that exists.
        per_month = {}
        for query in SEASON_QUERIES:
            try:
                month = fetcher.get(f"{url}{query}")
            except Exception as exc:  # noqa: BLE001
                print(f"    {query}: failed — {exc}")
                continue
            found, _ = count_nodes(month.body)
            per_month[query] = found
        total = sum(per_month.values())
        print(f"    season: {total} Event(s) across "
              f"{', '.join(f'{q[3:]}={c}' for q, c in per_month.items())}")

        if total:
            lost.append((slug, total))
            print("    -> ALIVE AND SELLING, and the listings do not link it."
                  "\n       These are real trips the crawl cannot currently see.")
        else:
            empty.append(slug)
            print("    -> alive, sells nothing this season; the skip list agrees")

    print("\n" + "=" * 78)
    print("SUMMARY")
    print(f"  alive and selling, but unlinked : {len(lost)}")
    print(f"  alive, genuinely empty          : {len(empty)}")
    print(f"  page gone or unreachable        : {len(gone)}")
    print(f"  no structured data              : {len(odd)}")
    if lost:
        print()
        for slug, total in lost:
            print(f"    LOSING {total} departure(s): {slug}")
        print("\n  discover() is the fix, not the skip list: these vessels are")
        print("  reachable by URL and absent from every listing the crawl reads.")
    elif empty and not gone:
        print("\n  Nothing is being lost. The listings and the skip list agree.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
