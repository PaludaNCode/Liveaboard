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
weak on departure-level pricing and strong on the things the price comparison
needs in order to be fair: which certification a trip demands, how many logged
dives, and what the operator is accredited to run. So this adapter is scoped to
*requirements and accreditation* rather than to prices, and its output enriches
itineraries matched from the other source instead of creating departures.

Keeping it narrow also keeps the crawl small, which is the polite thing to do
when the useful part of a site is a fraction of its pages.
"""

from __future__ import annotations

import re
from typing import Iterator

from html import unescape

from .base import FetchResult, ScrapeError, ScrapeOutput, SourceAdapter
from . import jsonld
from ..taxonomy import DiverLevel

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

NIGHTS_SUFFIX = re.compile(r"\s*\d+\s*Nights?\s*$", re.IGNORECASE)

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
        """
        return re.sub(r"[^a-z0-9]", "", value.translate(ZERO_WIDTH).lower())

    @staticmethod
    def split_title(title: str) -> tuple[str, str, int] | None:
        """"Name (Port - Port) N Nights" -> (name-with-ports, ports, nights).

        The night suffix is stripped before anything is matched, and in a loop,
        because PADI sometimes appends it twice -- *"... (Marsa Alam - Hurghada)
        7 Nights 7 Nights"* is a live title, not a typo of ours. Anchoring a
        pattern on the end of the string instead just fails on that trip.

        Two suffixes that disagree return ``None``. There is no way to tell which
        one the operator meant, and a trip length is the denominator under every
        per-night price on the page.

        The name keeps its ports: two sailings differing only by port are two
        trips, here as everywhere else in this codebase.
        """
        text = title.strip()
        counts: list[int] = []
        while True:
            match = NIGHTS_SUFFIX.search(text)
            if not match:
                break
            digits = re.search(r"\d+", match.group(0))
            if not digits:
                break
            counts.append(int(digits.group()))
            text = text[: match.start()].strip()
        if not counts or len(set(counts)) > 1:
            return None
        ports = re.match(r"^(?P<name>.+?)\s*\((?P<ports>[^)]*)\)$", text)
        if not ports:
            return None
        return text, ports.group("ports").strip(), counts[0]

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
