#!/usr/bin/env python3
"""Find out whether liveaboard.com states how many berths are left, and where.

Issue #79 asks the page to print how many spots are available at the listed
price. Everything already in the repository says the number is not in what we
fetch:

* **No seat count in the JSON-LD.** Across all 889 archived ``Event`` nodes the
  ``Offer`` carries exactly seven keys -- url, availabilityEnds, availability,
  price, priceCurrency, validFrom, @type -- and none of them is a count.
* **The description is binary.** The only inventory wording in any description
  is "No more spaces available", 128 times, with no number anywhere and no
  numeric form of it.
* ``spaces_left`` is on the model and is ``None`` on all 892 departures,
  because nothing has ever had anything to put in it.

So either the count is rendered client-side, the way the fee, gear and
specification panels are, or the source does not publish it at all. Those need
very different answers -- the weekly browser run against nothing -- and only a
live page can tell them apart. Hence a probe, before any parser.

What it does, per vessel-month:

1. loads the page in a browser and waits for it to settle;
2. prints the rendered text of each departure row, which is where a count
   would have to appear;
3. greps that text for every phrasing a count could take, and for bare numbers
   next to booking words;
4. reports every XHR the page made whose body carries an inventory-shaped
   field, since the panels this site renders late are fed that way.

Aimed at vessel-months holding a **limited** departure. "Available" says
nothing about how many, and "sold out" is zero; if a number exists anywhere it
is on the sailings that are nearly full, and those are the thirteen the
dataset marks limited.

Writes nothing and parses nothing into the dataset.

    python3 tools/probe_spaces.py [--targets "dune-longara:5,tala:5"] [--dump-html]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from liveaboard.scrape.liveaboard_com import HOST  # noqa: E402

# Vessel-months the committed dataset marks as holding a limited departure.
DEFAULT_TARGETS = "dune-longara:5,tala:5,emperor-superior:6,all-star-red-sea:7"

# Every way a remaining-berth count could be written.
COUNT = re.compile(
    r"""(\d{1,2}\s*(?:spaces?|places?|berths?|spots?|seats?|cabins?)\b
        |\b(?:only|last|just)\s+\d{1,2}\b
        |\d{1,2}\s*(?:left|remaining|available)\b
        |\b(?:spaces?|places?|berths?|spots?)\s*(?:left|remaining|available)
        |\bfully\s+booked\b|\bsold\s*out\b|\blimited\b|\balmost\s+full\b)""",
    re.I | re.X,
)

# Keys an inventory number would plausibly hide behind in a JSON reply.
INVENTORY_KEY = re.compile(
    r'"[^"]*(?:avail|space|place|berth|spot|seat|capacit|remain|slot|vacan|'
    r'occupanc|booked|quantity|stock)[^"]*"\s*:', re.I
)


def targets(spec: str) -> list[tuple[str, str]]:
    out = []
    for item in spec.split(","):
        item = item.strip()
        if not item:
            continue
        slug, _, month = item.partition(":")
        out.append((slug, month or "5"))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--targets", default=DEFAULT_TARGETS,
                        help="vessel:month pairs, e.g. dune-longara:5")
    parser.add_argument("--rows", type=int, default=6,
                        help="departure rows to print per page")
    parser.add_argument("--chars", type=int, default=400,
                        help="characters of each row's text to print")
    parser.add_argument("--dump-html", action="store_true",
                        help="print a row's markup, not just its text")
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright

    found_text, found_xhr, looked = [], [], 0

    with sync_playwright() as play:
        browser = play.chromium.launch()
        for slug, month in targets(args.targets):
            url = f"https://{HOST}/diving/egypt/{slug}?m={month}/2027"
            print("=" * 78)
            print(url)
            looked += 1

            page = browser.new_page()
            replies: list[tuple[str, str]] = []

            def capture(response, replies=replies):
                kind = (response.headers or {}).get("content-type", "")
                if "json" not in kind.lower():
                    return
                try:
                    replies.append((response.url, response.text()[:200000]))
                except Exception:  # noqa: BLE001 - a body we cannot read is not the answer
                    pass

            page.on("response", capture)
            try:
                page.goto(url, wait_until="networkidle", timeout=60000)
                page.wait_for_timeout(2500)
            except Exception as exc:  # noqa: BLE001 - one bad page must not end the run
                print(f"    failed: {exc}")
                page.close()
                continue

            # 2. The departure rows, as rendered.
            rows = page.query_selector_all(
                "[class*='trip'],[class*='departure'],[class*='date-row'],"
                "[class*='availability'],tr"
            )
            print(f"    {len(rows)} candidate row element(s)")
            shown = 0
            for row in rows:
                try:
                    text = " ".join((row.inner_text() or "").split())
                except Exception:  # noqa: BLE001
                    continue
                if not text or len(text) < 20:
                    continue
                hit = COUNT.search(text)
                if shown < args.rows or hit:
                    marker = "  <-- MATCH" if hit else ""
                    print(f"      {text[:args.chars]}{marker}")
                    if hit and args.dump_html:
                        print("      --- markup ---")
                        print("      " + (row.inner_html() or "")[:1500])
                    shown += 1
                if hit:
                    found_text.append((url, hit.group(0), text[:160]))

            # 4. Anything inventory-shaped in what the page fetched.
            for reply_url, body in replies:
                for key in set(INVENTORY_KEY.findall(body)):
                    found_xhr.append((url, reply_url, key))
            page.close()
        browser.close()

    print("\n" + "=" * 78)
    print("SUMMARY")
    print(f"  vessel-months read              : {looked}")
    print(f"  rendered rows naming a count    : {len(found_text)}")
    print(f"  inventory-shaped keys in XHR    : {len(found_xhr)}")
    for url, phrase, text in found_text[:20]:
        print(f"    {phrase!r} in {url}\n        {text}")
    for url, reply_url, key in found_xhr[:20]:
        print(f"    {key} from {reply_url[:90]}  (on {url})")
    print()
    if not found_text and not found_xhr:
        print("  Nothing states a remaining-berth count, in the markup or in")
        print("  anything the page fetched. On this evidence #79 cannot be")
        print("  sourced from liveaboard.com and should say so rather than")
        print("  ship a column that is empty on every row.")
    else:
        print("  Read the matches above before writing a parser: what matters")
        print("  is whether the number is per departure and whether it is the")
        print("  count at the *listed price* or for the whole boat.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
