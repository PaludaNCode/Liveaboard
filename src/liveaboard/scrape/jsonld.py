"""Pull JSON-LD blocks out of a page.

Travel sites almost universally embed schema.org metadata for search engines —
``Product``, ``Offer``, ``Trip``, ``TouristTrip`` — and that structured block is
a far better source than the rendered HTML: it survives redesigns, it carries
prices in a machine-readable currency, and reading it puts no interpretation of
someone's layout into this codebase.

So every adapter tries this first and only falls back to markup when a page
carries no usable structured data.
"""

from __future__ import annotations

import json
from html.parser import HTMLParser
from typing import Any, Iterator


class _ScriptCollector(HTMLParser):
    """Collect the contents of every ``<script type="application/ld+json">``."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self._capturing = False
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "script":
            return
        attributes = {k.lower(): (v or "").lower() for k, v in attrs}
        if attributes.get("type") == "application/ld+json":
            self._capturing = True
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._capturing:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._capturing:
            self._capturing = False
            self.blocks.append("".join(self._buffer))


def extract_blocks(html: str) -> list[Any]:
    """Return every JSON-LD document in the page, skipping malformed ones.

    A broken block is skipped rather than fatal: sites routinely ship one
    invalid block alongside three good ones, and losing the good ones over it
    would be a poor trade.
    """
    collector = _ScriptCollector()
    try:
        collector.feed(html)
    except Exception:  # noqa: BLE001 - malformed markup must not stop a scrape
        pass

    documents: list[Any] = []
    for block in collector.blocks:
        text = block.strip()
        if not text:
            continue
        try:
            documents.append(json.loads(text))
        except json.JSONDecodeError:
            continue
    return documents


def walk(node: Any) -> Iterator[dict[str, Any]]:
    """Yield every dict in a nested JSON-LD structure, including ``@graph`` members."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from walk(item)


def of_type(html: str, *types: str) -> list[dict[str, Any]]:
    """Find every JSON-LD node whose ``@type`` matches one of ``types``."""
    wanted = {t.lower() for t in types}
    found: list[dict[str, Any]] = []
    for document in extract_blocks(html):
        for node in walk(document):
            raw = node.get("@type")
            names = raw if isinstance(raw, list) else [raw]
            if any(isinstance(n, str) and n.lower() in wanted for n in names):
                found.append(node)
    return found


def first_offer(node: dict[str, Any]) -> dict[str, Any] | None:
    """Return the first ``Offer`` attached to a node, if there is one."""
    offers = node.get("offers")
    if isinstance(offers, dict):
        return offers
    if isinstance(offers, list) and offers:
        candidate = offers[0]
        return candidate if isinstance(candidate, dict) else None
    return None
