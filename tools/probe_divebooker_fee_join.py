#!/usr/bin/env python3
"""Can a fee block be attached to a departure, and by what?

The fee panel sits inside a trip object carrying `name`, `nights`,
`numberDives` and both ports. The departures come from the JSON-LD, which
names the same boat's trips differently: one page states fee blocks called
*"Northern Red Sea - Best Wreck Diving (7 nights) (Hurghada-Hurghada)"* and
*"Northern Red Sea & Dahab (7 nights) (Hurghada-Hurghada)"* beside sailings
called *"Northern Red Sea, Ras Mohamed, Straits of Tiran"*.

**A key that stops matching fails silently**, which this project has already
paid for once (`promote.itinerary_key`). So before a line of the panel is
parsed, this counts what a join would actually do, per hull:

* how many fee blocks and how many distinct trip names the JSON-LD states;
* how many blocks match a trip **exactly**, after the `(7 nights) (A-B)`
  suffix the panel appends is removed;
* how many match **case- and punctuation-insensitively**, which is the most a
  fold may ever be;
* whether the blocks all state the **same** surcharges anyway — because where
  they do, the fee set is the vessel's and no join is needed at all.

That last count is the one that matters. A book keyed on nothing is worse
than a book keyed on the boat.

Writes nothing.

    python3 tools/probe_divebooker_fee_join.py [--vessels 8]
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
from probe_divebooker_details import balanced, enclosing, payload_of  # noqa: E402

DETAILS = re.compile(r'"details"\s*:\s*(?=\{)')
#: What the panel appends to a trip's name: "(7 nights) (Hurghada-Hurghada)".
SUFFIX = re.compile(r"\s*\((?:\d+\s*nights?)\)\s*(?:\([^)]*\))?\s*$", re.I)


def loose(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book", default=Path("data/divebooker.json"), type=Path)
    parser.add_argument("--vessels", type=int, default=8)
    parser.add_argument("--delay", type=float, default=5.0)
    parser.add_argument("--snapshots", default=Path("data/snapshots"), type=Path)
    args = parser.parse_args()

    vessels = json.loads(args.book.read_text(encoding="utf-8")).get("vessels") or {}
    chosen = [s for s in sorted(vessels) if vessels[s].get("divebooker_id")][: args.vessels]

    fetcher = PoliteFetcher(snapshot_dir=args.snapshots, delay=args.delay)
    for host in (db.HOST, f"www.{db.HOST}"):
        repair_robots(fetcher, host)

    totals = {"blocks": 0, "exact": 0, "loose": 0, "trips": 0,
              "hulls": 0, "same": 0, "differ": 0}
    for slug in chosen:
        path = f"/{slug}-{vessels[slug]['divebooker_id']}"
        try:
            result = fetcher.get(f"https://{db.HOST}{path}")
        except FetchBlocked as exc:
            print(f"BLOCKED {path}: {exc}")
            continue
        body = payload_of(result.body)
        rows, _ = db.departures(result.body)
        trips = {row.trip for row in rows if row.trip}

        named: list[tuple[str, str]] = []
        shapes: set[str] = set()
        for match in DETAILS.finditer(body):
            block = balanced(body, match.end())
            parent = enclosing(body, match.start())
            if block is None or parent is None:
                continue
            try:
                node = json.loads(parent)
            except json.JSONDecodeError:
                continue
            named.append((str(node.get("name") or ""), block))
            shapes.add(block)

        totals["hulls"] += 1
        totals["blocks"] += len(named)
        totals["trips"] += len(trips)
        if len(named) > 1:
            totals["same" if len(shapes) == 1 else "differ"] += 1

        exact = {name for name, _ in named if SUFFIX.sub("", name) in trips}
        loose_hits = {name for name, _ in named
                      if loose(SUFFIX.sub("", name)) in {loose(t) for t in trips}}
        totals["exact"] += len(exact)
        totals["loose"] += len(loose_hits)

        print(f"\n== {path} ==")
        print(f"  {len(named)} fee block(s), {len(shapes)} distinct; "
              f"{len(trips)} trip name(s) in the JSON-LD")
        print(f"  exact matches {len(exact)}, loose {len(loose_hits)}")
        for name, _ in named[:4]:
            print(f"    block: {SUFFIX.sub('', name)!r}")
        for trip in sorted(trips)[:4]:
            print(f"    trip : {trip!r}")

    print(f"\n== over {totals['hulls']} hull(s) ==")
    print(f"  fee blocks {totals['blocks']}, trips {totals['trips']}")
    print(f"  blocks matching a trip exactly: {totals['exact']};  loosely: "
          f"{totals['loose']}")
    print(f"  hulls whose blocks all agree: {totals['same']};  differing: "
          f"{totals['differ']}")
    print("\n  What this decides: whether the fee book keys on the trip, or on "
          "the boat\n  where every block agrees — and what is left over, which "
          "is the part that\n  would have to stay unattached rather than "
          "guessed at.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
