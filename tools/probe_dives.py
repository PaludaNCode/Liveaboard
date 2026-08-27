#!/usr/bin/env python3
"""Find out whether a per-trip dive count can be sourced rather than assumed.

Price per dive is the number divers actually compare on, and the dataset
carries ``dives: 0`` for all 313 itineraries because nothing has ever looked
for it. Before adding a column, this establishes where the count lives and how
many vessels state one.

Deriving it from nights is the obvious shortcut and the wrong one: at a fixed
dives-per-day the result is a constant multiple of price per night, so the
column would carry no information its denominator did not invent. Worth doing
only if the number is real, or if a stated convention covers enough trips to
beat silence.

Run from CI; the sandbox cannot reach the host.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from liveaboard.scrape import jsonld  # noqa: E402
from liveaboard.scrape.base import PoliteFetcher  # noqa: E402
from liveaboard.scrape.liveaboard_com import (  # noqa: E402
    HOST,
    SEASON_QUERIES,
    LiveaboardComAdapter,
    _page_text,
    search_paths,
)

# Every way a page might state a count. Kept broad on purpose: the point is to
# see what the site says, not to confirm a guess about how it says it.
PATTERNS = (
    re.compile(r"\b(?:up to\s+)?(\d{1,2})\s*dives?\b", re.I),
    re.compile(r"\bdives?\s*[:=]\s*(\d{1,2})\b", re.I),
    re.compile(r"\b(\d{1,2})\s*dives?\s*(?:per|a|/)\s*(?:day|week|trip)\b", re.I),
    re.compile(r"\b(\d{1,2})\s*(?:dives?\s*)?(?:included|total)\b", re.I),
)

CONTEXT = 90


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=8, help="vessels to inspect")
    parser.add_argument("--snapshots", default=Path("data/snapshots"), type=Path)
    args = parser.parse_args()

    fetcher = PoliteFetcher(snapshot_dir=args.snapshots)

    listing = fetcher.get(f"https://{HOST}{search_paths()[0]}")
    links = sorted(LiveaboardComAdapter.boat_links(listing.body))[: args.limit]
    print(f"inspecting {len(links)} vessels\n")

    with_count = 0
    phrasing: Counter[str] = Counter()
    values: Counter[str] = Counter()

    for link in links:
        url = f"https://{HOST}{link}{SEASON_QUERIES[0]}"
        slug = link.rstrip("/").rsplit("/", 1)[-1]
        result = fetcher.get(url)
        text = " ".join(_page_text(result.body).split())

        found: list[str] = []
        for pattern in PATTERNS:
            for match in pattern.finditer(text):
                start = max(0, match.start() - CONTEXT // 2)
                found.append(text[start : match.end() + CONTEXT // 2])
                values[match.group(1)] += 1
                phrasing[pattern.pattern[:34]] += 1

        # Structured data would be far better than prose. Check whether any
        # node carries something dive-shaped before settling for text.
        nodes = jsonld.of_type(result.body, "Product", "Event", "TouristTrip", "Trip")
        keys = {
            key
            for node in nodes
            for key in node
            if "dive" in key.lower() or "duration" in key.lower()
        }

        print(f"-- {slug}")
        print(f"   json-ld dive/duration keys: {sorted(keys) or 'none'}")
        if found:
            with_count += 1
            for snippet in found[:3]:
                print(f"   text: ...{snippet}...")
        else:
            print("   text: no dive count stated")

    print(f"\n{with_count} of {len(links)} vessels state something dive-count shaped")
    print(f"phrasings hit: {dict(phrasing)}")
    print(f"numbers seen:  {dict(values.most_common(12))}")
    print(
        "\nA count that appears on most vessels and reads per-trip is worth "
        "parsing.\nOne that is a vessel-level 'up to N weekly' is marketing, "
        "and dividing a\nprice by it would understate every trip that does "
        "fewer."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
