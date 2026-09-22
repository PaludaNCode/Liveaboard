#!/usr/bin/env python3
"""Where does a sailing's booking id live, for the sailings that have no Event?

`/boatorder/booking?tripId=N` is the cabin ladder, and the id is the fragment
on a sailing's Event `@id`. But the top-level Events are a **capped ten per
hull** -- established over the whole fleet, `docs/sources/divebooker.com.md` --
and the `TouristTrip` chain that states the other 880 sailings carries no id at
all. So the committed book has a booking id on **21 of 906** sailings, and a
ladder run against it would read 21 ladders and call it the fleet.

This asks where the other 885 ids are, and asks it the one way that cannot be
answered by guessing: **the ids we already hold are the probe.** Take a hull
whose Events state ids, find those exact digits in the streamed payload, and
print the object each one sits in. If the page builds its own *Select cabin*
links, the field is in there under a name nobody has enumerated -- which is
how the fee book was found, and how `boatSpecials` was.

Then the same payload is asked whether that field covers every sailing: how
many distinct ids of that shape it holds, against how many sailings the page
states.

Writes nothing.

    python3 tools/probe_divebooker_trip_ids.py [--vessels emperor-superior]
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from liveaboard.scrape import divebooker_com as db  # noqa: E402
from liveaboard.scrape.base import FetchBlocked, PoliteFetcher  # noqa: E402
from probe_divebooker import repair_robots  # noqa: E402

KEYED = re.compile(r'"([A-Za-z_][A-Za-z0-9_]*)"\s*:\s*"?(\d{5,7})"?')
"""Any key whose value is a number the size of a trip id, with its name."""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book", default=Path("data/divebooker.json"), type=Path)
    parser.add_argument("--vessels", default="emperor-superior",
                        help="hull slugs, as the book names them")
    parser.add_argument("--around", type=int, default=260,
                        help="characters of payload to print around an id")
    parser.add_argument("--delay", type=float, default=5.0)
    parser.add_argument("--snapshots", default=Path("data/snapshots"), type=Path)
    args = parser.parse_args()

    book = json.loads(args.book.read_text(encoding="utf-8"))
    vessels = book.get("vessels") or {}
    sailings = book.get("departures") or {}

    fetcher = PoliteFetcher(snapshot_dir=args.snapshots, delay=args.delay)
    for host in (db.HOST, f"www.{db.HOST}"):
        repair_robots(fetcher, host)

    for slug in [s.strip() for s in args.vessels.split(",") if s.strip()]:
        record = vessels.get(slug)
        if not record:
            print(f"?? {slug}: not in the committed book")
            continue
        known = {key.split("::", 1)[1]: row["booking_id"]
                 for key, row in sailings.items()
                 if row.get("boat") == slug and row.get("booking_id")}
        dates = sorted(key.split("::", 1)[1] for key in sailings
                       if sailings[key].get("boat") == slug)

        path = f"/{record['slug']}-{record['divebooker_id']}"
        try:
            html = fetcher.get(f"https://{db.HOST}{path}").body
        except FetchBlocked as exc:
            print(f"BLOCKED {path}: {exc}")
            continue
        text, _ = db.payload_parts(html)

        print(f"\n{'=' * 72}\n{path}\n{'=' * 72}")
        print(f"  {len(dates)} in-season sailing(s) in the book, "
              f"{len(known)} with a booking id")

        # Every key whose value is a trip-id-shaped number, by name. The names
        # are the finding: one of them is what the page's own link is built
        # from, and a count per name says whether it covers the fleet.
        names = collections.Counter(m.group(1) for m in KEYED.finditer(text))
        print(f"\n-- keys holding a 5-7 digit number, by count --")
        for name, n in names.most_common(14):
            print(f"  {n:>5}  {name}")

        # And the ids we already hold, in context. Where the page states one,
        # whatever object it is in is the object every other sailing has too.
        for date, trip_id in sorted(known.items())[:3]:
            print(f"\n-- {date} -> {trip_id}, where the payload says it --")
            seen = 0
            for m in re.finditer(re.escape(trip_id), text):
                at = m.start()
                window = text[max(0, at - args.around):at + args.around]
                print(f"     at {at}: {window!r}")
                seen += 1
                if seen == 2:
                    break
            if not seen:
                print("     the payload does not hold it at all")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
