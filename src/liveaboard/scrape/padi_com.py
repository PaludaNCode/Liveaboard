"""Adapter for padi.com.

Status: **discovery and identity are verified against live pages; the entry bar
itself is not yet reachable.** A probe on 2026-08-28 read the Egypt vessel pages
on `travel.padi.com` and settled three things.

Discovery needs no crawl and no browser. The operator sitemap enumerates all 269
liveaboards, 58 of them Egyptian, so there is no listing to page through --
which matters, because the listing at `/s/liveaboards/egypt/` is a Next.js app
that server-renders a count and nothing else.

Vessel pages *are* server-rendered, and carry the vessel, its fleet, and every
itinerary title the operator sells. That is the identity half of the join.

The entry bar is not in that HTML. PADI stores it per itinerary as two coded
enums, and the page ships only their vocabulary -- reproduced verbatim below --
while the values arrive over an AngularJS XHR whose bundle is CDN-hosted. So
this module can map the vocabulary, and does; nothing here reads a value yet.

One trap is load-bearing enough to state up front: **an itinerary slug is an
opaque id, never a fact.** Fifteen of Hammerhead II's twenty-two slugs
contradict the page they serve -- `mini-wrecks-and-nature-hurghada-hurghada-5-
nights` answers with *Brothers Light 3 (Marsa Alam - Marsa Alam) 3 Nights*.
Reading nights, ports or reefs out of a URL here produces confident nonsense.

See `docs/sources/padi.com.md`.

PADI plays a different role in this dataset than liveaboard.com does. It is
strong on the things the price comparison needs in order to be fair: which
certification a trip demands, how many logged dives, what the operator is
accredited to run, and a fee book stated per itinerary rather than per vessel.
So this adapter is scoped to *requirements, fees and sailings* rather than to
being a second crawl of everything.

Its output mostly enriches itineraries matched from the other source. It is no
longer true that it never creates departures: of the 654 PADI sailings inside
the published season, 601 land on a row we already had and 53 do not, and those
53 are real, bookable trips -- Blue Storm's and Blue Seas' near-complete weekly
seasons among them -- that liveaboard.com does not list. `promote` creates a
row for each. A trip nobody asked about is not a trip that does not exist,
which is the same rule that stops an unreadable vessel page emptying a month.

Keeping the crawl narrow is still the point, and still the polite thing to do
when the useful part of a site is a fraction of its pages.
"""

from __future__ import annotations

import re
from typing import Callable, Iterator, Mapping, Sequence

from html import unescape
from urllib.parse import urlencode

from .base import FetchResult, ScrapeError, ScrapeOutput, SourceAdapter
from . import jsonld
from ..taxonomy import DiverLevel, FeeBasis, FeeCode, FeeTier
from .fees import _tier_for, classify_label, tier_for_inclusion
from .vessel import MAX_GUESTS

MAX_LENGTH_M = 200
"""A hull longer than this is a parse, not a boat.

The Egyptian fleet's longest is 48 m. The bound exists so a stray figure in a
value the label did not describe cannot land in the dataset as a length.
"""

# `.fees` used to be imported lazily, inside the three functions below that need
# it, and that cost a 530-request crawl 2026-08-30. `taxonomy` is imported above
# at module load; `fees.py` was not, so it compiled *fresh* at the book-building
# step -- forty minutes into the run -- against the `taxonomy` already sitting in
# `sys.modules`. A `FeeCode` member added to the source meanwhile existed in the
# new `fees.py` and not in the loaded enum, and the run died on `AttributeError`
# with every page fetched and nothing written.
#
# So: a module a long fetch depends on is imported before the fetch starts, not
# after it. Then the process holds one consistent snapshot of the code from its
# first line and an edit to a source file cannot reach into a run in flight.
# `fees` imports only `taxonomy`, `re` and `dataclasses`, so there is no cycle to
# be avoided here and never was -- the laziness bought nothing.
#
# `--rebuild` exists because of that run and is the other half: re-parsing the
# cached raw store must never mean re-crawling somebody else's 530 pages.

HOST = "travel.padi.com"
"""PADI Travel is its own host. `www.padi.com/travel` redirects here, and the
`www` sitemap -- 3304 URLs of certification and dive-centre pages -- contains no
travel URL at all."""

OPERATOR_SITEMAP = f"https://{HOST}/sitemap-travel-dive-operators-page_1.xml"
"""Every liveaboard and dive resort PADI Travel sells, in one 3 MB file.

This replaces a listing crawl rather than seeding one. The search page cannot be
paged through without a browser, and does not need to be.
"""

COUNTRY = "egypt"

API_BASE = f"https://{HOST}/api/v2/travel"
"""Where PADI Travel's own JSON lives.

Found by reading `itinerary.*.js` on a runner (`tools/probe_padi_bundle.py`).
Its API client resolves a relative endpoint as ```${origin}/api/v2/travel/${e}`
`` unless the path names the adventure or account service, so the prefix is
never spelled out in one literal -- which is why eight guessed bases, `/api/`
and `/api/v2/` among them, all 404 before the bundle was read.

**Unauthenticated, and needs no headers at all.** No token, no CSRF cookie, no
`X-Requested-With`: a plain GET answers 200 with JSON. Nothing under
`/api/v2/travel/` is disallowed by robots.
"""

ITINERARY_LIST = API_BASE + "/shop/{vessel}/itineraries/?kind=10"
"""Every itinerary one vessel sells: title, slug, id, dive-count range.

Paginated DRF -- ``{"count": 22, "next": null, "results": [...]}`` -- and one
request per vessel, which is 58 for Egypt.
"""

ITINERARY_DETAIL = API_BASE + "/shop/{country}/{vessel}/itineraries/{slug}/"
"""One itinerary, 95 fields, and the only place the entry bar is stated."""

PROMOTIONS = API_BASE + "/promotions/"
"""What PADI Travel is discounting: the deals page, without the deals page.

`/liveaboard-deals/` is an AngularJS shell -- 272 KB of chrome, zero prices,
`page=` reflected in `og:url` and never acted on, so page 99 serves page 1 --
and its bundle is `special_deals.*.js` on the CDN the sandbox cannot reach.
This is the XHR behind it, and it takes **the deals page's own query verbatim**:
repeated `country=` ids, repeated `date=` months, and a `page=` that works.

Paging is honest here in a way the HTML is not: on a 24-row query `page=2`
returns the last four with `next: null`, and `page=3` answers 404 rather than
recycling page 1. The fetcher still terminates on offer identity rather than on
either signal -- see `tools/fetch_deals.py` -- because a listing that lies about
its own paging once has no standing to be trusted about it twice.

One row per vessel per query, quoting the vessel's earliest promoted sailing in
the window. So a boat's deal is the unit, not a sailing's.
"""

DEAL_COUNTRIES: tuple[int, ...] = (110, 120)
"""The countries the deals query asks for: 110 is the USA, 120 is Egypt.

Both on purpose, and the reason is the whole of why this cannot be filtered on
PADI's side. **Some Egyptian boats are filed under the USA** -- all three Red Sea
Aggressors are, because Aggressor Fleet is American -- so asking for Egypt alone
silently drops them, which is the failure this query exists to avoid.

Asking for the USA as well is coarse rather than clever: of 18 deals in the
published season it also returns Bahamas, Belize (twice), Cayman and Roatan,
which sail nowhere near the Red Sea. That is not a flaw in the query, it is the
measurement -- 5 of those 18, so **more than a quarter** of what PADI's country
field returns here is somewhere else entirely. The field cannot place a deal and
nothing downstream asks it to: `promote` joins the deal's vessel to a boat of
ours and lets that decide.
"""

DEAL_MAX_PAGES = 25
"""A cap, because a listing has to be able to end even when it will not say so.

The HTML this endpoint sits behind serves page 1 for every value of `page`, so
a loop that trusted a page number there would never stop. This endpoint does
page properly, and the loop still does not trust it: it stops when a page adds
no offer it has not already seen, and this is the backstop under that. Twenty-
five pages is 500 offers, against 18 in the published season and 97 worldwide.
"""

PROMOTION_KIND: dict[int, str] = {
    10: "Fixed amount",
    20: "Discount %",
    30: "Free night(s)",
    40: "Other",
}
"""`PROMOTION_KIND`, verbatim from `window.info.promotions` on any travel page.

Kept as PADI's own words rather than folded into a percentage: a free night and
a third off are different offers, and the money saved is stated separately in
`compareAtPrice` anyway. `value` means whatever the kind says it means, which is
why neither is read as the other.
"""

VESSEL_URL = re.compile(
    rf"https://{re.escape(HOST)}/liveaboard/(?P<country>[a-z0-9-]+)/(?P<slug>[a-z0-9-]+)/"
)
"""A vessel page in the sitemap. Localised paths (`/de/tauchsafari-tauchen/...`)
are five per vessel and deliberately not matched: one language is enough."""

# The vessel page's itinerary nav, which is where the operator's own trip titles
# live. Paired by the anchor that carries them, never by the slug: see the
# module docstring on why a slug is not evidence.
ITINERARY_NAV = re.compile(
    r'href="/liveaboard/[a-z0-9-]+/[a-z0-9-]+/(?P<slug>[a-z0-9-]+)/"[^>]*>(?P<title>[^<]+)</a>',
    re.IGNORECASE,
)

TRIP_TITLE = re.compile(
    r"^(?P<name>.+?)\s*\((?P<ports>[^)]*)\)\s*(?P<nights>\d+)\s*Nights?$", re.IGNORECASE
)
"""PADI's trip title: "Name (Port - Port) N Nights".

Minus the night count that is our own `Itinerary.name`, ports included, which is
what makes the two sources joinable without inventing a key.
"""

ONLY_NIGHTS = re.compile(r"[\s\u2010-\u2015-]*(?:\d+\s*nights?[\s\u2010-\u2015-]*)+", re.IGNORECASE)
"""A tail that is nothing but night counts, possibly repeated.

Used to decide whether two disagreeing counts are a contradiction to refuse or a
trip length followed by something else -- a hotel night, a day count.
"""

DASHES = dict.fromkeys(map(ord, "\u2010\u2011\u2012\u2013\u2014\u2015"), "-")
"""Every dash PADI uses, folded to a hyphen. Their titles mix them freely."""

ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\ufeff"))
"""Both sources carry zero-width spaces inside operator titles -- "Red Sea
Charm\u200b:" reaches us that way from liveaboard.com too. They are part of the
string; discarding them is a comparison's job, not a reader's."""

CERTIFICATION_CHOICES: dict[int, DiverLevel] = {
    10: DiverLevel.OPEN_WATER,       # "Open Water"
    20: DiverLevel.OPEN_WATER,       # "Open Water + Nitrox"
    30: DiverLevel.ADVANCED,         # "Advanced Open Water"
    40: DiverLevel.ADVANCED,         # "Advanced Open Water + Nitrox"
    50: DiverLevel.EXPERIENCED_100,  # "Tec Diver"
}
"""`ITINERARY_CERTIFICATION_CHOICES`, read verbatim off a live vessel page.

Nitrox rides along with the certification in PADI's vocabulary but is a gas, not
an entry bar, so 10 and 20 land on the same level and so do 30 and 40. Whether
the trip charges for nitrox is a fee question, and `pricing.py` already answers
it from the source that quotes a number.
"""

PAYED_PER: dict[int, FeeBasis] = {
    0: FeeBasis.PER_PERSON_PER_DAY,  # "Day/Person"
    10: FeeBasis.PER_NIGHT,          # "Night/Person"
    20: FeeBasis.PER_DIVE,           # "Dive"
    30: FeeBasis.PER_TRIP,           # "Trip"
    40: FeeBasis.PER_DAY,            # "Diving day"
    70: FeeBasis.PER_WEEK,           # "Week"
}
"""`LIVEABOARD_EXTRA_PAYED_PER`, read verbatim off a live vessel page, and
deliberately only the part of it that is a per-person trip charge.

The enum has eighteen members. The twelve missing here are transfers ("From,
per vehicle", "Return, per person"), courses, activities, an "Offset", and two
priced per *cabin* -- "Day/Cabin" and "Night/Cabin". None of them normalises to
what this dataset compares, which is one diver's bill for one trip: a per-cabin
charge needs an occupancy nobody publishes, and a transfer is not part of the
sailing. `basis_for` returns ``None`` for all of them rather than guessing, and
a mandatory charge whose basis will not normalise makes the whole PADI bill
incomplete rather than being quietly dropped from it. Same rule as an extra
listed with no price: what cannot be added up is not zero.
"""


MANDATORY_FIELDS: tuple[str, ...] = ("mandatoryOnBoard", "mandatoryInAdvance")
"""Where PADI keeps the charges a diver cannot decline.

**Membership of these two lists is the fact; `section` and `kind` are not.**
Every entry in them carries ``isMandatory: true`` and ``isIncluded: false``, so
the field name is the claim. The codes beside it are unreliable in the way the
itinerary slugs are: All Star Ghani's "Marine Park/Port Fees" is filed under
``section: 10`` ("Information") and ``kind: 10`` ("Full board, including"), and
reading either would have made a €200 park fee into a meal. 333 of the 623
entries are ``kind: 600`` ("Other fees"), which says nothing at all. The title
is the only field that describes the charge, and it is classified with the same
table liveaboard.com's wording goes through.
"""


OPTIONAL_FIELDS: tuple[str, ...] = (
    "optionalOnBoard",
    "optionalInAdvance",
    "optionalBookableAdvancePaidOnBoard",
)
"""Where PADI keeps the charges a diver can decline -- and nothing read them.

The third half of a disclosure this module was reading two-thirds of. It had
`MANDATORY_FIELDS` for what PADI charges on top and `INCLUDED_FIELD` for what
it says is already in, and the *Optional* lists -- the ones holding nitrox and
gear hire, the two extras this site puts a toggle on -- were not opened at all.
liveaboard.com's parser has read the same half of its own disclosure since the
beginning (`parse_extras` reads the Required and Optional blocks together), so
this is one seller's book being read at a shallower depth than the other's,
which is the failure `INCLUDED_FIELD` was added to fix on the other side.

Bella 2 is what it costs. PADI states 50 EUR for nitrox on the trip and 40 EUR
per diving day for the full scuba set; both were absent from the page, on a
vessel where PADI's book is the only fee book there is -- liveaboard.com sells
no berth on it, so nothing else could fill them in.

Membership is the claim here too: every entry in these lists carries
``isMandatory: false``, and the tier a charge lands in is then this project's
own -- `_tier_for` files nitrox and gear as conditional because the page's
toggles govern them, gratuities as customary, and the rest as optional.

**They cannot make a bill incomplete.** `complete` is a verdict on the
mandatory charges: a course nobody can classify, or a transfer priced per
vehicle, says nothing about whether what a diver *must* pay adds up. Same rule
as the inclusions, and for the same reason -- letting an amenity block a total
took the book from 259 complete trips to none.
"""

SEASON: tuple[str, str] = ("2027-05-01", "2027-08-31")
"""The window the site publishes, as the fee book has to be read against it.

Only a default. `fetch_padi.py` takes `--season-start` / `--season-end` like
`fetch_deals.py`, so a season that moves moves here too.
"""


def _in_season(entry: dict[str, object], season: tuple[str, str]) -> bool:
    """Whether a fee entry's stated validity window reaches the season.

    **A charge PADI priced for last year is not this year's charge, and the
    payload keeps both.** Grand Sea Explorer lists "Route supplement" twice on
    every trip -- 300 valid to 2026-12-31, 400 valid from 2027-01-01 -- and
    DUNE Longara lists "Environmental taxes" at 100 and 200, the second taking
    over on 2026-06-14. Nothing read `validFrom`/`validTo`, so the bill got
    whichever the parser happened to keep.

    A comment here used to reason the opposite way: that two entries under one
    title are two charges the operator bills, "no pair in the book is an exact
    duplicate". The dates were in the same payload and refute it. Across the
    whole store there are **69 such pairs, and every one resolves to exactly a
    single entry valid in the published season** -- not one has two. They are
    one charge repriced, and they sit on the largest mandatory lines there are:
    the combined park/port/fuel charge, conservation fees, the environmental
    tax.

    **Silence is not expiry**, as everywhere else here: 750 of the 896 entries
    state no window at all and every one of them is kept. Only an entry that
    states a window the season cannot reach is dropped, which is the source
    saying this price stopped applying before the trip sails.
    """
    start, end = season
    valid_from = str(entry.get("validFrom") or "")[:10]
    valid_to = str(entry.get("validTo") or "")[:10]
    if valid_to and valid_to < start:
        return False
    return not (valid_from and valid_from > end)


def _money(entry: dict[str, object], currency: str) -> dict[str, object] | None:
    """What one fee entry costs, or ``None`` when it does not say.

    Two fields, in one order that is not a preference but a measurement: see
    `EXTRA_VALUE`. ``price`` is the maintained number and wins whenever it is
    one; ``extraValue`` is read only where ``price`` is null, and only when the
    whole string is a figure.

    Returns the ``amount``/``amount_max`` pair `FeeItem.from_dict` expects, so a
    range PADI types into the string keeps both ends the way liveaboard.com's
    ranges do -- collapsing one to its low end understates the bill, which is
    the failure this project exists to correct.
    """
    price = entry.get("price")
    if isinstance(price, (int, float)) and not isinstance(price, bool):
        return {"amount": {"amount": float(price), "currency": currency}}

    raw = entry.get("extraValue")
    match = EXTRA_VALUE.match(str(raw)) if isinstance(raw, str) else None
    if not match:
        return None
    low = _number(match.group("low"))
    if low is None:
        return None
    stated = (match.group("currency") or "").upper()
    if stated and stated not in CURRENCY_TOKENS:
        # A currency this parser cannot name is money it cannot add up. One
        # vessel writes "8 EU"; the entry stays unpriced rather than being
        # assumed into euro.
        return None
    money: dict[str, object] = {
        "amount": {"amount": low, "currency": stated or currency}
    }
    # A range keeps both ends, as liveaboard.com's do. It fires on nothing
    # today -- the only ranged strings in the store are two transfer entries
    # reading "55-75", and `PAYED_PER` declines their unit before this is
    # reached -- but a range collapsed to its low end understates a bill, which
    # is not a thing to leave to whether the source happens to reorganise.
    high = _number(match.group("high"))
    if high is not None and high != low:
        money["amount_max"] = dict(money["amount"], amount=high)
    return money


def _number(raw: str | None) -> float | None:
    """A figure typed into a string, or ``None``. Thousands separators only.

    Shared with `fees._number` in intent and kept local in fact: this reads a
    field, that one reads a page, and the two must be free to differ about what
    a stray character means.
    """
    if raw is None:
        return None
    try:
        return float(raw.replace(",", "").rstrip("."))
    except ValueError:
        return None


INCLUDED_FIELD = "whatsIncludedNew"
"""What PADI says the fare already covers, on 447 of 447 itineraries.

The other half of a disclosure, and the site was reading only one of them: what
PADI charges *on top* (`MANDATORY_FIELDS`) and not what it says is already in.
Those are different claims, and **included fees stay in the breakdown at zero**
by invariant -- removing them hides the difference between a bundled operator
and one that bills at the dock. Two bills in one expanded row were disclosing
at different depths with nothing saying which was the shallower.

A note that used to sit here said the rule "was being kept on liveaboard.com's
side only". It was not: that seller prints an `Included:` block above the two
`fees.BLOCK` was reading and nothing opened it either, on all 79 vessel pages.
Both sellers are read for it now, through `fees.tier_for_inclusion`, which is
what makes the two comparable rather than merely both present.

Membership is the claim, as with `MANDATORY_FIELDS`: nothing in an entry says
"included", the field it sits in does. `section` and `kind` are the same
fossils they are next door -- a marine park charge is filed under "Full board,
including" -- so the title is again the only field that describes the thing.
"""

CURRENCY_TOKENS: frozenset[str] = frozenset({"EUR", "USD", "GBP"})
"""Currency names this parser will read out of a free-text amount.

Deliberately a closed list. `extraValue` is typed by hand and one vessel writes
"8 EU", which plainly means euro and is plainly not a currency code -- and a
rule loose enough to accept it is loose enough to accept the next thing that
only looks like one. Those entries keep no amount, which is where they already
were.

Codes only, no symbols: not one `extraValue` in the store contains €, $ or £,
so a symbol here would be a rule about a shape nobody has seen. The three codes
are the three currencies the vessels quote in.
"""

EXTRA_VALUE = re.compile(
    r"""^\s*
    (?P<low>\d[\d.,]*)
    (?:\s*[-\u2013]\s*(?P<high>\d[\d.,]*))?
    \s*(?P<currency>[A-Za-z]{2,3})?
    \s*$""",
    re.X,
)
"""A price PADI states as a string rather than as a number.

**`price` is null on 236 of the fee book's 872 mandatory entries, and
`extraValue` states the figure on 133 of those.** Bella 2's Coast Guard Fee is ``price: null, extraValue: "5 EUR"`` and
its Service fees ``price: null, extraValue: "10 EUR"``: two of the three
mandatory charges on every one of that boat's trips, read as unpriced, on a
vessel whose PADI book is the only fee book the site has. The docstring in
`fees_from_payload` used to call these entries "unpriced, exactly as a third of
the liveaboard.com book is". They are not; nobody had read the other field.

**`price` still wins wherever it is set**, because where the two disagree
`extraValue` is the stale one. Blue Horizon states ``price: 56`` against
``extraValue: "8"`` -- 8 a night over a seven-night trip, kept beside the total
that replaced it, and still 56 on the boat's ten-night sailings. Blue Melody's
same charge has ``extraValue: "USD"`` with no number in it at all, and Andromeda
prices its fuel surcharge at 50 per week against an ``extraValue`` of "30 EUR".
So this is a fallback and never a second opinion.

Anchored at both ends, because the whole risk in the field is that it is prose:
"14% GST (on onboard purchases)" must not become 14 of anything, a bare "USD"
must not become a price, and 43 mandatory fuel surcharges reading
*"10 - 20 USD To be confirmed 30 days before the trip"* are an operator saying
the figure is not settled yet -- those bills stay incomplete, which is the
answer. A currency it states is the currency -- Andromeda
writes "5 USD" on a vessel PADI prices in EUR, and the vessel's currency is only
assumed where the string names none.
"""

PARENTHETICAL = re.compile(r"\s*\([^)]*\)")
"""A qualifier, never the name of the charge.

The inclusions list is prose where the mandatory list is labels: *"Transfer
from/to the airport (round-trip, only on boat arrival & departure days)"*. The
bracket qualifies what is included; it does not say what it is.

It matters exactly once, and expensively. **"Airport Meet & Greet (VISA
assistance, eligible countries only)"** classified as `visa`, which would have
published *"Egypt visa on arrival -- included"* on eight itineraries. Help with
the paperwork is not the €25 the diver still pays at the airport, and this
project telling somebody a government charge is bundled when it is not is the
whole failure it exists to report in other people.

Measured across all 63 distinct titles the field uses: stripping the
parenthetical changes the answer on that one and on nothing else. A rule rather
than an entry in a table of exceptions, because it says something true about
the shape of the field -- and the table would only have caught the wording
already seen.
"""


EXPERIENCE_DIVES: dict[int, int] = {0: 0, 10: 20, 20: 50, 30: 100}
"""`EXPERIENCE_REQUIRED_DIVES`, likewise verbatim.

PADI words every one of these as *recommended* -- "50+ dives recommended" --
and a recommendation is not a gate. It is reported separately from
`min_logged_dives` for that reason: hardening somebody's advice into a
requirement is the same class of error as softening their requirement into
advice, and this project does neither.
"""

CERT_PATTERNS: tuple[tuple[re.Pattern[str], DiverLevel], ...] = (
    (re.compile(r"\b(master\s+scuba|divemaster)\b", re.I), DiverLevel.EXPERIENCED_100),
    (re.compile(r"\badvanced\s+open\s+water\b|\baowd?\b", re.I), DiverLevel.ADVANCED),
    (re.compile(r"\bopen\s+water\b", re.I), DiverLevel.OPEN_WATER),
)

DIVES_PATTERN = re.compile(
    r"(?:minimum\s+of\s+|min\.?\s*|at\s+least\s+)?(\d{2,3})\s*(?:\+\s*)?logged\s+dives",
    re.IGNORECASE,
)
"""Matches the industry's stock phrasings: "50 logged dives", "minimum of 50
logged dives", "100+ logged dives"."""


class PadiComAdapter(SourceAdapter):
    """Reads certification and experience prerequisites from PADI Travel."""

    source_id = "padi.com"
    host = HOST

    country = COUNTRY

    @staticmethod
    def vessel_for(boat_id: str, aliases: dict[str, object]) -> str | None:
        """Which PADI slug is this boat of ours, or ``None``.

        Keyed on our ``boat_id``, which is what `promote` builds every itinerary
        id from and so the one identifier that cannot quietly drift. Keying on a
        folded *name* got the first entry in the map wrong: "MY Odyssey
        Liveaboard" has boat_id ``odyssey``.

        Three answers, and they are deliberately different:

        - a slug, for a boat somebody has paired;
        - ``None`` for a boat listed in `absent`, meaning somebody looked and
          PADI does not sell it;
        - ``None`` for a boat in neither, which the caller should report as
          unreviewed rather than treat as absent.

        Use :meth:`is_reviewed` to tell the last two apart. A boat nobody has
        looked at and a boat confirmed missing produce the same empty result
        here, and only the caller can say which silence it is looking at.
        """
        table = aliases.get("aliases") or {}
        slug = table.get(boat_id)
        return str(slug) if slug else None

    @staticmethod
    def is_reviewed(boat_id: str, aliases: dict[str, object]) -> bool:
        """Has a person decided about this boat yet?"""
        table = aliases.get("aliases") or {}
        absent = aliases.get("absent") or []
        return boat_id in table or boat_id in absent

    def discover(self) -> Iterator[str]:
        """Every vessel page for one country, from the operator sitemap.

        One request, then no crawl: the sitemap already knows the whole
        inventory, so there is nothing to page and nothing to guess. Scoped to a
        country because the dataset is -- 58 Egyptian vessels out of 269, and the
        other 211 are pages nobody here will read.
        """
        sitemap = self.fetcher.get(OPERATOR_SITEMAP)
        seen: set[str] = set()
        for match in VESSEL_URL.finditer(sitemap.body):
            if match.group("country") != self.country:
                continue
            url = match.group(0)
            if url not in seen:
                seen.add(url)
                yield url

    def parse(self, result: FetchResult) -> ScrapeOutput:
        """Read one vessel page for what it actually states.

        A vessel page names the boat and every itinerary the operator sells on
        it. It does not state an entry bar -- that arrives over an XHR -- so this
        emits the identity and says so in a warning rather than raising. A page
        that names twenty-two trips is not a failed fetch; it is the join half of
        the answer, and discarding it because the other half is missing would
        leave the run with nothing at all.
        """
        output = ScrapeOutput()
        name = self._name(result)
        if not name:
            raise ScrapeError(f"no vessel name found in {result.url}")

        provenance = self.provenance(result.url)
        titles = self.itinerary_titles(result.body)
        requirements = self.extract_requirements(result.body)

        output.boats.append({"name": name, "source_url": result.url, "provenance": provenance})
        for slug, title in sorted(titles.items()):
            split = self.split_title(title)
            itinerary: dict[str, object] = {
                "name": split[0] if split else title,
                "title": title,
                "padi_slug": slug,
                "boat_name": name,
                "source_url": f"{result.url}{slug}/",
                "provenance": provenance,
            }
            if split:
                itinerary["nights"] = split[2]
            if requirements:
                itinerary["requirements"] = requirements
            output.itineraries.append(itinerary)

        if not titles:
            output.warnings.append(f"no itinerary titles in {result.url}")
        if not requirements:
            output.warnings.append(
                f"no stated entry bar in {result.url} -- PADI serves it over an XHR, "
                "so a vessel page cannot supply one"
            )
        return output

    @staticmethod
    def compare_key(value: str) -> str:
        """Letters and digits only, so two spellings of one title agree.

        Operator titles reach the two sources with different punctuation and the
        odd zero-width space; joining on the raw string loses matches that are
        plainly the same trip.

        The conjunction goes with the punctuation, because otherwise it is the
        one separator that survives it. Stripping symbols folds *A & B* onto
        *A, B* but leaves *A and B* a third spelling, so operators writing one
        list three ways got two keys instead of one -- Red Sea Aggressor II's
        *Northern Red Sea, Ras Mohamed and Straits of Tiran* against our
        *...Ras Mohamed, Straits of Tiran*, and Blue Storm's
        *Brothers-Daedalus-Elphinstone* against *Brothers, Daedalus and
        Elphinstone*.

        Counted before it was believed, over all 317 trips of the dataset this
        was written against: the fold merges two pairs and no others, and both
        pairs are one trip typed twice (*South & St Johns* / *South and St.
        Johns*, on two Emperor boats). Nothing in PADI's own book collides
        either way. A word cannot separate a title from a different trip unless
        the two are otherwise identical, in which case they were already the
        same trip.
        """
        text = re.sub(r"\band\b", " ", value.translate(ZERO_WIDTH).lower())
        return re.sub(r"[^a-z0-9]", "", text)

    @staticmethod
    def fold_ports(value: str, aliases: dict[str, str] | None = None) -> str:
        """One harbour, one spelling.

        Operators name the same terminal differently inside a trip title, and the
        two sources disagree: our Emperor Asmaa trips say "Marsa Ghalib" where
        PADI's say "Port Ghalib". `promote.PORT_ALIASES` already folds that pair
        for the port *columns*; a title carrying the other spelling is the same
        fact in a place nothing was folding, and it cost that boat all seven of
        its matches.
        """
        from ..promote import PORT_ALIASES

        table = aliases if aliases is not None else PORT_ALIASES
        out = value
        for spelling, canonical in table.items():
            out = re.sub(rf"\b{re.escape(spelling)}\b", canonical, out, flags=re.I)
        return out

    @classmethod
    def split_title(cls, title: str) -> tuple[str, str, int] | None:
        """"Name (Port - Port) N Nights" -> (name-with-ports, ports, nights).

        The night count is read *after* the ports rather than off the end of the
        string, because PADI ends a title six ways:

            ... (Hurghada - Hurghada) 7 Nights
            ... (Hurghada \u2013 Hurghada) \u2013 7 nights
            ... (Marsa Alam - Hurghada) 7 Nights 7 Nights
            ... (Hurghada - Hurghada) 4 nights/4 days diving
            ... (Hamata - Hamata) 7 nights / 8 days
            ... (Sharm el Sheikh - ...) 7 nights liveabaord + 1 night hotel

        An end-anchored pattern reads the first three and silently drops the
        rest, which cost seven trips their entry bar -- they were fetched, stored
        and then not keyed.

        A seventh form puts the count *before* the ports and leaves nothing
        after them:

            Northern Red Sea, Ras Mohamed and Straits of Tiran 7 Nights (Hurghada -Hurghada)

        so the count is looked for in the head when the tail has none, and
        struck out of the returned name -- "7 Nights" is the trip's length
        wherever it is written, not part of what the trip is called. One trip
        in the fleet is written this way and it is Red Sea Aggressor II's; the
        fallback is deliberately second, because a head count competing with a
        tail count is the ambiguity the tail rule already refuses to guess at.

        Where the tail is nothing but repeated counts they must agree: "7 Nights
        7 Nights" is 7, and a tail claiming both 4 and 7 returns ``None`` rather
        than picking, since a trip length is the denominator under every
        per-night price. Where the tail says anything else, the first count wins
        -- "7 nights liveaboard + 1 night hotel" is a seven-night trip, and the
        hotel night is not part of it.

        The name keeps its ports: two sailings differing only by port are two
        trips, here as everywhere else in this codebase.
        """
        text = title.strip().translate(DASHES)
        close = text.rfind(")")
        if close == -1:
            return None
        head, tail = text[: close + 1], text[close + 1 :]

        counts = [int(m.group(1)) for m in re.finditer(r"(\d+)\s*nights?\b", tail, re.I)]
        if counts and ONLY_NIGHTS.fullmatch(tail) and len(set(counts)) > 1:
            return None
        if not counts:
            head, counts = cls._nights_in_head(head)
        if not counts:
            return None

        ports = re.match(r"^(?P<name>.+?)\s*\((?P<ports>[^()]*)\)$", head)
        if not ports:
            return None
        return head.strip(), ports.group("ports").strip(), counts[0]

    #: "... 7 Nights (Port - Port)" -- the count sitting in front of the ports.
    HEAD_NIGHTS = re.compile(r"\s*\b(\d+)\s*nights?\b", re.IGNORECASE)

    @classmethod
    def _nights_in_head(cls, head: str) -> tuple[str, list[int]]:
        """The count read from in front of the ports, and the head without it.

        Only consulted where the tail is silent, and it must be unanimous:
        two different counts in one name is the same refusal to guess as the
        tail rule, for the same reason -- a trip length is the denominator
        under every per-night price on the page.
        """
        counts = [int(m.group(1)) for m in cls.HEAD_NIGHTS.finditer(head)]
        if len(set(counts)) != 1:
            return head, []
        return cls.HEAD_NIGHTS.sub(" ", head, count=1).replace("  ", " "), counts

    @classmethod
    def itinerary_titles(cls, html: str) -> dict[str, str]:
        """Slug -> the operator's own title, from the vessel page's nav.

        Keyed by slug only because something has to key it; the slug carries no
        information and is never parsed. Titles without a night count are other
        navigation -- destinations, deals -- and are dropped.
        """
        found: dict[str, str] = {}
        for match in ITINERARY_NAV.finditer(html):
            title = unescape(unescape(match.group("title"))).strip()
            if title and cls.split_title(title):
                found.setdefault(match.group("slug"), title)
        return found

    @classmethod
    def requirements_from_payload(cls, detail: dict[str, object]) -> dict[str, object] | None:
        """The entry bar out of an `ITINERARY_DETAIL` response.

        Three fields carry it, and they are not the same claim:

        ``requiredCertification``
            The coded certification, mapped through `CERTIFICATION_CHOICES`. A
            requirement.
        ``experienceRequiredDives``
            The coded dive count, whose every label reads *recommended*.
        ``minimalNumberOfDives``
            A plain integer, and **not** the enum restated -- Blue Melody states
            30, which is not among the enum's 0/20/50/100. So it is the
            operator's own number rather than a rendering of the code beside it.

        The two dive fields are reported separately for that reason. Whether PADI
        presents `minimalNumberOfDives` to a diver as required or as advice has
        not been checked, so it is carried under its own name and not folded into
        either the level or the recommendation.
        """
        certification = detail.get("requiredCertification")
        experience = detail.get("experienceRequiredDives")
        requirements = cls.requirements_from_choices(
            certification if isinstance(certification, int) else None,
            experience if isinstance(experience, int) else None,
        ) or {}

        minimum = detail.get("minimalNumberOfDives")
        if isinstance(minimum, int) and minimum > 0:
            requirements["min_logged_dives"] = minimum
        return requirements or None

    @classmethod
    def itinerary_from_payload(cls, detail: dict[str, object]) -> dict[str, object]:
        """One itinerary's facts, stated rather than parsed.

        Everything the title parser had to infer is a field here: `length` for
        nights, the two harbour titles for ports. The dive count keeps the low
        end of `totalNumberOfDives`/`totalNumberOfDivesMax`, as everywhere else
        in this codebase -- a range reported as its ceiling flatters the price
        per dive.
        """
        title = str(detail.get("title") or "")
        record: dict[str, object] = {
            "title": title,
            "padi_slug": detail.get("slug"),
            "padi_id": detail.get("id"),
            "boat_name": detail.get("shopTitle"),
        }
        split = cls.split_title(title)
        if split:
            record["name"] = split[0]
        if isinstance(detail.get("length"), int):
            record["nights"] = detail["length"]
        # Two fields, kept as two. They were joined into one `ports` string
        # that nothing read, and could not have been read: two of the eight
        # harbour names PADI uses contain the separator, so
        # "Hurghada - Marriott Marina - Hurghada - Marriott Marina" is either
        # ("Hurghada", "Marriott Marina - Hurghada - Marriott Marina") or
        # ("Hurghada - Marriott Marina", "Hurghada - Marriott Marina") and the
        # string does not say which. 436 of the 447 split cleanly; the other 11
        # cannot be split without guessing, and a closed-vocabulary parse over
        # today's eight names is exactly the rule that breaks silently the
        # first time PADI names a ninth marina.
        #
        # So the fix was the record, not a parser. `ports` is dropped: nothing
        # read it, and leaving a lossy field beside the lossless one is an
        # invitation to read the wrong one.
        departure = detail.get("harbourDepartureTitle")
        arrival = detail.get("harbourArrivalTitle")
        if departure and arrival:
            record["port_from"] = " ".join(str(departure).split())
            record["port_to"] = " ".join(str(arrival).split())
        low = detail.get("totalNumberOfDives")
        if isinstance(low, int) and low > 0:
            record["dives"] = low
        requirements = cls.requirements_from_payload(detail)
        if requirements:
            record["requirements"] = requirements
        return record

    @staticmethod
    def iso_day(value: object) -> str | None:
        """A PADI timestamp as the day it names, or ``None``.

        ``"2027-05-08T00:00:00Z"`` -> ``"2027-05-08"``. By truncation rather
        than by parsing into a local timezone, which would move a midnight-UTC
        sailing to the previous day for every reader west of Greenwich.
        """
        if not isinstance(value, str) or len(value) < 10:
            return None
        day = value[:10]
        return day if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) else None

    @classmethod
    def deal_from_payload(cls, record: dict[str, object]) -> dict[str, object] | None:
        """One row of `PROMOTIONS` as a deal, or ``None`` if it states no price.

        Four things have to be present for this to be an offer rather than a
        banner: the vessel it is on, a price, the price it is *against*, and the
        currency both are in. PADI states the currency here, which the sailings
        endpoint next door does not -- a `price` there is a bare number in the
        vessel's own unit, and assuming one put every Aggressor out by the
        EUR/USD rate. A row missing any of the four is dropped rather than
        completed: this project does not invent a price, and a discount with no
        "was" beside it is a claim about a number nobody published.

        The vessel comes off the URL's path, which is the one place a slug is
        load-bearing here rather than decorative -- it is how the deal joins to
        a boat of ours. `countryTitle` is deliberately not read: it says United
        States of America for all three Red Sea Aggressors, and asking PADI for
        the USA in order to catch them also returns Bahamas, Belize, Cayman and
        Roatan. Where a deal sails is the join's answer, never the label's.
        """
        url = record.get("url")
        match = VESSEL_URL.match(str(url)) if url else None
        price, was = record.get("price"), record.get("compareAtPrice")
        currency = record.get("currency")
        if not match or not isinstance(currency, str) or not currency.strip():
            return None
        if not isinstance(price, (int, float)) or not isinstance(was, (int, float)):
            return None
        if price <= 0 or was <= 0:
            return None

        promotion = record.get("promotion")
        offer = promotion if isinstance(promotion, dict) else {}
        kind = offer.get("kind")
        deal: dict[str, object] = {
            "slug": match.group("slug"),
            "country": match.group("country"),
            "shop": str(record.get("shopTitle") or "").strip() or None,
            "shop_id": record.get("shopId"),
            "title": str(offer.get("title") or "").strip() or None,
            # PADI's own word for what sort of offer this is, not a reading of
            # it. `value` is 33.0 under "Discount %" and 1761.0 under "Fixed
            # amount", so the two are only meaningful together.
            "kind": kind if isinstance(kind, int) else None,
            "kind_label": PROMOTION_KIND.get(kind) if isinstance(kind, int) else None,
            "value": offer.get("value") if isinstance(offer.get("value"), (int, float)) else None,
            "price": float(price),
            "was": float(was),
            "currency": currency.strip(),
            "start": cls.iso_day(record.get("dateFrom")),
            "end": cls.iso_day(record.get("dateTo")),
            "url": str(url),
        }
        return deal

    @staticmethod
    def deals_url(months: Sequence[str], countries: Sequence[int] = DEAL_COUNTRIES) -> str:
        """The deals query, spelled the way the deals page spells it.

        Repeated `country=` and repeated `date=`, in the order the page writes
        them, so the URL in `data/deals.json` is one somebody can paste into a
        browser and check. `date=` is the first of each season month; robots
        disallows `trip_date=`, `departure_date=`, `date_from=`, `dateStart=`,
        `dateTo=`, `date_after=` and `activity_date=`, and plain `date=` is not
        among them.
        """
        query = [("country", str(c)) for c in countries] + [("date", d) for d in months]
        return PROMOTIONS + "?" + urlencode(query)

    @staticmethod
    def deal_identity(deal: Mapping[str, object]) -> tuple:
        """What makes this offer this offer, for the paging guard.

        Vessel, the sailing it is quoted on, its price and the offer's own name.
        The issue that asked for this said "vessel + trip name + departure date
        + price"; the listing publishes no trip name, and the offer's title is
        what stands in its place -- *"333 FLASH SALE"* against *15% Early Bird*
        distinguishes two offers on one hull the way a trip name would.
        """
        return (deal.get("slug"), deal.get("start"), deal.get("end"),
                deal.get("price"), deal.get("title"))

    @classmethod
    def collect_deals(
        cls,
        fetch: Callable[[str], object],
        url: str,
        *,
        max_pages: int = DEAL_MAX_PAGES,
    ) -> tuple[dict[str, dict[str, object]], dict[str, object]]:
        """Page a deals query to its end, and say how it ended.

        **Termination is on offer identity, never on a page number or a status
        code.** The page this endpoint backs returns page 1's content for every
        value of `page`, including 99, so "the listing ran out" is a question
        the page number cannot answer -- and a loop that asked it would either
        stop at one page or never stop at all. This one stops when a page adds
        no offer it has not already seen, which is true of a repeated page, an
        empty page and a page that 404s alike.

        Keyed by vessel, because PADI publishes one deal per vessel per query.
        A second row for a vessel already held is *kept out and reported*
        rather than allowed to overwrite the first: silently keeping one of two
        is how a row count stays right while the content is wrong.

        `fetch` returns the parsed body or ``None``, so the network lives in
        `tools/fetch_deals.py` and this is testable without one.
        """
        deals: dict[str, dict[str, object]] = {}
        seen: set[tuple] = set()
        crowded: list[str] = []
        pages = read = 0
        stopped = "page cap"
        # Whether the last page read claimed there was another. It decides
        # nothing about when to stop -- identity does that -- and decides
        # everything about what a failed fetch *meant*. A page that answers
        # nothing after the listing said it had ended is the end confirmed; the
        # same silence after it said there was more is a page we did not read,
        # which is not the same as a page with nothing on it.
        promised_more = True

        while pages < max_pages:
            separator = "&" if "?" in url else "?"
            body = fetch(url if pages == 0 else f"{url}{separator}page={pages + 1}")
            pages += 1
            if not isinstance(body, dict):
                stopped = "unreadable" if promised_more else "listing ended"
                break
            promised_more = bool(body.get("next"))
            results = body.get("results")
            if not isinstance(results, list) or not results:
                stopped = "empty page"
                break
            read += len(results)

            fresh = 0
            for row in results:
                if not isinstance(row, dict):
                    continue
                deal = cls.deal_from_payload(row)
                if not deal:
                    continue
                identity = cls.deal_identity(deal)
                if identity in seen:
                    continue
                seen.add(identity)
                fresh += 1
                slug = str(deal["slug"])
                if slug in deals:
                    crowded.append(f"{slug}: {deal.get('title') or 'a second offer'}")
                    continue
                deals[slug] = deal
            if not fresh:
                stopped = "no new offer"
                break

        return deals, {
            "pages": pages,
            "rows": read,
            "stopped": stopped,
            # Never silent, for the same reason `changes` never truncates
            # without saying so: a listing that ran into its cap looks exactly
            # like one that ended, and only one of those is the whole answer.
            # A page that could not be read counts here too -- the run does not
            # know what was on it, and the honest word for that is not "none".
            "truncated": stopped in ("page cap", "unreadable"),
            "crowded": crowded,
        }

    @staticmethod
    def basis_for(payed_per: object) -> FeeBasis | None:
        """PADI's charging unit as one of this project's, or ``None``."""
        return PAYED_PER.get(payed_per) if isinstance(payed_per, int) else None

    @classmethod
    def fees_from_payload(
        cls,
        detail: dict[str, object],
        currency: str,
        season: tuple[str, str] = SEASON,
    ) -> dict[str, object]:
        """The charges PADI says a diver cannot decline, and whether they add up.

        This is the half of the comparison the site was missing. Set against our
        *total*, PADI's headline price would have looked cheaper by exactly the
        fees nobody had read -- the trick this project exists to expose,
        committed by the page itself -- and the column dodged that by comparing
        berth to berth instead, which answers a question no diver asks. PADI
        does publish its fee book; it publishes it on the itinerary endpoint
        rather than beside the price, which is why it looked absent.

        Returns the lines *and* a verdict on whether they are complete, because
        those are different facts and only the pair of them is safe to use. A
        bill is complete when every mandatory entry both names a charge this
        project can classify and states a price in a unit that normalises. Where
        it is not, ``complete`` is false and no PADI total may be claimed:

        - **Unclassified.** "14% GST (on onboard purchases)", "Hospitality Fee",
          "Local Fees". Naming them is guesswork and pricing them is worse --
          the GST line carries ``price: 14.0``, so counting it would put
          fourteen euro on the bill for a percentage of an unrelated purchase.
        - **Unpriced.** 103 of the 872 entries name a charge and give no figure
          in either field -- see `EXTRA_VALUE` for the second one, which answers
          133 of the 236 the first leaves null. The line is kept, with no
          amount, so the reader sees the charge exists.
        - **A basis that will not normalise.** See `PAYED_PER`.

        The currency is the vessel's own, taken from `window.shop.currency` and
        passed in, because nothing in the payload states it -- the same trap as
        the sailing prices, where EUR, USD and GBP headers all return the same
        number. A vessel whose page states no currency gets no fees rather than
        fees assumed to be euro.
        """

        lines: list[dict[str, object]] = []
        unreadable: list[str] = []
        for field in MANDATORY_FIELDS:
            entries = detail.get(field)
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                # Before the title is even read: an entry the source has
                # already dated out of the season is not this trip's charge,
                # and letting it reach `unreadable` would block a bill on a
                # price nobody will be asked to pay.
                if not _in_season(entry, season):
                    continue
                title = str(entry.get("title") or "").strip()
                # `prose=False`: this is a field, not a line cut out of a page.
                code = classify_label(title, prose=False) if title else None
                basis = cls.basis_for(entry.get("payedPer"))
                money = _money(entry, currency)
                if code is None or basis is None:
                    unreadable.append(title or f"<untitled {entry.get('id')}>")
                    continue
                # `note` rather than a label of our own, and shaped like
                # liveaboard.com's lines: the code is this project's name for
                # the charge and the note is the seller's, which is the pair the
                # fee table already prints. Whether PADI collects it in advance
                # or at the dock is not kept -- it does not move the total, and
                # this dataset models no such distinction on its own fees
                # either.
                line: dict[str, object] = {
                    "code": code.value,
                    "tier": FeeTier.MANDATORY.value,
                    "basis": basis.value,
                    "note": title,
                }
                if money is not None:
                    line.update(money)
                lines.append(line)

        # Two entries with one title are still kept as two, and the reason has
        # changed. It used to be that they are two charges the operator bills,
        # on the evidence that no pair in the book is an exact duplicate --
        # DUNE Longara's "Environmental taxes" at €100 and €200 being the case
        # in point. The dates were in the same payload and refute it: those two
        # are one charge that changed price on 2026-06-14, and across the whole
        # store all 69 such pairs resolve to exactly one entry valid in the
        # published season. `_in_season` above drops the other, so what reaches
        # here is already one line per charge and the title no longer has to
        # carry that weight.
        #
        # Kept as two anyway, for the case the dates do not cover: an operator
        # that genuinely bills one title twice inside one season states no
        # window on either, and folding those on the title would halve a real
        # bill -- the direction this project never rounds.
        #
        # Read here, before anything optional is appended, and that ordering is
        # the whole of how `complete` stays a verdict about the mandatory bill.
        priced = all("amount" in line for line in lines)

        # The charges a diver *can* decline, on the source's own say-so. Read
        # after the mandatory ones and never mixed into that verdict: see
        # `OPTIONAL_FIELDS`. One line per code, which is the rule
        # `parse_extras` already keeps on the other seller's Optional block --
        # two lines under one code are one charge printed twice, and where the
        # site puts a toggle on that code they are also that charge counted
        # twice.
        #
        # First past the post, as it is there, and measured rather than assumed
        # safe: across all 438 trips in the store there is no code where an
        # unpriced entry in one of these lists is followed by a priced one, so
        # the order costs no figure today. A later reading where it does should
        # prefer the priced line -- an unpriced entry is not a cheaper charge,
        # it is a charge nobody stated.
        optional_codes: set[str] = {line["code"] for line in lines}
        for line in cls._optional(detail, currency, season):
            if line["code"] in optional_codes:
                continue
            optional_codes.add(str(line["code"]))
            lines.append(line)

        # What the fare already covers, appended at zero. A charge cannot be
        # billed and bundled on one trip, so a stated amount wins: PADI prices
        # nitrox on 31 trips whose inclusions also say "Free nitrox (for
        # certified nitrox divers)". Both claims are true and only one line per
        # code survives, so it is the one with money on it.
        #
        # **17 of those 31 are not the gas, and this rule is wrong about them.**
        # Probed 2026-09-03 (`tools/probe_nitrox.py`, all 29 vessels whose book
        # prices nitrox): every clash carries that identical inclusion, so the
        # explanation is never there -- it is in the *billed* title. Seven read
        # "15 LITER tank nitrox (only 12 liter is free of chanrge)", eight
        # "Nitrox 15 liter tanks", two "15 liters Nitrox". The operator gives
        # 12-litre fills free and charges 65 for 15-litre ones, and
        # `classify_label` files all three as `nitrox` because the word is in
        # them -- so the toggle that counts prices a tank upgrade as the gas,
        # on 15 itineraries and 48 sailings, against a vessel panel stating
        # nitrox included. liveaboard.com files the same charge under *gear*
        # ("15L tanks 35-65/week", on 79 of 79 vessels) and never touches its
        # nitrox line, which is what settles that these are one charge and not
        # two sellers disagreeing. `FeeCode.TANK_15L` exists for it and has no
        # pattern behind it yet. See docs/sources/padi.com.md.
        #
        # The remaining 14 are one vessel -- MY Seawolf Dominator, a bare
        # "Nitrox" at 50 beside the same free-nitrox line, no tank size and an
        # empty `generalInformation` -- and there the amount winning is right,
        # because turning a stated cost into free is the error this must never
        # make.
        #
        # **A charge that names no figure loses to an inclusion, though**, and
        # that only became possible when the optional lists were read: 15 trips
        # list a transfer with no price *and* state the transfer as included, so
        # first-past-the-post published "airport transfer, price unknown" where
        # the seller had said it was covered. An unpriced line is not a stated
        # amount, and **included fees stay in the breakdown at zero** by
        # invariant. Mandatory charges are exempt: the field an entry sits in is
        # the claim there, and the mandatory list saying a diver pays it
        # outranks another list saying they do not.
        for code, title in cls._inclusions(detail, season):
            clash = next((l for l in lines if l["code"] == code.value), None)
            if clash is not None:
                if "amount" in clash or clash["tier"] == FeeTier.MANDATORY.value:
                    continue
                lines.remove(clash)
            lines.append({
                "code": code.value,
                # The tier the fee would have had if it were charged. An
                # included line is drawn at zero rather than counted, so the
                # tier is what tells the page which column it belongs in.
                "tier": tier_for_inclusion(code).value,
                "basis": FeeBasis.PER_TRIP.value,
                "included": True,
                "note": title,
            })

        return {
            "lines": lines,
            # Complete means "every charge PADI states is on this list, named
            # and priced". An itinerary with no mandatory entries at all is
            # complete and empty -- 50 of the 307 are, and that is PADI saying
            # the fare covers everything, which is a disclosure and not a gap.
            #
            # Neither the optional charges nor the inclusions can make it
            # false, and for the same reason: a course this parser cannot name
            # and a transfer priced per vehicle say nothing about whether what
            # a diver *must* pay adds up.
            #
            # 4,493 of the 5,662 entries in the inclusions list are amenities -- Water, Coffee, Free WiFi, a shisha
            # lounge -- and an amenity nobody can classify is not a hole in a
            # fee book. Letting them reach `unreadable` would have taken the
            # book from 259 complete trips to none.
            "complete": not unreadable and priced,
            "unreadable": sorted(set(unreadable)),
        }

    @classmethod
    def _optional(
        cls, detail: dict[str, object], currency: str, season: tuple[str, str]
    ) -> list[dict[str, object]]:
        """The charges PADI says a diver may decline, in list order.

        Through the same table and the same money reader the mandatory charges
        go through, and returned as lines the caller dedupes by code.

        Three things the mandatory path does differently, each for a reason:

        - **The parenthetical is stripped before the title is classified**, as
          it is for the inclusions. These entries are prose in the same way:
          "Airport Meet & Greet (VISA assistance, eligible countries only)" is
          not the visa charge, "Flashlight (torch)" is a torch, and
          "Satellite phone call (per minute)" is not a charge this project can
          normalise. The mandatory list is labels and keeps its brackets, where
          "Safari Package (Marine Park fees, harbour fees and fuel)" is a
          combined charge that needs them to be read at all.
        - **An entry nobody can classify is dropped, not recorded as
          unreadable.** 72 of the 111 distinct titles here are courses,
          amenities and single gear items -- "PADI Deep Diver", "Espresso
          coffee", "Wetsuit" -- and an unrecognised extra costs a line of data
          where a
          misrecognised one puts an invented charge on the page. It cannot touch
          `complete`, which is a verdict on what a diver must pay.
        - **A charging unit that will not normalise is dropped the same way.**
          PADI prices its courses "per course" and its transfers "return, per
          person" (`PAYED_PER` maps neither), so those entries state a real
          price in a unit this dataset cannot add to a trip's bill.

        The gear set is the one entry that carries its own contents:
        ``fullSetDescription`` names what is in it, exactly as the other
        seller's bundle row does, and the note keeps both halves in the
        seller's words.
        """

        out: list[dict[str, object]] = []
        for field in OPTIONAL_FIELDS:
            entries = detail.get(field)
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict) or not _in_season(entry, season):
                    continue
                title = str(entry.get("title") or "").strip()
                if not title:
                    continue
                code = classify_label(PARENTHETICAL.sub("", title).strip(), prose=False)
                basis = cls.basis_for(entry.get("payedPer"))
                if code is None or basis is None:
                    continue
                contents = str(entry.get("fullSetDescription") or "").strip()
                line: dict[str, object] = {
                    "code": code.value,
                    "tier": _tier_for(code, required=False).value,
                    "basis": basis.value,
                    "note": f"{title}: {contents}" if contents else title,
                }
                money = _money(entry, currency)
                if money is not None:
                    line.update(money)
                out.append(line)
        return out

    @classmethod
    def _inclusions(
        cls, detail: dict[str, object], season: tuple[str, str]
    ) -> list[tuple[FeeCode, str]]:
        """The charges PADI says the fare already covers, in list order.

        Through the same `LABEL_PATTERNS` both sources' wording already goes
        through. A second vocabulary drifts, and the day it drifts is the day
        one seller's "Harbour fees" and the other's mean different things.
        """

        entries = detail.get(INCLUDED_FIELD)
        if not isinstance(entries, list):
            return []
        out: list[tuple[FeeCode, str]] = []
        seen: set[FeeCode] = set()
        for entry in entries:
            if not isinstance(entry, dict) or not _in_season(entry, season):
                continue
            title = str(entry.get("title") or "").strip()
            code = classify_label(PARENTHETICAL.sub("", title).strip(), prose=False)
            if code is None or code in seen:
                continue
            seen.add(code)
            out.append((code, title))
        return out

    @staticmethod
    def requirements_from_choices(
        certification: int | None, experience: int | None = None
    ) -> dict[str, object] | None:
        """PADI's two coded enums -> this project's entry bar.

        The vocabulary is verified; the plumbing that delivers values is not, so
        this takes the codes as arguments rather than digging them out of a
        payload nobody has seen. An unknown code returns ``None`` instead of a
        default: a new enum member is a thing to go and read, not to guess at.
        """
        level = CERTIFICATION_CHOICES.get(certification) if certification is not None else None
        dives = EXPERIENCE_DIVES.get(experience) if experience is not None else None
        if level is None and dives is None:
            return None
        if certification is not None and level is None:
            return None
        if experience is not None and dives is None:
            return None
        requirements: dict[str, object] = {}
        if level is not None:
            requirements["min_level"] = level.value
        if dives:
            # Recommended, not required -- see EXPERIENCE_DIVES.
            requirements["recommended_logged_dives"] = dives
        return requirements or None

    @staticmethod
    def extract_requirements(html: str) -> dict[str, object] | None:
        """Pull the entry bar out of page text.

        Deliberately conservative: it reports only what the page states in the
        industry's standard phrasing. Inferring a requirement that was never
        written down would turn a safety gate into a guess.
        """
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text)

        level: DiverLevel | None = None
        for pattern, candidate in CERT_PATTERNS:
            if pattern.search(text):
                level = candidate
                break

        dives_match = DIVES_PATTERN.search(text)
        min_dives = int(dives_match.group(1)) if dives_match else 0

        if level is None and not min_dives:
            return None

        return {
            "min_level": (level or DiverLevel.OPEN_WATER).value,
            "min_logged_dives": min_dives,
            "strong_current": bool(re.search(r"strong current|drift div", text, re.I)),
        }

    @staticmethod
    def specs_from_page(html: str) -> dict[str, object]:
        """The vessel page's specification strip: cabins, length, year built.

        Server-rendered, in the same response ``window.shop`` comes from, so
        it costs no request beyond the one `fetch_padi.py` already makes per
        vessel. That matters because it is the only source for the boats
        liveaboard.com does not sell -- their specification table does not
        exist, and 14 hulls publish no length as a result.

        The markup is a flat run of label/value pairs::

            <p class='o-title'>Cabins</p><p class="o-value">16</p>
            <p class='o-title'>Length / Width</p><p class="o-value">45 m / 8 m</p>
            <p class='o-title'>Year built / renovated</p><p class="o-value">2022&nbsp; / 2025</p>

        **Two of the three labels name two facts and the value holds both**,
        which is the trap: the second number is the beam and the *refit* year,
        neither of which is the field being read. A renovation is not a build
        date and printing 2025 for a hull laid down in 2022 would age the fleet
        wrong in the one direction an operator would like. Both take the figure
        before the slash and drop what follows.

        Deliberately not read here: **guests**. The strip has no such row and
        neither does the rest of the page -- searched in full, every numeric
        form of guests, divers, passengers, people and pax, zero hits. So a
        boat with no guest count from liveaboard.com has none from PADI either,
        and the honest output is the absence. See docs/sources/padi.com.md.
        """
        pairs = re.findall(
            r"""<p[^>]*class=['"]o-title['"][^>]*>(.*?)</p>\s*"""
            r"""<p[^>]*class=['"]o-value['"][^>]*>(.*?)</p>""",
            html,
            re.S | re.I,
        )

        def clean(raw: str) -> str:
            text = re.sub(r"<[^>]+>", " ", unescape(raw))
            return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()

        rows = {clean(label).lower(): clean(value) for label, value in pairs}

        def first_int(label: str, low: int, high: int) -> int | None:
            """The figure before the slash, which is the one the label leads with.

            Bounded rather than trusted, and bounded against fixed numbers
            rather than a clock: a parser whose output depends on the day it
            ran cannot be compared byte for byte, which is how `promote --check`
            establishes that the published page is this code's output.
            """
            value = rows.get(label)
            if not value:
                return None
            match = re.match(r"\s*(\d{1,4})", value.split("/")[0])
            if not match:
                return None
            number = int(match.group(1))
            return number if low <= number <= high else None

        specs: dict[str, object] = {
            "cabins": first_int("cabins", 1, MAX_GUESTS),
            "length_m": first_int("length / width", 1, MAX_LENGTH_M),
            "year_built": first_int("year built / renovated", 1900, 2100),
        }
        # "FREE" is PADI saying the boat does not charge for fills, which is
        # the same claim liveaboard.com's "Free Nitrox" amenity makes. Anything
        # else it prints -- a price, "YES", "NO" -- is not that claim, so only
        # the one word sets it and everything else leaves the field unstated
        # rather than false.
        #
        # Recorded and deliberately not acted on, like `_sailing_counts`:
        # `promote` folds only the vessel panel's nitrox tick into a fee book,
        # and doing the same with PADI's would put an *included* line on the 12
        # boats that have no panel -- a fee claim sourced from a strip beside a
        # price rather than from either seller's fee disclosure. Worth having
        # to check the panel against; not worth pricing a trip from.
        nitrox = rows.get("nitrox")
        if nitrox:
            specs["nitrox_free"] = nitrox.upper() == "FREE"
        return {k: v for k, v in specs.items() if v is not None}

    def _name(self, result: FetchResult) -> str | None:
        for node in jsonld.of_type(result.body, "Product", "TouristTrip", "Trip"):
            if isinstance(node.get("name"), str):
                return node["name"]
        match = re.search(r"<title[^>]*>(.*?)</title>", result.body, re.I | re.S)
        return match.group(1).strip() if match else None
