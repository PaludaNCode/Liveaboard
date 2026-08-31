#!/usr/bin/env python3
"""Keep a day of liveaboard.com's markdowns, so the change log has a second half.

`data/deals.json` holds PADI's discounts one day per reading, and `promote`
diffs the last two. liveaboard.com's markdowns had no such book: they are read
off the booking pages by `tools/fetch_cabins.py`, and `data/cabins.json` carries
a single `collected` date and is rewritten whole every run. So the *larger* of
the two signals -- 263 discounted sailings on 22 boats, against PADI's 13, nine
of those boats in no deals listing anywhere -- could say what is on sale today
and nothing at all about what moved.

What that cost, once: on 2026-08-30 the Red Sea Aggressors' 33% sale ended. The
page reported it from PADI, which publishes one exemplar sailing per vessel, so
it said *three offers withdrawn* for an event that moved **36 sailings**.

**This does not re-read anything.** It is a projection of the committed cabin
book onto three fields per sailing -- the advertised price, the list price
struck through beside it, and the currency both are in -- filed under the day
that reading was collected. Run it after `fetch_cabins.py` and it records that
run; run it on a fresh checkout and it records whatever day `cabins.json` last
held. Re-running for a day already in the book merges into it, which is what
makes a capped `fetch_cabins.py --limit N` run safe here too.

**A day is a census, and the keys are the census.** Every sailing whose booking
page was read that day is in the book, discounted or not, because that is the
only thing that separates *not on sale* from *not looked at* -- the distinction
the rest of this pipeline is built out of. `promote` compares the two days over
the sailings both of them cover and reports how many it could not.

    python3 tools/derive_sales.py [--cabins data/cabins.json] [--out data/sales.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from liveaboard.promote import berth_key  # noqa: E402

BOOK = Path("data/sales.json")

KEEP_DAYS = 7
"""Days of readings the book carries.

The diff needs two. Seven is a week -- enough to survive a run that fails for
several days running, and enough to answer "did this sale start on Monday"
without keeping a month.

Measured rather than guessed at, because this is the one number here with a
real cost behind it: a day of the census is 864 sailings and about 66 KB, so
the book is roughly 460 KB and is rewritten whole every night. That is a
quarter of what `data/cabins.json` already costs beside it, and it buys the
half of the change log that could not previously speak. Thirty days -- the
deals book's own figure -- would be 2 MB, which is not a file this repository
should carry for a diff that uses two of them.
"""

NOTE = (
    "One entry per day the booking pages were read, and per sailing the three "
    "fields a change log needs: [advertised price, the list price struck "
    "through beside it or null where the cabin is not discounted, the currency "
    "both are in]. Derived from data/cabins.json by tools/derive_sales.py, "
    "never fetched -- the cabin book keeps one reading, and a diff needs two "
    "committed days. Every sailing read that day is here, discounted or not: "
    "the keys are what says a sailing was looked at, and a sailing missing "
    "from a reading has not come off sale, it has not been read."
)


def sailings_read_on(departures: dict[str, Any], day: str) -> dict[str, list[Any]]:
    """One day's census: every sailing whose booking page was read on ``day``.

    Filtered on each record's own ``collected`` rather than on the book's,
    because a capped run merges into the book and leaves everything it did not
    visit carrying an older date. Taking the whole file would file sailings
    read a week ago under today and report a week-old price as this morning's.

    The cheapest cabin carries the answer, which is the same measured claim
    ``promote._sale_for`` rests on: on all 263 discounted sailings read, every
    cabin is marked down by the same percentage, and the advertised price is
    the bottom rung on 864 of 864. So the bottom rung against its own ``<del>``
    is like for like, where the cheapest price against the dearest room's list
    price reports a 33% sale as 40%.
    """
    out: dict[str, list[Any]] = {}
    for record in departures.values():
        if record.get("collected") != day:
            continue
        boat, start = record.get("boat"), record.get("start")
        if not boat or not start:
            continue
        priced = [c for c in record.get("cabins") or [] if c.get("price") is not None]
        if not priced:
            # A ladder with no readable figure states no price and no discount.
            # Writing it as "read, not on sale" would let tomorrow's reading
            # report a sale starting on a page nobody could price today.
            continue
        cheapest = min(priced, key=lambda c: c["price"])
        listed = cheapest.get("list_price")
        price = round(float(cheapest["price"]), 2)
        was = (round(float(listed), 2)
               if listed and float(listed) > cheapest["price"] > 0 else None)
        out[berth_key(boat, start)] = [price, was, record.get("currency")]
    return out


def prune(days: dict[str, Any], keep: int) -> dict[str, Any]:
    """The most recent ``keep`` readings, oldest dropped. As the deals book."""
    return {day: days[day] for day in sorted(days)[-keep:]}


def load(path: Path) -> dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"source": "liveaboard.com", "days": {}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cabins", default=Path("data/cabins.json"), type=Path)
    parser.add_argument("--out", default=BOOK, type=Path)
    parser.add_argument("--keep", type=int, default=KEEP_DAYS,
                        help="days of readings to carry; two is the minimum a diff needs")
    parser.add_argument("--dry-run", action="store_true", help="print, write nothing")
    args = parser.parse_args()

    if not args.cabins.exists():
        print(f"no {args.cabins}; run tools/fetch_cabins.py first", file=sys.stderr)
        return 1

    cabins = json.loads(args.cabins.read_text(encoding="utf-8"))
    day = cabins.get("collected")
    if not day:
        print(f"{args.cabins} states no collected date; nothing to file", file=sys.stderr)
        return 1

    reading = sailings_read_on(cabins.get("departures") or {}, day)
    if not reading:
        # The same rule the fetcher applies to itself: a run that read nothing
        # must not write a day. An empty census committed as a day would reach
        # the change log tomorrow as every sale on the fleet ending at once.
        print(f"::warning::no sailing in {args.cabins} was read on {day}; "
              f"{args.out} left as it was", file=sys.stderr)
        return 1

    book = load(args.out)
    days: dict[str, Any] = book.setdefault("days", {})
    # Merged, never replaced, for the reason `fetch_cabins.py --limit` merges:
    # a capped run knows nothing about the sailings it did not visit, and
    # overwriting the day with its handful would report the rest as unread.
    entry = days.setdefault(day, {"sailings": {}})
    before = len(entry.get("sailings") or {})
    entry["sailings"] = dict(sorted({**(entry.get("sailings") or {}), **reading}.items()))
    entry["read"] = len(entry["sailings"])
    entry["on_sale"] = sum(1 for row in entry["sailings"].values() if row[1] is not None)

    book["days"] = prune(days, args.keep)
    book["source"] = "liveaboard.com"
    book["note"] = NOTE
    book["collected"] = max(book["days"])

    added = entry["read"] - before
    print(f"{day}: {entry['read']} sailing(s) read, {entry['on_sale']} discounted "
          f"({added} added to the day)")
    for held in sorted(book["days"]):
        held_entry = book["days"][held]
        print(f"  {held}  {held_entry.get('read', 0):>4} read, "
              f"{held_entry.get('on_sale', 0):>4} on sale")

    if args.dry_run:
        print(f"--dry-run: {args.out} not written")
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(book, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {args.out}: {len(book['days'])} day(s) held")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
