"""Describe a fetched page's structure without needing to look at the file.

Snapshots are the proper record, but they are a build artifact — when the only
channel back from a scrape is a CI log, an artifact you cannot download tells
you nothing. This turns a page into a few lines of structure: what JSON-LD it
carries, what its links look like, whether a price appears anywhere.

Enough to write a parser against, and small enough to read in a log.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import TYPE_CHECKING

from . import jsonld

if TYPE_CHECKING:
    from .base import FetchResult

TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
HREF = re.compile(r'href="([^"#?]+)', re.I)
PRICE_HINT = re.compile(r"(?:€|\$|EUR|USD)\s?\d[\d.,]{2,}", re.I)

TOP_PATTERNS = 12
SAMPLE_LINKS = 4


def _types(html: str) -> Counter[str]:
    """Count every ``@type`` in the page's structured data."""
    counts: Counter[str] = Counter()
    for document in jsonld.extract_blocks(html):
        for node in jsonld.walk(document):
            raw = node.get("@type")
            for name in raw if isinstance(raw, list) else [raw]:
                if isinstance(name, str):
                    counts[name] += 1
    return counts


def _link_shapes(html: str) -> tuple[Counter[str], dict[str, str]]:
    """Group links by their first two path segments.

    The shape is what matters when guessing a detail-page pattern; the sample
    is what confirms it. ``/diving/egypt/*`` occurring 40 times is the signal
    that a boat page lives under that prefix.
    """
    shapes: Counter[str] = Counter()
    samples: dict[str, str] = {}
    for href in HREF.findall(html):
        path = re.sub(r"^https?://[^/]+", "", href)
        if not path.startswith("/"):
            continue
        parts = [p for p in path.split("/") if p]
        shape = "/" + "/".join(parts[:2]) + ("/*" if len(parts) > 2 else "")
        shapes[shape] += 1
        samples.setdefault(shape, path)
    return shapes, samples


def describe(result: FetchResult) -> str:
    """Render a compact structural summary of one fetched page."""
    html = result.body
    lines: list[str] = []

    title = TITLE.search(html)
    lines.append(f"   ~ {result.url}")
    lines.append(f"     status {result.status} · {len(html) / 1024:.0f} KB · digest {result.digest}")
    if title:
        clean = re.sub(r"\s+", " ", title.group(1)).strip()
        lines.append(f"     title: {clean[:90]}")

    types = _types(html)
    if types:
        rendered = ", ".join(f"{name}×{n}" for name, n in types.most_common(8))
        lines.append(f"     json-ld: {rendered}")
    else:
        lines.append("     json-ld: none")

    offers = [n for n in jsonld.walk_documents(html) if "offers" in n or "price" in n]
    if offers:
        lines.append(f"     json-ld nodes carrying offers/price: {len(offers)}")

    prices = PRICE_HINT.findall(html)
    if prices:
        lines.append(f"     price-shaped strings in markup: {len(prices)} (e.g. {prices[0]!r})")

    shapes, samples = _link_shapes(html)
    if shapes:
        lines.append("     link shapes:")
        for shape, count in shapes.most_common(TOP_PATTERNS):
            lines.append(f"       {count:>4}  {shape:<34} e.g. {samples[shape][:60]}")
    else:
        lines.append("     link shapes: none — the page is probably rendered client-side")

    return "\n".join(lines)
