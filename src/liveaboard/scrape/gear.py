"""Parse the rental-gear dialog on a liveaboard.com vessel page.

"Rental Gear" appears in almost every vessel's optional extras with no figure
beside it, so the extras parser can only report that a diver will be charged,
never how much. The figures are one click away, in a dialog the page addresses
as ``#modal-gear``, and this reads that.

A probe run against three vessels returned the text below, verbatim, and the
markup behind it is the same on all three: an ``h5`` heading each section, then
``<li><strong>ITEM</strong> <span>PRICE / week</span></li>`` per row.

    Rental Gear Prices
    Equipment is available for rent on this boat. ...
    Single gear rent
      15L tanks €35-46 / week    BCD €83 / week      Dive Computer €14 / week
      Nitrox tank Included       Regulator €83 / week
    Full equipment rent
      BCD, Dive Computer, Fins, Mask, Regulator, SMB, Wetsuit €206 / week
    * Prices are shown per person and in the operator's preferred currency.

Three things that decided the shape of this module:

* **The bundle is the only honest gear price.** A diver renting gear rents a
  set of it, and where the operator quotes one -- "Full equipment rent" -- that
  is what they pay. Where it does not, adding up single items would be
  inventing a basket the operator never sold: whether the set is cheaper than
  its parts is exactly the thing that is not stated. So the fee stays unpriced
  and the single items ride along in the note, where a reader can see the
  scale without the total pretending to know it.

* **Prices are quoted per week and trips are not a week long.** Nothing on the
  page says how a nine-night trip is billed, so :class:`FeeBasis.PER_WEEK`
  rounds up: a diver keeps the kit for the whole trip, and two weeks' hire for
  nine nights is the reading that does not undercharge.

* **"Nitrox tank: Included" is not "nitrox is included".** It sits in a list of
  what gear costs to hire, so the plain reading is that hiring a nitrox tank
  costs nothing on top of the gear -- not that fills are free for everyone. It
  is recorded and deliberately not promoted into the nitrox fee.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..taxonomy import FeeBasis, FeeCode, FeeTier

TITLE = re.compile(r"Rental\s+Gear\s+Prices", re.I)
"""The dialog's own heading. Its absence means the parse has the wrong text."""

SECTION_FULL = re.compile(r"Full\s+equipment\s+rent", re.I)
SECTION_SINGLE = re.compile(r"Single\s+gear\s+rent", re.I)

ROW = re.compile(
    r"""
    <li[^>]*>\s*
    <strong[^>]*>\s*(?P<label>[^<]+?)\s*</strong>\s*
    <span[^>]*>\s*(?P<value>[^<]+?)\s*</span>
    """,
    re.I | re.X,
)
"""One gear row. Read from markup rather than rendered text.

The rendered text runs the rows together on one line -- "BCD €40 / week Dive
Computer €40 / week" -- so splitting it means guessing where a label ends. The
markup states it.
"""

AMOUNT = re.compile(
    r"""
    (?P<currency>[€$£])?\s*
    (?P<low>\d[\d.,]*)
    (?:\s*[-–]\s*(?P<high>\d[\d.,]*))?
    \s*(?:/\s*(?P<basis>week|trip|day|night|dive|item))?
    """,
    re.I | re.X,
)

INCLUDED = re.compile(r"^\s*included\s*$", re.I)

CURRENCIES = {"€": "EUR", "$": "USD", "£": "GBP"}

BASES = {
    "week": FeeBasis.PER_WEEK,
    "trip": FeeBasis.PER_TRIP,
    "item": FeeBasis.PER_TRIP,
    "day": FeeBasis.PER_DAY,
    "night": FeeBasis.PER_NIGHT,
    "dive": FeeBasis.PER_DIVE,
}

MAX_LABEL_CHARS = 80
"""Longest plausible gear label.

Longer than the extras cap because the bundle row names its whole contents:
"BCD, Dive Computer, Fins, Mask, Regulator, SMB, Wetsuit" is 54 characters and
is a real label, not a parse running off the end.
"""

NITROX_TANK = re.compile(r"\bnitrox\b", re.I)


@dataclass(frozen=True, slots=True)
class GearItem:
    """One line of the hire list, priced or stated as included."""

    label: str
    low: float | None
    high: float | None
    currency: str
    basis: FeeBasis
    included: bool = False

    @property
    def has_price(self) -> bool:
        return self.low is not None

    @property
    def is_range(self) -> bool:
        return self.high is not None and self.high != self.low

    def as_text(self) -> str:
        """How this item reads in a note, e.g. "BCD €83/week"."""
        if self.included:
            return f"{self.label} included"
        if not self.has_price:
            return self.label
        symbol = next((s for s, c in CURRENCIES.items() if c == self.currency), "")
        span = f"{self.low:g}" + (f"-{self.high:g}" if self.is_range else "")
        unit = self.basis.value.replace("per_", "/")
        return f"{self.label} {symbol}{span}{unit}"


@dataclass(frozen=True, slots=True)
class GearReading:
    """Everything the dialog stated for one vessel."""

    items: list[GearItem] = field(default_factory=list)
    bundle: GearItem | None = None
    """The "Full equipment rent" row, when the operator quotes one."""

    @property
    def nitrox_tank_included(self) -> bool:
        """Whether hiring a nitrox tank costs nothing extra.

        Not the same question as whether nitrox is included in the fare, and
        deliberately not used to answer that one.
        """
        return any(i.included and NITROX_TANK.search(i.label) for i in self.items)

    def __bool__(self) -> bool:
        return bool(self.items or self.bundle)


def _number(raw: str | None) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw.replace(",", "").rstrip("."))
    except ValueError:
        return None


def _item(label: str, value: str, default_currency: str) -> GearItem | None:
    """Turn one ``<strong>``/``<span>`` pair into an item, or reject it."""
    label = " ".join(label.split())
    value = " ".join(value.split())
    if not label or len(label) > MAX_LABEL_CHARS:
        return None

    if INCLUDED.match(value):
        return GearItem(label, None, None, default_currency, FeeBasis.PER_TRIP, True)

    match = AMOUNT.search(value)
    low = _number(match.group("low")) if match else None
    if low is None:
        # Listed with no figure. Still worth carrying: it says the operator
        # rents the item, which is more than nothing.
        return GearItem(label, None, None, default_currency, FeeBasis.PER_TRIP)

    return GearItem(
        label=label,
        low=low,
        high=_number(match.group("high")),
        currency=CURRENCIES.get(match.group("currency") or "", default_currency),
        basis=BASES.get((match.group("basis") or "").lower(), FeeBasis.PER_TRIP),
    )


def parse_gear(html: str, default_currency: str = "EUR") -> GearReading:
    """Read the gear dialog's markup. Returns an empty reading, never a guess."""
    if not html or not TITLE.search(html):
        return GearReading()

    # The bundle sits under its own heading, so split there rather than trying
    # to recognise it by label: "BCD, Dive Computer, Fins, ..." is only a
    # bundle because of the heading above it.
    parts = SECTION_FULL.split(html, maxsplit=1)
    head, tail = (parts[0], parts[1]) if len(parts) == 2 else (html, "")

    items = [
        item
        for match in ROW.finditer(head)
        if (item := _item(match.group("label"), match.group("value"), default_currency))
    ]
    bundle = next(
        (
            item
            for match in ROW.finditer(tail)
            if (item := _item(match.group("label"), match.group("value"), default_currency))
            and item.has_price
        ),
        None,
    )
    return GearReading(items=items, bundle=bundle)


def to_fee_dict(reading: GearReading, provenance: dict) -> dict | None:
    """Render a reading as the dataset's gear-rental fee, or nothing.

    Optional, and toggled: plenty of divers bring their own kit, so this is off
    until the visitor says otherwise. What it is *not* is silently zero.
    """
    if not reading:
        return None

    priced = [i for i in reading.items if i.has_price]
    note = "; ".join(i.as_text() for i in reading.items) or None

    fee: dict = {
        "code": FeeCode.GEAR_RENTAL.value,
        "tier": FeeTier.OPTIONAL.value,
        "included": False,
        "provenance": provenance,
    }

    if reading.bundle is not None:
        fee["amount"] = {"amount": reading.bundle.low, "currency": reading.bundle.currency}
        if reading.bundle.is_range:
            fee["amount_max"] = {
                "amount": reading.bundle.high,
                "currency": reading.bundle.currency,
            }
        fee["basis"] = reading.bundle.basis.value
        fee["note"] = f"Full equipment hire: {reading.bundle.label}"
        return fee

    # No bundle quoted. Summing the singles would invent a set price the
    # operator never offered, so the line says what it knows and no more.
    fee["basis"] = FeeBasis.PER_WEEK.value if priced else FeeBasis.PER_TRIP.value
    fee["amount"] = None
    fee["note"] = (
        f"Operator prices gear per item, not as a set: {note}" if note
        else "Gear rental offered; no price stated"
    )
    return fee
