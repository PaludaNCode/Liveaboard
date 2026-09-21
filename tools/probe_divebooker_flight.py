#!/usr/bin/env python3
"""Open the payload a divebooker page streams to itself.

The page is Next.js App Router. `probe_divebooker.py` found `self.__next_f` on
every page it read and stopped there, because the JSON-LD was answering. It is
not answering any more: the fares are withheld for want of a unit, there is no
fee book in the JSON-LD, and a reader looking at the vessel page sees prices
this project cannot account for.

App Router does not fetch that data — it **ships it in the HTML**, as a flight
payload split across `self.__next_f.push([1, "..."])` calls. So "rendered by
JavaScript" and "not in the served bytes" are different claims here, and this
tool settles which one is true: it reassembles the payload from a plain GET
and reports what is in it.

Three questions, in order:

1. **Is it there at all?** Chunk count and decoded size against the page's own.
2. **What does it hold?** A census of JSON keys, and every key whose name
   suggests money, a cabin, an extra or a currency — with a sample, because a
   key called `price` is worth nothing until you see what sits beside it.
3. **Does the page call anything?** `/api/`, `/graphql/` and `/_next/data`
   literals, in the HTML and in the first few script chunks it loads — the
   same hunt that found PADI's itinerary endpoint.

Writes nothing.

    python3 tools/probe_divebooker_flight.py [--vessel red-sea-aggressor-ii]
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

BOOK = Path("data/divebooker.json")

#: `self.__next_f.push([1,"...."])`, whose second element is a JS string. They
#: are ordinary JSON strings, so `json.loads` on the literal is the decode.
FLIGHT = re.compile(r'self\.__next_f\.push\(\[\d+,\s*(".*?")\]\)', re.S)

SCRIPT_SRC = re.compile(r'<script[^>]+src="([^"]+)"')
API_LITERAL = re.compile(r"""["'](/(?:api|graphql|_next/data)/[A-Za-z0-9/_.\-{}$\[\]]*)["']""")
KEY = re.compile(r'\\?"([A-Za-z_][A-Za-z0-9_]{1,40})\\?":')

#: What a fee book, a cabin ladder or a currency switch would be called. Broad
#: on purpose: the point is to find the vocabulary, not to confirm a guess.
WANTED = re.compile(
    r"price|amount|cost|currency|fee|extra|includ|exclud|cabin|room|berth|"
    r"occupan|deposit|discount|promo|surcharge|rental|nitrox|tax|rate|fx",
    re.I,
)

CONTEXT = 160


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vessel", default="red-sea-aggressor-ii")
    parser.add_argument("--book", default=BOOK, type=Path)
    parser.add_argument("--scripts", type=int, default=6,
                        help="script chunks to fetch and grep for endpoints")
    parser.add_argument("--keys", type=int, default=40)
    parser.add_argument("--samples", type=int, default=12)
    parser.add_argument("--delay", type=float, default=5.0)
    parser.add_argument("--snapshots", default=Path("data/snapshots"), type=Path)
    args = parser.parse_args()

    fetcher = PoliteFetcher(snapshot_dir=args.snapshots, delay=args.delay)
    for host in (db.HOST, f"www.{db.HOST}"):
        repair_robots(fetcher, host)

    vessels = json.loads(args.book.read_text(encoding="utf-8")).get("vessels") or {}
    record = vessels.get(args.vessel)
    if not record or not record.get("divebooker_id"):
        print(f"{args.vessel} is not in {args.book}; the id is the site's and "
              f"typing one lands on another boat")
        return 1
    path = f"/{args.vessel}-{record['divebooker_id']}"

    try:
        result = fetcher.get(f"https://{db.HOST}{path}")
    except FetchBlocked as exc:
        print(f"BLOCKED {path}: {exc}")
        return 1
    html = result.body

    print(f"== 1. is the payload in the served bytes ==")
    chunks = FLIGHT.findall(html)
    payload = "".join(json.loads(c) for c in chunks)
    print(f"  {path}  {len(html) / 1024:.0f} KB of HTML")
    print(f"  __next_f chunks       : {len(chunks)}")
    print(f"  decoded flight payload: {len(payload) / 1024:.0f} KB "
          f"({len(payload) / max(len(html), 1):.0%} of the page)")
    if not payload:
        print("  nothing to read: the page streams no payload, so whatever the")
        print("  browser shows it fetched afterwards. Section 3 is the lead.")

    print("\n== 2. what the payload holds ==")
    keys = Counter(KEY.findall(payload))
    interesting = [(k, n) for k, n in keys.most_common() if WANTED.search(k)]
    print(f"  distinct JSON keys    : {len(keys)}")
    print(f"  of those, money-shaped: {len(interesting)}")
    for name, count in interesting[: args.keys]:
        print(f"    {count:>6}  {name}")

    print("\n  in context:")
    shown = 0
    for name, _ in interesting:
        for match in re.finditer(rf'\\?"{re.escape(name)}\\?":', payload):
            start = max(0, match.start() - 40)
            snippet = payload[start:match.end() + CONTEXT]
            print(f"    …{snippet}…".replace("\n", " "))
            shown += 1
            break
        if shown >= args.samples:
            break

    print("\n== 3. what the page calls ==")
    literals = sorted(set(API_LITERAL.findall(html)))
    print(f"  endpoint literals in the HTML: {len(literals)}")
    for one in literals[:20]:
        print(f"    {one}")

    scripts = [s for s in SCRIPT_SRC.findall(html) if s.startswith("/_next/")]
    print(f"  script chunks the page loads : {len(scripts)}"
          f" (fetching {min(len(scripts), args.scripts)})")
    found: set[str] = set()
    for src in scripts[: args.scripts]:
        try:
            chunk = fetcher.get(f"https://{db.HOST}{src}")
        except Exception as exc:  # noqa: BLE001 - one dead chunk must not end it
            print(f"    FAILED {src}: {type(exc).__name__}")
            continue
        hits = set(API_LITERAL.findall(chunk.body))
        if hits:
            print(f"    {src}  ->  {len(hits)} literal(s)")
            found |= hits
    for one in sorted(found)[:30]:
        print(f"      {one}")
    if not literals and not found:
        print("  none. Nothing here points at an endpoint, so if the payload in")
        print("  section 1 does not hold the prices, the next place to look is")
        print("  a browser watching the network — and that is a bigger change.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
