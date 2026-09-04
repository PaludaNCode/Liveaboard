"""The true-cost engine.

Given a departure and a set of visitor toggles, produce the full stack of what
a diver actually pays, in euro, with every line attributable to a source.

One design decision is load-bearing: **included fees still appear**, at zero
additional cost. An operator that bundles marine park fees should be visibly
different from one that bills at the dock, and deleting the line would hide
exactly the difference the site exists to show.

This module deliberately produces no score. It used to derive an "honesty"
percentage per operator, which turned the page into a league table and, worse,
disagreed with the total beside it: the score was measured against a fixed
basket while the total followed the visitor's own toggles. Two numbers about
the same trip, contradicting each other. What a diver needs is what the trip
costs them, so that is all this returns.
"""

from __future__ import annotations

import re

from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any, Iterable, Mapping

from .models import Departure, FeeItem, Itinerary, Provenance
from .money import DISPLAY_CURRENCY, FxRate, FxTable, Money, zero
from .taxonomy import DEFAULT_ON_TIERS, FeeBasis, FeeCode, FeeTier, SourceKind

Toggles = Mapping[str, bool]

DEFAULT_TOGGLES: dict[str, bool] = {
    "nitrox": True,
    "gear": True,
}
"""What the site starts with.

Both default **on**. They were off, on the reasoning that plenty of divers own
kit and the headline number should be the unavoidable minimum -- but a page
whose whole argument is that the advertised price is not the price should not
itself open on a number below what most visitors will pay. Rental gear is about
EUR 200 a week and nitrox EUR 70 where it is charged; a diver who owns a set and
breathes air can switch both off and watch the total fall, which is a better
first impression than one who does not own a set watching it rise.

Everything a diver cannot avoid is still in the total and still has no switch.
These two remain the only optional extras, so the headline number goes on
meaning the same thing on every row -- it is now the same fuller thing.
"""


GEAR_ESTIMATE = Money(Decimal("180"), DISPLAY_CURRENCY)
"""What a full set is priced at where the operator prices no set at all.

**A deliberate exception to "never invent a price", and the only one.** Every
other unpriced line stays unpriced: a fee this project cannot read is a known
cost of unknown size, and putting a figure on it is the failure the site exists
to report in other people. Rental gear is the one place where that answer was
costing the reader more than it protected them.

**Two** readings of the gear dialog leave the set unpriced with nothing to
read: an operator that prices items and never a bundle, and a bare "Rental
Gear" with no figure at all. They cover 8 vessels, 30 itineraries and 146
sailings, and on every one the line sat at nothing with the toggle **on by
default** -- so the Total the whole page is built to be trusted about was short
by a full week's hire, and the row said so only to a reader who opened the bill
and read the caveat under it.

**A third reading is not one of them, and this used to fill it too.** A bundle
figure with no unit beside it (see `scrape/gear.py`) is a price the operator
published; only its unit is missing. Filling €180 there put this site's number
over Bella 2's own €40, Blue Pearl's €135, Ghazala Adventure's €200 and Emperor
Superior's €206, on 82 sailings. An estimate answers a silence and does not
correct a source, so `FeeItem.unit_unstated` keeps those out and they stay
unpriced until somebody resolves the unit.

So the figure is stated, and stated as ours: `BreakdownLine.estimated` travels
with it, the amount prints with a `~` and a warning of its own on the bill, and
`render.gear_prices` excludes these lines from the footer's "about EUR X a
week" -- that sentence is about what operators charge and this is not one of
their numbers. A visitor who owns a set switches Rental gear off and it goes,
which is the same escape every other gear line has.

180 per trip, and per *trip* rather than per week because this project is
choosing the unit as well as the figure and a trip is the thing every row on
the page is one of. It sits under the 200-a-week the fleet's own quoted bundles
median at, which is the direction to be wrong in: an estimate that outran what
the boats actually charge would make the site's totals the dearest claim on the
page and it would be ours rather than an operator's.
"""

GEAR_ESTIMATE_PROVENANCE = Provenance(
    kind=SourceKind.DERIVED,
    source_id="site:gear-estimate",
    note="this site's own figure, not a price either seller published",
)
"""Attributed like everything else, and `DERIVED` rather than `SEED_ESTIMATE`.

`SEED_ESTIMATE` means *this whole row is placeholder research* and lights the
page's "not real quotes" banner; a real sailing at a real fare with one
estimated extra is not that, and firing the banner on a fifth of the table
would teach a reader to ignore it.
"""


def _gear_estimate_note(fee: FeeItem) -> str:
    """Say the figure is ours, and keep what the operator did say.

    The old note is the evidence -- the per-item prices, or the bundle figure
    whose unit was missing -- and it is the only thing on the line a reader can
    check the estimate against, so it survives in front of nothing and behind
    the sentence that admits the number is not the operator's.
    """
    ours = "estimated by this site: the operator rents gear and states no set price"
    return f"{ours} — {fee.note}" if fee.note else ours


def _needs_gear_estimate(fee: FeeItem) -> bool:
    """A gear line the operator offers and does not price.

    Not an *absent* line: a vessel whose panel nobody has read has no gear row
    at all, and inventing one would claim a service nobody stated. What this
    fills is a row the operator wrote itself, with the figure left out.
    """
    return (
        fee.code is FeeCode.GEAR_RENTAL
        and not fee.included
        and fee.amount is None
        # And **never over the top of a figure the operator published.** Four
        # vessels quote a set price with no unit beside it -- Bella 2 €40, Blue
        # Pearl €135, Ghazala Adventure €200, Emperor Superior €206 -- and
        # `amount` is `None` on those for a different reason: the unit is
        # missing, not the number. Filling €180 there swapped this site's guess
        # for the operator's own price on 82 sailings, which is not what an
        # estimate is for. The fallback answers a silence; it does not correct
        # a source. Those lines stay unpriced until their unit is resolved, and
        # the figure goes on saying what the page said.
        and not fee.unit_unstated
    )


@dataclass(frozen=True, slots=True)
class BreakdownLine:
    """One row of the cost table shown to the visitor."""

    code: FeeCode
    label: str
    tier: FeeTier
    quoted: Money | None
    display: Money | None
    included: bool
    counted: bool
    toggle: str | None
    provenance: Provenance | None
    note: str | None
    fx_rate: FxRate | None
    display_max: Money | None = None
    """High end of a quoted range, in display currency. ``None`` when fixed."""
    estimated: bool = False
    """Whether the amount is this project's own figure rather than a seller's.

    True on exactly one thing: a gear line the operator offers and does not
    price, filled from :data:`GEAR_ESTIMATE`. It ships to the browser because
    the page has to mark the figure and warn about it where it prints the bill;
    a number the site made up and did not label is the whole failure mode this
    file otherwise exists to avoid.
    """

    subsumed_by: FeeCode | None = None
    """The bundled charge on this same bill whose title names this one.

    Set means the line is shown and not added: see :func:`subsumed_charges`.
    ``counted`` is already ``False`` on such a line, so the arithmetic needs
    nothing from this -- what it carries is *which* line covers it, because a
    row saying a published charge is not counted has to name what covers it.
    """

    @property
    def has_price(self) -> bool:
        return self.display is not None

    @property
    def is_range(self) -> bool:
        return self.display_max is not None and self.display_max != self.display

    @property
    def charged(self) -> Money:
        """What this line adds to the total at the low end.

        An unpriced line adds nothing to the arithmetic but is *not* free; the
        breakdown flags it separately so the page can say so.
        """
        if self.included or not self.counted or self.display is None:
            return zero(DISPLAY_CURRENCY)
        return self.display

    @property
    def charged_max(self) -> Money:
        """What this line adds at the high end of its stated range."""
        if self.included or not self.counted or self.display is None:
            return zero(DISPLAY_CURRENCY)
        return self.display_max or self.display

    def as_dict(self) -> dict[str, Any]:
        """The line as the page needs it, and nothing else.

        Every byte here ships 838 times inside one HTML file, so what the
        browser never reads is not merely untidy -- it is the page's weight.
        Five fields were shipped and never read once: `charged` and
        `charged_max` (the browser sums `display` itself), `counted` (it
        re-decides that from the visitor's toggles), `basis` (already resolved
        to per-trip in Python) and `provenance` -- a whole nested object with a
        URL in it, on every line of every itinerary. Dropping them halved the
        payload, 2716 KB to 1328.

        The rest is omitted when it says nothing: a null, a false, a `quoted`
        equal to the `display` beside it, a `display_max` equal to `display`.
        The reader treats a missing key and a false one the same way, which is
        what `has_price` and `included` already relied on.

        Anything added here has to be read by `templates/app.js`, and
        `tests/test_dataset.py` asserts that both ways round.
        """
        out: dict[str, Any] = {
            "code": self.code.value,
            "label": self.label,
            "tier": self.tier.value,
        }
        if self.display:
            out["display"] = self.display.as_dict()
        if self.is_range and self.display_max:
            out["display_max"] = self.display_max.as_dict()
        if self.has_price:
            out["has_price"] = True
        if self.is_range:
            out["is_range"] = True
        if self.included:
            out["included"] = True
        if self.estimated:
            out["estimated"] = True
        if self.toggle:
            out["toggle"] = self.toggle
        if self.subsumed_by is not None:
            out["subsumed_by"] = self.subsumed_by.value
        if self.note:
            out["note"] = self.note
        if self.fx_rate is not None:
            # Only a converted line needs its original quote: the page prints
            # "converted from 1631 USD at 0.858738". On the 96% that were
            # quoted in euro, `quoted` was a byte-for-byte copy of `display`.
            out["converted"] = True
            if self.quoted:
                out["quoted"] = self.quoted.as_dict()
            out["fx"] = {
                "rate": float(self.fx_rate.rate),
                "as_of": self.fx_rate.as_of.isoformat(),
                "source": self.fx_rate.source,
            }
        return out


@dataclass(slots=True)
class Breakdown:
    """The complete answer to 'what does this trip actually cost?'"""

    departure_id: str
    nights: int
    base: Money
    lines: list[BreakdownLine] = field(default_factory=list)

    @property
    def total(self) -> Money:
        total = zero(DISPLAY_CURRENCY)
        for line in self.lines:
            total = total + line.charged
        return total

    @property
    def total_max(self) -> Money:
        """The total at the top of every quoted range."""
        total = zero(DISPLAY_CURRENCY)
        for line in self.lines:
            total = total + line.charged_max
        return total

    @property
    def is_range(self) -> bool:
        return self.total_max.amount != self.total.amount

    @property
    def unpriced(self) -> list[BreakdownLine]:
        """Counted lines the operator listed without a figure.

        These sit outside the arithmetic entirely: they are known costs of
        unknown size, so the honest total is "at least this much, plus these".
        """
        return [
            line
            for line in self.lines
            if line.counted and not line.included and not line.has_price
        ]

    @property
    def surcharge(self) -> Money:
        """Everything on top of the advertised price."""
        return Money(self.total.amount - self.base.amount, self.total.currency)

    @property
    def markup_pct(self) -> float:
        """How much the headline understates the bill, as a percentage."""
        if self.base.amount <= 0:
            return 0.0
        return float(self.surcharge.amount / self.base.amount * 100)

    @property
    def has_unverified(self) -> bool:
        return any(
            line.provenance is not None and not line.provenance.is_verified
            for line in self.lines
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "departure_id": self.departure_id,
            "base": float(self.base.rounded),
            "total": float(self.total.rounded),
            "total_max": float(self.total_max.rounded),
            "is_range": self.is_range,
            "unpriced": [line.code.value for line in self.unpriced],
            "surcharge": float(self.surcharge.rounded),
            "markup_pct": round(self.markup_pct, 1),
            "has_unverified": self.has_unverified,
            "lines": [line.as_dict() for line in self.lines],
        }


def resolve_fees(itinerary: Itinerary, departure: Departure) -> list[FeeItem]:
    """Merge itinerary-level and departure-level fees.

    A departure-level entry wins outright for its code: a sailing that has its
    own fuel surcharge replaces the route's standard one rather than stacking
    on top of it.
    """
    merged: dict[FeeCode, FeeItem] = {fee.code: fee for fee in itinerary.fees}
    for fee in departure.fees:
        merged[fee.code] = fee
    return list(merged.values())


def mandatory_known(itinerary: Itinerary, departure: Departure) -> bool:
    """Has the operator said anything about its unavoidable costs?

    Seven of seventy-nine vessels publish no *Required Extras* block at all.
    Every Egyptian liveaboard pays marine park and port fees, so that looked
    like a silence meaning one of two things without saying which: bundled into
    the fare, or collected at the dock and not advertised.

    Counting that silence as zero made the site rank those operators as its most
    honest: ``odyssey`` scored 96% and ``emperor-asmaa`` 93%, against 86% for a
    vessel that published its park and port fees in full. A page built to argue
    that advertised prices hide costs cannot reward the operators that disclose
    the least.

    An *included* mandatory line still counts as known — that is an operator
    stating the fee is in the fare, which is the honest case this rewards.

    **And it turned out the seven were not silent.** They print an `Included:`
    block that `fees.BLOCK` was not reading, and six of the seven name National
    Park Fees, Port Fees and the Fuel Surcharge in it -- Emperor Asmaa, the boat
    this note calls out by name, states all three as covered. The seventh,
    ``odyssey``, states VAT and the Environment Tax, which is the Red Sea levy
    under the name that operator gives it. So every one of them now answers this
    with an inclusion rather than with nothing, and the honest case is the one
    the site was declining to see: the ambiguity was never in the operators'
    disclosure, it was in what this code read of it.
    """
    return any(fee.tier is FeeTier.MANDATORY for fee in resolve_fees(itinerary, departure))


#: What separates one charge from the next inside a bundle's own title.
#: Commas, "and", and the ampersand -- the three an operator writes a list
#: with. Nothing is inferred from a part this cannot classify.
_LIST_SEPARATOR = re.compile(r",| and |&", re.IGNORECASE)


def subsumed_charges(fees: Iterable[FeeItem]) -> dict[FeeCode, FeeCode]:
    """Charges a bundle on the same bill already covers, and which bundle.

    Seawolf Dominator, on all six of its published itineraries. PADI puts two
    entries in `mandatoryOnBoard` and we read both, faithfully:

        Visa fees                                                     250
        Visa, dive permit, taxes, marine park fees, harbour fee
        and fuel surcharges                                       180-255

    The second names the first, so a total that added both charged the visa
    twice -- on 17 departures, on a vessel liveaboard.com does not list, so
    PADI's book is the only fee book those rows have.

    **The bundle is the charge and the component is a copy of it**, and that
    is the source's own account rather than an inference about what a visa
    ought to cost. Three readings, of the same seller's page:

    1. The operator itemises the money in its own `whatsNotIncluded` prose, on
       10 of the boat's 13 itineraries: *"Visa, dive permission and taxes 43
       Euro ... Fee for marine parks: South: 80 Euro ... Fuel surcharge: 30
       Euro per person ... Fee for marina Marsa Ghaleb 25 Euro"*. That is the
       bundle, itemised, and its parts sum to the bundle's own 180-255. **No
       Dominator itinerary states anything at 250 anywhere in that text.**
    2. The visa is priced *inside* the 43: *"By prior stay in a hotel we will
       cut from this amount 25 $ for the visa"*. So the standalone 250 is not
       this operator's visa charge under any reading of its own words.
    3. Seawolf Diving Safari's other hull settles what its visa is worth.
       Seawolf Steel, same operator, publishes one mandatory entry -- *Dive
       permission, taxes, Marine Park fee and harbor fee*, 185-255, the same
       figures moving the same way by route -- and prices *Visa fees* at **30,
       as an optional extra**. Same company, same seller, same season.

    An earlier reading of this pair had only the figure to go on (every other
    vessel pricing a visa alone prices it at 25-30) and stopped there, quite
    rightly: `tools/probe_padi_mandatory.py` had established that both are
    genuine catalogue items with different `extraId`, `kind` and `section`,
    that neither states a validity window, and that the enums do not mark one
    a package -- Galaxy files a package under the same `section` a bare
    component sits under elsewhere. So the sum was withheld and both lines
    kept. The prose and the sister ship are what that reading was missing, and
    they answer the question it could not: which of the two is the money.

    **So still nothing is dropped.** The line stays in the breakdown, priced,
    with the bundle that covers it named beside it -- the same shape as an
    included fee, which stays at zero rather than disappearing, and for the
    same reason: deleting it would hide that the seller published it. What
    changes is only that it is not added a second time, so the row states a
    total again.

    Deliberately narrow, and conservative in the same direction throughout:

    * Only charges a diver cannot decline -- priced, not included, no toggle,
      and a tier that counts without being asked for. An optional extra
      overlapping another changes no total.
    * Only a title naming **two or more** charges is a bundle. One name is a
      line, however long it is written.
    * `classify_label` does the naming, because it is the vocabulary this
      project already reads fee labels with and a second copy of it would
      drift. A part it cannot place -- *dive permit*, *taxes* -- contributes
      nothing, so the rule under-fires rather than over-fires.

    Across the fleet it resolves one code on one vessel and touches nothing
    else: Hammerhead's *Park and Port Fees* beside a separate fuel surcharge
    keeps both, as do the 40 bills pairing port fees with a fuel surcharge.
    """
    from .scrape.fees import classify_label

    priced = [
        fee for fee in fees
        if fee.has_price and not fee.included
        and fee.toggle is None and fee.tier in DEFAULT_ON_TIERS
    ]
    alone = {fee.code for fee in priced}

    found: dict[FeeCode, FeeCode] = {}
    for fee in priced:
        named = {
            code
            for part in _LIST_SEPARATOR.split(fee.note or "")
            if (code := classify_label(part.strip())) is not None
        }
        if len(named) < 2:  # one name is a line, not a bundle
            continue
        for code in named & alone:
            if code != fee.code:
                found.setdefault(code, fee.code)
    return {code: found[code] for code in sorted(found, key=lambda c: c.value)}


def _is_counted(fee: FeeItem, toggles: Toggles) -> bool:
    """Whether this fee lands in the total under the given toggles.

    A toggle is asked first, and the tier only decides fees that have none.
    The order used to be the other way round, which made both switches on the
    page inert: nitrox and gear are filed under the site's *Optional* Extras,
    the optional tier returned False before the toggle was read, and turning
    "Rental gear" on added nothing to any total. A switch that changes no
    number is worse than no switch -- it answers the visitor's question with a
    number that ignored them.

    Untoggled optional extras -- alcohol, courses, laundry -- still stay out.
    They are in the breakdown to be seen, not to be added to a comparison
    nobody asked for.
    """
    toggle = fee.toggle
    if toggle is not None:
        return bool(toggles.get(toggle, DEFAULT_TOGGLES.get(toggle, False)))
    if fee.tier is FeeTier.OPTIONAL:
        return False
    return fee.tier in DEFAULT_ON_TIERS


def base_line(departure: Departure, fx: FxTable) -> BreakdownLine:
    """The advertised berth price, as the first row of the cost table.

    Genuinely per-departure: the amount, the currency it was quoted in and the
    provenance of that quote all belong to the sailing, not the route.
    """
    display, rate = fx.to_display(departure.price)
    return BreakdownLine(
        code=FeeCode.BASE_FARE,
        label="Berth (advertised price)",
        tier=FeeTier.BASE,
        quoted=departure.price,
        display=display,
        included=False,
        counted=True,
        toggle=None,
        provenance=departure.price_provenance,
        note=None,
        fx_rate=rate,
    )


def _fee_line(
    fee: FeeItem,
    nights: int,
    dives: int,
    fx: FxTable,
    active: Toggles,
    subsumed_by: FeeCode | None = None,
) -> BreakdownLine:
    """Resolve one fee into a display row: basis normalised, currency converted.

    ``subsumed_by`` is the one thing here that is not the fee's own property
    but the bill's: a charge another line on the same bill already covers is
    shown and not counted. Every caller that sums a set of fees resolves it
    over **that** set -- see :func:`subsumed_charges` -- because the overlap
    is a fact about one bill and the two sellers publish two.

    The gear estimate is applied *here* rather than in any of the three
    callers, for the reason the toggles are read here: it is one rule, and a
    copy of it in `compute` and another in `padi_lines` would be two rules
    that agree until one of them is edited. Both sellers' bills carry the
    vessel's gear line, so both get the same estimate from one place.
    """
    estimated = _needs_gear_estimate(fee)
    if estimated:
        fee = replace(
            fee,
            amount=GEAR_ESTIMATE,
            amount_max=None,
            basis=FeeBasis.PER_TRIP,
            provenance=GEAR_ESTIMATE_PROVENANCE,
            note=_gear_estimate_note(fee),
        )

    low, high = fee.span_for_trip(nights, dives)

    display = display_max = rate = None
    if low is not None:
        display, rate = fx.to_display(low)
        if high is not None and high != low:
            display_max, _ = fx.to_display(high)

    return BreakdownLine(
        code=fee.code,
        label=fee.label,
        tier=fee.tier,
        quoted=low,
        display=display,
        display_max=display_max,
        included=fee.included,
        counted=_is_counted(fee, active) and subsumed_by is None,
        toggle=fee.toggle,
        provenance=fee.provenance,
        note=fee.note,
        fx_rate=rate,
        estimated=estimated,
        subsumed_by=subsumed_by,
    )


def itinerary_lines(
    itinerary: Itinerary,
    fx: FxTable,
    toggles: Toggles | None = None,
) -> list[BreakdownLine]:
    """The fee rows every departure of this itinerary shares.

    Everything a fee line needs is a property of the route: the fee itself, the
    nights and dives its basis normalises against, and the exchange rate. Only
    the base fare varies between sailings, so these resolve once per itinerary
    rather than once per departure.

    That is a fact about the data, not an optimisation the caller must trust:
    across the current dataset all 314 itineraries have departures whose
    non-base lines are identical. :func:`compute` remains the authority, and
    the renderer still checks a departure against this before reusing it, so a
    sailing that ever does price a fee differently keeps its own rows.
    """
    active = {**DEFAULT_TOGGLES, **(toggles or {})}
    covered = subsumed_charges(itinerary.fees)
    return [
        _fee_line(fee, itinerary.nights, itinerary.dives, fx, active,
                  covered.get(fee.code))
        for fee in _sorted_fees(itinerary.fees)
    ]


def padi_base_line(departure: Departure, fx: FxTable) -> BreakdownLine | None:
    """What the second seller advertises for this same berth.

    ``None`` where PADI does not sell the date, which is most of the time and
    is evidence of nothing: its calendar runs to a different depth on every
    boat. A berth nobody offered has no price, and a zero would read as free.
    """
    if departure.padi_price is None:
        return None
    display, rate = fx.to_display(departure.padi_price)
    return BreakdownLine(
        code=FeeCode.BASE_FARE,
        label="Berth (PADI Travel)",
        tier=FeeTier.BASE,
        quoted=departure.padi_price,
        display=display,
        included=False,
        counted=True,
        toggle=None,
        provenance=departure.padi_provenance,
        note=None,
        fx_rate=rate,
    )


def padi_lines(
    itinerary: Itinerary,
    fx: FxTable,
    toggles: Toggles | None = None,
) -> list[BreakdownLine] | None:
    """The same trip's fee rows as the second seller discloses them.

    ``None`` unless PADI's bill for this trip is complete -- every charge it
    names both classified and priced in a unit that normalises. A partial fee
    book cannot produce a total, and a total built from part of a disclosure is
    the precise thing this site was built to catch other people doing. The
    caller shows the price on its own instead, and says why there is no total
    beside it.

    **Two sellers, one dive deck.** The mandatory lines are PADI's own and are
    where the two sellers actually differ -- 43 of the 74 comparable trips
    disagree, Odyssey's *Premium Expedition* by €300. The rest of the bill is
    not the seller's at all: nitrox and rental gear are billed by the vessel,
    on board, out of one price list, to whoever walks up the gangway. So they
    are the same rows on both sides, taken from the vessel's disclosure, and
    the page says so.

    Doing anything else makes the comparison lie in a way a reader cannot see.
    Leave them out of PADI's side and its total is short by whatever the
    visitor has switched on -- about €200 with rental gear on, which is the
    default -- so PADI wins every row for a reason that has nothing to do with
    PADI. Leave them out of both and the column stops comparing the number the
    page is about.

    **PADI's own mandatory lines, and only those.** This used to take PADI's
    whole book and add the vessel's non-mandatory rows to it, which put the
    gangway charges in twice: PADI states nitrox and gear hire in its optional
    disclosure as well, so Serenity's PADI bill carried 35 of nitrox twice and
    210 of gear twice. It was every one of the 179 trips with a PADI bill --
    526 of 1,122 departures -- and rental gear is on by default, so half the
    page was quoting a second hire nobody would pay. Exactly the thing the
    paragraph above says this does not do; it just never filtered the side it
    was keeping.
    """
    if not itinerary.padi_fees_complete:
        return None
    active = {**DEFAULT_TOGGLES, **(toggles or {})}
    theirs = [fee for fee in itinerary.padi_fees if fee.tier is FeeTier.MANDATORY]
    shared = [fee for fee in itinerary.fees if fee.tier is not FeeTier.MANDATORY]
    covered = subsumed_charges(theirs + shared)
    return [
        _fee_line(fee, itinerary.nights, itinerary.dives, fx, active,
                  covered.get(fee.code))
        for fee in _sorted_fees(theirs + shared)
    ]


def compute(
    itinerary: Itinerary,
    departure: Departure,
    fx: FxTable,
    toggles: Toggles | None = None,
) -> Breakdown:
    """Build the full cost breakdown for one sailing."""
    active = {**DEFAULT_TOGGLES, **(toggles or {})}
    first = base_line(departure, fx)

    breakdown = Breakdown(
        departure_id=departure.id,
        nights=itinerary.nights,
        # Never None: a departure's price is required, so its conversion always
        # produces an amount. Only a *fee* can be listed without one.
        base=first.display,
    )
    breakdown.lines.append(first)
    merged = resolve_fees(itinerary, departure)
    covered = subsumed_charges(merged)
    breakdown.lines.extend(
        _fee_line(fee, itinerary.nights, itinerary.dives, fx, active,
                  covered.get(fee.code))
        for fee in _sorted_fees(merged)
    )
    return breakdown


_TIER_ORDER = {
    FeeTier.BASE: 0,
    FeeTier.MANDATORY: 1,
    FeeTier.CONDITIONAL: 2,
    FeeTier.CUSTOMARY: 3,
    FeeTier.OPTIONAL: 4,
}


def _sorted_fees(fees: Iterable[FeeItem]) -> list[FeeItem]:
    """Order fees the way the cost table should read: least avoidable first."""
    return sorted(fees, key=lambda f: (_TIER_ORDER[f.tier], f.code.value))


