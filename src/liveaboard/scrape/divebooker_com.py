"""divebooker.com — a third source, read from the JSON-LD its pages serve.

Everything here rests on what `tools/probe_divebooker.py` and
`tools/probe_divebooker_departures.py` read from a runner on 2026-09-20, and
`docs/sources/divebooker.com.md` is the map. The two facts that shape the
module:

**A vessel page is one request for a boat's whole season.** No month selector,
no browser, no endpoint to find — 219 `Event` nodes over three Egyptian hulls,
every one of them stating `name`, `startDate`, `endDate` and `location`, with
`price`, `priceCurrency` and `availability` on every `Offer`.

**The page states each sailing twice**, and only one of the two copies is
priced from the trip's side:

* ``Offer`` → ``itemOffered`` → ``TouristTrip`` → ``subjectOf`` → ``Event``.
  Discovery II has 49 of these, Bella 2 has 3 — which is every sailing each
  boat sells.
and a date carries **more than one offer** often enough to matter: Red Sea
Aggressor IV states 161 offers over 143 sailings. :func:`departures` keeps the
cheapest of them, which is the rule the advertised price already follows on
liveaboard.com — the bottom of the ladder is the price a reader can buy at —
and records how many there were.

* a top-level ``Event`` carrying ``offers``, and also ``url``, ``id``,
  ``duration``, ``organizer``, ``eventStatus``, ``description`` and ``image``.
  Ten on each of the two larger boats and three on Bella 2, which sells three.

**`AggregateOffer` is not a fare and is deliberately unread.** Bella 2 states
`lowPrice 143, highPrice 144, offerCount 3` in EUR beside three sailings at
576, 576 and 579 — which is each fare over *four*, on a trip of three nights.
So it is a per-day rate on days aboard rather than nights, the same
denominator `FeeBasis.PER_DAY` means, and reading it as a trip price would
quarter every fare on the page. A figure whose unit the source does not state
is a figure this project does not total.

A parser that collects ``@type == Event`` therefore gets every sailing twice,
one copy unpriced. :func:`departures` reads both and folds them on the start
date — the same key `promote` merges the other two sources on, because a date
has no spelling — so the second copy adds the booking URL rather than a
phantom sailing. Where the two state different money it keeps the trip
offer's and **says so**: this project does not pick a number quietly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterator

from . import jsonld

HOST = "divebooker.com"
SOURCE_ID = "divebooker.com"

#: Its flat namespace, typed by the two letters before the id. `haz` is a hull;
#: `daz` a country, `baz` a dive site, `eaz` a port, `jaz` an operator. Counted
#: over all 5,715 sitemap URLs rather than inferred from a handful.
HULL_HREF = re.compile(r'href="(?:https://[^/"]+)?(/(?P<slug>[a-z0-9-]+)-(?P<id>haz\d+))"')

ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _types(node: Any) -> list[str]:
    raw = node.get("@type") if isinstance(node, dict) else None
    return [t for t in (raw if isinstance(raw, list) else [raw]) if isinstance(t, str)]


def _is(node: Any, name: str) -> bool:
    return isinstance(node, dict) and name in _types(node)


def _first(value: Any) -> Any:
    """The first of a field that may be a node, a list of them, or nothing."""
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict):
        return _text(value.get("name"))
    return None


def _money(offer: dict[str, Any]) -> tuple[float | None, str | None]:
    """The price and currency an ``Offer`` states, or ``(None, None)``.

    A price that will not parse is not a price. Nothing here guesses a
    currency from a figure or a figure from a currency: a fare with one half
    missing is a fare this source did not state.
    """
    raw, currency = offer.get("price"), offer.get("priceCurrency")
    if not isinstance(currency, str) or not currency.strip():
        return None, None
    try:
        amount = float(str(raw).replace(",", ""))
    except (TypeError, ValueError):
        return None, None
    return (amount, currency.strip()) if amount > 0 else (None, None)


def _availability(offer: dict[str, Any]) -> str | None:
    """``InStock`` from ``https://schema.org/InStock``, and nothing invented.

    It is a **state, not a count**: every offer read states `InStock`, and
    nothing in 219 departures states how many berths are left. Kept as the
    source's own word so that nothing downstream can mistake it for a number.
    """
    value = offer.get("availability")
    return value.rsplit("/", 1)[-1] if isinstance(value, str) and value else None


def _nights(start: str, end: str) -> int | None:
    """Nights between two stated dates, or ``None`` if either will not parse.

    Never derived from a trip's title or its name: the two dates are what the
    source states, and a length inferred from prose is a length this project
    would have to defend.
    """
    if not (ISO_DATE.match(start) and ISO_DATE.match(end)):
        return None
    span = (date.fromisoformat(end) - date.fromisoformat(start)).days
    return span if span > 0 else None


@dataclass(slots=True)
class Departure:
    """One sailing as this source states it."""

    start: str
    end: str | None = None
    trip: str | None = None
    price: float | None = None
    currency: str | None = None
    availability: str | None = None
    #: How many offers the page stated for this date. More than one is
    #: ordinary — Red Sea Aggressor IV states 161 offers over 143 sailings —
    #: and the book keeps the cheapest, so the count is what says a choice
    #: was made.
    offers: int = 1
    url: str | None = None
    event_id: str | None = None
    #: Which of the page's two statements of this sailing were read. Kept
    #: because the counts are the evidence for the folding rule above, and a
    #: rule whose evidence is not in the data is a rule nobody can re-check.
    stated_by: list[str] = field(default_factory=list)

    @property
    def nights(self) -> int | None:
        return _nights(self.start, self.end) if self.end else None

    def as_dict(self) -> dict[str, Any]:
        """What the book keeps, which is less than what the page states.

        `url` is the vessel page on every row — the site links a sailing by a
        fragment, not a path — so writing it per departure is one constant
        repeated 977 times. It lives on the vessel instead. `event_id` is that
        fragment and goes the same way: nothing downstream keys on it, and a
        field kept in case is a field nobody maintains.

        `stated_by` is evidence for the folding rule rather than a fact about
        the sailing, so the vessel carries the counts and the row does not.
        """
        out: dict[str, Any] = {"start": self.start}
        for key in ("end", "trip", "price", "currency", "availability"):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        if self.nights is not None:
            out["nights"] = self.nights
        if self.offers > 1:
            out["offers"] = self.offers
        return out


@dataclass(slots=True)
class VesselBook:
    """What one vessel page said about itself."""

    slug: str
    divebooker_id: str | None = None
    name: str | None = None
    country: str | None = None
    departures: list[Departure] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"slug": self.slug}
        for key in ("divebooker_id", "name", "country"):
            if getattr(self, key):
                out[key] = getattr(self, key)
        # How the page stated this boat's sailings, counted. The fold is the
        # one rule this parser exists for, so the evidence for it travels with
        # the data rather than living only in a commit message.
        stated: dict[str, int] = {}
        for row in self.departures:
            stated["+".join(sorted(row.stated_by)) or "neither"] = (
                stated.get("+".join(sorted(row.stated_by)) or "neither", 0) + 1)
        if stated:
            out["stated_by"] = stated
        return out


def hull_links(html: str) -> list[str]:
    """Every ``/{slug}-haz{id}`` path the page links, in order, deduplicated.

    Discovery is the site's own links rather than a pattern anybody typed.
    The country page is the entry point because the sitemap knows all 516
    hulls worldwide and does not say which sea any of them is in.
    """
    return list(dict.fromkeys(m.group(1) for m in HULL_HREF.finditer(html)))


def split_slug(path: str) -> tuple[str, str | None]:
    """``/bella-2-haz432`` -> ``("bella-2", "haz432")``."""
    match = HULL_HREF.search(f'href="{path}"')
    return (match.group("slug"), match.group("id")) if match else (path.strip("/"), None)


def _trip_offers(html: str) -> Iterator[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]:
    """``(offer, trip, event)`` for every Offer stating what it is an offer for."""
    for node in jsonld.walk_documents(html):
        if not _is(node, "Offer"):
            continue
        trip = _first(node.get("itemOffered"))
        if not _is(trip, "TouristTrip"):
            continue
        event = _first(trip.get("subjectOf"))
        if _is(event, "Event"):
            yield node, trip, event


def _event_departures(html: str) -> Iterator[tuple[dict[str, Any], dict[str, Any] | None]]:
    """``(event, offer)`` for every Event that carries its own offer.

    These are the ten-odd a page states in full, with a booking URL. Ordinary
    Events nested under a trip carry no ``offers`` and are excluded here so
    they cannot arrive twice.
    """
    for node in jsonld.walk_documents(html):
        if _is(node, "Event") and node.get("offers"):
            offer = _first(node.get("offers"))
            yield node, offer if isinstance(offer, dict) else None


def departures(html: str) -> tuple[list[Departure], list[str]]:
    """Every sailing the page states, folded on its start date.

    Returns the departures and whatever the fold could not settle. A
    disagreement is reported rather than resolved: two statements of one
    sailing differing on the money is the page contradicting itself, and
    choosing quietly is how a site starts lying.
    """
    found: dict[str, Departure] = {}
    warnings: list[str] = []

    for offer, trip, event in _trip_offers(html):
        start = _text(event.get("startDate"))
        if not start or not ISO_DATE.match(start):
            continue
        amount, currency = _money(offer)
        fresh = start not in found
        row = found.setdefault(start, Departure(start=start))
        row.end = row.end or _text(event.get("endDate"))
        row.trip = row.trip or _text(trip.get("name")) or _text(offer.get("name"))
        row.currency = row.currency or currency
        row.availability = row.availability or _availability(offer)
        if not fresh:
            row.offers += 1
        # **The cheapest, not the first.** A date carries more than one offer
        # often enough to matter — Red Sea Aggressor IV states 161 offers over
        # 143 sailings — and taking whichever the page printed first put
        # 5,398 USD on 2027-07-24 beside 2,699 for the same week on the same
        # trip, which read as this source disagreeing with the other two by a
        # factor of two. It is the same rule as `the advertised price is the
        # bottom of a cabin ladder`: where a seller offers one sailing at
        # several prices, the lowest is the one a reader can buy at.
        #
        # Only within one currency. Two currencies on one date is not a
        # cheaper berth, it is the same berth quoted twice, and picking the
        # smaller number would pick the currency rather than the price.
        if amount is not None and (
            row.price is None
            or (currency == row.currency and amount < row.price)
        ):
            row.price, row.currency = amount, currency
        if "trip" not in row.stated_by:
            row.stated_by.append("trip")

    for event, offer in _event_departures(html):
        start = _text(event.get("startDate"))
        if not start or not ISO_DATE.match(start):
            continue
        row = found.get(start)
        if row is None:
            row = found.setdefault(start, Departure(start=start))
            row.end = _text(event.get("endDate"))
            row.trip = _text(event.get("name"))
        if "event" not in row.stated_by:
            row.stated_by.append("event")
        row.url = row.url or _text(event.get("url"))
        row.event_id = row.event_id or _text(event.get("id"))
        if offer is None:
            continue
        amount, currency = _money(offer)
        if amount is None:
            continue
        if row.price is None:
            row.price, row.currency = amount, currency
            row.availability = row.availability or _availability(offer)
        elif (amount, currency) != (row.price, row.currency):
            # Deliberately not resolved. The trip offer is kept because it is
            # the copy that exists for every sailing, and the disagreement is
            # published as a warning so a run can be read rather than trusted.
            warnings.append(
                f"{start}: the trip offer states {row.price} {row.currency} "
                f"and the event offer {amount} {currency}; kept the trip's"
            )

    return [found[key] for key in sorted(found)], warnings


def vessel(html: str, path: str) -> VesselBook:
    """Parse one vessel page into a book.

    **This source states no operator, and the parser must not invent one.**
    The obvious candidate is `Product.brand`, which is where PADI states the
    company — here it reads `{"@type": "Brand", "name": "Divebooker.com"}`,
    the seller. The next candidate is `Event.organizer`, and on Bella 2 that
    is `{"@type": "Organization", "name": "Bella 2"}` — the hull. Neither
    names a company, so nothing here does. The operator this site publishes
    goes on coming from the vessel's own page on liveaboard.com.

    Caught by a fixture rather than by reasoning: the first version of this
    function read `Product.brand` and would have published *Divebooker.com*
    as the operator of every Egyptian boat it read.

    The name is the hull as the page's own `organizer` gives it — `Bella 2`
    rather than `Product.name`'s *Bella 2 Liveaboard, Egypt*, which is a
    page title with the country appended.
    """
    slug, hull_id = split_slug(path)
    book = VesselBook(slug=slug, divebooker_id=hull_id)

    # Two passes rather than one, because the fallback must not win by
    # arriving first: the Product node is early in the document and the
    # Events carrying an organizer are late, so a single pass that took
    # whichever came first took the page title every time.
    organizer = product = None
    for node in jsonld.walk_documents(html):
        if _is(node, "Event"):
            book.country = book.country or _text(node.get("location"))
            organizer = organizer or _text(_first(node.get("organizer")))
        elif _is(node, "Product"):
            product = product or _text(node.get("name"))
    book.name = organizer or product

    book.departures, book.warnings = departures(html)
    if not book.departures:
        # A vessel selling nothing and a page that failed are different
        # answers, and only the caller knows which it asked for. Said here so
        # the run reports it either way.
        book.warnings.append(f"{path}: no departure stated")
    return book
