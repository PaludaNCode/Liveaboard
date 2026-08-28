"""A page this run could not read is not a page that said nothing.

The failure these pin is a real one, reported by the site's owner. A vessel
page is fetched once per season month, so one unreadable response empties that
boat's month while the other three come back fine. On 2026-08-28 fourteen
vessel-month pages came back with no structured data at all, and DUNE
Longara's five May sailings were deleted from the site and reported as
withdrawn while liveaboard.com was still selling every one of them.
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
        self.assertIn("could not be read", notes[0])

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


if __name__ == "__main__":
    unittest.main()
