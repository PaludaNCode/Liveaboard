"""Parse one trip's itinerary fragment.

A vessel page fetches this when a departure row is clicked:

    /itinerary/getpopupv2?boatID={boat}&tourID={tour}&languageID=1
                         &curr=USD&showPrices=false

Both ids are already in the repository -- every ``Event`` node carries an
``@id`` of the form ``LA-{x}-{boatID}-{tourID}``, which holds for all 878
archived events, and the boatID is constant per vessel across all 67. So no
crawling is needed to build the URL, and a probe confirmed a plain GET returns
the same bytes a browser does: this belongs in the nightly crawl with the
polite fetcher, not in the weekly browser run.

It answers four questions the rest of the pipeline was guessing at or leaving
blank, and all four are the operator's own words about *this trip* rather than
about the boat:

* **Where it goes.** A "Key regions" list, one ``<li title="...">`` per place.
  Sites were previously recovered from the trip title, which is branding on 23
  itineraries -- "Simply the Best" names no reef -- and which put a St John's
  week under a BDE badge because two of that route's three reefs outscored one
  southern site.

* **How many dives.** "Approximately 18 dives in total", stated per trip. The
  dataset's counts were per *vessel* and had to be pinned to one trip length,
  because a boat's weekly figure is wrong on its three-night mini-safari.

* **How many guests.** "Up to 20 guests" for this sailing, where the
  specification table gives the hull's maximum.

* **The entry bar.** "Advanced Open Water - 50 minimum logged dives required."
  A stated safety requirement, which is never softened.

The markup is minified and drops optional closing tags -- ``<dt>Dives <dd>18``
with no ``</dt>`` -- so every pattern here matches up to the next tag rather
than to a closing one.
"""

from __future__ import annotations

import html as html_module
import re
from dataclasses import dataclass, field

REGION_BLOCK = re.compile(
    r"Key\s+regions\s*</h\d>\s*<ol[^>]*>(.*?)</ol>", re.I | re.S
)
"""The curated place list, taken from its heading rather than by class.

Tailwind class strings on this page carry brackets, ampersands and colons and
change with the layout; the heading text does not.
"""

REGION_ITEM = re.compile(r"<li[^>]*\stitle=(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.I)
"""One region. Read from the ``title`` attribute, which is the bare name.

The element's text carries the same words plus whatever whitespace the
minifier left, and the attribute is quoted only when it contains a space --
``title=Daedalus`` beside ``title="Abu Dabab"`` -- so all three forms match.
"""

TOO_BROAD = frozenset({"red sea", "egypt", "northern red sea", "southern red sea"})
"""Regions that name the whole sea rather than anywhere in it.

Every trip in the dataset is in the Red Sea, so carrying it as a site would put
a filter chip on the page that selects all 314 rows.
"""

FIELD = re.compile(
    r"<dt[^>]*>\s*{label}\s*<dd[^>]*>\s*(?P<value>[^<]*)", re.I
)
"""Template for one Overview row. Formatted with a label below."""

DIVES = re.compile(r"(\d{1,3})\s+dives?\b", re.I)
GUESTS = re.compile(r"(\d{1,3})\s+guests?\b", re.I)
LOGGED = re.compile(r"(\d{1,4})\s+minimum\s+logged\s+dives?", re.I)

EXPERIENCE = re.compile(
    r"<strong>\s*Experience\s*</strong>\s*<span>\s*(?P<value>[^<]+)", re.I
)
"""The entry bar as the sidebar states it.

There is a second copy in the Overview grid for narrow screens; either will do,
so the Overview row is the fallback when this one is absent.
"""

MAX_GUESTS = 60
"""Above this the number is not a guest count. Same bound as the spec table."""

MAX_DIVES = 60
"""A fortnight at four a day is 56. More than this is a misparse."""


@dataclass(frozen=True, slots=True)
class TripDetail:
    """What one itinerary fragment states about the trip."""

    regions: tuple[str, ...] = ()
    dives: int | None = None
    guests: int | None = None
    experience: str | None = None

    def __bool__(self) -> bool:
        return bool(self.regions or self.dives or self.guests or self.experience)


def _text(value: str) -> str:
    return " ".join(html_module.unescape(value).split())


def _field(markup: str, label: str) -> str | None:
    """One Overview row's value, by its label."""
    pattern = re.compile(FIELD.pattern.replace("{label}", re.escape(label)), re.I)
    match = pattern.search(markup)
    return _text(match.group("value")) if match else None


def _bounded(text: str | None, pattern: re.Pattern[str], limit: int) -> int | None:
    if not text:
        return None
    match = pattern.search(text)
    if not match:
        return None
    number = int(match.group(1))
    return number if 0 < number <= limit else None


def parse_regions(markup: str) -> tuple[str, ...]:
    """The operator's own list of where the trip goes."""
    block = REGION_BLOCK.search(markup or "")
    if not block:
        return ()
    out: list[str] = []
    for match in REGION_ITEM.finditer(block.group(1)):
        name = _text(match.group(1).strip("\"'"))
        if not name or name.lower() in TOO_BROAD or name in out:
            continue
        out.append(name)
    return tuple(out)


def parse_trip(markup: str) -> TripDetail:
    """Read one fragment. Returns an empty record rather than guessing."""
    if not markup:
        return TripDetail()

    experience = None
    match = EXPERIENCE.search(markup)
    if match:
        experience = _text(match.group("value"))
    else:
        experience = _field(markup, "Experience")

    return TripDetail(
        regions=parse_regions(markup),
        # "Approximately 18 dives in total" -- the word matters, so the figure
        # stays a floor the way the vessel-level counts already are.
        dives=_bounded(_field(markup, "Dives"), DIVES, MAX_DIVES),
        guests=_bounded(_field(markup, "Group Size"), GUESTS, MAX_GUESTS),
        experience=experience or None,
    )


def min_logged_dives(experience: str | None) -> int:
    """The logged-dive bar an experience line states, or zero.

    Zero means the line named no number, not that the operator asks for none:
    "Advanced Open Water" alone is a certification requirement with no dive
    count attached, and inventing one would soften a stated safety requirement.
    """
    return _bounded(experience, LOGGED, 9999) or 0
