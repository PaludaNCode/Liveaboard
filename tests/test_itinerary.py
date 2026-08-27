"""What one trip's itinerary fragment states.

The fixture is markup a probe returned from the live endpoint, trimmed to the
parts anything reads and otherwise verbatim -- minified, with the optional
closing tags the page really omits.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from liveaboard.scrape.itinerary import (
    TripDetail,
    min_logged_dives,
    parse_regions,
    parse_trip,
)

FIXTURE = Path(__file__).parent / "fixtures" / "itinerary_fragment.html"


def markup() -> str:
    return FIXTURE.read_text(encoding="utf-8")


class TestWhereTheTripGoes(unittest.TestCase):
    """The reason this endpoint is worth fetching at all."""

    def test_the_curated_region_list_is_read(self):
        self.assertEqual(
            parse_regions(markup()),
            ("The Brothers", "Elphinstone", "Daedalus", "Abu Dabab"),
        )

    def test_the_whole_sea_is_not_a_place(self):
        """Every trip here is in the Red Sea, so it selects all 314 rows."""
        self.assertNotIn("Red Sea", parse_regions(markup()))

    def test_both_quoted_and_bare_titles_are_read(self):
        """The minifier quotes an attribute only when it contains a space, so
        `title=Daedalus` sits beside `title="Abu Dabab"` in one list."""
        regions = parse_regions(markup())
        self.assertIn("Daedalus", regions)      # title=Daedalus
        self.assertIn("Abu Dabab", regions)     # title="Abu Dabab"

    def test_the_list_is_found_by_its_heading_not_its_classes(self):
        """Tailwind class strings carry brackets and change with the layout."""
        stripped = markup().replace('class=leading-snug', "").replace(
            'class="relative pl-4.5 py-1 truncate"', "")
        self.assertEqual(len(parse_regions(stripped)), 4)

    def test_markup_without_the_block_yields_nothing(self):
        self.assertEqual(parse_regions("<div>Boat features</div>"), ())
        self.assertEqual(parse_regions(""), ())


class TestTheOverviewRows(unittest.TestCase):
    def setUp(self):
        self.detail = parse_trip(markup())

    def test_a_per_trip_dive_count(self):
        """Stated for this sailing, not for the hull. The dataset's counts were
        per vessel and had to be pinned to one trip length to stay honest."""
        self.assertEqual(self.detail.dives, 18)

    def test_a_per_trip_guest_count(self):
        self.assertEqual(self.detail.guests, 20)

    def test_the_stated_entry_bar(self):
        self.assertEqual(
            self.detail.experience,
            "Advanced Open Water - 50 minimum logged dives required.",
        )

    def test_rows_are_read_through_omitted_closing_tags(self):
        """`<dt>Dives <dd>18` with no `</dt>` is what the page actually sends."""
        self.assertIn("<dt class=text-sm>Dives <dd", markup())
        self.assertEqual(self.detail.dives, 18)

    def test_an_absent_row_is_none_not_zero(self):
        detail = parse_trip("<dl><dt>Country <dd>Egypt</dl>")
        self.assertIsNone(detail.dives)
        self.assertIsNone(detail.guests)

    def test_an_implausible_count_is_rejected(self):
        """A fortnight at four a day is 56; more is a misparse."""
        self.assertIsNone(parse_trip("<dt>Dives <dd>1998 dives in total").dives)
        self.assertIsNone(parse_trip("<dt>Group Size <dd>Up to 900 guests").guests)

    def test_an_empty_fragment_is_an_empty_record(self):
        self.assertFalse(parse_trip(""))
        self.assertFalse(TripDetail())


class TestTheLoggedDiveBar(unittest.TestCase):
    def test_a_stated_number_is_read(self):
        self.assertEqual(
            min_logged_dives("Advanced Open Water - 50 minimum logged dives required."),
            50,
        )

    def test_a_certification_with_no_number_asks_for_none(self):
        """"Advanced Open Water" alone states a certification and no dive
        count. Inventing one would soften a stated safety requirement."""
        self.assertEqual(min_logged_dives("Advanced Open Water required."), 0)
        self.assertEqual(min_logged_dives(None), 0)


if __name__ == "__main__":
    unittest.main()
