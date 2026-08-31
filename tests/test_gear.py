"""What the rental-gear dialog says, and what it does not say.

The fixture is the markup a probe run returned from a live vessel page,
unedited apart from decoding its HTML entities. Everything here is checked
against that rather than against a shape invented to be easy to parse.
"""

from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from liveaboard.models import FeeItem
from liveaboard.scrape.gear import GearItem, GearReading, parse_gear, to_fee_dict
from liveaboard.taxonomy import FeeBasis, FeeCode, FeeTier

FIXTURE = Path(__file__).parent / "fixtures" / "gear_modal.html"
PROVENANCE = {
    "kind": "scraped",
    "source_id": "liveaboard.com",
    "retrieved": "2026-08-27",
    "url": "https://www.liveaboard.com/diving/egypt/emperor-asmaa?m=5/2027",
}


def markup() -> str:
    return FIXTURE.read_text(encoding="utf-8")


class TestParsingTheDialog(unittest.TestCase):
    def setUp(self):
        self.reading = parse_gear(markup())

    def test_every_single_item_is_read(self):
        labels = [i.label for i in self.reading.items]
        self.assertEqual(
            labels,
            ["15L tanks", "BCD", "Dive Computer", "Dive Light", "Fins", "Mask",
             "Nitrox tank", "Regulator", "SMB", "Wetsuit"],
        )

    def test_a_range_keeps_both_ends(self):
        """The spread is the point. "€55-83" collapsed to 55 understates."""
        wetsuit = next(i for i in self.reading.items if i.label == "Wetsuit")
        self.assertEqual((wetsuit.low, wetsuit.high), (55.0, 83.0))
        self.assertTrue(wetsuit.is_range)

    def test_the_week_basis_is_carried_not_flattened(self):
        bcd = next(i for i in self.reading.items if i.label == "BCD")
        self.assertIs(bcd.basis, FeeBasis.PER_WEEK)

    def test_the_bundle_is_read_from_its_own_section(self):
        self.assertIsNotNone(self.reading.bundle)
        self.assertEqual(self.reading.bundle.low, 206.0)
        self.assertIn("Regulator", self.reading.bundle.label)

    def test_the_bundle_is_not_counted_as_a_single_item(self):
        self.assertNotIn(self.reading.bundle.label, [i.label for i in self.reading.items])

    def test_an_included_item_is_not_a_zero(self):
        nitrox = next(i for i in self.reading.items if i.label == "Nitrox tank")
        self.assertTrue(nitrox.included)
        self.assertFalse(nitrox.has_price)
        self.assertIsNone(nitrox.low)

    def test_text_that_is_not_the_dialog_yields_nothing(self):
        """No guessing from a page that never opened it."""
        self.assertFalse(parse_gear("<div>Boat features, Free Internet</div>"))
        self.assertFalse(parse_gear(""))


class TestNitroxIsNotOverclaimed(unittest.TestCase):
    """"Nitrox tank: Included" answers a narrower question than it looks."""

    def test_it_is_recorded(self):
        self.assertTrue(parse_gear(markup()).nitrox_tank_included)

    def test_it_does_not_become_a_nitrox_fee(self):
        """It sits in a list of hire charges, so the plain reading is that the
        tank costs nothing on top of the gear -- not that fills are free."""
        fee = to_fee_dict(parse_gear(markup()), PROVENANCE)
        self.assertEqual(fee["code"], FeeCode.GEAR_RENTAL.value)


class TestTheFeeItProduces(unittest.TestCase):
    def test_it_uses_the_bundle_price(self):
        fee = to_fee_dict(parse_gear(markup()), PROVENANCE)
        self.assertEqual(fee["amount"], {"amount": 206.0, "currency": "EUR"})
        self.assertEqual(fee["basis"], FeeBasis.PER_WEEK.value)
        self.assertEqual(fee["tier"], FeeTier.OPTIONAL.value)

    def test_singles_are_never_added_up_into_a_set(self):
        """Whether the set is cheaper than its parts is the thing not stated,
        so a summed total would be a price the operator never quoted."""
        no_bundle = GearReading(items=[
            GearItem("BCD", 40.0, None, "EUR", FeeBasis.PER_WEEK),
            GearItem("Regulator", 40.0, None, "EUR", FeeBasis.PER_WEEK),
        ])
        fee = to_fee_dict(no_bundle, PROVENANCE)
        self.assertIsNone(fee["amount"])
        self.assertIn("BCD €40/week", fee["note"])
        self.assertIn("Regulator €40/week", fee["note"])

    def test_an_empty_reading_produces_no_fee(self):
        self.assertIsNone(to_fee_dict(GearReading(), PROVENANCE))


class TestWeeklyHireOverATripThatIsNotAWeek(unittest.TestCase):
    """Trips run three to fourteen nights; the dialog quotes weeks."""

    def fee(self, amount=206.0):
        return FeeItem.from_dict(
            {
                "code": FeeCode.GEAR_RENTAL.value,
                "tier": FeeTier.OPTIONAL.value,
                "basis": FeeBasis.PER_WEEK.value,
                "amount": {"amount": amount, "currency": "EUR"},
            },
            "EUR",
        )

    def weeks(self, nights):
        low, _ = self.fee(amount=206.0).span_for_trip(nights=nights, dives=nights * 3)
        return float(low.amount) / 206.0

    def test_a_short_trip_is_still_one_week(self):
        """Nobody hires a BCD for three sevenths of a week."""
        self.assertEqual(self.weeks(3), 1)

    def test_the_standard_seven_night_week_is_one_week(self):
        """The case that matters most, and the one that was wrong.

        A seven-night liveaboard *is* the week the operator prices. Counting
        days aboard rather than nights makes it eight, tips it over the
        boundary, and bills the fleet's commonest trip as a fortnight -- so
        the gear line came out at double on more departures than any other.
        """
        self.assertEqual(self.weeks(7), 1)

    def test_a_longer_trip_rounds_up_rather_than_undercharging(self):
        """Nine nights is more than a week and the page states no pro-rata."""
        self.assertEqual(self.weeks(9), 2)

    def test_a_fortnight_is_two_weeks_not_three(self):
        self.assertEqual(self.weeks(14), 2)


class TestACappedRunCannotEmptyTheFeeBook(unittest.TestCase):
    """``--limit 6`` visits six vessels and knows nothing about the other 73.

    Writing only what such a run saw would delete the rest, which is a partial
    view overwriting a complete one -- and it would look like a successful
    refresh in the log.
    """

    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
        import scrape_fees

        self.previous = scrape_fees.previous
        self.tmp = Path(__file__).parent / "fixtures" / "_fee_book_probe.json"

    def tearDown(self):
        self.tmp.unlink(missing_ok=True)

    def test_the_existing_book_is_the_starting_point(self):
        self.tmp.write_text(
            json.dumps({"vessels": {"aphrodite": {"fees": []}, "blue-seas": {"fees": []}}}),
            encoding="utf-8",
        )
        self.assertEqual(sorted(self.previous(self.tmp)), ["aphrodite", "blue-seas"])

    def test_a_first_run_starts_empty(self):
        self.assertEqual(self.previous(self.tmp), {})

    def test_an_unreadable_book_does_not_stop_the_run(self):
        """Better to rebuild than to halt -- but it says so on stderr."""
        self.tmp.write_text("{ not json", encoding="utf-8")
        with redirect_stderr(io.StringIO()) as noise:
            self.assertEqual(self.previous(self.tmp), {})
        self.assertIn("fresh fee book", noise.getvalue())


class TestAFigureWithNoUnitIsNotAPerTripFigure(unittest.TestCase):
    """Five vessels quote a bundle price with nothing after the number.

    Bella 2's is ``<span>€40</span>``. Read as per trip -- the cheapest of the
    three units the page uses elsewhere -- that is a third of what a three-night
    trip charges for the same kit and a seventh of a week's, and both the boat's
    own singles (€11/day for a BCD) and PADI's book for the same set (40 EUR per
    diving day) say a day.

    70 vessels quote a priced bundle and 65 state a unit -- 25 per trip, 25 per
    week, 15 per day -- so the unit is the norm and its absence is the page
    failing to state one. The five that leave it out span every answer on the
    evidence beside them: Emperor Superior's unitless €206 is what its two
    sisters state per week, Bella 2's €40 is a day, and Blue Pearl's €135 sits
    between a sister quoting €135/week and one quoting €135/trip. So there is no
    fallback that is right, and the figure is stated in the note instead of
    being added up under a unit nobody wrote.
    """

    DIALOG = """
      <h4>Rental Gear Prices</h4>
      <h5>Single gear rent</h5>
      <ul><li> <strong>BCD</strong> <span>&#8364;11 / day</span>
              <li> <strong>Fins</strong> <span>&#8364;5</span></ul>
      <h5>Full equipment rent</h5>
      <ul><li> <strong>BCD, Fins, Mask, Regulator, Wetsuit</strong>
              <span>&#8364;40</span></ul>
    """

    def setUp(self):
        from html import unescape

        self.reading = parse_gear(unescape(self.DIALOG))
        self.fee = to_fee_dict(self.reading, PROVENANCE)

    def test_the_bundle_states_no_basis(self):
        self.assertIsNone(self.reading.bundle.basis)
        self.assertEqual(self.reading.bundle.low, 40.0)

    def test_a_single_item_states_no_basis_either(self):
        """45 priced singles across the fleet are written this way."""
        fins = next(i for i in self.reading.items if i.label == "Fins")
        self.assertIsNone(fins.basis)
        self.assertIs(next(i for i in self.reading.items if i.label == "BCD").basis,
                      FeeBasis.PER_DAY)

    def test_the_note_prints_the_figure_without_inventing_a_unit(self):
        self.assertEqual(
            next(i for i in self.reading.items if i.label == "Fins").as_text(),
            "Fins \u20ac5")

    def test_no_total_claims_the_bundle(self):
        self.assertIsNone(self.fee["amount"])

    def test_the_figure_survives_where_a_reader_can_see_it(self):
        self.assertIn("\u20ac40", self.fee["note"])
        self.assertIn("no unit stated", self.fee["note"])

    def test_it_still_loads_as_a_fee_item(self):
        """An unpriced line is a fee this dataset already models: the operator
        rents the kit and the page cannot say what a trip's hire comes to."""
        item = FeeItem.from_dict(self.fee, "EUR")
        self.assertIs(item.code, FeeCode.GEAR_RENTAL)
        self.assertIs(item.tier, FeeTier.OPTIONAL)
        self.assertIsNone(item.amount)

    def test_a_stated_unit_is_untouched(self):
        """The regression this must not cause: 65 vessels do state one."""
        fee = to_fee_dict(parse_gear(markup()), PROVENANCE)
        self.assertEqual(fee["amount"], {"amount": 206.0, "currency": "EUR"})
        self.assertEqual(fee["basis"], FeeBasis.PER_WEEK.value)


if __name__ == "__main__":
    unittest.main()
