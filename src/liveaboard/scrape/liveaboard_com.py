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
from datetime import date
from typing import Any, Iterator
from urllib.parse import urlparse

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


MAX_SEARCH_PAGES = 5
"""Result pages to walk per month.

August is known to run to three. Five leaves headroom without crawling
forever; the walk stops early as soon as a page yields no new boats.
"""

DESTINATION_PATHS = (
    "/diving/egypt",
    "/diving/egypt/red-sea",
)
"""Fallback listing pages.

Both confirmed live, but neither yielded a priced offer — they are destination
overviews, not search results. Kept as a secondary source of boat links.
"""

BOAT_LINK = re.compile(
    r'href="(?:https?://(?:www\.)?liveaboard\.com)?(/diving/' + DESTINATION + r'/[a-z0-9\-]+)"',
    re.IGNORECASE,
)
"""Boat detail links for the configured destination, absolute or relative.

Scoped deliberately tightly. A previous version accepted any two-segment
``/diving/`` path, and because the search page is a global template linking
every destination the site sells — 138 Indonesia links, 74 Rhine river cruises
— the crawler walked off into Antarctica. The page's own links are no guide to
what the page is about.
"""

NON_BOAT_SLUGS = frozenset(
    {
        "red-sea", "sharm-el-sheikh", "hurghada", "marsa-alam", "port-ghalib",
        "safaga", "brothers-islands", "daedalus-reef", "elphinstone",
        "st-johns", "fury-shoal", "ras-mohammed", "tiran", "thistlegorm",
        "abu-nuhas", "liveaboards", "reviews", "deals",
    }
)
"""Second-path segments under ``/diving/egypt/`` that are regions, dive sites or
site furniture rather than vessels. ``/diving/egypt/red-sea`` matched the boat
pattern in the last run and is not a boat."""


class LiveaboardComAdapter(SourceAdapter):
    """Reads Egypt liveaboard listings and their departure calendars."""

    source_id = "liveaboard.com"
    host = HOST

    @staticmethod
    def boat_links(html: str) -> set[str]:
        """Vessel paths on a listing page, with regions and dive sites removed."""
        found = set()
        for match in BOAT_LINK.finditer(html):
            path = match.group(1).lower()
            if path.rsplit("/", 1)[-1] not in NON_BOAT_SLUGS:
                found.add(path)
        return found

    def _listing_urls(self) -> Iterator[str]:
        """Every result page of every month search, then the fallback listings.

        Paging is walked rather than assumed: the loop stops as soon as a page
        adds no boat the previous ones did not, so a month with one page costs
        one extra request and a month with three is covered in full.
        """
        for base in search_paths():
            per_month: set[str] = set()
            for page in range(1, MAX_SEARCH_PAGES + 1):
                url = f"https://{self.host}{base}" + (f"?page={page}" if page > 1 else "")
                try:
                    listing = self.fetcher.get(url)
                except Exception as exc:  # noqa: BLE001 - a dead page must not end the run
                    self.note(f"listing unavailable {url}: {exc}")
                    break

                links = self.boat_links(listing.body)
                fresh = links - per_month
                if page > 1 and not fresh:
                    break
                if not links:
                    self.note(f"no boat links matched on {url}")
                    break
                per_month |= links
                yield url
            self.note(f"{base}: {len(per_month)} boats across up to {MAX_SEARCH_PAGES} pages")

        for path in DESTINATION_PATHS:
            url = f"https://{self.host}{path}"
            try:
                self.fetcher.get(url)
            except Exception as exc:  # noqa: BLE001
                self.note(f"listing unavailable {url}: {exc}")
                continue
            yield url

    def discover(self) -> Iterator[str]:
        """Crawl the month searches, then each boat page they link to."""
        seen: set[str] = set()

        for listing_url in self._listing_urls():
            yield listing_url
            # Already snapshotted by the fetcher, so re-reading is free.
            listing = self.fetcher.get(listing_url)
            for link in sorted(self.boat_links(listing.body)):
                boat_url = f"https://{self.host}{link}"
                if boat_url in seen:
                    continue
                seen.add(boat_url)
                if len(seen) > self.max_pages:
                    self.note(f"stopped at {self.max_pages} boat pages; more were available")
                    return
                yield boat_url

        if not seen:
            self.note("no boat pages were discovered from any listing")

    def parse(self, result: FetchResult) -> ScrapeOutput:
        """Read a boat page's departures from its structured data.

        The departures are ``Event`` nodes, each with its own ``Offer`` — a live
        page carried ten of each. The ``Product`` node describes the vessel and
        typically holds only an ``AggregateOffer``, which is a "from" price and
        no use for comparing what a specific sailing costs.
        """
        output = ScrapeOutput()
        slug = urlparse(result.url).path.rstrip("/").rsplit("/", 1)[-1]

        events = jsonld.of_type(result.body, "Event", "TouristTrip", "Trip")
        products = jsonld.of_type(result.body, "Product")

        if not events and not products:
            raise ScrapeError(
                f"no JSON-LD Event or Product node in {result.url}; "
                f"inspect the snapshot ({result.digest}) and add a markup parser"
            )

        if products:
            product = products[0]
            output.itineraries.append(
                {
                    "id": slug,
                    "name": product.get("name"),
                    "boat": product.get("name"),
                    "summary": product.get("description"),
                    "source_url": result.url,
                    "provenance": self.provenance(result.url),
                }
            )

        for index, event in enumerate(events):
            departure = self._departure_from(event, result, slug, index)
            if departure:
                output.departures.append(departure)

        if events and not output.departures:
            output.warnings.append(
                f"{result.url}: {len(events)} Event nodes but none carried a usable price"
            )
        elif not events:
            output.warnings.append(f"{result.url}: Product node but no Event nodes")
        return output

    def _departure_from(
        self, node: dict[str, Any], result: FetchResult, slug: str, index: int
    ) -> dict[str, Any] | None:
        """Build one departure from an ``Event`` node.

        Returns ``None`` rather than guessing whenever a date or price is
        missing. An invented number on a price-transparency site would be
        self-defeating, and a departure without dates cannot be booked or
        compared anyway.
        """
        start = _iso_date(node.get("startDate"))
        end = _iso_date(node.get("endDate"))
        if not start or not end:
            return None

        offer = jsonld.first_offer(node)
        if not offer:
            return None
        price = offer.get("price")
        currency = offer.get("priceCurrency")
        if price is None or not currency:
            return None
        try:
            amount = float(str(price).replace(",", ""))
        except ValueError:
            return None

        return {
            "id": f"{slug}-{start}-{index}",
            "itinerary_id": slug,
            "name": node.get("name"),
            "start": start,
            "end": end,
            "price": {"amount": amount, "currency": str(currency).upper()},
            "availability": offer.get("availability"),
            "booking_url": offer.get("url") or node.get("url"),
            "provenance": self.provenance(result.url),
        }


def _iso_date(value: Any) -> str | None:
    """Take the date part of a schema.org date or dateTime.

    Accepts ``2027-05-01`` and ``2027-05-01T00:00:00+02:00`` alike, and refuses
    anything that is not a plain ISO date once the time is stripped.
    """
    if not isinstance(value, str) or len(value) < 10:
        return None
    head = value[:10]
    try:
        date.fromisoformat(head)
    except ValueError:
        return None
    return head
