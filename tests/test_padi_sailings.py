"""Tests for merging PADI Travel's sailings onto our own.

The match is (boat, start date) and it is exact -- 601 of 892 departures find a
PADI price on the day alone, where the itinerary-title join reached a third of
that. A date has no spelling.

The regression these tests exist for is the comparison itself. Our ``total``
adds fees; PADI publishes none. Measuring PADI's berth price against our total
would show it cheaper by exactly the fees it never disclosed, on a site whose
whole argument is that undisclosed fees are the problem.
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

    def test_the_delta_is_berth_against_berth(self) -> None:
        """Ours advertises 1450 and carries 150 of fees; PADI advertises 1300.

        The difference is -150 against the berth price. Measured against the
        total it would read as -300 and PADI would look twice as cheap, entirely
        because it publishes no fees.
        """
        payload = promote(candidate([departure()]), season=SEASON,
                          fees=self.FEES, padi_departures=BOOK)
        page = build_payload(Dataset.from_dict(payload))
        row = page["departures"][0]
        self.assertAlmostEqual(row["padi_delta"], row["padi"] - row["base"], places=2)
        self.assertLess(row["base"], row["padi"] + 200)  # sanity: same order
        self.assertNotEqual(row["padi_delta"], row["padi"] - row["base"] - 150)

    def test_nothing_is_emitted_where_padi_is_silent(self) -> None:
        payload = promote(candidate([departure(start="2027-06-05", end="2027-06-12")]),
                          season=SEASON, padi_departures=BOOK)
        row = build_payload(Dataset.from_dict(payload))["departures"][0]
        self.assertNotIn("padi", row)
        self.assertNotIn("padi_delta", row)

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
