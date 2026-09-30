#!/usr/bin/env python3
"""Which currency does each boat's booking page quote in its own right? (#154)

Writes nothing. Between the committed cabin books of 2026-09-28 and 09-29,
676 of 1,030 ladders moved price and nothing else -- almost all by a factor
of 0.999, the booking page re-converting overnight. The page renders in the
session's currency, which it keeps in the ``boardCookie_prefs_v2`` cookie
(``currency=USD``), and the site's terms say every rate is "displayed and
charged in Euro or USD". So each boat has a currency it prices in, and a
ladder read in the other one is a conversion that moves with the rate.

For one sailing per boat this reads the ladder twice -- once as ``USD``, once
as ``EUR`` -- and asks three things:

- **Which one is round.** An operator prices in fives and tens; a
  conversion lands on one only by chance. Round in one and not the other
  names the currency the boat prices in (see :func:`whole` for why "a whole
  number" was not the test).
- **Did it drift.** The committed book holds yesterday's USD ladder: set
  today's against it. If the answer to *which is round* is right, the boats
  that drift are exactly the EUR ones.
- **Does the other seller's figure agree.** The crawl's JSON-LD states one
  fare per sailing, in the offer's own currency; the probe prints it beside
  both readings.

The finding is printed last, because a probe's answer is read from the end of
the job log.

    python3 tools/probe_booking_currency.py [--boats 12] [--delay 2]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from liveaboard.scrape.base import PoliteFetcher  # noqa: E402
from liveaboard.scrape.cabins import parse_cabins  # noqa: E402

COOKIE = "boardCookie_prefs_v2=currency%3D{}"
ASKED = ("USD", "EUR")


def whole(prices: list[float]) -> bool:
    """Every rung a multiple of five.

    Not merely whole: measured on 16 boats on 2026-09-30, the page rounds
    *every* figure it displays to a whole unit in either currency, so
    "whole" named both on all 16. An operator's list is priced in fives and
    tens -- 1,030, 1,350, 1,740 -- and a conversion lands on one only by
    chance, one ladder in five per rung.
    """
    return bool(prices) and all(abs(p / 5 - round(p / 5)) < 0.001 for p in prices)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book", default=Path("data/cabins.json"), type=Path)
    parser.add_argument("--boats", type=int, default=12)
    parser.add_argument("--delay", type=float, default=2.0)
    parser.add_argument("--snapshots", default=Path("data/snapshots"), type=Path)
    args = parser.parse_args()

    book = json.loads(args.book.read_text(encoding="utf-8"))["departures"]
    # One sailing per boat, the soonest the book read most recently: a boat is
    # the unit that prices in a currency, and the freshest record is the one
    # the drift is measured against.
    newest = max(r.get("collected") or "" for r in book.values())
    chosen: dict[str, tuple[str, dict]] = {}
    for tour, record in sorted(book.items(), key=lambda kv: kv[1].get("start") or ""):
        if record.get("collected") == newest and record.get("cabins"):
            chosen.setdefault(record["boat"], (tour, record))
    sample = sorted(chosen.items())[: args.boats]

    fetchers = {c: PoliteFetcher(snapshot_dir=args.snapshots / c, delay=args.delay,
                                 headers={"Cookie": COOKIE.format(c)})
                for c in ASKED}
    verdicts: Counter[str] = Counter()
    drift: Counter[tuple[str, bool]] = Counter()
    agree: Counter[tuple[str, bool]] = Counter()
    print(f"{len(sample)} boat(s), one sailing each, read as {' and '.join(ASKED)}; "
          f"yesterday's USD ladder from {args.book} ({newest})\n")
    for boat, (tour, record) in sample:
        url = record["source_url"]
        ladders: dict[str, list[float]] = {}
        for currency in ASKED:
            try:
                body = fetchers[currency].get(url).body
            except Exception as exc:  # noqa: BLE001 - a probe reports, it does not stop
                print(f"  {boat:24.24} {currency}: {exc}")
                continue
            reading = parse_cabins(body, currency)
            if reading.currency != currency:
                print(f"  {boat:24.24} asked {currency}, page says {reading.currency}")
            ladders[currency] = [c.price for c in reading.cabins if c.price is not None]
        usd, eur = ladders.get("USD") or [], ladders.get("EUR") or []
        if whole(usd) and not whole(eur):
            verdict = "USD"
        elif whole(eur) and not whole(usd):
            verdict = "EUR"
        elif whole(eur) and whole(usd):
            verdict = "both round"
        else:
            verdict = "neither round" if usd and eur else "unread"
        verdicts[verdict] += 1
        before = [c["price"] for c in record["cabins"] if c.get("price") is not None]
        moved = bool(usd) and sorted(before) != sorted(usd)
        drift[(verdict, moved)] += 1
        ratio = (min(usd) / min(before)) if usd and before else None
        fare = record.get("advertised")
        agrees = bool(usd) and fare is not None and float(fare) == min(usd)
        agree[(verdict, agrees)] += 1
        print(f"  {boat:24.24} tour {tour:>7}  USD {min(usd) if usd else '-':>8}  "
              f"EUR {min(eur) if eur else '-':>8}  fare {record.get('advertised') or '-':>6}  "
              f"prices in {verdict:<13} USD since {newest}: "
              f"{f'x{ratio:.4f}' if ratio else '-'}")

    print("\n== which currency each boat prices in ==")
    for verdict, n in verdicts.most_common():
        print(f"  {n:>3}  {verdict}")
    print("\n== did the USD ladder move since the committed book, by that answer ==")
    for (verdict, moved), n in sorted(drift.items()):
        print(f"  {n:>3}  {verdict:<14} {'moved' if moved else 'unchanged'}")
    print("\n== does the crawl's fare equal the USD ladder's bottom rung, by that answer ==")
    for (verdict, agrees), n in sorted(agree.items()):
        print(f"  {n:>3}  {verdict:<14} {'equal' if agrees else 'differs'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
