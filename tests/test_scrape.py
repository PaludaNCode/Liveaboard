"""Tests for link discovery, departure extraction and fetch caching.

Each of these pins down something a live run got wrong.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from liveaboard.scrape.base import (
    DEFAULT_DELAY_SECONDS,
    FetchResult,
    PoliteFetcher,
    ScrapeError,
    ScrapeOutput,
    SourceAdapter,
)
from liveaboard.scrape.liveaboard_com import LiveaboardComAdapter, _iso_date

NOW = datetime(2026, 8, 27, tzinfo=timezone.utc)


def result(body: str, url: str = "https://www.liveaboard.com/diving/egypt/a-boat") -> FetchResult:
    return FetchResult(url=url, status=200, body=body, fetched_at=NOW)


class TestBoatLinks(unittest.TestCase):
    """The search page is a global template linking every destination sold."""

    HTML = """
      <a href="/diving/egypt/blue-horizon">boat</a>
      <a href="https://www.liveaboard.com/diving/egypt/sea-serpent">boat</a>
      <a href="/diving/egypt/red-sea">region</a>
      <a href="/diving/indonesia/adelaar">other destination</a>
      <a href="/diving/antarctica/hondius-antarctica-diving">other destination</a>
      <a href="/river-cruise/rhine/amsterdam">not even diving</a>
    """

    def setUp(self):
        self.found = LiveaboardComAdapter.boat_links(self.HTML)

    def test_finds_egypt_boats_relative_and_absolute(self):
        self.assertIn("/diving/egypt/blue-horizon", self.found)
        self.assertIn("/diving/egypt/sea-serpent", self.found)

    def test_excludes_other_destinations(self):
        """A previous version crawled Antarctica off an Egypt search page."""
        self.assertNotIn("/diving/antarctica/hondius-antarctica-diving", self.found)
        self.assertNotIn("/diving/indonesia/adelaar", self.found)

    def test_excludes_regions_and_dive_sites(self):
        self.assertNotIn("/diving/egypt/red-sea", self.found)

    def test_finds_exactly_the_two_boats(self):
        self.assertEqual(len(self.found), 2)

    def test_rejects_the_dive_sites_the_search_page_actually_links(self):
        """Taken verbatim from a live search page's destination nav.

        The second group came back from a full 79-vessel fee run listed as
        vessels it had failed on -- Gordon, Jackson and Woodhouse are reefs in
        the Straits of Tiran and the Salem Express is a wreck.
        """
        sites = [
            "red-sea", "thistlegorm", "ras-mohammed", "the-brothers",
            "straits-of-tiran", "abu-nuhas", "daedalus", "elphinstone",
            "st-johns", "abu-dabab",
            "gordon-reef", "jackson-reef", "woodhouse-reef",
            "shark-and-yolanda", "salem-express", "hamata", "sinai", "rocky",
        ]
        html = "".join(f'<a href="/diving/egypt/{slug}">x</a>' for slug in sites)
        self.assertEqual(LiveaboardComAdapter.boat_links(html), set())

    def test_keeps_the_boats_the_same_page_links(self):
        html = "".join(
            f'<a href="/diving/egypt/{slug}">x</a>' for slug in ("alia-soul", "all-star-ghani")
        )
        self.assertEqual(len(LiveaboardComAdapter.boat_links(html)), 2)


class TestIsoDate(unittest.TestCase):
    def test_plain_date_passes_through(self):
        self.assertEqual(_iso_date("2027-05-01"), "2027-05-01")

    def test_datetime_is_truncated(self):
        self.assertEqual(_iso_date("2027-05-01T09:30:00+02:00"), "2027-05-01")

    def test_junk_is_refused(self):
        for value in ("next Tuesday", "2027-13-45", "", None, 20270501):
            self.assertIsNone(_iso_date(value), value)


class TestDepartureExtraction(unittest.TestCase):
    """Departures live in Event nodes, not in the Product's aggregate offer."""

    HTML = """
    <html><head>
    <script type="application/ld+json">
    {"@type":"Product","name":"MY Example","description":"A vessel.",
     "offers":{"@type":"AggregateOffer","lowPrice":"900","priceCurrency":"USD"}}
    </script>
    <script type="application/ld+json">
    {"@type":"Event","name":"Brothers & Daedalus","startDate":"2027-05-01T00:00:00",
     "endDate":"2027-05-08","offers":{"@type":"Offer","price":"1,450",
     "priceCurrency":"USD","availability":"InStock","url":"/BookingStep1?x=1"}}
    </script>
    <script type="application/ld+json">
    {"@type":"Event","name":"No price","startDate":"2027-06-05","endDate":"2027-06-12"}
    </script>
    <script type="application/ld+json">
    {"@type":"Event","name":"No dates",
     "offers":{"@type":"Offer","price":"1200","priceCurrency":"USD"}}
    </script>
    </head><body></body></html>
    """

    def setUp(self):
        adapter = LiveaboardComAdapter(PoliteFetcher(snapshot_dir="/tmp/unused"))
        self.output = adapter.parse(result(self.HTML))

    def test_only_the_complete_event_becomes_a_departure(self):
        self.assertEqual(len(self.output.departures), 1)

    def test_dates_and_price_are_read(self):
        departure = self.output.departures[0]
        self.assertEqual(departure["start"], "2027-05-01")
        self.assertEqual(departure["end"], "2027-05-08")
        self.assertEqual(departure["price"], {"amount": 1450.0, "currency": "USD"})

    def test_price_keeps_its_quoted_currency(self):
        """Conversion belongs in pricing.py, which records the rate it used."""
        self.assertEqual(self.output.departures[0]["price"]["currency"], "USD")

    def test_booking_url_is_carried(self):
        self.assertEqual(self.output.departures[0]["booking_url"], "/BookingStep1?x=1")

    def test_departure_is_tied_to_the_vessel_page(self):
        """The itinerary is formed later, by grouping departures at promote time."""
        self.assertEqual(self.output.departures[0]["boat_slug"], "a-boat")

    def test_the_product_becomes_an_itinerary(self):
        self.assertEqual(len(self.output.itineraries), 1)
        self.assertEqual(self.output.itineraries[0]["name"], "MY Example")

    def test_aggregate_offer_is_not_treated_as_a_price(self):
        """A 'from' price is not what any specific sailing costs."""
        prices = [d["price"]["amount"] for d in self.output.departures]
        self.assertNotIn(900.0, prices)


class TestFetchCache(unittest.TestCase):
    def test_second_get_is_served_from_cache(self):
        """Listing pages are read twice by design; paying twice is slow and rude."""
        fetcher = PoliteFetcher(snapshot_dir="/tmp/unused")
        stored = result("<html></html>", url="https://example.test/a")
        fetcher._cache[stored.url] = stored

        again = fetcher.get(stored.url)
        self.assertTrue(again.from_cache)
        self.assertEqual(again.body, stored.body)

class TestSeasonMonthSelector(unittest.TestCase):
    """A vessel page shows the month it is asked for, not the season by default."""

    def test_one_query_per_season_month(self):
        """The selector returns that month alone; a live run proved it.

        Asking only for the opening month produced 250 departures, every one
        of them in May.
        """
        from liveaboard.scrape.liveaboard_com import (
            SEASON_MONTHS, SEASON_QUERIES, SEASON_YEAR,
        )

        self.assertEqual(len(SEASON_QUERIES), len(SEASON_MONTHS))
        self.assertEqual(SEASON_QUERIES[0], f"?m={SEASON_MONTHS[0]}/{SEASON_YEAR}")
        self.assertEqual(SEASON_QUERIES[-1], f"?m={SEASON_MONTHS[-1]}/{SEASON_YEAR}")

    def test_listing_pages_are_not_treated_as_parse_failures(self):
        """They are crawled for links; calling that a failure buries real ones."""
        adapter = LiveaboardComAdapter(PoliteFetcher(snapshot_dir="/tmp/unused"))
        output = adapter.parse(
            result("<html><body>no structured data</body></html>",
                   url="https://www.liveaboard.com/diving/search/egypt/may/2027")
        )
        self.assertEqual(output.warnings, [])
        self.assertTrue(output.is_empty)


class TestArchive(unittest.TestCase):
    """Keep what the page published, not only what we chose to read.

    Current prices can always be re-scraped; the prices as they stood on a
    given day cannot. A field that starts mattering next month would otherwise
    arrive attached to next month's data, with today's gone.
    """

    HTML = """
    <html><head>
    <script type="application/ld+json">
    {"@type":"Product","name":"MY Example","description":"A vessel.",
     "aggregateRating":{"@type":"AggregateRating","ratingValue":"9.1","reviewCount":"214"},
     "numberOfRooms":10,"occupancy":20,"amenityFeature":["Nitrox","Jacuzzi"],
     "offers":{"@type":"AggregateOffer","lowPrice":"900","priceCurrency":"USD"}}
    </script>
    <script type="application/ld+json">
    {"@type":"Event","name":"Brothers","startDate":"2027-05-01","endDate":"2027-05-08",
     "remainingAttendeeCapacity":3,
     "offers":{"@type":"Offer","price":"1450","priceCurrency":"USD"}}
    </script>
    </head><body></body></html>
    """

    def setUp(self):
        adapter = LiveaboardComAdapter(PoliteFetcher(snapshot_dir="/tmp/unused"))
        self.output = adapter.parse(result(self.HTML))
        self.page = self.output.archive[0]

    def test_the_page_is_archived_once(self):
        self.assertEqual(len(self.output.archive), 1)

    def test_it_records_where_and_when(self):
        self.assertEqual(self.page["url"], result(self.HTML).url)
        self.assertEqual(self.page["retrieved"], "2026-08-27")
        self.assertTrue(self.page["digest"])

    def test_fields_nothing_parses_today_are_kept(self):
        product = self.page["nodes"][0]
        self.assertEqual(product["numberOfRooms"], 10)
        self.assertEqual(product["occupancy"], 20)
        self.assertEqual(product["aggregateRating"]["reviewCount"], "214")
        self.assertEqual(product["amenityFeature"], ["Nitrox", "Jacuzzi"])

    def test_remaining_capacity_survives(self):
        """Availability on a given day is the one thing a re-scrape cannot recover."""
        event = self.page["nodes"][1]
        self.assertEqual(event["remainingAttendeeCapacity"], 3)

    def test_a_listing_page_archives_nothing(self):
        adapter = LiveaboardComAdapter(PoliteFetcher(snapshot_dir="/tmp/unused"))
        output = adapter.parse(
            result("<html><body>nav only</body></html>",
                   url="https://www.liveaboard.com/diving/search/egypt/may/2027")
        )
        self.assertEqual(output.archive, [])

    def test_archives_merge_across_pages(self):
        from liveaboard.scrape.base import ScrapeOutput

        combined = ScrapeOutput()
        combined.extend(self.output)
        combined.extend(self.output)
        self.assertEqual(len(combined.archive), 2)


class TestCrawlDelay(unittest.TestCase):
    """The pace is a courtesy, and a stated one is not ours to shorten."""

    class Robots:
        def __init__(self, stated):
            self.stated = stated

        def crawl_delay(self, agent):
            return self.stated

    def fetcher(self, stated=None):
        f = PoliteFetcher(snapshot_dir="/tmp/unused")
        f._robots["www.liveaboard.com"] = self.Robots(stated)
        f._robots["example.test"] = self.Robots(stated)
        return f

    URL = "https://www.liveaboard.com/diving/egypt/alia-soul"

    def test_a_checked_host_uses_its_recorded_pace(self):
        """liveaboard.com states no Crawl-delay, so two seconds is our choice."""
        self.assertEqual(self.fetcher().crawl_delay(self.URL), 2.0)

    def test_an_unchecked_host_stays_slow(self):
        """A source nobody has read robots.txt for starts conservative."""
        self.assertEqual(
            self.fetcher().crawl_delay("https://example.test/page"),
            DEFAULT_DELAY_SECONDS,
        )

    def test_a_stated_delay_beats_our_faster_choice(self):
        """Nothing in CHECKED_HOSTS may make this crawler faster than asked."""
        self.assertEqual(self.fetcher(stated=10).crawl_delay(self.URL), 10.0)

    def test_a_stated_delay_slower_than_ours_is_honoured(self):
        self.assertEqual(self.fetcher(stated=6).crawl_delay(self.URL), 6.0)

    def test_we_do_not_speed_up_to_a_stated_delay(self):
        """A site permitting one second does not oblige us to take it."""
        self.assertEqual(self.fetcher(stated=1).crawl_delay(self.URL), 2.0)


if __name__ == "__main__":
    unittest.main()


class TestBarrenVesselsAreSkippedButNotForgotten(unittest.TestCase):
    """Twelve vessels sell nothing in the season and cost 48 fetches a night.

    Skipping them is worth doing and dangerous to do quietly: a cache that
    never expires is a fleet that silently shrinks.
    """

    def test_a_skipped_vessel_is_not_fetched(self):
        from liveaboard.scrape.liveaboard_com import LiveaboardComAdapter

        listing = '<a href="/diving/egypt/alia-soul">A</a><a href="/diving/egypt/topaz">B</a>'

        class Stub(LiveaboardComAdapter):
            def _listing_urls(self):
                return iter(["https://www.liveaboard.com/diving/search/egypt/may/2027"])

        adapter = Stub(_FetcherReturning(listing))
        adapter.skip_vessels = frozenset({"topaz"})
        urls = list(adapter.discover())
        self.assertTrue(any("alia-soul" in u for u in urls))
        self.assertFalse(any("topaz" in u for u in urls))

    def test_the_skip_is_announced_every_run(self):
        """A silent skip list is how a fleet quietly shrinks."""
        from liveaboard.scrape.liveaboard_com import LiveaboardComAdapter

        listing = '<a href="/diving/egypt/topaz">B</a>'

        class Stub(LiveaboardComAdapter):
            def _listing_urls(self):
                return iter(["https://www.liveaboard.com/diving/search/egypt/may/2027"])

        adapter = Stub(_FetcherReturning(listing))
        adapter.skip_vessels = frozenset({"topaz"})
        list(adapter.discover())
        self.assertTrue(any("skipped 1 vessel" in n for n in adapter._notes))

    def test_nothing_is_skipped_by_default(self):
        """The adapter never decides on its own to stop looking at a boat."""
        from liveaboard.scrape.liveaboard_com import LiveaboardComAdapter

        self.assertEqual(LiveaboardComAdapter.skip_vessels, frozenset())


class _FetcherReturning:
    """Minimal fetcher stub: every request returns the same listing body."""

    def __init__(self, body: str):
        self.body = body

    def get(self, url: str):
        from datetime import datetime, timezone

        from liveaboard.scrape.base import FetchResult

        return FetchResult(
            url=url, status=200, body=self.body,
            fetched_at=datetime.now(timezone.utc), from_cache=False,
        )


class TestAPageThatAnswersNothingIsAskedAgain(unittest.TestCase):
    """A response with no structured data is not a month with no trips.

    Fourteen vessel-month pages came back with no JSON-LD on 2026-08-28 and
    the crawl believed every one of them, deleting 49 real, bookable sailings
    -- DUNE Longara's whole May, still on sale at the source. A probe re-read
    all fourteen: thirteen answered in full on the first retry, and the
    fourteenth was a genuinely empty month. The failure is the response.
    """

    class _Adapter(SourceAdapter):
        source_id = "test"
        host = "example.test"

        def __init__(self, fetcher, fail_times: int):
            super().__init__(fetcher)
            self.fail_times = fail_times
            self.parses = 0

        def preflight(self) -> None:
            """No robots check: the stub fetcher is not a PoliteFetcher."""

        def discover(self):
            yield "https://example.test/boat?m=5/2027"

        def parse(self, result):
            self.parses += 1
            if self.parses <= self.fail_times:
                raise ScrapeError("no JSON-LD Event or Product node")
            out = ScrapeOutput()
            out.departures.append({"id": "d1"})
            return out

    class _CountingFetcher:
        """Counts real requests, and honours forget() like the real one."""

        def __init__(self):
            self.requests = 0
            self._cache: dict[str, object] = {}

        def forget(self, url):
            self._cache.pop(url, None)

        def get(self, url):
            from datetime import datetime, timezone

            if url in self._cache:
                return self._cache[url]
            self.requests += 1
            result = FetchResult(url=url, status=200, body="<html></html>",
                                 fetched_at=datetime.now(timezone.utc))
            self._cache[url] = result
            return result

    def test_a_transient_empty_response_is_retried_and_recovered(self):
        fetcher = self._CountingFetcher()
        adapter = self._Adapter(fetcher, fail_times=1)
        output = adapter.run()
        self.assertEqual(len(output.departures), 1)
        self.assertEqual(output.unread, [])
        self.assertEqual(fetcher.requests, 2, "the retry must be a real request")
        self.assertTrue(any("re-read" in w for w in output.warnings),
                        "a recovered page is said out loud, not silently fixed")

    def test_a_page_that_fails_twice_is_still_reported_unread(self):
        """carry_unread is the net under this, and it needs to be told."""
        fetcher = self._CountingFetcher()
        adapter = self._Adapter(fetcher, fail_times=99)
        output = adapter.run()
        self.assertEqual(len(output.unread), 1)
        self.assertTrue(any("unparsed" in w for w in output.warnings))

    def test_a_page_that_reads_first_time_is_fetched_once(self):
        """The retry must cost nothing on the 254 pages that are fine."""
        fetcher = self._CountingFetcher()
        adapter = self._Adapter(fetcher, fail_times=0)
        output = adapter.run()
        self.assertEqual(fetcher.requests, 1)
        self.assertEqual(len(output.departures), 1)
        self.assertFalse(any("re-read" in w for w in output.warnings))
