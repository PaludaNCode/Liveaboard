"""Tests for #6: whether a fare rises as a sailing's berths sell.

`tools/fill_and_price.py` walks the dataset's own commits, so its answer is
only as good as the rules that decide what counts as a reading and what counts
as a move. Each test below is one of those rules, and each is a mistake the
change log made once over the same data:

**A sailing is (seller, boat, start).** The ids moved under sailings nobody
touched; keyed on them, one sailing becomes two half-series and its step is
lost.

**A reading is the seller's own.** A re-promote of the same inputs, or a row
carried forward, is not the market moving.

**A boat's move is not a sailing's.** liveaboard.com quotes in dollars fares
set in euro, so a whole season moves with the rate -- 5,106 boat-wide moves on
sailings whose places held, against 149 that were a sailing's own.

**A markdown is not a rise, and a count nobody stated is not a fall.**

Fixtures only: nothing here opens the committed dataset, so the suite that
gates a fetch can run it.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from fill_and_price import (  # noqa: E402
    SELLERS,
    fare_verdicts,
    places_verdict,
    render,
    series,
    sold_through,
    steps,
    tally,
)


def dep(boat="alia-soul", start="2027-06-02", amount=1800.0, read="2026-10-01", *,
        currency="USD", seller="liveaboard.com", places=None, places_read=None,
        id_=None, sale=None, ladder=None, padi=None):
    """One departure as `promote` writes it, with one seller's block."""
    row = {
        "id": id_ or f"{boat}-{start}-0",
        "itinerary_id": f"{boat}--trip",
        "start": start,
        "price": {"amount": amount, "currency": currency},
        "provenance": {"source_id": seller, "retrieved": read},
    }
    if places is not None or ladder is not None:
        block = [SELLERS.index(seller), None, ladder, places]
        if places_read:
            block.append(places_read)
        row["berths"] = [block]
    if sale:
        row["sale"] = sale
    if padi:
        amount_p, read_p = padi
        row["padi_price"] = {"amount": amount_p, "currency": "EUR"}
        row["padi_provenance"] = {"source_id": "padi.com", "retrieved": read_p}
    return row


def dataset(*departures, berths_read="2026-10-01", cabin_names=()):
    boats = sorted({d["itinerary_id"] for d in departures})
    return {
        "sellers": list(SELLERS),
        "berths_read": berths_read,
        "cabin_names": list(cabin_names),
        "itineraries": [{"id": i, "boat_id": i.split("--")[0]} for i in boats],
        "departures": list(departures),
    }


def walk(*snapshots):
    by_sailing, _ = series((f"2026-10-{i + 1:02d}", s) for i, s in enumerate(snapshots))
    return steps(by_sailing)


def fleet(read, moves):
    """One boat's season: `moves` maps each start to its fare that day."""
    return dataset(*(dep(start=start, amount=amount, read=read)
                     for start, amount in moves.items()))


class TestASailingIsItsSellerBoatAndDay(unittest.TestCase):
    def test_a_renumbered_id_is_still_one_sailing(self):
        found = walk(
            dataset(dep(id_="alia-soul-2027-06-02-0", read="2026-10-01")),
            dataset(dep(id_="alia-soul-2027-06-02-3", read="2026-10-02", amount=1900.0)),
        )
        self.assertEqual(len(found), 1)
        self.assertEqual((found[0].before.amount, found[0].after.amount), (1800.0, 1900.0))

    def test_two_rows_one_seller_starts_on_one_day_are_set_aside(self):
        clash = dataset(dep(id_="a"), dep(id_="b", amount=2000.0))
        by_sailing, clashed = series([("2026-10-01", clash)])
        self.assertEqual(by_sailing, {})
        self.assertEqual(clashed["liveaboard.com"], 1)

    def test_each_seller_is_read_from_its_own_field(self):
        found = walk(
            dataset(dep(padi=(1500.0, "2026-10-01"))),
            dataset(dep(padi=(1600.0, "2026-10-02"))),
        )
        padi = [s for s in found if s.seller == "padi.com"]
        self.assertEqual([(s.before.amount, s.after.amount) for s in padi],
                         [(1500.0, 1600.0)])
        # liveaboard.com's fare was not read again, so it took no step.
        self.assertEqual([s.seller for s in found], ["padi.com"])


class TestAReadingIsTheSellersOwn(unittest.TestCase):
    def test_a_fare_not_read_again_is_not_a_reading(self):
        # A re-promote moved the figure on the same read date: our code, not
        # the market.
        found = walk(
            dataset(dep(amount=1800.0, read="2026-10-01")),
            dataset(dep(amount=1850.0, read="2026-10-01")),
            dataset(dep(amount=1850.0, read="2026-10-03")),
        )
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].before.fare_read, "2026-10-01")

    def test_places_not_read_again_are_not_a_fall(self):
        found = walk(
            dataset(dep(read="2026-10-01", places=20, places_read="2026-09-30")),
            dataset(dep(read="2026-10-02", places=12, places_read="2026-09-30")),
        )
        self.assertEqual(places_verdict(found[0]), "not re-read")

    def test_a_count_nobody_stated_is_not_a_fall(self):
        found = walk(
            dataset(dep(read="2026-10-01", places=20), berths_read="2026-10-01"),
            dataset(dep(read="2026-10-02", places=None, ladder=[]),
                    berths_read="2026-10-02"),
        )
        self.assertEqual(places_verdict(found[0]), "not stated")

    def test_a_count_read_again_is_a_fall(self):
        found = walk(
            dataset(dep(read="2026-10-01", places=20), berths_read="2026-10-01"),
            dataset(dep(read="2026-10-02", places=12), berths_read="2026-10-02"),
            dataset(dep(read="2026-10-03", places=0), berths_read="2026-10-03"),
        )
        self.assertEqual([places_verdict(s) for s in found], ["fell", "sold out"])


class TestABoatsMoveIsNotASailings(unittest.TestCase):
    SEASON = ("2027-06-02", "2027-06-09", "2027-06-16")

    def test_a_season_moving_with_the_rate_is_the_boats(self):
        found = walk(
            fleet("2026-10-01", dict(zip(self.SEASON, (1800.0, 1900.0, 2000.0)))),
            fleet("2026-10-02", dict(zip(self.SEASON, (1818.0, 1919.0, 2020.0)))),
        )
        self.assertEqual(fare_verdicts(found), ["boat-wide"] * 3)

    def test_a_sailing_moving_beyond_its_boat_is_its_own(self):
        found = walk(
            fleet("2026-10-01", dict(zip(self.SEASON, (1800.0, 1900.0, 2000.0)))),
            fleet("2026-10-02", dict(zip(self.SEASON, (1818.0, 2219.0, 2020.0)))),
        )
        self.assertEqual(fare_verdicts(found), ["boat-wide", "rose", "boat-wide"])

    def test_a_boat_with_too_few_sailings_read_cannot_say(self):
        found = walk(
            fleet("2026-10-01", dict(zip(self.SEASON[:2], (1800.0, 1900.0)))),
            fleet("2026-10-02", dict(zip(self.SEASON[:2], (1818.0, 2219.0)))),
        )
        self.assertEqual(fare_verdicts(found), ["too few siblings"] * 2)

    def test_a_move_under_the_floor_is_the_source_rounding(self):
        found = walk(
            fleet("2026-10-01", dict(zip(self.SEASON, (1800.0, 1900.0, 2000.0)))),
            fleet("2026-10-02", dict(zip(self.SEASON, (1799.0, 1900.0, 2000.0)))),
        )
        self.assertEqual(fare_verdicts(found), ["same"] * 3)


class TestWhatSaysNothingIsSetAside(unittest.TestCase):
    def test_a_currency_switch_is_not_a_move(self):
        found = walk(
            dataset(dep(amount=1800.0, read="2026-10-01")),
            dataset(dep(amount=1600.0, read="2026-10-02", currency="EUR")),
        )
        self.assertEqual(fare_verdicts(found), ["currency"])

    def test_a_markdown_starting_or_ending_is_not_a_move(self):
        on_sale = {"sellers": [0], "pct": 20, "was": 2250}
        found = walk(
            dataset(dep(amount=1800.0, read="2026-10-01", sale=on_sale)),
            dataset(dep(amount=2250.0, read="2026-10-02")),
        )
        self.assertEqual(fare_verdicts(found), ["markdown"])

    def test_another_sellers_markdown_is_not_this_ones(self):
        # PADI's offer on the sailing says nothing about liveaboard.com's fare,
        # which is the only one whose sale state is asked here.
        padi_sale = {"sellers": [1], "pct": 10}
        season = ("2027-06-02", "2027-06-09", "2027-06-16")
        first = dataset(*(dep(start=s, read="2026-10-01") for s in season))
        second = dataset(*(dep(start=s, read="2026-10-02",
                               amount=2000.0 if s == season[0] else 1800.0,
                               sale=padi_sale if s == season[0] else None)
                           for s in season))
        self.assertEqual(fare_verdicts(walk(first, second))[0], "rose")


class TestTheAnswer(unittest.TestCase):
    SEASON = ("2027-06-02", "2027-06-09", "2027-06-16", "2027-06-23", "2027-06-30")

    def day(self, n, fares, places):
        read = f"2026-10-{n:02d}"
        return dataset(*(dep(start=s, read=read, amount=f, places=p)
                         for s, f, p in zip(self.SEASON, fares, places)),
                       berths_read=read)

    def test_a_rise_the_reading_after_a_fill_counts(self):
        found = walk(
            self.day(1, (1800,) * 5, (20,) * 5),
            self.day(2, (1800,) * 5, (12, 20, 20, 20, 20)),
            self.day(3, (2000, 1800, 1800, 1800, 1800), (12, 20, 20, 20, 20)),
        )
        t = tally(found)["liveaboard.com"]
        self.assertEqual(t.table[("fell", "rose")], 1)
        # The fill's own sailing rose on its next pair, and that pair is
        # counted again in its own right: places held across it.
        self.assertEqual(t.table[("unchanged", "rose")], 1)
        self.assertEqual(t.table[("unchanged", "neither")], 8)
        (fill, rise), = t.rises
        self.assertEqual((fill.after.places, rise.after.amount), (12, 2000))

    def test_the_window_does_not_reach_another_sailing(self):
        # The step after the last one of a sailing belongs to the next sailing
        # in the list; a rise there is not this sailing's.
        found = walk(
            self.day(1, (1800,) * 5, (20,) * 5),
            self.day(2, (1800, 2000, 1800, 1800, 1800), (12, 20, 20, 20, 20)),
        )
        t = tally(found)["liveaboard.com"]
        self.assertEqual(t.table[("fell", "neither")], 1)
        self.assertEqual(t.rises, [])

    def test_a_rise_no_likelier_after_a_fill_than_after_places_grew_says_so(self):
        found = walk(
            self.day(1, (1800,) * 5, (20,) * 5),
            self.day(2, (2000, 2000, 1800, 1800, 1800), (12, 24, 20, 20, 20)),
        )
        text = render(tally(found), first="2026-10-01", last="2026-10-02",
                      days=2, clashed={"liveaboard.com": 0})
        finding = text.rsplit("== finding", 1)[1]
        self.assertIn("no likelier after a fill than after places grew", finding)

    def test_a_rise_likelier_after_a_fill_than_after_either_says_so(self):
        found = walk(
            self.day(1, (1800,) * 5, (20,) * 5),
            self.day(2, (2000, 1800, 1800, 1800, 1800), (12, 24, 20, 20, 20)),
        )
        text = render(tally(found), first="2026-10-01", last="2026-10-02",
                      days=2, clashed={"liveaboard.com": 0})
        self.assertIn("likelier after a fill than after either",
                      text.rsplit("== finding", 1)[1])


class TestTheCheapestRoomsSellingThrough(unittest.TestCase):
    NAMES = ("Lower", "Upper")

    def step(self, before, after):
        return walk(
            dataset(dep(read="2026-10-01", places=sum(p for _, _, p in before),
                        ladder=[list(r) for r in before]),
                    cabin_names=self.NAMES),
            dataset(dep(read="2026-10-02", places=sum(p for _, _, p in after),
                        ladder=[list(r) for r in after]),
                    cabin_names=self.NAMES),
        )[0]

    def test_the_cheapest_gone_while_a_dearer_sells(self):
        # Ladders converted at two different rates: the rung prices move, the
        # rooms do not, and which is cheapest is asked of one ladder alone.
        self.assertTrue(sold_through(self.step(
            [(1, 1900, 4), (0, 1650, 8)], [(1, 1880, 4), (0, 1633, 0)])))

    def test_the_whole_sailing_gone_is_not_a_step_up_the_ladder(self):
        self.assertFalse(sold_through(self.step(
            [(0, 1650, 8), (1, 1900, 4)], [(0, 1650, 0), (1, 1900, 0)])))

    def test_a_dearer_room_selling_out_is_not_the_cheapest(self):
        self.assertFalse(sold_through(self.step(
            [(0, 1650, 8), (1, 1900, 4)], [(0, 1650, 8), (1, 1900, 0)])))


if __name__ == "__main__":
    unittest.main()
