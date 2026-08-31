"""How many berths are left at the price on the row, and the ladder behind it.

The dataset's job here is two rules and one shape. The rules: a count is the
total across *every* room it covers, and is unknown the moment one of them does
not state a figure -- once for the rooms at the advertised price and once for
the whole sailing. The shape: a list of seller blocks, written that way so the
day PADI's own availability figure arrived it was another block rather than a
rewrite ([#92]). It has arrived, and the two counts are why it needed two
slots: PADI answers the second question and not the first, so its figure must
never reach the field the Places column reads.

Everything the browser does with this is display. The arithmetic that decides
what a berth costs stays here, where it is tested.
"""

from __future__ import annotations

import unittest

import published
from liveaboard.promote import (
    SELLERS,
    STALE_LADDER,
    _berth_blocks,
    _drop_stale_ladder,
    _fx_table,
    berth_key,
    promote,
)

# The candidate fixtures, from the module that owns them: a second copy here
# would be a second definition of what a departure looks like, and the two
# would drift the first time one of them gained a field.
from test_promote import SEASON, candidate, departure  # noqa: E402

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
        return _berth_blocks(record(*cabins), None, {}, None)

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
    """A list of sellers, written that way before the second one arrived.

    It has arrived (#92), and the shape held: PADI's figure is another block
    rather than a migration of every departure in the dataset.
    """

    def test_a_block_names_its_seller_by_index(self):
        blocks = _berth_blocks(record(cabin("Twin", 1200, 4)), None, {}, None)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(SELLERS[blocks[0][0]], "liveaboard.com")

    def test_padi_has_a_seat_at_the_table_already(self):
        self.assertIn("padi.com", SELLERS)

    def test_names_are_pooled_across_sailings(self):
        # 2,982 cabins share 157 names; a boat calls its rooms the same thing
        # every week it sells them.
        names: dict[str, int] = {}
        first = _berth_blocks(record(cabin("Twin", 1200, 4)), None, names, None)
        second = _berth_blocks(record(cabin("Twin", 1300, 2)), None, names, None)
        self.assertEqual(names, {"Twin": 0})
        self.assertEqual(first[0][2][0][0], second[0][2][0][0])

    def test_an_unread_sailing_gets_no_block_at_all(self):
        # Not a sailing with no cabins. The crawl draws the same distinction
        # between a page that answered nothing and a boat selling nothing.
        self.assertEqual(_berth_blocks(None, None, {}, None), [])
        self.assertEqual(_berth_blocks({"cabins": []}, None, {}, None), [])

    def test_a_cabin_with_no_price_is_not_a_rung_at_zero(self):
        blocks = _berth_blocks(record(cabin("Twin", 1200, 4),
                                      {"name": "Mystery", "berths": 2}), None, {}, None)
        self.assertEqual([rung[0] for rung in blocks[0][2]], [0])


def sailing(availability: int | None = 24) -> dict[str, object]:
    """One row of PADI's sailing book, of which only `availability` is read."""
    return {"boat": "test-boat", "start": "2027-05-01", "price": 1400.0,
            "currency": "USD", "availability": availability}


class TestTheSecondSellersCount(unittest.TestCase):
    """Two sellers, two questions, and no arithmetic between them.

    PADI publishes one figure and no ladder. Which of the two counts it is was
    measured rather than assumed: across the 584 sailings where both speak it
    matches liveaboard.com's whole-sailing total on 77% exactly and 88% within
    two berths, against 22% and a mean error of seven berths for the count at
    the advertised price.
    """

    def test_it_lands_in_its_own_slot_never_the_advertised_one(self):
        """The mistake this shape exists to prevent.

        Slot 1 means "at the price on the row". Putting a whole-sailing figure
        there would relabel "22 aboard" as "22 at this price" on the 249 rows
        with no ladder to contradict it.
        """
        blocks = _berth_blocks(record(cabin("Twin", 1200, 4)), sailing(24), {}, None)
        padi = [b for b in blocks if SELLERS[b[0]] == "padi.com"][0]
        self.assertIsNone(padi[1])
        self.assertEqual(padi[3], 24)

    def test_a_seller_with_a_count_and_no_ladder_gets_no_cabins(self):
        """"24 places" and "24 places at a stated price" are different claims,
        and inventing a rung to carry the first dresses it up as the second."""
        blocks = _berth_blocks(None, sailing(20), {}, None)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(SELLERS[blocks[0][0]], "padi.com")
        self.assertIsNone(blocks[0][2])
        self.assertEqual(blocks[0][3], 20)

    def test_liveaboard_states_the_whole_sailing_too(self):
        """Which is what makes the two comparable rather than merely adjacent."""
        blocks = _berth_blocks(
            record(cabin("Twin", 1200, 4), cabin("Suite", 1500, 3)), None, {}, None)
        self.assertEqual(blocks[0][1], 4)   # at the advertised price
        self.assertEqual(blocks[0][3], 7)   # aboard, at any price

    def test_one_unstated_cabin_makes_the_whole_sailing_unknown(self):
        """The same rule one rung up: a partial sum is not a total."""
        blocks = _berth_blocks(
            record(cabin("Twin", 1200, 4), cabin("Suite", 1500, None)), None, {}, None)
        self.assertEqual(blocks[0][1], 4)
        self.assertIsNone(blocks[0][3])

    def test_liveaboard_comes_first(self):
        """The page reads slot 1 off the first block that fills it, and only
        one seller can answer for the advertised price."""
        blocks = _berth_blocks(record(cabin("Twin", 1200, 4)), sailing(), {}, None)
        self.assertEqual([SELLERS[b[0]] for b in blocks], ["liveaboard.com", "padi.com"])

    def test_a_sailing_neither_seller_counted_gets_no_block(self):
        self.assertEqual(_berth_blocks(None, sailing(None), {}, None), [])
        self.assertEqual(_berth_blocks(None, {}, {}, None), [])

    def test_zero_aboard_is_an_answer_and_is_kept(self):
        """Sold out is a count. Dropping it would print "not stated"."""
        blocks = _berth_blocks(None, sailing(0), {}, None)
        self.assertEqual(blocks[0][3], 0)


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
            None, {}, fx,
        )
        self.assertEqual(blocks[0][2][0][1], 500)

    def test_prices_are_whole_numbers(self):
        # The page prints whole euros; "1501.0" is two characters of nothing,
        # 2,982 times over.
        blocks = _berth_blocks(record(cabin("Twin", 1200.4, 2)), None, {}, None)
        self.assertIsInstance(blocks[0][2][0][1], int)


class TestTheJoin(unittest.TestCase):
    def test_vessel_and_day_is_the_key(self):
        self.assertEqual(berth_key("iceberg", "2027-07-25"), "iceberg::2027-07-25")

    def test_a_sailing_with_no_cabin_record_keeps_its_row(self):
        """Merging a second source must never change the row count."""
        candidate = published.raw("candidate.json")
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
        cls.payload = published.raw()

    def test_the_pools_are_published(self):
        self.assertTrue(self.payload["cabin_names"])
        self.assertEqual(self.payload["sellers"][0], "liveaboard.com")
        self.assertTrue(self.payload["berths_read"])

    def test_every_rung_indexes_a_name_that_exists(self):
        names = self.payload["cabin_names"]
        for departure in self.payload["departures"]:
            for block in departure.get("berths", []):
                # A seller that counted the sailing and published no ladder has
                # no cabin list at all, which is the shape rather than a gap.
                for rung in block[2] or []:
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
        864 sat within 0.6%. The threshold is `promote.STALE_LADDER`, imported
        rather than repeated so the rule that drops a ladder and the test that
        checks none survived cannot drift apart. It is 3% — loose enough that a
        night's
        repricing is not a red build, tight enough that a ladder on the wrong
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
                    abs(cheapest - advertised) / advertised, STALE_LADDER,
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


class TestAStaleLadderIsRefused(unittest.TestCase):
    """A ladder that contradicts its row is not that row's ladder.

    The advertised price *is* the bottom rung — checked on 864 of 864 — so a
    bottom rung far below it is not a cheaper berth on offer, it is last week's
    prices still on the shelf. It happened live: the day the Red Sea Aggressors'
    33% sale ended, the daily refresh re-priced 36 sailings to list while the
    booking pages behind them had been read two days earlier, and the published
    page offered a €1,588 berth on a €2,371 sailing.
    """

    def ladder(self, cheapest: float):
        return [[0, 4, [[0, cheapest, 4, None]], 4]]

    def test_a_ladder_that_agrees_survives(self):
        kept, dropped = _drop_stale_ladder(self.ladder(1000), 1000)
        self.assertEqual(len(kept), 1)
        self.assertEqual(dropped, {})

    def test_a_night_s_repricing_is_not_a_contradiction(self):
        """All 864 ladders sat within 0.6% of their row when read an hour
        apart; the threshold has to leave room for that."""
        kept, dropped = _drop_stale_ladder(self.ladder(1000), int(1000 * (1 + STALE_LADDER / 2)))
        self.assertEqual(len(kept), 1)
        self.assertEqual(dropped, {})

    def test_a_ladder_from_before_a_sale_ended_is_dropped(self):
        kept, dropped = _drop_stale_ladder(self.ladder(1588), 2371)
        self.assertEqual(kept, [])
        # Keyed by the seller whose reading went, so `_list_prices` can refuse
        # to quote that seller about this sailing. A bare count could not.
        self.assertEqual(dropped, {0: 1588})

    def test_it_cuts_both_ways(self):
        """Whichever of the two is stale, they cannot both describe this
        sailing, and the ladder is the one the page can do without."""
        kept, dropped = _drop_stale_ladder(self.ladder(2371), 1588)
        self.assertEqual(kept, [])

    def test_the_other_seller_s_count_survives_the_drop(self):
        """PADI's figure is not a ladder and has nothing to contradict.

        Dropping it too would lose the only count left on those rows.
        """
        blocks = [[0, 4, [[0, 1588, 4, None]], 4], [1, None, None, 22]]
        kept, _ = _drop_stale_ladder(blocks, 2371)
        self.assertEqual(kept, [[1, None, None, 22]])

    def test_a_row_with_no_price_to_check_against_keeps_its_ladder(self):
        kept, dropped = _drop_stale_ladder(self.ladder(1000), None)
        self.assertEqual(len(kept), 1)
        self.assertEqual(dropped, {})

    def test_promote_names_every_ladder_it_refused(self):
        """Never silent: each one is a booking page this pipeline read and then
        declined to publish, and only a fresh crawl can put it back."""
        payload = published.raw()
        for line in payload.get("stale_ladders") or []:
            self.assertIn("ladder starts at", line)


class TestAStaleLadderCannotSpeak(unittest.TestCase):
    """A reading thrown away stays thrown away, in every field it touches.

    The drop above landed and the sale beside it did not: `_drop_stale_ladder`
    rejected the Aggressors' ladders for sitting 33% below their own rows, and
    the very next call was handed the same book. So all 36 dropped rows kept
    the discount that book claimed, and on all 36 the "down from" figure was
    the price printed beside it — "−33%, down from €2,371" on a €2,371 berth,
    published by the site that exists to catch that.

    Two assertions, at both ends. The unit one says the ladder does not reach
    `_list_prices`; the one over committed data says no shipped row states a
    list price equal to its own fare, which is the observable shape of the bug
    and cannot be satisfied by coincidence.
    """

    def sale(self, price, cheapest, listed):
        payload = promote(
            candidate([departure(price=price)]), season=SEASON,
            cabins={"collected": "2026-08-28", "departures": {"alia-soul::2027-05-01": {
                "boat": "alia-soul", "start": "2027-05-01", "currency": "USD",
                "cabins": [{"name": "Twin", "price": cheapest, "list_price": listed}],
            }}},
        )
        return payload["departures"][0]

    def test_a_ladder_that_explains_its_row_still_reports_its_sale(self):
        """The control: without it the test below passes on a broken parser."""
        self.assertEqual(self.sale(900.0, 900.0, 1000.0)["sale"]["pct"], 10)

    def test_a_rejected_ladder_reports_no_sale(self):
        """The Aggressors' 36 rows, in miniature: a booking page read before
        the sale ended, against a row re-priced to list after it."""
        row = self.sale(2760.0, 1849.0, 2760.0)
        self.assertNotIn("berths", row)
        self.assertNotIn("sale", row)

    def test_no_shipped_row_is_marked_down_from_its_own_price(self):
        """"Down from €2,371" beside €2,371 is the bug's visible form.

        Half a euro of slack, because the two sides round separately: `was` is
        a whole euro out of `_to_display` and the fare converts from whatever
        the seller quoted.
        """
        from liveaboard.money import FxTable, Money

        payload = published.raw()
        fx = FxTable.from_dict(payload["fx"])
        checked = 0
        for departure in payload["departures"]:
            was = (departure.get("sale") or {}).get("was")
            if was is None:
                continue
            quoted = Money.parse(departure["price"], "EUR")
            advertised = float(fx.to_display(quoted)[0].amount)
            self.assertGreater(
                abs(was - advertised), 0.5,
                f"{departure['id']}: on sale, down from {was}, and priced at "
                f"{advertised:.0f} — a markdown off the price beside it",
            )
            checked += 1
        # A guard on the guard: a dataset with no sales would pass in silence,
        # which is how this one would come back.
        self.assertGreater(checked, 100, "almost no row states a list price")

