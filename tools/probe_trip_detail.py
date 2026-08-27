#!/usr/bin/env python3
"""Find how a vessel page loads one trip's detail.

23 itineraries name no dive site and no direction, because the trip title is
all the site publishes and some titles are pure branding -- "Simply the Best",
"Golden Triangle", "Big Five". A per-trip site list would fill those, and would
be a better source than title-matching for **all 314**: the operator describing
the trip rather than naming it.

The obvious lead is dead and recorded as such in docs/sources/liveaboard.com.md:
every ``Event`` carries a url ending ``#tourid=353503``, but that fragment opens
nothing. Unlike ``#modal-gear`` the detail is not in the document at load, so
tools/probe_itinerary.py found roughly fifty dialogs and no dive site in any.

So the detail arrives after the page does. This watches the network while a
departure row is clicked and reports every request the click caused -- method,
url, content type, and whether the response mentions a reef the site already
knows. If it is a JSON endpoint, that is a far better source than any HTML
parse; if it is a fragment of markup, at least we know what to ask for.

Writes no parser and no data. The sandbox cannot reach the host (#1), so this
runs on a runner.

    python3 tools/probe_trip_detail.py [--vessel emperor-asmaa] [--dump-body]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pw_browser import resolve as resolve_browser  # noqa: E402

from liveaboard.promote import _sites_from_name  # noqa: E402
from liveaboard.scrape.liveaboard_com import HOST, SEASON_QUERY  # noqa: E402

# Requests the page makes regardless. Reporting these would bury the one that
# matters under analytics and image loads.
NOISE = re.compile(
    r"google|gstatic|doubleclick|facebook|hotjar|segment|sentry|cloudflareinsights"
    r"|\.(?:png|jpe?g|webp|gif|svg|woff2?|ttf|ico|css)(?:\?|$)",
    re.I,
)

# Controls that plausibly open a trip. Cast wide: the point is to find out.
ROW = (
    "[data-tourid]",
    "[href*='tourid']",
    "button[aria-controls*='tour']",
    "[class*='trip'] button",
    "[class*='departure'] button",
)


def interesting(url: str) -> bool:
    return not NOISE.search(url)


def probe(page: Any, vessel: str, dump_body: bool) -> None:
    url = f"https://{HOST}/diving/egypt/{vessel}{SEASON_QUERY}"
    print(f"\n=== {vessel} ===")

    seen: list[dict[str, Any]] = []

    def on_response(response: Any) -> None:
        if not interesting(response.url):
            return
        try:
            ctype = (response.headers or {}).get("content-type", "")
        except Exception:  # noqa: BLE001 - a dead response is not a failure
            ctype = ""
        seen.append({"url": response.url, "status": response.status, "type": ctype,
                     "response": response})

    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2500)

    # Everything from here is caused by the click, not by the page load.
    page.on("response", on_response)

    opener = None
    for selector in ROW:
        nodes = page.query_selector_all(selector)
        if nodes:
            opener = (selector, nodes[0])
            break

    if opener is None:
        print("  no departure-row control matched any of:")
        for selector in ROW:
            print(f"    {selector}")
        return

    selector, node = opener
    print(f"  clicking first {selector}")
    try:
        print(f"    outerHTML: {node.evaluate('el => el.outerHTML')[:300]}")
        node.click(timeout=5000)
    except Exception as exc:  # noqa: BLE001
        print(f"    click failed: {exc}")
        return
    page.wait_for_timeout(4000)

    print(f"\n  {len(seen)} request(s) followed the click:")
    for entry in seen:
        print(f"    {entry['status']} {entry['type'][:40]:40} {entry['url'][:110]}")
        try:
            body = entry["response"].text()
        except Exception:  # noqa: BLE001 - a body may not be readable
            continue
        sites = _sites_from_name(body[:200000])
        if sites:
            print(f"      *** names dive sites: {sites}")
        if dump_body and sites:
            print(f"      {body[:1500]}")

    if not seen:
        print("    none — the detail was already in the document, or the click did nothing")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vessel", default="emperor-asmaa")
    parser.add_argument("--dump-body", action="store_true")
    parser.add_argument("--executable", default=None)
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright missing: pip install playwright && playwright install chromium")
        return 2

    with sync_playwright() as p:
        executable, reason = resolve_browser(p, args.executable)
        launch: dict[str, Any] = {"args": ["--no-sandbox"]}
        if executable:
            launch["executable_path"] = executable
        print(f"browser: {reason}")
        browser = p.chromium.launch(**launch)
        page = browser.new_page(viewport={"width": 1400, "height": 1200})
        try:
            probe(page, args.vessel, args.dump_body)
        except Exception as exc:  # noqa: BLE001 - report, do not traceback
            print(f"  {args.vessel}: failed: {exc}")
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
