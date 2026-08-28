#!/usr/bin/env python3
"""Establish what the "What to expect" prose looks like on every vessel.

A parser was written against one fragment -- a hand-trimmed fixture -- and it
matched nothing at all on six real trips. That is the failure CLAUDE.md warns
about in as many words: do not write markup parsers for pages nobody has
fetched. One page had been fetched, and a trimmed copy of it was treated as
representative of 315.

So this reads one trip from each of the 67 vessels and reports the *shape* of
what comes back rather than a guess at it: which headings occur, what the day
markers are made of, and whether the current pattern matches. It writes nothing
and parses nothing into the dataset.

    python3 tools/probe_itinerary_prose.py [--boats 0] [--dump 3]
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

from liveaboard.scrape.itinerary import EXPECT_BLOCK, parse_expect  # noqa: E402
from liveaboard.scrape.liveaboard_com import HOST  # noqa: E402

EVENT_ID = re.compile(r"^LA-(\d+)-(\d+)-(\d+)$")
ARCHIVE = Path("data/archive.json")

USER_AGENT = (
    "LiveaboardPriceTransparency/0.1 "
    "(+https://github.com/PaludaNCode/Liveaboard; research, low volume)"
)

# Deliberately loose: the point is to find out what is there, not to assume.
HEADING = re.compile(r"<h(\d)[^>]*>\s*([^<]{2,40}?)\s*</h\1>", re.I)
DAY_ANY = re.compile(r"(<[a-z0-9]+[^>]*>)?\s*(Day\s*\d+)", re.I)
BOLD_DAY = re.compile(r"<(strong|b)>\s*Day\s*\d+[^<]*</\1>", re.I)
PROSE_DIV = re.compile(r'<div[^>]*class=["\']?prose', re.I)


def endpoint(boat_id: str, tour_id: str) -> str:
    return (
        f"https://{HOST}/itinerary/getpopupv2"
        f"?boatID={boat_id}&tourID={tour_id}&languageID=1&curr=USD&showPrices=false"
    )


def one_trip_per_boat() -> list[tuple[str, str, str, str]]:
    """``(slug, boatID, tourID, name)``, one per vessel.

    One per boat rather than per trip: the question here is whether the page
    template differs, and a template belongs to the site, not the sailing. 67
    requests answers it; 315 would mostly re-read the same shape.
    """
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
    parser.add_argument("--boats", type=int, default=0, help="cap vessels (0 = all)")
    parser.add_argument("--dump", type=int, default=3,
                        help="fragments to print whole where the pattern misses")
    args = parser.parse_args()

    boats = one_trip_per_boat()
    if args.boats:
        boats = boats[: args.boats]
    print(f"{len(boats)} vessels, one trip each\n")

    headings: Counter[str] = Counter()
    shapes: Counter[str] = Counter()
    day_markup: Counter[str] = Counter()
    matched = has_days = empty = 0
    dumped = 0
    misses: list[str] = []

    for n, (slug, boat_id, tour_id, name) in enumerate(boats, 1):
        body = fetch(endpoint(boat_id, tour_id))
        if not body:
            shapes["fetch failed"] += 1
            continue

        for _, text in HEADING.findall(body):
            headings[text] += 1

        block = EXPECT_BLOCK.search(body)
        intro, days = parse_expect(body)
        bold = BOLD_DAY.findall(body)
        any_day = DAY_ANY.findall(body)
        if any_day:
            has_days += 1
            for tag, _ in any_day[:4]:
                day_markup[(tag or "(bare text)").split()[0].rstrip(">") + ">"] += 1

        shape = (
            f"prose_div={'y' if PROSE_DIV.search(body) else 'n'} "
            f"expect_block={'y' if block else 'n'} "
            f"day_markers={len(any_day)} bold_days={len(bold)} "
            f"parsed_days={len(days)}"
        )
        shapes[shape] += 1
        if days:
            matched += 1
        else:
            empty += 1
            misses.append(slug)
            if any_day and dumped < args.dump:
                dumped += 1
                print(f"--- {slug}: has day markers, parser found none ---")
                start = max(0, body.lower().find("what to expect") - 300)
                print(body[start:start + 2000] if start else body[:2000])
                print("--- end ---\n")

        print(f"  [{n}/{len(boats)}] {slug:24.24} {shape}", flush=True)

    print("\n================ SUMMARY ================")
    print(f"vessels read              : {len(boats)}")
    print(f"parser produced days      : {matched}")
    print(f"parser produced none      : {empty}")
    print(f"page mentions 'Day N'     : {has_days}")
    print("\nstructures seen:")
    for shape, count in shapes.most_common():
        print(f"   {count:4}  {shape}")
    print("\nwhat a day marker is wrapped in:")
    for tag, count in day_markup.most_common(10):
        print(f"   {count:4}  {tag}")
    print("\nheadings on these pages:")
    for text, count in headings.most_common(20):
        print(f"   {count:4}  {text!r}")
    if misses:
        print(f"\nvessels the parser found nothing on ({len(misses)}):")
        print("   " + ", ".join(misses[:40]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
