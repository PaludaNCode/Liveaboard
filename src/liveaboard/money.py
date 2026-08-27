"""Money, currency and conversion.

Egyptian liveaboards quote in USD and EUR more or less interchangeably, and
sometimes in both on the same page. The site displays euro only, so conversion
is unavoidable — but a converted number is a weaker claim than a quoted one,
and this module keeps the two distinguishable all the way to the rendered page.
"""

from __future__ import annotations

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
