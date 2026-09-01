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

    def test_gratuities_take_the_tier_their_own_block_gives_them(self):
        """This asserted CUSTOMARY, on the reasoning that the site's split says
        what is escapable and not who actually escapes. That reasoning is about
        divers and this is a statement about a charge: a mandatory $50 tip and
        one you choose the size of are different things, and the operator is
        the only party who can say which it bills. Every vessel that states
        gratuities files them under Optional, so the promotion was overruling
        all 55 of them at once -- and adding a mean of EUR 74 to 278 sailings'
        counted totals on nobody's authority."""
        self.assertEqual(self.byc[FeeCode.GRATUITIES].tier, FeeTier.OPTIONAL)

    def test_a_tip_an_operator_bills_as_required_is_still_counted(self):
        """The other half of the same rule, and why it needs no special case:
        a charge in the Required block is mandatory whatever it is for."""
        from liveaboard.scrape.fees import _tier_for

        self.assertEqual(_tier_for(FeeCode.GRATUITIES, required=True),
                         FeeTier.MANDATORY)

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
            ("Glass Bottom Boat Excursion", FeeCode.LAND_EXCURSION),
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


class TestTheBlockNobodyRead(unittest.TestCase):
    """`Included:`, on all 79 vessel pages, above the two that were parsed.

    The disclosure is written in three blocks and this parser knew two. The
    third sits in the same paragraph and the same comma-separated prose, and
    what it cost is a line per vessel that the whole site turns on: **included
    fees stay in the breakdown at zero**, because removing them hides the
    difference between a bundled operator and one that bills at the dock. The
    rule was being enforced on PADI's `whatsIncludedNew` and on nothing here.

    Bella 2 is the case that found it. The other seller charges 50 EUR for
    nitrox on that boat; this one lists it under Included, and the page showed
    neither. Across the fleet it is 273 lines on 79 vessels -- VAT on 79, a
    transfer on 59, nitrox on 49, a fuel surcharge on 16, port fees on 10.
    """

    #: Bella 2's disclosure, verbatim, 2026-08-31.
    TEXT = (
        "Included: VAT, Drinking Water, Soft drinks, Tea & Coffee, Welcome "
        "Cocktails, Full-Board Meal Plan (All meals), Snacks, Diving Package, "
        "Night Dives, Nitrox, Snorkeling Guide, Beach Towels, Cabin Towels, "
        "Complimentary Toiletries, Deck Towels, WiFi internet."
        "Required Extras: Mandatory Service Charge (\u20ac10 / day), National "
        "Park Fees (\u20ac10 / day), Port Fees (\u20ac5 / trip)."
        "Optional Extras: Airport Transfer, Hotel Transfer, Alcoholic "
        "Beverages, Nitrox Course (\u20ac100-200 / activity), Rental Gear, "
        "Laundry / Pressing Services."
    )

    def fees(self, text=None):
        return {f.code: f for f in parse_extras(text or self.TEXT)}

    def test_the_inclusions_are_read(self):
        nitrox = self.fees()[FeeCode.NITROX]
        self.assertTrue(nitrox.included)
        self.assertIsNone(nitrox.low)
        self.assertEqual(nitrox.label, "Nitrox")

    def test_an_inclusion_is_never_read_as_free_of_charge_wording(self):
        """It is a price of zero the operator stated, not a blank."""
        dicts = to_fee_dicts(list(self.fees().values()), {})
        vat = next(f for f in dicts if f["code"] == "tax_vat")
        self.assertTrue(vat["included"])
        self.assertIsNone(vat["amount"])
        self.assertEqual(vat["note"], "VAT: stated as included")

    def test_the_charged_blocks_are_untouched(self):
        """The regression this must not cause. Bella 2's three required
        charges and its priced nitrox course read exactly as before."""
        fees = self.fees()
        self.assertEqual(
            [(fees[c].low, fees[c].basis.value, fees[c].included) for c in
             (FeeCode.SERVICE_CHARGE, FeeCode.MARINE_PARK, FeeCode.PORT_FEES)],
            [(10.0, "per_day", False), (10.0, "per_day", False),
             (5.0, "per_trip", False)])
        self.assertEqual(fees[FeeCode.NITROX_COURSE].low, 100.0)

    def test_the_nitrox_course_is_not_the_gas_being_included(self):
        """Two codes, and this page states one of each: nitrox included, the
        certification charged."""
        fees = self.fees()
        self.assertTrue(fees[FeeCode.NITROX].included)
        self.assertFalse(fees[FeeCode.NITROX_COURSE].included)

    def test_a_priced_charge_beats_an_inclusion_whatever_the_page_order(self):
        """`Included:` is printed first and would win by arriving first. It
        decides four vessels today and all four are one code covering two
        services: Topaz includes the airport transfer and charges 25 for the
        hotel one. Printing "included" there calls a published charge free."""
        fees = self.fees(
            "Included: Airport Transfer."
            "Optional Extras: Hotel Transfer (\u20ac25 / trip)."
        )
        transfer = fees[FeeCode.AIRPORT_TRANSFER]
        self.assertFalse(transfer.included)
        self.assertEqual(transfer.low, 25.0)

    def test_but_an_inclusion_beats_a_charge_with_no_figure(self):
        """Dune Longara lists a transfer with no price and states the transfer
        as included. "Listed with no price" there is this parser missing an
        answer that was on the page."""
        fees = self.fees(
            "Included: Airport Transfer."
            "Optional Extras: Hotel Transfer, Laundry / Pressing Services."
        )
        self.assertTrue(fees[FeeCode.AIRPORT_TRANSFER].included)

    def test_an_inclusion_keeps_the_place_of_the_line_it_displaced(self):
        """Order is the breakdown's order. A displaced line moving to the end
        would reshuffle the table for no reason a reader could see."""
        codes = [f.code for f in parse_extras(
            "Included: Airport Transfer."
            "Optional Extras: Hotel Transfer, Laundry / Pressing Services."
        )]
        self.assertEqual(codes, [FeeCode.AIRPORT_TRANSFER, FeeCode.LAUNDRY])

    def test_an_amenity_nobody_can_classify_is_not_a_fee(self):
        """Most of the block is towels and coffee. 40 distinct entries across
        the fleet and 15 of them name a charge this project models."""
        fees = self.fees("Included: Drinking Water, Cabin Towels, WiFi internet.")
        self.assertEqual(fees, {})

    def test_a_mandatory_charge_is_never_displaced(self):
        """The block an entry sits in is the claim. A required charge saying a
        diver pays outranks another block saying they do not, priced or not."""
        fees = self.fees(
            "Included: Port Fees."
            "Required Extras: Port Fees."
        )
        self.assertFalse(fees[FeeCode.PORT_FEES].included)

    def test_the_heading_survives_the_excerpt_round_trip(self):
        """`drift` rebuilds this text from the stored excerpt, whose keys are
        the bare heading words, so `BLOCK` has to accept "Included Extras:"
        as well as "Included:"."""
        from liveaboard.scrape.fees import extras_excerpt

        excerpt = extras_excerpt(self.TEXT)
        self.assertEqual(sorted(excerpt), ["included", "optional", "required"])
        rebuilt = "\n".join(f"{h.title()} Extras: {b}" for h, b in excerpt.items())
        self.assertEqual({f.code for f in parse_extras(rebuilt)},
                         {f.code for f in parse_extras(self.TEXT)})


class TestFeeBookDrift(unittest.TestCase):
    """Whether the committed book is what today's parser would produce.

    ``promote`` prefers the book over the daily run's own parse, rightly -- a
    browser sees extras the raw HTML never will. The cost is that a fee-parser
    fix reaches nothing until the weekly browser run goes again, and a refresh
    can run entirely green while the page keeps charges the fix removed.
    """

    def book(self, fees, disclosure=None):
        return {"vessels": {"alia-soul": {
            "disclosure": disclosure or {
                "required": "National Park Fees (€35 / trip).",
            },
            "fees": fees,
        }}}

    def test_a_book_matching_the_parser_reports_nothing(self):
        from liveaboard.scrape.fees import drift, parse_extras, to_fee_dicts

        text = "Required Extras: National Park Fees (€35 / trip)."
        fresh = to_fee_dicts(parse_extras(text), {})
        self.assertEqual(drift(self.book(fresh)), {})

    def test_a_book_the_parser_no_longer_agrees_with_is_reported(self):
        from liveaboard.scrape.fees import drift

        stale = [{"code": "marine_park", "tier": "mandatory", "basis": "per_trip",
                  "included": False, "amount": {"amount": 999.0, "currency": "EUR"}}]
        report = drift(self.book(stale))
        self.assertIn("alia-soul", report)
        gained, lost = report["alia-soul"]
        self.assertTrue(any("999" in entry for entry in lost))

    def test_gear_is_excluded_because_its_price_is_not_in_the_text(self):
        """The first run of this check reported all seventy-nine vessels.

        The disclosure lists "Rental Gear" and stops; the figure is in the
        #modal-gear dialog and overwrites the unpriced line. Re-reading the
        text alone can never reproduce it, so comparing them is meaningless.
        """
        from liveaboard.scrape.fees import drift

        priced_gear = [{"code": "gear_rental", "tier": "optional",
                        "basis": "per_week", "included": False,
                        "amount": {"amount": 206.0, "currency": "EUR"}}]
        book = self.book(priced_gear, disclosure={"optional": "Rental Gear."})
        self.assertEqual(drift(book), {})

    def test_a_vessel_with_no_stored_text_is_not_called_drifted(self):
        """Collected before the text was kept. Unanswerable, not wrong."""
        from liveaboard.scrape.fees import drift

        self.assertEqual(drift({"vessels": {"x": {"fees": [], "disclosure": {}}}}), {})


class TestTheChargesOnlyTheSecondSellerNames(unittest.TestCase):
    """Six wordings PADI's fee book uses and liveaboard.com's does not.

    They were the *only* thing keeping 41 trips from claiming a total: every
    other charge on those trips was named, priced and in a unit that
    normalises, so the page showed a berth price with no bill beside it —
    which is the state this site exists to correct in other people.

    Each is `isMandatory` on the source's own say-so and each is a port or
    government charge with no judgement in it, which is what separates these
    from the entries below that must keep failing.
    """

    UNREAD = (
        ("Local fees", FeeCode.LOCAL_FEES),
        ("Local Fees", FeeCode.LOCAL_FEES),
        ("Hospitality Fee", FeeCode.HOSPITALITY_FEE),
        ("Route supplement", FeeCode.ROUTE_SUPPLEMENT),
        ("Coast Guard Fee", FeeCode.COAST_GUARD),
        ("Navy fee", FeeCode.NAVY_FEE),
        ("Environmental/Government Fee", FeeCode.ENVIRONMENT_TAX),
    )

    def test_each_wording_now_resolves(self):
        for label, code in self.UNREAD:
            self.assertEqual(classify_label(label, prose=False), code, label)

    def test_the_operators_misspelling_is_in_the_table_like_the_others(self):
        """`Cost Gard Fee` is wrong on the operator's side, and is listed the
        way TITLE_FIXES lists the two misspellings of Daedalus: the trip's own
        sibling entries name the charge correctly, so the correction is
        confirmed by the data rather than guessed at."""
        self.assertEqual(classify_label("Cost Gard Fee", prose=False), FeeCode.COAST_GUARD)

    def test_the_authorities_are_not_folded_into_one_code(self):
        """The parser keeps one entry per code, so a shared code drops the
        second charge and shows the boat cheaper by exactly what it left out.
        Andromeda bills a Navy fee *and* an Environmental/Government Fee on the
        same trip, which is the case that settles it."""
        codes = {classify_label(label, prose=False)
                 for label in ("Navy fee", "Environmental/Government Fee",
                               "Coast Guard Fee", "Local fees")}
        self.assertEqual(len(codes), 4)

    def test_they_are_not_folded_into_the_charges_they_sit_beside(self):
        """Cassiopeia Glory bills a Navy fee beside a port fee, and Bella 2 a
        Coast Guard Fee beside a marine park fee. Filing either under its
        neighbour would have dropped one of the two."""
        self.assertNotEqual(classify_label("Navy fee", prose=False), FeeCode.PORT_FEES)
        self.assertNotEqual(classify_label("Coast Guard Fee", prose=False),
                            FeeCode.MARINE_PARK)

    STILL_DECLINED = (
        # A percentage in a price field is not an amount. Adding 14 to a bill
        # would be a number nobody quoted.
        "14% GST (on onboard purchases)",
        "15% Local GST (on onboard purchases)",
        # Conditional on who is diving, not a charge everyone pays.
        "Supervision fees for Level 1 divers and Level 2 divers beyond 20m:",
        # Gear, and gear is a toggle.
        "Fins, mask, snorkel (ABC)",
    )

    def test_what_must_keep_failing_still_does(self):
        """Each of these keeps its trip incomplete, which is the safe
        direction: a bill built from part of a fee book shows the seller
        cheaper by exactly what it left out."""
        for label in self.STILL_DECLINED:
            self.assertIsNone(classify_label(label, prose=False), label)

    def test_a_label_naming_two_charges_is_combined_rather_than_declined(self):
        """"Environmental and Route Fees" was in the list above, declined
        because filing it under half of itself would name a charge the
        operator did not. That is right about `environment_tax` and wrong
        about the outcome: `combined_fees` is the code for one line covering
        several, it keeps the whole amount undivided, and declining blocked
        the trip's bill instead. `route` joins the four parts `_combined_fee`
        already counts."""
        self.assertEqual(classify_label("Environmental and Route Fees", prose=False),
                         FeeCode.COMBINED_FEES)

    def test_a_route_supplement_on_its_own_is_still_its_own_charge(self):
        """One part is not a combination, and "Route supplement" carries none
        of the words `COMBINED_TAIL` looks for either."""
        self.assertEqual(classify_label("Route supplement", prose=False),
                         FeeCode.ROUTE_SUPPLEMENT)

    def test_the_chamber_levy_is_read_and_is_narrow(self):
        """A per-diver contribution to the recompression chamber: a charge to
        a named third party, on trips that bill park fees and a service charge
        separately, so it is its own code rather than a share of either."""
        self.assertEqual(classify_label("Hyperbaric chamber contribution", prose=False),
                         FeeCode.HYPERBARIC_LEVY)
        self.assertIsNone(classify_label("Chamber cabin", prose=False))

    def test_none_of_them_reaches_past_its_own_wording(self):
        """The reason they are listed rather than generalised into "any
        authority charge": a near-miss rule that catches these also catches
        something that only looks like them."""
        for label in ("Local tax", "Local dive guide", "Coastal excursion",
                      "Supplement for a single cabin", "Government of Egypt"):
            with self.subTest(label=label):
                self.assertNotIn(
                    classify_label(label, prose=False),
                    {FeeCode.LOCAL_FEES, FeeCode.COAST_GUARD, FeeCode.NAVY_FEE,
                     FeeCode.ROUTE_SUPPLEMENT, FeeCode.HOSPITALITY_FEE},
                )


class TestTheSellerDecidesWhatIsCounted(unittest.TestCase):
    """One rule, stated where a reader will look for it: a billed charge lands
    in the tier its own seller's block gives it. Nothing here promotes a code
    past that block, and the case that made it matter was tips.
    """

    PROV = {"kind": "scraped", "source_id": "liveaboard.com", "retrieved": "2026-08-27"}

    def tiers(self, text):
        from liveaboard.scrape.fees import parse_extras

        return {f.code: f.tier for f in parse_extras(text)}

    def test_a_tip_under_optional_is_optional(self):
        got = self.tiers("Optional Extras: Gratuities (€80 / trip).")
        self.assertEqual(got[FeeCode.GRATUITIES], FeeTier.OPTIONAL)

    def test_a_tip_under_required_is_mandatory(self):
        """No seller does this today. If one starts, the charge is counted and
        no table here needs editing for that to happen."""
        got = self.tiers("Required Extras: Gratuities (€80 / trip).")
        self.assertEqual(got[FeeCode.GRATUITIES], FeeTier.MANDATORY)

    def test_the_toggled_codes_still_outrank_the_optional_block(self):
        """Deliberately not the same case. Nitrox and gear are conditional
        because the *page* puts a switch on them, which is a fact about this
        site rather than a claim about the operator's charge."""
        got = self.tiers("Optional Extras: Nitrox (€30 / trip), Rental Gear (€25).")
        self.assertEqual(got[FeeCode.NITROX], FeeTier.CONDITIONAL)
        self.assertEqual(got[FeeCode.GEAR_RENTAL], FeeTier.CONDITIONAL)

    def test_nothing_emits_the_customary_tier_any_more(self):
        """It stays in the vocabulary and in DEFAULT_ON_TIERS, which app.js
        mirrors, so the two can keep moving together. But no parser writes it,
        and a seller that files a tip as owed writes MANDATORY instead."""
        from liveaboard.scrape.fees import _tier_for

        for code in FeeCode:
            for required in (True, False):
                self.assertNotEqual(_tier_for(code, required), FeeTier.CUSTOMARY)

    def test_an_included_tip_is_optional_at_zero(self):
        """An operator stating tips as covered has covered a cost its guests
        choose the size of, so the line belongs beside the other optional ones
        rather than among the charges nobody can decline."""
        from liveaboard.scrape.fees import tier_for_inclusion

        self.assertEqual(tier_for_inclusion(FeeCode.GRATUITIES), FeeTier.OPTIONAL)
        self.assertEqual(tier_for_inclusion(FeeCode.MARINE_PARK), FeeTier.MANDATORY)
