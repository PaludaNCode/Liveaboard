"""Tests for the true-cost engine.

Written against ``unittest`` so the suite runs with no installed dependencies:
``python3 -m unittest discover -s tests``.
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from liveaboard.models import Departure, FeeItem, Itinerary, Provenance
from liveaboard.money import FxTable, Money
from liveaboard.pricing import REFERENCE_BASKET, compute, resolve_fees, transparency_score
from liveaboard.taxonomy import FeeBasis, FeeCode, FeeTier, SourceKind

FX = FxTable.from_dict(
    {
        "display_currency": "EUR",
        "as_of": "2026-08-27",
        "source": "test",
        "rates": {"USD": 0.5},
    }
)

PROV = Provenance(kind=SourceKind.SCRAPED, source_id="test", retrieved=date(2026, 8, 27))


def make_itinerary(fees: list[FeeItem] | None = None, nights: int = 7, dives: int = 20) -> Itinerary:
    return Itinerary(
        id="itin",
        name="Test itinerary",
        operator_id="op",
        boat_id="boat",
        nights=nights,
        dives=dives,
        port_from="Hurghada",
        port_to="Hurghada",
        fees=fees or [],
    )


def make_departure(price: str = "1000 EUR", fees: list[FeeItem] | None = None) -> Departure:
    return Departure(
        id="dep",
        itinerary_id="itin",
        start=date(2027, 5, 1),
        end=date(2027, 5, 8),
        price=Money.parse(price),
        price_provenance=PROV,
        fees=fees or [],
    )


def fee(code: FeeCode, tier: FeeTier, amount: str, **kwargs) -> FeeItem:
    return FeeItem(code=code, tier=tier, amount=Money.parse(amount), provenance=PROV, **kwargs)


class TestFeeNormalisation(unittest.TestCase):
    def test_per_trip_is_unchanged(self):
        item = fee(FeeCode.MARINE_PARK, FeeTier.MANDATORY, "90 EUR")
        self.assertEqual(item.for_trip(7, 20).amount, Decimal("90"))

    def test_per_night_multiplies_by_nights(self):
        item = fee(FeeCode.PORT_FEES, FeeTier.MANDATORY, "10 EUR", basis=FeeBasis.PER_NIGHT)
        self.assertEqual(item.for_trip(7, 20).amount, Decimal("70"))

    def test_per_dive_multiplies_by_dives(self):
        item = fee(FeeCode.GUIDED_DIVING, FeeTier.CONDITIONAL, "6 EUR", basis=FeeBasis.PER_DIVE)
        self.assertEqual(item.for_trip(7, 20).amount, Decimal("120"))

    def test_per_day_counts_the_extra_day(self):
        """A seven-night trip is eight days aboard; day-rate fees must reflect that."""
        item = fee(FeeCode.GRATUITIES, FeeTier.CUSTOMARY, "15 EUR", basis=FeeBasis.PER_DAY)
        self.assertEqual(item.for_trip(7, 20).amount, Decimal("120"))


class TestTotals(unittest.TestCase):
    def test_mandatory_fees_are_added(self):
        itinerary = make_itinerary([fee(FeeCode.MARINE_PARK, FeeTier.MANDATORY, "90 EUR")])
        result = compute(itinerary, make_departure(), FX)
        self.assertEqual(result.total.amount, Decimal("1090"))

    def test_included_fees_add_nothing_but_still_appear(self):
        """A bundled fee must stay visible: hiding it hides the operator's honesty."""
        itinerary = make_itinerary(
            [fee(FeeCode.MARINE_PARK, FeeTier.MANDATORY, "0 EUR", included=True)]
        )
        result = compute(itinerary, make_departure(), FX)
        self.assertEqual(result.total.amount, Decimal("1000"))
        codes = [line.code for line in result.lines]
        self.assertIn(FeeCode.MARINE_PARK, codes)

    def test_optional_fees_never_count(self):
        itinerary = make_itinerary(
            [fee(FeeCode.SINGLE_SUPPLEMENT, FeeTier.OPTIONAL, "400 EUR")]
        )
        result = compute(itinerary, make_departure(), FX, REFERENCE_BASKET)
        self.assertEqual(result.total.amount, Decimal("1000"))

    def test_conditional_fee_follows_its_toggle(self):
        itinerary = make_itinerary([fee(FeeCode.NITROX, FeeTier.CONDITIONAL, "120 EUR")])
        off = compute(itinerary, make_departure(), FX, {"nitrox": False})
        on = compute(itinerary, make_departure(), FX, {"nitrox": True})
        self.assertEqual(off.total.amount, Decimal("1000"))
        self.assertEqual(on.total.amount, Decimal("1120"))

    def test_customary_fees_count_by_default(self):
        """Gratuities are not contractual, but excluding them understates the bill."""
        itinerary = make_itinerary([fee(FeeCode.GRATUITIES, FeeTier.CUSTOMARY, "120 EUR")])
        result = compute(itinerary, make_departure(), FX)
        self.assertEqual(result.total.amount, Decimal("1120"))

    def test_markup_and_per_night(self):
        itinerary = make_itinerary([fee(FeeCode.MARINE_PARK, FeeTier.MANDATORY, "400 EUR")])
        result = compute(itinerary, make_departure(), FX)
        self.assertEqual(result.surcharge.amount, Decimal("400"))
        self.assertAlmostEqual(result.markup_pct, 40.0)
        self.assertEqual(result.per_night.amount, Decimal("200"))


class TestCurrency(unittest.TestCase):
    def test_price_is_converted_to_display_currency(self):
        result = compute(make_itinerary(), make_departure("1000 USD"), FX)
        self.assertEqual(result.total.currency, "EUR")
        self.assertEqual(result.total.amount, Decimal("500.0"))

    def test_conversion_is_recorded_on_the_line(self):
        result = compute(make_itinerary(), make_departure("1000 USD"), FX)
        base_line = result.lines[0]
        self.assertIsNotNone(base_line.fx_rate)
        self.assertEqual(base_line.quoted.currency, "USD")

    def test_euro_price_is_not_marked_converted(self):
        result = compute(make_itinerary(), make_departure("1000 EUR"), FX)
        self.assertIsNone(result.lines[0].fx_rate)

    def test_mixed_currency_addition_is_refused(self):
        with self.assertRaises(ValueError):
            Money.parse("10 EUR") + Money.parse("10 USD")


class TestFeeResolution(unittest.TestCase):
    def test_departure_fee_replaces_itinerary_fee(self):
        itinerary = make_itinerary([fee(FeeCode.FUEL_SURCHARGE, FeeTier.MANDATORY, "50 EUR")])
        departure = make_departure(fees=[fee(FeeCode.FUEL_SURCHARGE, FeeTier.MANDATORY, "80 EUR")])
        resolved = resolve_fees(itinerary, departure)
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0].amount.amount, Decimal("80"))

    def test_lines_are_ordered_least_avoidable_first(self):
        itinerary = make_itinerary(
            [
                fee(FeeCode.ALCOHOL, FeeTier.OPTIONAL, "100 EUR"),
                fee(FeeCode.GRATUITIES, FeeTier.CUSTOMARY, "120 EUR"),
                fee(FeeCode.MARINE_PARK, FeeTier.MANDATORY, "90 EUR"),
            ]
        )
        result = compute(itinerary, make_departure(), FX)
        tiers = [line.tier for line in result.lines]
        self.assertEqual(
            tiers,
            [FeeTier.BASE, FeeTier.MANDATORY, FeeTier.CUSTOMARY, FeeTier.OPTIONAL],
        )


class TestTransparencyScore(unittest.TestCase):
    def test_all_inclusive_scores_perfectly(self):
        itinerary = make_itinerary(
            [
                fee(FeeCode.MARINE_PARK, FeeTier.MANDATORY, "0 EUR", included=True),
                fee(FeeCode.NITROX, FeeTier.CONDITIONAL, "0 EUR", included=True),
            ]
        )
        self.assertEqual(transparency_score(itinerary, make_departure(), FX), 1.0)

    def test_hidden_fees_lower_the_score(self):
        itinerary = make_itinerary([fee(FeeCode.MARINE_PARK, FeeTier.MANDATORY, "250 EUR")])
        self.assertAlmostEqual(transparency_score(itinerary, make_departure(), FX), 0.8)

    def test_score_ignores_visitor_toggles(self):
        """The score describes the operator, so it must not move with the UI."""
        itinerary = make_itinerary(
            [
                fee(FeeCode.MARINE_PARK, FeeTier.MANDATORY, "100 EUR"),
                fee(FeeCode.NITROX, FeeTier.CONDITIONAL, "150 EUR"),
            ]
        )
        departure = make_departure()
        first = transparency_score(itinerary, departure, FX)
        compute(itinerary, departure, FX, {"nitrox": False})
        self.assertEqual(first, transparency_score(itinerary, departure, FX))

    def test_bundling_beats_itemising_at_equal_true_cost(self):
        """Two boats costing the same in the end must not score the same."""
        hidden = make_itinerary([fee(FeeCode.MARINE_PARK, FeeTier.MANDATORY, "200 EUR")])
        bundled = make_itinerary(
            [fee(FeeCode.MARINE_PARK, FeeTier.MANDATORY, "0 EUR", included=True)]
        )
        cheap = make_departure("1000 EUR")
        dear = make_departure("1200 EUR")

        self.assertEqual(
            compute(hidden, cheap, FX).total.amount,
            compute(bundled, dear, FX).total.amount,
        )
        self.assertGreater(
            transparency_score(bundled, dear, FX),
            transparency_score(hidden, cheap, FX),
        )


if __name__ == "__main__":
    unittest.main()
