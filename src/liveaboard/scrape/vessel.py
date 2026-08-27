"""Read a vessel page's specification table and amenity lists.

Both are structured, both are already in the document, and both were being
worked around. The guest count was mined out of the marketing prose, which
found it on 36 of 67 boats and missed the rest. Nitrox inclusion was not read
at all -- it came from a hand-made file covering ten vessels.

A probe run returned the markup verbatim. Specifications are a run of
definition lists:

    <dl><dt class="!font-semibold">Max guests </dt><dd>20</dd></dl>
    <dl><dt class="!font-semibold">Number of cabins </dt><dd>9</dd></dl>
    <dl><dt class="!font-semibold">Length </dt><dd>30 meters</dd></dl>

and the amenity blocks are tick lists under their own headings:

    Diving:  Nitrox available · Free Nitrox · Shaded Diving Area · DIN Adaptors

Two judgements worth stating.

**"Free Nitrox" is the inclusion statement; "Nitrox available" is not.** Both
appear together, and read plainly they say different things: one that the boat
fills nitrox tanks, the other that it does not charge for them. Treating
"available" as included would mark half the fleet's paid nitrox as free.

**A missing row is not a zero.** A vessel whose table has no "Max guests" has
not declined to say -- it means the row is absent, and the caller gets ``None``
so the page can keep printing an empty cell rather than a number nobody stated.
"""

from __future__ import annotations

import html as html_module
import re
from dataclasses import dataclass

SPEC_ROW = re.compile(
    r"<dl[^>]*>\s*<dt[^>]*>\s*(?P<label>[^<]+?)\s*</dt>\s*<dd[^>]*>\s*(?P<value>[^<]*?)\s*</dd>",
    re.I,
)
"""One specification row. The table is many one-row ``dl`` elements, not one."""

TICK = re.compile(r"<li[^>]*>\s*(?P<item>[^<]+?)\s*</li>", re.I)
"""One amenity. The list carries Tailwind classes with brackets and ampersands,
so it is matched on the list items rather than on the container's class."""

GUESTS = re.compile(r"^(?:max(?:imum)?\s+)?guests?$|^number\s+of\s+guests?$", re.I)
CABINS = re.compile(r"^number\s+of\s+cabins?$|^cabins?$", re.I)
LENGTH = re.compile(r"^length$", re.I)
YEAR_BUILT = re.compile(r"^year\s+built$", re.I)

MAX_GUESTS = 60
"""Above this the value is not a guest count.

Liveaboards in this fleet run from eight to thirty-four berths. Kept even
though a table cell is far more trustworthy than a prose match: a mis-parsed
number on the page is worse than a blank, and the bound costs nothing.
"""

FREE_NITROX = re.compile(r"^free\s+nitrox$", re.I)
NITROX_AVAILABLE = re.compile(r"^nitrox\s+available$", re.I)


@dataclass(frozen=True, slots=True)
class VesselFacts:
    """What the vessel page states about the boat itself."""

    guests: int | None = None
    cabins: int | None = None
    length_m: int | None = None
    year_built: int | None = None
    amenities: tuple[str, ...] = ()

    @property
    def nitrox_free(self) -> bool:
        """The operator says nitrox costs nothing. Not the same as offering it."""
        return any(FREE_NITROX.match(a) for a in self.amenities)

    @property
    def nitrox_available(self) -> bool:
        """The boat fills nitrox. Says nothing about the price."""
        return any(
            NITROX_AVAILABLE.match(a) or FREE_NITROX.match(a) for a in self.amenities
        )

    def __bool__(self) -> bool:
        return any(
            (self.guests, self.cabins, self.length_m, self.year_built, self.amenities)
        )


def _int(value: str, *, limit: int | None = None) -> int | None:
    """The leading integer in a cell, or ``None``.

    Cells carry units -- "30 meters", "2 x Zodiac 25 HP" -- so this reads the
    first number and stops. Anything with no number at its start is not a count.
    """
    match = re.match(r"\s*(\d{1,4})\b", value)
    if not match:
        return None
    number = int(match.group(1))
    if limit is not None and number > limit:
        return None
    return number


def parse_specs(markup: str) -> dict[str, str]:
    """Every row of the specification table, label to value, as stated."""
    return {
        " ".join(html_module.unescape(m.group("label")).split()):
            " ".join(html_module.unescape(m.group("value")).split())
        for m in SPEC_ROW.finditer(markup or "")
    }


def parse_amenities(markup: str) -> tuple[str, ...]:
    """Every item of an amenity tick list, in the order the page lists them."""
    return tuple(
        " ".join(html_module.unescape(m.group("item")).split())
        for m in TICK.finditer(markup or "")
        if m.group("item").strip()
    )


def read_vessel(specs_markup: str, diving_markup: str = "") -> VesselFacts:
    """Fold the specification table and the diving amenities into one record."""
    rows = parse_specs(specs_markup)

    def find(pattern: re.Pattern[str]) -> str | None:
        return next((v for label, v in rows.items() if pattern.match(label)), None)

    guests = find(GUESTS)
    cabins = find(CABINS)
    length = find(LENGTH)
    built = find(YEAR_BUILT)

    return VesselFacts(
        guests=_int(guests, limit=MAX_GUESTS) if guests else None,
        cabins=_int(cabins, limit=MAX_GUESTS) if cabins else None,
        length_m=_int(length) if length else None,
        year_built=_int(built) if built else None,
        amenities=parse_amenities(diving_markup),
    )
