#!/usr/bin/env python3
"""What does `/boatorder/booking?tripId=…` state, and may we ask for it?

The owner's own link: *Select cabin* on a departure row opens
`https://divebooker.com/boatorder/booking?tripId=254581`. That is the page
where a fare the vessel listing does not state would be, and where a cabin
ladder — rooms, their prices, what is left — would be. 46 of 887 sailings
reach this dataset with no fare at all, and Argo Egypt's 16 are known to be
priced on the site.

Three questions, in this order, because the first can end the other two:

1. **Does robots.txt allow it?** This host's rules are honoured — an earlier
   probe was refused `/destinations/show` by them — so this is asked before
   anything is fetched and reported whether the answer is yes or no. A
   disallowed path is a decision for a person, not something a probe quietly
   takes.
2. **Where does `tripId` come from?** A URL this project cannot build from
   what it already holds is a URL it cannot crawl. The vessel payload is
   searched for the id and for anything else that could be it.
3. **What does the page state?** Every key, and the whole enclosing object for
   the ones that could be a fare, a room or a berth count — never a window
   around a match, because a parser written against a substring is the mistake
   `docs/sources/` exists to prevent.

Writes nothing.

    python3 tools/probe_divebooker_booking.py [--trips 254581] [--vessels …]
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from liveaboard.scrape import divebooker_com as db  # noqa: E402
from liveaboard.scrape.base import FetchBlocked, PoliteFetcher  # noqa: E402
from probe_divebooker import repair_robots  # noqa: E402

BOOKING = "https://divebooker.com/boatorder/booking?tripId={trip}"
#: Anything that could be the id in that query string.
IDISH = re.compile(r'"(\w*(?:trip|trp|order|book|event|departure)\w*[Ii]d\w*)"\s*:\s*'
                   r'("?[\w-]{1,24}"?)')
KEYISH = re.compile(
    r'"(\w*(?:price|cabin|room|berth|place|spot|avail|occupan|guest|bed|'
    r'old|discount|total|amount|currenc)\w*)"\s*:', re.I)
OPEN = ("price", "minPrice", "oldPrice", "old", "cabin", "cabins",
        "places", "available", "total")


def dump(text: str, key: str, limit: int = 3) -> None:
    at = [m.start() for m in re.finditer(f'"{re.escape(key)}"\\s*:', text)]
    if not at:
        return
    owners = db.enclosing(text, at[:limit])
    print(f"\n  -- {key}: {len(at)} occurrence(s) --")
    for pos in at[:limit]:
        bounds = owners.get(pos)
        print(f"     {text[bounds[0]:bounds[1] + 1][:700] if bounds else '(no owner)'}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book", default=Path("data/divebooker.json"), type=Path)
    parser.add_argument("--trips", default="254581",
                        help="tripId values to open, comma separated")
    parser.add_argument("--vessels", default="argo-egypt,omneia-spirit",
                        help="vessel pages to search for the id, minus -haz")
    parser.add_argument("--follow", type=int, default=3,
                        help="id fragments per vessel to actually ask about")
    parser.add_argument("--delay", type=float, default=5.0)
    parser.add_argument("--snapshots", default=Path("data/snapshots"), type=Path)
    args = parser.parse_args()

    fetcher = PoliteFetcher(snapshot_dir=args.snapshots, delay=args.delay)
    for host in (db.HOST, f"www.{db.HOST}"):
        repair_robots(fetcher, host)

    #: The one question, answered at the bottom. A job log is read from its
    #: end, and the verdict probe had to learn this once already: the finding
    #: was printed first and buried under eleven screens of working material.
    verdicts: list[tuple] = []

    # 1. May we ask at all. Printed first and on its own, because everything
    #    below is moot if the answer is no and the answer belongs to a person.
    print("== robots.txt ==")
    for path in ("/boatorder/booking", "/boatorder/", "/boatorder/booking?tripId=1"):
        url = f"https://{db.HOST}{path}"
        try:
            allowed = fetcher.allowed(url)
        except Exception as exc:  # noqa: BLE001 - shape of the API is what is being learned
            allowed = f"?? {type(exc).__name__}: {exc}"
        print(f"  {path:<34} -> {allowed}")

    # 2. Where the id comes from.
    vessels = json.loads(args.book.read_text(encoding="utf-8")).get("vessels") or {}
    by_slug = {v.get("slug", k).split("-haz")[0]: v for k, v in vessels.items()}
    for want in [s.strip() for s in args.vessels.split(",") if s.strip()]:
        record = by_slug.get(want)
        if not record:
            print(f"\n?? {want}: not in the committed book")
            continue
        path = f"/{record['slug']}-{record['divebooker_id']}"
        try:
            page = fetcher.get(f"https://{db.HOST}{path}").body
        except FetchBlocked as exc:
            print(f"\nBLOCKED {path}: {exc}")
            continue
        text, _ = db.payload_parts(page)
        print(f"\n== {path}: where a tripId could come from ==")
        ids = collections.Counter(m.group(1) for m in IDISH.finditer(text))
        for key, count in ids.most_common(20):
            sample = [m.group(2) for m in IDISH.finditer(text) if m.group(1) == key][:6]
            print(f"  {count:>5}  {key:<28} e.g. {sample}")
        for trip in [t.strip() for t in args.trips.split(",") if t.strip()]:
            print(f"  does the payload contain {trip!r}? "
                  f"{'yes' if trip in text else 'no'}")
        rows, _ = db.departures(page)
        unpriced = [r for r in rows if r.price is None]
        print(f"  {len(rows)} departure(s), {len(unpriced)} unpriced")

        # **The Event's own id fragment is the candidate.** The payload holds
        # no trip id at all — `orderId` is a display rank — but the JSON-LD
        # states `…-haz441#254581`, a six-figure number of exactly the shape
        # the owner's link carries. Whether it *is* that id is settled by
        # asking for it and comparing the dates the booking page returns with
        # the dates the vessel page stated: a number that looks right and
        # answers about a different week is the silent join this project keeps
        # closing.
        fragments = [(r, r.event_id.rsplit("#", 1)[-1])
                     for r in rows if r.event_id and "#" in r.event_id]
        print(f"  {len(fragments)} departure(s) carry an id fragment"
              f"{', e.g. ' + fragments[0][1] if fragments else ''}")
        # Unpriced first: those are what the backup is for.
        order = ([f for f in fragments if f[0].price is None]
                 + [f for f in fragments if f[0].price is not None])
        for row, frag in order[:args.follow]:
            try:
                got = fetcher.get(BOOKING.format(trip=frag))
            except FetchBlocked as exc:
                print(f"    BLOCKED tripId={frag}: {exc}")
                continue
            except Exception as exc:  # noqa: BLE001
                print(f"    FAILED  tripId={frag}: {type(exc).__name__}: {exc}")
                continue
            inner, _ = db.payload_parts(got.body)
            at = [m.start() for m in re.finditer(r'"startDate"\s*:', inner)]
            said = "?"
            if at:
                bounds = db.enclosing(inner, at[:1]).get(at[0])
                if bounds:
                    try:
                        said = json.loads(inner[bounds[0]:bounds[1] + 1])
                    except json.JSONDecodeError:
                        said = inner[bounds[0]:bounds[0] + 160]
            fares = re.findall(r'"current":"(\d+)"', inner)
            starts = said.get("startDate") if isinstance(said, dict) else None
            ends = said.get("endDate") if isinstance(said, dict) else None
            verdicts.append((
                want, frag, row.start, row.end, row.price,
                starts, ends, sorted(set(fares))[:6],
                said.get("sumFreeSpaces") if isinstance(said, dict) else None,
                "MATCH" if (starts == row.start and ends == row.end)
                else "MISMATCH"))
            print(f"    tripId={frag}: vessel page says {row.start}..{row.end} "
                  f"{row.price} — booking page says {said}")
            print(f"       fares on it: {sorted(set(fares))[:8]}")

    # 3. What the page states.
    for trip in [t.strip() for t in args.trips.split(",") if t.strip()]:
        url = BOOKING.format(trip=trip)
        print(f"\n{'=' * 72}\n{urlsplit(url).path}?tripId={trip}\n{'=' * 72}")
        try:
            got = fetcher.get(url)
        except FetchBlocked as exc:
            print(f"  BLOCKED by robots: {exc}")
            continue
        except Exception as exc:  # noqa: BLE001
            print(f"  FAILED: {type(exc).__name__}: {exc}")
            continue
        body = got.body
        text, dropped = db.payload_parts(body)
        print(f"  {len(body):,} bytes, payload {len(text):,} chars, "
              f"{dropped} chunk(s) undecodable")
        keys = collections.Counter(m.group(1) for m in KEYISH.finditer(text))
        print(f"\n  -- keys that could carry a fare, a room or a count --")
        for key, count in keys.most_common(34):
            print(f"    {count:>5}  {key}")
        for key in OPEN:
            dump(text, key)

    print(f"\n{'=' * 72}\n== the finding: is the Event id fragment the tripId? ==\n{'=' * 72}")
    if not verdicts:
        print("  nothing was asked — no departure carried an id fragment")
    for (boat, frag, start, end, price, said_start, said_end,
         fares, spaces, verdict) in verdicts:
        print(f"  {verdict:<9} {boat:<16} tripId={frag:<8} "
              f"vessel {start}..{end} price={price} | "
              f"booking {said_start}..{said_end} spaces={spaces} fares={fares}")
    agreed = sum(1 for v in verdicts if v[-1] == "MATCH")
    print(f"\n  {agreed} of {len(verdicts)} agree on both dates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
