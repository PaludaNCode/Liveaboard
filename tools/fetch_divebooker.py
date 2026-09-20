#!/usr/bin/env python3
"""Read divebooker.com's Egyptian fleet into `data/divebooker.json`.

One request per vessel, and a vessel page is a whole season — so the Egyptian
fleet costs about as many requests as it has hulls, against liveaboard.com's
four per vessel and PADI's per-itinerary calls. Nothing here needs a browser:
every fact is in the JSON-LD the page serves (`docs/sources/divebooker.com.md`).

The fleet is **discovered, never typed**: the Egypt country page links its own
hulls, and `scrape.divebooker_com.hull_links` reads them. The sitemap knows all
516 hulls worldwide and does not say which sea any of them is in, so it cannot
be the entry point.

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
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from liveaboard.scrape import divebooker_com as db  # noqa: E402
from liveaboard.scrape.base import FetchBlocked, PoliteFetcher  # noqa: E402
from probe_divebooker import repair_robots  # noqa: E402

COUNTRY = "egypt-daz3881"
BOOK = Path("data/divebooker.json")

MIN_BOOK_RATIO = 0.6
"""How much of the previous book a full run must reproduce before it may replace it.

Same guard as `fetch_padi.py`'s, for the same failure: this book is rebuilt
whole, so a run that is blocked, rate-limited or pointed at a moved country
page writes a valid file holding nothing and exits 0. Six tenths is loose
enough for a season ending and tight enough that a silent wipe cannot pass.
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--country", default=COUNTRY)
    parser.add_argument("--limit", type=int, default=0, help="vessels to read, 0 for all")
    parser.add_argument("--delay", type=float, default=5.0)
    parser.add_argument("--book", default=BOOK, type=Path)
    parser.add_argument("--save-html", type=Path,
                        help="keep each page's bytes here, for fixtures")
    parser.add_argument("--snapshots", default=Path("data/snapshots"), type=Path)
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

    country = get(f"{base}/{args.country}")
    if country is None:
        print("the country page could not be read, so this run knows no fleet")
        return 1
    keep(country, f"{args.country}.html")

    hulls = db.hull_links(country.body)
    print(f"{args.country}: {len(hulls)} hull(s) linked")
    if not hulls:
        print("  no hull linked — the entry point has moved, and writing an "
              "empty book over a good one is exactly what MIN_BOOK_RATIO is for")
        return 1

    visiting = hulls[: args.limit] if args.limit else hulls
    vessels: dict[str, dict] = {}
    departures: dict[str, dict] = {}
    warnings: list[str] = []

    for path in visiting:
        result = get(base + path)
        if result is None:
            warnings.append(f"{path}: unread, so this run knows nothing about it")
            continue
        keep(result, f"{path.strip('/')}.html")
        book = db.vessel(result.body, path)
        vessels[book.slug] = book.as_dict() | {"url": base + path}
        for row in book.departures:
            departures[f"{book.slug}::{row.start}"] = row.as_dict() | {"boat": book.slug}
        warnings.extend(book.warnings)
        print(f"  {path:<40} {len(book.departures):>3} departure(s)"
              f"{'  ' + book.name if book.name else ''}", flush=True)

    fresh = {
        "collected": date.today().isoformat(),
        "source": db.SOURCE_ID,
        "country": args.country,
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
    args.book.write_text(json.dumps(fresh, indent=1, sort_keys=True) + "\n",
                         encoding="utf-8")
    print(f"\n{args.book}: {len(fresh['vessels'])} vessel(s), "
          f"{len(fresh['departures'])} departure(s), {len(warnings)} warning(s)")
    for line in warnings[:20]:
        print(f"  ! {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
