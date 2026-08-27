#!/usr/bin/env python3
"""Watch what a search page actually asks the network for.

If a listing is assembled client-side, the HTML is the wrong thing to parse:
somewhere behind it is a JSON endpoint returning the same trips in a form that
needs no scraping at all. This drives a real browser at the page, records every
XHR and fetch, and reports the shape of any JSON that comes back.

Run it when a scrape finds live pages but no data on them — the answer is
usually that the data arrives after the HTML does.

**Not part of the package.** It needs Playwright, and the library it probes for
is deliberately dependency-free. It lives in tools/ and runs in CI only.

    python3 tools/probe_network.py [--url URL ...] [--max-pages N]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pw_browser import resolve as resolve_browser  # noqa: E402

DEFAULT_URLS = [
    "https://www.liveaboard.com/diving/search/egypt/may/2027",
    "https://www.liveaboard.com/diving/search/egypt/june/2027",
    "https://www.liveaboard.com/diving/search/egypt/july/2027",
    "https://www.liveaboard.com/diving/search/egypt/august/2027",
]

INTERESTING = {"xhr", "fetch"}

# Query keys worth calling out: these are how a listing endpoint is usually
# paged, and paging is the immediate blocker on August's three pages.
PAGING_KEYS = ("page", "offset", "start", "limit", "per_page", "size", "cursor")

PRICE_KEY = re.compile(r"price|cost|amount|fare|rate", re.I)

BOAT_SLUG = re.compile(r'href="(?:https?://[^"/]+)?(/diving/egypt/[a-z0-9\-]+)"', re.I)


def _boat_slugs(html: str) -> set[str]:
    """Egypt boat paths in a page, used to tell one result page from another."""
    return {m.group(1).lower() for m in BOAT_SLUG.finditer(html)}


def shape(value: Any, depth: int = 0) -> str:
    """Summarise a JSON value's structure rather than dumping its content."""
    pad = "  " * depth
    if isinstance(value, dict):
        if depth >= 2:
            return f"{{{len(value)} keys}}"
        lines = []
        for key, item in list(value.items())[:14]:
            lines.append(f"{pad}  {key}: {shape(item, depth + 1)}")
        more = len(value) - 14
        if more > 0:
            lines.append(f"{pad}  ... {more} more keys")
        return "{\n" + "\n".join(lines) + f"\n{pad}}}"
    if isinstance(value, list):
        if not value:
            return "[] (empty)"
        return f"[{len(value)} items] first = {shape(value[0], depth + 1)}"
    if isinstance(value, str):
        return f"str({value[:40]!r})" if len(value) > 40 else f"str({value!r})"
    return type(value).__name__


def describe_json(payload: Any) -> list[str]:
    """Report the parts of a JSON response a parser would actually want."""
    lines = [shape(payload)]

    # Find the biggest list in the response: on a search endpoint that is
    # almost always the results array.
    biggest: tuple[int, str, list] | None = None

    def walk(node: Any, path: str) -> None:
        nonlocal biggest
        if isinstance(node, list):
            if biggest is None or len(node) > biggest[0]:
                biggest = (len(node), path, node)
            for item in node[:1]:
                walk(item, f"{path}[]")
        elif isinstance(node, dict):
            for key, item in node.items():
                walk(item, f"{path}.{key}" if path else key)

    walk(payload, "")
    if biggest and biggest[0] > 1 and isinstance(biggest[2][0], dict):
        count, path, items = biggest
        record = items[0]
        lines.append(f"    likely results array: {path or '<root>'} ({count} items)")
        lines.append(f"    record keys: {', '.join(sorted(record)[:25])}")
        priced = [k for k in record if PRICE_KEY.search(k)]
        if priced:
            lines.append(f"    price-ish keys: {', '.join(priced)}")
    return lines


FEE_WORDS = re.compile(
    r"not\s+included|excluded|extra(?:s)?\b|surcharge|marine\s*park|park\s*fee|"
    r"port\s*fee|harbou?r|fuel|nitrox|visa|gratuit|tip(?:s|ping)\b|"
    r"equipment\s*rental|rental\s*equipment|mandatory|payable\s+(?:on|at)",
    re.I,
)
"""The vocabulary operators use for the costs they leave out of the headline."""


def hunt_fees(page: Any, html: str) -> None:
    """Locate the fee disclosure on a page, in markup or in embedded JSON.

    This is the missing half of the dataset: prices scrape cleanly, fees do
    not, and until they do the site can only report an advertised number and
    admit it does not know the rest.
    """
    print("\n  -- fee disclosure --")

    blocks = page.eval_on_selector_all(
        "li, td, dd, p, span, div",
        """els => els
             .filter(e => e.children.length === 0)
             .map(e => (e.textContent || '').replace(/\\s+/g, ' ').trim())
             .filter(t => t.length > 3 && t.length < 160)""",
    )
    hits, seen = [], set()
    for text in blocks:
        if FEE_WORDS.search(text) and text.lower() not in seen:
            seen.add(text.lower())
            hits.append(text)
    if hits:
        print(f"    {len(hits)} fee-ish text nodes; first 30:")
        for text in hits[:30]:
            print(f"      · {text}")
    else:
        print("    no fee wording found in rendered text")

    # Embedded state often carries the fees as data long before the DOM shows
    # them, and data beats scraping prose off a layout.
    for script in re.findall(r"<script[^>]*>(.*?)</script>", html, re.S)[:60]:
        if not FEE_WORDS.search(script) or len(script) > 400_000:
            continue
        keys = sorted(set(re.findall(r'"([A-Za-z_][A-Za-z0-9_]{2,40})"\s*:', script)))
        fee_keys = [k for k in keys if FEE_WORDS.search(k) or PRICE_KEY.search(k)]
        if fee_keys:
            print(f"    embedded JSON keys of interest: {fee_keys[:30]}")
            for match in re.finditer(r'"([^"]{0,40}(?:ee|harge|ax|ip)s?)"\s*:\s*([0-9.]+)', script):
                print(f"      {match.group(1)} = {match.group(2)}")
            break


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", action="append", default=None)
    parser.add_argument("--max-pages", type=int, default=2, help="URLs to visit")
    parser.add_argument("--executable", default=None, help="chromium binary path")
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright is not installed: pip install playwright && playwright install chromium")
        return 2

    urls = (args.url or DEFAULT_URLS)[: args.max_pages]
    launch: dict[str, Any] = {"args": ["--no-sandbox"]}

    with sync_playwright() as p:
        executable, reason = resolve_browser(p, args.executable)
        if executable:
            launch["executable_path"] = executable
        print(f"browser: {reason}")
        browser = p.chromium.launch(**launch)
        for url in urls:
            print(f"\n{'=' * 78}\n{url}\n{'=' * 78}")
            probe_one(browser, url)
        browser.close()
    return 0


def probe_one(browser: Any, url: str) -> None:
    page = browser.new_page(viewport={"width": 1400, "height": 1000})
    calls: list[dict[str, Any]] = []

    def on_response(response: Any) -> None:
        request = response.request
        if request.resource_type not in INTERESTING:
            return
        entry = {
            "method": request.method,
            "url": response.url,
            "status": response.status,
            "type": (response.headers or {}).get("content-type", ""),
            "body": None,
        }
        if "json" in entry["type"]:
            try:
                entry["body"] = response.json()
            except Exception:  # noqa: BLE001 - a body we cannot read is still worth listing
                pass
        calls.append(entry)

    page.on("response", on_response)

    try:
        page.goto(url, wait_until="networkidle", timeout=60000)
    except Exception as exc:  # noqa: BLE001 - report and continue to the next URL
        print(f"  navigation failed: {exc}")
        page.close()
        return

    print(f"  title: {page.title()[:100]}")
    print(f"  final url: {page.url}")

    html = page.content()
    print(f"  rendered size: {len(html) / 1024:.0f} KB")

    # Paging is the immediate blocker, so look for it several ways rather than
    # trusting one selector: an anchor, a button, a rel=next hint, or the word
    # "page" anywhere in the rendered markup.
    print("\n  -- pagination --")

    anchors = page.eval_on_selector_all(
        "a[href*='page'], a[href*='Page'], [class*='pagin'] a, [class*='Pagin'] a, "
        "nav a, [aria-label*='age'] , link[rel='next'], a[rel='next']",
        """els => els.map(e => ({
             tag: e.tagName,
             href: e.getAttribute('href'),
             rel: e.getAttribute('rel'),
             label: (e.getAttribute('aria-label') || e.textContent || '').trim().slice(0, 30)
           })).filter(x => x.href || x.label).slice(0, 25)""",
    )
    for item in anchors:
        print(f"    {item['tag']:6} {str(item['href'])[:70]:72} {item['label']}")
    if not anchors:
        print("    no paginator anchors matched")

    buttons = page.eval_on_selector_all(
        "button, [role='button']",
        """els => els.map(e => (e.textContent || '').trim())
             .filter(t => /next|more|page|\\d+$/i.test(t) && t.length < 24).slice(0, 15)""",
    )
    if buttons:
        print(f"    button labels: {buttons}")

    for pattern in (r"[?&]page=\d+", r"[?&]p=\d+", r"/page/\d+", r'"totalPages"\s*:\s*\d+',
                    r'"pageCount"\s*:\s*\d+', r"showing\s+\d+\s*[-–]\s*\d+\s+of\s+\d+"):
        hits = re.findall(pattern, html, re.I)
        if hits:
            unique = sorted(set(hits))[:8]
            print(f"    markup matches {pattern}: {unique}")

    # Does ?page=2 actually return different boats, or silently repeat page 1?
    first_ids = _boat_slugs(html)
    print(f"    boat links on this page: {len(first_ids)}")
    try:
        page.goto(f"{url}?page=2", wait_until="domcontentloaded", timeout=45000)
        second = _boat_slugs(page.content())
        overlap = len(first_ids & second)
        print(
            f"    ?page=2 -> {len(second)} boat links, {overlap} shared with page 1"
            + ("  (SAME PAGE — paging is not ?page=)" if second and overlap == len(second) else "")
        )
    except Exception as exc:  # noqa: BLE001 - the probe must not die on one navigation
        print(f"    ?page=2 navigation failed: {exc}")

    hunt_fees(page, html)

    if not calls:
        print("  no XHR/fetch calls — the listing is server-rendered, parse the HTML")
        page.close()
        return

    print(f"\n  {len(calls)} XHR/fetch calls:")
    for call in calls:
        parsed = urlparse(call["url"])
        query = parsed.query
        flags = [k for k in PAGING_KEYS if f"{k}=" in query]
        marker = f"  [paging: {', '.join(flags)}]" if flags else ""
        print(f"    {call['method']:5} {call['status']} {parsed.netloc}{parsed.path}{marker}")
        if query:
            print(f"          ?{query[:180]}")

    for call in calls:
        if call["body"] is None:
            continue
        print(f"\n  --- JSON from {urlparse(call['url']).path} ---")
        for line in describe_json(call["body"]):
            print(f"  {line}")

    page.close()


if __name__ == "__main__":
    raise SystemExit(main())
