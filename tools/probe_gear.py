#!/usr/bin/env python3
"""Look at what the rental-gear modal actually contains.

"Rental Gear" appears in almost every vessel's optional extras with no figure
beside it, so the site can say a diver will be charged for gear and not what.
The prices are a click away: the listing links to ``#modal-gear``, which opens
a dialog the page builds client-side.

This writes no parser. The rule is to fetch first and parse what came back, so
this prints the dialog -- how it opens, what it is called, and the shape of the
text inside -- and stops there. The sandbox cannot reach the host, so it runs
on a runner.

    python3 tools/probe_gear.py [--vessels aphrodite,blue-seas] [--dump-html]
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

from liveaboard.scrape.liveaboard_com import HOST, SEASON_QUERY  # noqa: E402

DEFAULT_VESSELS = ("aphrodite", "blue-seas", "emperor-asmaa")

# Anything that could be the dialog. Cast wide on purpose: the point of a probe
# is to find out what the markup is called, not to assume it.
CANDIDATES = (
    "#modal-gear",
    "[id*='modal-gear']",
    "[id*='gear']",
    "[class*='modal']",
    "dialog",
    "[role='dialog']",
)

OPENERS = re.compile(r"rental\s*gear|equipment\s*rental|gear\s*rental", re.I)

# A price of any of the three shapes the disclosure uses elsewhere.
PRICE = re.compile(r"(?:€|EUR|\$|USD)\s?\d[\d.,]*|\d[\d.,]*\s?(?:€|EUR|\$|USD)", re.I)


def describe(page: Any, selector: str, dump_html: bool) -> bool:
    """Print what one selector matched, if anything. True when it found text."""
    found = False
    for index, node in enumerate(page.query_selector_all(selector)):
        try:
            text = (node.inner_text() or "").strip()
        except Exception:  # noqa: BLE001 - a detached node is not a failure
            continue
        if not text:
            continue
        found = True
        prices = PRICE.findall(text)
        print(f"    {selector} [{index}] {len(text)} chars, {len(prices)} prices")
        print("      " + "\n      ".join(text.splitlines()[:40]))
        if dump_html:
            try:
                print("      --- html ---")
                print("      " + (node.inner_html() or "")[:2000])
            except Exception:  # noqa: BLE001
                pass
    return found


def probe(page: Any, slug: str, dump_html: bool) -> None:
    url = f"https://{HOST}/diving/egypt/{slug}{SEASON_QUERY}"
    print(f"\n=== {slug} ===")

    # First: does the hash alone open it? That is how the link is written.
    page.goto(f"{url}#modal-gear", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2500)
    print("  after #modal-gear:")
    opened = any(describe(page, sel, dump_html) for sel in CANDIDATES)

    if opened:
        return

    # Otherwise find the control that opens it and say what kind of node it is,
    # which is what a scraper would have to click.
    print("  nothing matched; looking for an opener")
    for node in page.query_selector_all("a, button, [role='button']"):
        try:
            label = (node.inner_text() or "").strip()
        except Exception:  # noqa: BLE001
            continue
        if not OPENERS.search(label):
            continue
        tag = node.evaluate("el => el.tagName.toLowerCase()")
        href = node.get_attribute("href") or ""
        print(f"    opener: <{tag}> {label!r} href={href!r}")
        try:
            node.click(timeout=3000)
            page.wait_for_timeout(2000)
        except Exception as exc:  # noqa: BLE001
            print(f"      click failed: {exc}")
            continue
        print("  after click:")
        for sel in CANDIDATES:
            describe(page, sel, dump_html)
        return
    print("    no opener found either")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vessels", default=",".join(DEFAULT_VESSELS))
    parser.add_argument("--dump-html", action="store_true")
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

    with sync_playwright() as p:
        executable, reason = resolve_browser(p, args.executable)
        if executable:
            launch["executable_path"] = executable
        print(f"browser: {reason}")
        browser = p.chromium.launch(**launch)
        page = browser.new_page(viewport={"width": 1400, "height": 1200})
        for slug in [s.strip() for s in args.vessels.split(",") if s.strip()]:
            try:
                probe(page, slug, args.dump_html)
            except Exception as exc:  # noqa: BLE001 - one bad page must not end the run
                print(f"  {slug}: failed: {exc}")
            page.wait_for_timeout(3000)
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
