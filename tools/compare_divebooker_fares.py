#!/usr/bin/env python3
"""What the third seller's fares are, measured against the two we publish.

Offline: reads `data/divebooker.json`, `data/egypt-2027.json` and the alias
map, and makes no request. That is the point — the question *is the divebooker
figure a per-person berth?* was being answered from one row read by eye, and
this answers it from every row that joins, reproducibly, on whatever the
latest book holds.

**Everything is normalised to euros first.** The three books do not agree on
currency and the disagreement is per boat rather than per seller:
liveaboard.com states Alsuraya in USD, PADI and divebooker in EUR, and
comparing the digits told a 15% gap and a 1-unit gap apart the wrong way
round. `pricing.py` converts in exactly one place and this borrows the
dataset's own committed FX table, so a figure here is the figure the page
would print.

It reports, per row, the distance to the **nearer** of the two sellers, which
is the honest denominator: a third seller is not wrong for undercutting one of
them, and the question here is only whether its figure counts the same thing.

    python3 tools/compare_divebooker_fares.py [--over 20]
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

BOOK = Path("data/divebooker.json")
DATASET = Path("data/egypt-2027.json")
ALIASES = Path("data/divebooker_aliases.json")

BANDS = (("exact (<0.2%)", 0.002), ("within 1%", 0.01), ("within 3%", 0.03),
         ("within 10%", 0.10), ("within 20%", 0.20))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book", default=BOOK, type=Path)
    parser.add_argument("--dataset", default=DATASET, type=Path)
    parser.add_argument("--aliases", default=ALIASES, type=Path)
    parser.add_argument("--over", type=float, default=20.0,
                        help="print every row further than this %% from both")
    args = parser.parse_args()

    book = json.loads(args.book.read_text(encoding="utf-8"))
    ds = json.loads(args.dataset.read_text(encoding="utf-8"))
    alias = json.loads(args.aliases.read_text(encoding="utf-8"))["aliases"]

    rates = ds["fx"]["rates"]

    def eur(amount: float, currency: str) -> float:
        return amount if currency == "EUR" else amount / rates[currency]

    boat_of = {i["id"]: i["boat_id"] for i in ds["itineraries"]}
    ours = {(boat_of[d["itinerary_id"]], d["start"]): d for d in ds["departures"]}

    bands: Counter[str] = Counter()
    joined = 0
    far: list[tuple[float, str, str, float, float, float | None]] = []
    for row in (book.get("departures") or {}).values():
        them = ours.get((alias.get(row["boat"], row["boat"]), row["start"]))
        if not them or row.get("price") is None:
            continue
        joined += 1
        mine = eur(row["price"], row["currency"])
        lav = eur(them["price"]["amount"], them["price"]["currency"])
        padi = them.get("padi_price")
        padi = eur(padi["amount"], padi["currency"]) if padi else None
        off = min([abs(mine / lav - 1)] + ([abs(mine / padi - 1)] if padi else []))
        for name, edge in BANDS:
            if off <= edge:
                bands[name] += 1
                break
        else:
            bands[f"over {BANDS[-1][1]:.0%}"] += 1
        far.append((off, row["boat"], row["start"], mine, lav, padi))

    print(f"{joined} row(s) join a sailing this site carries\n")
    for name, _ in BANDS:
        print(f"  {bands[name]:>4}  {name}")
    print(f"  {bands[f'over {BANDS[-1][1]:.0%}']:>4}  over {BANDS[-1][1]:.0%}")

    print(f"\nfurthest from the nearer seller, in EUR:")
    for off, boat, start, mine, lav, padi in sorted(far, reverse=True)[:20]:
        if off * 100 < args.over and len(far) > 20:
            break
        print(f"  {boat:<24} {start}  divebooker {mine:>7.0f}  "
              f"liveaboard {lav:>7.0f}  padi "
              f"{('%7.0f' % padi) if padi else '      -'}  off {off * 100:>5.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
