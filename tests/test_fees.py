"""Tests for parsing the extras disclosure off a vessel page.

The fixture is the real text from liveaboard.com's Grand Discovery page.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from liveaboard.models import FeeItem
from liveaboard.scrape.base import FetchResult, PoliteFetcher
from liveaboard.scrape.fees import parse_extras, to_fee_dicts
from liveaboard.scrape.liveaboard_com import LiveaboardComAdapter, _page_text
from liveaboard.taxonomy import FeeCode, FeeTier

REAL = (
    "Required Extras: Environment Tax (€45), Fuel Surcharge (€60-70 / trip), "
    "National Park Fees (€35-100 / trip), Port Fees (€35). "
    "Optional Extras: Gratuities (€80), Nitrox (€30 / trip), "
    "Nitrox Course (€250 / item), Private Dive Guide (€500 / trip), "
    "Rental Gear, Scuba Diving Courses (€300-350), "
    "Laundry / Pressing Services (€5 / item)."
)


def by_code(fees):
    return {fee.code: fee for fee in fees}


class TestRealDisclosure(unittest.TestCase):
    def setUp(self):
        self.fees = parse_extras(REAL)
        self.byc = by_code(self.fees)

    def test_every_extra_is_found(self):
        self.assertEqual(len(self.fees), 10)

    def test_required_block_becomes_mandatory(self):
        for code in (
            FeeCode.ENVIRONMENT_TAX,
            FeeCode.FUEL_SURCHARGE,
            FeeCode.MARINE_PARK,
            FeeCode.PORT_FEES,
        ):
            self.assertEqual(self.byc[code].tier, FeeTier.MANDATORY, code)

    def test_ranges_keep_both_ends(self):
        """Park fees quoted 35-100 are a 65 euro spread; the low end alone lies."""
        park = self.byc[FeeCode.MARINE_PARK]
        self.assertTrue(park.is_range)
        self.assertEqual((park.low, park.high), (35.0, 100.0))

    def test_a_fixed_amount_is_not_a_range(self):
        self.assertFalse(self.byc[FeeCode.PORT_FEES].is_range)

    def test_an_extra_with_no_figure_has_no_price(self):
        """Rental Gear is listed and left blank; that is not free."""
        gear = self.byc[FeeCode.GEAR_RENTAL]
        self.assertFalse(gear.has_price)
        self.assertIsNone(gear.low)

    def test_currency_comes_from_the_symbol(self):
        """The page quotes fees in euro while quoting trip prices in dollars."""
        self.assertEqual(self.byc[FeeCode.PORT_FEES].currency, "EUR")

    def test_nitrox_course_does_not_resolve_as_nitrox(self):
        self.assertEqual(self.byc[FeeCode.NITROX].low, 30.0)
        self.assertEqual(self.byc[FeeCode.COURSE].low, 250.0)

    def test_gratuities_are_customary_despite_being_listed_optional(self):
        """The site's split says escapable; it does not say who actually escapes."""
        self.assertEqual(self.byc[FeeCode.GRATUITIES].tier, FeeTier.CUSTOMARY)

    def test_nitrox_is_conditional_so_a_toggle_governs_it(self):
        self.assertEqual(self.byc[FeeCode.NITROX].tier, FeeTier.CONDITIONAL)

    def test_a_genuine_luxury_stays_optional(self):
        self.assertEqual(self.byc[FeeCode.PRIVATE_GUIDE].tier, FeeTier.OPTIONAL)


class TestFeeDicts(unittest.TestCase):
    PROV = {"kind": "scraped", "source_id": "liveaboard.com", "retrieved": "2026-08-27"}

    def setUp(self):
        self.dicts = to_fee_dicts(parse_extras(REAL), self.PROV)
        self.items = [FeeItem.from_dict(d, "EUR") for d in self.dicts]
        self.byc = {item.code: item for item in self.items}

    def test_dicts_load_as_fee_items(self):
        self.assertEqual(len(self.items), 10)

    def test_range_survives_the_round_trip(self):
        park = self.byc[FeeCode.MARINE_PARK]
        self.assertTrue(park.is_range)
        self.assertEqual(float(park.amount.amount), 35.0)
        self.assertEqual(float(park.amount_max.amount), 100.0)

    def test_missing_price_survives_as_none_not_zero(self):
        self.assertIsNone(self.byc[FeeCode.GEAR_RENTAL].amount)
        self.assertFalse(self.byc[FeeCode.GEAR_RENTAL].has_price)

    def test_asking_an_unpriced_fee_for_a_total_raises(self):
        """Better a loud failure than a silent zero in a cost total."""
        with self.assertRaises(ValueError):
            self.byc[FeeCode.GEAR_RENTAL].for_trip(7, 20)


class TestExtractionFromMarkup(unittest.TestCase):
    """The disclosure is split across anchors and spans in real markup."""

    HTML = (
        "<html><head>"
        '<script type="application/ld+json">{"@type":"Product","name":"Grand Discovery"}</script>'
        "</head><body><h3>Required Extras:</h3><ul>"
        "<li><a>Environment Tax</a> <span>(&euro;45)</span></li>"
        "<li><a>Fuel Surcharge</a> <span>(&euro;60-70 / trip)</span></li>"
        "</ul><h3>Optional Extras:</h3><ul>"
        "<li><a>Nitrox</a> <span>(&euro;30 / trip)</span></li>"
        "<li><a>Rental Gear</a></li></ul></body></html>"
    )

    def test_block_ends_become_separators(self):
        """Without this the entries run together and their boundaries vanish."""
        text = _page_text(self.HTML)
        self.assertIn("Environment Tax (€45)", text)
        # A separator lands between the entries; incidental spacing around it
        # is the parser's problem, not the flattener's.
        between = text.split("Environment Tax (€45)")[1].split("Fuel Surcharge")[0]
        self.assertIn(",", between)

    def test_fees_are_read_off_the_vessel_page(self):
        adapter = LiveaboardComAdapter(PoliteFetcher(snapshot_dir="/tmp/unused"))
        output = adapter.parse(
            FetchResult(
                url="https://www.liveaboard.com/diving/egypt/grand-discovery",
                status=200,
                body=self.HTML,
                fetched_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
            )
        )
        codes = {fee["code"] for fee in output.itineraries[0]["fees"]}
        self.assertEqual(
            codes, {"environment_tax", "fuel_surcharge", "nitrox", "gear_rental"}
        )

    def test_a_page_with_no_disclosure_is_reported(self):
        adapter = LiveaboardComAdapter(PoliteFetcher(snapshot_dir="/tmp/unused"))
        output = adapter.parse(
            FetchResult(
                url="https://www.liveaboard.com/diving/egypt/quiet-boat",
                status=200,
                body='<script type="application/ld+json">{"@type":"Product","name":"X"}</script>',
                fetched_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
            )
        )
        self.assertTrue(any("Extras" in w for w in output.warnings))


class TestNoise(unittest.TestCase):
    def test_absent_blocks_yield_nothing(self):
        self.assertEqual(parse_extras("A lovely boat with a sun deck."), [])

    def test_unrecognised_labels_are_dropped_not_guessed(self):
        fees = parse_extras("Required Extras: Sun Cream (€9), Port Fees (€35).")
        self.assertEqual([f.code for f in fees], [FeeCode.PORT_FEES])


if __name__ == "__main__":
    unittest.main()
