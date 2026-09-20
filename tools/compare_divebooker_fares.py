#!/usr/bin/env python3
"""What the third seller's fares are, measured against the two we publish.

Offline: reads `data/divebooker.json`, `data/egypt-2027.json` and the alias
map, and makes no request. That is the point — the question *is the divebooker
figure a per-person berth?* was being answered from one row read by eye, and
this answers it from every row that joins, reproducibly, on whatever the
latest book holds.

**It reports two comparisons, because which one is right is the open
question.** The three books do not agree on currency, and the disagreement is
per boat rather than per seller: liveaboard.com states Alsuraya in USD, PADI
and divebooker in EUR.

* **as labelled** — each figure converted into euros through the dataset's own
  committed ECB table, `money.FxTable`'s direction (euros = amount x rate).
* **as digits** — divebooker's number compared with the other seller's number
  with both labels ignored.

The second exists because the digits agree far too well to be a coincidence:
**554 of the 618 rows where divebooker says EUR and liveaboard.com says USD
carry the same number**, and 87 of the 94 where both say USD. Matching to the
cent across a dozen operators is the signature of one underlying figure, not
of a third seller charging a 14.6% premium that happens to land exactly on the
dollar price every time. Which of the two the source means is what
`tools/probe_divebooker_currency.py` asks it.

Each row is measured against the **nearer** of the two sellers, which is the
honest denominator: a third seller is not wrong for undercutting one of them,
and the question here is only whether its figure counts the same thing.

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
        """Into euros, the way `money.FxTable.to_display` does it.

        The table is euros per unit of the quoted currency and the conversion
        is a **multiplication** -- USD 0.8726, GBP 1.1644. The first version of
        this tool divided, which inflated every dollar figure by 31% against
        itself and produced a tidy false story about divebooker sitting 13%
        under one seller and 15% over the other.
        """
        return amount if currency == "EUR" else amount * rates[currency]

    boat_of = {i["id"]: i["boat_id"] for i in ds["itineraries"]}
    ours = {(boat_of[d["itinerary_id"]], d["start"]): d for d in ds["departures"]}

    labelled: Counter[str] = Counter()
    digits: Counter[str] = Counter()
    joined = 0
    far: list[tuple[float, str, str, float, float, float | None, str]] = []

    def band(off: float) -> str:
        for name, edge in BANDS:
            if off <= edge:
                return name
        return f"over {BANDS[-1][1]:.0%}"

    for row in (book.get("departures") or {}).values():
        them = ours.get((alias.get(row["boat"], row["boat"]), row["start"]))
        if not them or row.get("price") is None:
            continue
        joined += 1
        lav_raw, lav_ccy = them["price"]["amount"], them["price"]["currency"]
        padi_raw = them.get("padi_price")

        mine = eur(row["price"], row["currency"])
        lav = eur(lav_raw, lav_ccy)
        padi = eur(padi_raw["amount"], padi_raw["currency"]) if padi_raw else None
        off = min([abs(mine / lav - 1)] + ([abs(mine / padi - 1)] if padi else []))
        labelled[band(off)] += 1
        far.append((off, row["boat"], row["start"], mine, lav, padi,
                    row["currency"]))

        # The same row with both labels ignored: is the number the number?
        bare = min([abs(row["price"] / lav_raw - 1)]
                   + ([abs(row["price"] / padi_raw["amount"] - 1)]
                      if padi_raw else []))
        digits[band(bare)] += 1

    print(f"{joined} row(s) join a sailing this site carries\n")
    print("                        as labelled   as digits")
    for name, _ in BANDS:
        print(f"  {name:<20} {labelled[name]:>9}   {digits[name]:>9}")
    over = f"over {BANDS[-1][1]:.0%}"
    print(f"  {over:<20} {labelled[over]:>9}   {digits[over]:>9}")

    print(f"\nfurthest from the nearer seller, as labelled, in EUR:")
    for off, boat, start, mine, lav, padi, ccy in sorted(far, reverse=True)[:20]:
        if off * 100 < args.over and len(far) > 20:
            break
        print(f"  {boat:<24} {start}  divebooker {mine:>7.0f} ({ccy})  "
              f"liveaboard {lav:>7.0f}  padi "
              f"{('%7.0f' % padi) if padi else '      -'}  off {off * 100:>5.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
