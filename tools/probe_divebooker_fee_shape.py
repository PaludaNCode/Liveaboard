#!/usr/bin/env python3
"""What shape is divebooker's *Price details* panel, across the whole fleet?

One vessel page showed three columns — `included`, `notincluded` ("Obligatory
surcharges") and `extra` ("Extra cost") — with the middle one carrying real
money: *"Port fees - 50 USD per person (to be paid on board)"*. One page is an
example, not a shape. Before any of it is parsed, this counts what the fleet
actually states, because every fee rule this project has was written after a
count and half of them were written twice for want of one.

It asks, over every hull in the committed book:

* how many pages carry a `details` block at all, and with which column types;
* how many lines each column holds, and how many of those state an **amount**
  — the thing that separates a fee book from a list of words;
* which **currency** those amounts are in, and whether the page's own currency
  (`page_currency`) agrees;
* which **unit** each priced line states — per person, per day, per night, per
  dive, per trip, per week — and how many state none, since a figure with no
  unit is a figure this project does not total;
* where the charge is **paid**, which the prose says outright;
* whether `pricing`'s existing vocabulary recognises the labels
  (`scrape.fees.classify_label`), because a second fee vocabulary would drift
  from the first;
* whether a boat's programmes state the **same** surcharges, which decides
  whether this book keys on the vessel or on the trip.

Writes nothing, prints counts and a sample. ~92 requests at the five-second
pace, about eight minutes.

    python3 tools/probe_divebooker_fee_shape.py [--limit 0] [--sample 20]
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
from liveaboard.scrape.fees import classify_label  # noqa: E402
from probe_divebooker import repair_robots  # noqa: E402
from probe_divebooker_details import balanced, payload_of  # noqa: E402

DETAILS = re.compile(r'"details"\s*:\s*(?=\{)')
#: A figure with a currency beside it, either order, as the prose writes them.
MONEY = re.compile(
    r"(?:(?P<sym>[€$£])\s*(?P<a1>\d[\d.,]*)|"
    r"(?P<a2>\d[\d.,]*)\s*(?P<code>EUR|USD|GBP|€|\$|£))", re.I)
UNITS = (
    ("per person per day", r"per\s+(?:person|pax|diver)\s*/?\s*per\s+day"),
    ("per person", r"per\s+(?:person|pax|diver)\b"),
    ("per night", r"per\s+night"),
    ("per day", r"per\s+day"),
    ("per dive", r"per\s+dive\b"),
    ("per week", r"per\s+week"),
    ("per trip", r"per\s+(?:trip|safari|cruise|week-long)"),
    ("per tank", r"per\s+(?:tank|cylinder|fill)"),
    ("percentage", r"\d\s*%"),
)
WHERE = (("on board", r"on\s*board|onboard"), ("in advance", r"in\s+advance"),
         ("at the airport", r"airport"), ("cash", r"\bcash\b"))


def lines_of(text: str) -> list[str]:
    return [line.strip(" \t-•*") for line in re.split(r"[\r\n]+", text or "")
            if line.strip(" \t-•*")]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book", default=Path("data/divebooker.json"), type=Path)
    parser.add_argument("--limit", type=int, default=0, help="hulls to read, 0 for all")
    parser.add_argument("--sample", type=int, default=24,
                        help="priced lines to print verbatim")
    parser.add_argument("--delay", type=float, default=5.0)
    parser.add_argument("--snapshots", default=Path("data/snapshots"), type=Path)
    args = parser.parse_args()

    vessels = json.loads(args.book.read_text(encoding="utf-8")).get("vessels") or {}
    slugs = sorted(vessels)
    if args.limit:
        slugs = slugs[: args.limit]

    fetcher = PoliteFetcher(snapshot_dir=args.snapshots, delay=args.delay)
    for host in (db.HOST, f"www.{db.HOST}"):
        repair_robots(fetcher, host)

    pages = blocks = 0
    with_details: list[str] = []
    without: list[str] = []
    titles: Counter[str] = Counter()
    types: Counter[str] = Counter()
    lines_per_type: Counter[str] = Counter()
    priced_per_type: Counter[str] = Counter()
    currencies: Counter[str] = Counter()
    units: Counter[str] = Counter()
    where: Counter[str] = Counter()
    classified: Counter[str] = Counter()
    unclassified: Counter[str] = Counter()
    disagree_currency = 0
    per_boat_blocks: Counter[int] = Counter()
    blocks_differ = same_blocks = 0
    sample: list[str] = []

    for slug in slugs:
        record = vessels[slug]
        if not record.get("divebooker_id"):
            continue
        path = f"/{slug}-{record['divebooker_id']}"
        try:
            result = fetcher.get(f"https://{db.HOST}{path}")
        except FetchBlocked as exc:
            print(f"BLOCKED {path}: {exc}", flush=True)
            continue
        except Exception as exc:  # noqa: BLE001 - one dead hull must not end a census
            print(f"FAILED  {path}: {type(exc).__name__}: {exc}", flush=True)
            continue
        pages += 1
        body = payload_of(result.body)
        page_ccy = db.page_currency(result.body)

        found = []
        for match in DETAILS.finditer(body):
            chunk = balanced(body, match.end())
            if chunk is None:
                continue
            try:
                found.append(json.loads(chunk))
            except json.JSONDecodeError:
                continue
        per_boat_blocks[len(found)] += 1
        if not found:
            without.append(slug)
            continue
        with_details.append(slug)
        shapes = {json.dumps(block, sort_keys=True) for block in found}
        if len(found) > 1:
            if len(shapes) == 1:
                same_blocks += 1
            else:
                blocks_differ += 1

        for block in found:
            blocks += 1
            titles[str(block.get("title"))] += 1
            for column in block.get("columns") or []:
                kind = str(column.get("type"))
                types[f"{kind} / {column.get('title')}"] += 1
                for line in lines_of(column.get("text", "")):
                    lines_per_type[kind] += 1
                    money = MONEY.search(line)
                    if money:
                        priced_per_type[kind] += 1
                        code = (money.group("code") or money.group("sym") or "?").upper()
                        code = {"€": "EUR", "$": "USD", "£": "GBP"}.get(code, code)
                        currencies[code] += 1
                        if page_ccy and code != page_ccy:
                            disagree_currency += 1
                        named = next((name for name, pattern in UNITS
                                      if re.search(pattern, line, re.I)), "none stated")
                        units[named] += 1
                        for name, pattern in WHERE:
                            if re.search(pattern, line, re.I):
                                where[name] += 1
                        if kind == "notincluded" and len(sample) < args.sample:
                            sample.append(f"{slug}: {line}")
                    label = re.split(r"[-–—:(]", line, 1)[0].strip()
                    found_code = classify_label(label, prose=False) if label else None
                    if kind in ("notincluded", "extra"):
                        (classified if found_code else unclassified)[
                            str(getattr(found_code, "value", label.lower()[:40]))] += 1
        print(f"  {path:<42} {len(found)} block(s)", flush=True)

    print(f"\n== {pages} page(s) read ==")
    print(f"  with a details block   : {len(with_details)}")
    print(f"  without one            : {len(without)}  {without[:12]}")
    print(f"  blocks per page        : {dict(sorted(per_boat_blocks.items()))}")
    print(f"  several blocks, same   : {same_blocks};  differing: {blocks_differ}")
    print(f"  block titles           : {dict(titles)}")
    print(f"\n== columns ==")
    for name, count in types.most_common():
        print(f"  {count:>4}  {name}")
    print(f"\n== lines, and how many state money ==")
    for kind in sorted(lines_per_type):
        print(f"  {kind:<14} {lines_per_type[kind]:>5} line(s), "
              f"{priced_per_type[kind]:>4} priced")
    print(f"\n  currencies: {dict(currencies)}   "
          f"(disagreeing with the page's own: {disagree_currency})")
    print(f"  units     : {dict(units)}")
    print(f"  paid where: {dict(where)}")
    print(f"\n== does this project's vocabulary know the labels? ==")
    print(f"  classified  ({sum(classified.values())}): "
          f"{dict(classified.most_common(14))}")
    print(f"  unrecognised ({sum(unclassified.values())}): "
          f"{dict(unclassified.most_common(20))}")
    print(f"\n== obligatory surcharge lines, verbatim ==")
    for line in sample:
        print(f"  {line}")
    print("\n  What this decides: whether the panel is a fee book this project "
          "can read\n  — priced, united, classifiable, and keyed to something — "
          "or prose that\n  would have to be guessed at.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
