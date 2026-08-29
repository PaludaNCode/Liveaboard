"""Tests for merging PADI Travel's sailings onto our own.

The match is (boat, start date) and it is exact -- 601 of 892 departures find a
PADI price on the day alone, where the itinerary-title join reached a third of
that. A date has no spelling.

The regression these tests exist for is the comparison itself, and what counts
as one has changed. It used to be berth against berth, on the stated grounds
that PADI publishes no fee book. It does -- on its itinerary endpoint rather
than beside its price -- and the two books disagree far more than the berths
do: 43 of the 74 trips where both can be added up, 16 of them by over €150,
against a berth gap under five euro on 89% of the rows. Comparing berths was
comparing the half the sellers agree about.

So the rule now has two halves and both are load-bearing. A total may be set
against a total, never against a berth price. And a PADI total exists only
where PADI's own disclosure is complete: a bill built from part of a fee book
is the exact thing this site exists to catch other people doing.
"""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from fetch_padi import _departure_book, _iso_day  # noqa: E402
from liveaboard.dataset import Dataset  # noqa: E402
from liveaboard.promote import promote  # noqa: E402
from liveaboard.render import build_payload  # noqa: E402

from test_promote import SEASON, candidate, departure  # noqa: E402

BOOK = {
    "collected": "2026-08-28",
    "departures": {
        "alia-soul::2027-05-01": {
            "boat": "alia-soul", "start": "2027-05-01", "end": "2027-05-08",
            "nights": 7, "price": 1300.0, "currency": "USD",
            "availability": 0, "padi_id": 1, "itinerary": "Brothers 7 Nights",
        }
    },
}


class TestDateNormalisation(unittest.TestCase):
    """PADI dates are midnight-Z timestamps; a sailing is a day."""

    def test_timestamp_becomes_a_day(self) -> None:
        self.assertEqual(_iso_day("2027-05-01T00:00:00Z"), "2027-05-01")

    def test_truncated_not_parsed(self) -> None:
        """Parsing into a local timezone moves a midnight-UTC sailing back a day
        for every reader west of Greenwich. Truncation cannot."""
        self.assertEqual(_iso_day("2027-05-01T00:00:00Z"), "2027-05-01")
        self.assertEqual(_iso_day("2027-01-01T00:00:00Z"), "2027-01-01")

    def test_nonsense_is_none(self) -> None:
        for value in (None, "", "soon", "2027-13"):
            self.assertIsNone(_iso_day(value), value)


class TestDepartureBook(unittest.TestCase):
    """A price with no unit is an invented price."""

    RAW = {
        "shops": {"a-boat": {"boat": "alia-soul", "currency": "EUR", "country": "egypt"}},
        "trips": {"a-boat": [
            {"startDate": "2027-05-01T00:00:00Z", "endDate": "2027-05-08T00:00:00Z",
             "price": 1300.0, "duration": 7, "id": 1},
            {"startDate": "2027-05-08T00:00:00Z", "price": None, "id": 2},
            {"startDate": "2027-05-15T00:00:00Z", "price": 0, "id": 3},
            {"startDate": None, "price": 900.0, "id": 4},
        ]},
    }

    def test_only_priced_and_dated_sailings_are_kept(self) -> None:
        book = _departure_book({"alia-soul": "a-boat"}, self.RAW)
        self.assertEqual(list(book), ["alia-soul::2027-05-01"])

    def test_a_zero_price_is_not_a_price(self) -> None:
        book = _departure_book({"alia-soul": "a-boat"}, self.RAW)
        self.assertNotIn("alia-soul::2027-05-15", book)

    def test_a_vessel_with_no_stated_currency_is_dropped_whole(self) -> None:
        """Its prices are real numbers in an unknown unit, which is worse than
        no number: it would convert as though it were euros."""
        raw = {**self.RAW, "shops": {"a-boat": {"boat": "alia-soul", "currency": None}}}
        self.assertEqual(_departure_book({"alia-soul": "a-boat"}, raw), {})


class TestMerge(unittest.TestCase):
    """One row per sailing. A second seller must not create one."""

    def payload(self, book=BOOK):
        return promote(candidate([departure()]), season=SEASON, padi_departures=book)

    def test_a_matched_sailing_gets_padi_s_price(self) -> None:
        dep = self.payload()["departures"][0]
        self.assertEqual(dep["padi_price"], {"amount": 1300.0, "currency": "USD"})

    def test_the_price_carries_its_provenance(self) -> None:
        """Never a bare number: every price on this site says where it came from."""
        dep = self.payload()["departures"][0]
        self.assertEqual(dep["padi_provenance"]["source_id"], "padi.com")
        self.assertEqual(dep["padi_provenance"]["retrieved"], "2026-08-28")

    def test_an_unmatched_sailing_gets_nothing(self) -> None:
        """Not zero. A berth nobody offered has no price, and zero reads free."""
        payload = promote(
            candidate([departure(start="2027-06-05", end="2027-06-12")]),
            season=SEASON, padi_departures=BOOK,
        )
        self.assertNotIn("padi_price", payload["departures"][0])

    def test_the_row_count_is_ours_alone(self) -> None:
        """PADI sells 2,797 sailings we do not carry. None of them is a row."""
        extra = {"departures": {**BOOK["departures"],
                                "alia-soul::2027-07-04": {
                                    "boat": "alia-soul", "start": "2027-07-04",
                                    "price": 999.0, "currency": "EUR"}}}
        payload = promote(candidate([departure()]), season=SEASON, padi_departures=extra)
        self.assertEqual(len(payload["departures"]), 1)

    def test_no_book_changes_nothing(self) -> None:
        without = promote(candidate([departure()]), season=SEASON)
        self.assertNotIn("padi_price", without["departures"][0])


class TestComparison(unittest.TestCase):
    """The regression that would otherwise ship silently."""

    FEES = {"vessels": {"alia-soul": {"fees": [
        {"code": "marine_park", "label": "Marine park fees", "tier": "mandatory",
         "amount": {"amount": 150.0, "currency": "USD"}, "basis": "per_trip",
         "included": False,
         "provenance": {"kind": "scraped", "source_id": "liveaboard.com",
                        "retrieved": "2026-08-27"}},
    ]}}}

    #: PADI's book for the same trip, complete: one charge, named and priced.
    PADI = {
        "collected": "2026-08-28",
        "trips": {
            "alia-soul::brothers-7-nights": {
                "boat": "alia-soul",
                # The join is `promote.padi_key`: PADI's title minus its
                # night suffix is our itinerary's name.
                "name": "Brothers, Daedalus & Elphinstone",
                "fees": {
                    "complete": True,
                    "unreadable": [],
                    "lines": [{
                        "code": "marine_park", "tier": "mandatory",
                        "basis": "per_trip", "note": "National park fees",
                        "amount": {"amount": 60.0, "currency": "USD"},
                    }],
                },
            }
        },
    }

    def padi_book(self, **fees) -> dict:
        """The book above with its fee block overridden."""
        trip = dict(next(iter(self.PADI["trips"].values())))
        trip["fees"] = {**trip["fees"], **fees}
        return {**self.PADI, "trips": {"alia-soul::brothers-7-nights": trip}}

    def page(self, padi: dict | None) -> dict:
        payload = promote(candidate([departure()]), season=SEASON,
                          fees=self.FEES, padi_departures=BOOK, padi=padi)
        return build_payload(Dataset.from_dict(payload))

    def test_a_complete_padi_book_reaches_the_page_as_lines(self) -> None:
        """Lines, not a number: the browser adds PADI's bill up with the same
        code it adds ours up with, so the visitor's toggles reach both sides and
        a difference can never be an artefact of two adders."""
        page = self.page(self.padi_book())
        itinerary = next(iter(page["itineraries"].values()))
        self.assertIn("padi_lines", itinerary)
        codes = [line["code"] for line in itinerary["padi_lines"]]
        self.assertIn("marine_park", codes)
        self.assertIn("base", [line["tier"] for line in [page["departures"][0]["padi_base_line"]]])

    def test_the_shared_on_board_extras_are_on_both_sides(self) -> None:
        """Nitrox and rental gear are the vessel's charge, billed on board to
        whoever walks up the gangway, so they belong to both bills.

        Left off PADI's side, its total would be short by whatever the visitor
        has switched on -- and both switches start on -- so PADI would win every
        row for a reason that has nothing to do with PADI.
        """
        fees = {"vessels": {"alia-soul": {"fees": [
            *self.FEES["vessels"]["alia-soul"]["fees"],
            {"code": "gear_rental", "label": "Equipment rental",
             "tier": "conditional", "basis": "per_trip", "included": False,
             "amount": {"amount": 200.0, "currency": "USD"},
             "provenance": {"kind": "scraped", "source_id": "liveaboard.com",
                            "retrieved": "2026-08-27"}},
        ]}}}
        payload = promote(candidate([departure()]), season=SEASON, fees=fees,
                          padi_departures=BOOK, padi=self.padi_book())
        page = build_payload(Dataset.from_dict(payload))
        itinerary = next(iter(page["itineraries"].values()))
        codes = [line["code"] for line in itinerary["padi_lines"]]
        self.assertIn("gear_rental", codes)
        # And exactly once -- a shared line duplicated is a fee charged twice.
        self.assertEqual(codes.count("gear_rental"), 1)

    def test_an_incomplete_padi_book_yields_no_second_total(self) -> None:
        """The invariant, in the one place it can be broken silently.

        An unpriced charge, or one whose name this project cannot resolve, means
        the bill does not add up -- and a total built from part of a disclosure
        would show PADI cheaper by whatever it left out, which is the failure
        this site reports in operators.
        """
        for label, book in (
            ("unpriced", self.padi_book(complete=False)),
            ("unreadable", self.padi_book(complete=False, unreadable=["Local Fees"])),
        ):
            with self.subTest(label):
                page = self.page(book)
                itinerary = next(iter(page["itineraries"].values()))
                self.assertNotIn("padi_lines", itinerary)
                # The berth price survives; it is the *total* that is withheld.
                self.assertIn("padi", page["departures"][0])

    def test_no_padi_book_leaves_the_berth_price_alone(self) -> None:
        """432 rows are this: a second price, no second bill."""
        page = self.page(None)
        self.assertNotIn("padi_lines", next(iter(page["itineraries"].values())))
        self.assertIn("padi", page["departures"][0])

    def test_nothing_is_emitted_where_padi_is_silent(self) -> None:
        payload = promote(candidate([departure(start="2027-06-05", end="2027-06-12")]),
                          season=SEASON, padi_departures=BOOK)
        row = build_payload(Dataset.from_dict(payload))["departures"][0]
        self.assertNotIn("padi", row)
        self.assertNotIn("padi_base_line", row)

    def test_the_difference_needs_one_currency(self) -> None:
        """Ours in USD, PADI's in EUR: the model refuses rather than subtracting."""
        payload = promote(candidate([departure(currency="USD")]), season=SEASON,
                          padi_departures={"collected": "2026-08-28", "departures": {
                              "alia-soul::2027-05-01": {
                                  "boat": "alia-soul", "start": "2027-05-01",
                                  "price": 1300.0, "currency": "EUR"}}})
        dep = Dataset.from_dict(payload).departures[0]
        self.assertIsNone(dep.padi_difference)


if __name__ == "__main__":
    unittest.main()
