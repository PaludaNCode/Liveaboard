"""What a departure actually costs, cabin by cabin, and how many berths are left.

The dataset stores one price per sailing -- the figure the vessel page
advertises. The booking page shows what that figure is the *bottom of*:

    Cabin 3      2 People, 1 Bunk bed    $688 -> $619 /person    2 berths
    Cabin 1 & 2  2 People, 1 Bunk bed    $757 -> $682 /person    4 berths
    Suite        2 People, 1 Double bed  $874 -> $787 /person    2 berths

So the advertised price is the cheapest bunk in the cheapest cabin, and on that
sailing exactly two of them exist. A diver who wants the Suite pays $787, and a
diver who wants it alone pays that plus a stated 60% single supplement. That is
this site's own argument one level below where it currently stops.

**Read from `/BookingStep1?tourid={tour}&boatid={boat}`**, one plain GET, no
browser. Both ids come out of `Event.@id` (`LA-{x}-{boatID}-{tourID}`), which
the archive holds for every departure, so no crawling is needed to build the
URL -- the same trick that made the itinerary fragment affordable.

**The berth count is an attribute, not the red text.** ``data-allocation`` on
each cabin's guest-count select states it as a number for every cabin, whether
or not the page also prints *"only 2 spaces left!"* -- which it does only at
four or fewer, in red, beside "Save 10%". Both were read on two vessels and
they agree wherever both appear (Iceberg 2/4/2 against "only 2"/"only 4"/"only
2"), and the select's own options run 1..allocation, so three things on the
page say the same number. The attribute is the datum; the red text is
marketing that happens to quote it, and :func:`parse_cabins` reports a
disagreement rather than choosing between them.

It is still **the operator's claim**, not verified inventory, and the most
perishable thing here: true at fetch, stale by morning. Anything rendering it
must say when it was read.

Three more things this module is careful about, because they are the ways a
price column starts lying:

* **Two prices, and which is which matters.** The struck-through figure is the
  list price and the bold one is what you would pay today. Storing one and
  calling it "the price" would either overstate the cost or hide a discount
  that may not last. Cabins with no discount have no ``<del>`` at all.
* **Attribute values are quoted only when they have to be.** The site emits
  minified HTML: ``title=Suite`` unquoted, ``title="Cabin 1 &amp; 2"`` quoted
  because it has spaces. A pattern requiring the quotes reads two of Iceberg's
  three cabins and silently names the third after its database id.
* **Nothing is derived.** No cabin total from a per-person price, no capacity
  from a bed description, no single-occupancy figure from a percentage in
  prose that does not state one. A field the page does not answer is ``None``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import unescape
from typing import Any

# A cabin listing begins at the button that opens that cabin's own dialog, and
# `title` is its name -- the button text sits in a truncating element, and the
# title is what the site itself considers the full name.
#
# **The title is what separates a listing from a dialog.** The same
# `aria-controls=help-content-cabin-details-{id}` sits on the close button
# inside each cabin's modal, where it carries `aria-label="Close dialog"` and
# no title. Counting those doubles every cabin on a page read whole -- which a
# probe dump that stopped before the modals could not show.
CABIN_NAME = re.compile(
    r"aria-controls=help-content-cabin-details-(\d+)[^>]*\btitle=[^>]*>", re.I
)

# Each bookable cabin has exactly one guest-count select, and it carries
# everything the site knows about that cabin as data attributes.
CABIN_SELECT = re.compile(r"<select[^>]*\bname=input-cabin-guests-(\d+)[^>]*>", re.I)

# What stands where the select would be on a cabin nobody can book. The cabin
# is still listed in full -- name, beds, price -- so a sold-out sailing has a
# readable ladder and only its berths are zero.
FULL = re.compile(r">\s*FULL\s*<", re.I)

# What the cabin sleeps and in what: the <ol> under the name, and only that
# one. The "Save 10%" badge is an <li><span> too, in the price list, and
# reading every <li><span> on the block files it as an amenity.
DETAILS_LIST = re.compile(r"<ol[^>]*>(.*?)</ol>", re.I | re.S)
DETAIL_ITEM = re.compile(r"<li[^>]*>\s*(<span[^>]*>)([^<]+)</span>", re.I)
PEOPLE = re.compile(r"^\s*(\d{1,2})\s*(?:People|Person|Guests?)\s*$", re.I)

# The sleeping arrangement is the one detail whose span carries a `title`, on
# all eight cabins read across three vessels -- the occupancy and the amenities
# never do. Structure rather than vocabulary, because the vocabulary runs out:
# Red Sea Aggressor II's "1 Double or Twin (convertible)" contains no word a
# bed pattern would match, and matching on "bed" alone files it as an amenity.
BEDS = re.compile(r"\bbeds?\b", re.I)

# The struck-through list price and the price actually charged. `translate=no`
# marks the numbers and nothing else, which makes it a better anchor than the
# Tailwind classes around it, those being generated.
LIST_PRICE = re.compile(r"<del[^>]*>\s*([^<]*?\d[\d,.]*)\s*</del>", re.I)
NOW_PRICE = re.compile(
    r"<em[^>]*>\s*([^\s<]{1,3})\s*</em>\s*<span[^>]*\btranslate=no[^>]*>\s*([\d,.]+)\s*</span>",
    re.I,
)

# The red banner. Read only to check it against `data-allocation`.
SPACES_LEFT = re.compile(r"(?:only\s*)?(\d{1,2})\s*spaces?\s*left", re.I)
SOLD_OUT = re.compile(r"\b(?:fully\s+booked|sold\s*out|no\s+more\s+spaces)\b", re.I)

# What one person is charged to have the cabin to themselves, in prose, in a
# hidden div the site keys by cabin id -- so it is attributed by id rather than
# by where it happens to sit. Two phrasings, one number:
#
#   private-cabin-help-text-{id}    "a 60% surcharge applies for single
#                                    occupancy, which will be shown at the
#                                    next reservation step"
#   privacy-optional-help-text-{id} "If you'd like privacy, we can arrange it
#                                    for an additional 60% surcharge"
SURCHARGE = re.compile(r"(\d{1,3})\s*%\s*surcharge", re.I)

OPTION = re.compile(r"<option[^>]*>(.*?)(?=<option|</select|$)", re.I | re.S)
TAG = re.compile(r"<[^>]+>")

# What the glyph beside the number can be trusted to mean. `$` is deliberately
# absent: it is the Australian, Canadian, Singapore and US dollar alike, and
# the booking page renders whichever the visitor's session is set to. Guessing
# it is exactly the mistake this project exists to avoid, so the caller states
# the currency it asked for and the parser only ever contradicts it.
GLYPHS = {"€": "EUR", "£": "GBP", "¥": "JPY", "₪": "ILS", "R$": "BRL"}


@dataclass(frozen=True, slots=True)
class Cabin:
    """One cabin type on one departure, as the booking page states it."""

    cabin_id: str
    name: str
    sleeps: int | None
    """How many the cabin takes, from ``data-cabin-occupancy``."""
    beds: str | None
    amenities: tuple[str, ...]
    price: float | None
    """What one person pays today, in the reading's currency."""
    list_price: float | None
    """The struck-through figure, on the cabins that are discounted."""
    berths: int | None
    """**The operator's claim** of how many berths remain, and perishable.

    From ``data-allocation``, or zero on a cabin the page marks ``FULL``.
    ``None`` only where the page states neither.
    """
    sold_out: bool
    """Marked ``FULL``: listed, priced, and not for sale."""
    single_supplement_pct: int | None
    shareable: bool | None
    """Whether the site will seat a stranger in the other bunk.

    ``None`` where the page says ``undefined``, which it does -- a JavaScript
    value reaching the markup is not an answer and is not read as one.
    """
    occupancy_options: tuple[str, ...]

    @property
    def is_discounted(self) -> bool:
        return (self.list_price is not None and self.price is not None
                and self.list_price > self.price)

    @property
    def single_price(self) -> float | None:
        """What one diver pays to have the cabin alone, where both parts are
        stated. Derived arithmetic on two stated numbers, and ``None`` the
        moment either is missing -- never a default supplement of zero."""
        if self.price is None or self.single_supplement_pct is None:
            return None
        return round(self.price * (1 + self.single_supplement_pct / 100), 2)

    def as_dict(self) -> dict[str, Any]:
        """Only what is stated. Nulls are dropped, so a field that is absent is
        a field the page did not answer rather than a zero."""
        out: dict[str, Any] = {"cabin_id": self.cabin_id, "name": self.name}
        for key, value in (
            ("sleeps", self.sleeps), ("beds", self.beds),
            ("price", self.price), ("list_price", self.list_price),
            ("berths", self.berths),
            ("single_supplement_pct", self.single_supplement_pct),
            ("shareable", self.shareable),
        ):
            if value is not None:
                out[key] = value
        if self.amenities:
            out["amenities"] = list(self.amenities)
        if self.occupancy_options:
            out["occupancy_options"] = list(self.occupancy_options)
        if self.sold_out:
            out["sold_out"] = True
        return out


@dataclass
class CabinReading:
    """Every cabin one booking page offered, and what was odd about it."""

    cabins: list[Cabin] = field(default_factory=list)
    currency: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def cheapest(self) -> Cabin | None:
        """The rung the advertised price is quoting, sold out or not.

        Not "the cheapest one still for sale": the vessel page advertises this
        cabin's figure either way, and answering a different question here
        would make the berth count beside that figure belong to some other
        cabin.
        """
        priced = [c for c in self.cabins if c.price is not None]
        return min(priced, key=lambda c: c.price) if priced else None

    @property
    def berths_at_cheapest(self) -> int | None:
        """How many berths are left at the advertised price -- the question
        the vessel page cannot answer. Zero is an answer; ``None`` is not."""
        cabin = self.cheapest
        return cabin.berths if cabin else None

    @property
    def nothing_bookable(self) -> bool:
        """Every cabin listed and every one of them full."""
        return bool(self.cabins) and all(c.sold_out for c in self.cabins)

    def __bool__(self) -> bool:
        return bool(self.cabins)


def _attr(tag: str, name: str) -> str | None:
    """One attribute's value, quoted or not.

    The site quotes only what it must: ``title=Suite`` beside ``title="Cabin 1
    &amp; 2"``. Reading only the quoted form takes the name off every cabin
    whose name is one word.
    """
    match = re.search(
        rf"\b{re.escape(name)}=(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))", tag, re.I
    )
    if not match:
        return None
    value = next(g for g in match.groups() if g is not None)
    return unescape(value).strip()


def _flag(tag: str, name: str) -> bool | None:
    value = (_attr(tag, name) or "").lower()
    if value in {"true", "false"}:
        return value == "true"
    return None


def _int(value: str | None) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _prose(markup: str) -> str:
    """The words, with the markup and the entities out of the way."""
    return " ".join(unescape(TAG.sub(" ", markup)).split())


def _money(text: str) -> tuple[float | None, str]:
    """A figure and its currency mark, from "$ 688" or "€1,234.50"."""
    match = re.search(r"([^\d\s]{0,3})\s*([\d,]+(?:\.\d{1,2})?)", text or "")
    if not match:
        return None, ""
    try:
        return float(match.group(2).replace(",", "")), match.group(1).strip()
    except ValueError:
        return None, match.group(1).strip()


def _options(block: str) -> tuple[str, ...]:
    """The guest counts this cabin can be booked for.

    The options are unclosed -- ``<option value=1>1 person<option value=2>`` --
    so each runs to the next one rather than to a ``</option>``.
    """
    found = []
    for raw in OPTION.findall(block):
        text = _prose(raw)
        # The placeholder is the site's own "-", not an occupancy.
        if text and text != "-":
            found.append(text)
    return tuple(found)


def _supplement(html: str, cabin_id: str) -> int | None:
    """The single-occupancy surcharge for one cabin, found by that cabin's id.

    Attribution by id rather than by position is the whole point. The prose
    sits *after* the cabin's select and before the next cabin, so a parser
    slicing blocks at the select gives every cabin the surcharge belonging to
    the one above it and drops the last cabin's entirely -- a wrong number
    rather than a missing one.
    """
    for prefix in ("private-cabin-help-text", "privacy-optional-help-text"):
        anchor = html.find(f"id={prefix}-{cabin_id}")
        if anchor == -1:
            continue
        match = SURCHARGE.search(_prose(html[anchor:anchor + 4000]))
        if match:
            return int(match.group(1))
    return None




def _listings(html: str) -> list[tuple[re.Match[str], int, int]]:
    """``(name button, start, end)`` for each cabin the page lists, in order.

    A listing runs from its own name button to the next cabin's, and is kept
    only if it holds a price *and* either a guest-count select or the ``FULL``
    marker. Three corroborating signals, because one is not enough: the cabin
    ids reappear later in the document on the dialogs the buttons open, and a
    block that is only an id is a dialog rather than a cabin for sale.
    """
    buttons = list(CABIN_NAME.finditer(html))
    out: list[tuple[re.Match[str], int, int]] = []
    for index, button in enumerate(buttons):
        end = buttons[index + 1].start() if index + 1 < len(buttons) else len(html)
        block = html[button.start():end]
        if not NOW_PRICE.search(block):
            continue
        if not (CABIN_SELECT.search(block) or FULL.search(block)):
            continue
        out.append((button, button.start(), end))
    return out


def parse_cabins(html: str, currency: str) -> CabinReading:
    """Every cabin the booking page lists, in the order it lists them.

    Listings, not offers: a sold-out sailing states each cabin's name, beds and
    price in full and puts ``FULL`` where the guest-count select would be. A
    parser anchored on the select reads nothing at all from such a page, and a
    boat with one full cabin among three would quietly lose a rung of its
    ladder -- including, on the wrong day, the rung the advertised price quotes.

    ``currency`` is what the caller asked the page for. It is not re-derived
    from the glyph beside the price: ``$`` is four currencies the site sells
    in, and a booking page renders whichever the session is set to. Where the
    glyph does contradict the stated currency, that is reported and the
    reading keeps the caller's answer rather than inventing one.
    """
    reading = CabinReading(currency=currency)

    for button, start, end in _listings(html):
        cabin_id = button.group(1)
        block = html[start:end]
        select = CABIN_SELECT.search(block)
        tag = select.group(0) if select else ""

        name = _attr(button.group(0), "title") or ""
        if not name:
            reading.warnings.append(f"cabin {cabin_id}: no name stated; using its id")
            name = f"Cabin {cabin_id}"

        # `data-cabin-occupancy` only exists on the select, so a full cabin has
        # only the "2 People" line. Both are read and they are cross-checked.
        sleeps = _int(_attr(tag, "data-cabin-occupancy"))
        beds = None
        amenities: list[str] = []
        details = DETAILS_LIST.search(block)
        for span, raw in DETAIL_ITEM.findall(details.group(1) if details else ""):
            item = " ".join(unescape(raw).split())
            people = PEOPLE.match(item)
            if people:
                if sleeps is None:
                    sleeps = int(people.group(1))
                elif sleeps != int(people.group(1)):
                    reading.warnings.append(
                        f"cabin {cabin_id}: sleeps {sleeps} by attribute, "
                        f"{people.group(1)} in the text"
                    )
            elif beds is None and (_attr(span, "title") or BEDS.search(item)):
                beds = item
            elif item:
                amenities.append(item)

        now = NOW_PRICE.search(block)
        price, glyph = (None, "")
        if now:
            price, _ = _money(now.group(2))
            glyph = now.group(1).strip()
        listed = LIST_PRICE.search(block)
        list_price, list_glyph = _money(listed.group(1)) if listed else (None, "")

        code = GLYPHS.get(glyph or list_glyph)
        if code and code != currency:
            reading.warnings.append(
                f"cabin {cabin_id}: page shows {glyph or list_glyph} "
                f"({code}), not the {currency} that was asked for"
            )

        sold_out = select is None
        if sold_out:
            # FULL, stated. Zero berths is what the page says, and is a
            # different thing from a berth count it did not state.
            berths: int | None = 0
        else:
            berths = _int(_attr(tag, "data-allocation"))
            claimed = SPACES_LEFT.search(_prose(block))
            if claimed and berths is not None and int(claimed.group(1)) != berths:
                # Three things on the page state this number. Two of them
                # disagreeing is worth a line in the run, not a silent choice.
                reading.warnings.append(
                    f"cabin {cabin_id}: {berths} berths by attribute, "
                    f"{claimed.group(1)} in the banner"
                )

        reading.cabins.append(Cabin(
            cabin_id=cabin_id,
            name=name,
            sleeps=sleeps,
            beds=beds,
            amenities=tuple(amenities),
            price=price,
            list_price=list_price,
            berths=berths,
            sold_out=sold_out,
            single_supplement_pct=_supplement(html, cabin_id),
            shareable=_flag(tag, "data-shareable"),
            occupancy_options=_options(block) if select else (),
        ))

    if reading.nothing_bookable:
        reading.warnings.append(
            f"{len(reading.cabins)} cabin(s) listed, every one full"
        )
    return reading
