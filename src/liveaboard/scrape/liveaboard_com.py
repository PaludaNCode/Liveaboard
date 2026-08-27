"""Adapter for liveaboard.com.

Status: **structural, not yet validated against the live site.** The host is
blocked by this environment's network policy, so the discovery patterns and the
markup fallback below are written from the site's known URL shape and have
never been run against a real response.

What *is* finished and trustworthy: the JSON-LD path. If the site embeds
schema.org ``Product``/``Offer`` data, which listing sites of this kind
normally do, this adapter reads prices without interpreting anyone's layout.

To finish it, allowlist the host, run ``python3 -m liveaboard.cli scrape
--source liveaboard.com --limit 1``, and read the snapshot it writes into
``data/snapshots/``. The parse contract is defined; only the selectors are open.
"""

from __future__ import annotations

import re
from typing import Any, Iterator

from .base import FetchResult, ScrapeError, ScrapeOutput, SourceAdapter
from . import jsonld

HOST = "www.liveaboard.com"

SEASON_MONTHS = (5, 6, 7, 8)
SEASON_YEAR = 2027

DESTINATION_PATHS = (
    "/diving/egypt",
    "/diving/egypt/red-sea",
)
"""Listing pages to crawl for boat and itinerary links.

Verify these against the live site before trusting a run: a 404 here yields an
empty scrape rather than an error, which is the failure mode most likely to go
unnoticed.
"""

BOAT_LINK = re.compile(r'href="(/diving/egypt/[a-z0-9\-]+)"', re.IGNORECASE)


class LiveaboardComAdapter(SourceAdapter):
    """Reads Egypt liveaboard listings and their departure calendars."""

    source_id = "liveaboard.com"
    host = HOST

    def discover(self) -> Iterator[str]:
        """Crawl the destination listings, then each boat page they link to."""
        seen: set[str] = set()
        for path in DESTINATION_PATHS:
            url = f"https://{self.host}{path}"
            try:
                listing = self.fetcher.get(url)
            except Exception:  # noqa: BLE001 - a dead listing must not end the run
                continue
            yield url
            for match in BOAT_LINK.finditer(listing.body):
                boat_url = f"https://{self.host}{match.group(1)}"
                if boat_url not in seen:
                    seen.add(boat_url)
                    yield boat_url

    def parse(self, result: FetchResult) -> ScrapeOutput:
        """Prefer structured data; fall back to markup only when there is none."""
        output = ScrapeOutput()
        products = jsonld.of_type(result.body, "Product", "TouristTrip", "Trip")
        if not products:
            raise ScrapeError(
                f"no JSON-LD Product/Trip node in {result.url}; "
                f"inspect the snapshot ({result.digest}) and add a markup parser"
            )

        for node in products:
            departure = self._departure_from(node, result)
            if departure:
                output.departures.append(departure)
        if not output.departures:
            output.warnings.append(f"{result.url}: JSON-LD present but carried no priced offer")
        return output

    def _departure_from(self, node: dict[str, Any], result: FetchResult) -> dict[str, Any] | None:
        """Build a departure record from one structured-data node.

        Returns ``None`` rather than guessing when the node carries no price:
        an invented number on a price-transparency site would be self-defeating.
        """
        offer = jsonld.first_offer(node)
        if not offer:
            return None
        price = offer.get("price")
        currency = offer.get("priceCurrency")
        if price is None or not currency:
            return None

        return {
            "name": node.get("name"),
            "price": {"amount": float(price), "currency": str(currency).upper()},
            "provenance": self.provenance(result.url),
            "source_url": result.url,
        }
