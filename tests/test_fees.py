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
        """Eleven: the two courses are separate priced lines, not one."""
        self.assertEqual(len(self.fees), 11)

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
        self.assertEqual(self.byc[FeeCode.NITROX_COURSE].low, 250.0)

    def test_the_two_courses_keep_their_own_prices(self):
        """One code for both dropped whichever the page listed second."""
        self.assertEqual(self.byc[FeeCode.NITROX_COURSE].low, 250.0)
        self.assertEqual(self.byc[FeeCode.COURSE].low, 300.0)

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
        self.assertEqual(len(self.items), 11)

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


# Verbatim from the stored disclosure of amelie, whose Optional block runs 273
# characters of real list inside 1500 characters of page.
AMELIE = (
    "Optional Extras: Gratuities, Alcoholic Beverages, Extra Dives "
    "(\u20ac50 / activity), Nitrox Course (\u20ac132), Private Dive Guide (\u20ac55), "
    "Rental Gear, Scuba Diving Courses (\u20ac210-370), Land Excursions "
    "(\u20ac35 / trip), Naturalist Guide (\u20ac50 / day), Snorkel Gear."
    " Book now, pay later: You can easily place your booking online."
    " Best Price Guaranteed, You're getting the lowest rate."
    " Boat features, Free Internet, Jacuzzi / Hot Tub, Naturalist Guide,"
    " Beer available, Wine Available, Bar, Nitrox, Library."
)


class TestListEndsAtTheFullStop(unittest.TestCase):
    """The operator closes the list with a period; the page continues after it."""

    def setUp(self):
        self.fees = parse_extras(AMELIE)
        self.byc = by_code(self.fees)

    def test_every_extra_before_the_stop_is_captured(self):
        for code in (
            FeeCode.GRATUITIES, FeeCode.ALCOHOL, FeeCode.EXTRA_DIVES,
            FeeCode.NITROX_COURSE, FeeCode.PRIVATE_GUIDE, FeeCode.GEAR_RENTAL,
            FeeCode.COURSE, FeeCode.LAND_EXCURSION, FeeCode.NATURALIST_GUIDE,
            FeeCode.SNORKEL_GEAR,
        ):
            self.assertIn(code, self.byc, code)

    def test_nothing_after_the_stop_becomes_a_fee(self):
        """"Bar", "Beer available" and "Nitrox" are amenities down there."""
        self.assertNotIn(FeeCode.NITROX, self.byc)
        self.assertNotIn(FeeCode.LAUNDRY, self.byc)

    def test_the_stop_holds_on_an_extra_we_do_not_model(self):
        """Breaking only on recognised entries let the whole page through."""
        fees = parse_extras(
            "Optional Extras: Gratuities, Yoga Sessions."
            " Boat features, Nitrox, Bar, Free Internet, Laundry."
        )
        self.assertEqual([f.code for f in fees], [FeeCode.GRATUITIES])

    def test_a_block_that_never_closes_still_stops(self):
        """The label bounds remain the second line of defence."""
        fees = parse_extras(
            "Optional Extras: Gratuities, "
            + "Pay by bank transfer or online with Best Price Guaranteed, " * 5
        )
        self.assertEqual([f.code for f in fees], [FeeCode.GRATUITIES])


class TestExtrasWeUsedToDrop(unittest.TestCase):
    """Six real entries the taxonomy had no code for, several of them priced."""

    def test_alcohol_is_charged_on_every_vessel_seen(self):
        self.assertEqual(classify_label("Alcoholic Beverages"), FeeCode.ALCOHOL)

    def test_snorkel_gear_is_not_folded_into_rental_gear(self):
        """One entry per code: folding them dropped whichever came second."""
        fees = by_code(parse_extras(
            "Optional Extras: Rental Gear, Snorkel Gear (\u20ac50)."
        ))
        self.assertIn(FeeCode.GEAR_RENTAL, fees)
        self.assertEqual(fees[FeeCode.SNORKEL_GEAR].low, 50.0)

    def test_the_rest_classify(self):
        for text, code in (
            ("Extra Dives", FeeCode.EXTRA_DIVES),
            ("Land Excursions", FeeCode.LAND_EXCURSION),
            ("Naturalist Guide", FeeCode.NATURALIST_GUIDE),
            ("Snorkeling Guide", FeeCode.NATURALIST_GUIDE),
        ):
            self.assertEqual(classify_label(text), code, text)

    def test_an_amenity_is_not_an_alcohol_charge(self):
        """The feature list carries "Beer available" and "Wine Available"."""
        self.assertIsNone(classify_label("Beer available"))
        self.assertIsNone(classify_label("Wine Available"))

    def test_a_naturalist_guide_is_not_a_private_dive_guide(self):
        self.assertEqual(classify_label("Naturalist Guide"), FeeCode.NATURALIST_GUIDE)
        self.assertEqual(classify_label("Private Dive Guide"), FeeCode.PRIVATE_GUIDE)

    def test_the_new_codes_are_optional_so_no_default_total_moves(self):
        """None is base, mandatory or customary, so DEFAULT_ON_TIERS is untouched."""
        for fee in parse_extras(AMELIE):
            if fee.code in (
                FeeCode.SNORKEL_GEAR, FeeCode.EXTRA_DIVES,
                FeeCode.LAND_EXCURSION, FeeCode.NATURALIST_GUIDE,
                FeeCode.ALCOHOL, FeeCode.NITROX_COURSE,
            ):
                self.assertEqual(fee.tier, FeeTier.OPTIONAL, fee.code)


class TestMandatoryFeesNoOneNamed(unittest.TestCase):
    """Required-block charges the parser had no code for, so it dropped them.

    Understating a bill is the same failure as inventing one, told the other
    way round -- and it also flatters the honesty score, because a fee that
    never reaches the breakdown cannot count against the operator.
    """

    AVO = (
        "Required Extras: Mandatory Service Charge (\u20ac80), "
        "Park, Port and Fuel Fees (\u20ac200-450 / trip)."
    )

    def test_a_whole_required_block_is_no_longer_lost(self):
        """This vessel published two mandatory charges and we showed neither."""
        fees = parse_extras(self.AVO)
        self.assertEqual(len(fees), 2)
        self.assertTrue(all(f.tier is FeeTier.MANDATORY for f in fees))

    def test_the_service_charge_is_captured(self):
        byc = by_code(parse_extras(self.AVO))
        self.assertEqual(byc[FeeCode.SERVICE_CHARGE].low, 80.0)

    def test_a_combined_charge_keeps_its_whole_amount(self):
        """Splitting it across three codes would invent three prices."""
        byc = by_code(parse_extras(self.AVO))
        combined = byc[FeeCode.COMBINED_FEES]
        self.assertEqual((combined.low, combined.high), (200.0, 450.0))
        self.assertTrue(combined.is_range)

    def test_two_components_make_a_combined_charge(self):
        for label in ("Park, Port and Fuel Fees", "Park and Port Fees",
                      "Port and Fuel Fees"):
            self.assertEqual(classify_label(label), FeeCode.COMBINED_FEES, label)

    def test_one_component_stays_its_own_fee(self):
        """The combined check must not swallow the ordinary single lines."""
        for label, code in (
            ("National Park Fees", FeeCode.MARINE_PARK),
            ("Port Fees", FeeCode.PORT_FEES),
            ("Fuel Surcharge", FeeCode.FUEL_SURCHARGE),
            ("Environment Tax", FeeCode.ENVIRONMENT_TAX),
            ("Harbour Dues", FeeCode.PORT_FEES),
        ):
            self.assertEqual(classify_label(label), code, label)

    def test_a_bare_component_with_no_fee_word_is_not_a_charge(self):
        """"Park" alone is the comma-split remains of the label above it."""
        self.assertIsNone(classify_label("Park"))

    def test_separately_billed_fees_are_still_separate(self):
        fees = by_code(parse_extras(
            "Required Extras: Mandatory Service Charge (\u20ac10 / day), "
            "National Park Fees (\u20ac10 / day), Port Fees (\u20ac5 / trip)."
        ))
        self.assertEqual(
            set(fees),
            {FeeCode.SERVICE_CHARGE, FeeCode.MARINE_PARK, FeeCode.PORT_FEES},
        )


class TestEveryCodeCanBeNamed(unittest.TestCase):
    """render.py hands FEE_LABELS to the page as the only source of names."""

    def test_no_code_would_render_as_its_slug(self):
        from liveaboard.taxonomy import FEE_LABELS

        missing = sorted(c.value for c in FeeCode if c not in FEE_LABELS)
        self.assertEqual(missing, [], f"unnamed fee codes: {missing}")

    def test_every_code_the_parser_can_emit_has_a_label(self):
        from liveaboard.scrape.fees import LABEL_PATTERNS
        from liveaboard.taxonomy import FEE_LABELS

        for _, code in LABEL_PATTERNS:
            self.assertIn(code, FEE_LABELS, code)


if __name__ == "__main__":
    unittest.main()
