#!/usr/bin/env python3
"""Look at what a single trip's detail view says about where it goes.

The site publishes an ``Event`` node per departure whose ``description`` is
boilerplate -- it restates the trip name, the vessel and the dates and names no
dive site. So a title like "Simply the Best" carries no route at all, and 23
itineraries currently show nothing in the Dive sites column because there is
nothing in the data to show.

Each Event does carry a URL with a ``#tourid=`` fragment, which is the same
shape as ``#modal-gear`` -- a dialog the page builds client-side. If the tour
detail behind it names the reefs, that is a real source for the column and a
better one than the title, because it is the operator describing the trip
rather than branding it.

This writes no parser. Fetch first, read what came back, then parse:

    python3 tools/probe_itinerary.py --vessel emperor-asmaa --tours 353503,353505
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from liveaboard.promote import SITE_HINTS, _sites_from_name  # noqa: E402
from liveaboard.scrape.liveaboard_com import HOST, SEASON_QUERY  # noqa: E402

CHROMIUM = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"

# Cast wide: the point is to learn what the dialog is called.
CANDIDATES = (
    "[id*='modal-tour']",
    "[id*='tour']",
    "[role='dialog']",
    "dialog",
    "[class*='modal']",
)

def named_sites(text: str) -> list[str]:
    """Which reefs the existing site parser finds in this text.

    Reuses promote's own recogniser rather than a second list: the question is
    whether this text would feed the column, so the answer should come from the
    thing that fills it.
    """
    return _sites_from_name(text)


def describe(page: Any, selector: str, dump_html: bool) -> bool:
    found = False
    for index, node in enumerate(page.query_selector_all(selector)):
        try:
            text = (node.inner_text() or "").strip()
        except Exception:  # noqa: BLE001 - a detached node is not a failure
            continue
        if not text:
            continue
        found = True
        sites = named_sites(text)
        print(f"    {selector} [{index}] {len(text)} chars, sites named: {sites or 'none'}")
        print("      " + "\n      ".join(text.splitlines()[:50]))
        if dump_html:
            try:
                print("      --- html ---")
                print("      " + (node.inner_html() or "")[:2500])
            except Exception:  # noqa: BLE001
                pass
    return found


def probe(page: Any, vessel: str, tour: str, dump_html: bool) -> None:
    url = f"https://{HOST}/diving/egypt/{vessel}{SEASON_QUERY}#tourid={tour}"
    print(f"\n=== {vessel} tour {tour} ===")
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)

    if not any(describe(page, sel, dump_html) for sel in CANDIDATES):
        print("  no dialog matched; looking for the trip's own row")
        # Fall back to whatever names the trip on the page itself.
        for node in page.query_selector_all(f"[href*='{tour}'], [data-tourid='{tour}']"):
            try:
                print(f"    opener: {node.evaluate('el => el.outerHTML')[:400]}")
                node.click(timeout=3000)
                page.wait_for_timeout(2500)
            except Exception as exc:  # noqa: BLE001
                print(f"      click failed: {exc}")
                continue
            print("  after click:")
            for sel in CANDIDATES:
                describe(page, sel, dump_html)
            return
        print("    nothing found")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vessel", default="emperor-asmaa")
    parser.add_argument("--tours", default="353503,353505")
    parser.add_argument("--dump-html", action="store_true")
    parser.add_argument("--executable", default=None)
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright missing: pip install playwright && playwright install chromium")
        return 2

    launch: dict[str, Any] = {"args": ["--no-sandbox"]}
    executable = args.executable or (CHROMIUM if Path(CHROMIUM).exists() else None)
    if executable:
        launch["executable_path"] = executable

    with sync_playwright() as p:
        browser = p.chromium.launch(**launch)
        page = browser.new_page(viewport={"width": 1400, "height": 1200})
        for tour in [t.strip() for t in args.tours.split(",") if t.strip()]:
            try:
                probe(page, args.vessel, tour, args.dump_html)
            except Exception as exc:  # noqa: BLE001 - one bad page must not end the run
                print(f"  tour {tour}: failed: {exc}")
            page.wait_for_timeout(3000)
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
