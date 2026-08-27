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
from liveaboard.dataset import Dataset
from liveaboard.pricing import compute, mandatory_known, resolve_fees
from liveaboard.render import TEMPLATE_DIR, build_payload
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
        result = compute(itinerary, make_departure(), FX, {"nitrox": True, "gear": True})
        self.assertEqual(result.total.amount, Decimal("1000"))

    def test_an_optional_fee_with_a_toggle_follows_the_toggle(self):
        """The switch on the page has to change the number beside it.

        liveaboard.com files rental gear under *Optional* Extras, and the
        counting rule tested the tier before the toggle -- so the optional
        branch returned first and turning "Rental gear" on added nothing to
        any total. A switch that changes no number answers the visitor's
        question with a figure that ignored them.
        """
        itinerary = make_itinerary([fee(FeeCode.GEAR_RENTAL, FeeTier.OPTIONAL, "200 EUR")])
        off = compute(itinerary, make_departure(), FX, {"gear": False})
        on = compute(itinerary, make_departure(), FX, {"gear": True})
        self.assertEqual(off.total.amount, Decimal("1000"))
        self.assertEqual(on.total.amount, Decimal("1200"))

    def test_the_page_counts_lines_the_same_way_python_does(self):
        """DEFAULT_ON_TIERS is not the only rule the JS mirrors.

        The order of the toggle and tier checks is load-bearing too, and the
        two copies drifted apart once already.
        """
        js = (TEMPLATE_DIR / "app.js").read_text(encoding="utf-8")
        body = js[js.index("function lineCounts"):]
        body = body[: body.index("}")]
        self.assertLess(
            body.index("line.toggle"),
            body.index('line.tier === "optional"'),
            "app.js must ask the toggle before the tier, as pricing._is_counted does",
        )

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


class TestBundlingIsVisibleWithoutAScore(unittest.TestCase):
    """Two boats can cost the same and price very differently.

    The site used to express that as an honesty percentage per operator, which
    made the page a league table and contradicted the total beside it. The
    difference still has to be visible -- it is the whole point -- but as lines
    in the breakdown rather than as a grade.
    """

    def test_equal_true_cost_still_reads_differently(self):
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
        # The advertised prices are what differ, and that is what a visitor
        # comparing the two actually needs to see.
        self.assertLess(
            compute(hidden, cheap, FX).base.amount,
            compute(bundled, dear, FX).base.amount,
        )

    def test_a_bundled_fee_keeps_its_line_at_zero(self):
        """Deleting it would erase the difference between the two boats."""
        bundled = make_itinerary(
            [fee(FeeCode.MARINE_PARK, FeeTier.MANDATORY, "0 EUR", included=True)]
        )
        result = compute(bundled, make_departure("1200 EUR"), FX)
        park = [line for line in result.lines if line.code is FeeCode.MARINE_PARK]
        self.assertEqual(len(park), 1)
        self.assertTrue(park[0].included)


class TestOnlyOptionalExtrasIsNotACleanBill(unittest.TestCase):
    """Seven of seventy-nine vessels publish optional extras and no required ones.

    Counting that silence as zero made the site rank them as its most honest
    operators: odyssey scored 96% and emperor-asmaa 93%, against 86% for a
    vessel that published its park and port fees in full.
    """

    PROV = {"kind": "scraped", "source_id": "liveaboard.com", "retrieved": "2026-08-27"}

    def dataset(self, fees):
        return Dataset.from_dict({
            "schema_version": 1, "generated": "2026-08-27", "default_currency": "EUR",
            "notes": "t",
            "fx": {"display_currency": "EUR", "as_of": "2026-08-27",
                   "source": "test", "rates": {"USD": 0.92}},
            "operators": [{"id": "o", "name": "O"}],
            "boats": [{"id": "b", "name": "B", "operator_id": "o"}],
            "itineraries": [{
                "id": "i", "name": "I", "operator_id": "o", "boat_id": "b",
                "nights": 7, "dives": 0, "port_from": "Hurghada",
                "port_to": "Hurghada", "dive_sites": ["brothers"], "fees": fees,
            }],
            "departures": [{
                "id": "d", "itinerary_id": "i", "start": "2027-05-01",
                "end": "2027-05-08", "price": {"amount": 1500.0, "currency": "EUR"},
                "provenance": self.PROV,
            }],
        })

    def fee(self, code, tier, amount=100.0, included=False):
        entry = {"code": code, "tier": tier, "basis": "per_trip",
                 "included": included, "provenance": self.PROV}
        entry["amount"] = {"amount": amount, "currency": "EUR"} if amount else None
        return entry

    def parts(self, fees):
        d = self.dataset(fees)
        itinerary = next(iter(d.itineraries.values()))
        return itinerary, d.departures[0], d

    def test_optional_extras_alone_leave_the_mandatory_picture_unknown(self):
        itinerary, departure, _ = self.parts([
            self.fee("gratuities", "customary"),
            self.fee("gear_rental", "conditional", None),
            self.fee("course", "optional", 250.0),
        ])
        self.assertFalse(mandatory_known(itinerary, departure))

    def test_one_required_line_is_enough_to_know(self):
        itinerary, departure, _ = self.parts([self.fee("marine_park", "mandatory", 80.0)])
        self.assertTrue(mandatory_known(itinerary, departure))

    def test_a_bundled_fee_still_counts_as_stated(self):
        """An operator saying "it is in the fare" is the honest case."""
        itinerary, departure, _ = self.parts([
            self.fee("marine_park", "mandatory", 0.0, included=True),
        ])
        self.assertTrue(mandatory_known(itinerary, departure))

    def test_the_page_is_told_the_required_picture_is_missing(self):
        payload = build_payload(self.dataset([
            self.fee("gratuities", "customary"),
            self.fee("course", "optional", 250.0),
        ]))
        self.assertFalse(payload["departures"][0]["mandatory_known"])

    def test_a_disclosing_operator_gets_a_total(self):
        payload = build_payload(self.dataset([
            self.fee("marine_park", "mandatory", 150.0),
            self.fee("gratuities", "customary"),
        ]))
        self.assertTrue(payload["departures"][0]["mandatory_known"])

    def test_the_page_carries_no_score_to_rank_operators_by(self):
        """Comparing trips is the job; grading operators is not."""
        payload = build_payload(self.dataset([
            self.fee("marine_park", "mandatory", 150.0),
        ]))
        self.assertNotIn("transparency", payload["departures"][0])


if __name__ == "__main__":
    unittest.main()
