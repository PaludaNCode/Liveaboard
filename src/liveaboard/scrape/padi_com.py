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
        departure = detail.get("harbourDepartureTitle")
        arrival = detail.get("harbourArrivalTitle")
        if departure and arrival:
            record["ports"] = f"{departure} - {arrival}"
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
        - **Unpriced.** 142 of the 623 entries name a charge and give no figure,
          exactly as a third of the liveaboard.com book does. The line is kept,
          with no amount, so the reader sees the charge exists.
        - **A basis that will not normalise.** See `PAYED_PER`.

        The currency is the vessel's own, taken from `window.shop.currency` and
        passed in, because nothing in the payload states it -- the same trap as
        the sailing prices, where EUR, USD and GBP headers all return the same
        number. A vessel whose page states no currency gets no fees rather than
        fees assumed to be euro.
        """
        from .fees import classify_label

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
                price = entry.get("price")
                amount = float(price) if isinstance(price, (int, float)) else None
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
                if amount is not None:
                    line["amount"] = {"amount": amount, "currency": currency}
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
        priced = all("amount" in line for line in lines)
        return {
            "lines": lines,
            # Complete means "every charge PADI states is on this list, named
            # and priced". An itinerary with no mandatory entries at all is
            # complete and empty -- 50 of the 307 are, and that is PADI saying
            # the fare covers everything, which is a disclosure and not a gap.
            "complete": not unreadable and priced,
            "unreadable": sorted(set(unreadable)),
        }

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

    def _name(self, result: FetchResult) -> str | None:
        for node in jsonld.of_type(result.body, "Product", "TouristTrip", "Trip"):
            if isinstance(node.get("name"), str):
                return node["name"]
        match = re.search(r"<title[^>]*>(.*?)</title>", result.body, re.I | re.S)
        return match.group(1).strip() if match else None
