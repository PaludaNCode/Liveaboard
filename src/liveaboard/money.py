"""Money, currency and conversion.

Egyptian liveaboards quote in USD and EUR more or less interchangeably, and
sometimes in both on the same page. The site displays euro only, so conversion
is unavoidable — but a converted number is a weaker claim than a quoted one,
and this module keeps the two distinguishable all the way to the rendered page.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

DISPLAY_CURRENCY = "EUR"

CENT = Decimal("0.01")


def _dec(value: Any) -> Decimal:
    """Coerce JSON scalars to Decimal without going through binary float."""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


@dataclass(frozen=True, slots=True)
class Money:
    """An amount in a named currency. Never silently comparable across currencies."""

    amount: Decimal
    currency: str = DISPLAY_CURRENCY

    @classmethod
    def parse(cls, raw: Any, default_currency: str = DISPLAY_CURRENCY) -> Money:
        """Build from JSON: a bare number, ``"120 EUR"``, or ``{"amount", "currency"}``."""
        if isinstance(raw, Money):
            return raw
        if isinstance(raw, dict):
            return cls(_dec(raw["amount"]), str(raw.get("currency", default_currency)).upper())
        if isinstance(raw, str):
            parts = raw.split()
            if len(parts) == 2:
                return cls(_dec(parts[0]), parts[1].upper())
            return cls(_dec(parts[0]), default_currency)
        return cls(_dec(raw), default_currency)

    def __add__(self, other: Money) -> Money:
        if self.currency != other.currency:
            raise ValueError(f"refusing to add {self.currency} to {other.currency}")
        return Money(self.amount + other.amount, self.currency)

    def __mul__(self, factor: int | Decimal) -> Money:
        return Money(self.amount * _dec(factor), self.currency)

    @property
    def rounded(self) -> Decimal:
        return self.amount.quantize(CENT, rounding=ROUND_HALF_UP)

    def as_dict(self) -> dict[str, Any]:
        return {"amount": float(self.rounded), "currency": self.currency}

    def __str__(self) -> str:
        return f"{self.rounded} {self.currency}"


def zero(currency: str = DISPLAY_CURRENCY) -> Money:
    return Money(Decimal("0"), currency)


PLACEHOLDER_SOURCE = re.compile(r"placeholder|unknown|example|todo|stand-?in", re.I)
"""How a rate table admits it is not a real rate source."""


@dataclass(frozen=True, slots=True)
class FxRate:
    """One currency pair on one day, with an attributed source."""

    base: str
    quote: str
    rate: Decimal
    as_of: date
    source: str

    def convert(self, money: Money) -> Money:
        if money.currency != self.base:
            raise ValueError(f"rate is {self.base}->{self.quote}, got {money.currency}")
        return Money(money.amount * self.rate, self.quote)


class FxTable:
    """Rates into the display currency, keyed by source currency.

    Conversions are recorded so a page can state which numbers were quoted in
    euro and which this project converted, and at what rate.
    """

    def __init__(self, rates: dict[str, FxRate], display: str = DISPLAY_CURRENCY) -> None:
        self.display = display
        self._rates = rates

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> FxTable:
        display = payload.get("display_currency", DISPLAY_CURRENCY)
        as_of = date.fromisoformat(payload["as_of"])
        source = payload.get("source", "unknown")
        rates = {
            code: FxRate(code, display, _dec(rate), as_of, source)
            for code, rate in payload["rates"].items()
        }
        return cls(rates, display)

    def to_display(self, money: Money) -> tuple[Money, FxRate | None]:
        """Return the euro amount and the rate used, or ``None`` if already euro."""
        if money.currency == self.display:
            return money, None
        try:
            rate = self._rates[money.currency]
        except KeyError as exc:
            raise ValueError(f"no {money.currency}->{self.display} rate available") from exc
        return rate.convert(money), rate

    @property
    def as_of(self) -> date | None:
        for rate in self._rates.values():
            return rate.as_of
        return None

    @property
    def source(self) -> str | None:
        for rate in self._rates.values():
            return rate.source
        return None

    MAX_FRESH_DAYS = 7
    """How old a published rate may be before the page should say so.

    The ECB publishes on working days only, so a Monday build legitimately
    carries Friday's rate and a holiday can stretch that to four or five days.
    A week means the fetch has been failing rather than resting.
    """

    def age_days(self, today: date) -> int | None:
        """How old this rate was on ``today``, which the caller must supply.

        There is deliberately no default. It was ``date.today()``, and that
        default is what `render` reached for by writing nothing: the same
        committed inputs then built a different page after every midnight, and
        on 2026-09-04 `main` went red with nobody having touched it. `render`
        names its own day now (`read_on`), which fixed the caller — this fixes
        the shape that let it happen, because a clock reachable by omission is
        a clock something will omit again. Every other caller already passed a
        date, so the door closes on nothing that was using it, and leaving it
        out is a `TypeError` rather than a page that quietly drifts.
        """
        as_of = self.as_of
        if as_of is None:
            return None
        return (today - as_of).days

    def is_stale(self, today: date) -> bool:
        """A sourced rate that has stopped being refreshed.

        Distinct from unsourced: this one came from somewhere real, it is just
        old. The fetcher keeps the previous file when a fetch fails rather than
        reverting to a placeholder, which is the right call -- yesterday's real
        rate beats a made-up one -- but it means silence looks identical to
        success unless something notices the date stopped moving.
        """
        age = self.age_days(today)
        return age is not None and age > self.MAX_FRESH_DAYS

    @property
    def is_sourced(self) -> bool:
        """Does the rate come from somewhere, or is it a stand-in?

        The shipped table is a hardcoded 0.92 labelled "placeholder — replace
        with a real rate source". The page was showing it as "converted at 0.92
        (2026-08-27)", which reads as a rate someone looked up on that date.

        Every euro figure on a site about advertised prices being wrong rests
        on this number, so the one thing it must not do is look more certain
        than it is.
        """
        source = self.source
        return bool(source) and not PLACEHOLDER_SOURCE.search(source or "")
