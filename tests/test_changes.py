"""What the change report must and must not claim.

Most of these pin a specific way of reporting a change that did not happen.
A report nobody trusts is worse than no report, because the whole point is to
be told when five new trips appear without having to check.
"""

from __future__ import annotations

import unittest

from liveaboard.changes import (
    MISSING_VESSEL_MIN, MIN_MOVE, as_dict, compare, headline, render,
)


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
              itinerary="it1", start="2027-05-01", seller="liveaboard.com",
              also_padi=False):
    row = {
        "id": dep_id, "itinerary_id": itinerary, "start": start, "end": "2027-05-08",
        "price": {"amount": price, "currency": currency},
        "availability": availability,
        "provenance": {"kind": "scraped", "source_id": seller},
    }
    if also_padi:
        row["padi_provenance"] = {"kind": "scraped", "source_id": "padi.com"}
    return row


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

    def test_a_month_that_went_unread_is_not_five_withdrawals(self):
        """The real one, reported by the site's owner. A vessel page is fetched
        once per season month, so one unreadable response empties that boat's
        month while the other three come back fine -- and the vessel-level
        guard never fires, because the boat did not lose everything.

        DUNE Longara's five May sailings were reported as withdrawn while
        liveaboard.com was still selling every one of them."""
        may = [departure(f"m{i}", start=f"2027-05-{i+1:02}") for i in range(5)]
        june = [departure(f"j{i}", start=f"2027-06-{i+1:02}") for i in range(4)]
        report = compare(dataset(may + june), dataset(june))
        self.assertEqual(report.months_gone, ["Alia Soul 2027-05"])
        self.assertEqual(report.withdrawn, [])
        self.assertIn("came back unreadable", render(report))

    def test_a_month_a_vessel_barely_sold_is_still_withdrawn(self):
        """Below the threshold a boat can legitimately lose its only sailing
        that month, and calling that an unread page would hide a real change."""
        may = [departure("m0", start="2027-05-01")]
        june = [departure(f"j{i}", start=f"2027-06-{i+1:02}") for i in range(4)]
        report = compare(dataset(may + june), dataset(june))
        self.assertEqual(report.months_gone, [])
        self.assertEqual(len(report.withdrawn), 1)

    def test_a_vessel_losing_everything_is_reported_once_not_twice(self):
        """Both guards match when a boat vanishes entirely. It is one event."""
        deps = [departure(f"d{i}", start=f"2027-05-{i+1:02}")
                for i in range(MISSING_VESSEL_MIN + 1)]
        report = compare(dataset(deps), dataset([]))
        self.assertEqual(report.vessels_gone, ["Alia Soul"])
        self.assertEqual(report.months_gone, [])

    def test_a_rate_nothing_is_priced_in_is_not_reported(self):
        """The FX table carries every rate the feed publishes. GBP is in it and
        no vessel here quotes GBP, but it sorted first -- so a GBP wobble was
        reported as the reason every euro figure on the page had moved."""
        report = compare(
            dataset([departure("d1", currency="USD")],
                    fx={"rates": {"GBP": 1.166317, "USD": 0.858885}}),
            dataset([departure("d1", currency="USD")],
                    fx={"rates": {"GBP": 1.900000, "USD": 0.858885}}),
        )
        self.assertFalse(report.fx_moved)
        self.assertNotIn("GBP", render(report))

    def test_the_rate_the_fares_are_quoted_in_is_reported(self):
        report = compare(
            dataset([departure("d1", currency="USD")],
                    fx={"rates": {"GBP": 1.166317, "USD": 0.858885}}),
            dataset([departure("d1", currency="USD")],
                    fx={"rates": {"GBP": 1.166317, "USD": 0.900000}}),
        )
        self.assertEqual([m.currency for m in report.fx], ["USD"])

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


class TestASailingThatOnlyLooksNew(unittest.TestCase):
    """The Blue report: twelve weeks listed as new and withdrawn at once.

    A departure id is identity, and two things move it under a sailing nobody
    withdrew. The seller: liveaboard.com's rows are `blue-2027-05-06-0` and a
    sailing PADI alone lists is `blue-2027-05-06-padi`, so the day the first
    seller starts listing a week the second was carrying, one id leaves and
    another arrives. And a sibling: the suffix is the Event node's position on
    the vessel-month page, so one sailing inserted earlier renumbers every
    later one.

    Published as two events that was 24 lines of news for a fleet that did
    nothing — and with the fares side by side, 1,645 USD against 1,420 EUR
    read as a €225 cut rather than as the other seller's currency.
    """

    def relist(self, before_id, after_id, **after):
        old = departure(before_id, price=1420.0, currency="EUR",
                        seller="padi.com")
        new = departure(after_id, price=1420.0, currency="EUR", **after)
        return compare(dataset([old]), dataset([new]))

    def test_the_same_sailing_from_the_other_seller_is_not_news_twice(self):
        report = self.relist("blue-2027-05-06-padi", "blue-2027-05-06-0",
                             seller="liveaboard.com", also_padi=True)
        self.assertEqual([], report.added)
        self.assertEqual([], report.withdrawn)
        row = report.relisted[0]
        self.assertEqual(("padi.com",), row.was_sellers)
        self.assertEqual(("liveaboard.com", "padi.com"), row.sellers)
        self.assertTrue(row.sellers_moved)

    def test_a_renumbered_sibling_is_not_news_at_all(self):
        """Same seller, same trip, a suffix that moved because a sailing
        earlier on the page did. Nothing about this week changed."""
        report = compare(
            dataset([departure("blue-2027-08-12-0")]),
            dataset([departure("blue-2027-08-12-1")]),
        )
        self.assertEqual([], report.added)
        self.assertEqual([], report.withdrawn)
        self.assertEqual(1, len(report.relisted))
        self.assertFalse(report.relisted[0].sellers_moved)

    def test_the_currency_the_seller_switched_to_is_not_a_price_move(self):
        """1,645 USD -> 1,420 EUR on one week of Blue's. The two figures are
        printed, and `repriced` refuses to call the difference a fare move —
        the same rule the price blocks keep."""
        report = compare(
            dataset([departure("blue-2027-08-12-0", price=1645.0, currency="USD")]),
            dataset([departure("blue-2027-08-12-1", price=1420.0, currency="EUR")]),
        )
        row = report.relisted[0]
        self.assertEqual((1645.0, "USD"), (row.was_price, row.was_currency))
        self.assertEqual((1420.0, "EUR"), (row.price, row.currency))
        self.assertFalse(row.repriced)
        self.assertEqual([], report.price_up + report.price_down)

    def test_a_boat_that_swapped_one_trip_for_another_still_reports_both(self):
        """The thing this must never swallow: same boat, same date, a
        different trip and the same seller. That is a withdrawal and an
        arrival, and it stays two events."""
        report = compare(
            dataset([departure("blue-2027-05-06-0", itinerary="it1")]),
            dataset([departure("blue-2027-05-06-1", itinerary="it2")],
                    itineraries=[
                        {"id": "it1", "boat_id": "alia-soul", "title": "Brothers",
                         "name": "Brothers (Hurghada - Hurghada)", "fees": []},
                        {"id": "it2", "boat_id": "alia-soul", "title": "St. John's",
                         "name": "St. John's (Hurghada - Hurghada)", "fees": []}]),
        )
        self.assertEqual(1, len(report.added))
        self.assertEqual(1, len(report.withdrawn))
        self.assertEqual([], report.relisted)

    def test_two_rows_on_one_day_are_left_alone(self):
        """`padi_key`'s rule: fold only where the key names exactly one. A boat
        with two sailings starting the same day is a pairing nothing here can
        make."""
        old = [departure("a-0", itinerary="it1"), departure("a-1", itinerary="it1")]
        new = [departure("a-2", itinerary="it1"), departure("a-3", itinerary="it1")]
        report = compare(dataset(old), dataset(new))
        self.assertEqual([], report.relisted)
        self.assertEqual(2, len(report.added))
        self.assertEqual(2, len(report.withdrawn))

    def test_it_reaches_both_shapes_of_the_report(self):
        report = self.relist("blue-2027-05-06-padi", "blue-2027-05-06-0",
                             seller="liveaboard.com", also_padi=True)
        row = as_dict(report)["relisted"][0]
        self.assertEqual(["padi.com"], row["was_sellers"])
        self.assertEqual(["liveaboard.com", "padi.com"], row["sellers"])
        text = render(report)
        self.assertIn("re-listed", text)
        self.assertIn("padi.com -> liveaboard.com+padi.com", text)
        self.assertNotIn("new departures", text)
        self.assertNotIn("withdrawn", text)


class TestEveryEventNamesItsSeller(unittest.TestCase):
    """Two sellers, and a report that named neither.

    An arrival because PADI started listing a sailing is a different fact from
    one liveaboard.com added, and it was published as the same line. The host
    comes off the departure's own provenance — the pair the Seller column
    prints — rather than being worked out a second time.
    """

    def test_a_new_departure_says_who_published_it(self):
        report = compare(
            dataset([departure("d1")]),
            dataset([departure("d1"),
                     departure("d2", start="2027-05-08", seller="padi.com")]),
        )
        self.assertEqual(("padi.com",), report.added[0].sellers)

    def test_both_sellers_are_named_where_both_list_it(self):
        report = compare(
            dataset([departure("d1")]),
            dataset([departure("d1"),
                     departure("d2", start="2027-05-08", also_padi=True)]),
        )
        self.assertEqual(("liveaboard.com", "padi.com"), report.added[0].sellers)

    def test_it_reaches_the_page_and_the_log(self):
        report = compare(
            dataset([departure("d1")]),
            dataset([departure("d1"), departure("d2", start="2027-05-08")]),
        )
        self.assertEqual(["liveaboard.com"], as_dict(report)["added"][0]["sellers"])
        self.assertIn("liveaboard.com", render(report))


class TestRoundingIsNotARepricing(unittest.TestCase):
    """The real one, again. On 2026-08-28 every one of 174 "price changes"
    was exactly -1 on a four-figure fare: the source had re-rounded. Left in,
    they filled both price blocks and pushed the real moves past the cap."""

    def test_a_one_unit_move_is_not_listed(self):
        report = compare(
            dataset([departure("d1", price=1469.0)]),
            dataset([departure("d1", price=1468.0)]),
        )
        self.assertFalse(report.price_down)
        self.assertEqual(report.price_rounding, 1)

    def test_it_is_counted_and_said_out_loud(self):
        """Suppressed, never silently dropped."""
        before = dataset([departure(f"d{i}", price=1000.0) for i in range(3)])
        after = dataset([departure(f"d{i}", price=999.0) for i in range(3)]
                        + [departure("new", start="2027-06-05")])
        text = render(compare(before, after))
        self.assertIn("3 further fare(s) moved by less than", text)

    def test_the_report_shows_what_it_was_and_what_it_is(self):
        """"It went up" is not the answer to "by how much, from what?"."""
        text = render(compare(
            dataset([departure("d1", price=2400.0)]),
            dataset([departure("d1", price=2560.0)]),
        ))
        self.assertIn("2,400 ->   2,560 USD", text)
        self.assertIn("+160", text)
        # A decimal, because +6.7% printed as +7% is a different claim.
        self.assertIn("+6.7%", text)

    def test_a_real_move_still_gets_through(self):
        report = compare(
            dataset([departure("d1", price=1000.0)]),
            dataset([departure("d1", price=1000.0 + MIN_MOVE)]),
        )
        self.assertEqual(len(report.price_up), 1)
        self.assertEqual(report.price_rounding, 0)

    def test_rounding_alone_is_still_a_quiet_run(self):
        report = compare(
            dataset([departure("d1", price=1000.0)]),
            dataset([departure("d1", price=999.0)]),
        )
        self.assertTrue(report.is_quiet)
        self.assertIn("shifting by under", render(report))


class TestHeadline(unittest.TestCase):
    """One line for the commit subject, so `git log` is the changelog."""

    def test_a_quiet_run_says_nothing_changed(self):
        report = compare(dataset([departure("d1")]), dataset([departure("d1")]))
        self.assertEqual(headline(report), "no change to trips, prices or availability")

    def test_a_missing_vessel_outranks_everything_else(self):
        """It means a fetch broke, and it is the one thing worth interrupting
        a reader for -- so it must not be buried behind eighty withdrawals."""
        deps = [departure(f"d{i}", start=f"2027-05-{i+1:02}")
                for i in range(MISSING_VESSEL_MIN + 2)]
        line = headline(compare(dataset(deps), dataset([])))
        self.assertIn("lost every departure", line)

    def test_new_departures_are_counted(self):
        report = compare(
            dataset([departure("d1")]),
            dataset([departure("d1"), departure("d2", start="2027-05-08")]),
        )
        self.assertIn("1 new departures", headline(report))

    def test_a_price_move_names_the_biggest(self):
        report = compare(
            dataset([departure("d1", price=1000.0)]),
            dataset([departure("d1", price=1200.0)]),
        )
        line = headline(report)
        # Both ends, not just the delta: "what was it before" is the first
        # thing anyone asks of a price change.
        self.assertIn("1,000 -> 1,200 USD", line)
        self.assertIn("Alia Soul", line)

    def test_a_fee_change_names_the_vessel(self):
        """The weekly fee run re-promotes the same candidate, so a fee change
        is the only thing it can report. Counting them alone would make every
        one of those commit subjects identical."""
        before = dataset([departure("d1")], itineraries=[
            {"id": "it1", "boat_id": "alia-soul", "title": "T", "fees": [park(100.0)]}])
        after = dataset([departure("d1")], itineraries=[
            {"id": "it1", "boat_id": "alia-soul", "title": "T", "fees": [park(140.0)]}])
        line = headline(compare(before, after))
        self.assertIn("Alia Soul", line)
        self.assertIn("marine_park", line)

    def test_it_is_always_one_line(self):
        """It becomes a commit subject; a newline would split the message."""
        before = dataset([departure(f"d{i}", price=1000.0) for i in range(8)])
        after = dataset([departure(f"d{i}", price=1000.0 + i * 50) for i in range(8)]
                        + [departure("new", start="2027-06-05")])
        for report in (compare(before, after), compare(after, before)):
            self.assertNotIn("\n", headline(report))


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
