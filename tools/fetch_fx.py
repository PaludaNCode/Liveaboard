#!/usr/bin/env python3
"""Fetch the European Central Bank's daily euro reference rates.

Every advertised price in the dataset is quoted in dollars and every figure on
the page is shown in euro, so one exchange rate sits underneath the entire
site. It was a hardcoded ``0.92`` whose own table admitted to being a
placeholder, and the page rendered it as "converted at 0.92 (2026-08-27)" --
which reads as a rate somebody looked up that day.

The ECB feed is the right source for this: free, no key, one small XML file,
published every working day, and authoritative enough that nobody has to
wonder whose number it is.

Run from CI. A development sandbox behind a strict egress allowlist cannot
reach ecb.europa.eu, which is also why this validates loudly rather than
guessing: an unrecognised response fails the run and prints what came back,
instead of quietly writing a rate nobody checked.

    python3 tools/fetch_fx.py --out data/fx.json
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

ECB_DAILY = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"

SOURCE = "European Central Bank euro foreign exchange reference rates"
"""Recorded on every converted line. Must not match money.PLACEHOLDER_SOURCE,
or the page would keep warning that the rate is a stand-in."""

USER_AGENT = (
    "LiveaboardPriceTransparency/1.0 "
    "(+https://github.com/PaludaNCode/Liveaboard; price-transparency research)"
)

# The site quotes in these; anything else the ECB publishes is noise here. EGP
# is deliberately absent -- the ECB does not publish it, and carrying an
# unsourced Egyptian rate beside sourced ones is the exact mix this replaces.
# A price in an unlisted currency raises in FxTable.to_display rather than
# converting at a number nobody stands behind.
WANTED = ("USD", "GBP")

REQUIRED = "USD"
"""Every advertised price is quoted in dollars. Without this there is no site."""


def fetch(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def parse(xml: bytes) -> tuple[str, dict[str, Decimal]]:
    """Pull the quote date and the wanted rates out of the ECB envelope.

    The feed nests ``Cube`` elements inside a gesmes envelope and namespaces
    them, so elements are matched on their local name and attributes rather
    than on a namespace-qualified path. That survives the ECB changing its
    namespace URI, which it has done before.

    Rates are published as units of foreign currency per one euro. The dataset
    needs the other direction -- what one dollar is worth in euro -- so each is
    inverted here rather than anywhere the number could be mistaken for the
    published one.
    """
    root = ET.fromstring(xml)

    quoted_on = ""
    rates: dict[str, Decimal] = {}

    for element in root.iter():
        attrib = element.attrib
        if "time" in attrib:
            quoted_on = attrib["time"]
        currency = attrib.get("currency")
        if currency in WANTED and "rate" in attrib:
            per_euro = Decimal(attrib["rate"])
            if per_euro <= 0:
                raise ValueError(f"{currency} rate is {per_euro}, which cannot be inverted")
            rates[currency] = (Decimal(1) / per_euro).quantize(Decimal("0.000001"))

    if not quoted_on:
        raise ValueError("no Cube carried a time attribute")
    date.fromisoformat(quoted_on)  # raises if the ECB ever changes the format
    if REQUIRED not in rates:
        raise ValueError(f"no {REQUIRED} rate in the response")
    return quoted_on, rates


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=Path("data/fx.json"), type=Path)
    parser.add_argument("--url", default=ECB_DAILY)
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()

    body = b""
    try:
        body = fetch(args.url, args.timeout)
        quoted_on, rates = parse(body)
    except (urllib.error.URLError, ET.ParseError, ValueError, OSError) as exc:
        print(f"could not read rates from {args.url}: {exc}", file=sys.stderr)
        # A stale rate is not a missing rate. Yesterday's real ECB number is a
        # far better answer than reverting to a made-up one, and the file
        # already records the day it was quoted, so the page keeps telling the
        # truth about its own age without anything being rewritten here.
        if args.out.exists():
            print(f"keeping the existing {args.out}; it says when it was quoted")
            return 0
        print(
            "no previous rates to fall back on. Publishing would mean inventing "
            "an exchange rate for every price on the site.",
            file=sys.stderr,
        )
        if body:
            # Print what actually came back rather than leaving the next person
            # to guess at a format nobody has looked at.
            print(body[:500].decode("utf-8", "replace"), file=sys.stderr)
        return 1

    payload: dict[str, Any] = {
        "display_currency": "EUR",
        "as_of": quoted_on,
        "source": SOURCE,
        "url": args.url,
        "retrieved": date.today().isoformat(),
        "rates": {code: float(rate) for code, rate in sorted(rates.items())},
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    quoted = ", ".join(f"1 {code} = {rate} EUR" for code, rate in sorted(rates.items()))
    print(f"rates quoted {quoted_on}: {quoted} -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
