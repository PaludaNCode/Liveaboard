"""How many berths are left at the price on the row, and the ladder behind it.

The dataset's job here is one rule and one shape. The rule: places left at the
advertised price is the total across *every* room selling at that price, and is
unknown the moment one of them does not state a count. The shape: a list of
seller blocks, so the day PADI's own availability figure arrives it is another
block rather than a rewrite ([#92]).

Everything the browser does with this is display. The arithmetic that decides
what a berth costs stays here, where it is tested.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from liveaboard.promote import SELLERS, _berth_blocks, _fx_table, berth_key, promote

DATASET = Path(__file__).resolve().parents[1] / "data" / "egypt-2027.json"


def cabin(name: str, price: float, berths: int | None, *, sold_out: bool = False,
          supp: int | None = None) -> dict[str, object]:
    return {"name": name, "price": price, "berths": berths,
            "sold_out": sold_out, "single_supplement_pct": supp}


def record(*cabins: dict[str, object]) -> dict[str, object]:
    return {"boat": "test-boat", "start": "2027-05-01", "currency": "EUR",
            "cabins": list(cabins)}


class TestSpotsAtTheAdvertisedPrice(unittest.TestCase):
    """One rule, in one place, because the page must not re-derive it."""

    def blocks(self, *cabins: dict[str, object]):
        return _berth_blocks(record(*cabins), {}, None)

    def spots(self, *cabins: dict[str, object]):
        return self.blocks(*cabins)[0][1]

    def test_one_room_at_the_price(self):
        self.assertEqual(self.spots(cabin("Twin", 1200, 4), cabin("Suite", 1500, 2)), 4)

    def test_two_rooms_at_one_price_are_added_up(self):
        # 233 of 864 sailings sell more than one room at their cheapest price,
        # so this is a quarter of the fleet rather than an edge case.
        self.assertEqual(
            self.spots(cabin("Twin", 1200, 4), cabin("Suite A+", 1200, 8),
                       cabin("Suite B", 1500, 2)),
            12,
        )

    def test_a_full_room_at_the_price_still_counts_as_zero_of_it(self):
        # Yachtiano: a Twin and a Suite A+ both at $1,748, the twin full.
        # Reporting the twin's 0 read as "the advertised price is gone" on
        # thirteen sailings while eight berths were on sale at that price.
        self.assertEqual(
            self.spots(cabin("Twin A", 1748, 0, sold_out=True),
                       cabin("Suite A+", 1748, 8)),
            8,
        )

    def test_every_room_at_the_price_full_is_zero_not_unknown(self):
        self.assertEqual(
            self.spots(cabin("Twin", 1200, 0, sold_out=True),
                       cabin("Suite", 1500, 6)),
            0,
        )

    def test_one_unstated_count_makes_the_whole_total_unknown(self):
        # A partial sum is a lower bound wearing a total's clothes, and this
        # site does not publish those.
        self.assertIsNone(
            self.spots(cabin("Twin", 1200, 4), cabin("Bunk", 1200, None))
        )

    def test_a_dearer_room_is_not_counted_at_the_cheaper_price(self):
        self.assertEqual(self.spots(cabin("Twin", 1200, 4), cabin("Suite", 1500, 99)), 4)


class TestTheShapeTheresPadiRoomIn(unittest.TestCase):
    """A list of sellers, written that way before the second one arrives."""

    def test_a_block_names_its_seller_by_index(self):
        blocks = _berth_blocks(record(cabin("Twin", 1200, 4)), {}, None)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(SELLERS[blocks[0][0]], "liveaboard.com")

    def test_padi_has_a_seat_at_the_table_already(self):
        # The pool is written now so a second seller is another block rather
        # than a migration of every departure in the dataset.
        self.assertIn("padi.com", SELLERS)

    def test_names_are_pooled_across_sailings(self):
        # 2,982 cabins share 157 names; a boat calls its rooms the same thing
        # every week it sells them.
        names: dict[str, int] = {}
        first = _berth_blocks(record(cabin("Twin", 1200, 4)), names, None)
        second = _berth_blocks(record(cabin("Twin", 1300, 2)), names, None)
        self.assertEqual(names, {"Twin": 0})
        self.assertEqual(first[0][2][0][0], second[0][2][0][0])

    def test_an_unread_sailing_gets_no_block_at_all(self):
        # Not a sailing with no cabins. The crawl draws the same distinction
        # between a page that answered nothing and a boat selling nothing.
        self.assertEqual(_berth_blocks(None, {}, None), [])
        self.assertEqual(_berth_blocks({"cabins": []}, {}, None), [])

    def test_a_cabin_with_no_price_is_not_a_rung_at_zero(self):
        blocks = _berth_blocks(record(cabin("Twin", 1200, 4),
                                      {"name": "Mystery", "berths": 2}), {}, None)
        self.assertEqual([rung[0] for rung in blocks[0][2]], [0])


class TestConversionHappensInPython(unittest.TestCase):
    """The browser sums what it is given; it does not convert.

    A ladder quoted in dollars beside a euro berth price would be two
    currencies in one panel, and the page has no way to tell them apart.
    """

    def test_a_dollar_ladder_arrives_in_the_display_currency(self):
        fx = _fx_table({
            "display_currency": "EUR", "as_of": "2026-08-28", "source": "test",
            "rates": {"USD": "0.5"},
        })
        blocks = _berth_blocks(
            {"boat": "b", "start": "2027-05-01", "currency": "USD",
             "cabins": [cabin("Twin", 1000, 2)]},
            {}, fx,
        )
        self.assertEqual(blocks[0][2][0][1], 500)

    def test_prices_are_whole_numbers(self):
        # The page prints whole euros; "1501.0" is two characters of nothing,
        # 2,982 times over.
        blocks = _berth_blocks(record(cabin("Twin", 1200.4, 2)), {}, None)
        self.assertIsInstance(blocks[0][2][0][1], int)


class TestTheJoin(unittest.TestCase):
    def test_vessel_and_day_is_the_key(self):
        self.assertEqual(berth_key("iceberg", "2027-07-25"), "iceberg::2027-07-25")

    def test_a_sailing_with_no_cabin_record_keeps_its_row(self):
        """Merging a second source must never change the row count."""
        candidate = json.loads(
            (Path(__file__).resolve().parents[1] / "data" / "candidate.json")
            .read_text(encoding="utf-8")
        )
        with_book = promote(candidate, cabins={
            "collected": "2026-08-28",
            "departures": {"x": record(cabin("Twin", 1200, 4))},
        })
        without = promote(candidate)
        self.assertEqual(len(with_book["departures"]), len(without["departures"]))


class TestTheCommittedDataset(unittest.TestCase):
    """What actually shipped, not what the helpers do in isolation."""

    @classmethod
    def setUpClass(cls):
        cls.payload = json.loads(DATASET.read_text(encoding="utf-8"))

    def test_the_pools_are_published(self):
        self.assertTrue(self.payload["cabin_names"])
        self.assertEqual(self.payload["sellers"][0], "liveaboard.com")
        self.assertTrue(self.payload["berths_read"])

    def test_every_rung_indexes_a_name_that_exists(self):
        names = self.payload["cabin_names"]
        for departure in self.payload["departures"]:
            for block in departure.get("berths", []):
                for rung in block[2]:
                    self.assertLess(rung[0], len(names), departure["id"])

    def test_every_block_names_a_seller_that_exists(self):
        sellers = self.payload["sellers"]
        for departure in self.payload["departures"]:
            for block in departure.get("berths", []):
                self.assertLess(block[0], len(sellers), departure["id"])

    def test_the_advertised_price_is_the_bottom_of_the_ladder(self):
        """The claim the whole feature rests on, checked on every shipped row.

        The berth price is stored as the source quoted it and the ladder is
        already converted, so the comparison has to convert too — and then
        allows a euro of slack, because each side rounds separately.

        Not a guard against a parser bug so much as against a *join* bug: a
        ladder attached to the wrong sailing would still be a valid ladder, and
        this is the only thing that would notice.

        Compared as a percentage rather than exactly, because the row and the
        ladder come from two passes and liveaboard.com re-prices overnight.
        Measured on a book read the day before the refresh: every one of the
        864 sat within 0.6%. The threshold is 3% — loose enough that a night's
        drift is not a red build, tight enough that a ladder on the wrong
        sailing is, since the rungs across this fleet run from €500 to €2,900.
        Running the two passes an hour apart (``cabins.yml``) is what keeps
        the real figure near zero; this is the net under that.
        """
        from liveaboard.money import FxTable, Money

        fx = FxTable.from_dict(self.payload["fx"])
        checked = 0
        for departure in self.payload["departures"]:
            for block in departure.get("berths", []):
                if not block[2]:
                    continue
                quoted = Money.parse(departure["price"], "EUR")
                advertised = float(fx.to_display(quoted)[0].amount)
                cheapest = min(rung[1] for rung in block[2])
                self.assertLessEqual(
                    abs(cheapest - advertised) / advertised, 0.03,
                    f"{departure['id']}: row says {advertised:.0f}, "
                    f"ladder starts at {cheapest} — too far apart to be a "
                    f"night's repricing, so check the join",
                )
                checked += 1
        # A guard on the guard: an empty dataset would pass the loop silently.
        self.assertGreater(checked, 800, "the ladder reached almost no rows")

    def test_places_left_never_exceeds_what_the_rooms_state(self):
        for departure in self.payload["departures"]:
            for block in departure.get("berths", []):
                if block[1] is None:
                    continue
                cheapest = min(rung[1] for rung in block[2])
                stated = sum(
                    rung[2] or 0 for rung in block[2] if rung[1] == cheapest
                )
                self.assertEqual(block[1], stated, departure["id"])


if __name__ == "__main__":
    unittest.main()
