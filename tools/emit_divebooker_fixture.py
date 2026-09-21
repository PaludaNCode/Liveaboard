#!/usr/bin/env python3
"""Carry one vessel page's price panels back as a test fixture.

The sandbox this parser is written in cannot reach divebooker.com, so a
fixture has to travel through a job log — and a fee reader guarded by bytes
somebody imagined is a fee reader guarded by nothing. `tests/fixtures/
divebooker-bella-2.jsonld.json` was carried the same way and caught two bugs
before they shipped.

What it prints is the **verbatim** pair the parser needs: the enclosing trip
object's own `title`, and its `details` block whole. The test puts them back
into `self.__next_f.push([1, "…"])`, which is how the site serves them —
exactly what the JSON-LD fixture does with its blocks.

It prints what the shipped reader makes of them too, so the same run says
whether the parser reads real bytes and not only the four lines this project
happened to copy out of a census.

    python3 tools/emit_divebooker_fixture.py --vessels alsuraya,amelie
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from liveaboard.scrape import divebooker_com as db  # noqa: E402
from liveaboard.scrape.base import FetchBlocked, PoliteFetcher  # noqa: E402
from probe_divebooker import repair_robots  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vessels", default="alsuraya,amelie",
                        help="slugs as the committed book names them")
    parser.add_argument("--blocks", type=int, default=1,
                        help="panels to carry per hull")
    parser.add_argument("--book", default=Path("data/divebooker.json"), type=Path)
    parser.add_argument("--delay", type=float, default=5.0)
    parser.add_argument("--snapshots", default=Path("data/snapshots"), type=Path)
    args = parser.parse_args()

    vessels = json.loads(args.book.read_text(encoding="utf-8")).get("vessels") or {}
    fetcher = PoliteFetcher(snapshot_dir=args.snapshots, delay=args.delay)
    for host in (db.HOST, f"www.{db.HOST}"):
        repair_robots(fetcher, host)

    carried: list[dict] = []
    for slug in (s.strip() for s in args.vessels.split(",") if s.strip()):
        record = vessels.get(slug)
        if not record or not record.get("divebooker_id"):
            # Typing an id reads another boat, which is the mistake this
            # module has already made once.
            print(f"{slug} is not in {args.book}")
            continue
        path = f"/{slug}-{record['divebooker_id']}"
        try:
            result = fetcher.get(f"https://{db.HOST}{path}")
        except FetchBlocked as exc:
            print(f"BLOCKED {path}: {exc}")
            continue
        text, dropped = db.payload_parts(result.body)
        at = [m.end() for m in db.DETAILS_AT.finditer(text)]
        owners = db.enclosing(text, at)
        print(f"== {path} — {len(at)} panel(s), {dropped} chunk(s) undecoded ==")
        for position in at[: args.blocks]:
            chunk = db.balanced(text, position)
            bounds = owners.get(position)
            owner = json.loads(text[bounds[0]:bounds[1] + 1]) if bounds else {}
            carried.append({"title": owner.get("title"),
                            "details": json.loads(chunk)})

        blocks, warnings = db.fee_blocks(result.body)
        for block in blocks[: args.blocks]:
            print(f"  {block.trip!r} ({block.nights} nights) — "
                  f"{'complete' if block.complete else 'INCOMPLETE'}")
            for fee in block.fees:
                span = ("-" if fee.is_range else "")
                print(f"    {fee.code.value:<20} {fee.tier.value:<10} "
                      f"{fee.low}{span}{fee.high if fee.is_range else ''} "
                      f"{fee.currency} {fee.basis.value}"
                      f"{'  UNIT UNSTATED' if fee.unit_unstated else ''}"
                      f"  <- {fee.label!r}")
            for line in block.unnamed:
                print(f"    UNNAMED  {line!r}")
        for note in warnings:
            print(f"  warning: {note}")

    print("\n== fixture, verbatim ==")
    print(json.dumps(carried, indent=1, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
