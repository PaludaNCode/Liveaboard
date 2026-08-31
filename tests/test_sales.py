"""Tests for the other seller's change log: deriving it, and diffing two days.

PADI publishes a deals *listing* and `data/deals.json` keeps a day per reading
of it, so `promote` can say what moved. liveaboard.com publishes no listing at
all -- it strikes the list price through beside every discounted cabin -- and
`data/cabins.json` is rewritten whole each run, so the larger of the two
signals could say what is on sale today and nothing about what changed. On the
day the Red Sea Aggressors' 33% sale ended, the page reported *three offers
withdrawn* from PADI's exemplar sailings for an event that moved 36 sailings.

`tools/derive_sales.py` writes the second committed day. Three properties, and
each is a mistake this codebase has made somewhere before:

**A day is filed by the record's own collected date, never the book's.** A
capped `fetch_cabins.py --limit N` run merges, so most of the file is older
than its header; taking the whole file would report a week-old price as this
morning's.

**A census, not a list of sales.** Every sailing read that day is in the book,
discounted or not, because the keys are the only thing separating *not on sale*
from *not looked at* -- the same distinction as `not_looked_at`, `carry_unread`
and the deals book's `partial`.

**The cheapest rung against its own list price.** The advertised price is the
bottom of the ladder on 864 of 864 sailings; setting it against the *dearest*
room's list price reports a 33% sale as 40%.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import published  # noqa: E402
from derive_sales import prune, sailings_read_on  # noqa: E402
from liveaboard.dataset import Dataset  # noqa: E402
from liveaboard.promote import promote  # noqa: E402
from liveaboard.render import build_payload  # noqa: E402

from test_promote import SEASON, candidate, departure  # noqa: E402

FLEET = candidate(
    [departure(), departure(boat="serenity", name="Northern Wrecks")],
    itineraries=[
        {"id": "alia-soul", "name": "Alia Soul", "boat": "Alia Soul"},
        {"id": "serenity", "name": "Serenity", "boat": "Serenity"},
    ],
)
"""Two boats, so a move can be attributed to one of them rather than to "the
fleet" -- a single-boat fixture lets every grouping test pass for the wrong
reason."""


def record(boat="alia-soul", start="2027-05-01", collected="2026-08-29",
           cabins=(("Twin", 900.0, 1000.0),), currency="USD") -> dict:
    """One booking page's ladder, as `fetch_cabins.py` records it."""
    return {
        "boat": boat, "start": start, "collected": collected, "currency": currency,
        "cabins": [{"name": n, "price": p, "list_price": lp} for n, p, lp in cabins],
    }


def cabins(*records, collected="2026-08-29") -> dict:
    return {"collected": collected,
            "departures": {f"{r['boat']}::{r['start']}": r for r in records}}


def day(*rows) -> dict:
    """One day of the sale book: ``key -> [price, list price or None, currency]``."""
    sailings = {key: list(value) for key, value in rows}
    return {
        "read": len(sailings),
        "on_sale": sum(1 for v in sailings.values() if v[1] is not None),
        "sailings": sailings,
    }


def sale_book(days: dict) -> dict:
    return {"source": "liveaboard.com", "collected": max(days) if days else "", "days": days}


def on_sale(price=900.0, was=1000.0, currency="USD") -> tuple:
    return [price, was, currency]


class TestDerivingADay(unittest.TestCase):
    def test_a_sailing_is_filed_under_the_day_its_own_page_was_read(self):
        """Not under the book's header. A capped run merges into the book, so
        most of the file carries an older date than the header does."""
        book = cabins(record(start="2027-05-01", collected="2026-08-29"),
                      record(start="2027-05-08", collected="2026-08-22"))
        self.assertEqual(sorted(sailings_read_on(book["departures"], "2026-08-29")),
                         ["alia-soul::2027-05-01"])
        self.assertEqual(sorted(sailings_read_on(book["departures"], "2026-08-22")),
                         ["alia-soul::2027-05-08"])

    def test_an_undiscounted_sailing_is_still_in_the_census(self):
        """The keys are what says a page was read. A book of sales alone could
        not tell "came off sale" from "was not looked at"."""
        book = cabins(record(cabins=(("Twin", 1000.0, None),)))
        read = sailings_read_on(book["departures"], "2026-08-29")
        self.assertEqual(read["alia-soul::2027-05-01"], [1000.0, None, "USD"])

    def test_the_cheapest_rung_is_set_against_its_own_list_price(self):
        """Red Sea Aggressor II's ladder. The dearest room's list price would
        report its 33% sale as 40%."""
        book = cabins(record(cabins=(
            ("Deluxe", 1849.0, 2760.0), ("Master", 1983.0, 2960.0),
            ("Suite", 2050.0, 3060.0))))
        self.assertEqual(book and sailings_read_on(book["departures"], "2026-08-29")
                         ["alia-soul::2027-05-01"], [1849.0, 2760.0, "USD"])

    def test_a_list_price_at_or_below_the_price_is_not_a_discount(self):
        book = cabins(record(cabins=(("Twin", 1000.0, 1000.0),)))
        self.assertIsNone(sailings_read_on(book["departures"], "2026-08-29")
                          ["alia-soul::2027-05-01"][1])

    def test_a_ladder_with_no_readable_price_states_nothing_at_all(self):
        """Absent rather than "read, not on sale". Recording it as the second
        would let tomorrow's reading report a sale starting on a page nobody
        could price today."""
        book = cabins(record(cabins=()), record(start="2027-05-08"))
        self.assertEqual(sorted(sailings_read_on(book["departures"], "2026-08-29")),
                         ["alia-soul::2027-05-08"])

    def test_the_book_keeps_the_most_recent_readings(self):
        days = {f"2026-08-{n:02d}": day() for n in range(20, 30)}
        self.assertEqual(sorted(prune(days, 3)),
                         ["2026-08-27", "2026-08-28", "2026-08-29"])


class TestTheChangeLog(unittest.TestCase):
    """Two committed days, diffed the way `_deals_block` diffs the deals book."""

    def _moved(self, before: dict, after: dict) -> dict:
        payload = promote(
            FLEET, season=SEASON,
            cabins=cabins(record()),
            sales=sale_book({"2026-08-28": before, "2026-08-29": after}),
        )
        return payload["deals"]["on_sale_changes"]

    def test_a_first_reading_says_so_rather_than_reporting_no_changes(self):
        payload = promote(FLEET, season=SEASON, cabins=cabins(record()),
                          sales=sale_book({"2026-08-29": day(
                              ("alia-soul::2027-05-01", on_sale()))}))
        block = payload["deals"]["on_sale_changes"]
        self.assertTrue(block["first_reading"])
        self.assertNotIn("moves", block)

    def test_a_sale_that_started_is_reported_with_its_rate(self):
        moved = self._moved(
            day(("alia-soul::2027-05-01", [1000.0, None, "USD"])),
            day(("alia-soul::2027-05-01", on_sale(900.0, 1000.0))),
        )
        self.assertEqual([(m["boat"], m["kind"], m["sailings"], m["pct"])
                          for m in moved["moves"]],
                         [("alia-soul", "started", 1, 10)])

    def test_a_sale_that_ended_is_reported_at_the_rate_it_ran_at(self):
        """The event this feature exists for. A discount that has gone is
        described by what came off the price while it ran, not by a rate the
        sailing no longer has."""
        moved = self._moved(
            day(("alia-soul::2027-05-01", on_sale(1588.0, 2371.0))),
            day(("alia-soul::2027-05-01", [2371.0, None, "USD"])),
        )
        self.assertEqual([(m["kind"], m["sailings"], m["pct"]) for m in moved["moves"]],
                         [("ended", 1, 33)])

    def test_a_rate_that_moved_carries_both_figures(self):
        moved = self._moved(
            day(("alia-soul::2027-05-01", on_sale(900.0, 1000.0))),
            day(("alia-soul::2027-05-01", on_sale(850.0, 1000.0))),
        )
        row = moved["moves"][0]
        self.assertEqual((row["kind"], row["was_pct"], row["pct"]), ("changed", 10, 15))

    def test_a_price_that_moved_at_the_same_rate_is_not_a_change_of_discount(self):
        """A whole ladder re-priced 10% off to 10% off is the operator moving
        its fare, which the price columns already report. Calling it a change
        of discount would put a line in the sale log for a sale that did not
        move."""
        moved = self._moved(
            day(("alia-soul::2027-05-01", on_sale(900.0, 1000.0))),
            day(("alia-soul::2027-05-01", on_sale(1800.0, 2000.0))),
        )
        self.assertEqual(moved["moves"], [])

    def test_a_boat_s_sailings_are_one_line_with_a_count(self):
        """36 identical lines say less than one line saying 36 — which is the
        whole complaint against reporting this from PADI's exemplar sailings."""
        moved = self._moved(
            day(*[(f"alia-soul::2027-05-{n:02d}", on_sale(1588.0, 2371.0))
                  for n in (1, 8, 15)]),
            day(*[(f"alia-soul::2027-05-{n:02d}", [2371.0, None, "USD"])
                  for n in (1, 8, 15)]),
        )
        self.assertEqual(len(moved["moves"]), 1)
        row = moved["moves"][0]
        self.assertEqual((row["sailings"], row["first"], row["last"]),
                         (3, "2027-05-01", "2027-05-15"))

    def test_two_boats_moving_are_two_lines_ordered_by_the_name_shown(self):
        moved = self._moved(
            day(("serenity::2027-05-01", [1000.0, None, "USD"]),
                ("alia-soul::2027-05-01", [1000.0, None, "USD"])),
            day(("serenity::2027-05-01", on_sale(900.0, 1000.0)),
                ("alia-soul::2027-05-01", on_sale(800.0, 1000.0))),
        )
        self.assertEqual([m["boat_name"] for m in moved["moves"]],
                         ["Alia Soul", "Serenity"])

    def test_a_range_is_printed_only_where_the_boat_runs_more_than_one_rate(self):
        moved = self._moved(
            day(("alia-soul::2027-05-01", [1000.0, None, "USD"]),
                ("alia-soul::2027-05-08", [1000.0, None, "USD"])),
            day(("alia-soul::2027-05-01", on_sale(900.0, 1000.0)),
                ("alia-soul::2027-05-08", on_sale(800.0, 1000.0))),
        )
        row = moved["moves"][0]
        self.assertEqual((row["pct"], row["pct_max"]), (10, 20))

    def test_nothing_moving_is_an_empty_list_rather_than_a_missing_one(self):
        moved = self._moved(
            day(("alia-soul::2027-05-01", on_sale())),
            day(("alia-soul::2027-05-01", on_sale())),
        )
        self.assertEqual(moved["moves"], [])
        self.assertEqual(moved["compared"], 1)


class TestASailingOnlyOneReadingCovered(unittest.TestCase):
    """The rule the rest of this pipeline is built out of, arriving here.

    A booking page that was not read states nothing about its sailing. It has
    not come off sale, it has not been looked at — and the count of such
    sailings is printed rather than quietly excluded, because a change list
    that narrows its own scope in silence reads as "that was everything".
    """

    def _moved(self, before, after):
        payload = promote(FLEET, season=SEASON, cabins=cabins(record()),
                          sales=sale_book({"2026-08-28": before, "2026-08-29": after}))
        return payload["deals"]["on_sale_changes"]

    def test_a_sailing_missing_from_today_is_not_a_sale_that_ended(self):
        moved = self._moved(
            day(("alia-soul::2027-05-01", on_sale()),
                ("alia-soul::2027-05-08", on_sale())),
            day(("alia-soul::2027-05-01", on_sale())),
        )
        self.assertEqual(moved["moves"], [])
        self.assertEqual((moved["compared"], moved["not_compared"]), (1, 1))

    def test_a_sailing_missing_from_yesterday_is_not_a_sale_that_started(self):
        moved = self._moved(
            day(("alia-soul::2027-05-01", on_sale())),
            day(("alia-soul::2027-05-01", on_sale()),
                ("alia-soul::2027-05-08", on_sale())),
        )
        self.assertEqual(moved["moves"], [])
        self.assertEqual((moved["compared"], moved["not_compared"]), (1, 1))

    def test_a_sailing_on_a_boat_this_site_does_not_carry_is_counted_apart(self):
        moved = self._moved(
            day(("alia-soul::2027-05-01", on_sale()),
                ("some-other-boat::2027-05-01", on_sale())),
            day(("alia-soul::2027-05-01", [1000.0, None, "USD"]),
                ("some-other-boat::2027-05-01", [1000.0, None, "USD"])),
        )
        self.assertEqual([m["boat"] for m in moved["moves"]], ["alia-soul"])
        self.assertEqual((moved["compared"], moved["unlisted"]), (1, 1))


class TestItReachesThePage(unittest.TestCase):
    def _payload(self, days, cabin_book=None):
        return promote(FLEET, season=SEASON,
                       cabins=cabin_book if cabin_book is not None else cabins(record()),
                       sales=sale_book(days))

    def test_the_change_log_ships_with_the_page(self):
        payload = self._payload({
            "2026-08-28": day(("alia-soul::2027-05-01", on_sale())),
            "2026-08-29": day(("alia-soul::2027-05-01", [1000.0, None, "USD"])),
        })
        page = build_payload(Dataset.from_dict(payload))
        self.assertEqual(page["deals"]["on_sale_changes"]["moves"][0]["kind"], "ended")

    def test_the_day_every_sale_ends_still_has_a_change_log(self):
        """The block sits beside the fleet summary rather than inside it, so
        the one day the summary is empty is not the day the panel goes silent
        about why."""
        payload = self._payload(
            {
                "2026-08-28": day(("alia-soul::2027-05-01", on_sale())),
                "2026-08-29": day(("alia-soul::2027-05-01", [1000.0, None, "USD"])),
            },
            cabin_book=cabins(record(cabins=(("Twin", 1000.0, None),))),
        )
        self.assertNotIn("on_sale", payload["deals"])
        self.assertEqual(payload["deals"]["on_sale_changes"]["moves"][0]["kind"], "ended")

    def test_a_dataset_with_no_sale_book_ships_no_key(self):
        payload = promote(FLEET, season=SEASON, cabins=cabins(record()))
        self.assertNotIn("on_sale_changes", payload.get("deals") or {})

    def test_a_book_with_no_readable_day_produces_no_block(self):
        payload = self._payload({})
        self.assertNotIn("on_sale_changes", payload.get("deals") or {})


class TestPromotionStaysPure(unittest.TestCase):
    def test_the_same_book_promotes_to_the_same_block_every_time(self):
        """CI compares promote's output byte for byte, so an order that depends
        on a dict's insertion order is a build that fails on somebody else's
        machine."""
        days = {
            "2026-08-28": day(("serenity::2027-05-01", on_sale()),
                              ("alia-soul::2027-05-01", on_sale())),
            "2026-08-29": day(("serenity::2027-05-01", [1000.0, None, "USD"]),
                              ("alia-soul::2027-05-01", [1000.0, None, "USD"])),
        }
        first = promote(FLEET, season=SEASON, cabins=cabins(record()), sales=sale_book(days))
        again = promote(FLEET, season=SEASON, cabins=cabins(record()), sales=sale_book(days))
        self.assertEqual(json.dumps(first["deals"], sort_keys=True),
                         json.dumps(again["deals"], sort_keys=True))


class TestTheCommittedBook(unittest.TestCase):
    """The book in `data/` is what the published page's change log is built
    from, so its shape is checked here rather than only in a fixture.

    Through `published`, because it is an assertion about a file the cabin
    pass writes: read directly it would sit in front of `cabins.yml`'s fetch
    and could stop the only job able to correct whatever it was complaining
    about.
    """

    def setUp(self):
        self.book = published.raw("sales.json")

    def test_every_day_is_a_census_with_a_count_that_matches_it(self):
        for name, entry in self.book["days"].items():
            with self.subTest(day=name):
                self.assertEqual(entry["read"], len(entry["sailings"]))
                self.assertEqual(
                    entry["on_sale"],
                    sum(1 for row in entry["sailings"].values() if row[1] is not None),
                )

    def test_every_row_is_a_price_a_list_price_or_none_and_a_currency(self):
        for name, entry in self.book["days"].items():
            for key, row in entry["sailings"].items():
                with self.subTest(day=name, sailing=key):
                    price, was, currency = row
                    self.assertGreater(price, 0)
                    self.assertTrue(currency)
                    if was is not None:
                        self.assertGreater(was, price)


if __name__ == "__main__":
    unittest.main()
