#!/usr/bin/env python3
"""What a vessel page states about extras, and what of it needs a browser.

Two questions, one page load each, and both were answered wrong before this was
written.

**The disclosure is three blocks and `fees.BLOCK` matched two.** Every vessel
page prints `Included:` above `Required Extras:` and `Optional Extras:`, in the
same paragraph and the same comma-separated prose, and nothing opened it -- so
liveaboard.com's own statement of what the fare covers reached no bill, while
the same rule was being enforced on PADI's `whatsIncludedNew`. Bella 2 is the
case: the other seller charges 50 EUR for nitrox on that boat and this one lists
it under Included, and the page showed neither.

**And "needs a browser" is not the same as "rendered client-side".**
`scrape_fees.py` drives Playwright for four panels. The extras and the gear
dialog parse from the *served bytes* exactly as they do through the browser. The
specification table and the diving amenities are in those bytes too and fail for
a different reason: the page ships `<dl><dt>Year built <dd>2017</dl>` with the
tags unclosed, so `SPEC_ROW` and `TICK` are getting their closing tags from the
browser's normalised DOM rather than from the page.

So this prints, per vessel: the three blocks as the parser bounds them, what
each extra classifies to, the gear bundle's amount **and whether it states a
unit at all** -- five vessels quote `<span>€40</span>` with nothing after it --
and whether the spec and amenity parsers answer from raw HTML.

Writes nothing. Plain `urllib`, no browser, one request per vessel.

    python3 tools/probe_disclosure.py                      # every vessel in the fee book
    python3 tools/probe_disclosure.py --vessels bella-2    # or a named few
    python3 tools/probe_disclosure.py --summary            # counts only
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from liveaboard.scrape.fees import BLOCK, classify_label, parse_extras  # noqa: E402
from liveaboard.scrape.gear import parse_gear  # noqa: E402
from liveaboard.scrape.vessel import parse_amenities, parse_specs  # noqa: E402

UA = {"User-Agent": "Mozilla/5.0 (compatible; liveaboard-import/1.0; +price-transparency research)"}

FEE_BOOK = Path("data/fees.json")

TAGS = re.compile(r"<[^>]+>")
SCRIPTS = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)

#: Where the served HTML puts the two panels whose parsers want a browser. Both
#: are outside the modal ids `scrape_fees.py` queries, which is the second half
#: of why raw HTML looked empty for them.
SPEC_HEADING = "Boat Specifications"
AMENITY_LIST = "boat-highlights-bundle"


def page_text(markup: str) -> str:
    """The page as text, the way a browser's ``innerText`` reaches the parser."""
    return html.unescape(TAGS.sub(" ", SCRIPTS.sub("", markup)))


def around(markup: str, needle: str, span: int = 6000) -> str:
    index = markup.find(needle)
    return markup[max(0, index - 500):index + span] if index >= 0 else ""


def describe(slug: str, markup: str, summary: bool, counts: Counter) -> None:
    text = page_text(markup)

    blocks = dict((head.lower(), " ".join(body.split())) for head, body in BLOCK.findall(text))
    counts[tuple(sorted(blocks))] += 1

    fees = parse_extras(text)
    counts["extras read"] += bool(fees)
    for fee in fees:
        counts[("included" if fee.included else "charged", fee.code.value)] += 1

    reading = parse_gear(html.unescape(markup))
    bundle = reading.bundle
    if bundle is not None:
        counts["bundle: unit stated" if bundle.basis else "bundle: NO UNIT STATED"] += 1
    else:
        counts["no priced bundle"] += 1

    specs = parse_specs(around(markup, SPEC_HEADING))
    amenities = parse_amenities(around(markup, AMENITY_LIST))
    counts["specs from raw html" if specs else "specs NEED the browser"] += 1
    counts["amenities from raw html" if amenities else "amenities NEED the browser"] += 1

    if summary:
        return

    print(f"\n=== {slug}")
    for heading in ("included", "required", "optional"):
        body = blocks.get(heading)
        print(f"  {heading + ':':<10} {body[:160] if body else '-- block absent --'}")
    for fee in fees:
        state = "included" if fee.included else (f"{fee.low:g}" if fee.has_price else "no price")
        print(f"     {fee.code.value:<18} {fee.tier.value:<12} {state:<10} {fee.label[:44]}")
    if bundle is not None:
        unit = bundle.basis.value if bundle.basis else "NO UNIT STATED"
        print(f"  gear bundle: {bundle.low:g} {bundle.currency} {unit}  ({bundle.label[:50]})")
    print(f"  specs from raw html: {specs or 'none -- unclosed <dt>/<dd>, needs a normalised DOM'}")
    print(f"  amenities from raw html: {amenities or 'none -- unclosed <li>, same reason'}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vessels", help="comma-separated slugs; default is the whole fee book")
    parser.add_argument("--summary", action="store_true", help="counts only, no per-vessel dump")
    parser.add_argument("--delay", type=float, default=0.3)
    args = parser.parse_args()

    if not FEE_BOOK.exists():
        print(f"{FEE_BOOK} not found; it is where the vessel urls come from", file=sys.stderr)
        return 1
    vessels = json.loads(FEE_BOOK.read_text())["vessels"]

    wanted = args.vessels.split(",") if args.vessels else sorted(vessels)
    counts: Counter = Counter()
    for slug in wanted:
        url = (vessels.get(slug) or {}).get("source_url")
        if not url:
            print(f"{slug}: not in {FEE_BOOK}", file=sys.stderr)
            continue
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:
                markup = r.read().decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001 - one bad page must not end the probe
            print(f"{slug}: {exc}", file=sys.stderr)
            continue
        describe(slug, markup, args.summary, counts)
        time.sleep(args.delay)

    print(f"\n--- {len(wanted)} vessel(s)")
    for key, n in counts.most_common():
        print(f"  {n:>4} {key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
