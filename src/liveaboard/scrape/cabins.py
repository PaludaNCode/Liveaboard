"""What a departure actually costs, cabin by cabin.

The dataset stores one price per sailing -- the figure the vessel page
advertises. The booking page shows what that figure is the *bottom of*:

    Cabin 3      2 People, 1 Bunk bed    $688 -> $619 /person   only 2 spaces left!
    Cabin 1 & 2  2 People, 1 Bunk bed    $757 -> $682 /person   only 4 spaces left!
    Suite        2 People, 1 Double bed  $874 -> $787 /person   only 2 spaces left!

So the advertised price is the cheapest bunk in the cheapest cabin, and on that
sailing exactly two of them exist. A diver who wants the Suite pays $787, and a
diver who wants it alone pays that plus a stated 60% single supplement. That is
this site's own argument one level below where it currently stops.

**Read from `/BookingStep1?tourid={tour}&boatid={boat}`**, one plain GET, no
browser. Both ids come out of `Event.@id` (`LA-{x}-{boatID}-{tourID}`), which
the archive holds for every departure, so no crawling is needed to build the
URL -- the same trick that made the itinerary fragment affordable.

Three things this module is careful about, because they are the ways a price
column starts lying:

* **A count is what the page said, not what is true.** "only 2 spaces left!"
  sits beside "Save 10%" and "Good catch! You found the last available spot",
  and it is red marketing text as much as inventory. It is stored as the
  operator's claim with the date it was read, and the page must present it that
  way. It is also the most perishable thing here: true at fetch, stale by
  morning.
* **Two prices, and which is which matters.** The struck-through figure is the
  list price and the bold one is what you would pay today. Storing one and
  calling it "the price" would either overstate the cost or hide a discount
  that may not last.
* **Nothing is derived.** No cabin total from a per-person price, no capacity
  from a bed description, no single-occupancy figure from a percentage in
  prose. A field the page does not state stays ``None``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from typing import Any

# Each cabin's controls carry its own id, in two places: the details dialog the
# name button opens, and the guest-count select. The select is the anchor --
# every bookable cabin has one, and the dialog markup is repeated elsewhere on
# the page, so splitting on the dialog id double-counts.
CABIN_SELECT = re.compile(r"<select[^>]*\bname=input-cabin-guests-(\d+)", re.I)

# The name, from the button that opens the cabin's own dialog. `title` rather
# than the button text: the text is inside a truncating element and the title
# is what the site itself considers the full name.
CABIN_NAME = re.compile(
    r"aria-controls=help-content-cabin-details-(\d+)[^>]*\btitle=\"([^\"]+)\"", re.I
)

# The struck-through list price and the price actually charged. `translate=no`
# marks both -- it is on the numbers and nothing else, which makes it a better
# anchor than the Tailwind classes around it, those being generated.
LIST_PRICE = re.compile(r"<del[^>]*>\s*([^<]*?\d[\d,.]*)\s*</del>", re.I)
NOW_PRICE = re.compile(
    r"<em[^>]*>\s*([^\s<]{1,3})\s*</em>\s*<span[^>]*translate=no[^>]*>\s*([\d,.]+)\s*</span>",
    re.I,
)

SPACES_LEFT = re.compile(r"(?:only\s*)?(\d{1,2})\s*spaces?\s*left", re.I)
SOLD_OUT = re.compile(r"\b(?:fully\s+booked|sold\s*out|no\s+more\s+spaces)\b", re.I)

# "a 60% surcharge applies for single occupancy". Stated in prose, per cabin,
# and the reason a solo diver's real price is not the per-person one.
SINGLE_SUPPLEMENT = re.compile(r"(\d{1,3})\s*%\s*surcharge[^.]*single occupancy", re.I)

# What the cabin sleeps and in what, from the <ol> under the name.
DETAIL_ITEM = re.compile(r"<li[^>]*>\s*<span[^>]*>([^<]+)</span>", re.I)
PEOPLE = re.compile(r"^\s*(\d{1,2})\s*(?:People|Person|Guests?)\s*$", re.I)

TAG = re.compile(r"<[^>]+>")


@dataclass(frozen=True, slots=True)
class Cabin:
    """One cabin type on one departure, as the booking page states it."""

    cabin_id: str
    name: str
    sleeps: int | None
    """How many the cabin takes. ``None`` where the page does not say."""
    beds: str | None
    amenities: tuple[str, ...]
    price: float | None
    """What one person pays today, in the page's currency."""
    list_price: float | None
    """The struck-through figure, where the cabin is discounted."""
    currency: str
    spaces_left: int | None
    """**The operator's claim**, not a verified count, and perishable."""
    sold_out: bool
    single_supplement_pct: int | None
    occupancy_options: tuple[str, ...]

    @property
    def is_discounted(self) -> bool:
        return (self.list_price is not None and self.price is not None
                and self.list_price > self.price)

    def as_dict(self) -> dict[str, Any]:
        """Only what is stated. Nulls and falses are dropped, so a field that
        is absent is a field the page did not answer rather than a zero."""
        out: dict[str, Any] = {"cabin_id": self.cabin_id, "name": self.name}
        for key, value in (
            ("sleeps", self.sleeps), ("beds", self.beds),
            ("price", self.price), ("list_price", self.list_price),
            ("spaces_left", self.spaces_left),
            ("single_supplement_pct", self.single_supplement_pct),
        ):
            if value is not None:
                out[key] = value
        if self.price is not None or self.list_price is not None:
            out["currency"] = self.currency
        if self.amenities:
            out["amenities"] = list(self.amenities)
        if self.occupancy_options:
            out["occupancy_options"] = list(self.occupancy_options)
        if self.sold_out:
            out["sold_out"] = True
        return out


def _select_end(html: str, select: re.Match[str]) -> int:
    closed = html.find("</select>", select.start())
    return closed + len("</select>") if closed != -1 else select.end()


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
    """The guest counts this cabin can be booked for."""
    found = []
    for raw in re.findall(r"<option[^>]*>(.*?)</option>", block, re.I | re.S):
        text = _prose(raw)
        # The placeholder is the site's own "-", not an occupancy.
        if text and text != "-":
            found.append(text)
    return tuple(found)


def parse_cabins(html: str, default_currency: str = "USD") -> list[Cabin]:
    """Every cabin the booking page offers, in the order it lists them.

    Each cabin is split in two by its own guest-count select: the name, beds,
    both prices and the berth count sit above it, and the occupancy prose --
    which is where the single-occupancy surcharge is stated -- sits below it,
    before the next cabin begins. So a block cut *at* the select gets every
    cabin's surcharge from the cabin above it, which is a wrong number rather
    than a missing one.

    Blocks are anchored on the ids the site itself keys cabins on rather than
    on a container class: the page is Tailwind, so its class names are
    generated and a parser pinned to them is a parser pinned to a stylesheet.
    """
    selects = list(CABIN_SELECT.finditer(html))
    if not selects:
        return []

    cabins: list[Cabin] = []
    for index, select in enumerate(selects):
        cabin_id = select.group(1)
        closed = html.find("</select>", select.start())
        select_end = closed + len("</select>") if closed != -1 else select.end()
        previous_end = 0 if index == 0 else _select_end(html, selects[index - 1])

        # Start the block at this cabin's own name button where there is one.
        # Without that anchor the first cabin's block would begin at the top of
        # the document and take the header's prices for its own.
        head_start = previous_end
        for match in CABIN_NAME.finditer(html[previous_end:select.start()]):
            if match.group(1) == cabin_id:
                head_start = previous_end + match.start()
        block = html[head_start:select_end]

        # Everything between this select and the next cabin: the site's prose
        # about sharing, privacy and what a single occupant is charged.
        tail_end = selects[index + 1].start() if index + 1 < len(selects) else len(html)
        tail = _prose(html[select_end:tail_end])

        name = ""
        for match in CABIN_NAME.finditer(block):
            if match.group(1) == cabin_id:
                name = unescape(match.group(2)).strip()
        if not name:
            names = CABIN_NAME.findall(block)
            name = unescape(names[-1][1]).strip() if names else f"Cabin {cabin_id}"

        details = [" ".join(unescape(d).split()) for d in DETAIL_ITEM.findall(block)]
        sleeps = None
        beds = None
        amenities: list[str] = []
        for item in details:
            people = PEOPLE.match(item)
            if people and sleeps is None:
                sleeps = int(people.group(1))
            elif re.search(r"\bbeds?\b", item, re.I) and beds is None:
                beds = item
            elif item:
                amenities.append(item)

        now = NOW_PRICE.search(block)
        price, currency = (None, "")
        if now:
            price, _ = _money(now.group(2))
            currency = now.group(1).strip()
        listed = LIST_PRICE.search(block)
        list_price, list_currency = _money(listed.group(1)) if listed else (None, "")

        text = _prose(block)
        spaces = SPACES_LEFT.search(text)
        supplement = SINGLE_SUPPLEMENT.search(tail)

        cabins.append(Cabin(
            cabin_id=cabin_id,
            name=name,
            sleeps=sleeps,
            beds=beds,
            amenities=tuple(amenities),
            price=price,
            list_price=list_price,
            currency=currency or list_currency or default_currency,
            spaces_left=int(spaces.group(1)) if spaces else None,
            sold_out=bool(SOLD_OUT.search(text)),
            single_supplement_pct=int(supplement.group(1)) if supplement else None,
            occupancy_options=_options(block),
        ))
    return cabins
