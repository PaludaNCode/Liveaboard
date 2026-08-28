"""What the booking page says a departure costs, cabin by cabin.

Both fixtures are markup a probe run returned from live booking pages, trimmed
at each end to a whole tag and otherwise untouched -- entities, unquoted
attributes, minified spacing and all. Everything here is checked against those
rather than against a shape invented to be easy to parse, because the three
bugs this parser has already had were all things a tidied fixture would have
hidden:

* the first dump stopped inside the guest-count select, so the single-occupancy
  surcharge below it was never seen and blocks were cut on the wrong boundary;
* ``title=Suite`` is unquoted -- the site quotes an attribute only when it has
  to -- so a pattern requiring quotes named one of Iceberg's three cabins after
  its database id;
* the "Save 10%" badge is an ``<li><span>`` in the price list, and reading
  every ``<li><span>`` in the block filed it as a cabin amenity.

The two pages are deliberately different shapes: Iceberg discounts all three
cabins and prints a berth banner on each; Alia Soul discounts none, prints a
banner on two of three, and has a twelve-berth cabin.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from liveaboard.scrape.cabins import parse_cabins

FIXTURES = Path(__file__).parent / "fixtures"

# https://www.liveaboard.com/BookingStep1?tourid=415714&boatid=6240
# Iceberg, 06-10 Jun 2027, "10% Off: Hurghada North".
DISCOUNTED = FIXTURES / "booking_cabins.html"

# https://www.liveaboard.com/BookingStep1?tourid=421166&boatid=6565
# Alia Soul, 19-26 May 2027, "Marine Park South".
UNDISCOUNTED = FIXTURES / "booking_cabins_undiscounted.html"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestTheCabinLadder(unittest.TestCase):
    def setUp(self):
        self.reading = parse_cabins(read(DISCOUNTED), "USD")

    def test_every_cabin_is_read_in_the_order_the_page_lists_them(self):
        self.assertEqual(
            [c.name for c in self.reading.cabins],
            ["Cabin 3", "Cabin 1 & 2", "Suite"],
        )

    def test_a_one_word_name_is_read_though_its_attribute_is_unquoted(self):
        # `title=Suite`, beside `title="Cabin 1 &amp; 2"`. The site quotes only
        # what it must, and requiring the quotes loses the name silently --
        # the cabin still parses, it is just called "Cabin 10766".
        suite = self.reading.cabins[2]
        self.assertEqual(suite.name, "Suite")
        self.assertNotIn("10766", suite.name)

    def test_an_entity_in_a_name_is_decoded(self):
        self.assertEqual(self.reading.cabins[1].name, "Cabin 1 & 2")

    def test_both_prices_are_kept_and_told_apart(self):
        cabin = self.reading.cabins[0]
        self.assertEqual(cabin.list_price, 688.0)
        self.assertEqual(cabin.price, 619.0)
        self.assertTrue(cabin.is_discounted)

    def test_the_ladder_is_a_ladder(self):
        self.assertEqual([c.price for c in self.reading.cabins], [619.0, 682.0, 787.0])

    def test_the_cheapest_cabin_is_the_advertised_price(self):
        # The dataset advertises $619 for this sailing, which is the bottom of
        # this ladder rather than a figure of its own. That is the whole claim
        # the module makes, so it is asserted rather than assumed.
        self.assertEqual(self.reading.cheapest.price, 619.0)
        self.assertEqual(self.reading.cheapest.name, "Cabin 3")

    def test_the_berth_count_comes_from_the_attribute(self):
        self.assertEqual([c.berths for c in self.reading.cabins], [2, 4, 2])

    def test_how_many_berths_are_left_at_the_advertised_price(self):
        # #79, in one number: two.
        self.assertEqual(self.reading.berths_at_cheapest, 2)

    def test_the_banner_and_the_attribute_agree_silently(self):
        # "only 2 spaces left!" against data-allocation=2, on all three. A
        # disagreement is a warning, so an empty warning list is the assertion
        # that three independent statements of the number match.
        self.assertEqual(self.reading.warnings, [])

    def test_the_options_run_up_to_the_berths_left(self):
        self.assertEqual(
            self.reading.cabins[1].occupancy_options,
            ("1 person", "2 people", "3 people", "4 people"),
        )

    def test_the_beds_are_read_and_are_not_an_amenity(self):
        self.assertEqual(self.reading.cabins[0].beds, "1 Bunk bed (singles)")
        self.assertEqual(self.reading.cabins[2].beds, "1 Double bed")

    def test_the_discount_badge_is_not_a_cabin_amenity(self):
        # "Save 10%" is an <li><span> in the price list, not in the <ol> of
        # cabin details. Reading every <li><span> on the block made it one.
        for cabin in self.reading.cabins:
            self.assertEqual(cabin.amenities, ("Aircon with control",))

    def test_the_cabin_states_how_many_it_sleeps(self):
        self.assertEqual([c.sleeps for c in self.reading.cabins], [2, 2, 2])

    def test_sleeping_two_and_leaving_four_berths_is_not_a_contradiction(self):
        # Cabin 1 & 2 is one listing for two cabins: it sleeps 2 and has 4
        # berths left. Deriving either number from the other would be wrong
        # on this row, which is why neither is derived.
        cabin = self.reading.cabins[1]
        self.assertEqual((cabin.sleeps, cabin.berths), (2, 4))


class TestTheSingleSupplement(unittest.TestCase):
    """The surcharge is attributed by cabin id, never by position.

    It is stated in prose in a hidden div *after* each cabin's select and
    before the next cabin. A parser cutting blocks at the select gives every
    cabin the surcharge belonging to the one above it and loses the last
    cabin's entirely -- a wrong number rather than a missing one.
    """

    def test_every_cabin_gets_its_own_surcharge_including_the_last(self):
        reading = parse_cabins(read(DISCOUNTED), "USD")
        self.assertEqual([c.single_supplement_pct for c in reading.cabins], [60, 60, 60])

    def test_both_phrasings_are_read(self):
        # The site says "an additional 60% surcharge" for a shareable cabin
        # and "a 60% surcharge applies for single occupancy" for a private
        # one, in two differently named divs. Iceberg's page has both: its
        # Suite is the private phrasing, the other two the shareable one.
        reading = parse_cabins(read(DISCOUNTED), "USD")
        self.assertIsNotNone(reading.cabins[2].single_supplement_pct)
        self.assertIsNotNone(reading.cabins[0].single_supplement_pct)

    def test_the_surcharge_is_the_vessels_own_figure(self):
        # 60% on Iceberg, 50% on Alia Soul. A constant would pass one test.
        self.assertEqual(parse_cabins(read(DISCOUNTED), "USD").cabins[0]
                         .single_supplement_pct, 60)
        self.assertEqual(parse_cabins(read(UNDISCOUNTED), "USD").cabins[0]
                         .single_supplement_pct, 50)

    def test_what_a_solo_diver_pays(self):
        cabin = parse_cabins(read(DISCOUNTED), "USD").cabins[2]
        self.assertEqual(cabin.single_price, 1259.2)

    def test_no_supplement_means_no_single_price_rather_than_no_supplement(self):
        from liveaboard.scrape.cabins import Cabin

        cabin = Cabin(
            cabin_id="1", name="x", sleeps=2, beds=None, amenities=(),
            price=100.0, list_price=None, berths=2,
            single_supplement_pct=None, shareable=None, occupancy_options=(),
        )
        self.assertIsNone(cabin.single_price)


class TestAPageThatDiscountsNothing(unittest.TestCase):
    def setUp(self):
        self.reading = parse_cabins(read(UNDISCOUNTED), "USD")

    def test_an_undiscounted_cabin_has_no_list_price(self):
        # No <del> at all on this page. The absence is the answer: there is
        # one price, not a list price equal to it.
        for cabin in self.reading.cabins:
            self.assertIsNone(cabin.list_price)
            self.assertFalse(cabin.is_discounted)

    def test_the_prices_are_still_read(self):
        self.assertEqual(
            [c.price for c in self.reading.cabins], [1923.0, 2039.0, 2039.0]
        )

    def test_a_thousands_separator_is_not_a_decimal_point(self):
        # "1,923" is not 1.923, and this is the page that would catch it.
        self.assertEqual(self.reading.cabins[0].price, 1923.0)

    def test_the_cheapest_cabin_is_the_advertised_price(self):
        self.assertEqual(self.reading.cheapest.price, 1923.0)

    def test_a_cabin_with_berths_to_spare_prints_no_banner_and_is_still_counted(self):
        # Twelve berths left and no "only N spaces left!" anywhere near it --
        # the banner appears at four or fewer. A parser reading the banner
        # would call this cabin's count unknown; the attribute states it.
        markup = read(UNDISCOUNTED)
        block = markup[: markup.index("name=input-cabin-guests-12341")]
        self.assertEqual(self.reading.cabins[0].berths, 12)
        self.assertNotIn("spaces left", block)

    def test_the_options_run_to_twelve(self):
        self.assertEqual(len(self.reading.cabins[0].occupancy_options), 12)

    def test_shareable_is_read_per_cabin(self):
        self.assertEqual(
            [c.shareable for c in self.reading.cabins], [True, False, True]
        )

    def test_nothing_was_odd(self):
        self.assertEqual(self.reading.warnings, [])


class TestWhatItRefusesToInvent(unittest.TestCase):
    def test_a_page_with_no_cabins_reads_as_nothing_rather_than_as_zero(self):
        reading = parse_cabins("<html><body>Server error</body></html>", "USD")
        self.assertEqual(reading.cabins, [])
        self.assertFalse(reading)
        self.assertIsNone(reading.berths_at_cheapest)
        self.assertEqual(reading.warnings, [])

    def test_empty_input_is_not_a_crash(self):
        self.assertEqual(parse_cabins("", "USD").cabins, [])

    def test_cabins_listed_but_not_offered_are_reported_not_dropped(self):
        # The sold-out shape: named cabins, no guest-count select. "Nothing
        # bookable" is the page's answer to how many berths are left, and is
        # not the same as a page that failed to load.
        listed = (
            '<button aria-controls=help-content-cabin-details-99 '
            'title="Standard Cabin">Standard Cabin</button>'
        )
        reading = parse_cabins(listed, "USD")
        self.assertEqual(reading.cabins, [])
        self.assertEqual(reading.listed_only, 1)
        self.assertTrue(any("none offered" in w for w in reading.warnings))

    def test_the_currency_is_the_callers_not_the_glyph(self):
        # "$" is the Australian, Canadian, Singapore and US dollar alike, and
        # the page renders whichever the session is set to. It is never used
        # to name the currency.
        self.assertEqual(parse_cabins(read(DISCOUNTED), "EUR").currency, "EUR")

    def test_a_glyph_that_contradicts_the_currency_is_reported(self):
        page = (
            '<button aria-controls=help-content-cabin-details-7 title=Cabin>x</button>'
            '<em>€</em> <span translate=no>500</span>'
            '<select name=input-cabin-guests-7 data-cabinid=7 data-allocation=3>'
            '<option value=0>-<option value=1>1 person</select>'
        )
        reading = parse_cabins(page, "USD")
        self.assertEqual(reading.cabins[0].price, 500.0)
        self.assertEqual(reading.currency, "USD")
        self.assertTrue(any("EUR" in w for w in reading.warnings))

    def test_a_banner_disagreeing_with_the_attribute_is_reported(self):
        page = (
            '<button aria-controls=help-content-cabin-details-7 title=Cabin>x</button>'
            '<span>only 9 spaces left!</span>'
            '<select name=input-cabin-guests-7 data-cabinid=7 data-allocation=3>'
            '<option value=0>-<option value=1>1 person</select>'
        )
        reading = parse_cabins(page, "USD")
        self.assertEqual(reading.cabins[0].berths, 3)
        self.assertTrue(any("banner" in w for w in reading.warnings))

    def test_an_undefined_attribute_is_unknown_rather_than_false(self):
        # The site emits `data-privacy-optional=undefined` -- a JavaScript
        # value reaching the markup. Iceberg's Suite has it. Reading that as
        # False would state something the page does not.
        page = (
            '<button aria-controls=help-content-cabin-details-7 title=Cabin>x</button>'
            '<select name=input-cabin-guests-7 data-cabinid=7 '
            'data-shareable=undefined data-allocation=3>'
            '<option value=0>-<option value=1>1 person</select>'
        )
        self.assertIsNone(parse_cabins(page, "USD").cabins[0].shareable)

    def test_a_cabin_with_no_stated_berths_is_unknown_rather_than_zero(self):
        page = (
            '<button aria-controls=help-content-cabin-details-7 title=Cabin>x</button>'
            '<select name=input-cabin-guests-7 data-cabinid=7>'
            '<option value=0>-<option value=1>1 person</select>'
        )
        cabin = parse_cabins(page, "USD").cabins[0]
        self.assertIsNone(cabin.berths)
        self.assertNotIn("berths", cabin.as_dict())

    def test_a_cabin_with_no_name_button_says_so(self):
        page = (
            '<select name=input-cabin-guests-7 data-cabinid=7 data-allocation=3>'
            '<option value=0>-<option value=1>1 person</select>'
        )
        reading = parse_cabins(page, "USD")
        self.assertEqual(reading.cabins[0].name, "Cabin 7")
        self.assertTrue(any("no name button" in w for w in reading.warnings))


class TestWhatIsWrittenDown(unittest.TestCase):
    def test_a_record_carries_only_what_the_page_stated(self):
        cabin = parse_cabins(read(UNDISCOUNTED), "USD").cabins[0]
        self.assertEqual(
            cabin.as_dict(),
            {
                "cabin_id": "12341",
                "name": "Lower Deck Twin Cabin",
                "sleeps": 2,
                "beds": "2 Single beds",
                "price": 1923.0,
                "berths": 12,
                "single_supplement_pct": 50,
                "shareable": True,
                "amenities": ["Aircon with control"],
                "occupancy_options": [
                    "1 person", "2 people", "3 people", "4 people",
                    "5 people", "6 people", "7 people", "8 people",
                    "9 people", "10 people", "11 people", "12 people",
                ],
            },
        )
        self.assertNotIn("list_price", cabin.as_dict())


if __name__ == "__main__":
    unittest.main()
