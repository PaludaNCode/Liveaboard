"""Tests for parsing the extras disclosure off a vessel page.

The fixture is the real text from liveaboard.com's Grand Discovery page.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from liveaboard.models import FeeItem
from liveaboard.scrape.base import FetchResult, PoliteFetcher
from liveaboard.scrape.fees import (
    classify_label,
    extras_excerpt,
    normalise_disclosure,
    parse_extras,
    to_fee_dicts,
)
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


class TestRenderedTextLayout(unittest.TestCase):
    """A browser's innerText puts each extra, and often its amount, on its own line."""

    INNER = (
        "Required Extras:\n"
        "Environment Tax\n(\u20ac45)\n"
        "Fuel Surcharge\n(\u20ac60-70 / trip)\n"
        "National Park Fees\n(\u20ac35-100 / trip)\n"
        "Optional Extras:\n"
        "Gratuities\n(\u20ac80)\n"
        "Rental Gear"
    )

    def setUp(self):
        self.byc = by_code(parse_extras(self.INNER))

    def test_amounts_survive_the_line_break(self):
        """Swapping every newline for a comma orphans each label from its price."""
        self.assertEqual(self.byc[FeeCode.ENVIRONMENT_TAX].low, 45.0)
        self.assertEqual(self.byc[FeeCode.GRATUITIES].low, 80.0)

    def test_ranges_survive_the_line_break(self):
        park = self.byc[FeeCode.MARINE_PARK]
        self.assertEqual((park.low, park.high), (35.0, 100.0))

    def test_a_genuinely_unpriced_extra_is_still_unpriced(self):
        self.assertFalse(self.byc[FeeCode.GEAR_RENTAL].has_price)

    def test_only_one_extra_lacks_a_price(self):
        """A first live run reported five to seven per vessel; that was the bug."""
        unpriced = [f for f in parse_extras(self.INNER) if not f.has_price]
        self.assertEqual(len(unpriced), 1)

    def test_normalising_is_idempotent_on_comma_separated_text(self):
        self.assertEqual(len(parse_extras(REAL)), len(parse_extras(normalise_disclosure(REAL))))

class TestRejectsPageFurniture(unittest.TestCase):
    """A live run mined fees out of CSS, a spec sheet and a destination menu."""

    JUNK = (
        "Optional Extras: Gratuities (€80), Rental Gear, "
        "Pay by bank transfer or online with: listed cards, "
        '] [&>*]:mx-3 -mx-3"> Nitrox available Free Internet En-Suite, '
        "Year built 2014 Year renovated 2025 Length 40 meters Top speed 11 Knots, "
        "K Koh Tachai Komodo Kimud Shoal Koror Kerama, "
        "V Visayas Viti Levu Vicente Vaavu Atoll, "
        "T The Au Co Tip Top II Tip Top IV Treasure."
    )

    def setUp(self):
        self.codes = {f.code for f in parse_extras(self.JUNK)}

    def test_only_the_genuine_entries_survive(self):
        self.assertEqual(self.codes, {FeeCode.GRATUITIES, FeeCode.GEAR_RENTAL})

    def test_renovated_is_not_vat(self):
        self.assertNotIn(FeeCode.TAX_VAT, self.codes)

    def test_visayas_is_not_a_visa(self):
        self.assertNotIn(FeeCode.VISA, self.codes)

    def test_bank_transfer_is_not_an_airport_transfer(self):
        self.assertNotIn(FeeCode.AIRPORT_TRANSFER, self.codes)

    def test_a_boat_named_tip_top_is_not_a_gratuity_source(self):
        """Gratuities appear here legitimately; the point is the label matched."""
        fees = {f.code: f for f in parse_extras(self.JUNK)}
        self.assertEqual(fees[FeeCode.GRATUITIES].label, "Gratuities")

    def test_leaked_css_does_not_become_a_nitrox_charge(self):
        self.assertNotIn(FeeCode.NITROX, self.codes)

    def test_a_destination_menu_yields_no_park_fee(self):
        self.assertNotIn(FeeCode.MARINE_PARK, self.codes)


class TestTruncation(unittest.TestCase):
    """Where the list stops matters as much as what is in it."""

    def test_a_long_priced_entry_does_not_end_the_list(self):
        """Losing real mandatory fees is the same lie as inventing them."""
        text = (
            "Required Extras: Environment Tax (€45), "
            "A rather verbosely named harbour and mooring facility charge "
            "levied per trip (€35), Fuel Surcharge (€60-70 / trip)."
        )
        codes = {f.code for f in parse_extras(text)}
        self.assertIn(FeeCode.ENVIRONMENT_TAX, codes)
        self.assertIn(FeeCode.FUEL_SURCHARGE, codes)

    def test_a_long_unpriced_segment_still_ends_the_list(self):
        """That segment is the page running on past the disclosure."""
        text = (
            "Required Extras: Port Fees (€35), "
            "Year built 2014 Year renovated 2025 Length 40 meters Top speed 11 Knots, "
            "Visa on arrival (€25)."
        )
        codes = {f.code for f in parse_extras(text)}
        self.assertIn(FeeCode.PORT_FEES, codes)
        self.assertNotIn(FeeCode.VISA, codes)


class TestPublishedFabrications(unittest.TestCase):
    """Verbatim label text from a dataset that reached the live site.

    Each of these produced a fee line nobody was ever charged. They are kept
    word for word so the same page furniture cannot come back quietly.
    """

    FABRICATIONS = (
        "Pay by bank transfer or online with Best Price Guarantee",
        "Diving Nitrox available Free Nitrox Shaded Dive Deck",
        "Show prices Drawings & Vessel Layouts Cabin Types",
        "meters Top speed 11 Knots Cruising speed 11",
        "Year built 2014 Year renovated 2025 Length 40 meters",
        "V Visayas Viti Levu Vicente Vaavu Atoll",
        "T The Au Co Tip Top II Tip Top IV",
    )

    def test_none_of_them_resolves_to_a_fee(self):
        for text in self.FABRICATIONS:
            self.assertIsNone(classify_label(text), text)

    def test_real_labels_are_untouched(self):
        for text, code in (
            ("Environment Tax", FeeCode.ENVIRONMENT_TAX),
            ("National Park Fees", FeeCode.MARINE_PARK),
            ("Fuel Surcharge", FeeCode.FUEL_SURCHARGE),
            ("Rental Gear", FeeCode.GEAR_RENTAL),
            ("Laundry / Pressing Services", FeeCode.LAUNDRY),
            ("Private Dive Guide", FeeCode.PRIVATE_GUIDE),
            ("Scuba Diving Courses", FeeCode.COURSE),
        ):
            self.assertEqual(classify_label(text), code, text)

    def test_a_label_is_a_noun_phrase_not_a_sentence(self):
        """Six words is the line: no real extra needs a seventh."""
        self.assertIsNotNone(classify_label("Laundry / Pressing Services"))
        self.assertIsNone(classify_label("Nitrox is available on request for all guests"))


class TestDisclosureExcerpt(unittest.TestCase):
    """The fee book keeps what it parsed, so a fix can be replayed offline."""

    def setUp(self):
        self.blocks = extras_excerpt(REAL)

    def test_both_blocks_are_kept(self):
        self.assertEqual(set(self.blocks), {"required", "optional"})

    def test_the_text_the_parser_read_is_recoverable(self):
        self.assertIn("Environment Tax", self.blocks["required"])
        self.assertIn("Laundry / Pressing Services", self.blocks["optional"])

    def test_a_page_dump_is_bounded(self):
        """One vessel's evidence must not become a copy of its whole page."""
        flooded = REAL + " " + ("filler words that run on and on " * 400)
        for excerpt in extras_excerpt(flooded).values():
            self.assertLessEqual(len(excerpt), 1500)

    def test_replaying_the_excerpt_reproduces_the_parse(self):
        """The point of storing it: same text in, same fees out."""
        replayed = parse_extras(
            "Required Extras: " + self.blocks["required"]
            + " Optional Extras: " + self.blocks["optional"]
        )
        self.assertEqual(
            [f.code for f in replayed], [f.code for f in parse_extras(REAL)]
        )

    def test_a_page_with_no_disclosure_yields_nothing(self):
        self.assertEqual(extras_excerpt("Boat Specifications Year built 2023"), {})


if __name__ == "__main__":
    unittest.main()
