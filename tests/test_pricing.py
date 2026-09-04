"""Tests for the true-cost engine.

Written against ``unittest`` so the suite runs with no installed dependencies:
``python3 -m unittest discover -s tests``.
"""

from __future__ import annotations

import unittest
from dataclasses import replace
from typing import Any
from datetime import date
from decimal import Decimal

from liveaboard.models import Departure, FeeItem, Itinerary, Provenance
from liveaboard.money import FxTable, Money
from liveaboard.dataset import Dataset
from liveaboard.pricing import (
    GEAR_ESTIMATE,
    _is_counted,
    compute,
    itinerary_lines,
    mandatory_known,
    padi_lines,
    subsumed_charges,
    resolve_fees,
)
from liveaboard.render import TEMPLATE_DIR, build_payload
from liveaboard.taxonomy import DEFAULT_ON_TIERS, FeeBasis, FeeCode, FeeTier, SourceKind

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

    def test_the_page_counts_the_same_tiers_by_default(self):
        """The other half of the mirror: *which* tiers count, not just the order.

        CLAUDE.md names both -- "both DEFAULT_ON_TIERS and the order of its
        checks" -- and only the order was pinned. So adding a tier to the Python
        frozenset, or dropping one from the JS object, changed what the page
        totals with nothing failing. Two implementations of the same rule need
        both halves held together or the pin is only half a pin.

        Read out of the JS source rather than executed: the suite runs on the
        standard library, and the declaration is a literal.
        """
        js = (TEMPLATE_DIR / "app.js").read_text(encoding="utf-8")
        literal = js[js.index("var DEFAULT_ON_TIERS"):]
        literal = literal[literal.index("{") + 1: literal.index("}")]

        in_js = set()
        for entry in literal.split(","):
            if not entry.strip():
                continue
            key, _, value = entry.partition(":")
            # A tier present but set false counts for nothing, so it is not in
            # the set -- matching how the JS itself reads the object.
            if value.strip() == "true":
                in_js.add(key.strip().strip("\"'"))

        self.assertEqual(
            in_js,
            {tier.value for tier in DEFAULT_ON_TIERS},
            "app.js DEFAULT_ON_TIERS must name the same tiers as liveaboard.taxonomy",
        )

    def test_the_two_counting_rules_agree_on_every_tier(self):
        """Belt and braces: drive both rules over every tier and compare.

        The two tests above check the JS text. This checks the *behaviour* they
        are meant to guarantee, so a rule that stops being expressible as a set
        plus an ordering still has to agree tier by tier.
        """
        for tier in FeeTier:
            with self.subTest(tier=tier.value):
                counted = _is_counted(fee(FeeCode.PORT_FEES, tier, "50 EUR"), {})
                expected = tier is not FeeTier.OPTIONAL and tier in DEFAULT_ON_TIERS
                self.assertEqual(counted, expected)

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

    def test_what_lands_after_the_headline(self):
        """Price per night is gone: divers compare per dive, not per night, and
        a second denominator on the same total said nothing the first did not."""
        itinerary = make_itinerary([fee(FeeCode.MARINE_PARK, FeeTier.MANDATORY, "400 EUR")])
        result = compute(itinerary, make_departure(), FX)
        self.assertEqual(result.surcharge.amount, Decimal("400"))
        self.assertAlmostEqual(result.markup_pct, 40.0)
        self.assertNotIn("per_night", result.as_dict())


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


class TestABundleThatNamesItsOwnLine(unittest.TestCase):
    """A charge the seller states twice is counted once, and says so.

    Seawolf Dominator. PADI publishes both of these as required, and we read
    both faithfully -- they are distinct catalogue items, and
    `tools/probe_padi_mandatory.py` established that before anything changed:

        Visa fees                                                     250
        Visa, dive permit, taxes, marine park fees, harbour fee
        and fuel surcharges                                       180-255

    Neither is dropped, and the total is not withheld either. The operator
    itemises the bundle in its own `whatsNotIncluded` prose on 10 of the boat's
    13 itineraries -- *"Visa, dive permission and taxes 43 Euro ... marine
    parks: South: 80 Euro ... Fuel surcharge: 30 Euro"* -- states nothing at
    250 on any of them, and prices the visa at 30 on its other hull. So the
    bundle is the money, the standalone line is a copy of part of it, and the
    copy is shown at its published figure and left out of the sum. See
    `pricing.subsumed_charges` for the readings and what they replace.
    """

    def fee(self, code: FeeCode, amount: str, note: str,
            tier: FeeTier = FeeTier.MANDATORY) -> FeeItem:
        return FeeItem(code=code, tier=tier, basis=FeeBasis.PER_TRIP,
                       amount=Money.parse(amount), note=note, provenance=PROV)

    def raw(self, code: str, amount: float, note: str,
            tier: str = "mandatory") -> dict:
        """The same fee as the dataset spells it, for the payload path."""
        return {"code": code, "tier": tier, "basis": "per_trip",
                "included": False, "note": note,
                "amount": {"amount": amount, "currency": "EUR"},
                "provenance": {"kind": "scraped", "source_id": "test",
                               "retrieved": "2026-08-27"}}

    def dataset(self, fees: list[dict]) -> Dataset:
        """A one-departure dataset carrying these fees."""
        return Dataset.from_dict({
            "schema_version": 1, "generated": "2026-08-27",
            "default_currency": "EUR", "notes": "t",
            "fx": {"display_currency": "EUR", "as_of": "2026-08-27",
                   "source": "test", "rates": {"USD": 0.92}},
            "operators": [{"id": "o", "name": "O"}],
            "boats": [{"id": "b", "name": "B", "operator_id": "o"}],
            "itineraries": [{
                "id": "i", "name": "I", "operator_id": "o", "boat_id": "b",
                "nights": 7, "dives": 20, "port_from": "Hurghada",
                "port_to": "Hurghada",
                "fees": fees,
            }],
            "departures": [{
                "id": "d", "itinerary_id": "i", "start": "2027-05-01",
                "end": "2027-05-08",
                "price": {"amount": 1500.0, "currency": "EUR"},
                "provenance": {"kind": "scraped", "source_id": "test",
                               "retrieved": "2026-08-27"},
            }],
        })

    #: The pair, as Seawolf Dominator publishes it.
    PAIR = [
        {"code": "visa", "amount": 250.0, "note": "Visa fees"},
        {"code": "marine_park", "amount": 255.0,
         "note": "Visa, dive permit, taxes, marine park fees, "
                 "harbour fee and fuel surcharges"},
    ]

    def pair_raw(self) -> list[dict]:
        return [self.raw(f["code"], f["amount"], f["note"]) for f in self.PAIR]

    def test_a_bundle_naming_a_line_that_is_also_billed_alone(self) -> None:
        """The component, and which bundle covers it -- the second half matters
        because the panel names it, and a row that says a published charge is
        not counted has to say what accounts for it."""
        found = subsumed_charges([
            self.fee(FeeCode.VISA, "250 EUR", "Visa fees"),
            self.fee(FeeCode.MARINE_PARK, "255 EUR",
                     "Visa, dive permit, taxes, marine park fees, "
                     "harbour fee and fuel surcharges"),
        ])
        self.assertEqual(found, {FeeCode.VISA: FeeCode.MARINE_PARK})

    def test_a_bundle_naming_nothing_else_on_the_bill_is_fine(self) -> None:
        """Hammerhead II: *Park and Port Fees* beside a separate fuel
        surcharge. The bundle does not name fuel, so nothing is billed twice
        and 29 bills like it count both lines."""
        self.assertEqual(subsumed_charges([
            self.fee(FeeCode.MARINE_PARK, "80 EUR", "Park and Port Fees"),
            self.fee(FeeCode.FUEL_SURCHARGE, "45 EUR", "Fuel surcharge"),
        ]), {})

    def test_two_plain_lines_are_two_charges(self) -> None:
        """Port fees beside a fuel surcharge, on 40 bills. Neither title names
        the other, and a single name is a line however long it is written."""
        self.assertEqual(subsumed_charges([
            self.fee(FeeCode.PORT_FEES, "25 EUR", "Port Fees"),
            self.fee(FeeCode.FUEL_SURCHARGE, "40 EUR", "Fuel surcharges"),
        ]), {})

    def test_an_optional_overlap_changes_no_total(self) -> None:
        """The rule is about charges a diver cannot decline, so an optional
        extra overlapping another is not this. Nothing counts it either way."""
        self.assertEqual(subsumed_charges([
            self.fee(FeeCode.VISA, "250 EUR", "Visa fees", tier=FeeTier.OPTIONAL),
            self.fee(FeeCode.MARINE_PARK, "255 EUR",
                     "Visa, marine park fees and harbour fee",
                     tier=FeeTier.OPTIONAL),
        ]), {})

    def test_the_bundle_is_the_charge_and_the_copy_adds_nothing(self) -> None:
        """The arithmetic, which is the whole point: 1,500 + 255, not + 505.

        And the copy is still a line on the bill at its published figure --
        `compute` is the authority the page's adder mirrors, so both facts are
        asserted here rather than only in the payload.
        """
        dataset = self.dataset(self.pair_raw())
        breakdown = compute(dataset.itineraries["i"], dataset.departures[0],
                            dataset.fx)

        self.assertEqual(float(breakdown.total.amount), 1755.0)
        lines = {line.code: line for line in breakdown.lines}
        self.assertEqual(lines[FeeCode.VISA].subsumed_by, FeeCode.MARINE_PARK)
        self.assertFalse(lines[FeeCode.VISA].counted)
        self.assertEqual(float(lines[FeeCode.VISA].display.amount), 250.0)
        self.assertIsNone(lines[FeeCode.MARINE_PARK].subsumed_by)
        self.assertTrue(lines[FeeCode.MARINE_PARK].counted)

    def test_the_page_ships_the_line_and_what_covers_it(self) -> None:
        """What the reader gets: the fare, every fee line, a total, and the
        name of the bundle that accounts for the line not in it."""
        payload = build_payload(self.dataset(self.pair_raw()))
        lines = {line["code"]: line
                 for line in payload["itineraries"]["i"]["lines"]}

        self.assertEqual(lines["visa"]["subsumed_by"], "marine_park")
        self.assertEqual(lines["visa"]["display"]["amount"], 250.0)
        self.assertIn("marine_park", payload["fee_labels"])
        entry = payload["departures"][0]
        self.assertTrue(entry["fees_known"])
        self.assertTrue(entry["mandatory_known"])
        self.assertIn("base", entry)

    def test_a_clean_bill_carries_no_such_key(self) -> None:
        """One code on one boat in the fleet. A key written per fee line is
        written on 2,400 of them, so it is absent wherever it does not fire."""
        payload = build_payload(self.dataset([
            self.raw("marine_park", 80.0, "Park and Port Fees"),
        ]))
        for line in payload["itineraries"]["i"]["lines"]:
            self.assertNotIn("subsumed_by", line)


class TestGearWithNoStatedPriceIsEstimated(unittest.TestCase):
    """The one invented figure on the page, and everything that keeps it honest.

    "Never invent a price" is this project's oldest rule and rental gear is its
    single exception, taken deliberately. Three readings of the gear dialog
    produce no set price -- an operator pricing items and never a bundle, a
    bundle figure with no unit beside it, and a bare "Rental Gear" -- and on
    all of them the line sat at nothing with the toggle **on by default**, so
    the Total was short by a week's hire on 228 sailings and said so only to a
    reader who opened the bill and read the caveat.

    So the figure is stated and stated as ours. What these assert is the
    "as ours" half: the flag travels, the note admits it, the footer's
    fleet-wide gear figure is computed without it, and nothing else on any bill
    is ever filled in.
    """

    def gear(self, amount: str | None = None, included: bool = False,
             note: str | None = None, basis: FeeBasis = FeeBasis.PER_WEEK) -> FeeItem:
        return FeeItem(
            code=FeeCode.GEAR_RENTAL,
            tier=FeeTier.OPTIONAL,
            amount=Money.parse(amount) if amount else None,
            basis=basis,
            included=included,
            provenance=PROV,
            note=note,
        )

    def line(self, fee: FeeItem, nights: int = 7, toggles=None) -> Any:
        itinerary = make_itinerary([fee], nights=nights)
        breakdown = compute(itinerary, make_departure(), FX, toggles)
        return next(l for l in breakdown.lines if l.code is FeeCode.GEAR_RENTAL)

    def test_an_unpriced_gear_line_is_filled_at_the_stated_figure(self):
        line = self.line(self.gear())
        self.assertEqual(line.display.amount, GEAR_ESTIMATE.amount)
        self.assertTrue(line.has_price)
        self.assertTrue(line.estimated)

    def test_it_reaches_the_total_it_was_missing_from(self):
        """The whole point. Gear is on by default, so a line at nothing was a
        total short by a week's hire on a fifth of the table."""
        itinerary = make_itinerary([self.gear()])
        on = compute(itinerary, make_departure(), FX, {"gear": True})
        off = compute(itinerary, make_departure(), FX, {"gear": False})
        self.assertEqual(on.total.amount - off.total.amount, GEAR_ESTIMATE.amount)

    def test_switching_gear_off_still_removes_it(self):
        """An estimate is not a charge a reader cannot decline. It follows the
        same switch every other gear figure does."""
        line = self.line(self.gear(), toggles={"gear": False})
        self.assertFalse(line.counted)
        self.assertTrue(line.estimated)

    def test_it_is_a_trip_figure_and_a_fortnight_does_not_double_it(self):
        """Per trip, chosen along with the number: the unit the page reasons in
        is the trip, and `PER_WEEK` on a fourteen-night sailing would charge
        two of an estimate nobody quoted."""
        short = self.line(self.gear(), nights=3)
        long = self.line(self.gear(), nights=14)
        self.assertEqual(short.display.amount, long.display.amount)
        self.assertEqual(long.display.amount, GEAR_ESTIMATE.amount)

    def test_the_operators_own_wording_survives_in_front_of_it(self):
        """The per-item prices are the only thing a reader can check the
        estimate against, so the note admits the figure is ours and then keeps
        what the page actually said."""
        line = self.line(self.gear(note="Operator prices gear per item: BCD €5/day"))
        self.assertIn("estimated by this site", line.note)
        self.assertIn("BCD €5/day", line.note)

    def test_a_priced_set_is_left_exactly_as_the_operator_quoted_it(self):
        line = self.line(self.gear("200 EUR", basis=FeeBasis.PER_WEEK))
        self.assertEqual(line.display.amount, Decimal("200"))
        self.assertFalse(line.estimated)

    def test_a_figure_with_no_unit_is_never_overwritten(self):
        """The estimate answers a silence; it does not correct a source.

        Four vessels quote a set price and no unit -- Bella 2 €40, Blue Pearl
        €135, Ghazala Adventure €200, Emperor Superior €206 -- and `amount` is
        `None` there because the *unit* is missing, not the number. Filling 180
        put this site's guess over the operator's own price on 82 sailings."""
        fee = replace(self.gear(note="Full equipment hire: BCD, Fins €40, "
                                    "with no unit stated"), unit_unstated=True)
        line = self.line(fee)
        self.assertFalse(line.estimated)
        self.assertFalse(line.has_price)
        self.assertIsNone(line.display)

    def test_and_the_operator_s_own_figure_survives_in_the_note(self):
        """What the page said is the whole of what is left on that line, so it
        may not be replaced by wording about an estimate that did not happen."""
        fee = replace(self.gear(note="Full equipment hire: BCD, Fins €40, "
                                    "with no unit stated"), unit_unstated=True)
        note = self.line(fee).note
        self.assertIn("€40", note)
        self.assertNotIn("estimated by this site", note)

    def test_a_silence_is_still_filled_beside_it(self):
        """The two states are told apart by the flag and nothing else: same
        code, same absent amount, opposite answers."""
        self.assertTrue(self.line(self.gear()).estimated)
        self.assertFalse(self.line(replace(self.gear(), unit_unstated=True)).estimated)

    def test_gear_the_operator_includes_is_not_filled_in(self):
        """An inclusion is an answer. Putting 180 on it would price a set the
        operator says it does not charge for -- the opposite failure."""
        line = self.line(self.gear(included=True))
        self.assertTrue(line.included)
        self.assertFalse(line.estimated)
        self.assertFalse(line.has_price)

    def test_no_other_unpriced_line_is_ever_filled(self):
        """The exception is gear and it is only gear. Every other unstated fee
        is a known cost of unknown size and stays one."""
        itinerary = make_itinerary([
            FeeItem(code=FeeCode.NITROX, tier=FeeTier.CONDITIONAL, amount=None,
                    provenance=PROV),
            FeeItem(code=FeeCode.MARINE_PARK, tier=FeeTier.MANDATORY, amount=None,
                    provenance=PROV),
        ])
        for line in compute(itinerary, make_departure(), FX).lines:
            if line.code is not FeeCode.BASE_FARE:
                with self.subTest(code=line.code):
                    self.assertFalse(line.has_price)
                    self.assertFalse(line.estimated)

    def test_the_estimate_is_attributed_and_does_not_light_the_seed_banner(self):
        """`DERIVED`, not `SEED_ESTIMATE`. The banner means *this row is
        placeholder research*; a real sailing at a real fare with one estimated
        extra is not that, and firing it on a fifth of the table would teach a
        reader to ignore it."""
        breakdown = compute(make_itinerary([self.gear()]), make_departure(), FX)
        line = next(l for l in breakdown.lines if l.code is FeeCode.GEAR_RENTAL)
        self.assertIs(line.provenance.kind, SourceKind.DERIVED)
        self.assertFalse(breakdown.has_unverified)

    def test_the_page_is_told_which_figure_is_ours(self):
        """It ships only where true, and app.js reads it -- both halves,
        because a flag nothing renders is a number presented as a quote."""
        itinerary = make_itinerary([self.gear()])
        estimated = itinerary_lines(itinerary, FX)[0].as_dict()
        priced = itinerary_lines(make_itinerary([self.gear("200 EUR")]), FX)[0].as_dict()
        self.assertTrue(estimated["estimated"])
        self.assertNotIn("estimated", priced)
        js = (TEMPLATE_DIR / "app.js").read_text(encoding="utf-8")
        self.assertIn("line.estimated", js)

    def test_both_sellers_bills_carry_one_estimate_from_one_rule(self):
        """Gear is the vessel's charge and appears once on each bill, so the
        rule lives in `_fee_line` where both sides pass through it. A copy per
        caller is two rules that agree until one is edited."""
        itinerary = Itinerary(
            id="itin", name="I", operator_id="op", boat_id="boat", nights=7,
            dives=20, port_from="Hurghada", port_to="Hurghada",
            fees=[self.gear()],
            padi_fees=[fee(FeeCode.MARINE_PARK, FeeTier.MANDATORY, "90 EUR")],
            padi_fees_complete=True,
        )
        theirs = [l for l in padi_lines(itinerary, FX) if l.code is FeeCode.GEAR_RENTAL]
        ours = [l for l in itinerary_lines(itinerary, FX) if l.code is FeeCode.GEAR_RENTAL]
        self.assertEqual(len(theirs), 1)
        self.assertEqual(len(ours), 1)
        self.assertTrue(theirs[0].estimated and ours[0].estimated)
        self.assertEqual(theirs[0].display.amount, ours[0].display.amount)
