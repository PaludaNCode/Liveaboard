"""The domain objects.

The shape here is deliberately two-level: an :class:`Itinerary` is the product
an operator sells over and over, a :class:`Departure` is one sailing of it with
its own date and price. Fees hang off whichever level actually owns them —
marine park fees belong to the route, a fuel surcharge to the sailing — so the
true-cost engine can resolve them without the dataset repeating itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .money import Money
from .taxonomy import (
    FEE_LABELS,
    TOGGLEABLE,
    DiverLevel,
    FeeBasis,
    FeeCode,
    FeeTier,
    SourceKind,
)


@dataclass(frozen=True, slots=True)
class Provenance:
    """Where a value came from, and when.

    Carried on every price and every fee. A transparency site that cannot say
    where its own numbers came from has no standing to complain about opacity
    in anyone else's.
    """

    kind: SourceKind
    source_id: str
    retrieved: date | None = None
    url: str | None = None
    note: str | None = None

    @property
    def is_verified(self) -> bool:
        return self.kind is not SourceKind.SEED_ESTIMATE

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Provenance:
        retrieved = payload.get("retrieved")
        return cls(
            kind=SourceKind(payload["kind"]),
            source_id=payload["source_id"],
            retrieved=date.fromisoformat(retrieved) if retrieved else None,
            url=payload.get("url"),
            note=payload.get("note"),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "source_id": self.source_id,
            "retrieved": self.retrieved.isoformat() if self.retrieved else None,
            "url": self.url,
            "note": self.note,
            "verified": self.is_verified,
        }


@dataclass(frozen=True, slots=True)
class FeeItem:
    """One line of the true cost.

    ``included`` is the crux: the same fee is bundled by one operator and billed
    at the dock by another, and that difference is the whole story this site is
    trying to tell. An included fee still appears in the breakdown, at zero
    additional cost, so a generous operator gets visible credit for it.
    """

    code: FeeCode
    tier: FeeTier
    amount: Money | None
    """The stated amount, or the low end of a stated range.

    ``None`` means the operator listed the extra without a figure — "Rental
    Gear" with no price beside it. That is a real cost of unknown size, and
    treating it as zero would be a straightforward falsehood.
    """

    amount_max: Money | None = None
    """The high end, when the page quotes a range like "€35-100".

    Ranges are common and wide: park fees quoted at 35 to 100 euro are a 65 euro
    spread on a supposedly fixed charge. Keeping only the low end would
    understate the bill, which is the failure this project exists to correct.
    """

    basis: FeeBasis = FeeBasis.PER_TRIP
    included: bool = False
    provenance: Provenance | None = None
    note: str | None = None

    unit_unstated: bool = False
    """The source stated a figure and not the unit it is charged in.

    A third state, and it is not the same silence as :attr:`amount` being
    ``None`` on its own. *Nothing stated* is a charge nobody put a number to;
    this is a number the operator published whose unit the page left out, so
    it cannot be normalised and does not reach a total -- the figure stays in
    the note. See `scrape/gear.py`, which is the only writer today.

    It exists because `pricing.GEAR_ESTIMATE` must tell the two apart. The
    estimate is a fallback for a silence and **never an overwrite of a stated
    figure**: on the four vessels that quote a set price with no unit beside
    it, filling €180 replaced the operator's own €40, €135, €200 and €206 with
    this site's number. A guess is defensible where a source said nothing; over
    the top of what it did say it is not.
    """

    @property
    def label(self) -> str:
        return FEE_LABELS.get(self.code, self.code.value.replace("_", " ").title())

    @property
    def toggle(self) -> str | None:
        """The site toggle that governs this fee, if any."""
        return TOGGLEABLE.get(self.code)

    @property
    def has_price(self) -> bool:
        return self.amount is not None

    @property
    def is_range(self) -> bool:
        return self.amount_max is not None and self.amount_max != self.amount

    def _scale(self, money: Money, nights: int, dives: int) -> Money:
        if self.basis is FeeBasis.PER_TRIP:
            return money
        if self.basis is FeeBasis.PER_NIGHT:
            return money * nights
        if self.basis in (FeeBasis.PER_DAY, FeeBasis.PER_PERSON_PER_DAY):
            return money * (nights + 1)
        if self.basis is FeeBasis.PER_DIVE:
            return money * dives
        if self.basis is FeeBasis.PER_WEEK:
            # Counted in nights, not in days aboard. A seven-night Red Sea
            # liveaboard *is* the week the operator prices -- it spans eight
            # calendar days, and rounding those up billed the fleet's single
            # most common trip length as a fortnight's hire, doubling it.
            # Part weeks beyond that still round up: the kit is kept for the
            # whole trip and the page says nothing about pro-rata.
            return money * max(1, -(-nights // 7))
        raise ValueError(f"unhandled fee basis {self.basis}")

    def span_for_trip(self, nights: int, dives: int) -> tuple[Money | None, Money | None]:
        """Normalise the quoted basis to a per-person trip low and high."""
        if self.amount is None:
            return None, None
        if self.basis is FeeBasis.PER_DIVE and dives <= 0:
            # A charge per dive on a trip whose dive count nobody publishes is
            # a known cost of unknown size, which the breakdown already has a
            # state for. Multiplying by zero would show it as free, and a fee
            # rendered free because a denominator is missing is the exact
            # failure this project exists to expose in other people.
            return None, None
        low = self._scale(self.amount, nights, dives)
        high = self._scale(self.amount_max, nights, dives) if self.amount_max else low
        return low, high

    def for_trip(self, nights: int, dives: int) -> Money:
        """The low end, normalised. Raises when no price was stated."""
        low, _ = self.span_for_trip(nights, dives)
        if low is None:
            raise ValueError(f"{self.code.value} has no stated price")
        return low

    @classmethod
    def from_dict(cls, payload: dict[str, Any], default_currency: str) -> FeeItem:
        prov = payload.get("provenance")
        raw = payload.get("amount")
        raw_max = payload.get("amount_max")
        return cls(
            code=FeeCode(payload["code"]),
            tier=FeeTier(payload["tier"]),
            amount=Money.parse(raw, default_currency) if raw is not None else None,
            amount_max=Money.parse(raw_max, default_currency) if raw_max is not None else None,
            basis=FeeBasis(payload.get("basis", FeeBasis.PER_TRIP.value)),
            included=bool(payload.get("included", False)),
            provenance=Provenance.from_dict(prov) if prov else None,
            note=payload.get("note"),
            unit_unstated=bool(payload.get("unit_unstated", False)),
        )


@dataclass(frozen=True, slots=True)
class Operator:
    id: str
    name: str
    website: str | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Operator:
        return cls(id=payload["id"], name=payload["name"], website=payload.get("website"))


@dataclass(frozen=True, slots=True)
class Boat:
    """A vessel.

    Kept deliberately thin. Boats are a grouping axis for the by-boat view, not
    a subject in their own right: no cabin scoring, no luxury tiers.
    """

    id: str
    name: str
    operator_id: str
    cabins: int | None = None
    guests: int | None = None
    length_m: float | None = None
    year_built: int | None = None
    """Both read from the vessel's specification table, and both were sitting
    in ``data/fees.json`` unpublished for as long as that table has been
    scraped: ``promote`` read the ``specs`` block for ``nitrox_free`` and let
    the rest fall on the floor -- 71 lengths and 67 build years already in the
    repository, ``length_m`` null on all 77 boats while the field existed here
    the whole time. Nothing noticed, because a key that goes nowhere looks
    exactly like a key the source does not publish. See
    ``TestEverySpecTheFeeBookHoldsIsPublished``.
    """

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Boat:
        return cls(
            id=payload["id"],
            name=payload["name"],
            operator_id=payload["operator_id"],
            cabins=payload.get("cabins"),
            guests=payload.get("guests"),
            length_m=payload.get("length_m"),
            year_built=payload.get("year_built"),
        )


@dataclass(frozen=True, slots=True)
class Requirements:
    """The entry bar, as a filter rather than a paragraph of prose."""

    min_level: DiverLevel = DiverLevel.OPEN_WATER
    min_logged_dives: int = 0
    max_depth_m: int | None = None
    nitrox_recommended: bool = False
    strong_current: bool = False
    notes: str | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> Requirements:
        if not payload:
            return cls()
        return cls(
            min_level=DiverLevel(payload.get("min_level", DiverLevel.OPEN_WATER.value)),
            min_logged_dives=int(payload.get("min_logged_dives", 0)),
            max_depth_m=payload.get("max_depth_m"),
            nitrox_recommended=bool(payload.get("nitrox_recommended", False)),
            strong_current=bool(payload.get("strong_current", False)),
            notes=payload.get("notes"),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "min_level": self.min_level.value,
            "min_logged_dives": self.min_logged_dives,
            "max_depth_m": self.max_depth_m,
            "nitrox_recommended": self.nitrox_recommended,
            "strong_current": self.strong_current,
            "notes": self.notes,
        }


@dataclass(slots=True)
class Itinerary:
    """A route sold repeatedly through a season."""

    id: str
    name: str
    operator_id: str
    boat_id: str
    nights: int
    dives: int
    port_from: str
    port_to: str
    dive_sites: list[str] = field(default_factory=list)
    requirements: Requirements = field(default_factory=Requirements)
    fees: list[FeeItem] = field(default_factory=list)
    source_url: str | None = None
    summary: str | None = None
    title: str | None = None
    """The name as the page prints it: the same words, minus the port pair.

    ``name`` stays whole because it is the trip's identity -- the itinerary id
    is built from it, and two sailings differing only by port are two trips.
    This is the presentation of it, resolved in Python beside the port aliases
    that decide what counts as a port at all.
    """
    region: str | None = None
    """What a title says about where it goes when it names no dive site.

    Transcribed from the operator's own word, never inferred: "North" means the
    title said north. Absent whenever real sites were found, because a list of
    reefs is strictly better than a direction.
    """
    padi_fees: list[FeeItem] = field(default_factory=list)
    """The charges PADI Travel says a diver cannot decline on this same trip.

    A second seller's own disclosure, kept apart from ``fees`` and never merged
    into it. The two genuinely differ: of the 74 trips where both books can be
    added up, 43 disagree and 16 by €150 or more -- Odyssey's *Premium
    Expedition* is €120 of required extras through one seller and €420 through
    the other. Unioning them would produce a bill neither site quotes, and
    preferring one wholesale would hide the difference this site exists to show.
    """
    padi_fees_complete: bool = False

    dives_read: bool = False
    """Whether anybody has read this trip's own itinerary fragment.

    The difference between a source that answered and a source nobody asked,
    which ``dives = 0`` cannot express on its own. liveaboard.com prints the
    Dives row as a dash for exactly one trip of 352 -- Aphrodite's *North
    Dolphins*, a snorkelling week -- and that is the seller stating no count.
    The 74 itineraries with no fragment at all were never asked, and 41 of
    those are on boats liveaboard.com publishes no vessel page for.

    Both print as unknown and neither may produce a price per dive; the page
    says which it is, on the same rule ``fees_known`` and ``not_asked`` follow.
    """

    padi_sourced_fees: bool = False
    """True where this trip's own fee rows came from PADI Travel.

    Every other itinerary takes them from the vessel panel liveaboard.com
    publishes, which is per boat and does not change with the month. The 22
    vessels liveaboard.com does not sell have no such panel, so PADI's
    per-itinerary book is the only one there is and becomes theirs.

    A fallback where ours is absent, never a merge: the two disclose at
    different resolutions -- one figure per vessel against one per itinerary --
    and taking a line from each would build a bill neither seller quotes. The
    flag exists because the page names the source under the fee table, and
    naming the wrong one is the failure this project reports in other people.
    """
    """Whether every charge PADI states here is named and priced.

    False is the ordinary case for a trip PADI has not been read for *and* for
    one where it named a charge without a figure, and both mean the same thing
    downstream: no total may be claimed on PADI's behalf. Separate from
    ``padi_fees`` being empty, which is PADI stating that the fare covers
    everything -- a disclosure, not a gap. See
    `PadiComAdapter.fees_from_payload`.
    """

    @classmethod
    def from_dict(cls, payload: dict[str, Any], default_currency: str) -> Itinerary:
        return cls(
            id=payload["id"],
            name=payload["name"],
            operator_id=payload["operator_id"],
            boat_id=payload["boat_id"],
            nights=int(payload["nights"]),
            dives=int(payload.get("dives", 0)),
            port_from=payload["port_from"],
            port_to=payload.get("port_to", payload["port_from"]),
            dive_sites=list(payload.get("dive_sites", [])),
            requirements=Requirements.from_dict(payload.get("requirements")),
            fees=[FeeItem.from_dict(f, default_currency) for f in payload.get("fees", [])],
            source_url=payload.get("source_url"),
            summary=payload.get("summary"),
            title=payload.get("title"),
            region=payload.get("region"),
            padi_fees=[
                FeeItem.from_dict(f, default_currency)
                for f in payload.get("padi_fees", [])
            ],
            padi_fees_complete=bool(payload.get("padi_fees_complete", False)),
            dives_read=bool(payload.get("dives_read", False)),
            padi_sourced_fees=bool(payload.get("padi_sourced_fees", False)),
        )


@dataclass(slots=True)
class Departure:
    """One sailing: a date, a price, and any fees specific to that date."""

    id: str
    itinerary_id: str
    start: date
    end: date
    price: Money
    price_provenance: Provenance

    availability: str | None = None
    """Whether this sailing can still be booked.

    ``"available"``, ``"limited"``, ``"sold_out"``, or ``None`` when the source
    did not say. A sold-out departure priced alongside bookable ones is a
    comparison site recommending something nobody can buy, so this has to reach
    the page rather than being dropped on the way.
    """
    fees: list[FeeItem] = field(default_factory=list)
    booking_url: str | None = None
    padi_price: Money | None = None
    """What PADI Travel advertises for this same sailing, when it sells it.

    A berth price like :attr:`price`, and comparable only to a berth price. It
    becomes comparable to a total once PADI's own required extras are added to
    it, which is what `Itinerary.padi_fees` carries and what
    `Itinerary.padi_fees_complete` decides is safe to do. Where that is false
    this figure stays a berth price and the page says so rather than setting it
    beside a bill: a total measured against a fare shows whichever seller
    discloses less as the cheaper one, on a site whose argument is that
    undisclosed fees are the problem.

    ``None`` where PADI does not sell the sailing, which is 291 of our 892. Not
    zero, and not the operator's price copied across.
    """
    padi_provenance: Provenance | None = None
    berths: list[dict[str, Any]] = field(default_factory=list)
    """What is left on this sailing and at what price, one block per seller.

    A list because a sailing has more than one seller: liveaboard.com fills a
    block today and PADI sells 601 of the same departures ([#92]). Carried
    through in the shape ``promote`` wrote it — the ladder is normalised and
    converted there, and re-deriving it here would be a second place for the
    two to disagree.

    Empty where the booking page could not be read, which is not the same as a
    sailing with no cabins: 25 of 889 answered nothing and are absent rather
    than recorded as having none.
    """

    BLOCK_SELLER, BLOCK_SPOTS, BLOCK_CABINS, BLOCK_ABOARD = 0, 1, 2, 3
    """Positions inside one seller's block, named once.

    Mirrors the identical line in ``templates/app.js`` and the shape stated in
    the dataset's own ``berths_note``. The block is a list rather than an
    object because it ships 878 times and the keys would too.
    """

    def _stated(self, index: int) -> int | None:
        """The first seller to state the count at ``index``, or ``None``.

        ``0`` is an answer -- nothing left -- and only an absent figure is
        unknown, so this tests against ``None`` rather than truthiness.
        """
        for block in self.berths:
            if len(block) > index and block[index] is not None:
                return int(block[index])
        return None

    @property
    def spots_at_advertised(self) -> int | None:
        """Berths left at the price on the row, as the seller states it.

        Across every room selling at that price — a boat can split them, and
        233 of 864 sailings do. ``None`` where no seller stated a count;
        ``0`` is an answer and means nothing is on sale at that price.

        This read ``block.get("spots")`` until the CSV asked it for a number.
        The blocks have been lists since they gained a second seller, so the
        property could only ever have raised -- which nothing noticed, because
        nothing called it. A dead accessor written against a shape the data
        left behind is worse than none: it reads as the answer to a question
        that has one.
        """
        return self._stated(self.BLOCK_SPOTS)

    @property
    def berths_aboard(self) -> int | None:
        """Berths left on the sailing at any price, as a seller states it.

        The other of the two counts, and deliberately a separate word from
        :attr:`spots_at_advertised`: PADI publishes only this one, and putting
        it in the at-price slot would have relabelled *22 aboard* as *22 at
        this price* on the 249 rows with no ladder to contradict it.
        """
        return self._stated(self.BLOCK_ABOARD)

    sale: dict[str, Any] = field(default_factory=dict)
    """Whether this berth is marked down, and which sellers say so.

    ``{"sellers": [0, 1], "pct": 33, "was": 2565}`` — the seller indices point
    into the dataset's own pool, and ``pct``/``was`` describe *this row's*
    advertised price, so they are present only where the seller who set that
    price is the one discounting it. A row can therefore be on sale with no
    percentage beside it: two Red Sea Aggressor IV sailings are discounted on
    PADI and at list price on the site this row's figure comes from, and
    printing PADI's 33% against our undiscounted fare would invent a saving.

    Empty is "no seller published a list price above what they charge", which
    is not quite "not on sale": a booking page nobody could read states
    nothing, and three of the five PADI-only discounts are exactly that.
    """

    padi_only: bool = False
    """True where PADI Travel is the only seller listing this sailing.

    Not a quality of the trip -- a fact about who was asked. 53 of the dataset's
    sailings are here, on 14 boats it already carried, and Blue Storm and Blue
    Seas contribute 29 between them: near-complete weekly seasons PADI sells and
    liveaboard.com does not list at all.

    Such a row's :attr:`price` and :attr:`price_provenance` are PADI's, and its
    :attr:`padi_price` is always ``None``. One seller's figure repeated into the
    second seller's field would read on the page as two sellers agreeing, which
    is the opposite of what this flag records.
    """

    @property
    def padi_difference(self) -> Money | None:
        """PADI's berth price minus ours, or ``None`` if either is missing.

        Both sides are advertised prices in their own currencies here; the
        conversion to one currency happens where every other price is converted,
        so this is not the number the page prints. It exists so the comparison
        is defined in one place rather than in the renderer.
        """
        if self.padi_price is None or self.padi_price.currency != self.price.currency:
            return None
        return Money(self.padi_price.amount - self.price.amount, self.price.currency)

    @property
    def bookable(self) -> bool:
        """Anything but a stated sold-out. Unknown is not a refusal."""
        return self.availability != "sold_out"

    @property
    def month(self) -> int:
        return self.start.month

    @classmethod
    def from_dict(cls, payload: dict[str, Any], default_currency: str) -> Departure:
        return cls(
            id=payload["id"],
            itinerary_id=payload["itinerary_id"],
            start=date.fromisoformat(payload["start"]),
            end=date.fromisoformat(payload["end"]),
            price=Money.parse(payload["price"], default_currency),
            price_provenance=Provenance.from_dict(payload["provenance"]),
            availability=payload.get("availability"),
            fees=[FeeItem.from_dict(f, default_currency) for f in payload.get("fees", [])],
            booking_url=payload.get("booking_url"),
            padi_price=(Money.parse(payload["padi_price"], default_currency)
                        if payload.get("padi_price") else None),
            padi_provenance=(Provenance.from_dict(payload["padi_provenance"])
                             if payload.get("padi_provenance") else None),
            padi_only=bool(payload.get("padi_only")),
            berths=list(payload.get("berths") or []),
            sale=dict(payload.get("sale") or {}),
        )
