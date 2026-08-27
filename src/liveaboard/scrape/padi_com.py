"""Adapter for padi.com.

Status: **structural, not yet validated against the live site.** The host is
blocked by this environment's network policy.

PADI plays a different role in this dataset than liveaboard.com does. It is
weak on departure-level pricing and strong on the things the price comparison
needs in order to be fair: which certification a trip demands, how many logged
dives, and what the operator is accredited to run. So this adapter is scoped to
*requirements and accreditation* rather than to prices, and its output enriches
itineraries matched from the other source instead of creating departures.

Keeping it narrow also keeps the crawl small, which is the polite thing to do
when the useful part of a site is a fraction of its pages.
"""

from __future__ import annotations

import re
from typing import Iterator

from .base import FetchResult, ScrapeError, ScrapeOutput, SourceAdapter
from . import jsonld
from ..taxonomy import DiverLevel

HOST = "www.padi.com"

TRAVEL_PATHS = (
    "/travel/liveaboards",
    "/travel/destinations/egypt",
)
"""Entry points for PADI Travel's liveaboard listings. Verify before trusting."""

TRIP_LINK = re.compile(r'href="(/travel/[a-z0-9\-/]*liveaboard[a-z0-9\-/]*)"', re.IGNORECASE)

CERT_PATTERNS: tuple[tuple[re.Pattern[str], DiverLevel], ...] = (
    (re.compile(r"\b(master\s+scuba|divemaster)\b", re.I), DiverLevel.EXPERIENCED_100),
    (re.compile(r"\badvanced\s+open\s+water\b|\baowd?\b", re.I), DiverLevel.ADVANCED),
    (re.compile(r"\bopen\s+water\b", re.I), DiverLevel.OPEN_WATER),
)

DIVES_PATTERN = re.compile(
    r"(?:minimum\s+of\s+|min\.?\s*|at\s+least\s+)?(\d{2,3})\s*(?:\+\s*)?logged\s+dives",
    re.IGNORECASE,
)
"""Matches the industry's stock phrasings: "50 logged dives", "minimum of 50
logged dives", "100+ logged dives"."""


class PadiComAdapter(SourceAdapter):
    """Reads certification and experience prerequisites from PADI Travel."""

    source_id = "padi.com"
    host = HOST

    def discover(self) -> Iterator[str]:
        seen: set[str] = set()
        for path in TRAVEL_PATHS:
            url = f"https://{self.host}{path}"
            try:
                listing = self.fetcher.get(url)
            except Exception:  # noqa: BLE001 - a dead listing must not end the run
                continue
            yield url
            for match in TRIP_LINK.finditer(listing.body):
                trip_url = f"https://{self.host}{match.group(1)}"
                if trip_url not in seen:
                    seen.add(trip_url)
                    yield trip_url

    def parse(self, result: FetchResult) -> ScrapeOutput:
        output = ScrapeOutput()
        name = self._name(result)
        requirements = self.extract_requirements(result.body)
        if not requirements:
            raise ScrapeError(f"no certification requirements found in {result.url}")

        output.itineraries.append(
            {
                "name": name,
                "requirements": requirements,
                "source_url": result.url,
                "provenance": self.provenance(result.url),
            }
        )
        return output

    @staticmethod
    def extract_requirements(html: str) -> dict[str, object] | None:
        """Pull the entry bar out of page text.

        Deliberately conservative: it reports only what the page states in the
        industry's standard phrasing. Inferring a requirement that was never
        written down would turn a safety gate into a guess.
        """
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text)

        level: DiverLevel | None = None
        for pattern, candidate in CERT_PATTERNS:
            if pattern.search(text):
                level = candidate
                break

        dives_match = DIVES_PATTERN.search(text)
        min_dives = int(dives_match.group(1)) if dives_match else 0

        if level is None and not min_dives:
            return None

        return {
            "min_level": (level or DiverLevel.OPEN_WATER).value,
            "min_logged_dives": min_dives,
            "strong_current": bool(re.search(r"strong current|drift div", text, re.I)),
        }

    def _name(self, result: FetchResult) -> str | None:
        for node in jsonld.of_type(result.body, "Product", "TouristTrip", "Trip"):
            if isinstance(node.get("name"), str):
                return node["name"]
        match = re.search(r"<title[^>]*>(.*?)</title>", result.body, re.I | re.S)
        return match.group(1).strip() if match else None
