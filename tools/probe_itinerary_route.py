#!/usr/bin/env python3
"""Read the one heading on the itinerary fragment nothing has ever parsed.

Every fragment carries four headings -- Overview, Route, What to expect, Key
regions -- on all 67 vessels. Three are read. "Route" is not, and it is the
only one whose name suggests it says where the boat goes.

That matters now because the plan is to take dive sites from the description
and the route and to stop trusting the curated "Key regions" list, which is
demonstrably wrong on real trips: All Star Red Sea's "North & Brothers" week
lists Daedalus, 180 km from anywhere its own day plan visits.

So this prints the markup around the Route heading rather than guessing at it,
which is the rule -- no parser for a page nobody has read in full. It writes
nothing and parses nothing into the dataset.

    python3 tools/probe_itinerary_route.py [--boats 6] [--chars 1400]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from liveaboard.scrape.liveaboard_com import HOST  # noqa: E402

EVENT_ID = re.compile(r"^LA-(\d+)-(\d+)-(\d+)$")
ARCHIVE = Path("data/archive.json")

USER_AGENT = (
    "LiveaboardPriceTransparency/0.1 "
    "(+https://github.com/PaludaNCode/Liveaboard; research, low volume)"
)

ROUTE = re.compile(r"Route\s*</h\d>(.*?)(?=<h\d|\Z)", re.I | re.S)
TAG = re.compile(r"<[^>]+>")


def endpoint(boat_id: str, tour_id: str) -> str:
    return (
        f"https://{HOST}/itinerary/getpopupv2"
        f"?boatID={boat_id}&tourID={tour_id}&languageID=1&curr=USD&showPrices=false"
    )


def one_trip_per_boat() -> list[tuple[str, str, str, str]]:
    archive = json.loads(ARCHIVE.read_text(encoding="utf-8"))
    seen: dict[str, tuple[str, str, str, str]] = {}
    for page in archive["pages"]:
        slug = page["url"].split("?")[0].rstrip("/").rsplit("/", 1)[-1]
        for node in page.get("nodes", []):
            if node.get("@type") != "Event":
                continue
            match = EVENT_ID.match(node.get("@id") or "")
            if not match:
                continue
            _, boat_id, tour_id = match.groups()
            name = " ".join((node.get("name") or "").split())
            seen.setdefault(slug, (slug, boat_id, tour_id, name))
    return list(seen.values())


def fetch(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        print(f"    HTTP {exc.code}")
        return ""
    except Exception as exc:  # noqa: BLE001 - one bad boat must not end the run
        print(f"    failed: {exc}")
        return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--boats", type=int, default=6, help="vessels to read")
    parser.add_argument("--chars", type=int, default=1400,
                        help="markup to print after the heading")
    args = parser.parse_args()

    boats = one_trip_per_boat()[: args.boats] if args.boats else one_trip_per_boat()
    print(f"{len(boats)} vessels, one trip each\n")

    shapes: Counter[str] = Counter()
    found = 0

    for n, (slug, boat_id, tour_id, name) in enumerate(boats, 1):
        body = fetch(endpoint(boat_id, tour_id))
        if not body:
            continue
        match = ROUTE.search(body)
        print("=" * 72)
        print(f"[{n}/{len(boats)}] {slug} -- {name[:48]}")
        if not match:
            print("   no Route block")
            shapes["absent"] += 1
            continue
        found += 1
        block = match.group(1)
        text = " ".join(TAG.sub(" ", block).split())
        shapes[f"{len(block)//500*500}-{len(block)//500*500+499} bytes"] += 1
        print(f"--- text ({len(text)} chars) ---")
        print("   " + (text[:600] or "(empty)"))
        print(f"--- raw markup, first {args.chars} ---")
        print(block[: args.chars])

    print("\n================ SUMMARY ================")
    print(f"vessels with a Route block: {found}/{len(boats)}")
    for shape, count in shapes.most_common():
        print(f"   {count:3}  {shape}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
