#!/usr/bin/env python3
"""Read the per-trip itinerary fragment, and find out what it costs to fetch.

A network trace (``tools/probe_trip_detail.py``) found the endpoint that a
vessel page calls when a departure row is clicked:

    /itinerary/getpopupv2?boatID=4418&tourID=353504&languageID=1&curr=USD
                         &showPrices=false

and the site's own recogniser found real reefs in the reply. That makes it a
better source for the Dive sites column than the trip title, which is all we
read today and which is pure branding on 23 itineraries -- "Simply the Best",
"Golden Triangle", "Big Five".

Both ids are already in the repository, so nothing has to be crawled to build
the URL: every ``Event`` node carries ``@id`` of the form ``LA-{x}-{boatID}-
{tourID}``, verified against all 878 archived events, and the boatID is
constant per vessel across all 67.

This probe answers the two questions that decide whether a parser is worth
writing, and writes no parser itself:

**Does it need a browser?** The fee run drives Playwright weekly because the
extras block is rendered client-side. If this endpoint answers a plain GET,
the nightly crawl can fetch it with the existing polite fetcher instead, which
is a different and much cheaper place to put 314 requests. So it tries both
and prints whether the bodies agree.

**What is in it?** It prints the whole fragment for the first tour, so the
markup can be read before anything parses it, and then reports for every tour
what the existing site recogniser finds.

    python3 tools/probe_itinerary_endpoint.py [--tours 6] [--full]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pw_browser import resolve as resolve_browser  # noqa: E402

from liveaboard.promote import _sites_from_name  # noqa: E402
from liveaboard.scrape.liveaboard_com import HOST  # noqa: E402

EVENT_ID = re.compile(r"^LA-(\d+)-(\d+)-(\d+)$")

ARCHIVE = Path("data/archive.json")

USER_AGENT = (
    "LiveaboardPriceTransparency/0.1 "
    "(+https://github.com/PaludaNCode/Liveaboard; research, low volume)"
)


def endpoint(boat_id: str, tour_id: str) -> str:
    return (
        f"https://{HOST}/itinerary/getpopupv2"
        f"?boatID={boat_id}&tourID={tour_id}&languageID=1&curr=USD&showPrices=false"
    )


def tours(limit: int) -> list[tuple[str, str, str, str]]:
    """``(slug, boatID, tourID, trip name)`` for one departure per itinerary.

    One per *itinerary*, not per departure. 878 events carry 878 distinct tour
    ids, but dive sites belong to the trip rather than the sailing, so every
    departure of one itinerary would return the same reefs -- fetching all of
    them would be 564 requests spent re-reading answers we already had.
    """
    archive = json.loads(ARCHIVE.read_text(encoding="utf-8"))
    seen: dict[tuple[str, str], tuple[str, str, str, str]] = {}
    for page in archive["pages"]:
        slug = page["url"].split("?")[0].rstrip("/").rsplit("/", 1)[-1]
        for node in page.get("nodes", []):
            if node.get("@type") != "Event":
                continue
            match = EVENT_ID.match(node.get("@id") or "")
            if not match:
                continue
            _, boat_id, tour_id = match.groups()
            name = " ".join((node.get("name") or "").split())
            # Keyed on vessel and trip name: that pair is what an itinerary is.
            key = (slug, name)
            seen.setdefault(key, (slug, boat_id, tour_id, name))
    return list(seen.values())[:limit]


def over_http(url: str) -> tuple[int, str]:
    """Fetch with the stdlib, the way the nightly crawl would."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except Exception as exc:  # noqa: BLE001 - the answer is "no", not a crash
        print(f"    plain GET failed: {exc}")
        return 0, ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tours", type=int, default=6)
    parser.add_argument("--full", action="store_true", help="print every fragment whole")
    parser.add_argument("--executable", default=None)
    args = parser.parse_args()

    picked = tours(args.tours)
    print(f"{len(picked)} tour(s), one per itinerary\n")

    # 1. Plain HTTP. If this works the nightly crawl can do it.
    print("=== plain GET, no browser ===")
    plain: dict[str, str] = {}
    for slug, boat_id, tour_id, name in picked:
        status, body = over_http(endpoint(boat_id, tour_id))
        sites = _sites_from_name(body) if body else []
        plain[tour_id] = body
        print(f"  {status} {slug:22.22} {name[:34]:34} sites={sites or '-'}")

    if args.full and picked:
        first = picked[0]
        print(f"\n--- full fragment: {first[0]} / {first[3]} ---")
        print(plain.get(first[2], "(empty)")[:12000])

    # 2. The same URLs through the browser, to see whether the reply differs.
    print("\n=== through a browser ===")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  playwright missing; skipping")
        return 0

    with sync_playwright() as p:
        executable, reason = resolve_browser(p, args.executable)
        launch: dict[str, Any] = {"args": ["--no-sandbox"]}
        if executable:
            launch["executable_path"] = executable
        print(f"  browser: {reason}")
        browser = p.chromium.launch(**launch)
        page = browser.new_page()
        for slug, boat_id, tour_id, name in picked:
            try:
                response = page.goto(endpoint(boat_id, tour_id),
                                     wait_until="domcontentloaded", timeout=60000)
                body = response.text() if response else ""
            except Exception as exc:  # noqa: BLE001
                print(f"  {slug}: failed: {exc}")
                continue
            sites = _sites_from_name(body)
            same = "same as plain GET" if body.strip() == plain.get(tour_id, "").strip() \
                else "DIFFERENT from plain GET"
            print(f"  {slug:22.22} {len(body):>6} chars  sites={sites or '-'}  {same}")
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
