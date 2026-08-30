#!/usr/bin/env python3
"""Read what PADI Travel is discounting, and keep every day of it.

The deals page itself cannot be read. `https://travel.padi.com/liveaboard-deals/`
is an AngularJS shell: 272 KB of navigation chrome, zero `application/ld+json`,
not one price, and a `page=` parameter that is echoed into `og:url` and never
acted on -- pages 1, 2, 3 and 99 came back byte-identical but for the URL
reflected back. Its bundle is `special_deals.*.js` on the CDN, which the
development sandbox refuses with a 403 to CONNECT.

None of that matters, because the XHR behind it is a plain unauthenticated GET:

    /api/v2/travel/promotions/?country=110&country=120&date=2027-05-01&...

It takes the deals page's own query verbatim, states a **currency** beside every
price -- which the sailings endpoint next door does not -- and pages properly.

`data/deals.json` is committed, and that is the whole design:

    A change log is a diff between two committed days. Re-reading this endpoint
    recovers today's deals and never yesterday's, so a change log computed from
    an artifact that ages out silently becomes "no changes" a fortnight later.

So the book keeps one entry per day it was read, `KEEP_DAYS` of them, and
`promote` diffs the two most recent. It is small: 18 offers a day in the
published season, against a 2.4 MB dataset beside it.

**Re-running on a day already in the book fetches nothing at all.** Not one
request, rather than a request whose answer is thrown away.

    python3 tools/fetch_deals.py [--season-start 2027-05-01] [--season-end 2027-08-31]
                                 [--refresh] [--dry-run]
"""

from __future__ import annotations

import argparse
import http.client
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from liveaboard.scrape.padi_com import (  # noqa: E402
    DEAL_COUNTRIES,
    DEAL_MAX_PAGES,
    PadiComAdapter,
)

BOOK = Path("data/deals.json")

UA = {"User-Agent": "Mozilla/5.0 (compatible; liveaboard-import/1.0; +price-transparency research)"}

KEEP_DAYS = 30
"""Days of readings the book carries.

The change log needs two. The rest are kept because a month of them is 60 KB
and answers questions a single diff cannot -- whether a boat's "Early Bird" has
been running since spring, whether a price crept down over a week or dropped in
one morning -- and because a run that fails for three days would otherwise
leave the log comparing today against nothing.
"""

DELAY = 2.0
"""Seconds between requests.

`www.padi.com/robots.txt` names ClaudeBot with `Crawl-delay: 2` and says the
delay is kept in step with a Cloudflare AI-bot limit of 30 requests a minute
per IP. This query is one or two requests, so the pacing costs nothing and
being wrong about it would cost the whole run.
"""


def season_months(start: str, end: str) -> list[str]:
    """The first of every month the season touches.

    What `date=` means to this endpoint: a month, named by its first day, and
    repeated once per month wanted. Derived from the season the rest of the
    pipeline uses rather than typed out, so a season that moves moves here too.
    """
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    months: list[str] = []
    year, month = first.year, first.month
    while (year, month) <= (last.year, last.month):
        months.append(f"{year:04d}-{month:02d}-01")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def get(url: str, timeout: int = 40) -> dict | None:
    """The parsed body, or ``None`` for anything that is not one.

    Every failure is the same failure here -- a page that answered nothing --
    and `collect_deals` treats it as the end of the listing rather than as an
    error, which is right: a run that could not read a page knows nothing about
    what was on it, and the day's entry says how many pages it managed.
    """
    request = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError,
            http.client.HTTPException, ConnectionError, json.JSONDecodeError):
        return None


def load(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"source": "padi.com", "days": {}}


def prune(days: dict, keep: int) -> dict:
    """The most recent `keep` readings, oldest dropped."""
    return {day: days[day] for day in sorted(days)[-keep:]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=BOOK, type=Path)
    parser.add_argument("--season-start", default="2027-05-01")
    parser.add_argument("--season-end", default="2027-08-31")
    parser.add_argument("--today", default=None,
                        help="the day to file this reading under; defaults to today")
    parser.add_argument("--delay", type=float, default=DELAY,
                        help="seconds between requests; the edge rate-limits AI agents at 30/min")
    parser.add_argument("--max-pages", type=int, default=DEAL_MAX_PAGES)
    parser.add_argument("--keep", type=int, default=KEEP_DAYS,
                        help="days of readings to carry; two is the minimum a diff needs")
    parser.add_argument("--refresh", action="store_true",
                        help="re-read a day already in the book")
    parser.add_argument("--dry-run", action="store_true", help="fetch and print, write nothing")
    args = parser.parse_args()

    today = args.today or date.today().isoformat()
    book = load(Path(args.out))
    days: dict = book.setdefault("days", {})

    if today in days and not args.refresh:
        # The acceptance criterion this satisfies is "re-running the same day is
        # a no-op", and a no-op means no request. A second read would answer the
        # same question at somebody else's expense and then be discarded.
        print(f"{today} is already in {args.out} "
              f"({len(days[today].get('offers') or {})} offers); nothing fetched")
        return 0

    months = season_months(args.season_start, args.season_end)
    url = PadiComAdapter.deals_url(months, DEAL_COUNTRIES)
    print(f"{url}\n  {len(months)} months, countries {', '.join(map(str, DEAL_COUNTRIES))}")

    calls = {"n": 0}

    def fetch(page_url: str) -> dict | None:
        if calls["n"]:
            time.sleep(args.delay)
        calls["n"] += 1
        body = get(page_url)
        print(f"  page {calls['n']}: {'no answer' if body is None else str(body.get('count'))} "
              f"total, {len(((body or {}).get('results')) or [])} rows")
        return body

    offers, report = PadiComAdapter.collect_deals(fetch, url, max_pages=args.max_pages)

    print(f"\n{len(offers)} offers from {report['rows']} rows over {report['pages']} page(s); "
          f"stopped on {report['stopped']}")
    if report["truncated"]:
        # Said out loud rather than left to be inferred from a count, on the
        # same rule the change report follows: a truncated list that does not
        # admit it reads as "that was everything".
        print(f"::warning::the deals listing hit the {args.max_pages}-page cap; "
              f"there may be offers this run did not see")
    for note in report["crowded"]:
        print(f"::warning::a second offer on a vessel already listed was not kept -- {note}")

    for slug, deal in sorted(offers.items()):
        saving = deal["was"] - deal["price"]
        print(f"  {slug:<28} {deal['start']} {deal['price']:>9,.0f} was {deal['was']:>9,.0f} "
              f"{deal['currency']}  -{saving:>8,.0f}  {deal.get('title') or ''}")

    if not offers:
        # An empty read is not the same claim as a read that found no deals,
        # and only the second is worth committing. A page that answered nothing
        # would otherwise land in the book as "PADI discounted nothing today"
        # and reach the change log as every offer being withdrawn at once.
        print("::warning::no offers parsed; the book is left as it was", file=sys.stderr)
        return 1

    days[today] = {
        "url": url,
        "pages": report["pages"],
        "stopped": report["stopped"],
        "truncated": report["truncated"],
        "offers": offers,
    }
    book["days"] = prune(days, args.keep)
    book["source"] = "padi.com"
    book["collected"] = today

    if args.dry_run:
        print(f"\n--dry-run: {args.out} not written")
        return 0

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(
        json.dumps(book, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"\n{args.out}: {len(book['days'])} day(s) held, {today} added")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
