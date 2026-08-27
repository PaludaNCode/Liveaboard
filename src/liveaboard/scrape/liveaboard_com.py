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

import calendar
import re
from typing import Any, Iterator

from .base import FetchResult, ScrapeError, ScrapeOutput, SourceAdapter
from . import jsonld

HOST = "www.liveaboard.com"

SEASON_MONTHS = (5, 6, 7, 8)
SEASON_YEAR = 2027
DESTINATION = "egypt"


def search_paths() -> tuple[str, ...]:
    """Month-scoped search URLs for the configured season.

    Derived rather than hardcoded so that moving the season moves the crawl
    with it. These are the right entry point: they return only trips sailing in
    a given month, so the crawl is already scoped to what the site publishes
    instead of filtering a whole destination afterwards.

        /diving/search/egypt/may/2027
        /diving/search/egypt/june/2027   ... and so on
    """
    return tuple(
        f"/diving/search/{DESTINATION}/{calendar.month_name[month].lower()}/{SEASON_YEAR}"
        for month in SEASON_MONTHS
    )


DESTINATION_PATHS = (
    "/diving/egypt",
    "/diving/egypt/red-sea",
)
"""Fallback listing pages.

Both confirmed live and carrying JSON-LD, but neither yielded a priced offer —
they are destination overviews, not search results. Kept as a secondary source
of boat links in case the search pages are rendered client-side.
"""

BOAT_LINK = re.compile(
    r'href="(?:https?://(?:www\.)?liveaboard\.com)?(/diving/[a-z0-9\-]+/[a-z0-9\-]+)"',
    re.IGNORECASE,
)
"""Boat detail links, absolute or relative.

The first attempt anchored on a literal ``/diving/egypt/`` prefix and matched
nothing across two live pages, so this accepts any two-segment ``/diving/``
path and an optional absolute host.
"""


class LiveaboardComAdapter(SourceAdapter):
    """Reads Egypt liveaboard listings and their departure calendars."""

    source_id = "liveaboard.com"
    host = HOST

    def discover(self) -> Iterator[str]:
        """Crawl the month searches, then each boat page they link to."""
        seen: set[str] = set()
        found_any = False

        for path in search_paths() + DESTINATION_PATHS:
            url = f"https://{self.host}{path}"
            try:
                listing = self.fetcher.get(url)
            except Exception as exc:  # noqa: BLE001 - a dead listing must not end the run
                # Reported rather than swallowed: a silently skipped listing is
                # indistinguishable from a site with nothing to sell.
                self.note(f"listing unavailable {url}: {exc}")
                continue
            yield url

            links = {match.group(1) for match in BOAT_LINK.finditer(listing.body)}
            if not links:
                self.note(f"no boat links matched on {url}")
            for link in sorted(links):
                boat_url = f"https://{self.host}{link}"
                if boat_url not in seen:
                    seen.add(boat_url)
                    found_any = True
                    if len(seen) > self.max_pages:
                        self.note(f"stopped at {self.max_pages} boat pages; more were available")
                        return
                    yield boat_url

        if not found_any:
            self.note("no boat pages were discovered from any listing")

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
