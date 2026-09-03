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

import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

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


class TestForcingARecheckKeepsTheRecord(unittest.TestCase):
    """`--recheck-all` ignores the skip list for one run without losing it.

    The file's own note used to say *delete this file to force a full crawl*,
    and that is a destructive instruction for something that should be a flag:
    the dates in it are what the seven-day clock counts from, so deleting the
    file makes every vessel in it read as never-checked rather than as checked
    and then re-checked. It also cannot be done from a dispatched run, which
    is where somebody actually wants it -- 13 boats were held off the page and
    the only lever was a commit deleting committed data.

    The record has to survive because the write path needs it: a vessel this
    run finds selling is popped from it, and one still empty is re-stamped.
    Return an empty record and the first of those silently stops happening.
    """

    def barren(self, tmp, vessels):
        path = Path(tmp) / "barren.json"
        path.write_text(json.dumps({"vessels": vessels}), encoding="utf-8")
        return path

    def test_a_fresh_verdict_is_skipped_normally_and_visited_when_forced(self):
        from liveaboard.cli import _barren

        today = date.today().isoformat()
        with tempfile.TemporaryDirectory() as tmp:
            path = self.barren(tmp, {"ashrafi": today, "lady-m": today})
            skip, record = _barren(path)
            self.assertEqual(skip, {"ashrafi", "lady-m"})
            forced, forced_record = _barren(path, recheck=True)
            self.assertEqual(forced, set(), "--recheck-all still skipped a vessel")
            self.assertEqual(forced_record, record,
                             "the record must survive, or the write path "
                             "cannot drop a vessel that is now selling")

    def test_an_expired_verdict_is_visited_either_way(self):
        """The flag is about hurrying the clock, not about overriding it."""
        from liveaboard.cli import BARREN_RECHECK_DAYS, _barren

        stale = (date.today() - timedelta(days=BARREN_RECHECK_DAYS + 1)).isoformat()
        with tempfile.TemporaryDirectory() as tmp:
            path = self.barren(tmp, {"ashrafi": stale})
            self.assertEqual(_barren(path)[0], set())
            self.assertEqual(_barren(path, recheck=True)[0], set())

    def test_the_workflow_can_ask_for_it(self):
        """A flag no dispatched run can set is a flag nobody can use here: the
        crawl is 320 requests and does not run on anybody's laptop."""
        yml = (Path(__file__).resolve().parents[1]
               / ".github" / "workflows" / "refresh.yml").read_text(encoding="utf-8")
        self.assertIn("recheck_skipped:", yml)
        self.assertIn("--recheck-all", yml)

    def test_the_note_no_longer_tells_anyone_to_delete_the_file(self):
        """The note is written into the file every run, so it is the one place
        a person looks. It pointed at the destructive option only."""
        source = (Path(__file__).resolve().parents[1]
                  / "src" / "liveaboard" / "cli.py").read_text(encoding="utf-8")
        note = source[source.index("Vessels that published no departure"):]
        note = note[: note.index('"vessels"')]
        self.assertIn("--recheck-all", note)


class TestAVesselNobodyAskedIsNotOneWithNothingOnSale(unittest.TestCase):
    """The same distinction, one layer further down: in what the page *says*.

    The crawl keeps it — `discover` records a skip through `not_looked_at`, and
    the departures are carried rather than deleted. `promote` lost it: a boat
    with no candidate departures is indistinguishable from a boat nobody asked
    about, so a sailing PADI sells on a skipped vessel was published as
    **"liveaboard.com does not list this sailing"** — a result for a page this
    site did not open.

    It is not hypothetical. `data/barren.json` holds 13 vessels, and PADI sells
    **87 season sailings on four of them** — Bella 2, Bella 3, Eriny and Blue
    Pearl — every one of which carried that sentence. All four have a
    liveaboard.com vessel page the fee scraper read in full, 7 to 13 extras
    each, so the first source plainly knows the boat.

    Same rule as `fees_known`: no fee lines means nobody looked, not that there
    are none.
    """

    SEASON = (date(2027, 5, 1), date(2027, 8, 31))

    def rows(self, not_asked=()):
        from liveaboard.promote import promote

        from test_promote import candidate, departure

        sailings = {"eriny::2027-06-05": {
            "boat": "eriny", "slug": "eriny", "start": "2027-06-05",
            "end": "2027-06-12", "nights": 7, "price": 1200.0, "currency": "EUR",
            "itinerary": "Sinai Classic (Sharm El Sheikh - Sharm El Sheikh) 7 Nights",
        }}
        payload = promote(
            {**candidate([departure()]), "not_asked": list(not_asked)},
            season=self.SEASON,
            padi_departures={"collected": "2026-08-29", "departures": sailings},
        )
        return [d for d in payload["departures"] if d.get("padi_only")]

    def test_a_sailing_on_a_visited_vessel_states_the_stronger_claim(self):
        row, = self.rows()
        self.assertTrue(row["padi_only"])
        self.assertNotIn("not_asked", row)

    def test_a_sailing_on_a_skipped_vessel_states_the_weaker_one(self):
        row, = self.rows(not_asked=["eriny"])
        self.assertTrue(row["padi_only"])
        self.assertTrue(row["not_asked"])

    def test_the_row_is_still_published(self):
        """The berth is real and PADI is really selling it. What changes is the
        sentence about the other seller, not whether the sailing appears."""
        self.assertEqual(len(self.rows(not_asked=["eriny"])), 1)

    def test_a_candidate_with_no_such_key_behaves_as_before(self):
        """The field arrives with the next crawl. A dataset promoted from a
        candidate written before it must not change."""
        from liveaboard.promote import promote

        from test_promote import candidate, departure

        payload = promote(candidate([departure()]), season=self.SEASON)
        self.assertFalse(any(d.get("not_asked") for d in payload["departures"]))
