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
    "nitrox": False,
    "gear": False,
}
"""What the site starts with.

Both default off because plenty of divers bring their own. Everything a diver
cannot avoid is already in the total and has no switch: a comparison is only
easy when the headline number means the same thing on every row.
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
        return {
            "code": self.code.value,
            "label": self.label,
            "tier": self.tier.value,
            "quoted": self.quoted.as_dict() if self.quoted else None,
            "display": self.display.as_dict() if self.display else None,
            "display_max": self.display_max.as_dict() if self.is_range else None,
            "charged": float(self.charged.rounded),
            "charged_max": float(self.charged_max.rounded),
            "has_price": self.has_price,
            "is_range": self.is_range,
            "included": self.included,
            "counted": self.counted,
            "toggle": self.toggle,
            "note": self.note,
            "converted": self.fx_rate is not None,
            "fx": (
                {
                    "rate": float(self.fx_rate.rate),
                    "as_of": self.fx_rate.as_of.isoformat(),
                    "source": self.fx_rate.source,
                }
                if self.fx_rate
                else None
            ),
            "provenance": self.provenance.as_dict() if self.provenance else None,
        }


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
    def per_night(self) -> Money:
        if self.nights <= 0:
            return self.total
        return Money(self.total.amount / Decimal(self.nights), self.total.currency)

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
            "per_night": float(self.per_night.rounded),
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
    """Whether this fee lands in the total under the given toggles."""
    if fee.tier is FeeTier.OPTIONAL:
        return False
    toggle = fee.toggle
    if toggle is not None:
        return bool(toggles.get(toggle, DEFAULT_TOGGLES.get(toggle, False)))
    return fee.tier in DEFAULT_ON_TIERS


def compute(
    itinerary: Itinerary,
    departure: Departure,
    fx: FxTable,
    toggles: Toggles | None = None,
) -> Breakdown:
    """Build the full cost breakdown for one sailing."""
    active = {**DEFAULT_TOGGLES, **(toggles or {})}
    base_display, base_fx = fx.to_display(departure.price)

    breakdown = Breakdown(
        departure_id=departure.id,
        nights=itinerary.nights,
        base=base_display,
    )
    breakdown.lines.append(
        BreakdownLine(
            code=FeeCode.BASE_FARE,
            label="Berth (advertised price)",
            tier=FeeTier.BASE,
            quoted=departure.price,
            display=base_display,
            included=False,
            counted=True,
            toggle=None,
            provenance=departure.price_provenance,
            note=None,
            fx_rate=base_fx,
        )
    )

    for fee in _sorted_fees(resolve_fees(itinerary, departure)):
        low, high = fee.span_for_trip(itinerary.nights, itinerary.dives)

        display = display_max = rate = None
        if low is not None:
            display, rate = fx.to_display(low)
            if high is not None and high != low:
                display_max, _ = fx.to_display(high)

        breakdown.lines.append(
            BreakdownLine(
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


