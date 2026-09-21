#!/usr/bin/env python3
"""Is the *Trip & price details* panel per trip, or per sailing?

The vessel page lists each departure with its own **Trip & price details**
link, and this project reads one panel per *trip name* and attaches it to every
sailing of that trip. If the panel behind those links can differ between two
sailings of one trip, that reading puts one departure's charges on another's
bill — silently, because both are real panels from the same page.

Nothing has ever checked it. The join was measured (603 of 605 panel titles are
a trip name exactly) and the *bill* was corroborated once end to end on one
sailing. Neither asks this question.

So this one asks the payload directly, for one hull:

* every `"details"` occurrence, and the **keys of the object that owns it** --
  a trip object and a sailing object do not look alike, and a date field in
  there would answer the question on its own;
* whether one trip name ever owns **two different panels** -- the failure this
  is looking for, and the only shape in which the current reading is wrong;
* whether any panel's owner carries a date, a start, or a departure id;
* what the page links from a departure row: every href or route fragment
  holding *price*, *detail* or a date, so a per-sailing page can be ruled in
  or out rather than assumed absent.

Writes nothing. One request per hull.

    python3 tools/probe_divebooker_details_per_sailing.py [--vessels 2]
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from liveaboard.scrape import divebooker_com as db  # noqa: E402
from liveaboard.scrape.base import FetchBlocked, PoliteFetcher  # noqa: E402
from probe_divebooker import repair_robots  # noqa: E402

#: Anything that could be a route to a per-sailing panel.
LINKISH = re.compile(
    r'"((?:/|https?://)[^"\\]{0,120}?(?:price|detail|booking|checkout|cabin)'
    r'[^"\\]{0,80})"', re.I)
#: A date sitting inside an owner object, which is what would make it a sailing
#: rather than a trip. **Anchored, and not on `depart`/`arriv`** — the first
#: version matched `departurePort` and `arrivalPort` on every trip object and
#: reported "21 of 21 owners carry a date", which is a harbour name and not a
#: date. A probe that answers its own question wrongly is worse than one that
#: does not answer it.
DATEISH = re.compile(r"^(?:startDate|endDate|date|day|departureDate|arrivalDate)$",
                     re.I)


def digest(panel: dict) -> str:
    return hashlib.sha1(
        json.dumps(panel, sort_keys=True).encode()).hexdigest()[:10]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book", default=Path("data/divebooker.json"), type=Path)
    parser.add_argument("--vessels", default="independence-iii",
                        help="comma-separated slugs, as the book names them")
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
            result = fetcher.get(f"https://{db.HOST}{path}")
        except FetchBlocked as exc:
            print(f"BLOCKED {path}: {exc}")
            continue
        html = result.body
        text, dropped = db.payload_parts(html)
        print(f"\n{'=' * 72}\n{path}   payload {len(text):,} chars, "
              f"{dropped} chunk(s) undecodable\n{'=' * 72}")

        found = [(m.start(), m.end()) for m in db.DETAILS_AT.finditer(text)]
        owners = db.enclosing(text, [start for start, _ in found])
        print(f"{len(found)} details panel(s)")

        per_trip: dict[str, set[str]] = collections.defaultdict(set)
        per_panel: dict[str, set[str]] = collections.defaultdict(set)
        owner_keys: collections.Counter[str] = collections.Counter()
        dated_owners = 0

        for owner_at, position in found:
            chunk = db.balanced(text, position)
            panel = json.loads(chunk) if chunk else None
            bounds = owners.get(owner_at)
            owner = {}
            if bounds:
                try:
                    owner = json.loads(text[bounds[0]:bounds[1] + 1])
                except json.JSONDecodeError:
                    owner = {}
            keys = tuple(sorted(owner)) if isinstance(owner, dict) else ()
            owner_keys["|".join(keys)] += 1
            if any(DATEISH.search(k) for k in keys):
                dated_owners += 1
            # **Through the parser's own suffix rule.** The owner writes
            # *Brothers - Daedalus - Elphinstone (7 nights) (Hurghada-Hurghada)*
            # and `fee_blocks` keys on the name with that suffix removed, so a
            # probe comparing the raw name against a departure's trip name
            # matches nothing and reports "no panel" for the whole fleet. The
            # first run of this file did exactly that.
            raw = (owner.get("name") or owner.get("title") or "?") if owner else "?"
            name = db.TRIP_SUFFIX.sub("", str(raw)).strip() or str(raw)
            if panel is not None:
                per_trip[str(name)].add(digest(panel))
                per_panel[digest(panel)].add(str(name))

        print("\n-- what owns a panel (key sets, counted) --")
        for keys, count in owner_keys.most_common():
            # Whole, never truncated: the question is whether a date key is in
            # there, and a list cut at 150 characters cannot answer it.
            print(f"  {count:>3}  {keys}")
        print(f"\n  owners carrying a date key: {dated_owners} of {len(found)}"
              f"   <- a sailing would; a trip would not")

        print(f"\n-- {len(per_trip)} distinct owner name(s), "
              f"{len(per_panel)} distinct panel(s) --")
        split = {n: d for n, d in per_trip.items() if len(d) > 1}
        if split:
            print("  ** A NAME OWNS MORE THAN ONE PANEL — the panel is not "
                  "per trip **")
            for name, digests in sorted(split.items()):
                print(f"    {name!r}: {len(digests)} panels {sorted(digests)}")
        else:
            print("  every owner name maps to exactly one panel")
        shared = {d: n for d, n in per_panel.items() if len(n) > 1}
        print(f"  panels shared by several names: {len(shared)}")

        rows, warnings = db.departures(html)
        print(f"\n-- {len(rows)} departure(s) read, "
              f"{len({r.trip for r in rows})} distinct trip name(s) --")
        per_name = collections.Counter(r.trip for r in rows)
        for name, count in per_name.most_common(10):
            owned = per_trip.get(str(name))
            state = ("no panel" if not owned else
                     f"{len(owned)} panel(s)")
            print(f"  {count:>3} sailing(s)  {str(name)[:56]:<58} {state}")
        if warnings:
            print(f"  warnings: {warnings[:4]}")

        links = collections.Counter(m.group(1) for m in LINKISH.finditer(text))
        print(f"\n-- {len(links)} link-ish route(s) in the payload --")
        for url, count in links.most_common(20):
            print(f"  {count:>3}  {url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
