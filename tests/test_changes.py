"""What the change report must and must not claim.

Most of these pin a specific way of reporting a change that did not happen.
A report nobody trusts is worse than no report, because the whole point is to
be told when five new trips appear without having to check.
"""

from __future__ import annotations

import unittest

from liveaboard.changes import MISSING_VESSEL_MIN, compare, render


def dataset(departures, itineraries=None, boats=None, fx=None):
    return {
        "generated": "2026-08-27",
        "departures": departures,
        "itineraries": itineraries or [
            {"id": "it1", "boat_id": "alia-soul", "title": "Brothers & Daedalus",
             "name": "Brothers & Daedalus (Hurghada - Hurghada)", "fees": []}
        ],
        "boats": boats or [{"id": "alia-soul", "name": "Alia Soul"}],
        "fx": fx or {"rates": {"USD": 1.1645}},
    }


def park(amount):
    return {"code": "marine_park", "tier": "mandatory", "basis": "per_trip",
            "amount": {"amount": amount, "currency": "EUR"}}


def departure(dep_id="d1", price=1000.0, currency="USD", availability="available",
              itinerary="it1", start="2027-05-01"):
    return {
        "id": dep_id, "itinerary_id": itinerary, "start": start, "end": "2027-05-08",
        "price": {"amount": price, "currency": currency},
        "availability": availability,
    }


class TestTheThingItIsFor(unittest.TestCase):
    """Five new trips appear; say which five."""

    def test_a_new_departure_is_named(self):
        report = compare(
            dataset([departure("d1")]),
            dataset([departure("d1"), departure("d2", start="2027-05-08")]),
        )
        self.assertEqual([d.departure_id for d in report.added], ["d2"])
        self.assertEqual(report.added[0].boat, "Alia Soul")

    def test_a_price_rise_is_named_with_both_ends(self):
        report = compare(
            dataset([departure("d1", price=1000.0)]),
            dataset([departure("d1", price=1200.0)]),
        )
        move = report.price_up[0]
        self.assertEqual((move.was, move.now), (1000.0, 1200.0))
        self.assertAlmostEqual(move.pct, 20.0)
        self.assertFalse(report.price_down)

    def test_reordering_is_not_a_change(self):
        """Departures are matched on id, so a moved list entry is not news."""
        a, b = departure("d1"), departure("d2", start="2027-05-08")
        report = compare(dataset([a, b]), dataset([b, a]))
        self.assertTrue(report.is_quiet)


class TestChangesThatDidNotHappen(unittest.TestCase):
    """Every one of these was a false positive worth designing against."""

    def test_an_fx_move_is_not_a_price_change(self):
        """Fares are quoted in dollars and the site shows euro. Comparing euro
        would report every vessel on any day the ECB rate moves."""
        report = compare(
            dataset([departure("d1", price=1000.0)], fx={"rates": {"USD": 1.1645}}),
            dataset([departure("d1", price=1000.0)], fx={"rates": {"USD": 1.2000}}),
        )
        self.assertFalse(report.price_up)
        self.assertFalse(report.price_down)
        self.assertTrue(report.fx_moved)
        self.assertIn("no operator changed a price", render(report))

    def test_a_newly_read_field_is_not_126_sell_outs(self):
        """The real one. Before availability was parsed every departure held
        None; comparing that against real values reported 126 sailings as
        having just sold out when somebody had merely written a parser."""
        before = dataset([departure(f"d{i}", availability=None) for i in range(10)])
        after = dataset([
            departure(f"d{i}", availability="sold_out" if i < 6 else "available")
            for i in range(10)
        ])
        report = compare(before, after)
        self.assertTrue(report.availability_newly_read)
        self.assertEqual(report.sold_out, [])
        self.assertIn("Nobody had looked before", render(report))

    def test_a_vessel_missing_from_the_crawl_is_not_a_cancelled_season(self):
        """A page that 500s removes every departure that vessel sells.
        Reporting eighty withdrawals when a fetch failed is worse than
        reporting nothing."""
        deps = [departure(f"d{i}", start=f"2027-05-{i+1:02}")
                for i in range(MISSING_VESSEL_MIN + 2)]
        report = compare(dataset(deps), dataset([]))
        self.assertEqual(report.vessels_gone, ["Alia Soul"])
        self.assertEqual(report.withdrawn, [])
        self.assertIn("failed fetch", render(report))

    def test_a_small_vessel_selling_out_is_still_reported(self):
        """Below the threshold a boat can legitimately lose all its trips, and
        calling that a failed fetch would hide a real change."""
        deps = [departure("d1"), departure("d2", start="2027-05-08")]
        report = compare(dataset(deps), dataset([]))
        self.assertEqual(report.vessels_gone, [])
        self.assertEqual(len(report.withdrawn), 2)

    def test_a_currency_switch_is_not_a_price_move(self):
        """1000 USD against 1000 EUR is not a flat price; it is two numbers
        that cannot be compared."""
        report = compare(
            dataset([departure("d1", price=1000.0, currency="USD")]),
            dataset([departure("d1", price=1000.0, currency="EUR")]),
        )
        self.assertFalse(report.price_up)
        self.assertFalse(report.price_down)

    def test_a_first_reading_of_a_vessels_fees_is_not_a_fee_change(self):
        """A boat nobody had read yet has not raised anything.

        The same distinction the site itself makes: no fee lines means nobody
        looked, not that the trip is clean. Two silences qualify -- a vessel
        absent from the older dataset, and one present with an empty fee list.
        """
        after = dataset([departure("d1")], itineraries=[
            {"id": "it1", "boat_id": "alia-soul", "title": "T", "fees": [park(100.0)]}])

        never_read = dataset([departure("d1")], itineraries=[
            {"id": "it1", "boat_id": "alia-soul", "title": "T", "fees": []}])
        self.assertEqual(compare(never_read, after).fees, [])

        absent = dataset([departure("d1")], itineraries=[], boats=[])
        self.assertEqual(compare(absent, after).fees, [])

    def test_a_new_fee_beside_existing_ones_is_a_change(self):
        """Once a vessel has any fee line, a code appearing beside it is the
        operator listing something it did not list before."""
        before = dataset([departure("d1")], itineraries=[
            {"id": "it1", "boat_id": "alia-soul", "title": "T", "fees": [park(100.0)]}])
        after = dataset([departure("d1")], itineraries=[
            {"id": "it1", "boat_id": "alia-soul", "title": "T",
             "fees": [park(100.0), {"code": "fuel_surcharge", "tier": "mandatory",
                                    "basis": "per_trip",
                                    "amount": {"amount": 60.0, "currency": "EUR"}}]}])
        move = compare(before, after).fees[0]
        self.assertEqual((move.code, move.was), ("fuel_surcharge", "not listed"))

    def test_a_fee_that_stops_being_listed_is_a_change(self):
        before = dataset([departure("d1")], itineraries=[
            {"id": "it1", "boat_id": "alia-soul", "title": "T", "fees": [park(100.0)]}])
        after = dataset([departure("d1")], itineraries=[
            {"id": "it1", "boat_id": "alia-soul", "title": "T",
             "fees": [{"code": "gratuities", "tier": "customary", "basis": "per_trip",
                       "amount": {"amount": 80.0, "currency": "EUR"}}]}])
        codes = {(f.code, f.now) for f in compare(before, after).fees}
        self.assertIn(("marine_park", "no longer listed"), codes)


class TestSoldOutAndWithdrawnAreDifferent(unittest.TestCase):
    def test_sold_out_is_not_withdrawn(self):
        report = compare(
            dataset([departure("d1", availability="available")]),
            dataset([departure("d1", availability="sold_out")]),
        )
        self.assertEqual(len(report.sold_out), 1)
        self.assertEqual(report.withdrawn, [])

    def test_a_berth_freeing_up_is_reported(self):
        report = compare(
            dataset([departure("d1", availability="sold_out")]),
            dataset([departure("d1", availability="available")]),
        )
        self.assertEqual(len(report.returned), 1)


class TestRendering(unittest.TestCase):
    def test_a_quiet_run_says_so(self):
        report = compare(dataset([departure("d1")]), dataset([departure("d1")]))
        self.assertTrue(report.is_quiet)
        self.assertIn("nothing moved", render(report))

    def test_truncation_is_never_silent(self):
        """A capped list that does not say it is capped reads as 'that was
        everything', which is the failure this project exists to correct."""
        after = [departure(f"d{i}", start=f"2027-05-{i+1:02}") for i in range(20)]
        report = compare(dataset([]), dataset(after))
        text = render(report, limit=5)
        self.assertIn("and 15 more not shown", text)


if __name__ == "__main__":
    unittest.main()
