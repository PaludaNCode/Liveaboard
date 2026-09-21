#!/usr/bin/env python3
"""What is behind *Select cabin*, and does it price a sailing the list cannot?

Two questions this project has been answering by assumption.

**A backup price.** 46 of 887 sailings state no `Offer.price` at all — Argo
Egypt 16, Vita Xplorer 18, Omneia Spirit 7, Galaxy 720 4, Independence II 1 —
and 42 of those are `InStock`, so they are bookable weeks this site prints no
fare for. The vessel page puts a **Select cabin** button on each row, and a
cabin page is where a fare that is not on the list would be.

**And three "never" claims.** `docs/divebooker-limitations.md` states this
source has no berth count, no cabin ladder and no list price, so it can never
fill *Places* or the sale view. Every one of those came from reading the
*vessel* page, which is the same kind of claim as "no fee book hiding
client-side" — a verdict about the page that was looked at. The fee book was
behind a key nobody had enumerated; this is behind a button nobody has pressed.

So this finds the route first and reports it rather than guessing at one: every
absolute and relative URL the payload holds, every `booking`/`cabin`/`select`
key in it, and — where a route is found — what that page states. Writes
nothing.

    python3 tools/probe_divebooker_cabins.py [--vessels vita-xplorer,argo-egypt]
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

#: Every route the payload states, however it is written. Deliberately wider
#: than the last probe's filter, which asked only for urls holding *price* or
#: *cabin* and answered "0 routes" — a filter that names what it expects can
#: only confirm it.
ROUTE = re.compile(r'"((?:/|https?://[^"\\]{0,40}divebooker[^"\\]{0,20})[^"\\ ]{2,120})"')
#: Keys that would carry a booking step, a room or a berth count.
KEYISH = re.compile(
    r'"(\w*(?:cabin|room|berth|place|spot|book|select|avail|occupan|'
    r'guest|bed|price|discount|old|was|strike)\w*)"\s*:', re.I)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book", default=Path("data/divebooker.json"), type=Path)
    parser.add_argument("--vessels", default="vita-xplorer,argo-egypt",
                        help="slugs as the book names them, minus the -haz id")
    parser.add_argument("--follow", type=int, default=3,
                        help="candidate routes to actually fetch")
    parser.add_argument("--delay", type=float, default=5.0)
    parser.add_argument("--snapshots", default=Path("data/snapshots"), type=Path)
    args = parser.parse_args()

    vessels = json.loads(args.book.read_text(encoding="utf-8")).get("vessels") or {}
    by_slug = {v.get("slug", k).split("-haz")[0]: v for k, v in vessels.items()}

    fetcher = PoliteFetcher(snapshot_dir=args.snapshots, delay=args.delay)
    for host in (db.HOST, f"www.{db.HOST}"):
        repair_robots(fetcher, host)

    for want in [s.strip() for s in args.vessels.split(",") if s.strip()]:
        record = by_slug.get(want)
        if not record:
            print(f"?? {want}: not in the committed book")
            continue
        path = f"/{record['slug']}-{record['divebooker_id']}"
        try:
            page = fetcher.get(f"https://{db.HOST}{path}").body
        except FetchBlocked as exc:
            print(f"BLOCKED {path}: {exc}")
            continue
        text, _ = db.payload_parts(page)
        print(f"\n{'=' * 72}\n{path}   payload {len(text):,} chars\n{'=' * 72}")

        rows, _ = db.departures(page)
        unpriced = [r for r in rows if r.price is None]
        print(f"{len(rows)} departure(s), {len(unpriced)} with no price")
        for r in unpriced[:6]:
            print(f"    {r.start} -> {r.end}  {r.trip}  [{r.availability}]")

        keys = collections.Counter(m.group(1) for m in KEYISH.finditer(text))
        print(f"\n-- keys that could carry a room, a berth or a booking step --")
        for key, count in keys.most_common(30):
            print(f"  {count:>5}  {key}")

        routes = collections.Counter(m.group(1) for m in ROUTE.finditer(text))
        print(f"\n-- {len(routes)} distinct route(s) in the payload --")
        for url, count in routes.most_common(40):
            print(f"  {count:>4}  {url[:110]}")

        # Anything that looks like it leads onward, tried in order. Reported
        # by status rather than assumed reachable: a 404 is an answer and a
        # 403 is robots, and telling them apart is the whole point.
        seen: set[str] = set()
        tried = 0
        for url, _ in routes.most_common():
            if tried >= args.follow:
                break
            if not re.search(r"book|cabin|select|checkout|room", url, re.I):
                continue
            full = url if url.startswith("http") else f"https://{db.HOST}{url}"
            if full in seen:
                continue
            seen.add(full)
            tried += 1
            try:
                got = fetcher.get(full)
            except FetchBlocked as exc:
                print(f"\n  BLOCKED {url[:80]}: {exc}")
                continue
            except Exception as exc:  # noqa: BLE001
                print(f"\n  FAILED  {url[:80]}: {type(exc).__name__}: {exc}")
                continue
            body = got.body
            inner, _ = db.payload_parts(body)
            print(f"\n  FETCHED {url[:80]}  {len(body):,} bytes, "
                  f"payload {len(inner):,}")
            ikeys = collections.Counter(m.group(1) for m in KEYISH.finditer(inner))
            print(f"    keys: {dict(ikeys.most_common(18))}")
            for hint in ("cabin", "berth", "occupan", "oldPrice", "discount"):
                at = inner.lower().find(hint.lower())
                if at >= 0:
                    print(f"    …{hint}: {inner[max(0, at - 90):at + 170]!r}")

        # **The whole object each key sits in, not a window around it.** A
        # parser written against a 170-character excerpt is written against a
        # substring, which is the mistake `docs/sources` exists to stop. These
        # are the keys that would carry a fare the JSON-LD does not state, a
        # room, or a struck-through list price, so each is opened with the
        # same bracket matcher the fee panel is read by.
        for key in ("minPrice", "minPriceDay", "inseanqCabinTypeId", "old",
                    "oldPrice", "numberCabins", "occupancy", "places"):
            at = [m.start() for m in re.finditer(f'"{key}"\\s*:', text)]
            if not at:
                continue
            owners = db.enclosing(text, at[:4])
            print(f"\n-- {key}: {len(at)} occurrence(s), first {min(4, len(at))} "
                  f"owners in full --")
            for pos in at[:4]:
                bounds = owners.get(pos)
                if not bounds:
                    print("    (no enclosing object)")
                    continue
                chunk = text[bounds[0]:bounds[1] + 1]
                print(f"    {chunk[:600]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
