#!/usr/bin/env python3
"""Read divebooker.com's cabin ladders into `data/divebooker_cabins.json`.

One request per **sailing**, against `/boatorder/booking?tripId=N` — the page
behind *Select cabin* — which is the only place this seller states what a room
costs and how many berths are left. The vessel page states neither, which is
why `docs/divebooker-limitations.md` carried *a cabin ladder* as a hole for
three weeks.

The id comes out of the book rather than out of a second crawl: a sailing's
Event `@id` is the vessel page plus a fragment, that fragment **is** the
`tripId`, and `fetch_divebooker.py` now keeps it as `booking_id`. So this run
opens no vessel page at all.

**A per-room count is the seller's own claim about that room, and is never
summed.** Three options on Argo Egypt each state 8 free spaces beside a
sailing total of 8 — one set of berths offered shared or private — so the
sailing total is `sumFreeSpaces`, which the seller states, and the rooms carry
theirs unsummed.

Ordering is load-bearing, as it is for `cabins.yml` one seller along: a ladder
read a day after the fare it explains disagrees with it, and `promote` drops
such a ladder rather than publishing a berth nobody can buy. This runs after
`divebooker.yml`, against the book that run wrote.

    python3 tools/fetch_divebooker_cabins.py [--limit N] [--delay 5]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from liveaboard.scrape import divebooker_com as db  # noqa: E402
from liveaboard.scrape.base import FetchBlocked, PoliteFetcher  # noqa: E402
from probe_divebooker import repair_robots  # noqa: E402

BOOK = Path("data/divebooker.json")
LADDERS = Path("data/divebooker_cabins.json")

MIN_BOOK_RATIO = 0.6
"""How much of the previous book a full run must reproduce before replacing it.

`fetch_divebooker.py`'s guard, for its failure: this file is rewritten whole,
so a run that is blocked or pointed at a moved path writes a valid book
holding nothing and exits 0. A capped run merges instead and is never measured
against this, because it knows nothing about the sailings it did not visit.
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book", default=BOOK, type=Path)
    parser.add_argument("--ladders", default=LADDERS, type=Path)
    parser.add_argument("--limit", type=int, default=0,
                        help="sailings to read, 0 for all")
    parser.add_argument("--delay", type=float, default=5.0)
    parser.add_argument("--snapshots", default=Path("data/snapshots"), type=Path)
    parser.add_argument("--save-html", type=Path,
                        help="keep each page's bytes here, for fixtures")
    args = parser.parse_args()

    if not args.book.exists():
        print(f"{args.book} is not here — this run reads its ids from the "
              f"sailing book and cannot invent them")
        return 1
    book = json.loads(args.book.read_text(encoding="utf-8"))
    sailings = book.get("departures") or {}

    # A sailing with no id is one this reader cannot open, and saying so is the
    # point: the id is written by the crawl, so a book predating that change
    # has none and the remedy is a fresh `divebooker.yml` rather than a guess
    # at the query string.
    wanted = {key: row for key, row in sorted(sailings.items())
              if row.get("booking_id")}
    missing = len(sailings) - len(wanted)
    print(f"{len(sailings)} sailing(s) in {args.book}, {len(wanted)} with a "
          f"booking id, {missing} without")
    if not wanted:
        print("  no id to open — run tools/fetch_divebooker.py first")
        return 1

    visiting = dict(list(wanted.items())[: args.limit]) if args.limit else wanted
    fetcher = PoliteFetcher(snapshot_dir=args.snapshots, delay=args.delay)
    for host in (db.HOST, f"www.{db.HOST}"):
        repair_robots(fetcher, host)

    ladders: dict[str, dict] = {}
    warnings: list[str] = []
    rooms = priced = counted = 0
    for key, row in visiting.items():
        url = f"https://{db.HOST}/boatorder/booking?tripId={row['booking_id']}"
        try:
            html = fetcher.get(url).body
        except FetchBlocked as exc:
            warnings.append(f"{key}: {exc}")
            print(f"  BLOCKED {key}: {exc}", flush=True)
            continue
        except Exception as exc:  # noqa: BLE001 — one dead sailing ends no run
            warnings.append(f"{key}: {type(exc).__name__}: {exc}")
            print(f"  FAILED  {key}: {type(exc).__name__}: {exc}", flush=True)
            continue
        if args.save_html:
            args.save_html.mkdir(parents=True, exist_ok=True)
            (args.save_html / f"booking-{row['booking_id']}.html").write_text(
                html, encoding="utf-8")

        page = db.booking_page(html)
        warnings.extend(f"{key}: {note}" for note in page.warnings)
        record = page.as_dict()
        if not record.get("cabins"):
            # No room is not an empty ladder: it is a page that answered
            # nothing, and the oldest rule here is that the two must not look
            # alike. Counted in the warnings, kept out of the book.
            warnings.append(f"{key}: the booking page states no cabin")
            continue

        # **The sailing this reader asked about, not the one it was handed.**
        # The id is a query string and a page that answers with another
        # sailing's dates would file a ladder under the wrong berth key -- the
        # exact failure `_drop_stale_ladder` exists to catch after the fact.
        if record.get("start") and record["start"] != row.get("start"):
            warnings.append(
                f"{key}: tripId {row['booking_id']} answered with "
                f"{record['start']}, so it is not this sailing's ladder")
            continue

        ladders[key] = record | {"boat": row.get("boat"),
                                 "collected": date.today().isoformat()}
        rooms += len(record["cabins"])
        priced += sum(1 for c in record["cabins"] if c.get("price") is not None)
        counted += 1 if record.get("free_spaces") is not None else 0

    if args.limit and args.ladders.exists():
        # A capped run merges, like every capped run here: it knows nothing
        # about the sailings it did not visit.
        held = json.loads(args.ladders.read_text(encoding="utf-8"))
        merged = (held.get("sailings") or {}) | ladders
    else:
        held = json.loads(args.ladders.read_text(encoding="utf-8")) \
            if args.ladders.exists() else {}
        before = len(held.get("sailings") or {})
        if before and len(ladders) < before * MIN_BOOK_RATIO:
            print(f"  read {len(ladders)} ladder(s) against {before} held — "
                  f"under {MIN_BOOK_RATIO:.0%}, so the book stands")
            return 1
        merged = ladders

    args.ladders.write_text(
        json.dumps({
            "source": db.HOST,
            "collected": date.today().isoformat(),
            "partial": bool(args.limit) or None,
            "note": (
                "One cabin ladder per sailing, from /boatorder/booking?tripId=. "
                "Room prices and the seller's own per-room count, which is "
                "never summed: the options overlap, so the sailing's total is "
                "its stated sumFreeSpaces."
            ),
            "sailings": dict(sorted(merged.items())),
            "warnings": warnings,
        }, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8")

    print(f"wrote {args.ladders}: {len(merged)} sailing(s) "
          f"({len(ladders)} read now), {rooms} room(s), {priced} priced, "
          f"{counted} stating a sailing total")
    for line in warnings[:20]:
        print(f"  {line}")
    if len(warnings) > 20:
        print(f"  … and {len(warnings) - 20} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
