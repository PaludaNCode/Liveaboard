#!/usr/bin/env python3
"""Does a vessel page carry the whole season, or only the month you asked for?

The crawl fetches every vessel four times, once per `?m={M}/{YYYY}` season
month, because an early attempt asking only for May came back with 250
departures *all in May* -- the selector means that month and no other. That
settled the selector and left the interesting question unasked: **the page you
get when you ask for no month at all.**

`liveaboard_com.SEASON_QUERIES` records that a bare fetch returns "whatever
window it defaults to, starting from today", and that a full run of them
scraped 746 departures spanning 2026-09 to 2027-10 and kept 14. That range
*contains* the season, which is either the whole crawl's request budget
sitting in plain sight -- 320 fetches down to 80 -- or an aggregate across
vessels whose individual windows are short and near-term. The two look
identical in that sentence and are opposite answers. Nobody has measured it
per vessel ([`docs/sources/liveaboard.com.md`], *Still open*).

So, per vessel:

* fetch the four `?m=` pages, and union their `Event` nodes -- this is what the
  crawl gets today, and it is the thing to beat;
* fetch the page bare, with no selector at all;
* ask whether the bare page **contains** that union.

Containment is the only test that matters and it is one-directional. A bare
page holding *more* events proves nothing (it reaches outside the season, which
we already knew and filter for). A bare page missing even one is fatal: the
saving would be bought by silently dropping real, bookable sailings, which is
the failure this project exists to catch in other people.

Two speculative multi-month forms are tried once each, on one vessel, purely
because they cost two requests: a repeated `?m=` and a range. Neither is
documented anywhere; both are guesses, and the point of a probe is that a guess
gets answered rather than shipped.

Writes nothing.

    python3 tools/probe_season_months.py                  # five vessels
    python3 tools/probe_season_months.py --vessels topaz  # or a named few
"""

from __future__ import annotations

import argparse
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from liveaboard.scrape import jsonld  # noqa: E402
from liveaboard.scrape.liveaboard_com import (  # noqa: E402
    HOST,
    SEASON_MONTHS,
    SEASON_QUERIES,
    SEASON_YEAR,
)

UA = {"User-Agent": "Mozilla/5.0 (compatible; liveaboard-import/1.0; +price-transparency research)"}

#: Five vessels that sell in all four season months on liveaboard.com's own
#: listing, picked off the committed dataset. Spread across the fleet's
#: shapes -- the busiest boat here, two mid-sized, and two whose seasons are
#: thinner -- because a page that truncates would truncate the long one first.
DEFAULT = ("snefro-pearl", "amelie", "topaz", "odyssey", "marselia-star")

SEASON = {f"{SEASON_YEAR}-{m:02d}" for m in SEASON_MONTHS}


def read(url: str, timeout: int = 40) -> str | None:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 - one bad page must not end the probe
        print(f"      ! {exc}", flush=True)
        return None


def events(html: str) -> set[str]:
    """Departure identities on a page: the `startDate` of each `Event` node.

    A date identifies a sailing on one vessel's page, which is the only claim
    made here -- the same date under two names would be one entry and would
    understate the union, i.e. err towards *rejecting* the saving. The
    conservative direction is the right one when the answer decides whether to
    stop fetching something.
    """
    out = set()
    for node in jsonld.of_type(html, "Event"):
        start = node.get("startDate")
        if isinstance(start, str) and start:
            out.add(start[:10])
    return out


def in_season(dates: set[str]) -> set[str]:
    return {d for d in dates if d[:7] in SEASON}


def probe(slug: str, delay: float) -> tuple[str, str] | None:
    print(f"{slug}")
    asked: set[str] = set()
    for query in SEASON_QUERIES:
        html = read(f"https://{HOST}/diving/egypt/{slug}{query}")
        time.sleep(delay)
        if html is None:
            print(f"  {query:<12} FETCH FAILED -- this vessel proves nothing either way")
            return None
        found = events(html)
        asked |= found
        print(f"  {query:<12} {len(found)} Event")

    html = read(f"https://{HOST}/diving/egypt/{slug}")
    time.sleep(delay)
    if html is None:
        print("  (no selector) FETCH FAILED -- this vessel proves nothing either way")
        return None

    bare = events(html)
    spread = Counter(d[:7] for d in bare)
    print(f"  (no selector) {len(bare)} Event, {len(in_season(bare))} of them in season")
    print(f"                months: {', '.join(f'{m} x{n}' for m, n in sorted(spread.items()))}"
          if spread else "                months: none")

    missing = asked - bare
    if not asked:
        return slug, "no departures in any of the four months; nothing to contain"
    if missing:
        return slug, (f"bare page MISSES {len(missing)} of {len(asked)} season sailings "
                      f"(earliest {min(missing)}, latest {max(missing)})")
    return slug, f"bare page contains all {len(asked)} -- one fetch would do"


def guesses(slug: str, delay: float) -> None:
    """Two undocumented multi-month forms, two requests, on one vessel."""
    print(f"\n--- speculative selectors, on {slug}")
    m = SEASON_MONTHS
    for query in (f"?m={m[0]}/{SEASON_YEAR}&m={m[1]}/{SEASON_YEAR}",
                  f"?m={m[0]}-{m[-1]}/{SEASON_YEAR}"):
        html = read(f"https://{HOST}/diving/egypt/{slug}{query}")
        time.sleep(delay)
        if html is None:
            print(f"  {query:<28} FETCH FAILED")
            continue
        spread = Counter(d[:7] for d in events(html))
        got = ", ".join(f"{k} x{v}" for k, v in sorted(spread.items())) or "no Event nodes"
        print(f"  {query:<28} {got}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vessels", default=",".join(DEFAULT))
    parser.add_argument("--delay", type=float, default=2.0)
    args = parser.parse_args()

    slugs = [s for s in args.vessels.split(",") if s]
    verdicts = [v for v in (probe(s, args.delay) for s in slugs) if v]
    if slugs:
        guesses(slugs[0], args.delay)

    print("\n--- verdict")
    for slug, line in verdicts:
        print(f"  {slug:<16} {line}")
    print("\nOne fetch replaces four only if EVERY vessel's bare page contains the\n"
          "whole union. A page that misses one sailing buys the saving by deleting\n"
          "a bookable trip, which is the trade this project refuses everywhere else.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
