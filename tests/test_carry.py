"""A page this run did not read is not a page that said nothing.

Two ways to not read one, and the site lost real trips to both.

A vessel page is fetched once per season month, so one unreadable response
empties that boat's month while the other three come back fine. On 2026-08-28
fourteen vessel-month pages came back with no structured data at all, and DUNE
Longara's five May sailings were deleted from the site and reported as
withdrawn while liveaboard.com was still selling every one of them.

The barren skip list then did the same thing with nothing going wrong at all:
it holds a vessel back for a week to save four requests, and AVO's and Blue's
three sailings were dropped and reported as withdrawn by a run that simply
never asked. A probe found all three still on sale.
"""

from __future__ import annotations

import unittest
from datetime import date, timedelta

from liveaboard.cli import CARRY_MAX_DAYS, carry_unread

TODAY = date(2026, 8, 28)
MAY = "https://www.liveaboard.com/diving/egypt/dune-longara?m=5/2027"
JUNE = "https://www.liveaboard.com/diving/egypt/dune-longara?m=6/2027"


def departure(dep_id, url=MAY, retrieved="2026-08-27", slug="dune-longara"):
    return {
        "id": dep_id,
        "boat_slug": slug,
        "name": "Golden Triangle (Safaga - Port Ghalib)",
        "start": "2027-05-15",
        "end": "2027-05-22",
        "price": {"amount": 1200.0, "currency": "USD"},
        "provenance": {"kind": "scraped", "source_id": "liveaboard.com",
                       "retrieved": retrieved, "url": url},
    }


def vessel(slug="dune-longara", retrieved="2026-08-27"):
    return {
        "id": slug, "name": "DUNE Longara", "boat": "DUNE Longara",
        "provenance": {"kind": "scraped", "source_id": "liveaboard.com",
                       "retrieved": retrieved, "url": MAY},
        "fees": [],
    }


class TestCarryingUnreadPages(unittest.TestCase):
    def test_departures_from_an_unreadable_page_are_kept(self):
        previous = {"departures": [departure("d1"), departure("d2")],
                    "itineraries": [vessel()]}
        kept, _, notes = carry_unread(previous, [MAY], TODAY)
        self.assertEqual([d["id"] for d in kept], ["d1", "d2"])
        self.assertEqual(len(notes), 1)
        self.assertIn("was not read this run", notes[0])

    def test_a_page_that_read_fine_is_not_carried(self):
        """Only the pages that failed. Carrying a page this run *did* read
        would republish yesterday's prices over today's."""
        previous = {"departures": [departure("d1", url=MAY),
                                   departure("d2", url=JUNE)],
                    "itineraries": []}
        kept, _, _ = carry_unread(previous, [MAY], TODAY)
        self.assertEqual([d["id"] for d in kept], ["d1"])

    def test_nothing_is_carried_when_every_page_read(self):
        previous = {"departures": [departure("d1")], "itineraries": [vessel()]}
        self.assertEqual(carry_unread(previous, [], TODAY), ([], [], []))

    def test_a_first_run_has_nothing_to_carry(self):
        self.assertEqual(carry_unread(None, [MAY], TODAY), ([], [], []))

    def test_stale_rows_are_dropped_rather_than_asserted_forever(self):
        """A page that has failed for a fortnight is one we can no longer see.
        Carrying it indefinitely would keep claiming a sailing exists on the
        strength of a reading nobody can still vouch for."""
        old = (TODAY - timedelta(days=CARRY_MAX_DAYS + 1)).isoformat()
        previous = {"departures": [departure("d1", retrieved=old)],
                    "itineraries": [vessel(retrieved=old)]}
        kept, boats, notes = carry_unread(previous, [MAY], TODAY)
        self.assertEqual(kept, [])
        self.assertEqual(boats, [])
        self.assertEqual(notes, [])

    def test_a_row_exactly_at_the_limit_is_still_carried(self):
        edge = (TODAY - timedelta(days=CARRY_MAX_DAYS)).isoformat()
        previous = {"departures": [departure("d1", retrieved=edge)],
                    "itineraries": []}
        kept, _, _ = carry_unread(previous, [MAY], TODAY)
        self.assertEqual(len(kept), 1)

    def test_an_undated_row_is_not_carried(self):
        """Without a reading date there is no way to know how old it is, and
        the safe answer to that is not "forever"."""
        row = departure("d1")
        row["provenance"].pop("retrieved")
        kept, _, _ = carry_unread({"departures": [row], "itineraries": []},
                                  [MAY], TODAY)
        self.assertEqual(kept, [])

    def test_the_carried_price_keeps_the_date_it_was_read(self):
        """The page says when each price was last read. A carried row must not
        claim to have been read today -- that would be the site inventing a
        reading, which is the thing it exists to catch others doing."""
        previous = {"departures": [departure("d1", retrieved="2026-08-20")],
                    "itineraries": []}
        kept, _, _ = carry_unread(previous, [MAY], TODAY)
        self.assertEqual(kept[0]["provenance"]["retrieved"], "2026-08-20")

    def test_the_vessel_record_comes_with_its_departures(self):
        """A boat whose every page failed has no record in this run either, and
        a departure promote cannot find a vessel for is a departure dropped."""
        previous = {"departures": [departure("d1")], "itineraries": [vessel()]}
        _, boats, _ = carry_unread(previous, [MAY], TODAY)
        self.assertEqual([b["id"] for b in boats], ["dune-longara"])

    def test_no_vessel_record_is_carried_for_a_boat_nothing_was_kept_for(self):
        previous = {"departures": [], "itineraries": [vessel()]}
        _, boats, _ = carry_unread(previous, [MAY], TODAY)
        self.assertEqual(boats, [])




class TestASkippedVesselIsNotAWithdrawnOne(unittest.TestCase):
    """The barren list holds a vessel back for a week to save four requests.

    Nothing goes wrong, and the departures vanished anyway: AVO and Blue lost
    three real, bookable sailings to a run that never asked the source about
    them, and the change report called it a withdrawal. A probe found all
    three still on sale.
    """

    LISTING = ('<a href="/diving/egypt/avo">A</a>'
               '<a href="/diving/egypt/alia-soul">B</a>')

    def _adapter(self):
        from liveaboard.scrape.liveaboard_com import LiveaboardComAdapter

        class Stub(LiveaboardComAdapter):
            def _listing_urls(self):
                return iter(["https://www.liveaboard.com/diving/search/egypt/may/2027"])

        adapter = Stub(_FetcherReturning(self.LISTING))
        adapter.skip_vessels = frozenset({"avo"})
        return adapter

    def test_a_skipped_vessel_records_the_pages_it_did_not_read(self):
        adapter = self._adapter()
        list(adapter.discover())
        skipped = [u for u in adapter._unread if "avo" in u]
        # One per season month: the whole boat's season is unseen, not one page.
        self.assertEqual(len(skipped), 4)
        self.assertFalse([u for u in adapter._unread if "alia-soul" in u])

    def test_those_pages_reach_the_output_so_carry_can_see_them(self):
        """The link between the two halves. Without it discover() knows and
        nothing downstream does, which is how this stayed invisible."""
        adapter = self._adapter()
        adapter.preflight = lambda: None
        output = adapter.run()
        self.assertTrue(any("avo" in u for u in output.unread))

    def test_a_skipped_vessels_departures_are_carried_not_deleted(self):
        url = "https://www.liveaboard.com/diving/egypt/avo?m=7/2027"
        previous = {
            "departures": [departure("avo-1", url=url, slug="avo")],
            "itineraries": [vessel(slug="avo")],
        }
        kept, boats, notes = carry_unread(previous, [url], TODAY)
        self.assertEqual([d["id"] for d in kept], ["avo-1"])
        self.assertEqual([b["id"] for b in boats], ["avo"])
        self.assertIn("was not read this run", notes[0])

    def test_a_vessel_record_is_carried_once_not_once_per_month(self):
        """The candidate holds a record per month page, so a boat whose whole
        season is carried would otherwise arrive four times over."""
        url = "https://www.liveaboard.com/diving/egypt/avo?m=7/2027"
        previous = {
            "departures": [departure("avo-1", url=url, slug="avo")],
            "itineraries": [vessel(slug="avo"), vessel(slug="avo"),
                            vessel(slug="avo"), vessel(slug="avo")],
        }
        _, boats, _ = carry_unread(previous, [url], TODAY)
        self.assertEqual([b["id"] for b in boats], ["avo"])

    def test_the_carry_outlasts_the_skip(self):
        """A skipped vessel is re-read within a week, so the carry must hold at
        least that long or the departures fall out before anyone looks again."""
        from liveaboard.cli import BARREN_RECHECK_DAYS

        self.assertGreater(CARRY_MAX_DAYS, BARREN_RECHECK_DAYS)


class _FetcherReturning:
    """Minimal fetcher stub: every request returns the same listing body."""

    def __init__(self, body: str):
        self.body = body
        self._cache: dict = {}

    def forget(self, url):
        self._cache.pop(url, None)

    def get(self, url: str):
        from datetime import datetime, timezone

        from liveaboard.scrape.base import FetchResult

        return FetchResult(
            url=url, status=200, body=self.body,
            fetched_at=datetime.now(timezone.utc), from_cache=False,
        )


if __name__ == "__main__":
    unittest.main()
