#!/usr/bin/env python3
"""Carry one whole vessel page back, and say what the shipped reader makes of it.

On 2026-10-08 every hull began reading as ``N in season of 10   0 fee
block(s)`` (#157): the capped ten Events still parse, and the ``TouristTrip``
chain and the fee panels in the streamed payload both read nothing. Both
broke on the same morning with no code change here, so the page changed —
and every fixture in ``tests/fixtures`` is a cut-down piece of the old page,
which is exactly the wrong evidence for "what does it look like now".

So this prints a census first — the JSON-LD types, the payload chunks and
how many decoded, and the markers the reader looks for, counted in the raw
bytes and in the decoded payload — and then the **whole page** as
gzip+base64 for ``tools/land_divebooker.py``. The census is printed last of
the two so the answer is at the end of the job log.

Writes nothing.

    python3 tools/probe_divebooker_page.py --vessels topaz
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fetch_divebooker import emit  # noqa: E402
from liveaboard.scrape import divebooker_com as db  # noqa: E402
from liveaboard.scrape import jsonld  # noqa: E402
from liveaboard.scrape.base import PoliteFetcher  # noqa: E402
from probe_divebooker import repair_robots  # noqa: E402

MARKERS = ("TouristTrip", "subjectOf", "itemOffered", "\"details\"",
           "boatSpecials", "__next_f", "application/ld+json", "tripId")


def census(html: str) -> list[str]:
    lines = [f"page: {len(html)} chars"]
    types: Counter[str] = Counter()
    for node in jsonld.walk_documents(html):
        for name in db._types(node):
            types[name] += 1
    lines.append(f"  json-ld types : {dict(types.most_common())}")
    lines.append(f"  ld+json blocks: {len(jsonld.extract_blocks(html))}")
    pushes = re.findall(r"self\.__next_f\.push\(\[[^\]]{0,12}", html)
    lines.append(f"  __next_f pushes (raw): {len(pushes)}; "
                 f"heads {Counter(p[:24] for p in pushes).most_common(4)}")
    text, dropped = db.payload_parts(html)
    lines.append(f"  FLIGHT matches: {len(db.FLIGHT.findall(html))}, decoded "
                 f"{len(text)} chars, {dropped} undecodable")
    for marker in MARKERS:
        lines.append(f"  {marker:<22} raw {html.count(marker):>5}   "
                     f"payload {text.count(marker):>5}")
    book = db.vessel(html, "/probe")
    lines.append(f"  shipped reader: {len(book.departures)} departure(s), "
                 f"{len(book.fees)} fee block(s), {len(book.specials)} "
                 f"special(s), {len(book.warnings)} warning(s)")
    for warning in book.warnings[:5]:
        lines.append(f"    ! {warning}")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vessels", default="topaz",
                        help="slugs as the committed book names them")
    parser.add_argument("--book", default=Path("data/divebooker.json"), type=Path)
    parser.add_argument("--delay", type=float, default=5.0)
    parser.add_argument("--snapshots", default=Path("data/snapshots"), type=Path)
    parser.add_argument("--no-emit", action="store_true",
                        help="print the census only")
    args = parser.parse_args()

    vessels = json.loads(args.book.read_text(encoding="utf-8")).get("vessels") or {}
    fetcher = PoliteFetcher(snapshot_dir=args.snapshots, delay=args.delay)
    for host in (db.HOST, f"www.{db.HOST}"):
        repair_robots(fetcher, host)

    report: list[str] = []
    for slug in (s.strip() for s in args.vessels.split(",") if s.strip()):
        record = vessels.get(slug)
        if not record or not record.get("url"):
            report.append(f"{slug} is not in {args.book}")
            continue
        try:
            result = fetcher.get(record["url"])
        except Exception as exc:  # noqa: BLE001 - say why, ask the next one
            report.append(f"{slug}: unread ({exc})")
            continue
        if not args.no_emit:
            emit(f"{slug}.html", result.body.encode("utf-8"))
        report.append(f"== {slug} ({record['url']}) ==")
        report.extend(census(result.body))

    print("\n".join(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
