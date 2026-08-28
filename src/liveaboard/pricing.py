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

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable, Mapping

from .models import Departure, FeeItem, Itinerary, Provenance
from .money import DISPLAY_CURRENCY, FxRate, FxTable, Money, zero
from .taxonomy import DEFAULT_ON_TIERS, FeeCode, FeeTier

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
        if self.toggle:
            out["toggle"] = self.toggle
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

    Seven of seventy-nine vessels publish an extras disclosure listing only
    optional items — gratuities, gear, courses — and no required block at all.
    Every Egyptian liveaboard pays marine park and port fees, so that silence
    means one of two things and does not say which: the fees are bundled into
    the fare, or they are collected at the dock and simply not advertised.

    Counting the silence as zero made the site rank those operators as its most
    honest: ``odyssey`` scored 96% and ``emperor-asmaa`` 93%, against 86% for a
    vessel that published its park and port fees in full. A page built to argue
    that advertised prices hide costs cannot reward the operators that disclose
    the least.

    An *included* mandatory line still counts as known — that is an operator
    stating the fee is in the fare, which is the honest case this rewards.
    """
    return any(fee.tier is FeeTier.MANDATORY for fee in resolve_fees(itinerary, departure))


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


def _fee_line(fee: FeeItem, nights: int, dives: int, fx: FxTable, active: Toggles) -> BreakdownLine:
    """Resolve one fee into a display row: basis normalised, currency converted."""
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
        counted=_is_counted(fee, active),
        toggle=fee.toggle,
        provenance=fee.provenance,
        note=fee.note,
        fx_rate=rate,
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
    return [
        _fee_line(fee, itinerary.nights, itinerary.dives, fx, active)
        for fee in _sorted_fees(itinerary.fees)
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
    breakdown.lines.extend(
        _fee_line(fee, itinerary.nights, itinerary.dives, fx, active)
        for fee in _sorted_fees(resolve_fees(itinerary, departure))
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


