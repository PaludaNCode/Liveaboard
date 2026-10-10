#!/usr/bin/env python3
"""What `/restapi/trips/{boatId}` answers: the season the vessel page stopped
carrying (#157).

Since 2026-10-08 a vessel page states its ten nearest sailings and nothing
else. Its own script says where the rest went: the schedule block fetches
``getSchedule`` -- ``https://divebooker.com/restapi/trips/`` -- plus the
page's ``boatId``, ``?p=N`` to page, ``&f[dm]=…`` to filter a month, and
always ``type=desc``; it reads ``trips.list`` (each with a ``boatTripId``)
and ``filters`` off the answer. Read out of chunk ``app/[...page]/page``
(run 38063457268), never guessed.

Three questions, in order:

1. **What one answer holds** -- the top-level keys, one list entry verbatim,
   and the whole first answer as gzip+base64 for a fixture.
2. **How it pages** -- ``p=`` walked until a page adds no ``boatTripId`` this
   walk has seen, which is `walk_search`'s stop: the repeat, never a count.
3. **What ``f[dm]`` takes** -- the two spellings a month could have here.

Writes nothing.

    python3 tools/probe_divebooker_schedule.py --vessels topaz,alsuraya
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fetch_divebooker import emit  # noqa: E402
from liveaboard.scrape import divebooker_com as db  # noqa: E402
from liveaboard.scrape.base import PoliteFetcher  # noqa: E402
from probe_divebooker import repair_robots  # noqa: E402

ENDPOINT = f"https://{db.HOST}/restapi/trips/"


def entries(answer: object) -> list[dict]:
    trips = answer.get("trips") if isinstance(answer, dict) else None
    listed = trips.get("list") if isinstance(trips, dict) else trips
    return [e for e in listed or [] if isinstance(e, dict)]


def shape(value: object, depth: int = 0) -> str:
    if isinstance(value, dict) and depth < 2:
        return "{" + ", ".join(f"{k}: {shape(v, depth + 1)}"
                               for k, v in list(value.items())[:30]) + "}"
    if isinstance(value, list):
        return f"list[{len(value)}]"
    return type(value).__name__


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vessels", default="topaz,alsuraya")
    parser.add_argument("--book", default=Path("data/divebooker.json"), type=Path)
    parser.add_argument("--pages", type=int, default=12,
                        help="ceiling on p= per hull; the repeat stops it first")
    parser.add_argument("--months", default="202707,2027-07")
    parser.add_argument("--delay", type=float, default=5.0)
    parser.add_argument("--snapshots", default=Path("data/snapshots"), type=Path)
    args = parser.parse_args()

    vessels = json.loads(args.book.read_text(encoding="utf-8")).get("vessels") or {}
    fetcher = PoliteFetcher(snapshot_dir=args.snapshots, delay=args.delay)
    for host in (db.HOST, f"www.{db.HOST}"):
        repair_robots(fetcher, host)

    def ask(url: str) -> tuple[object | None, str]:
        try:
            body = fetcher.get(url).body
        except Exception as exc:  # noqa: BLE001 - say why, ask the next one
            return None, f"unread ({exc})"
        try:
            return json.loads(body), f"{len(body)} chars"
        except json.JSONDecodeError:
            return None, f"not JSON: {body[:200]!r}"

    report: list[str] = []
    for slug in (s.strip() for s in args.vessels.split(",") if s.strip()):
        record = vessels.get(slug) or {}
        hull = (record.get("divebooker_id") or "").removeprefix("haz")
        if not hull:
            report.append(f"{slug}: no id in {args.book}")
            continue
        report.append(f"== {slug} (boatId {hull}) ==")

        seen: set[str] = set()
        starts: list[str] = []
        for page in range(1, args.pages + 1):
            url = (f"{ENDPOINT}{hull}?type=desc" if page == 1
                   else f"{ENDPOINT}{hull}?p={page}&type=desc")
            answer, note = ask(url)
            if page == 1 and answer is not None:
                emit(f"{slug}.trips.json", json.dumps(answer).encode("utf-8"))
                report.append(f"  answer: {shape(answer)}")
                listed = entries(answer)
                if listed:
                    report.append(f"  one entry: {json.dumps(listed[0])[:1500]}")
                if isinstance(answer, dict):
                    trips = answer.get("trips")
                    if isinstance(trips, dict):
                        report.append("  trips besides list: " + json.dumps(
                            {k: v for k, v in trips.items() if k != "list"})[:600])
            listed = entries(answer)
            ids = [str(e.get("boatTripId")) for e in listed]
            new = [i for i in ids if i not in seen]
            seen.update(ids)
            for entry in listed:
                if str(entry.get("boatTripId")) in new:
                    starts.append(json.dumps(entry.get("departureDate"))[:80])
            report.append(f"  p={page}: {note}, {len(ids)} trip(s), {len(new)} new")
            if not new:
                break
        report.append(f"  walked: {len(seen)} distinct trip(s)")
        report.append(f"  first/last departureDate: {starts[:1]} … {starts[-1:]}")

        for month in (m.strip() for m in args.months.split(",") if m.strip()):
            answer, note = ask(f"{ENDPOINT}{hull}?f[dm]={month}&type=desc")
            listed = entries(answer)
            report.append(f"  f[dm]={month}: {note}, {len(listed)} trip(s); "
                          f"dates {[json.dumps(e.get('departureDate'))[:60] for e in listed[:3]]}")

    print("\n".join(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
