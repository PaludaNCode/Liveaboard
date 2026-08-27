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
"""Conservative default when a site states no ``Crawl-delay``.

Slower than necessary on purpose: this scrape has a whole day to finish and
nothing is gained by leaning on someone else's origin server.
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
    _robots: dict[str, urllib.robotparser.RobotFileParser] = field(default_factory=dict)
    _last_request: dict[str, float] = field(default_factory=dict)

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
        """The site's stated delay, or our conservative default if it states none."""
        stated = self._robots_for(url).crawl_delay(self.user_agent)
        return max(float(stated), self.delay) if stated else self.delay

    def _wait(self, url: str) -> None:
        host = urlparse(url).netloc
        delay = self.crawl_delay(url)
        elapsed = time.monotonic() - self._last_request.get(host, 0.0)
        if elapsed < delay:
            time.sleep(delay - elapsed)
        self._last_request[host] = time.monotonic()

    def get(self, url: str) -> FetchResult:
        """Fetch one URL, refusing if robots.txt disallows it."""
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
        self.snapshot(result)
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

    def extend(self, other: ScrapeOutput) -> None:
        self.operators.extend(other.operators)
        self.boats.extend(other.boats)
        self.itineraries.extend(other.itineraries)
        self.departures.extend(other.departures)
        self.warnings.extend(other.warnings)

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

    def __init__(self, fetcher: PoliteFetcher) -> None:
        self.fetcher = fetcher

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
        for url in self.discover():
            try:
                result = self.fetcher.get(url)
            except FetchBlocked as exc:
                output.warnings.append(f"skipped {url}: {exc}")
                continue
            try:
                output.extend(self.parse(result))
            except ScrapeError as exc:
                output.warnings.append(f"unparsed {url}: {exc}")
        return output
