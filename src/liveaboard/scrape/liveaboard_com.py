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
from .fees import parse_extras, to_fee_dicts

HOST = "www.liveaboard.com"

SEASON_MONTHS = (5, 6, 7, 8)
SEASON_YEAR = 2027
DESTINATION = "egypt"

SEASON_QUERIES: tuple[str, ...] = tuple(
    f"?m={month}/{SEASON_YEAR}" for month in SEASON_MONTHS
)
"""Month selectors for a vessel page: ``?m=5/2027`` through ``?m=8/2027``.

Without one, a boat page returns whatever window it defaults to, starting from
today: a full run scraped 746 departures spanning 2026-09 to 2027-10 and kept
just 14.

The first attempt asked only for the season's opening month, on the assumption
that each page returns events running forward from it. It does not — a live run
came back with 250 departures, every single one in May. The selector means that
month and no other, so covering the season means asking four times per vessel.

That is four times the requests, which is why the crawl is capped on vessels
rather than on pages and the job is given room to finish.
"""

SEASON_QUERY = SEASON_QUERIES[0]
"""The season's opening month, for tools that need a single representative URL."""


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


# Paging deliberately not implemented. A live probe showed ?page=2 returning
# byte-identical content to page 1, "pageCount":0 in the markup, and 100 boat
# links already present on the first response against a title of "79 Egypt
# liveaboards". The site's "Next" button pages the rendered view in the
# browser; the server sends every result at once. One fetch per month is the
# whole month.

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
        # Regions and dive sites seen in the search page's own destination nav.
        "red-sea", "thistlegorm", "ras-mohammed", "the-brothers",
        "straits-of-tiran", "abu-nuhas", "daedalus", "elphinstone", "st-johns",
        "abu-dabab", "brothers-islands", "daedalus-reef", "fury-shoal", "tiran",
        "sharm-el-sheikh", "hurghada", "marsa-alam", "port-ghalib", "safaga",
        # Found by a full 79-vessel run: these reached the fee collector and
        # came back as vessels it had failed on, which is a different thing
        # from a boat whose disclosure could not be read.
        "gordon-reef", "jackson-reef", "woodhouse-reef", "shark-and-yolanda",
        "salem-express", "hamata", "sinai", "rocky", "dahab",
        # Site furniture.
        "liveaboards", "reviews", "deals",
    }
)
"""Segments under ``/diving/egypt/`` that are places, not vessels.

The search page links roughly twenty of these from its destination nav, and
they take the same URL shape as a boat. Skipping them by name saves a wasted
fetch each; the real guarantee is downstream, where a page with no ``Event``
nodes yields no departures and says so. This list is an optimisation, not a
correctness boundary — an unknown dive site costs one request, not a bad price.
"""


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
        """One search page per season month, then the fallback listings."""
        for path in search_paths() + DESTINATION_PATHS:
            url = f"https://{self.host}{path}"
            try:
                listing = self.fetcher.get(url)
            except Exception as exc:  # noqa: BLE001 - a dead listing must not end the run
                # Reported rather than swallowed: a silently skipped listing is
                # indistinguishable from a site with nothing to sell.
                self.note(f"listing unavailable {url}: {exc}")
                continue

            count = len(self.boat_links(listing.body))
            if not count:
                self.note(f"no boat links matched on {url}")
            else:
                self.note(f"{path}: {count} boats")
            yield url

    def discover(self) -> Iterator[str]:
        """Crawl the month searches, then each boat page they link to."""
        seen: set[str] = set()

        for listing_url in self._listing_urls():
            yield listing_url
            # Already cached by the fetcher, so re-reading costs nothing.
            listing = self.fetcher.get(listing_url)
            for link in sorted(self.boat_links(listing.body)):
                if link in seen:
                    continue
                seen.add(link)
                if len(seen) > self.max_pages:
                    self.note(f"stopped at {self.max_pages} vessels; more were available")
                    return
                # One request per season month: the selector returns that month
                # alone, so a single fetch would publish a May-only season.
                for query in SEASON_QUERIES:
                    yield f"https://{self.host}{link}{query}"

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
        path = urlparse(result.url).path
        slug = path.rstrip("/").rsplit("/", 1)[-1]

        # Listing pages are crawled for their links, not their content. They
        # carry no structured data, and reporting that as a parse failure buries
        # the real failures in noise.
        if path.startswith(f"/diving/search/") or path.rstrip("/") in {
            f"/diving/{DESTINATION}",
            *(p.rstrip("/") for p in DESTINATION_PATHS),
        }:
            return output

        events = jsonld.of_type(result.body, "Event", "TouristTrip", "Trip")
        products = jsonld.of_type(result.body, "Product")

        if not events and not products:
            raise ScrapeError(
                f"no JSON-LD Event or Product node in {result.url}; "
                f"inspect the snapshot ({result.digest}) and add a markup parser"
            )

        if products:
            product = products[0]
            # Fees are a property of the vessel, not the sailing: the same
            # extras apply whichever month you book. So they are read once per
            # boat page and attached to every itinerary it yields.
            extras = parse_extras(_page_text(result.body))
            output.itineraries.append(
                {
                    "id": slug,
                    "name": product.get("name"),
                    "boat": product.get("name"),
                    "summary": product.get("description"),
                    "source_url": result.url,
                    "provenance": self.provenance(result.url),
                    "fees": to_fee_dicts(extras, self.provenance(result.url)),
                }
            )
            if not extras:
                output.warnings.append(f"{result.url}: no Required/Optional Extras block found")

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
            "boat_slug": slug,
            "name": node.get("name"),
            "start": start,
            "end": end,
            "price": {"amount": amount, "currency": str(currency).upper()},
            "availability": offer.get("availability"),
            "booking_url": offer.get("url") or node.get("url"),
            "location": _place_name(node.get("location")),
            "provenance": self.provenance(result.url),
        }


SCRIPT_OR_STYLE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.I | re.S)
BLOCK_END = re.compile(r"</(li|p|td|tr|div|h[1-6]|ul|ol|dd|dt)\s*>|<br\s*/?>", re.I)
TAG = re.compile(r"<[^>]+>")


def _page_text(html: str) -> str:
    """Flatten markup to readable text.

    The extras block is read from text rather than from selectors on purpose.
    A probe that looked only at leaf elements missed it entirely, because
    "Environment Tax (€45)" is split across an anchor and a span — the labels
    and their amounts live in different nodes. Flattening puts them back
    together and survives a redesign that moves them around again.
    """
    without_code = SCRIPT_OR_STYLE.sub(" ", html)
    # Block boundaries become commas before the tags go. The extras are usually
    # list items, and flattening them without a separator runs "Port Fees (€35)"
    # straight into the next entry, destroying exactly the boundaries the parser
    # needs. Prose renders the same list comma-separated anyway.
    delimited = BLOCK_END.sub(", ", without_code)
    text = TAG.sub(" ", delimited)
    text = (
        text.replace("&euro;", "€")
        .replace("&pound;", "£")
        .replace("&nbsp;", " ")
        .replace("&amp;", "&")
    )
    return re.sub(r"\s+", " ", text)


def _place_name(location: Any) -> str | None:
    """The departure port, from an Event's ``location``.

    Each Event carries a Place with a PostalAddress. The town is the useful
    part — Hurghada, Port Ghalib, Marsa Alam — because it is what a diver books
    flights against.
    """
    if isinstance(location, list):
        location = location[0] if location else None
    if not isinstance(location, dict):
        return None
    address = location.get("address")
    if isinstance(address, dict):
        for key in ("addressLocality", "addressRegion", "streetAddress"):
            value = address.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    name = location.get("name")
    return name.strip() if isinstance(name, str) and name.strip() else None


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
