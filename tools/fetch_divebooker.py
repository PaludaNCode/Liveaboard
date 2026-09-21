#!/usr/bin/env python3
"""Read divebooker.com's Egyptian fleet into `data/divebooker.json`.

One request per vessel, and a vessel page is a whole season — so the Egyptian
fleet costs about as many requests as it has hulls, against liveaboard.com's
four per vessel and PADI's per-itinerary calls. Nothing here needs a browser:
every fact is in the JSON-LD the page serves (`docs/sources/divebooker.com.md`).

The fleet is **discovered, never typed**, and it is discovered from the
seller's own search rather than from its country page. `/egypt-daz3881` links
**ten** hulls and `/boatsearch?et=2&e=3881&ym=202705` states **75** for one
month: the country page is a landing page with a carousel on it, and reading a
carousel as an inventory is the same error as reading liveaboard.com's
featured strip as its fleet. The sitemap knows all 516 hulls worldwide and
does not say which sea any of them is in, so it cannot be the entry point
either.

The search returns twenty a page and `p=` is what walks it — measured against
nine other spellings that all returned the first twenty again, which is why
`divebooker_com.PAGE_PARAM` carries the measurement beside it. A month is
walked until a page adds no hull the month has not already shown, and every
month of the season is walked: which boats a search lists is a question about
that month.

`--save-html DIR` keeps the raw pages. That is how the fixtures under
`tests/fixtures/` were made, and it is the point: a parser proved against
markup somebody wrote from a description is a parser proved against the
description.

**The book is never quietly emptied.** Rebuilt whole from a crawl, so a run
that reaches nothing would otherwise write a valid file with nothing in it and
exit green — `fetch_padi.py` has `MIN_BOOK_RATIO` for the same reason and
`fetch_cabins.py` refuses to rewrite after reading nothing. A capped run
(`--limit N`) merges into the existing book instead of replacing it, because it
knows nothing about the vessels it did not visit.

    python3 tools/fetch_divebooker.py [--limit N] [--save-html DIR]
"""

from __future__ import annotations

import argparse
import base64
import gzip
import json
import sys
import zlib
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from liveaboard.scrape import divebooker_com as db  # noqa: E402
from liveaboard.scrape.base import FetchBlocked, PoliteFetcher  # noqa: E402
from probe_divebooker import repair_robots  # noqa: E402

BOOK = Path("data/divebooker.json")

MIN_BOOK_RATIO = 0.6
"""How much of the previous book a full run must reproduce before it may replace it.

Same guard as `fetch_padi.py`'s, for the same failure: this book is rebuilt
whole, so a run that is blocked, rate-limited or pointed at a moved country
page writes a valid file holding nothing and exits 0. Six tenths is loose
enough for a season ending and tight enough that a silent wipe cannot pass.
"""


EMIT_WIDTH = 1000
"""Characters per printed line.

A courier, and it exists because of a measured hole rather than a preference:
the development sandbox's egress policy refuses divebooker.com *and* the blob
host GitHub serves artifacts from, so a runner can read this source and then
has no way to hand back what it read. A job log is the one channel that
survives both, and long lines keep a 200 KB book inside a readable number of
them. Reassembled by `tools/land_divebooker.py`.
"""


def emit(name: str, payload: bytes) -> None:
    """Print one file as gzip+base64, a checksum per line.

    The checksum is not belt-and-braces. Carrying 17,400 characters out of a
    job log by hand failed once on a single wrong character, and gzip's own
    CRC could only say *somewhere* — which makes the whole payload suspect and
    the only remedy a full re-copy. A CRC per line names the line, so one is
    re-read and the other seventeen stand.
    """
    text = base64.b64encode(gzip.compress(payload, mtime=0)).decode("ascii")
    print(f"-----BEGIN {name}-----", flush=True)
    for start in range(0, len(text), EMIT_WIDTH):
        chunk = text[start:start + EMIT_WIDTH]
        print(f"{zlib.crc32(chunk.encode('ascii')):08x} {chunk}", flush=True)
    print(f"-----END {name}----- ({len(payload)} bytes, {len(text)} encoded)",
          flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--months", default=",".join(db.SEASON_YM),
                        help="year-months to search, the seller's own ym format")
    parser.add_argument("--entity", default=db.EGYPT,
                        help="the country id in egypt-daz3881")
    parser.add_argument("--from", dest="season_start", default=db.SEASON[0])
    parser.add_argument("--to", dest="season_end", default=db.SEASON[1],
                        help="the committed book is the published season; a "
                             "run's whole reading goes up as an artifact")
    parser.add_argument("--limit", type=int, default=0, help="vessels to read, 0 for all")
    parser.add_argument("--delay", type=float, default=5.0)
    parser.add_argument("--book", default=BOOK, type=Path)
    parser.add_argument("--save-html", type=Path,
                        help="keep each page's bytes here, for fixtures")
    parser.add_argument("--snapshots", default=Path("data/snapshots"), type=Path)
    parser.add_argument("--emit", action="store_true",
                        help="print the book, and any saved page's JSON-LD, as "
                             "gzip+base64 — the only way back from a runner")
    parser.add_argument("--emit-pages", type=int, default=1,
                        help="how many saved pages' JSON-LD to print with --emit; "
                             "the smallest ones, which are real pages and cheap "
                             "fixtures — a boat selling three weeks states the "
                             "same shapes as one selling fifty")
    args = parser.parse_args()

    fetcher = PoliteFetcher(snapshot_dir=args.snapshots, delay=args.delay)
    base = f"https://{db.HOST}"
    for host in (db.HOST, f"www.{db.HOST}"):
        repair_robots(fetcher, host)

    def get(url: str):
        try:
            return fetcher.get(url)
        except FetchBlocked as exc:
            print(f"  BLOCKED {url}: {exc}", flush=True)
        except Exception as exc:  # noqa: BLE001 - one dead hull must not end the run
            print(f"  FAILED  {url}: {type(exc).__name__}: {exc}", flush=True)
        return None

    def keep(result, name: str) -> None:
        if args.save_html and result is not None:
            args.save_html.mkdir(parents=True, exist_ok=True)
            (args.save_html / name).write_text(result.body, encoding="utf-8")

    months = [m.strip() for m in args.months.split(",") if m.strip()]

    def search(path: str):
        result = get(base + path)
        if result is None:
            return None
        keep(result, f"{path.split('?', 1)[0].strip('/')}-"
                     f"{path.split('?', 1)[1].replace('&', '-')}.html")
        return result.body

    hulls, notes = db.walk_search(search, months, entity=args.entity)
    for line in notes:
        print(f"  {line}", flush=True)

    print(f"the search links {len(hulls)} distinct hull(s) over "
          f"{len(months)} month(s)")
    if not hulls:
        print("  no hull linked — the entry point has moved, and writing an "
              "empty book over a good one is exactly what MIN_BOOK_RATIO is for")
        return 1

    visiting = hulls[: args.limit] if args.limit else hulls
    vessels: dict[str, dict] = {}
    departures: dict[str, dict] = {}
    warnings: list[str] = []
    # The fee census, counted as the book is read rather than by a second run
    # over the same 92 pages. Every rule in `_read_fee_line` was written after
    # a count; these are the counts that say what the rules bought.
    bases: Counter[str] = Counter()
    tiers: Counter[str] = Counter()
    codes: Counter[str] = Counter()
    unnamed: Counter[str] = Counter()
    priced = unstated = fee_lines = complete_books = fee_books = 0

    for path in visiting:
        result = get(base + path)
        if result is None:
            warnings.append(f"{path}: unread, so this run knows nothing about it")
            continue
        keep(result, f"{path.strip('/')}.html")
        book = db.vessel(result.body, path)
        vessels[book.slug] = book.as_dict() | {"url": base + path}
        kept = 0
        for row in book.departures:
            # **The committed book is the published season, and the run's whole
            # reading is the artifact beside it.** divebooker sells years
            # ahead — 392 sailings in 2027, 316 in 2028 and 163 in 2029 on ten
            # hulls — and 15% of that is what the dataset's window can read.
            # Scoped rather than trimmed later: a book holding 85% of rows
            # nothing looks at is 838 KB of weight and, on this source, 82 KB
            # of job log to carry it, since a runner's only channel back is
            # what it prints. What is dropped is stated, never silent.
            if not (args.season_start <= row.start <= args.season_end):
                continue
            departures[f"{book.slug}::{row.start}"] = row.as_dict() | {"boat": book.slug}
            kept += 1
        warnings.extend(book.warnings)
        for line in book.unnamed_fees:
            unnamed[line] += 1
        for lines, complete in book.fees.values():
            fee_books += 1
            complete_books += bool(complete)
            for fee in lines:
                fee_lines += 1
                codes[fee.code.value] += 1
                tiers[fee.tier.value] += 1
                if not fee.has_price:
                    continue
                priced += 1
                if fee.unit_unstated:
                    unstated += 1
                else:
                    bases[fee.basis.value] += 1
        print(f"  {path:<40} {kept:>3} in season of {len(book.departures):>3}"
              f"  {len(book.fees):>2} fee block(s)"
              f"{'  ' + book.name if book.name else ''}", flush=True)

    fresh = {
        "collected": date.today().isoformat(),
        "source": db.SOURCE_ID,
        "scope": {"entity": args.entity, "months": months,
                  "from": args.season_start, "to": args.season_end},
        "vessels": vessels,
        "departures": departures,
        "warnings": warnings,
    }

    existing = json.loads(args.book.read_text()) if args.book.exists() else {}
    before = len(existing.get("departures") or {})
    if args.limit and existing:
        # A capped run visited a slice and knows nothing about the rest, so it
        # merges. Same rule as `scrape_fees.py --limit` and the itinerary book.
        merged = dict(existing.get("departures") or {}) | departures
        fresh["vessels"] = dict(existing.get("vessels") or {}) | vessels
        fresh["departures"] = merged
        fresh["partial"] = True
    elif before and len(departures) < before * MIN_BOOK_RATIO:
        print(f"\nREFUSED: {len(departures)} departure(s) against {before} in the "
              f"book already. That is below MIN_BOOK_RATIO and this file is "
              f"rebuilt whole, so writing it would delete what the last run "
              f"read. Nothing written.")
        return 1

    args.book.parent.mkdir(parents=True, exist_ok=True)
    # Compact, and sorted so a re-read of an unchanged fleet is a byte-for-byte
    # no-op rather than a diff nobody can read. 977 departures indented is
    # 290 KB of committed file for about 150 KB of facts.
    args.book.write_text(
        json.dumps(fresh, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8")
    # What the fee reader made of the fleet. Stated whether or not anybody
    # asked, because a book nobody counted is a book nobody can say is worth
    # publishing — and the two numbers that decide that are how many lines
    # carry a price and how many of those carry a unit a total can use.
    print(f"\n== the fee panel, as this run read it ==")
    print(f"  {fee_lines} line(s) in {fee_books} trip book(s) on "
          f"{sum(1 for v in vessels.values() if v.get('fees'))} hull(s); "
          f"{priced} priced, {unstated} of them with no unit stated")
    # The number that decides whether this seller can show a total at all: a
    # bill missing one mandatory figure, or one whose unit is missing, cannot
    # be added up, and a total built from part of a disclosure is the thing
    # this site exists to catch other people doing.
    print(f"  {complete_books} of {fee_books} book(s) name, price and scale "
          f"every charge a diver cannot decline")
    print(f"  tiers : {dict(tiers.most_common())}")
    print(f"  bases : {dict(bases.most_common())}")
    print(f"  codes : {dict(codes.most_common(16))}")
    print(f"  priced lines nothing could name ({sum(unnamed.values())}):")
    for line, count in unnamed.most_common(30):
        print(f"    {count:>4}x  {line}")

    print(f"\n{args.book}: {len(fresh['vessels'])} vessel(s), "
          f"{len(fresh['departures'])} departure(s), {len(warnings)} warning(s)")
    for line in warnings[:20]:
        print(f"  ! {line}")

    if args.emit:
        emit("divebooker.json", args.book.read_bytes())
        # The JSON-LD only, for fixtures. A page is ~300 KB and the parser
        # reads nothing outside these blocks, so this keeps every structured
        # fact the page published and drops markup no fixture would exercise.
        if args.save_html:
            from liveaboard.scrape import jsonld
            pages = sorted(args.save_html.glob("*-haz*.html"),
                           key=lambda f: f.stat().st_size)[: args.emit_pages]
            for page in pages:
                blocks = jsonld.extract_blocks(page.read_text(encoding="utf-8"))
                emit(f"{page.stem}.jsonld.json",
                     json.dumps(blocks, indent=1).encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
