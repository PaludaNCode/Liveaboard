#!/usr/bin/env python3
"""Collect vessel fee disclosures with a real browser.

liveaboard.com does not serve the "Required Extras" block in its HTML — a full
crawl found it on none of eleven vessel pages. It is rendered client-side, so
the dependency-free scraper cannot reach it and a browser has to.

This runs separately from the daily scrape and on a slower schedule, because
the two have different natures. Prices and availability change constantly and
are cheap to fetch. **Fees are a property of the vessel and do not change with
the month**, so re-rendering ninety pages every night would be a lot of
someone else's CPU for an answer that is nearly always the same.

Output is ``data/fees.json``, keyed by vessel slug, which ``promote`` merges
into itineraries. Parsing is shared with the daily path via
``liveaboard.scrape.fees`` — one implementation of what a fee means.

    python3 tools/scrape_fees.py --out data/fees.json [--limit N] [--delay 4]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from liveaboard.scrape.fees import parse_extras, to_fee_dicts  # noqa: E402
from liveaboard.scrape.liveaboard_com import (  # noqa: E402
    HOST,
    SEASON_QUERY,
    LiveaboardComAdapter,
    search_paths,
)

CHROMIUM = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"

# Buttons that reveal collapsed detail. The live page carries a "+4" control,
# and the extras may sit behind it.
EXPANDERS = re.compile(r"^\s*(\+\d+|show all.*|more.*|read more)\s*$", re.I)

EXTRAS_MARKER = re.compile(r"(Required|Optional)\s+Extras", re.I)


def boat_slugs(page: Any, limit: int) -> list[str]:
    """Vessel paths for the season, taken from the month search pages.

    Read through the browser rather than over plain HTTP so this tool stands
    alone: it needs no prior scrape output to run.
    """
    found: list[str] = []
    seen: set[str] = set()
    for path in search_paths():
        page.goto(f"https://{HOST}{path}", wait_until="domcontentloaded", timeout=60000)
        for link in sorted(LiveaboardComAdapter.boat_links(page.content())):
            if link not in seen:
                seen.add(link)
                found.append(link)
        if limit and len(found) >= limit:
            break
    return found[:limit] if limit else found


def read_extras(page: Any, url: str) -> tuple[str, bool]:
    """Return the page's visible text, expanding collapsed sections if needed.

    Tries the plain rendered text first. A previous probe concluded the block
    was absent when it was only being filtered out by a bad selector, so this
    reads the whole body before assuming anything is hidden.
    """
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(1200)

    text = page.evaluate("() => document.body.innerText || ''")
    if EXTRAS_MARKER.search(text):
        return text, False

    # Not in the rendered text: try the disclosure controls.
    clicked = 0
    for button in page.query_selector_all("button, [role='button'], summary, a[href='#']"):
        try:
            label = (button.inner_text() or "").strip()
        except Exception:  # noqa: BLE001 - a detached node is not a failure
            continue
        if not EXPANDERS.match(label):
            continue
        try:
            button.click(timeout=2000)
            clicked += 1
            page.wait_for_timeout(250)
        except Exception:  # noqa: BLE001 - an unclickable control is not a failure
            continue

    if clicked:
        page.wait_for_timeout(600)
        text = page.evaluate("() => document.body.innerText || ''")
    return text, clicked > 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=Path("data/fees.json"), type=Path)
    parser.add_argument("--limit", type=int, default=0, help="cap vessels (0 = all)")
    parser.add_argument("--delay", type=float, default=4.0, help="seconds between vessels")
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

    collected: dict[str, Any] = {}
    missing: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(**launch)
        page = browser.new_page(viewport={"width": 1400, "height": 1200})

        slugs = boat_slugs(page, args.limit)
        print(f"{len(slugs)} vessels to visit", flush=True)

        for index, link in enumerate(slugs, 1):
            slug = link.rstrip("/").rsplit("/", 1)[-1]
            url = f"https://{HOST}{link}{SEASON_QUERY}"
            try:
                text, expanded = read_extras(page, url)
            except Exception as exc:  # noqa: BLE001 - one bad page must not end the run
                print(f"  [{index}/{len(slugs)}] {slug}: navigation failed: {exc}", flush=True)
                missing.append(slug)
                continue

            fees = parse_extras(text)
            if not fees:
                print(f"  [{index}/{len(slugs)}] {slug}: no extras found", flush=True)
                missing.append(slug)
            else:
                required = sum(1 for f in fees if f.tier.value == "mandatory")
                ranges = sum(1 for f in fees if f.is_range)
                unpriced = sum(1 for f in fees if not f.has_price)
                collected[slug] = {
                    "source_url": url,
                    "fees": to_fee_dicts(
                        fees,
                        {
                            "kind": "scraped",
                            "source_id": "liveaboard.com",
                            "retrieved": date.today().isoformat(),
                            "url": url,
                        },
                    ),
                }
                print(
                    f"  [{index}/{len(slugs)}] {slug}: {len(fees)} extras "
                    f"({required} required, {ranges} ranged, {unpriced} unpriced)"
                    + (" [expanded]" if expanded else ""),
                    flush=True,
                )
            page.wait_for_timeout(int(args.delay * 1000))

        browser.close()

    if not collected:
        print("no fees collected from any vessel", file=sys.stderr)
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "scraped_at": date.today().isoformat(),
                "source": "liveaboard.com",
                "vessels": collected,
                "missing": missing,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\nwrote {args.out}: {len(collected)} vessels with fees, {len(missing)} without")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
