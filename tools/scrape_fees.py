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
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pw_browser import resolve as resolve_browser  # noqa: E402

from liveaboard.scrape.fees import (  # noqa: E402
    extras_excerpt,
    parse_extras,
    to_fee_dicts,
)
from liveaboard.scrape.gear import parse_gear, to_fee_dict as gear_fee_dict  # noqa: E402
from liveaboard.scrape.vessel import read_vessel  # noqa: E402
from liveaboard.scrape.liveaboard_com import (  # noqa: E402
    HOST,
    SEASON_QUERY,
    LiveaboardComAdapter,
    search_paths,
)

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


SPECS_ID = "#help-content-boat-amenities-specifications"
DIVING_ID = "#help-content-boat-amenities-diving"


def read_markup(page: Any, selector: str) -> str:
    """One hidden panel's markup, or empty when the vessel has no such panel.

    Every one of these is in the document at load time, so reading them costs
    no request beyond the page already fetched for the fee disclosure.
    """
    node = page.query_selector(selector)
    if node is None:
        return ""
    try:
        return node.inner_html() or ""
    except Exception:  # noqa: BLE001 - a detached node is not a failure
        return ""


def read_gear(page: Any) -> str:
    """The gear dialog's markup, or empty when the vessel has none.

    Read from the same page load rather than a second visit: the dialog is in
    the document already, hidden, so this costs no request. A vessel that does
    not rent gear simply has no such node, which is not a failure.
    """
    node = page.query_selector("#modal-gear")
    if node is None:
        return ""
    try:
        return node.inner_html() or ""
    except Exception:  # noqa: BLE001 - a detached node is not a failure
        return ""


def previous(path: Path) -> dict[str, Any]:
    """The fee book already on disk, or nothing on a first run."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("vessels", {})
    except (OSError, ValueError) as exc:
        # Better to rebuild from scratch than to stop, but say so: a silently
        # emptied fee book is exactly the failure this file guards against.
        print(f"could not read {path} ({exc}); starting a fresh fee book", file=sys.stderr)
        return {}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=Path("data/fees.json"), type=Path)
    parser.add_argument("--limit", type=int, default=0, help="cap vessels (0 = all)")
    parser.add_argument("--delay", type=float, default=4.0, help="seconds between vessels")
    # No default: tools/pw_browser.py explains why, and resolves it.
    parser.add_argument(
        "--executable", default=None, help="chromium binary path (default: Playwright's own)"
    )
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright missing: pip install playwright && playwright install chromium")
        return 2

    launch: dict[str, Any] = {"args": ["--no-sandbox"]}

    # Start from what is already on disk. A --limit run visits the first N
    # vessels and knows nothing about the rest, so writing only what it saw
    # would delete the other seventy-odd fee disclosures -- a partial view
    # overwriting a complete one. Merging makes a capped run an incremental
    # update, which is the only thing it can honestly be.
    collected: dict[str, Any] = dict(previous(args.out))
    known = len(collected)
    missing: list[str] = []

    with sync_playwright() as p:
        executable, reason = resolve_browser(p, args.executable)
        if executable:
            launch["executable_path"] = executable
        print(f"browser: {reason}")
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
            provenance = {
                "kind": "scraped",
                "source_id": "liveaboard.com",
                "retrieved": date.today().isoformat(),
                "url": url,
            }

            # The extras list names "Rental Gear" and leaves it blank; the
            # figures are in the dialog behind it. Read before the no-fees
            # check so a vessel that publishes gear prices and nothing else
            # is not filed as having disclosed nothing.
            gear = parse_gear(read_gear(page))
            gear_fee = gear_fee_dict(gear, provenance)

            # The specification table carries the guest count that the prose
            # match misses on half the fleet, and the diving amenities state
            # "Free Nitrox" outright -- which is the answer a hand-written
            # file has been standing in for on ten vessels.
            facts = read_vessel(
                read_markup(page, SPECS_ID), read_markup(page, DIVING_ID)
            )

            if not fees and gear_fee is None and not facts:
                print(f"  [{index}/{len(slugs)}] {slug}: no extras found", flush=True)
                missing.append(slug)
            else:
                required = sum(1 for f in fees if f.tier.value == "mandatory")
                ranges = sum(1 for f in fees if f.is_range)
                unpriced = sum(1 for f in fees if not f.has_price)

                # The dialog's gear line replaces the extras list's blank one:
                # same code, and one of them carries a figure.
                priced = to_fee_dicts(fees, provenance)
                if gear_fee is not None:
                    priced = [f for f in priced if f["code"] != gear_fee["code"]]
                    priced.append(gear_fee)

                collected[slug] = {
                    "source_url": url,
                    # Per vessel, because the book is now merged across runs
                    # and a single top-level date would claim every entry was
                    # collected on the day of the last capped run.
                    "collected": date.today().isoformat(),
                    # What the parse was made from. Without it a parser fix
                    # cannot be checked without driving a browser at the live
                    # site all over again.
                    "disclosure": extras_excerpt(text),
                    "gear": [i.as_text() for i in gear.items] or None,
                    "specs": {
                        "guests": facts.guests,
                        "cabins": facts.cabins,
                        "length_m": facts.length_m,
                        "year_built": facts.year_built,
                        # Stated, not inferred. "Free Nitrox" and "Nitrox
                        # available" are different claims and both are kept.
                        "nitrox_free": facts.nitrox_free,
                        "nitrox_available": facts.nitrox_available,
                    } if facts else None,
                    "fees": priced,
                }
                gear_note = (
                    f", gear {gear.bundle.as_text()}" if gear.bundle
                    else f", {len(gear.items)} gear items unbundled" if gear.items
                    else ""
                )
                spec_note = "".join(
                    part for part in (
                        f", {facts.guests} guests" if facts.guests else "",
                        ", free nitrox" if facts.nitrox_free else "",
                    ) if part
                )
                print(
                    f"  [{index}/{len(slugs)}] {slug}: {len(fees)} extras "
                    f"({required} required, {ranges} ranged, {unpriced} unpriced)"
                    + gear_note
                    + spec_note
                    + (" [expanded]" if expanded else ""),
                    flush=True,
                )
            page.wait_for_timeout(int(args.delay * 1000))

        browser.close()

    if not collected:
        print("no fees collected from any vessel", file=sys.stderr)
        return 1

    # A run that visited vessels and learned nothing from any of them has not
    # produced a fee book; it has kept the old one. Say so rather than letting
    # a silent no-op read as a successful refresh.
    if len(collected) == known and not missing:
        print("no vessel yielded fees this run; previous book kept", file=sys.stderr)

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
    print(
        f"\nwrote {args.out}: {len(collected)} vessels with fees "
        f"({len(collected) - known} new this run), {len(missing)} without"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
