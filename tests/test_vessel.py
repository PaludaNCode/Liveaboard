"""The specification table and the diving amenities, read from real markup.

Both fixtures are what a probe run returned from a live vessel page, unedited.
Between them they replace two workarounds: a guest count mined out of marketing
prose, and a nitrox inclusion typed in by hand.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from liveaboard.scrape.vessel import (
    VesselFacts,
    parse_amenities,
    parse_specs,
    read_vessel,
)

FIXTURES = Path(__file__).parent / "fixtures"


def specs() -> str:
    return (FIXTURES / "vessel_specs.html").read_text(encoding="utf-8")


def diving() -> str:
    return (FIXTURES / "vessel_diving.html").read_text(encoding="utf-8")


class TestTheSpecificationTable(unittest.TestCase):
    def test_every_row_is_read(self):
        rows = parse_specs(specs())
        self.assertEqual(rows["Max guests"], "20")
        self.assertEqual(rows["Number of cabins"], "9")
        self.assertEqual(rows["Year built"], "2003")

    def test_the_table_is_many_one_row_lists_not_one_list(self):
        """Each row is its own <dl>, so a parser expecting one table finds one row."""
        self.assertEqual(len(parse_specs(specs())), 10)

    def test_entities_are_decoded(self):
        self.assertEqual(
            parse_specs(specs())["Water capacity"], "2 Aquaset x 5500 L / Day & storage"
        )

    def test_the_guest_count_comes_from_the_table(self):
        """The whole point: 31 of 67 boats have no guest count because the
        number was being read out of marketing prose instead of this."""
        self.assertEqual(read_vessel(specs()).guests, 20)

    def test_cabins_length_and_year_come_free(self):
        facts = read_vessel(specs())
        self.assertEqual((facts.cabins, facts.length_m, facts.year_built), (9, 30, 2003))

    def test_a_unit_after_the_number_is_not_part_of_it(self):
        self.assertEqual(read_vessel(specs()).length_m, 30)

    def test_a_missing_row_is_none_not_zero(self):
        """A vessel that states no guest count has not said it carries nobody."""
        facts = read_vessel("<dl><dt>Length </dt><dd>30 meters</dd></dl>")
        self.assertIsNone(facts.guests)
        self.assertIsNone(facts.cabins)

    def test_an_implausible_count_is_rejected(self):
        """A number that large is a length in feet or a year, not berths."""
        self.assertIsNone(
            read_vessel("<dl><dt>Max guests </dt><dd>1998</dd></dl>").guests
        )

    def test_empty_markup_is_an_empty_record(self):
        self.assertFalse(read_vessel(""))


class TestNitrox(unittest.TestCase):
    """The distinction the site has to get right."""

    def test_free_nitrox_is_read_as_included(self):
        self.assertTrue(read_vessel(specs(), diving()).nitrox_free)

    def test_available_alone_is_not_included(self):
        """Both appear together on boats that charge. Reading "available" as
        included would mark half the fleet's paid nitrox as free."""
        facts = VesselFacts(amenities=("Nitrox available", "DIN Adaptors"))
        self.assertFalse(facts.nitrox_free)
        self.assertTrue(facts.nitrox_available)

    def test_a_boat_that_never_mentions_nitrox_claims_neither(self):
        facts = VesselFacts(amenities=("Dive deck", "Rinse Hoses"))
        self.assertFalse(facts.nitrox_free)
        self.assertFalse(facts.nitrox_available)

    def test_the_amenity_list_survives_its_tailwind_classes(self):
        """The <ul> carries bracketed selectors and escaped ampersands; a
        published dataset once charged a nitrox fee for a fragment of CSS."""
        items = parse_amenities(diving())
        self.assertIn("Free Nitrox", items)
        self.assertNotIn("", items)
        self.assertTrue(all("&" not in i or i == "Audio & video" for i in items))


if __name__ == "__main__":
    unittest.main()
