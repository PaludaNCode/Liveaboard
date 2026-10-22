#!/usr/bin/env python3
"""Which trip names a reef the vocabulary cannot place, and what else it says.

On 2026-10-22 the divebooker publish was refused by
`test_every_reef_the_committed_book_states_is_placed`: a trip with an
in-season sailing listed *Tubbataha Reef*, which is in the Philippines. The
gate did its job -- a name nobody can place is a row the site filter cannot
reach -- and said nothing about where the name came from, because the reading
it refused travels as a one-day artifact and the log prints no reef list. The
one hull new to that reading was Gypsy (`/gypsy-haz486`); the committed book
names Tubbataha nowhere. That is a lead, and a lead is not a finding.

So this reads the named hulls exactly as `fetch_divebooker.py` does -- the
vessel page, then its season from the schedule, asked with the page as
`Referer` -- parses them with the same `vessel()`, and prints for every trip:
its in-season sailings, its harbours, and its reef list with each name the
vocabulary cannot place marked. For each unplaced name it then prints where in
the payload it sits, so the entry can be read whole rather than through the
parser that flattened it. The finding is printed last, where a probe's answer
is read from.

Writes nothing, and reads each hull once plus its schedule.

    python3 tools/probe_divebooker_sites.py --hulls gypsy-haz486
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from liveaboard.promote import _sites_from_regions  # noqa: E402
from liveaboard.scrape import divebooker_com as db  # noqa: E402
from liveaboard.scrape.base import FetchBlocked, PoliteFetcher  # noqa: E402
from probe_divebooker import repair_robots  # noqa: E402

CONTEXT = 600


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hulls", required=True,
                        help="hull paths as the search links them, e.g. gypsy-haz486")
    parser.add_argument("--delay", type=float, default=5.0)
    parser.add_argument("--snapshots", type=Path, default=Path("data/snapshots"))
    args = parser.parse_args()

    fetcher = PoliteFetcher(snapshot_dir=args.snapshots, delay=args.delay)
    base = f"https://{db.HOST}"
    for host in (db.HOST, f"www.{db.HOST}"):
        repair_robots(fetcher, host)

    def get(url: str, headers: dict[str, str] | None = None) -> str | None:
        try:
            return fetcher.get(url, headers=headers).body
        except FetchBlocked as exc:
            print(f"  BLOCKED {url}: {exc}")
        except Exception as exc:  # noqa: BLE001 - one dead page must not end the run
            print(f"  FAILED  {url}: {type(exc).__name__}: {exc}")
        return None

    findings: list[str] = []
    for hull in [h.strip().strip("/") for h in args.hulls.split(",") if h.strip()]:
        path = f"/{hull}"
        print(f"\n== {path} ==")
        page = get(base + path)
        if page is None:
            findings.append(f"{path}: the page did not answer")
            continue

        schedule = None
        boat_id = db.schedule_id(page)
        if boat_id:
            def ask(spath: str, referer: str = base + path) -> str | None:
                return get(base + spath,
                           headers=db.SCHEDULE_HEADERS | {"Referer": referer})
            entries, notes = db.walk_schedule(ask, boat_id, db.SEASON_YM[0],
                                              db.SEASON[1])
            for note in notes:
                print(f"  schedule: {note}")
            if entries is not None:
                schedule, _ = db.schedule_departures(entries, db.page_currency(page))
        print(f"  schedule {'read' if schedule is not None else 'NOT read'}"
              f" (boatId {boat_id or 'absent'})")

        book = db.vessel(page, path, schedule=schedule)
        print(f"  name {book.name!r}, country {book.country!r}, "
              f"{len(book.departures)} sailing(s) stated")

        in_season: dict[str, list[str]] = {}
        for row in book.departures:
            if db.SEASON[0] <= row.start <= db.SEASON[1]:
                in_season.setdefault(db.fee_key(row.trip or "", row.nights), []
                                     ).append(row.start)

        text, _ = db.payload_parts(page)
        for key, trip in sorted(book.trips.items()):
            starts = sorted(in_season.get(key, []))
            sites = trip.get("sites") or []
            unplaced = [s for s in sites if not _sites_from_regions([s])]
            print(f"\n  trip {key!r}: {len(starts)} in-season sailing(s)"
                  f"{' ' + ', '.join(starts[:6]) if starts else ''}")
            print(f"    harbours {trip.get('port_from')!r} -> {trip.get('port_to')!r}")
            print(f"    reefs ({len(sites)}): "
                  + ", ".join(f"[{s}]" if s in unplaced else s for s in sites))
            for name in unplaced:
                findings.append(
                    f"{path} ({book.name}): trip {key!r} names {name!r}, "
                    f"{len(starts)} in-season sailing(s); its other reefs: "
                    f"{', '.join(s for s in sites if s != name) or 'none'}")
                for match in list(re.finditer(re.escape(name), text))[:2]:
                    lo = max(0, match.start() - CONTEXT // 2)
                    print(f"    payload around {name!r}: "
                          f"...{text[lo:match.end() + CONTEXT // 2]}...")

    print("\n== finding ==")
    for line in findings or ["every reef on every trip read is placed"]:
        print(f"  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
