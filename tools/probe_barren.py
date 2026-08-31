#!/usr/bin/env python3
"""Ask the first seller about a vessel the barren list told the crawl to skip.

`data/barren.json` holds vessels a crawl found selling nothing this season and
then declines to visit for `BARREN_RECHECK_DAYS` at a time. PADI sells 87
season sailings on four of them -- Bella 2, Bella 3, Eriny, Blue Pearl -- and
all four have a liveaboard.com vessel page the fee scraper reads in full, 7 to
13 extras each. So the first source plainly knows the boat; the question is
whether its *departure* listing really is empty ([#110]).

**Two answers, and they are not the same.** The distinction is the source's
own, and the whole pipeline turns on it:

* a `Product` node with **no** `Event` nodes is the page saying this boat sells
  nothing that month, and its absence is the answer;
* **no structured data at all** answers nothing, and a run that saw it knows
  nothing about the month behind it.

If every month of every vessel is the first, the barren verdict is sound and
`padi_only` is right for those rows. If any is the second -- or if events turn
up -- `barren.json` has been cementing a parse failure every seven days.

Writes nothing. One page per vessel per season month, four of each, at the
crawl's own pacing.

    python3 tools/probe_barren.py                    # the four from #110
    python3 tools/probe_barren.py --vessels eriny    # or a named few
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from liveaboard.scrape import jsonld  # noqa: E402
from liveaboard.scrape.liveaboard_com import HOST, SEASON_QUERIES  # noqa: E402

UA = {"User-Agent": "Mozilla/5.0 (compatible; liveaboard-import/1.0; +price-transparency research)"}

#: The four PADI contradicts. Named rather than read from `barren.json`, which
#: holds thirteen: the other nine have no PADI sailings in the window either,
#: so nothing contradicts their skip and asking costs somebody else's bandwidth
#: for an answer nobody is waiting on.
CONTRADICTED = ("bella-2", "bella-3", "eriny", "blue-pearl")


def read(url: str, timeout: int = 40) -> str | None:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 - one bad page must not end the probe
        print(f"      ! {exc}", flush=True)
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vessels", default=",".join(CONTRADICTED))
    parser.add_argument("--delay", type=float, default=2.0)
    args = parser.parse_args()

    verdicts: dict[str, list[str]] = {}
    for slug in [s for s in args.vessels.split(",") if s]:
        print(f"{slug}")
        verdicts[slug] = []
        for query in SEASON_QUERIES:
            url = f"https://{HOST}/diving/egypt/{slug}{query}"
            html = read(url)
            time.sleep(args.delay)
            if html is None:
                verdicts[slug].append("unread")
                print(f"  {query:<12} FETCH FAILED")
                continue
            events = jsonld.of_type(html, "Event")
            products = jsonld.of_type(html, "Product")
            if not products and not events:
                # The state that answers nothing. Never the same as an empty
                # month, and the reason `carry_unread` exists.
                verdicts[slug].append("no structured data")
                print(f"  {query:<12} NO JSON-LD AT ALL -- this page answers nothing")
                continue
            verdicts[slug].append("empty" if not events else f"{len(events)} events")
            print(f"  {query:<12} {len(products)} Product, {len(events)} Event"
                  f"{'  <-- SELLS SOMETHING' if events else ''}")

    print("\n--- verdict")
    for slug, months in verdicts.items():
        if any(m not in ("empty",) for m in months):
            print(f"  {slug:<14} CONTRADICTED or UNREADABLE: {months}")
        else:
            print(f"  {slug:<14} genuinely empty in all four months; the skip is sound")
    print("\nAn 'empty' month is a Product node with no Events -- the source saying "
          "this boat sells nothing then, which is an answer. 'no structured data' "
          "is not an answer and must never be read as one.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
