#!/usr/bin/env python3
"""What is in a vessel page's *Trip & price details*, and do we read any of it?

This project's map says of the vessel page: *"no fee book hiding client-side …
253 distinct keys in the payload, and not one matches `fee`, `extra`, `includ`
or `exclud`"*. That is a **keyword sweep**, and a keyword sweep answers only
about the words it was given. A panel headed *Trip & price details* is exactly
the shape that survives such a sweep: its fields can be called anything.

So this enumerates instead of matching. For one vessel page it prints:

* **every distinct key** in the streamed payload, sorted, with a count — the
  census the earlier probe reported a filtered slice of;
* the payload around each occurrence of the panel's own words, so the fields
  that render it can be read rather than guessed at;
* what each candidate field holds, for the price vocabulary this project
  actually needs: an occupancy, a supplement, a cabin price, a per-person
  qualifier, an included list, a tax or a transfer;
* whether `scrape.divebooker_com` reads any of it today, by parsing the same
  bytes and printing what the book would get.

**Why it matters beyond completeness.** One row in the committed book is
unexplained — Red Sea Aggressor IV on 2027-07-24 states exactly twice what the
other two sellers state for the same seven nights — and the two readings that
would explain it are both the sort of thing a details panel holds: a price
quoted for a cabin rather than a berth, or a stated occupancy.

Writes nothing.

    python3 tools/probe_divebooker_details.py [--vessel red-sea-aggressor-ii]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from liveaboard.scrape import divebooker_com as db  # noqa: E402
from liveaboard.scrape.base import FetchBlocked, PoliteFetcher  # noqa: E402
from probe_divebooker import repair_robots  # noqa: E402

FLIGHT = re.compile(r'self\.__next_f\.push\(\[\d+,\s*(".*?")\]\)', re.S)
KEY = re.compile(r'"([A-Za-z_][A-Za-z0-9_]{1,40})"\s*:')

#: The panel's own words, and the words a price panel is built from. Used to
#: *locate*, never to decide what is there: the census above it is what says
#: that.
HEADINGS = ("trip & price", "trip and price", "price details", "trip details")
VOCABULARY = (
    "occupancy", "perperson", "per_person", "pax", "guest", "diver",
    "supplement", "single", "double", "cabin", "berth", "bed",
    "included", "notincluded", "exclude", "extra", "fee", "tax", "surcharge",
    "transfer", "nitrox", "equipment", "rental", "insurance", "visa", "park",
    "deposit", "discount", "duration", "night", "day",
)


def payload_of(html: str) -> str:
    return "".join(json.loads(chunk) for chunk in FLIGHT.findall(html))


def show(label: str, text: str, needle: str, span: int = 260, most: int = 3) -> int:
    found = 0
    for match in re.finditer(re.escape(needle), text, re.I):
        start = max(0, match.start() - span // 2)
        print(f"  [{label}] …{text[start:match.start() + span]!r}…")
        found += 1
        if found >= most:
            break
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vessel", default="red-sea-aggressor-ii",
                        help="slug as the committed book names it")
    parser.add_argument("--book", default=Path("data/divebooker.json"), type=Path)
    parser.add_argument("--keys", type=int, default=400,
                        help="how many distinct payload keys to print")
    parser.add_argument("--delay", type=float, default=5.0)
    parser.add_argument("--snapshots", default=Path("data/snapshots"), type=Path)
    args = parser.parse_args()

    vessels = json.loads(args.book.read_text(encoding="utf-8")).get("vessels") or {}
    record = vessels.get(args.vessel)
    if not record or not record.get("divebooker_id"):
        print(f"{args.vessel} is not in {args.book}, and typing an id reads "
              f"another boat")
        return 1

    fetcher = PoliteFetcher(snapshot_dir=args.snapshots, delay=args.delay)
    for host in (db.HOST, f"www.{db.HOST}"):
        repair_robots(fetcher, host)

    path = f"/{args.vessel}-{record['divebooker_id']}"
    try:
        result = fetcher.get(f"https://{db.HOST}{path}")
    except FetchBlocked as exc:
        print(f"BLOCKED {path}: {exc}")
        return 1

    html = result.body
    body = payload_of(html)
    print(f"== {path} ({record.get('name')}) ==")
    print(f"  {len(html) / 1024:.0f} KB served, {len(body) / 1024:.0f} KB of "
          f"streamed payload\n")

    print("== where the panel's own words appear ==")
    for heading in HEADINGS:
        seen = show("html", html, heading) + show("payload", body, heading)
        if not seen:
            print(f"  {heading!r}: nowhere")
    print()

    keys = Counter(KEY.findall(body))
    print(f"== every key in the payload: {len(keys)} distinct ==")
    line = []
    for name, count in sorted(keys.items()):
        line.append(f"{name}({count})")
        if len(line) == 6:
            print("  " + "  ".join(line))
            line = []
    if line:
        print("  " + "  ".join(line))
    print()

    print("== keys whose name is price vocabulary, and what they hold ==")
    interesting = [k for k in sorted(keys) if any(w in k.lower() for w in VOCABULARY)]
    if not interesting:
        print("  none")
    for name in interesting:
        match = re.search(rf'"{re.escape(name)}"\s*:\s*(.{{0,160}})', body, re.S)
        print(f"  {name:<28} {keys[name]:>4}x  {match.group(1)[:160]!r}"
              if match else f"  {name:<28} {keys[name]:>4}x")
    print()

    book = db.vessel(html, path)
    print(f"== what this project reads from the same bytes ==")
    print(f"  {len(book.departures)} departure(s), currency "
          f"{db.page_currency(html)}, name {book.name!r}")
    if book.departures:
        first = book.departures[0]
        print(f"  first: {json.dumps(first.as_dict())}")
    print(f"  warnings: {book.warnings[:3] or 'none'}")
    print("\n  What this decides: whether the panel holds anything this site "
          "publishes\n  — a fee, an occupancy, a cabin price — or whether the "
          "JSON-LD really is\n  the whole of what the page states.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
