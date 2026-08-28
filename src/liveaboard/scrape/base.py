"""Source adapters and the polite fetcher they share.

Every adapter turns one source site into the normalised dataset shape. The
fetcher enforces the manners: it reads ``robots.txt`` and obeys it, honours
``Crawl-delay``, identifies itself truthfully, and writes every response to a
snapshot directory so a price on the site can always be traced back to the
bytes it was read from.

The snapshots are the point. A daily scrape that keeps no evidence is just a
number that changes; one that keeps the raw page can show what changed and
when, which is the whole reason this project exists.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import time
import urllib.error
import urllib.request
import urllib.robotparser
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse

USER_AGENT = (
    "LiveaboardTransparencyBot/0.1 "
    "(+https://github.com/PaludaNCode/Liveaboard; price-transparency research)"
)

DEFAULT_DELAY_SECONDS = 5.0

PARSE_ATTEMPTS = 2
"""How many times to ask for a page that comes back without structured data.

Measured, not assumed. Fourteen vessel-month pages returned no JSON-LD at all
on 2026-08-28; a probe re-read all fourteen and thirteen answered in full on
the very first retry, the fourteenth being a genuinely empty month. So the
failure is the response, not the page, and one more request settles it --
against 49 real, bookable sailings that the first version of this loop deleted
from the site by believing the empty answer.

Two, not more: the pages that fail are a handful out of 268, the retry is
paced by the same crawl delay as everything else, and a page that answers
nothing twice is a genuine unknown that `carry_unread` then covers.
"""
"""Conservative default for a host nobody has checked.

Slower than necessary on purpose: this scrape has a whole day to finish and
nothing is gained by leaning on someone else's origin server. A host stays at
this pace until somebody has actually read its robots.txt and recorded the
answer in CHECKED_HOSTS below.
"""

CHECKED_HOSTS: dict[str, float] = {
    # robots.txt states no Crawl-delay (tools/probe_crawl.py, 2026-08-27), so
    # the pace here is ours to choose rather than the site's to dictate. Two
    # seconds is still slower than a person clicking through the same pages,
    # for a job that runs once a day.
    #
    # This is deliberately a per-host note rather than a lower global default:
    # the next source added has not been checked, and should start slow.
    "www.liveaboard.com": 2.0,
}
"""Hosts whose robots.txt has been read, and the pace chosen for each.

Never overrides a stated Crawl-delay -- crawl_delay takes the larger of the
two -- so a site that starts asking for more gets it without anyone noticing
this table.
"""


class FetchBlocked(RuntimeError):
    """Raised when a fetch is refused, by robots.txt or by network policy.

    Distinct from a transport failure: this means we were told no, and the
    correct response is to stop rather than to retry.
    """


class ScrapeError(RuntimeError):
    """Raised when a page loaded but could not be understood."""


@dataclass(slots=True)
class FetchResult:
    url: str
    status: int
    body: str
    fetched_at: datetime
    from_cache: bool = False

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.body.encode("utf-8")).hexdigest()[:16]


@dataclass(slots=True)
class PoliteFetcher:
    """A rate-limited, robots-respecting HTTP client with a snapshot trail."""

    snapshot_dir: Path
    delay: float = DEFAULT_DELAY_SECONDS
    timeout: float = 30.0
    user_agent: str = USER_AGENT
    diagnose: bool = False
    """Print each page's structure as it is fetched.

    Set when the only channel back from a scrape is a CI log and the snapshot
    artifact cannot be opened from where the parser is being written."""
    _robots: dict[str, urllib.robotparser.RobotFileParser] = field(default_factory=dict)
    _last_request: dict[str, float] = field(default_factory=dict)
    _cache: dict[str, FetchResult] = field(default_factory=dict)
    """Responses already fetched in this run.

    Listing pages are read twice by design — once by ``discover`` to find links,
    once by ``run`` to parse — and paying the crawl delay for that twice is
    both slow and rude. The cache lives for one run only; the daily
    schedule is what makes data fresh, not re-fetching within a single pass.
    """

    def _robots_for(self, url: str) -> urllib.robotparser.RobotFileParser:
        host = urlparse(url).netloc
        if host not in self._robots:
            parser = urllib.robotparser.RobotFileParser()
            parser.set_url(f"{urlparse(url).scheme}://{host}/robots.txt")
            try:
                parser.read()
            except Exception as exc:  # noqa: BLE001 - unreachable robots is decisive
                raise FetchBlocked(f"cannot read robots.txt for {host}: {exc}") from exc
            self._robots[host] = parser
        return self._robots[host]

    def allowed(self, url: str) -> bool:
        return self._robots_for(url).can_fetch(self.user_agent, url)

    def crawl_delay(self, url: str) -> float:
        """The politest of: the site's stated delay, ours for this host, ours by default."""
        ours = CHECKED_HOSTS.get(urlparse(url).netloc, self.delay)
        stated = self._robots_for(url).crawl_delay(self.user_agent)
        # A stated delay always wins when it is the slower of the two. Nothing
        # in CHECKED_HOSTS can make this crawler faster than a site asked for.
        return max(float(stated), ours) if stated else ours

    def _wait(self, url: str) -> None:
        host = urlparse(url).netloc
        delay = self.crawl_delay(url)
        elapsed = time.monotonic() - self._last_request.get(host, 0.0)
        if elapsed < delay:
            time.sleep(delay - elapsed)
        self._last_request[host] = time.monotonic()

    def forget(self, url: str) -> None:
        """Drop one cached body so the next ``get`` is a real request.

        For the one case where the cache is the wrong answer: a page that came
        back without its structured data. Asking again through the cache would
        hand back the same nothing, and the whole point of a retry is to find
        out whether the *response* was the problem.
        """
        self._cache.pop(url, None)

    def get(self, url: str) -> FetchResult:
        """Fetch one URL, refusing if robots.txt disallows it."""
        cached = self._cache.get(url)
        if cached is not None:
            return FetchResult(
                url=cached.url,
                status=cached.status,
                body=cached.body,
                fetched_at=cached.fetched_at,
                from_cache=True,
            )

        if not self.allowed(url):
            raise FetchBlocked(f"robots.txt disallows {url}")

        self._wait(url)
        request = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
                status = response.status
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403, 407, 451):
                raise FetchBlocked(f"{url} refused with HTTP {exc.code}") from exc
            raise
        except urllib.error.URLError as exc:
            raise FetchBlocked(f"{url} unreachable: {exc.reason}") from exc

        result = FetchResult(url=url, status=status, body=body, fetched_at=_now())
        self._cache[url] = result
        self.snapshot(result)
        if self.diagnose:
            from . import diagnose as _diagnose

            print(_diagnose.describe(result), flush=True)
        return result

    def snapshot(self, result: FetchResult) -> Path:
        """Write the raw response to the audit trail, one directory per day."""
        day = result.fetched_at.date().isoformat()
        target = self.snapshot_dir / day
        target.mkdir(parents=True, exist_ok=True)

        key = hashlib.sha256(result.url.encode("utf-8")).hexdigest()[:16]
        path = target / f"{key}.html.gz"
        path.write_bytes(gzip.compress(result.body.encode("utf-8")))

        index = target / "index.jsonl"
        with index.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "url": result.url,
                        "status": result.status,
                        "file": path.name,
                        "digest": result.digest,
                        "fetched_at": result.fetched_at.isoformat(),
                    }
                )
                + "\n"
            )
        return path


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class ScrapeOutput:
    """What one adapter produced in one run."""

    operators: list[dict[str, Any]] = field(default_factory=list)
    boats: list[dict[str, Any]] = field(default_factory=list)
    itineraries: list[dict[str, Any]] = field(default_factory=list)
    departures: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unread: list[str] = field(default_factory=list)
    """Pages this run did not read: skipped, blocked, or fetched unparseable.

    Not the same as a page that said nothing. A vessel page carrying a Product
    node and no Events is a boat selling nothing that month, and its absence
    from ``departures`` is the answer. A page that came back with no structured
    data at all answers nothing, and treating the two alike is how a run that
    failed to read five sailings publishes a site that says they do not exist.

    A vessel the barren list skipped belongs here for the same reason, even
    though nothing went wrong: the run chose not to ask, so it has no more
    evidence about that boat than if the page had failed.
    """
    archive: list[dict[str, Any]] = field(default_factory=list)
    """The structured data each page published, whether or not we parse it.

    Everything else in this class is what the adapter chose to read today. This
    is what the page actually said, kept so a question nobody has asked yet can
    still be answered later.

    That matters because the two are not equally recoverable. Current prices can
    always be re-scraped; the prices as they stood on a given day cannot. A
    field we start caring about next month would otherwise arrive attached to
    next month's data, with today's gone for good.

    Snapshots do not cover this: they are gitignored and expire from CI after
    fourteen days. This is committed, and it is JSON rather than HTML, so it
    stays queryable without a browser -- which is precisely how the stored fee
    disclosures turned a live re-run into an offline audit.
    """

    def extend(self, other: ScrapeOutput) -> None:
        self.operators.extend(other.operators)
        self.boats.extend(other.boats)
        self.itineraries.extend(other.itineraries)
        self.departures.extend(other.departures)
        self.warnings.extend(other.warnings)
        self.unread.extend(other.unread)
        self.archive.extend(other.archive)

    @property
    def is_empty(self) -> bool:
        return not (self.itineraries or self.departures)


class SourceAdapter(ABC):
    """One source site.

    Adapters emit dictionaries in the dataset's own JSON shape rather than model
    objects, so a partial or malformed scrape fails at the validation boundary
    in :mod:`liveaboard.dataset` instead of halfway through parsing.
    """

    #: Stable identifier recorded in every provenance entry this adapter writes.
    source_id: str = ""

    #: Host this adapter reads. Used to report precisely which allowlist entry
    #: is missing when the environment blocks it.
    host: str = ""

    #: Cap on detail pages per run. An uncapped listing can outlast the CI job
    #: timeout, and a truncated scrape that says so beats one killed halfway.
    #:
    #: Egypt listed 79 vessels and now lists 80, so 60 would have silently
    #: dropped a quarter of the season. 120 covers it with headroom.
    #:
    #: The cap counts vessels, and each one costs four requests -- the month
    #: selector returns a single month, and the listings are not month-filtered
    #: (tools/probe_crawl.py), so there is no way to learn a vessel does not
    #: sail in July without asking. Roughly a third of a run comes back empty
    #: for that reason and it is the price of the answer, not waste to remove.
    max_pages: int = 120

    def __init__(self, fetcher: PoliteFetcher) -> None:
        self.fetcher = fetcher
        self._notes: list[str] = []
        self._unread: list[str] = []

    def note(self, message: str) -> None:
        """Record something the run should report but which is not fatal.

        Discovery is a generator, so it cannot return warnings; without this a
        404 listing page or an unmatched link pattern disappears silently, and
        an empty scrape becomes indistinguishable from a site with nothing on
        it. That is the failure this project can least afford.
        """
        self._notes.append(message)

    def not_looked_at(self, url: str) -> None:
        """Record a page this run decided not to fetch.

        Deliberately the same channel as a page that came back unreadable,
        because the consequence is identical: this run learned nothing about
        it, and publishing its absence would delete whatever it holds. A
        vessel skipped by the barren list did exactly that -- AVO's and Blue's
        three sailings were dropped from the site and reported as withdrawn by
        a run that never asked the source about them.

        Discovery is a generator and cannot return this, which is why it lands
        here rather than on the output directly. Same reason as ``note``.
        """
        self._unread.append(url)

    def provenance(self, url: str, retrieved: date | None = None) -> dict[str, Any]:
        return {
            "kind": "scraped",
            "source_id": self.source_id,
            "retrieved": (retrieved or _now().date()).isoformat(),
            "url": url,
        }

    def preflight(self) -> None:
        """Confirm the host is reachable and permitted before a full run.

        Called first so a blocked allowlist produces one clear message rather
        than a hundred identical connection errors.
        """
        probe = f"https://{self.host}/robots.txt"
        try:
            self.fetcher._robots_for(probe)
        except FetchBlocked as exc:
            raise FetchBlocked(
                f"{self.host} is not reachable. Add '{self.host}' and '*.{self.host}' "
                f"to the environment's Custom network allowlist, then start a new "
                f"session. Original error: {exc}"
            ) from exc

    @abstractmethod
    def discover(self) -> Iterator[str]:
        """Yield the URLs worth fetching for the configured season."""

    @abstractmethod
    def parse(self, result: FetchResult) -> ScrapeOutput:
        """Turn one fetched page into dataset fragments."""

    def run(self) -> ScrapeOutput:
        """Fetch and parse everything this adapter can see."""
        self.preflight()
        output = ScrapeOutput()
        fetched = 0

        for url in self.discover():
            try:
                result = self.fetcher.get(url)
            except FetchBlocked as exc:
                output.warnings.append(f"skipped {url}: {exc}")
                output.unread.append(url)
                continue
            fetched += 1

            error: ScrapeError | None = None
            for attempt in range(PARSE_ATTEMPTS):
                if attempt:
                    # The cached body is the one that just failed, so asking
                    # again through the cache would return the same nothing.
                    self.fetcher.forget(url)
                    try:
                        result = self.fetcher.get(url)
                    except FetchBlocked as exc:
                        error = ScrapeError(str(exc))
                        break
                    fetched += 1
                try:
                    output.extend(self.parse(result))
                    error = None
                    break
                except ScrapeError as exc:
                    error = exc

            if error is not None:
                output.warnings.append(f"unparsed {url}: {error}")
                output.unread.append(url)
            elif attempt:
                output.warnings.append(
                    f"re-read {url} on attempt {attempt + 1}; the first "
                    f"response carried no structured data"
                )

        output.warnings.extend(self._notes)
        output.unread.extend(self._unread)
        if fetched == 0:
            output.warnings.append(
                f"{self.source_id}: no page was fetched at all — check the entry paths"
            )
        return output
