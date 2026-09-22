#!/usr/bin/env python3
"""Does the day plan arrive now, and what does it say that nothing else does?

`programm` was shipped as `"Program\\n$3e"` on all 492 trips -- a label and a
**chunk reference**, never the plan -- so the day-plan reader ran on the
literal string and found nothing. `divebooker_com.chunk_table` resolves those
references against the payload they arrived in; this asks the fleet whether
that is true of more than the one hull it was measured on.

Three questions, and the third is the one worth the requests:

* how many trips carry a reference at all, and how many of them resolve;
* whether any reference is left over, with the bytes where the payload
  declares it, because a reference this reader cannot follow is a piece of
  the page nobody read;
* what reefs the resolved plan names that the trip's own `divesites`, its
  name and its `programm` label did not -- which is the whole reason to read
  it, and a count of zero would mean the fix buys nothing.

`--fixture` also prints the raw `self.__next_f.push` chunks one trip's panel
and its day plan are carried in, verbatim, so a guard can be pointed at real
bytes rather than at bytes this project wrote about itself.

Writes nothing.

    python3 tools/probe_divebooker_program.py [--limit 6] [--fixture aml-hayaty]
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

from liveaboard.scrape import divebooker_com as db  # noqa: E402
from liveaboard.scrape.base import FetchBlocked, PoliteFetcher  # noqa: E402
from liveaboard.promote import _sites_from_name  # noqa: E402
from probe_divebooker import repair_robots  # noqa: E402

USED = re.compile(r'"\$([0-9a-f]{1,4})"')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book", default=Path("data/divebooker.json"), type=Path)
    parser.add_argument("--limit", type=int, default=6, help="hulls to read, 0 for all")
    parser.add_argument("--fixture", default="", help="hull whose chunks to print")
    parser.add_argument("--delay", type=float, default=5.0)
    parser.add_argument("--snapshots", default=Path("data/snapshots"), type=Path)
    args = parser.parse_args()

    vessels = json.loads(args.book.read_text(encoding="utf-8")).get("vessels") or {}
    slugs = sorted(vessels)
    if args.fixture:
        slugs = [s for s in slugs if s.startswith(args.fixture)] + [
            s for s in slugs if not s.startswith(args.fixture)]
    if args.limit:
        slugs = slugs[: args.limit]

    fetcher = PoliteFetcher(snapshot_dir=args.snapshots, delay=args.delay)
    for host in (db.HOST, f"www.{db.HOST}"):
        repair_robots(fetcher, host)

    trips = plans = new_reefs = 0
    unresolved: Counter[str] = Counter()
    gained: list[tuple[str, str, list[str]]] = []
    fixture: list[str] = []

    for slug in slugs:
        record = vessels[slug]
        path = f"/{record['slug']}-{record['divebooker_id']}"
        try:
            html = fetcher.get(f"https://{db.HOST}{path}").body
        except FetchBlocked as exc:
            print(f"BLOCKED {path}: {exc}")
            continue

        text, _ = db.payload_parts(html)
        table = db.chunk_table(text)
        for ref in USED.findall(text):
            if ref not in table:
                unresolved[ref] += 1

        blocks, _ = db.fee_blocks(html)
        for block in blocks:
            trips += 1
            plan = block.programme or ""
            if len(plan) > 120:
                plans += 1
            already = set(_sites_from_name(block.trip or ""))
            for site in block.sites:
                already.update(_sites_from_name(site))
            found = [s for s in _sites_from_name(plan) if s not in already]
            if found:
                new_reefs += 1
                gained.append((record["slug"], block.trip or "?", found))

        if args.fixture and record["slug"].startswith(args.fixture) and not fixture:
            fixture = db.FLIGHT.findall(html)

    print(f"\n{'=' * 72}")
    print(f"  {trips} trip(s) read, {plans} carrying a day plan of any length")
    print(f"  {new_reefs} trip(s) name a reef in that plan and nowhere else")
    print(f"  {len(unresolved)} reference(s) left unresolved: "
          f"{dict(unresolved.most_common(8)) or 'none'}")
    for slug, trip, found in gained[:40]:
        print(f"    {slug:<24} {trip[:38]:<38} {', '.join(found)}")

    if fixture:
        # Verbatim, and contiguous: a row's length prefix counts bytes that may
        # run into the next chunk, so a fixture that kept only the chunks it
        # found something in would cut a day plan in half. The boundaries are
        # the point -- a row beginning exactly where a chunk does is the case
        # that made `$3f` look undeclared.
        decoded = [json.loads(c) for c in fixture]
        joined = "".join(decoded)
        # Every reference the page uses, not the ones under a key this probe
        # guessed: `programm` holds `{"title": "Program", "text": "$3d"}`, so
        # a pattern written against `"programm":"$3d"` found none of them and
        # carried back a fixture with no day plan in it.
        refs = {m.group(1) for m in re.finditer(r'"\$([0-9a-f]{1,4})"', joined)}
        want = set()
        for i, part in enumerate(decoded):
            if "Price details" in part or '"programm"' in part:
                want.add(i)
            if any(re.search(rf'{ref}:T[0-9a-f]+,', part) for ref in refs):
                want.add(i)
        if want:
            first, last = min(want), max(want)
            keep = fixture[first:last + 1]
            print(f"\n-- chunks {first}..{last} of {len(fixture)}, "
                  f"{sum(len(c) for c in keep):,} chars, "
                  f"references {sorted(refs)} --")
            print(json.dumps(keep))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
