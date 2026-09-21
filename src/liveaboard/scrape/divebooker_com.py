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

import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Iterable, Iterator

from . import jsonld
from ..taxonomy import FeeBasis, FeeCode, FeeTier
from .fees import (
    CURRENCIES,
    ParsedFee,
    _number,
    _tier_for,
    classify_label,
    tier_for_inclusion,
    to_fee_dicts,
)

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

    It is a **state, not a count**: nothing here says how many berths are left.
    Kept as the source's own word so nothing downstream can mistake it for a
    number.

    This used to say *every offer read states `InStock`*, which was true of the
    offers being read and false of the page: the trip's offer says it for every
    sailing that trip sells, and the sailing's own Event node says
    `LimitedAvailability` and `OnlineOnly` in the same fixture. See the reading
    order in `departures`.
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
    fees: dict[str, tuple[list[ParsedFee], bool]] = field(default_factory=dict)
    """The *Price details* panel, **keyed on the trip and its length**.

    Never on the boat, and never on the name alone — see `fee_key`. Red Sea
    Aggressor IV sells one trip name at two lengths with two different bills,
    so a key of the name is a key that says two bills are one.

    Measured over all 92 hulls before it was written (run 35545965933): 603 of
    605 blocks carry a title that is one of the page's own trip names exactly,
    once the panel's `(7 nights) (A-B)` suffix is off. The tempting fallback --
    fold the lot onto the vessel where its blocks agree -- is wrong on 25 of
    the 67 hulls that have blocks, whose trips state *different* surcharges, so
    it would publish one week's bill on another's row.

    The two that match nothing stay out. An unattached fee book is a fee book
    nobody can put a price beside, and guessing which trip it belongs to is
    the failure `promote.itinerary_key` already cost this project once.
    """
    unnamed_fees: list[str] = field(default_factory=list)
    """Priced fee lines this project's vocabulary declined, verbatim."""
    unpriced: dict[str, int] = field(default_factory=dict)
    """Sailings stating no fare, counted by the seller's own reason.

    `_departure_book` drops an unpriced sailing silently, which is the right
    thing to publish and the wrong thing to say nothing about: 46 of 887 in
    one season, and until they were read by hand nobody could tell a charter
    slot from a fare this parser had missed. 42 are *Route on Request
    (Available for groups and charters)* and 4 are sold out.

    The key that matters is `unexplained`. A sailing with no price, no charter
    wording and no sold-out flag is the only one of these worth a person's
    time, and a count that lumps all three together hides it.
    """
    trips: dict[str, dict[str, Any]] = field(default_factory=dict)
    """What each trip states about itself: dives, entry bar, reefs, harbours.

    Keyed by `fee_key` exactly as `fees` is, and kept only for trips this page
    really sells -- a panel naming a week the page does not list is as
    unattachable here as it is there. It is dropped alongside the fee book
    where two panels collide, because a dive count read off a panel this code
    cannot attach is a claim about a trip it cannot identify.
    """

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
        if self.fees:
            # No provenance per line: this whole file is one seller's reading
            # on one day, and the header says so once. The same reasoning
            # keeps the booking URL off every departure.
            #
            # `lines` and `complete` in the shape `promote._padi_fees` already
            # unwraps, so the two sellers' books arrive the same way: a partial
            # disclosure produces no total on either side, and one shape means
            # one rule rather than two that drift.
            out["fees"] = {
                trip: {"lines": to_fee_dicts(lines), "complete": complete}
                for trip, (lines, complete) in sorted(self.fees.items())
            }
        if self.trips:
            # What each trip states about itself beside its panel. Written
            # under its own key rather than folded into `fees`, because a fee
            # book and a dive count are different claims and a reader of this
            # file should not have to open one to find the other.
            out["trips"] = {trip: dict(facts)
                            for trip, facts in sorted(self.trips.items())}
        if self.unpriced:
            # In the book as well as the log: a reader of this file asking why
            # a boat shows fewer sailings than its page lists gets the answer
            # from the file rather than from a run that has scrolled away.
            out["unpriced"] = dict(sorted(self.unpriced.items()))
        if self.unnamed_fees:
            # Named rather than counted, because what an unread charge needs
            # is a word added to `fees.LABEL_PATTERNS` and a count cannot say
            # which word.
            out["unnamed_fees"] = sorted(set(self.unnamed_fees))
        return out


#: A hull id wherever it appears — in an href, in the streamed payload, in a
#: JSON string. The search page renders twenty links and states seventy-five,
#: so what it *links* and what it *holds* are different questions.
HULL_ANYWHERE = re.compile(r"/?([a-z0-9][a-z0-9-]*?)-(haz\d+)\b")

#: The site's own pagination, whatever it is called. Followed rather than
#: guessed: a page parameter somebody typed is the mistake this module has
#: already made once with a hull id.
SEARCH_LINK = re.compile(r'href="(?:https://[^/"]+)?(/boatsearch\?[^"]+)"')


#: The currency the page says it rendered in, in the payload it streams to
#: itself. Tolerant of the escaping, because the RSC chunks arrive as JSON
#: string literals inside `self.__next_f.push([1,"…"])` and the quotes are
#: backslashed there; on a page that carries it plainly the same pattern
#: matches.
PAGE_CURRENCY = re.compile(
    r'\\?"currencies\\?"\s*:\s*\{\\?"current\\?"\s*:\s*\\?"([A-Z]{3})\\?"')


def page_currency(html: str) -> str | None:
    """What the page states it is priced in, or ``None`` if it does not say.

    **`Offer.priceCurrency` is not it.** Measured 2026-09-20 over four hulls:
    Seawolf Steel, Unity and Iceberg label every offer `EUR` while their own
    payload says `{"currencies":{"current":"USD"…}}`, and USD is the only
    currency code anywhere in those bytes. Red Sea Aggressor IV labels them
    `USD` and says the same. The label is static per vessel; the payload is
    what the numbers follow.

    The other two sellers agree, which is how this was noticed rather than how
    it is decided: 645 of 777 joined sailings carry the same number as a
    figure liveaboard.com or PADI states in **dollars**, and where
    liveaboard.com itself quotes euros the divebooker figure is 1.148x it —
    1/0.8708, which is the rate this very payload states for currency id 2.

    Asking for another currency changes nothing: `?currency=EUR`,
    `?currency=USD` and `?cur=USD` all came back with the same numbers, the
    same labels and `current: USD`.
    """
    found = PAGE_CURRENCY.search(html)
    return found.group(1) if found else None


def hull_slugs(html: str) -> dict[str, str]:
    """Every `{slug: id}` the page mentions, linked or not.

    `hull_links` reads anchors, which is right for a country page and wrong
    for a search: the search renders twenty anchors and its streamed payload
    carries the rest of what it knows. Both are the same bytes to a plain GET,
    so the ids are there to be read either way.
    """
    found: dict[str, str] = {}
    for slug, hull in HULL_ANYWHERE.findall(html):
        found.setdefault(slug, hull)
    return found


def search_pages(html: str) -> list[str]:
    """Every `/boatsearch?…` the page links, deduplicated, in order."""
    return list(dict.fromkeys(SEARCH_LINK.findall(html)))


#: The search URL is the site's own, copied out of a link it renders:
#: an entity type, the Egypt id the country page already carries in its slug
#: (`egypt-daz3881`), and a year-month.
SEARCH_PATH = "/boatsearch?et={et}&e={entity}&ym={ym}"
EGYPT = "3881"
ENTITY_TYPE = "2"

#: The published season, the dataset's window, in ISO dates. `padi_com.SEASON`
#: is the same pair; the months below are the same window in the search's own
#: vocabulary.
SEASON: tuple[str, str] = ("2027-05-01", "2027-08-31")

#: The months the published season covers, in this seller's vocabulary.
#: `padi_com.SEASON` and `liveaboard_com.SEASON_MONTHS` say the same thing in
#: theirs — each source is asked in the words it answers in, and a shared
#: constant would have to be translated three times anyway.
SEASON_YM: tuple[str, ...] = ("202705", "202706", "202707", "202708")

#: The site's own pagination, **measured rather than typed** (2026-09-20).
#: `/boatsearch?…&p=2` returns twenty hulls the first page does not, among them
#: Aphrodite, Blue Pearl and Blue Seas. `page=2`, `pg=2`, `offset=20`,
#: `start=20`, `skip=20`, `limit=100`, `perPage=100`, `size=100` and `take=100`
#: each return the first twenty again — no error, no hint, just the same page,
#: which is why this was tried against a known answer instead of assumed. The
#: search renders no numbered links and the payload carries no endpoint
#: literal and no server action id, so there was nothing to follow.
PAGE_PARAM = "p"


def search_path(ym: str, page: int = 1, entity: str = EGYPT,
                et: str = ENTITY_TYPE) -> str:
    """``/boatsearch?…`` for one month, one page.

    Page one omits the parameter, so the first request of every month is the
    URL a visitor gets.
    """
    path = SEARCH_PATH.format(et=et, entity=entity, ym=ym)
    return path if page <= 1 else f"{path}&{PAGE_PARAM}={page}"


def hull_links(html: str) -> list[str]:
    """Every ``/{slug}-haz{id}`` path the page links, in order, deduplicated.

    Discovery is the site's own links rather than a pattern anybody typed.
    The country page is the entry point because the sitemap knows all 516
    hulls worldwide and does not say which sea any of them is in.
    """
    return list(dict.fromkeys(m.group(1) for m in HULL_HREF.finditer(html)))


MAX_PAGES = 12
"""How deep one month's search is walked before the walk says so and stops.

Twenty a page against a stated 75 is four pages, so this is loose. It is not
a belief about the fleet's size: a paginator that stops changing is caught by
the repeat rule in :func:`walk_search`, and this bounds only a paginator that
never repeats — which would be the site answering something other than the
question asked.
"""


def walk_search(fetch: Callable[[str], str | None],
                months: Iterable[str], entity: str = EGYPT,
                max_pages: int = MAX_PAGES) -> tuple[list[str], list[str]]:
    """Every hull the search links across those months, and what the walk saw.

    `fetch(path)` returns the page's bytes or ``None`` for a page that could
    not be read; the caller owns the transport, so this is testable without
    one.

    **Two ways a walk ends, and neither of them is a page number.** An empty
    page is the site saying there is no more. A page repeating one this month
    has already shown is the site ignoring `p=` — which is what nine other
    spellings of it do, silently, so it is the shape to expect rather than a
    surprise. A page that cannot be read ends that month and says so, because
    a walk that carried on would report the rest of the month as absent.
    """
    found: list[str] = []
    seen: set[str] = set()
    notes: list[str] = []
    for ym in months:
        shown: list[frozenset[str]] = []
        for page in range(1, max_pages + 1):
            body = fetch(search_path(ym, page, entity=entity))
            if body is None:
                notes.append(f"{ym} p{page}: unread, so this run knows nothing "
                             f"about the rest of that month")
                break
            linked = hull_links(body)
            here = frozenset(linked)
            fresh = [path for path in linked if path not in seen]
            notes.append(f"{ym} p{page}: {len(linked)} linked, {len(fresh)} new")
            found.extend(fresh)
            seen.update(linked)
            if not linked or here in shown:
                break
            shown.append(here)
        else:
            notes.append(f"{ym}: still finding hulls at page {max_pages} — "
                         f"stopped there, and this run does not claim the "
                         f"month is complete")
    return found, notes


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

    **The currency is the page's, not the offer's**, wherever the page states
    one: see :func:`page_currency`. Three of four hulls read label every offer
    `EUR` on a page whose own payload says it rendered in USD, so a book that
    believed the label carried a dollar figure under a euro name — and a euro
    name is what a total would convert. Where the page says nothing the label
    stands, because then it is the only thing that was said.
    """
    found: dict[str, Departure] = {}
    warnings: list[str] = []
    page = page_currency(html)
    mislabelled: set[str] = set()

    def money(offer: dict[str, Any]) -> tuple[float | None, str | None]:
        amount, label = _money(offer)
        if amount is None or page is None:
            return amount, label
        if label and label != page:
            mislabelled.add(label)
        return amount, page

    for offer, trip, event in _trip_offers(html):
        start = _text(event.get("startDate"))
        if not start or not ISO_DATE.match(start):
            continue
        amount, currency = money(offer)
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

        # **Whether a berth can be bought is the sailing's claim, not its
        # trip's.** Both nodes state `availability` and they do not agree: the
        # trip's offer is one copy covering every sailing that trip sells and
        # says `InStock` throughout, while the Event is one sailing. Bella 2's
        # three say `LimitedAvailability`, `OnlineOnly` and `OnlineOnly` in the
        # fixture this parser was written against.
        #
        # This used to sit inside the `row.price is None` branch behind an
        # `or`, so the trip pass -- which runs first -- answered every row and
        # the sailing's own word was read by nothing. The committed book stated
        # `InStock` on **888 of 888** departures: a field with one value on
        # every row is a field carrying no information, and this is the field
        # that says whether the trip is on sale at all. The vessel page marks
        # sailings SOLD OUT and none of that reached us.
        #
        # Still a fallback rather than a replacement, in that direction only:
        # an Event stating nothing leaves the trip's answer standing, because a
        # silence is not a contradiction.
        stated = _availability(offer)
        if stated:
            row.availability = stated

        amount, currency = money(offer)
        if amount is None:
            continue
        if row.price is None:
            row.price, row.currency = amount, currency
        elif (amount, currency) != (row.price, row.currency):
            # Deliberately not resolved. The trip offer is kept because it is
            # the copy that exists for every sailing, and the disagreement is
            # published as a warning so a run can be read rather than trusted.
            warnings.append(
                f"{start}: the trip offer states {row.price} {row.currency} "
                f"and the event offer {amount} {currency}; kept the trip's"
            )

    if mislabelled:
        # Counted rather than silent, and stated the way round it matters: the
        # figure is the page's, the label was somebody else's.
        warnings.append(
            f"the page states {page} and its offers are labelled "
            f"{', '.join(sorted(mislabelled))}; read as {page}")

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

    # The fee panel, attached by the trip it sits in. A block naming a trip the
    # page does not sell is kept out and said out loud: this source states the
    # panel per programme and the departures per sailing, and a key that stops
    # matching fails silently.
    blocks, fee_warnings = fee_blocks(html)
    book.warnings.extend(f"{path}: {note}" for note in fee_warnings)
    sold = {row.trip for row in book.departures if row.trip}
    # What each key has already been given, so a second panel under one key is
    # noticed rather than silently preferred. See `fee_key`.
    seen: dict[str, tuple[list[dict[str, Any]], bool]] = {}
    refused: set[str] = set()
    for block in blocks:
        book.unnamed_fees.extend(block.unnamed)
        if not (block.trip and block.trip in sold):
            if block.fees:
                book.warnings.append(
                    f"{path}: a price panel names {block.trip!r}, which is not "
                    f"a trip this page sells; its {len(block.fees)} fee "
                    f"line(s) are unattached")
            continue

        # The dive count and the entry bar are kept whether or not the panel
        # priced anything. They are separate claims about the trip and a page
        # that states no surcharge has not thereby stopped stating them --
        # which is the same distinction `fees_known` draws one layer up.
        facts = {k: v for k, v in (
            ("dives", block.dives),
            ("requirements", block.requirements),
            ("certification", block.certification),
            ("sites", block.sites or None),
            ("port_from", block.port_from),
            ("port_to", block.port_to),
        ) if v}
        key = fee_key(block.trip, block.nights)

        # **A second panel under one key refuses both.** The page really does
        # repeat a panel — Red Sea Aggressor IV states *St. Johns / Daedalus
        # (7 nights)* twice with byte-identical columns — so a repeat is not by
        # itself a contradiction, and only differing *content* is. Where it
        # differs, nothing here can say which bill belongs to the sailing, and
        # the one thing that must not happen is the last one winning: that is
        # `promote.itinerary_key`'s rule, which this project has already paid
        # for once, and it is why the fee book is dropped rather than picked
        # from.
        #
        # The trip facts go with it. A dive count and an entry bar read off a
        # panel this code cannot attach are claims about a trip it cannot
        # identify.
        shape = (to_fee_dicts(block.fees), block.complete)
        if key in seen and seen[key] != shape:
            if key not in refused:
                refused.add(key)
                book.fees.pop(key, None)
                book.trips.pop(key, None)
                book.warnings.append(
                    f"{path}: two different price panels are filed under "
                    f"{block.trip!r} at {block.nights} night(s); nothing can "
                    f"say which a sailing gets, so both are dropped")
            continue
        if key in refused:
            continue
        seen[key] = shape

        if facts:
            book.trips[key] = facts
        if block.fees:
            book.fees[key] = (block.fees, block.complete)

    # Why each unpriced sailing states no fare, in the seller's own terms.
    # Counted rather than dropped, and split by reason, because "no price"
    # covers a boat offered for charter and a fare nobody read, and only the
    # second is this project's problem.
    for row in book.departures:
        if row.price is not None:
            continue
        book.unpriced[why_unpriced(row.trip, row.availability) or "unexplained"] = (
            book.unpriced.get(
                why_unpriced(row.trip, row.availability) or "unexplained", 0) + 1)

    if not book.departures:
        # A vessel selling nothing and a page that failed are different
        # answers, and only the caller knows which it asked for. Said here so
        # the run reports it either way.
        book.warnings.append(f"{path}: no departure stated")
    return book


# --------------------------------------------------------------------------
# The fee panel
#
# `Trip & price details` on a vessel page, which "no fee book hiding
# client-side" missed because that verdict came from a keyword sweep over the
# payload's key *names* — `fee`, `extra`, `includ`, `exclud` — and this one is
# called `details`. Counted over all 92 hulls before a word of it was parsed
# (run 35533600353): 606 blocks on 75 pages, every one of them titled *Price
# details* and carrying exactly three columns —
#
#     included      5,244 lines,     0 priced
#     notincluded     986 lines,   792 priced   ("Obligatory surcharges")
#     extra         4,276 lines,   303 priced   ("Extra cost")
#
# — with 4,311 of the billed labels already named by `fees.classify_label` and
# 951 declined. That is a fee book, on the fleet's own terms: priced, tiered by
# the seller itself, and in the vocabulary this project already has.
# --------------------------------------------------------------------------

#: The chunks the page streams to itself. Next.js App Router ships its data as
#: JSON **string literals** inside these calls, so the payload's own quotes
#: arrive backslashed and nothing in it can be read without decoding first. A
#: regex written against a pretty-printed scratch file is the reason this
#: module probes before it parses.
FLIGHT = re.compile(r'self\.__next_f\.push\(\[\d+,\s*(".*?")\]\)', re.S)


def payload_parts(html: str) -> tuple[str, int]:
    """The streamed payload, decoded and joined, and how much would not decode.

    The count travels with the text because a chunk that fails to decode is a
    piece of the page nobody read, and this project's oldest rule about that
    is that it must not look like a page with nothing on it.
    """
    parts: list[str] = []
    dropped = 0
    for chunk in FLIGHT.findall(html):
        try:
            parts.append(json.loads(chunk))
        except json.JSONDecodeError:
            dropped += 1
    return "".join(parts), dropped


def payload(html: str) -> str:
    """The streamed payload alone, for a caller that only wants to look."""
    return payload_parts(html)[0]


def balanced(text: str, start: int) -> str | None:
    """The JSON value beginning at `start`, by counting brackets.

    The payload is one enormous line, so a value cannot be read by looking for
    the end of anything — only by matching what opened it. Strings are walked
    through so a brace inside prose does not end the object.
    """
    opener = text[start]
    closer = {"{": "}", "[": "]"}.get(opener)
    if closer is None:
        return None
    depth, index, in_string, escaped = 0, start, False, False
    while index < len(text):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
        index += 1
    return None


def enclosing(text: str, positions: Iterable[int]) -> dict[int, tuple[int, int]]:
    """For each position, the smallest JSON object containing it.

    A fee block is worth nothing without the trip it belongs to, and the
    payload is one line, so the only way to ask *whose* block this is, is to
    find what encloses it. Forward scan with a stack rather than a backwards
    walk, because reading backwards cannot tell a brace inside prose from a
    brace that opened something — and one scan for every position rather than
    one per block, since the payload runs to a megabyte.
    """
    wanted = sorted(positions)
    if not wanted:
        return {}
    best: dict[int, tuple[int, int]] = {}
    stack: list[int] = []
    index, in_string, escaped = 0, False, False
    while index < len(text):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "{":
            stack.append(index)
        elif char == "}" and stack:
            start = stack.pop()
            for position in wanted:
                if start <= position <= index and (
                    position not in best
                    or (index - start) < (best[position][1] - best[position][0])
                ):
                    best[position] = (start, index)
        index += 1
    return best


#: A figure with its currency beside it, either order, as this seller's prose
#: writes them: *125-250 EUR per person*, *10EUR per day*, *50 USD*, *€45*.
#:
#: The currency token must **touch** the number, because the label is whatever
#: sits in front of the first amount and a line naming a figure a sentence away
#: from a currency is prose, not a price. `14% GST applicable to all onboard
#: payments` states a number and no currency, and stays an unpriced line rather
#: than becoming 14 of something — the same rule `padi_com` keeps about reading
#: a whole string or none of it.
FEE_MONEY = re.compile(
    r"(?:(?P<sym>[€$£])\s*(?P<symlow>\d[\d.,]*)"
    r"(?:\s*[-–—]\s*(?P<symhigh>\d[\d.,]*))?"
    r"|(?P<low>\d[\d.,]*)(?:\s*[-–—]\s*(?P<high>\d[\d.,]*))?\s*(?P<code>EUR|USD|GBP)\b)",
    re.I,
)

#: Who pays, which is not how often. Every figure this site publishes is per
#: person, so the phrase says nothing a total needs — and stripping it is what
#: lets the unit beside it be read. *165-240 EUR per person per trip* states
#: both, and a reader that stops at the first `per` reads the payer and calls
#: the period unstated. The fleet census counted 507 lines under "per person"
#: for exactly that reason, which is a number about the probe rather than
#: about the fleet.
FEE_PAYER = re.compile(r"\bper\s+(?:persons?|pax|divers?|guests?)\b", re.I)

#: The unit, in the seller's own words. Ordered, and the order is not a
#: preference: no line in the census states two of these.
FEE_BASES: tuple[tuple[re.Pattern[str], FeeBasis], ...] = tuple(
    (re.compile(pattern, re.I), basis)
    for pattern, basis in (
        (r"\bper\s+nights?\b", FeeBasis.PER_NIGHT),
        (r"\bper\s+days?\b", FeeBasis.PER_DAY),
        (r"\bper\s+dives?\b", FeeBasis.PER_DIVE),
        (r"\bper\s+weeks?\b", FeeBasis.PER_WEEK),
        (r"\bper\s+(?:trips?|safaris?|cruises?|tours?|itinerar(?:y|ies))\b",
         FeeBasis.PER_TRIP),
        # `per item` is one purchase on one trip, which is what `fees.BASES`
        # has always said about the other seller's word for it. Aml Hayaty
        # prices its Open Water course that way and everything else on the
        # same panel per trip.
        (r"\bper\s+items?\b", FeeBasis.PER_TRIP),
    )
)

#: The separator between a label and its money, stripped off the label's tail.
#: Two spellings across the fleet — *Port fees - 50 USD* and *Fuel Surcharge:
#: 10EUR* — and the dash has to be **spaced**, or `Check-dive` loses its head.
#:
#: An opening bracket too, because a third spelling puts the figure inside one:
#: Aml Hayaty writes *Gratuities (€70)*, whose label came out as `Gratuities (`
#: until this was measured. Only a *trailing* one, so the qualifier in *Full
#: Equipment set (Mask, Fins, Snorkel, …): 130.00EUR* survives — that bracket
#: closes before the money and is part of what the operator called the thing.
FEE_SEPARATOR = re.compile(r"(?:\s+[-–—]|[:.,;(])\s*$")

#: The panel's own trip suffix: *Northern Red Sea - Best Wreck Diving
#: (7 nights) (Hurghada-Hurghada)*. The night count is a fact the panel states
#: and is kept; the rest is the same trip name the JSON-LD gives.
TRIP_SUFFIX = re.compile(
    r"\s*\((?P<nights>\d+)\s*nights?\)\s*(?:\([^)]*\))?\s*$", re.I)

#: The columns, and whether the seller says a diver can decline the charge.
#: **The seller's own block decides the tier**, which is the rule `fees._tier_for`
#: was rewritten around: a mandatory tip and a tip you choose the size of are
#: different charges and only the operator can say which is billed.
FEE_COLUMNS = {"included": None, "notincluded": True, "extra": False}


#: What a trip states about itself, beside the panel. Measured 2026-09-21
#: rather than assumed: the object holding `details` also holds `name`,
#: `nights`, `numberDives`, `requirements`, `divesites`, `departurePort` and
#: `arrivalPort`, which is every fact this site takes from the other two
#: sellers. Reading them costs nothing — they arrive in the same object the
#: fee panel is found in.
TRIP_COUNT = re.compile(r"(\d+)")


def _stated_count(value: Any) -> int | None:
    """`"9 dives"` as 9, and anything else as nothing.

    Never derived from a trip's length or its day plan: **a dive count is the
    arithmetic this dataset refuses**, because ten vessels publish one and they
    state 15 to 21 for the same seven-night week. This reads a figure the
    seller wrote and returns `None` where it wrote none.
    """
    if not isinstance(value, str):
        return None
    found = TRIP_COUNT.search(value)
    return int(found.group(1)) if found else None


#: What this source writes in the harbour field when it has no harbour.
#: Literally *"Port is not stated"*, on 4 of the 591 panels that fill the field
#: at all — a sentence about the data sitting where a place name goes, which
#: would have shipped as a *Departs from* chip nobody can sail from and as a
#: row claiming a port it does not have. The fleet's other 1,170 readings are
#: the six harbours this page already names — Hurghada, Port Ghalib, Marsa
#: Alam, Safaga, Sharm El Sheikh, Hamata — so `PORT_ALIASES` needs nothing and
#: this needs one line. Matched whole and case-folded, never as a substring:
#: a rule broad enough to catch "not stated" anywhere would catch a marina
#: whose name happens to contain it.
NO_PORT = frozenset({"port is not stated"})


def _stated_name(node: Any) -> str | None:
    """The harbour inside `{"name": …, "url": …}`, or nothing.

    The `url` beside it is this site's own port page and is dropped: a link is
    not a fact about the trip, and `ALLOWED_EXTERNAL` is empty for the reason
    the page ships nothing external at all.

    A source saying *it does not know* is read as not knowing. `promote` fills
    this field only where both ends are stated, so a placeholder arriving as a
    name is not one bad chip — it is the pair passing the test that was meant
    to stop exactly this.
    """
    if isinstance(node, dict):
        name = node.get("name")
        if isinstance(name, str) and name.strip():
            return None if name.strip().lower() in NO_PORT else name.strip()
    return None


def _stated_text(node: Any) -> str | None:
    """The sentence inside `{"title": …, "text": …}`, or nothing."""
    if isinstance(node, dict):
        text = node.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()
    return None


@dataclass(slots=True)
class FeeBlock:
    """One *Price details* panel, and the trip it was found inside.

    `trip` is the enclosing object's own title with the panel's `(7 nights)
    (A-B)` suffix removed, which is **not** necessarily the name the JSON-LD
    gives the same week: the page keeps two vocabularies for one boat's trips.
    Kept as the seller wrote it and joined by the caller, because a key that
    stops matching fails silently and this project has paid for that once
    already in `promote.itinerary_key`.
    """

    trip: str | None = None
    nights: int | None = None
    fees: list[ParsedFee] = field(default_factory=list)
    complete: bool = False
    """Whether every charge a diver cannot decline is named, priced and scalable.

    The same verdict `padi_com` reaches about its own book and for the same
    reason: **a total built from part of a disclosure is the precise thing this
    site was built to catch other people doing.** Only the *Obligatory
    surcharges* column decides it — neither the optional lines nor the
    inclusions can make it false, because a massage nobody can classify and a
    transfer with no price say nothing about what a diver must pay.

    Three ways it goes false, and the third is this seller's own: a mandatory
    line whose label nothing could name, one with no figure, and one whose unit
    is missing. The last is not a technicality — `FeeItem.span_for_trip`
    refuses such a line outright, so a bill containing one cannot add up, and
    calling it complete would publish a total short by whatever that line is.

    An empty column is complete and empty, which is the seller saying the fare
    covers everything: a disclosure, not a gap.
    """
    dives: int | None = None
    """The dive count this trip states, as a figure and never as a derivation.

    `"9 dives"` on the trip object beside the panel. It is the **last** answer
    this site would take — after the itinerary fragment and after PADI, both of
    which it may not outrank — and for the 33 hulls neither of the other two
    sellers lists it is the only one there is.
    """
    requirements: str | None = None
    certification: str | None = None
    """The entry bar, in the operator's own two sentences.

    `requirements.expirience.text` reads *"Minimum 0 dives"* and
    `requirements.sertification.text` names a certification — the source's own
    spellings, kept because they are the keys it publishes. A stated safety
    requirement is the operator's claim and is never softened, so both travel
    as prose and nothing here hardens advice into a gate.
    """
    sites: list[str] = field(default_factory=list)
    """The reefs this trip names, which is the site filter's raw material."""

    port_from: str | None = None
    port_to: str | None = None
    """The two harbours, as two fields, because the source states them as two.

    `departurePort.name` and `arrivalPort.name`. **A joined string is not a
    record** — PADI's `ports` was one and could not be split back, because two
    of its eight harbour names contain the separator — so nothing here ever
    joins them, not even for a key.

    It is the same claim PADI makes in `port_from`/`port_to` and the same one
    liveaboard.com leaves to be parsed out of a trip title, and it is the only
    statement of it there is for the 33 hulls neither of the others lists: a
    row this seller founded has no title to parse and no second source to ask,
    so without these every one of them reads *Unknown*.
    """

    unnamed: list[str] = field(default_factory=list)
    """Priced lines whose label this project's vocabulary declined, verbatim.

    Named rather than counted. An unrecognised charge is a word missing from
    `fees.LABEL_PATTERNS`, and a number cannot say which word — the census that
    found this panel counted 951 of them and the names are what made *fuel
    charge*, *route suplement* and *port & permission fees* visible as the
    fleet's own spellings of charges the table already holds.
    """


def _fee_lines(text: str) -> Iterator[str]:
    """The lines of one column, as the seller's prose breaks them."""
    for line in re.split(r"[\r\n]+", text or ""):
        line = line.strip(" \t-–—•* ")
        if line:
            yield line


def _read_fee_line(line: str, required: bool | None) -> tuple[ParsedFee | None, str | None]:
    """One prose line as a parsed charge, or as a name nothing could place.

    Returns the charge and, where a **priced** line could not be named, the
    line itself. An unpriced line nothing recognises is ordinary — the
    inclusion column runs to 5,244 lines of *Water*, *Coffee*, *Free WiFi*, and
    an amenity nobody can classify is not a hole in a fee book. A priced one
    is a charge going unread, which is a different thing and is reported.
    """
    money = FEE_MONEY.search(line)
    label = (line[: money.start()] if money else line).strip()
    label = FEE_SEPARATOR.sub("", label).strip()
    if not label:
        # A bare amount with nothing in front of it — the census turned up
        # `$44`, `$43`, `$46` as whole lines. A figure is not a charge.
        return None, None
    code = classify_label(label, prose=False)
    if code is None:
        return None, line if money else None

    included = required is None
    tier = tier_for_inclusion(code) if included else _tier_for(code, bool(required))
    if included or money is None:
        # An inclusion is an answer and carries no figure; a billed line with
        # no figure is a charge of unknown size. `to_fee_dicts` tells the two
        # apart and neither is ever drawn as free.
        return ParsedFee(code=code, label=label, tier=tier, low=None, high=None,
                         currency="EUR", basis=FeeBasis.PER_TRIP,
                         included=included), None

    symbol = money.group("sym")
    low = _number(money.group("symlow") if symbol else money.group("low"))
    high = _number(money.group("symhigh") if symbol else money.group("high"))
    currency = (CURRENCIES[symbol] if symbol
                else (money.group("code") or "EUR").upper())

    tail = FEE_PAYER.sub(" ", line[money.end():])
    basis = next((b for pattern, b in FEE_BASES if pattern.search(tail)), None)
    return ParsedFee(
        code=code, label=label, tier=tier, low=low,
        high=high if high is not None else low, currency=currency,
        basis=basis or FeeBasis.PER_TRIP,
        # No unit stated is no unit read. `per person` alone says who pays and
        # not how often, and `per tank` — five lines on this fleet — states a
        # unit that is real and that this project cannot scale: one fill per
        # dive is the diving world's ordinary assumption and it is still a
        # derivation, and deriving a dive count is the arithmetic this dataset
        # refuses outright, since ten vessels publish one and they state 15 to
        # 21 for the same seven-night week. Either way the figure is kept and
        # marked rather than scaled by a number nobody published or thrown
        # away for want of one. `basis` below is a placeholder that
        # `FeeItem.span_for_trip` never reaches.
        unit_unstated=basis is None,
    ), None


def _rank(fee: ParsedFee) -> int:
    """Which of two readings of one charge the block keeps.

    **A stated amount beats an inclusion, and an inclusion beats a line with no
    amount** — the rule the fee book already keeps where one code covers two
    services. The columns are read in the page's own order, so a tie goes to
    the obligatory line: where the seller states a charge as owed and again as
    an optional extra, what a diver cannot decline is the truer half.
    """
    if fee.has_price:
        return 2
    return 1 if fee.included else 0


DETAILS_AT = re.compile(r'"details"\s*:\s*(?=\{)')


#: A slot the seller offers to charter rather than a week it sells berths on.
#: Its own words, on 42 of the 46 unpriced sailings in the season: *"Route on
#: Request (Available for groups and charters; Please enquire…)"* — Argo Egypt
#: 16, Vita Xplorer 18, Omneia Spirit 7, Independence II 1.
#:
#: **This is why those rows carry no fare, and it is not a gap.** The booking
#: page confirms it from the other side: asked for one of them it returns the
#: right week, 24 or 25 free spaces, and *no cabin option at all* — the boat is
#: empty because nobody is selling seats on it. A sailing that prices nothing
#: is not a fetch that failed, which is the rule `fetch_padi._sailing_counts`
#: already keeps one source over.
#:
#: Matched on the seller's phrase rather than on the absence of a price,
#: because the two are different claims and only one of them is an answer.
ON_REQUEST = re.compile(r"\b(?:route\s+on\s+request|on\s+request)\b"
                        r"|\bfor\s+(?:groups?\s+and\s+)?charters?\b"
                        r"|\bfull\s+charter\b", re.I)


def why_unpriced(trip: str | None, availability: str | None) -> str | None:
    """Why this sailing states no fare, in the seller's terms, or ``None``.

    ``None`` is the one that matters: a sailing with no price, no charter
    wording and no sold-out flag is a fare this reading did not find, and that
    is worth a person's attention. Everything else here is the seller
    answering.
    """
    if trip and ON_REQUEST.search(trip):
        return "on request"
    if availability in ("SoldOut", "OutOfStock"):
        return "sold out"
    return None


def fee_key(trip: str | None, nights: int | None) -> str:
    """How a fee panel is filed, and how a sailing finds it again.

    **The trip name is not identity here.** Red Sea Aggressor IV sells
    *Brothers - Daedalus - Elphinstone* as a 7-night week and as a 9-night one,
    and they are different trips with different bills — 7 nights carries one
    panel, 9 nights another, on all 143 of that boat's sailings. The night
    count is in the owner's own `nights` field and in the name's
    `(9 nights)` suffix, and `TRIP_SUFFIX` strips that suffix before this
    project keys on anything. So the two collapsed onto one key and the page's
    second panel silently overwrote the first, on 142 sailings.

    The same shape as `Itinerary.name` two layers up: *two sailings differing
    only by port are two trips*. Here it is the length rather than the port,
    and a key that drops it is a key that says two bills are one.

    Both sides state the length — the panel's owner writes it and a sailing has
    two dates — so this is an equality on a number, which is the only kind of
    join this module makes.
    """
    return f"{trip}::{nights if nights is not None else ''}"


def fee_blocks(html: str) -> tuple[list[FeeBlock], list[str]]:
    """Every *Price details* panel the vessel page streams, and what it could not read.

    The panel is in the payload rather than in the JSON-LD, so it is read by
    bracket matching: the payload arrives as one enormous line and a value can
    only be found by matching what opened it — a brace inside a sentence about
    marine parks must not end an object.
    """
    text, dropped = payload_parts(html)
    warnings: list[str] = []
    if dropped:
        # A chunk that would not decode is a piece of the page nobody read,
        # and the oldest rule here is that such a page must not be mistaken
        # for one with nothing on it.
        warnings.append(f"{dropped} streamed chunk(s) did not decode")

    # Two positions per panel, and they are not interchangeable. `end()` is the
    # `{` the panel itself opens at, which is what `balanced` reads; the
    # smallest object containing *that* index is the panel, so asking for the
    # owner there hands back `{"title": "Price details"}` — the panel's own
    # heading, on every hull, wearing a trip name's clothes. `start()` sits on
    # the key's opening quote, inside the parent and before the panel begins,
    # so the smallest object containing it is the trip. Caught by a fixture
    # from a real page: this shipped reading `Price details` as the trip and
    # attaching nothing.
    found_at = [(m.start(), m.end()) for m in DETAILS_AT.finditer(text)]
    owners = enclosing(text, [start for start, _ in found_at])
    blocks: list[FeeBlock] = []
    for owner_at, position in found_at:
        chunk = balanced(text, position)
        if chunk is None:
            warnings.append("a price panel was not closed in the payload")
            continue
        try:
            panel = json.loads(chunk)
        except json.JSONDecodeError:
            warnings.append("a price panel did not parse as JSON")
            continue

        block = FeeBlock()
        bounds = owners.get(owner_at)
        if bounds:
            try:
                owner = json.loads(text[bounds[0]:bounds[1] + 1])
            except json.JSONDecodeError:
                owner = {}
            # **`name`, not `title`.** The panel's own heading is `title` and
            # says *Price details* on every hull; the trip that holds it
            # states `name`. Read the wrong one and every block comes back
            # called Price details — or, once the index was right, called
            # nothing at all, because the trip object has no `title` to read.
            # `title` stays as a fallback and has never fired.
            named = owner.get("name") or owner.get("title")
            if isinstance(named, str) and named.strip():
                suffix = TRIP_SUFFIX.search(named)
                block.nights = int(suffix.group("nights")) if suffix else None
                block.trip = TRIP_SUFFIX.sub("", named).strip() or None

            # And everything else the trip states about itself, from the same
            # object and for nothing. Each is read as the seller wrote it and
            # used downstream only where no other source answers.
            block.dives = _stated_count(owner.get("numberDives"))
            bar = owner.get("requirements")
            if isinstance(bar, dict):
                # The source's own spellings, because they are the keys it
                # publishes and a tidied copy would be a second vocabulary.
                block.requirements = _stated_text(bar.get("expirience"))
                block.certification = _stated_text(bar.get("sertification"))
            block.sites = [
                site["name"].strip()
                for site in (owner.get("divesites") or [])
                if isinstance(site, dict) and isinstance(site.get("name"), str)
                and site["name"].strip()
            ]
            block.port_from = _stated_name(owner.get("departurePort"))
            block.port_to = _stated_name(owner.get("arrivalPort"))

        found: dict[FeeCode, ParsedFee] = {}
        unreadable = False
        for column in panel.get("columns") or []:
            if not isinstance(column, dict):
                continue
            required = FEE_COLUMNS.get(str(column.get("type")))
            if required is None and str(column.get("type")) != "included":
                # A fourth column would be a disclosure nobody is reading.
                warnings.append(f"unknown price column {column.get('type')!r}")
                continue
            for line in _fee_lines(column.get("text", "")):
                fee, unread = _read_fee_line(line, required)
                if unread:
                    # Marked with the column it came from, because the two are
                    # different findings: a word missing from an obligatory
                    # line is what keeps a bill from adding up, and one missing
                    # from *Extra cost* is a course or a massage and costs the
                    # total nothing. Reported together and told apart, rather
                    # than filtered here -- both are charges going unread.
                    block.unnamed.append(
                        f"[{'owed' if required else 'extra'}] {unread}")
                    # Only in the obligatory column. A course nobody can name
                    # in the *Extra cost* list says nothing about whether what
                    # a diver must pay adds up.
                    unreadable = unreadable or bool(required)
                if fee is None:
                    continue
                kept = found.get(fee.code)
                if kept is None or _rank(fee) > _rank(kept):
                    found[fee.code] = fee
        block.fees = list(found.values())
        owed = [fee for fee in block.fees if fee.tier is FeeTier.MANDATORY
                and not fee.included]
        block.complete = not unreadable and all(
            fee.has_price and not fee.unit_unstated for fee in owed)
        blocks.append(block)
    return blocks, warnings
