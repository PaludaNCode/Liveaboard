"""Parse liveaboard.com's "Required Extras" and "Optional Extras" blocks.

The disclosure looks like this, verbatim from a vessel page:

    Required Extras: Environment Tax (€45), Fuel Surcharge (€60-70 / trip),
    National Park Fees (€35-100 / trip), Port Fees (€35).

    Optional Extras: Gratuities (€80), Nitrox (€30 / trip),
    Nitrox Course (€250 / item), Private Dive Guide (€500 / trip),
    Rental Gear, Scuba Diving Courses (€300-350),
    Laundry / Pressing Services (€5 / item).

Three details drive the whole design here:

* **Amounts are ranges as often as not.** "€35-100" for park fees is a 65 euro
  spread on a supposedly fixed cost. Collapsing it to the low end would
  understate the bill, which is the exact failure this project exists to
  correct, so both ends are kept and the total is reported as a range.

* **Some extras carry no price at all** — "Rental Gear" is listed and left
  blank. That is a third state, distinct from zero and from absent, and it has
  to survive to the page.

* **The site's own Required/Optional split is authoritative** for whether a
  cost is escapable, but not for how it should be counted. Gratuities are filed
  under Optional here and are nevertheless paid by nearly everyone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..taxonomy import FEE_LABELS, FeeBasis, FeeCode, FeeTier

BLOCK = re.compile(
    r"(Required|Optional)\s+Extras\s*:\s*(.+?)(?=(?:Required|Optional)\s+Extras\s*:|$)",
    re.I | re.S,
)

ENTRY = re.compile(
    r"""
    (?P<label>[^,()]+?)                      # "National Park Fees"
    \s*
    (?:\(                                    # optional bracketed amount
        \s*(?P<currency>[€$£])?\s*
        (?P<low>\d[\d.,]*)
        (?:\s*[-–]\s*(?P<high>\d[\d.,]*))?   # "60-70"
        \s*(?:/\s*(?P<basis>trip|item|day|night|dive|person))?
        [^)]*
    \))?
    \s*(?:,|\.|$)
    """,
    re.I | re.X,
)

CURRENCIES = {"€": "EUR", "$": "USD", "£": "GBP"}

BASES = {
    "trip": FeeBasis.PER_TRIP,
    "item": FeeBasis.PER_TRIP,  # an "item" is one purchase on one trip
    "day": FeeBasis.PER_DAY,
    "night": FeeBasis.PER_NIGHT,
    "dive": FeeBasis.PER_DIVE,
    "person": FeeBasis.PER_TRIP,
}

# Matched longest-first so "Nitrox Course" never resolves as "Nitrox".
LABEL_CODES: tuple[tuple[str, FeeCode], ...] = (
    ("national park", FeeCode.MARINE_PARK),
    ("marine park", FeeCode.MARINE_PARK),
    ("park fee", FeeCode.MARINE_PARK),
    ("environment tax", FeeCode.ENVIRONMENT_TAX),
    ("environmental tax", FeeCode.ENVIRONMENT_TAX),
    ("fuel surcharge", FeeCode.FUEL_SURCHARGE),
    ("fuel", FeeCode.FUEL_SURCHARGE),
    ("port fee", FeeCode.PORT_FEES),
    ("harbour", FeeCode.PORT_FEES),
    ("nitrox course", FeeCode.COURSE),
    ("scuba diving course", FeeCode.COURSE),
    ("diving course", FeeCode.COURSE),
    ("course", FeeCode.COURSE),
    ("nitrox", FeeCode.NITROX),
    ("private dive guide", FeeCode.PRIVATE_GUIDE),
    ("dive guide", FeeCode.PRIVATE_GUIDE),
    ("rental gear", FeeCode.GEAR_RENTAL),
    ("gear rental", FeeCode.GEAR_RENTAL),
    ("equipment rental", FeeCode.GEAR_RENTAL),
    ("rental equipment", FeeCode.GEAR_RENTAL),
    ("gratuit", FeeCode.GRATUITIES),
    ("tip", FeeCode.GRATUITIES),
    ("laundry", FeeCode.LAUNDRY),
    ("pressing", FeeCode.LAUNDRY),
    ("visa", FeeCode.VISA),
    ("insurance", FeeCode.DIVE_INSURANCE),
    ("transfer", FeeCode.AIRPORT_TRANSFER),
    ("single supplement", FeeCode.SINGLE_SUPPLEMENT),
    ("vat", FeeCode.TAX_VAT),
)

# Optional-block codes that are nevertheless paid by nearly everyone, or that
# the site's own toggles govern. The block a fee appears in decides whether it
# is escapable; this decides how it is counted.
CUSTOMARY_CODES = frozenset({FeeCode.GRATUITIES})
TOGGLED_CODES = frozenset(
    {
        FeeCode.NITROX,
        FeeCode.GEAR_RENTAL,
        FeeCode.DIVE_INSURANCE,
        FeeCode.AIRPORT_TRANSFER,
    }
)

NOISE = re.compile(r"^\s*(and|or|etc|extras?|none|n/?a)\s*$", re.I)


@dataclass(frozen=True, slots=True)
class ParsedFee:
    """One extra as the page states it, before it becomes a :class:`FeeItem`."""

    code: FeeCode
    label: str
    tier: FeeTier
    low: float | None
    high: float | None
    currency: str
    basis: FeeBasis

    @property
    def is_range(self) -> bool:
        return self.high is not None and self.high != self.low

    @property
    def has_price(self) -> bool:
        return self.low is not None


def classify_label(label: str) -> FeeCode | None:
    lowered = label.lower()
    for needle, code in LABEL_CODES:
        if needle in lowered:
            return code
    return None


def _tier_for(code: FeeCode, required: bool) -> FeeTier:
    if required:
        return FeeTier.MANDATORY
    if code in CUSTOMARY_CODES:
        return FeeTier.CUSTOMARY
    if code in TOGGLED_CODES:
        return FeeTier.CONDITIONAL
    return FeeTier.OPTIONAL


def _number(raw: str | None) -> float | None:
    if raw is None:
        return None
    cleaned = raw.replace(",", "").rstrip(".")
    try:
        return float(cleaned)
    except ValueError:
        return None


CONTINUATION = re.compile(r"^\s*[(\-–—/]")
"""A line that continues the one above rather than starting a new entry."""


def normalise_disclosure(text: str) -> str:
    """Turn a browser's ``innerText`` into comma-separated entries.

    Rendered text puts each extra on its own line and often its amount on the
    next one again::

        Environment Tax
        (€45)
        Fuel Surcharge
        (€60-70 / trip)

    Naively swapping every newline for a comma separates each label from its
    own price and silently reports seven priced extras as unpriced — worse than
    the run-together text it was meant to fix. So a line opening with a bracket
    or a dash is rejoined to the line above, and only the remaining breaks
    become separators.

    Idempotent on text that is already comma-separated, so both the raw-HTML
    and rendered-text paths can call it.
    """
    joined: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if joined and CONTINUATION.match(line):
            joined[-1] = f"{joined[-1]} {line}"
        else:
            joined.append(line)
    return ", ".join(joined)


def parse_extras(text: str, default_currency: str = "EUR") -> list[ParsedFee]:
    """Extract every stated extra from a vessel page's disclosure text."""
    found: list[ParsedFee] = []
    seen: set[FeeCode] = set()

    if "\n" in text:
        text = normalise_disclosure(text)

    for required_word, body in BLOCK.findall(text):
        required = required_word.lower() == "required"
        for match in ENTRY.finditer(body):
            label = " ".join(match.group("label").split())
            if not label or NOISE.match(label):
                continue
            code = classify_label(label)
            if code is None or code in seen:
                continue
            seen.add(code)

            low = _number(match.group("low"))
            high = _number(match.group("high"))
            symbol = match.group("currency")
            basis = BASES.get((match.group("basis") or "").lower(), FeeBasis.PER_TRIP)

            found.append(
                ParsedFee(
                    code=code,
                    label=label,
                    tier=_tier_for(code, required),
                    low=low,
                    high=high if high is not None else low,
                    currency=CURRENCIES.get(symbol or "", default_currency),
                    basis=basis,
                )
            )
    return found


def to_fee_dicts(fees: list[ParsedFee], provenance: dict) -> list[dict]:
    """Render parsed extras into the dataset's fee shape."""
    out = []
    for fee in fees:
        entry: dict = {
            "code": fee.code.value,
            "tier": fee.tier.value,
            "basis": fee.basis.value,
            "included": False,
            "provenance": provenance,
        }
        if fee.has_price:
            entry["amount"] = {"amount": fee.low, "currency": fee.currency}
            if fee.is_range:
                entry["amount_max"] = {"amount": fee.high, "currency": fee.currency}
                entry["note"] = f'Operator quotes "{fee.label}" as a range'
            elif fee.label.lower() != FEE_LABELS.get(fee.code, "").lower():
                # Keep the operator's own wording only when it says something
                # our label does not; echoing it back is noise.
                entry["note"] = fee.label
        else:
            # Listed with no figure. Never treated as free.
            entry["amount"] = None
            entry["note"] = f"{fee.label}: listed with no price"
        out.append(entry)
    return out
