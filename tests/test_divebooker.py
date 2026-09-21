"""What divebooker.com's pages state, read from bytes it really served.

The fixture is the JSON-LD of `/bella-2-haz432`, kept verbatim from a runner
on 2026-09-20 — a boat selling three weeks states the same shapes as one
selling fifty, and costs a tenth of the log to carry back. The parser reads
nothing outside those blocks, so nothing is lost by keeping them rather than
300 KB of markup.

`divebooker-price-details.json` is the second, carried back the same way on
2026-09-21: two vessels' *Price details* panels, which are the two line shapes
the fleet census found and neither of which a reader handles by accident.

Every assertion below is a fact somebody read, and four of them are bugs a
fixture caught before they shipped.
"""

from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path

from liveaboard.promote import _port
from liveaboard.scrape import divebooker_com as db

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "divebooker-bella-2.jsonld.json"


def page() -> str:
    """The blocks put back into a page, which is how the site serves them."""
    blocks = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return "".join(
        f'<script type="application/ld+json">{json.dumps(block)}</script>'
        for block in blocks
    )


class TestOneSailingIsReadOnce(unittest.TestCase):
    """The page states each sailing twice and the book must hold it once.

    Six `Event` nodes on this page and three sailings: each trip offer carries
    its own nested Event, and three top-level Events carry a booking url. A
    parser collecting `@type == Event` gets six departures, three of them
    unpriced — which is the whole reason this module folds on the start date.
    """

    def setUp(self):
        self.book = db.vessel(page(), "/bella-2-haz432")

    def test_six_event_nodes_become_three_departures(self):
        nodes = [n for n in __import__("liveaboard.scrape.jsonld", fromlist=["x"])
                 .walk_documents(page()) if n.get("@type") == "Event"]
        self.assertEqual(len(nodes), 6, "the fixture no longer states the doubling")
        self.assertEqual(len(self.book.departures), 3)

    def test_each_departure_names_both_statements(self):
        """Not decoration: the counts are the evidence for the folding rule,
        and a rule whose evidence is not in the data cannot be re-checked."""
        for row in self.book.departures:
            self.assertEqual(sorted(row.stated_by), ["event", "trip"], row.start)

    def test_every_departure_carries_a_fare_and_a_currency(self):
        for row in self.book.departures:
            self.assertIsNotNone(row.price, row.start)
            self.assertEqual(row.currency, "EUR", row.start)

    def test_nights_come_from_the_dates(self):
        """Never from the trip's name. `Mini Safari` says nothing a parser may
        count, and the two stated dates say everything."""
        self.assertEqual([r.nights for r in self.book.departures], [3, 3, 3])
        self.assertEqual([r.start for r in self.book.departures],
                         ["2026-10-04", "2026-10-11", "2026-10-18"])


class TestWhetherABerthCanBeBoughtComesFromTheSailing(unittest.TestCase):
    """`availability` is the sailing's own claim, not its trip's.

    Two nodes state it and they do not agree. The trip's offer is one copy
    covering every sailing that trip sells and says `InStock` throughout; the
    **Event** is one sailing, and Bella 2's three say `LimitedAvailability`,
    `OnlineOnly` and `OnlineOnly` in the very fixture this parser was written
    against.

    The event pass only filled the field when it was still empty, so the trip's
    answer — read first — won every time, and the committed book stated
    `InStock` on **888 of 888** departures. A field with one value on every row
    is a field carrying no information, and this is the one that says whether a
    diver can buy the berth at all: the vessel page marks sailings SOLD OUT and
    none of that reached us.
    """

    def setUp(self):
        self.rows, self.warnings = db.departures(page())

    def test_the_event_states_it_and_the_event_wins(self):
        self.assertEqual([r.availability for r in self.rows],
                         ["LimitedAvailability", "OnlineOnly", "OnlineOnly"],
                         "the trip's blanket InStock is overwriting the "
                         "sailing's own state")

    def test_the_trip_still_answers_where_the_sailing_does_not(self):
        """A fallback, not a replacement: an unread sailing states nothing."""
        self.assertTrue(all(r.availability for r in self.rows))

    def test_it_stays_a_state_and_never_becomes_a_count(self):
        """No node here says how many berths are left."""
        for row in self.rows:
            self.assertIsInstance(row.availability, str)
            self.assertNotIn("/", row.availability)


class TestASailingThatPricesNothingSaysWhy(unittest.TestCase):
    """46 of 887 state no fare, and 42 of them are not a gap.

    *"Route on Request (Available for groups and charters; Please enquire…)"*
    is the seller's own title on Argo Egypt's 16, Vita Xplorer's 18, Omneia
    Spirit's 7 and Independence II's 1. The booking page agrees from the other
    side: asked for one it returns the right week, 24 or 25 free spaces, and no
    cabin option at all — the boat is empty because nobody is selling seats on
    it. The other 4 are Galaxy 720, sold out.

    `_departure_book` drops all of them silently, which is right to publish and
    wrong to say nothing about. Counted by reason, because `unexplained` is the
    only one worth a person's time and a single total would bury it — the rule
    `fetch_padi._sailing_counts` already keeps one source over.
    """

    def test_the_sellers_own_words_are_the_reason(self):
        self.assertEqual(
            db.why_unpriced("Route on Request (Available for groups and "
                            "charters; Please enquire)", "InStock"),
            "on request")
        self.assertEqual(db.why_unpriced("Full Charter Request", "InStock"),
                         "on request")

    def test_a_withdrawn_week_is_told_apart_from_a_chartered_one(self):
        self.assertEqual(db.why_unpriced("Brothers - Daedalus - Elphinstone",
                                         "SoldOut"), "sold out")

    def test_a_fare_nobody_found_is_the_one_that_stays_unexplained(self):
        """The whole reason the count is split rather than totalled."""
        self.assertIsNone(db.why_unpriced("Best of North (Wrecks)", "InStock"))
        self.assertIsNone(db.why_unpriced(None, None))

    def test_the_vessel_counts_them_by_reason(self):
        node = {"@type": "Event", "name": "Route on Request (Available for "
                "groups and charters)", "startDate": "2027-05-01",
                "endDate": "2027-05-08",
                "offers": {"@type": "Offer", "priceCurrency": "EUR",
                           "availability": "https://schema.org/InStock"}}
        html = ('<script type="application/ld+json">'
                + json.dumps(node) + "</script>")
        book = db.vessel(html, "/argo-egypt-haz441")
        self.assertEqual(book.unpriced, {"on request": 1})
        self.assertEqual(book.as_dict()["unpriced"], {"on request": 1})


BOOKING = Path(__file__).resolve().parent / "fixtures" / "divebooker-booking.json"


class TestTheBookingPageIsTheCabinLadder(unittest.TestCase):
    """`/boatorder/booking?tripId=…`, behind *Select cabin*, on real bytes.

    The one page this seller states a ladder on. This file said for weeks that
    it had no cabin ladder, no berth count and no list price — a verdict about
    the vessel page, which is the only page that had been read. The same shape
    of mistake as "no fee book hiding client-side".
    """

    def setUp(self):
        node = json.loads(BOOKING.read_text(encoding="utf-8"))
        self.page = db.booking_page(payload_page([node]))

    def test_the_sailing_it_is_about(self):
        self.assertEqual((self.page.start, self.page.end),
                         ("2026-12-05", "2026-12-12"))
        self.assertEqual(self.page.trip_id, "75551")

    def test_every_room_carries_its_own_fare_and_what_is_left(self):
        self.assertEqual(len(self.page.cabins), 3)
        self.assertEqual([c.price for c in self.page.cabins],
                         [1272.0, 1272.0, 1272.0])
        self.assertEqual([c.free_spaces for c in self.page.cabins], [8, 8, 8])
        self.assertEqual(self.page.cheapest, 1272.0)

    def test_the_sailing_total_is_the_sellers_and_never_a_sum(self):
        """Three rooms at 8 free spaces beside a sailing total of 8.

        They overlap — the same berths offered shared or private — so adding
        them would put 24 berths on an eight-berth boat. `places left` is the
        seller's own figure or nothing.
        """
        self.assertEqual(self.page.free_spaces, 8)
        self.assertNotEqual(
            self.page.free_spaces,
            sum(c.free_spaces for c in self.page.cabins),
            "the sailing's count was derived by adding the rooms'")

    def test_shared_and_private_are_the_sellers_flag(self):
        self.assertEqual([c.sharing for c in self.page.cabins],
                         [True, True, False])

    def test_the_unit_is_kept_as_the_seller_wrote_it(self):
        """A fare is not a fee: nothing here maps this onto a `FeeBasis`."""
        self.assertEqual({c.unit for c in self.page.cabins}, {"per person"})

    def test_a_markdown_is_read_but_has_never_been_seen(self):
        """`price.old` is an empty string on every option read.

        So it parses to nothing, and no divebooker discount may be counted
        until a real one has been read. The field is carried because the seller
        publishes it, not because this project has evidence of its shape.
        """
        self.assertEqual([c.was for c in self.page.cabins], [None, None, None])

    def test_a_page_with_nothing_on_it_reads_as_nothing(self):
        """A *Route on Request* slot returns spaces and no cabin option.

        It is not a fare this reading missed, and it must never be used as a
        backup price — see `why_unpriced`.
        """
        empty = db.booking_page(payload_page([{"trip": {
            "id": "1", "startDate": "2027-05-01", "endDate": "2027-05-08",
            "sumFreeSpaces": "25"}}]))
        self.assertEqual(empty.cabins, [])
        self.assertIsNone(empty.cheapest)
        self.assertEqual(empty.free_spaces, 25)


class TestTheDayPlanNamesReefsTheSiteListDoesNot(unittest.TestCase):
    """Aml Hayaty's own programme, as the page prints it.

    Its `divesites` array is empty and its day plan dives Abu Nuhas and
    Thistlegorm — 35 sailings, the largest block of blank reef cells on the
    page. PADI's day plan is read for exactly this reason one source over.
    """

    #: Abridged from the page, keeping every reef it names and the shape of the
    #: lines they sit on. Three of the five are deliberately unreadable here.
    PLAN = ("Day 1:\nCheck-In & welcome onboard\n6:00pm: Check in onboard\n"
            "Day 2:\nDolphin House & Siyoul Kebir\n11:00: Dive 1 at Dolphin "
            "House\n15:00: Dive 2 at Siyoul Kebir\n18:30: Night dive at "
            "Siyoul Kebir\nDay 3:\nAbu Nuhas & Thistlegorm\n6:30am: Dive 1 "
            "at Abu Nuhas\n15:00: Dive 3 at Thistlegorm\n"
            "Day 4:\nThistlegorm, Giftun & Departure\n10:00: Dive at Giftun")

    def test_the_reefs_this_project_can_place_are_read(self):
        from liveaboard.promote import _sites_from_name
        self.assertEqual(sorted(_sites_from_name(self.PLAN)),
                         ["abu nuhas", "thistlegorm"])

    def test_an_ambiguous_name_is_left_unplaced(self):
        """*Dolphin House* names two reefs this dataset carries separately.

        Sha'ab Samadai at Marsa Alam and Sha'ab El Erg at Hurghada, 600 km
        apart and both already in the vocabulary. This is a Hurghada
        mini-safari so it is almost certainly El Erg — and *almost certainly*
        is how a St John's week got badged BDE. *Giftun* is not *Small
        Giftun*, and *Siyoul Kebir* appears nowhere in the fleet.
        """
        from liveaboard.promote import _sites_from_name
        for name in ("Dolphin House", "Giftun", "Siyoul Kebir"):
            with self.subTest(name=name):
                self.assertEqual(_sites_from_name(name), [], f"{name} was placed")

    def test_the_plan_is_read_whatever_shape_the_seller_writes_it_in(self):
        """A string, a list of days, or a list of `{title, text}`.

        Which of the three the seller uses has not been read, and a parser
        written against the guess would break on the first page using another.
        """
        for shape in (self.PLAN,
                      self.PLAN.split("\n"),
                      [{"title": line} for line in self.PLAN.split("\n")],
                      {"days": [{"text": self.PLAN}]}):
            with self.subTest(shape=type(shape).__name__):
                read = db._prose(shape)
                self.assertIn("Thistlegorm", read)
                self.assertIn("Abu Nuhas", read)
        self.assertIsNone(db._prose(None))
        self.assertIsNone(db._prose([]))

    def test_it_is_the_last_answer_in_the_chain(self):
        """Behind that seller's own structured list, which is behind everyone."""
        from pathlib import Path as _P
        src = (_P(__file__).resolve().parents[1] / "src" / "liveaboard"
               / "promote.py").read_text(encoding="utf-8")
        chain = src.split("sites = (_sites_from_description", 1)[1].split(
            "# The title's", 1)[0]
        self.assertLess(chain.index('divebooker_trip.get("sites")'),
                        chain.index('divebooker_trip.get("programme")'),
                        "the day plan now outranks that seller's own list")


class TestNothingIsInvented(unittest.TestCase):
    """The two mistakes this fixture caught, kept as guards.

    Both were live in the parser when the real bytes arrived, and neither
    would have been visible in a fixture written from a description of the
    page — which is the reason the project fetches before it parses.
    """

    def setUp(self):
        self.book = db.vessel(page(), "/bella-2-haz432")

    def test_no_operator_is_claimed(self):
        """`Product.brand` on this page is `Divebooker.com` — the seller.

        Reading it as the operator would have published the booking site as
        the company running every Egyptian boat. `Event.organizer` is no
        better: it is `Bella 2`, the hull. This source names no company, and
        a book that states one is stating something nobody published.
        """
        self.assertNotIn("operator", self.book.as_dict())
        self.assertNotIn("Divebooker", json.dumps(self.book.as_dict()))

    def test_the_aggregate_offer_is_not_read_as_a_fare(self):
        """143–144 EUR beside fares of 576, 576 and 579.

        That is each fare over four, on a trip of three nights — a per-day
        rate on days aboard, which the page never labels. Reading it would
        quarter every price on the site.
        """
        stated = {row.price for row in self.book.departures}
        self.assertEqual(stated, {576.0, 579.0})
        for row in self.book.departures:
            self.assertGreater(row.price, 200, "a per-day rate reached a fare")

    def test_a_fare_without_a_currency_is_not_a_fare(self):
        self.assertEqual(db._money({"price": 400}), (None, None))
        self.assertEqual(db._money({"price": "", "priceCurrency": "EUR"}), (None, None))
        self.assertEqual(db._money({"price": "576", "priceCurrency": "EUR"}), (576.0, "EUR"))

    def test_availability_is_kept_as_a_state_and_never_a_count(self):
        """The source's own word, and nothing states a number.

        Kept verbatim so nothing downstream can read a berth count out of it:
        *places left* is a question this source cannot answer, and the page may
        not imply otherwise.

        This used to assert `InStock` on every row, which is how the bug got
        written into its own guard — the trip's blanket answer was the only one
        being read, so a test asserting it passed while the sailing's own state
        was thrown away. It asserts the property it is named for now. Which
        value belongs on which row is
        `TestWhetherABerthCanBeBoughtComesFromTheSailing`.
        """
        for row in self.book.departures:
            self.assertIsInstance(row.availability, str)
            self.assertTrue(row.availability)
            self.assertNotIn("/", row.availability,
                             "the schema.org url reached the book whole")


class TestTheFleetIsDiscoveredNotTyped(unittest.TestCase):
    """Hulls come from the seller's own links, on whichever page lists them.

    The flat namespace is typed by the two letters before the id, and only
    `haz` is a hull — `baz` is a dive site, `eaz` a port, `daz` a country.
    A pattern that took them all would crawl 3,716 reefs.
    """

    LINKS = ('<a href="/bella-2-haz432">a</a>'
             '<a href="https://divebooker.com/discovery-ii-haz395">b</a>'
             '<a href="/blue-hole-baz10740">reef</a>'
             '<a href="/egypt-daz3881">country</a>'
             '<a href="/indonesia-port-of-sorong-eaz18153">port</a>'
             '<a href="/bella-2-haz432">again</a>')

    def test_only_hulls_are_followed(self):
        self.assertEqual(db.hull_links(self.LINKS),
                         ["/bella-2-haz432", "/discovery-ii-haz395"])

    def test_a_hull_path_splits_into_a_slug_and_an_id(self):
        self.assertEqual(db.split_slug("/bella-2-haz432"), ("bella-2", "haz432"))


if __name__ == "__main__":
    unittest.main()


class TestWhatTheDatasetRecordsAboutAThirdSeller(unittest.TestCase):
    """Coverage is published; fares are not, and the reason is a measurement.

    Red Sea Aggressor IV on 2027-07-24 reads 5,398 against our 2,699 for the
    same seven nights — exactly twice. So `Offer.price` is a per-person berth
    on most rows and something else on at least one, and a figure whose unit
    this site cannot state is a figure it does not publish.
    """

    BOOK = {
        "source": "divebooker.com",
        "collected": "2026-09-20",
        "vessels": {"bella-2": {"slug": "bella-2"}, "silky": {"slug": "silky"},
                    "new-hull": {"slug": "new-hull"}},
        "departures": {
            "bella-2::2027-05-08": {"boat": "bella-2", "start": "2027-05-08",
                                    "price": 576.0, "currency": "EUR"},
            "silky::2027-05-15": {"boat": "silky", "start": "2027-05-15",
                                  "price": 1200.0, "currency": "EUR"},
            "bella-2::2030-01-01": {"boat": "bella-2", "start": "2030-01-01",
                                    "price": 600.0, "currency": "EUR"},
            "new-hull::2027-06-01": {"boat": "new-hull", "start": "2027-06-01",
                                     "price": 900.0, "currency": "USD"},
        },
    }
    ALIASES = {"aliases": {"bella-2": "bella-2", "silky": "dune-silky"}}
    DEPARTURES = [
        {"itinerary_id": "i1", "start": "2027-05-01"},
        {"itinerary_id": "i1", "start": "2027-05-08"},
        {"itinerary_id": "i2", "start": "2027-05-15"},
        {"itinerary_id": "i1", "start": "2027-08-30"},
    ]
    BOAT_OF = {"i1": "bella-2", "i2": "dune-silky"}

    def block(self):
        from liveaboard.promote import divebooker_coverage
        return divebooker_coverage(self.BOOK, self.ALIASES, self.DEPARTURES, self.BOAT_OF)

    def test_it_counts_the_season_and_the_join(self):
        block = self.block()
        self.assertEqual(block["departures"], 4)
        # 2030 is outside the season's own span, and the span comes from our
        # departures rather than from a date anybody typed.
        self.assertEqual(block["in_season"], 3)
        self.assertEqual(block["matched"], 2)

    def test_the_alias_is_what_joins_a_hull(self):
        """`silky` is `dune-silky` here, and a slug that matched itself would
        have joined nothing."""
        self.assertEqual(self.block()["matched"], 2)

    def test_a_hull_no_alias_maps_is_named(self):
        """Counted would not do: only a name tells a new hull from a renamed
        one, which is why `deals.unmatched` names its vessels too."""
        self.assertEqual(self.block()["unmapped_vessels"], ["new-hull"])

    def test_no_fare_reaches_the_block_itself(self):
        """The fares are published **on the rows**, and this block is a summary.

        Still asserted after the withholding ended, and for a reason that did
        not change with it: a coverage block that carried prices would be a
        second place the same figures live, and two copies of a price are two
        things that can disagree about one berth.
        """
        import json as _json
        printed = _json.dumps(self.block())
        for fare in ("576", "1200", "900", "600"):
            self.assertNotIn(fare, printed, f"a fare reached the dataset: {fare}")
        self.assertEqual(self.block()["fares"], "published")

    def test_no_book_means_no_block(self):
        from liveaboard.promote import divebooker_coverage
        self.assertIsNone(divebooker_coverage(None, self.ALIASES, self.DEPARTURES, self.BOAT_OF))
        self.assertIsNone(divebooker_coverage({"departures": {}}, self.ALIASES,
                                              self.DEPARTURES, self.BOAT_OF))


class TestTheShippedDatasetCarriesTheThirdFare(unittest.TestCase):
    """The publication gate for the rule above.

    A guard over the code can be satisfied and the data still wrong, which is
    the whole reason this project separates the two.

    This class asserted the **withholding** until the currency was settled —
    `fares == "withheld"`, and that no departure carried any key with
    `divebooker` in its name. Re-aimed rather than deleted: what made the
    withholding right was that `Offer.priceCurrency` is a static per-vessel
    label, and reading the page's own currency instead put 725 of 777 joined
    sailings on a figure one of the other two sellers also states. The rule
    each assertion below keeps is the same one — a figure reaches a row only
    where the seller stated it for that sailing, and it never reaches a row
    that seller does not sell.
    """

    def setUp(self):
        from published import raw
        self.payload = raw()

    def test_the_block_says_the_fares_are_published(self):
        block = self.payload.get("divebooker")
        if block is None:
            self.skipTest("no divebooker book is committed on this checkout")
        self.assertEqual(block["fares"], "published")

    def test_every_in_season_row_is_accounted_for(self):
        """Matched, on a hull we do not map, or a sailing we do not carry.

        Four buckets, and the fourth is why: `matched` counted every sailing
        landing on a row this dataset carries, which was a statement about the
        other two sellers until this one started founding rows of its own —
        after which 69 of its sailings matched *because it had put them there*
        and the figure read as agreement it had not earned.

        This asserted `matched == in_season` while the book was ten hulls the
        country page happened to link. The whole fleet is 92 and the equality
        is simply false now — 85 rows sit on hulls this site does not carry at
        all and 10 are dates our two sellers do not list. Neither is a
        publication, and an assertion that they cannot exist would have been an
        assertion about how little we had read. What may not happen is a row
        falling out of the accounting, because that is the count going quiet.
        """
        block = self.payload.get("divebooker")
        if block is None:
            self.skipTest("no divebooker book is committed on this checkout")
        self.assertEqual(
            block["matched"] + block["founded"] + block["on_unmapped_vessels"]
            + block["unmatched"],
            block["in_season"],
            "an in-season sailing is in none of the four buckets")
        self.assertLessEqual(
            block["unpriced"], block["unmatched"],
            "more sailings are explained away as unpriced than went "
            "unaccounted for, which is a reason larger than the thing it "
            "explains")
        self.assertIn(str(block["unmatched"]), block["note"])

    def test_every_published_fare_says_who_said_it_and_when(self):
        """No figure without a `Provenance`. The oldest rule here."""
        priced = 0
        for row in self.payload.get("departures", []):
            money = row.get("divebooker_price")
            if money is None:
                self.assertNotIn("divebooker_provenance", row,
                                 "a provenance with no price under it")
                continue
            priced += 1
            self.assertIn(money.get("currency"), {"EUR", "USD", "GBP"})
            self.assertGreater(money.get("amount") or 0, 0,
                               "a berth nobody offered has no price, and zero "
                               "would read as free")
            where = row.get("divebooker_provenance")
            self.assertIsNotNone(where, "a price with nobody's name on it")
            self.assertEqual(where["source_id"], "divebooker.com")
            self.assertTrue(where["retrieved"], "a claim with no date is not one")
        self.assertGreater(priced, 0, "the third seller reached no row at all")

    def test_the_block_s_own_count_is_the_number_of_rows_it_put_a_fare_on(self):
        """The coverage figure and the departures have to be one claim.

        `matched` is counted from the book and the rows are written from it in
        a different loop, so the two agreeing is the statement that the join
        the block reports is the join the dataset made. They were allowed to
        drift while nothing was published; now one of them is what a reader
        sees and the other is what this file says about it.
        """
        block = self.payload.get("divebooker")
        if block is None:
            self.skipTest("no divebooker book is committed on this checkout")
        priced = sum(1 for row in self.payload.get("departures", [])
                     if row.get("divebooker_price"))
        self.assertLessEqual(
            priced, block["matched"],
            "more rows carry a fare than the block says joined")

    def test_a_row_nobody_else_sells_never_carries_a_second_sellers_figure(self):
        """One seller's number in another's field reads as two agreeing."""
        for row in self.payload.get("departures", []):
            if not row.get("padi_only"):
                continue
            self.assertIsNone(row.get("padi_price"),
                              "PADI's own price repeated into PADI's field")


class TestADateWithTwoOffersKeepsTheCheapest(unittest.TestCase):
    """161 offers over 143 sailings, and the first one is not the fare.

    Red Sea Aggressor IV states 5,398 USD and 2,699 USD for 2027-07-24 — the
    same trip, the same seven nights — and taking whichever came first put the
    dearer one in the book, where it read as this source disagreeing with both
    other sellers by a factor of two. The bottom of the ladder is the price a
    reader can buy at.
    """

    def page(self, *prices, currency="USD"):
        blocks = [{
            "@type": "Offer", "price": p, "priceCurrency": currency,
            "availability": "https://schema.org/InStock",
            "itemOffered": {
                "@type": "TouristTrip", "name": "St. Johns / Daedalus",
                "subjectOf": {"@type": "Event", "name": "St. Johns / Daedalus",
                              "startDate": "2027-07-24", "endDate": "2027-07-31"},
            },
        } for p in prices]
        return "".join(
            f'<script type="application/ld+json">{json.dumps(b)}</script>'
            for b in blocks
        )

    def test_the_cheaper_offer_wins_whichever_came_first(self):
        for order in ((5398, 2699), (2699, 5398)):
            rows, _ = db.departures(self.page(*order))
            self.assertEqual(len(rows), 1, order)
            self.assertEqual(rows[0].price, 2699.0, order)
            self.assertEqual(rows[0].offers, 2, order)

    def test_a_single_offer_says_nothing_about_a_choice(self):
        rows, _ = db.departures(self.page(2699))
        self.assertEqual(rows[0].offers, 1)
        self.assertNotIn("offers", rows[0].as_dict())

    def test_two_currencies_on_one_date_do_not_race(self):
        """The smaller number would pick the currency, not the price."""
        page = self.page(2699) + self.page(2500, currency="EUR")
        rows, _ = db.departures(page)
        self.assertEqual(rows[0].price, 2699.0)
        self.assertEqual(rows[0].currency, "USD")


class TestTheSearchIsWalkedTheSiteSOwnWay(unittest.TestCase):
    """`p=` is the paginator, and page one is the URL a visitor gets.

    Nine other spellings — `page`, `pg`, `offset`, `start`, `skip`, `limit`,
    `perPage`, `size`, `take` — each returned the first twenty hulls again,
    with no error to say so. So the parameter is recorded with its
    measurement beside it rather than typed, and these assert the shape that
    measurement fixed: the season's four months, and a first page carrying no
    page number at all.
    """

    def test_page_one_is_the_visitors_url(self):
        self.assertEqual(db.search_path("202705"),
                         "/boatsearch?et=2&e=3881&ym=202705")
        self.assertEqual(db.search_path("202705", 1),
                         db.search_path("202705"))

    def test_later_pages_carry_the_measured_parameter(self):
        self.assertEqual(db.search_path("202706", 3),
                         "/boatsearch?et=2&e=3881&ym=202706&p=3")

    def test_the_season_is_asked_in_this_sellers_own_vocabulary(self):
        self.assertEqual(db.SEASON_YM, ("202705", "202706", "202707", "202708"))


class TestNoAliasContradictsTheNameBothSourcesState(unittest.TestCase):
    """A hand-maintained pair may go beyond the rule; it may not go against it.

    `tools/match_divebooker.py` states the rule the first ten pairs were made
    by — the vessel name divebooker states, normalised, equals one of ours —
    and it reproduces all ten, `silky` -> `dune-silky` included. What this
    guards is the other direction: a pair in the file that the rule can make
    *and disagrees with* is a typo, and a typo here serves one boat's
    departures under another boat's name with the row count still right. A
    slug the rule cannot pair is left alone, because a person reading two
    names is allowed to know something a string comparison does not.
    """

    ROOT = Path(__file__).resolve().parents[1]

    def setUp(self):
        import sys
        sys.path.insert(0, str(self.ROOT / "tools"))
        from match_divebooker import compare  # noqa: PLC0415
        self.compare = compare
        from published import raw  # noqa: PLC0415
        self.book = raw("divebooker.json")
        self.file = json.loads(
            (self.ROOT / "data" / "divebooker_aliases.json").read_text(
                encoding="utf-8"))
        self.aliases = self.file["aliases"]
        self.boats = raw()["boats"]

    def test_every_pair_the_rule_can_make_the_file_agrees_with(self):
        ours: dict[str, set[str]] = {}
        for boat in self.boats:
            for key in (self.compare(boat["id"]), self.compare(boat["name"])):
                ours.setdefault(key, set()).add(boat["id"])
        for slug, vessel in (self.book.get("vessels") or {}).items():
            if slug not in self.aliases:
                continue
            hits = ours.get(self.compare(vessel.get("name") or slug), set())
            if len(hits) == 1:
                self.assertEqual(
                    self.aliases[slug], next(iter(hits)),
                    f"{slug} states {vessel.get('name')!r}, which is one of "
                    f"our boats by name, and the alias file says otherwise")

    def test_every_alias_names_a_boat_this_site_carries_or_mints_one(self):
        """An alias points at a boat on the page, or at a hull only this
        seller lists.

        It asserted the first alone, which was true while every mapped hull
        was one of the other two sellers' boats. The 33 under `divebooker_only`
        are Egyptian liveaboards neither of them carries, and 27 of them
        publish a page and no departure inside the published season — so they
        have an id and no boat, which is the `padi_only` shape exactly: an id
        is a commitment, and a hull that starts selling next week should
        arrive under one a person chose rather than one a run invented.

        What may not happen is an alias pointing at neither, because that is a
        slug this file has silently stopped resolving.
        """
        ids = {boat["id"] for boat in self.boats}
        minted = set(self.file.get("divebooker_only") or ())
        for slug, boat_id in self.aliases.items():
            self.assertTrue(
                boat_id in ids or boat_id in minted,
                f"{slug} maps to {boat_id!r}, which is neither a boat on the "
                f"page nor a hull listed under divebooker_only")
        for slug in minted:
            self.assertEqual(self.aliases.get(slug), slug,
                             f"{slug} is minted and the alias does not agree")


class TestTheWalkStopsOnWhatItSeesNotOnACount(unittest.TestCase):
    """A month ends on an empty page or on a repeat, and never on a number.

    The failure this guards is the one the measurement found: nine wrong
    paging parameters each return the **first page again**, silently. A walk
    that trusted `p=` without watching what came back would follow that
    forever and report the same twenty boats as a whole fleet.
    """

    def walk(self, pages, months=("202705", "202706"), **kw):
        return db.walk_search(lambda path: pages.get(path), months, **kw)

    def link(self, *slugs):
        return "".join(f'<a href="/{s}">x</a>' for s in slugs)

    def test_a_repeat_of_this_months_first_page_ends_the_month(self):
        pages = {db.search_path("202705", 1): self.link("a-haz1", "b-haz2"),
                 db.search_path("202705", 2): self.link("c-haz3"),
                 db.search_path("202705", 3): self.link("a-haz1", "b-haz2"),
                 db.search_path("202705", 4): self.link("d-haz4")}
        found, _ = self.walk(pages, months=("202705",))
        self.assertEqual(found, ["/a-haz1", "/b-haz2", "/c-haz3"])

    def test_an_empty_page_ends_the_month(self):
        pages = {db.search_path("202705", 1): self.link("a-haz1"),
                 db.search_path("202705", 2): "",
                 db.search_path("202705", 3): self.link("z-haz9")}
        found, _ = self.walk(pages, months=("202705",))
        self.assertEqual(found, ["/a-haz1"])

    def test_a_month_that_repeats_the_previous_months_page_still_walks(self):
        """Seen-before is per month, or month two would stop at page one."""
        pages = {db.search_path("202705", 1): self.link("a-haz1"),
                 db.search_path("202705", 2): "",
                 db.search_path("202706", 1): self.link("a-haz1"),
                 db.search_path("202706", 2): self.link("b-haz2"),
                 db.search_path("202706", 3): ""}
        found, _ = self.walk(pages)
        self.assertEqual(found, ["/a-haz1", "/b-haz2"])

    def test_a_paginator_that_never_repeats_is_bounded_and_says_so(self):
        class Endless(dict):
            def get(self, path, default=None):  # noqa: D102
                page = path.rsplit("=", 1)[-1]
                return f'<a href="/boat-{page}-haz{page}">x</a>'
        found, notes = db.walk_search(Endless().get, ["202705"], max_pages=4)
        self.assertEqual(len(found), 4)
        self.assertTrue(any("does not claim the month is complete" in n
                            for n in notes), notes)

    def test_a_page_that_cannot_be_read_ends_the_month_and_says_so(self):
        pages = {db.search_path("202705", 1): self.link("a-haz1")}
        found, notes = self.walk(pages, months=("202705",))
        self.assertEqual(found, ["/a-haz1"])
        self.assertTrue(any("unread" in n for n in notes), notes)


class TestThePageSaysWhatCurrencyItIsIn(unittest.TestCase):
    """`Offer.priceCurrency` is a label; the payload is the money.

    Measured over four hulls on 2026-09-20: Seawolf Steel, Unity and Iceberg
    label every offer EUR on a page whose own payload states
    `{"currencies":{"current":"USD"}}`, with USD the only currency code in
    those bytes and a rates table keyed by currency id (`"2":"0.8708"`). Read
    by the label, a dollar figure travels under a euro name — and a euro name
    is what a total would convert, so every such row would be 15% wrong in the
    direction nobody would notice.
    """

    OFFER = {
        "@type": "Offer", "price": 1653, "priceCurrency": "EUR",
        "availability": "https://schema.org/InStock",
        "itemOffered": {
            "@type": "TouristTrip", "name": "North",
            "subjectOf": {"@type": "Event", "name": "North",
                          "startDate": "2027-05-01", "endDate": "2027-05-08"}},
    }
    # The shape a runner really served: the RSC chunks are JSON string
    # literals, so the payload's own quotes arrive backslashed.
    PAYLOAD = ('<script>self.__next_f.push([1,"9:[\\"$\\",\\"div\\",'
               '{\\"currencies\\":{\\"current\\":\\"USD\\",'
               '\\"currentSymb\\":\\"USD\\"}}]"])</script>')

    def page(self, payload: str = "") -> str:
        return (f'<script type="application/ld+json">{json.dumps(self.OFFER)}'
                f'</script>{payload}')

    def test_the_payload_is_read_through_its_escaping(self):
        self.assertEqual(db.page_currency(self.PAYLOAD), "USD")
        self.assertEqual(db.page_currency('{"currencies":{"current":"EUR"}}'),
                         "EUR")
        self.assertIsNone(db.page_currency("<html></html>"))

    def test_the_page_beats_the_label_and_says_it_did(self):
        rows, warnings = db.departures(self.page(self.PAYLOAD))
        self.assertEqual(rows[0].currency, "USD")
        self.assertEqual(rows[0].price, 1653.0)
        self.assertTrue(any("labelled EUR" in w for w in warnings), warnings)

    def test_a_page_that_states_nothing_leaves_the_label_alone(self):
        """Silence is not a currency. Then the label is all that was said."""
        rows, warnings = db.departures(self.page())
        self.assertEqual(rows[0].currency, "EUR")
        self.assertEqual(warnings, [])


class TestThePriceDetailsPanelIsAFeeBook(unittest.TestCase):
    """Every line below is verbatim from the fleet census, run 35533600353.

    Four wordings across 792 priced obligatory lines, and each of them is a
    way this reader could be wrong:

    * the separator is a spaced dash on some hulls and a colon on others;
    * the currency comes after the figure (`10EUR`) or before it (`€45`);
    * the amount is a range as often as not — collapsing `125-250` to its low
      end understates the bill, which is the failure this project exists to
      correct;
    * and the unit is stated, or is *`per person`*, which says who pays and
      not how often.
    """

    def read(self, line, required=True):
        fee, unread = db._read_fee_line(line, required)
        return fee

    def test_the_two_separators_both_yield_a_label(self):
        dash = self.read("Port fees - 50 USD per person (to be paid on board)")
        colon = self.read("Fuel Surcharge: 10EUR per day, to be paid on board")
        self.assertEqual(dash.label, "Port fees")
        self.assertEqual(colon.label, "Fuel Surcharge")
        self.assertEqual((dash.low, dash.currency), (50.0, "USD"))
        self.assertEqual((colon.low, colon.currency), (10.0, "EUR"))

    def test_a_hyphen_inside_a_label_is_not_a_separator(self):
        """An unspaced dash belongs to the word, so only a spaced one separates.

        Asserted on the rule rather than through a fee, because the fleet's own
        hyphenated wording — *Check dive*, 14 entries — is one this project's
        vocabulary declines, and inventing a label to test a separator would
        make the guard about a charge nobody bills.
        """
        self.assertEqual(db.FEE_SEPARATOR.sub("", "Check-dive:"), "Check-dive")
        self.assertEqual(db.FEE_SEPARATOR.sub("", "Port fees -"), "Port fees")
        self.assertEqual(db.FEE_SEPARATOR.sub("", "Nitrox"), "Nitrox")

    def test_a_range_keeps_both_ends(self):
        fee = self.read("Marine Park, Port Fees and Permissions - 125-250 EUR "
                        "per person (to be paid on board)")
        self.assertEqual((fee.low, fee.high), (125.0, 250.0))
        self.assertTrue(fee.is_range)

    def test_per_person_is_a_payer_and_not_a_period(self):
        """The reason the fleet census reported 507 lines as `per person`.

        `165-240 EUR per person per trip` states both, and a reader that stops
        at the first `per` reads the payer and calls the period unstated — so
        the payer phrase comes out before the unit is looked for.
        """
        both = self.read("Marine Park fees, harbour fees and fuel surcharge - "
                         "165-240 EUR per person per trip (to be paid on board)")
        self.assertIs(both.basis, db.FeeBasis.PER_TRIP)
        self.assertFalse(both.unit_unstated)

        payer_only = self.read("Port fees - 50 USD per person (to be paid on board)")
        self.assertTrue(payer_only.unit_unstated)
        self.assertEqual(payer_only.low, 50.0, "the figure is the seller's and stays")

    def test_a_unit_this_project_cannot_scale_is_not_invented(self):
        """One fill per dive is ordinary and it is still a derivation."""
        fee = self.read("Nitrox fill - 8 EUR per tank", required=False)
        self.assertTrue(fee.unit_unstated)
        self.assertEqual(fee.low, 8.0)

    def test_the_seller_s_own_column_decides_the_tier(self):
        obligatory = self.read("Port fees: 25 EUR per trip", required=True)
        optional = self.read("Land excursions: 25 EUR per trip", required=False)
        inclusion = self.read("Nitrox", required=None)
        self.assertIs(obligatory.tier, db.FeeTier.MANDATORY)
        self.assertIs(optional.tier, db.FeeTier.OPTIONAL)
        self.assertTrue(inclusion.included)
        self.assertIsNone(inclusion.low, "an inclusion is an answer, not a figure")

    def test_a_figure_with_no_currency_never_becomes_a_price(self):
        """`14% GST` must not become 14 of anything — `padi_com`'s rule."""
        fee, unread = db._read_fee_line(
            "14% GST applicable to all onboard payments", False)
        self.assertIsNone(fee)
        self.assertIsNone(unread, "no money was stated, so nothing went unread")

    def test_a_bare_amount_is_not_a_charge(self):
        """The census turned up `$44`, `$43` and `$46` as whole lines."""
        self.assertIsNone(self.read("$44", required=False))

    def test_a_priced_line_nothing_can_name_is_reported(self):
        """Counted is not enough: what it needs is the word, not the number."""
        fee, unread = db._read_fee_line("Bonex scooter rental - 90 EUR per trip",
                                        False)
        self.assertIsNone(fee)
        self.assertEqual(unread, "Bonex scooter rental - 90 EUR per trip")

    def test_an_unpriced_amenity_is_not_a_hole_in_the_book(self):
        """5,244 inclusion lines, and Water and Free WiFi are among them."""
        fee, unread = db._read_fee_line("Free WiFi", None)
        self.assertIsNone(fee)
        self.assertIsNone(unread)

    def test_a_stated_amount_beats_an_inclusion_which_beats_a_blank(self):
        priced = self.read("Nitrox: 50 EUR per trip", required=True)
        included = self.read("Nitrox", required=None)
        blank = self.read("Nitrox", required=False)
        self.assertEqual(
            [db._rank(f) for f in (blank, included, priced)], [0, 1, 2])


PANELS = Path(__file__).resolve().parent / "fixtures" / "divebooker-price-details.json"


def payload_page(nodes) -> str:
    """The panels put back the way the site streams them.

    App Router ships its data as JSON **string literals** inside
    `self.__next_f.push([1, "…"])`, so the payload's own quotes arrive
    backslashed. A fixture that skipped that would prove a regex against
    pretty-printed bytes no server ever sent, which is the reason this module
    probes before it parses.
    """
    body = json.dumps({"trips": list(nodes)}, ensure_ascii=False)
    return ('<script>self.__next_f.push([1,' + json.dumps(body) + '])</script>')


class TestThePanelIsReadOffBytesTheSiteServed(unittest.TestCase):
    """Red Sea Aggressor II's and Amelie's *Price details*, verbatim.

    Both pairs are exactly what the runner printed on 2026-09-21 — the panel
    and the `name` of the trip that holds it, together — wrapped the way
    `divebooker-bella-2.jsonld.json` wraps its blocks into a page.

    The names in the first version of this file were wrong, and the way they
    were wrong is the finding: they came from a probe's earlier output and read
    *Northern Red Sea - Best Wreck Diving*, so this project believed the panel
    names a trip in a vocabulary of its own. It does not. `name` here is the
    same wording the sailings use, which is why the whole-fleet join matched
    603 of 605 exactly and why the fee book can key on it at all.

    Two hulls because they are the two line shapes the fleet census found, and
    a reader that handles one handles neither by accident: a spaced dash with
    the currency after the figure and no period stated, and a colon with the
    currency against the figure and the period spelled out.
    """

    def setUp(self):
        self.nodes = json.loads(PANELS.read_text(encoding="utf-8"))
        self.blocks, self.warnings = db.fee_blocks(payload_page(self.nodes))

    def codes(self, block):
        return {fee.code.value: fee for fee in block.fees}

    def test_each_panel_is_found_and_named_for_its_trip(self):
        self.assertEqual(len(self.blocks), 3, self.warnings)
        self.assertEqual([b.trip for b in self.blocks],
                         ["Northern Red Sea, Ras Mohamed, Straits of Tiran",
                          "Best of Hurghada",
                          "Mini Safari: Wrecks & Reefs"],
                         "the panel is named for its own heading rather than "
                         "for the trip that holds it")
        self.assertEqual([b.nights for b in self.blocks], [7, 3, 3])
        self.assertEqual(self.warnings, [])

    def test_a_trip_states_its_own_dives_and_entry_bar(self):
        """Both read from the object the panel sits in, for nothing.

        `numberDives` and `requirements.expirience.text` are that source's own
        keys and its own spellings. The count is the figure it wrote and never
        one derived from the length or the day plan — ten vessels publish one
        and they state 15 to 21 for the same seven-night week.
        """
        aml = self.blocks[2]
        self.assertEqual(aml.dives, 9)
        self.assertEqual(aml.requirements, "Minimum 0 dives")
        # The two hulls whose fixture carries no such fields say nothing
        # rather than nothing-shaped: an unread trip must not carry a
        # requirement nobody stated.
        self.assertIsNone(self.blocks[0].dives)
        self.assertIsNone(self.blocks[0].requirements)

    def test_a_trip_states_its_two_harbours_as_two_fields(self):
        """`departurePort` and `arrivalPort`, kept apart because they arrive apart.

        **A joined string is not a record.** PADI states the same pair and this
        project once stored it joined; two of that source's eight harbour
        names contain the separator, so it could never be split back and
        nothing read it. Two fields in, two fields out.
        """
        aml = self.blocks[2]
        self.assertEqual((aml.port_from, aml.port_to), ("Hurghada", "Hurghada"))
        # And nothing is invented for a trip that stated neither.
        self.assertIsNone(self.blocks[0].port_from)
        self.assertIsNone(self.blocks[0].port_to)

    def test_a_sentence_about_the_data_is_not_a_harbour(self):
        """This source writes *"Port is not stated"* into the port field.

        Four panels of 591 on the fleet census of 2026-09-21. A sentence about
        the data sitting where a place name goes would have shipped as a
        *Departs from* chip nobody can sail from — and worse, as the pair that
        passes the both-ends-stated test the row is otherwise protected by.
        Matched whole, never as a substring: a rule broad enough to catch "not
        stated" anywhere catches a marina whose name contains it.
        """
        said = {"name": "Port is not stated",
                "url": "https://divebooker.com/egypt-eaz1"}
        self.assertIsNone(db._stated_name(said))
        self.assertIsNone(db._stated_name({"name": "  PORT IS NOT STATED "}))
        self.assertEqual(db._stated_name({"name": "Hurghada"}), "Hurghada")

    def test_a_count_is_a_stated_figure_or_nothing(self):
        self.assertEqual(db._stated_count("9 dives"), 9)
        self.assertEqual(db._stated_count("21 dives"), 21)
        self.assertIsNone(db._stated_count(""))
        self.assertIsNone(db._stated_count(None))
        self.assertIsNone(db._stated_count("a few dives"))

    def test_the_gear_bundle_is_named_and_the_singles_are_not(self):
        """Adding up singles invents a basket the operator never sold.

        Aml Hayaty prices a wetsuit, a BCD, a computer, a torch, an SMB and a
        mask-fins-snorkel set beside one *Full Equipment set* at €130. The
        bundle is the honest gear price and the only one read; the singles are
        declined and reported, so a reader of `unnamed_fees` sees what was
        left rather than a silence.
        """
        aml = self.blocks[2]
        gear = [f for f in aml.fees if f.code.value == "gear_rental"]
        self.assertEqual(len(gear), 1)
        self.assertEqual(gear[0].low, 130.0)
        self.assertIs(gear[0].basis, db.FeeBasis.PER_TRIP)
        self.assertTrue(any("BCD" in line for line in aml.unnamed))
        self.assertFalse(any("Full Equipment set" in line for line in aml.unnamed))

    def test_a_declined_label_says_which_column_it_came_from(self):
        """Two findings, not one.

        A word missing from an obligatory line is what keeps a bill from
        adding up; one missing from *Extra cost* is a course or a massage and
        costs the total nothing. Reported together and told apart — filtering
        either out here would hide a charge going unread.
        """
        aml = self.blocks[2]
        self.assertTrue(any(line.startswith("[extra] BCD") for line in aml.unnamed),
                        aml.unnamed)
        self.assertTrue(all(line.startswith(("[owed] ", "[extra] "))
                            for line in aml.unnamed), aml.unnamed)

    def test_three_spellings_the_fleet_census_found(self):
        """Each is the operator's own word, each counted before it was added.

        *Fuel Charge* 24 lines, *Crew Gratitude* 32, *Route suplement* 13, on
        the whole-fleet read of 2026-09-21. None of them reaches the table by
        the route its correctly-spelled sibling does: `gratuit\w*` stems on
        *gratuit* and cannot see *gratitude* at all.
        """
        from liveaboard.scrape.fees import classify_label
        for label, code in (("Fuel Charge", "fuel_surcharge"),
                            ("Crew Gratitude", "gratuities"),
                            ("Route suplement", "route_supplement")):
            with self.subTest(label=label):
                found = classify_label(label, prose=False)
                self.assertIsNotNone(found, f"{label!r} is still declined")
                self.assertEqual(found.value, code)
        # And the near-misses each one was kept narrow to avoid.
        self.assertIsNone(classify_label("Gratitude", prose=False))

    def test_a_charge_the_seller_calls_mandatory_is_named_even_unscalable(self):
        """*Government fees* — 35 lines, six Sea Serpent hulls, its own code.

        Not the park fee's: every one of those trips bills a separate
        `marine_park` line, so folding it either doubles a charge or deletes
        one. Not `LOCAL_FEES` either — what it is for is not stated and this
        project does not decide; who levies it is.

        Naming it is what puts it in the bill at all. An unnamed priced line is
        dropped outright, so the charge reached no reader; named, it is carried
        and printed and marked, and `unit_unstated` keeps it out of every
        total. A charge a seller calls mandatory belongs in the breakdown even
        where this project cannot scale it.
        """
        from liveaboard.scrape.fees import classify_label
        found = classify_label("Government fees", prose=False)
        self.assertIsNotNone(found, "the charge is still dropped")
        self.assertEqual(found.value, "government_fee")
        # It must not become the park fee, which those trips bill separately.
        self.assertEqual(classify_label("National park fees", prose=False).value,
                         "marine_park")
        # And the existing combined wording keeps the code it already had.
        self.assertEqual(
            classify_label("Environmental/Government Fee", prose=False).value,
            "environment_tax")

        fee, unread = db._read_fee_line(
            "Government fees - 100 EUR per person (for trips from January, 2027)",
            True)
        self.assertIsNotNone(fee)
        self.assertEqual(fee.low, 100.0)
        self.assertTrue(fee.unit_unstated, "a payer is not a period")
        self.assertIsNone(unread, "a named charge is not also reported unread")

    def test_a_title_naming_two_charges_is_one_line_carrying_both(self):
        """`COMBINED_FEES`, which is what this project does with such a title.

        Not a refusal: splitting *Port & Permission fees: 150.00EUR* between a
        port and a permit invents two prices nobody quoted, so it stays one
        line at the whole figure. Both of these were declining, and each was
        the *entire* obligatory column of the panel it sat in — Tala's on 12
        panels, Royal Evolution's on 9 — so the line going unread took the
        whole bill with it.
        """
        from liveaboard.scrape.fees import classify_label
        for label in ("Route fees and enviromental taxes",
                      "Port & Permission fees"):
            with self.subTest(label=label):
                found = classify_label(label, prose=False)
                self.assertIsNotNone(found, f"{label!r} is still declined")
                self.assertEqual(found.value, "combined_fees")
        # A single component keeps its own code: one part is not a bundle.
        self.assertEqual(classify_label("Port fees", prose=False).value,
                         "port_fees")
        self.assertEqual(classify_label("Route supplement", prose=False).value,
                         "route_supplement")

    def test_a_figure_inside_a_bracket_keeps_its_label(self):
        """`Gratuities (€70)` came out labelled `Gratuities (`."""
        tips = [f for f in self.blocks[2].fees if f.code.value == "gratuities"]
        self.assertEqual([f.label for f in tips], ["Gratuities"])
        self.assertEqual(tips[0].low, 70.0)

    def test_one_obligatory_line_naming_two_charges_totals_once(self):
        aml = self.codes(self.blocks[2])
        self.assertIn("combined_fees", aml)
        self.assertEqual(aml["combined_fees"].low, 70.0)
        self.assertIs(aml["combined_fees"].basis, db.FeeBasis.PER_TRIP)
        self.assertTrue(self.blocks[2].complete)

    def test_the_obligatory_column_is_mandatory_and_the_extras_are_not(self):
        fees = self.codes(self.blocks[0])
        for code in ("port_fees", "marine_park", "fuel_surcharge"):
            self.assertIs(fees[code].tier, db.FeeTier.MANDATORY, code)
        self.assertIsNot(fees["gratuities"].tier, db.FeeTier.MANDATORY)

    def test_a_figure_with_a_payer_and_no_period_totals_nothing(self):
        """"50 USD per person" is who pays, not how often."""
        fees = self.codes(self.blocks[0])
        for code in ("port_fees", "marine_park", "fuel_surcharge"):
            self.assertTrue(fees[code].unit_unstated, code)
            self.assertIsNotNone(fees[code].low, "the seller's figure is kept")
        self.assertFalse(self.blocks[0].complete,
                         "a bill whose mandatory lines cannot be scaled is "
                         "not a bill this site may total")

    def test_a_stated_period_is_read_and_the_bill_then_adds_up(self):
        fees = self.codes(self.blocks[1])
        self.assertIs(fees["fuel_surcharge"].basis, db.FeeBasis.PER_DAY)
        self.assertIs(fees["marine_park"].basis, db.FeeBasis.PER_DAY)
        self.assertIs(fees["port_fees"].basis, db.FeeBasis.PER_TRIP)
        self.assertEqual(
            [fees[c].low for c in ("fuel_surcharge", "marine_park", "port_fees")],
            [10.0, 15.0, 25.0])
        self.assertTrue(self.blocks[1].complete)

    def test_the_free_tank_size_is_not_the_charged_one(self):
        """`12l tanks and weights` is what the operator gives you.

        It read as `TANK_15L` and outranked the *Extra cost* line naming the
        15-litre upgrade, which published this boat's charged tanks as
        included — turning a charge into free, the one error the label table's
        own comment says it must never make.
        """
        fees = self.codes(self.blocks[0])
        self.assertIn("tank_15l", fees)
        self.assertFalse(fees["tank_15l"].included,
                         "the 15-litre upgrade is stated as included")

    def test_an_inclusion_is_an_answer_and_carries_no_figure(self):
        included = [f for f in self.blocks[1].fees if f.included]
        self.assertTrue(included, "the inclusion column read as nothing")
        for fee in included:
            self.assertIsNone(fee.low)

    def test_a_bare_amount_line_is_not_a_charge(self):
        """Amelie's whole *Extra cost* column is the string `$3f`."""
        self.assertEqual(self.blocks[1].unnamed, [])
        self.assertNotIn("3", "".join(f.label for f in self.blocks[1].fees))


class TestTwoPanelsUnderOneKeyRefuseBoth(unittest.TestCase):
    """Red Sea Aggressor IV's real shape, and the one case that is not a clash.

    Its page files *St. Johns / Daedalus (7 nights)* **twice**, with
    byte-identical columns, so a repeated key is not by itself a contradiction
    — refusing on the repeat alone would throw away a bill the seller states
    perfectly clearly. Only differing content is a clash, and there nothing can
    say which bill a sailing gets, so the fee book is dropped rather than
    picked from. `promote.itinerary_key` cost this project that lesson once.

    The trip facts go with it: a dive count read off a panel this code cannot
    attach is a claim about a trip it cannot identify.
    """

    def panel(self, name, nights, port_fee, dives="9 dives"):
        return {
            "name": f"{name} ({nights} nights) (Marsa Alam-Marsa Alam)",
            "numberDives": dives,
            "details": {"title": "Price details", "columns": [{
                "type": "notincluded", "title": "Obligatory surcharges",
                "text": f"Port Fee: {port_fee}EUR per trip, to be paid on board",
            }]},
        }

    def book(self, *panels):
        html = payload_page(list(panels)) + (
            '<script type="application/ld+json">'
            + json.dumps({"@type": "Event", "name": "St. Johns / Daedalus",
                          "startDate": "2027-05-01", "endDate": "2027-05-08",
                          "offers": {"@type": "Offer", "price": "2000",
                                     "priceCurrency": "EUR",
                                     "availability": "https://schema.org/InStock"}})
            + "</script>")
        return db.vessel(html, "/red-sea-aggressor-iv-haz426")

    def test_an_identical_repeat_is_not_a_clash(self):
        book = self.book(self.panel("St. Johns / Daedalus", 7, 25),
                         self.panel("St. Johns / Daedalus", 7, 25))
        self.assertIn("St. Johns / Daedalus::7", book.fees)
        self.assertEqual(
            [w for w in book.warnings if "two different price panels" in w], [])

    def test_two_different_panels_under_one_key_drop_both(self):
        book = self.book(self.panel("St. Johns / Daedalus", 7, 25),
                         self.panel("St. Johns / Daedalus", 7, 90))
        self.assertNotIn("St. Johns / Daedalus::7", book.fees,
                         "the second panel silently won")
        self.assertNotIn("St. Johns / Daedalus::7", book.trips,
                         "the trip facts outlived the bill they came with")
        self.assertTrue(
            any("two different price panels" in w for w in book.warnings),
            book.warnings)

    def test_the_same_name_at_two_lengths_is_not_a_clash(self):
        """Which is the whole reason the length is in the key."""
        book = self.book(self.panel("St. Johns / Daedalus", 7, 25),
                         self.panel("St. Johns / Daedalus", 9, 90))
        self.assertIn("St. Johns / Daedalus::7", book.fees)
        self.assertEqual(
            [w for w in book.warnings if "two different price panels" in w], [])


class TestTheFeeBookReachesTheTripThroughItsDates(unittest.TestCase):
    """`promote._divebooker_fees`, over a book shaped like the real one.

    The join is the part that cannot be read off the parser: three sellers
    spell one week three ways, so this source's panel is found through the
    *dates* its sailings share with our itinerary and never through a name.
    Every step is an equality on something with no spelling.

    Built rather than landed. The real book is 887 KB and the only channel
    from a runner to this sandbox is a job log, so carrying it back to prove a
    dict lookup would be 47 KB of base64 for an assertion a fixture makes
    better. What is verbatim here is the *shape* — `{trip: {lines, complete}}`
    per hull, keyed on that seller's own slug — which is what
    `VesselBook.as_dict` writes and what the runner's own census printed.
    """

    LINES = [{"code": "port_fees", "tier": "mandatory", "basis": "per_trip",
              "included": False, "amount": {"amount": 25.0, "currency": "EUR"}}]
    LONGER = [{"code": "port_fees", "tier": "mandatory", "basis": "per_trip",
               "included": False, "amount": {"amount": 60.0, "currency": "EUR"}}]
    #: Keyed by `fee_key` -- the trip **and its length** -- because that seller
    #: sells one name at two lengths with two different bills. *Best of
    #: Hurghada* at 7 nights and at 3 is the shape Red Sea Aggressor IV really
    #: has, and a fixture that carried only one length would pass whether or
    #: not the length is in the key.
    BOOK = {
        "aml-hayaty": {
            "Best of Hurghada::3": {"lines": LINES, "complete": True},
            "Best of Hurghada::7": {"lines": LONGER, "complete": True},
            "Deep South::7": {"lines": [], "complete": False},
        },
    }
    SAILINGS = {
        "aml-hayaty::2027-05-01": {"boat": "aml-hayaty", "trip": "Best of Hurghada",
                                   "nights": 3},
        "aml-hayaty::2027-05-08": {"boat": "aml-hayaty", "trip": "Best of Hurghada",
                                   "nights": 3},
        "aml-hayaty::2027-05-15": {"boat": "aml-hayaty", "trip": "Best of Hurghada",
                                   "nights": 7},
        "aml-hayaty::2027-06-01": {"boat": "aml-hayaty", "trip": "Deep South",
                                   "nights": 7},
        "aml-hayaty::2027-07-01": {"boat": "aml-hayaty", "trip": "Unlisted week",
                                   "nights": 7},
    }

    def fees(self, *starts):
        from liveaboard.promote import _divebooker_fees
        return _divebooker_fees([{"start": s} for s in starts],
                                "aml-hayaty", self.SAILINGS, self.BOOK)

    def test_one_trip_name_at_two_lengths_is_two_bills(self):
        """The failure this key was rewritten for.

        Red Sea Aggressor IV sells *Brothers - Daedalus - Elphinstone* as a
        7-night week and a 9-night one, with a different panel on each, and a
        key of the name alone handed every sailing whichever the page emitted
        last -- 142 of that boat's 143.
        """
        self.assertEqual(self.fees("2027-05-01")["lines"][0]["amount"]["amount"],
                         25.0)
        self.assertEqual(self.fees("2027-05-15")["lines"][0]["amount"]["amount"],
                         60.0)

    def test_two_lengths_under_one_itinerary_attach_neither(self):
        """Same rule as two panels: a bill neither of their weeks quotes."""
        self.assertIsNone(self.fees("2027-05-01", "2027-05-15"))

    def test_a_trip_whose_departures_all_name_one_panel_gets_it(self):
        found = self.fees("2027-05-01", "2027-05-08")
        self.assertIsNotNone(found)
        self.assertTrue(found["complete"])
        self.assertEqual(found["lines"][0]["code"], "port_fees")

    def test_every_line_says_who_published_it(self):
        line = self.fees("2027-05-01")["lines"][0]
        self.assertEqual(line["provenance"]["source_id"], "divebooker.com")
        self.assertIsNot(line["provenance"],
                         self.BOOK["aml-hayaty"]["Best of Hurghada::3"]["lines"][0]
                         .get("provenance"),
                         "the book's own line was mutated in place")

    def test_two_panels_under_one_itinerary_attach_neither(self):
        """A bill assembled from two of their trips is a bill neither quotes."""
        self.assertIsNone(self.fees("2027-05-01", "2027-06-01"))

    def test_a_trip_this_seller_has_no_panel_for_claims_nothing(self):
        """`None` is "nobody looked", which is not "there are no fees"."""
        self.assertIsNone(self.fees("2027-07-01"))
        self.assertIsNone(self.fees("2027-09-09"))

    def test_an_empty_panel_is_a_disclosure_and_not_a_gap(self):
        """The seller saying the fare covers everything, which is an answer."""
        found = self.fees("2027-06-01")
        self.assertIsNotNone(found)
        self.assertEqual(found["lines"], [])
        self.assertFalse(found["complete"])


class TestTheThirdBillAddsUpOrShowsNothing(unittest.TestCase):
    """`pricing.divebooker_lines`, which is where a third total comes from.

    The rule it enforces is the one every seller here is held to: **a total
    built from part of a disclosure is the thing this site was built to catch
    other people doing**. So a bill reaches the page whole or not at all, and
    the row prints a berth price with a sentence instead.
    """

    def build(self, *, complete, fees=None):
        from datetime import date as _date
        from liveaboard.models import Departure, FeeItem, Itinerary, Provenance
        from liveaboard.money import Money
        from liveaboard.taxonomy import FeeBasis, FeeCode, FeeTier, SourceKind

        where = Provenance(kind=SourceKind.SCRAPED, source_id="divebooker.com")
        theirs = fees if fees is not None else [
            FeeItem(code=FeeCode.PORT_FEES, tier=FeeTier.MANDATORY,
                    amount=Money(25, "EUR"), basis=FeeBasis.PER_TRIP,
                    provenance=where),
            FeeItem(code=FeeCode.MARINE_PARK, tier=FeeTier.MANDATORY,
                    amount=Money(15, "EUR"), basis=FeeBasis.PER_DAY,
                    provenance=where),
        ]
        itinerary = Itinerary(
            id="t", name="Best of Hurghada", operator_id="o", boat_id="b",
            nights=3, dives=0, port_from="Hurghada", port_to="Hurghada",
            # The vessel's own optional lines, which this column takes rather
            # than the seller's: nitrox and gear are billed on board out of one
            # price list, to whoever walked up the gangway.
            fees=[FeeItem(code=FeeCode.NITROX, tier=FeeTier.CONDITIONAL,
                          amount=Money(50, "EUR"), basis=FeeBasis.PER_TRIP,
                          provenance=where)],
            divebooker_fees=theirs, divebooker_fees_complete=complete)
        departure = Departure(
            id="d", itinerary_id="t",
            start=_date(2027, 5, 1), end=_date(2027, 5, 4),
            price=Money(600, "EUR"), price_provenance=where,
            divebooker_price=Money(640, "EUR"), divebooker_provenance=where)
        return itinerary, departure

    def fx(self):
        """A one-currency table, built rather than loaded.

        The committed dataset is behind `tests/published.py` and this needs no
        rate from it: every figure here is already in euro, so what a real
        table would add is a conversion of 1.0 and a dependency on the day it
        was quoted.
        """
        from liveaboard.money import FxTable
        return FxTable({})

    def test_an_incomplete_book_produces_no_third_total(self):
        from liveaboard.pricing import divebooker_lines
        itinerary, _ = self.build(complete=False)
        self.assertIsNone(divebooker_lines(itinerary, self.fx()))

    def test_a_complete_book_bills_its_own_mandatory_rows(self):
        from liveaboard.pricing import divebooker_lines
        itinerary, _ = self.build(complete=True)
        lines = divebooker_lines(itinerary, self.fx())
        codes = [line.code.value for line in lines]
        self.assertIn("port_fees", codes)
        self.assertIn("marine_park", codes)

    def test_the_vessels_own_optional_lines_come_with_it(self):
        """Nitrox and gear are the boat's charge on board, whoever sold the
        berth — so they are in every seller's column and from one book."""
        from liveaboard.pricing import divebooker_lines
        itinerary, _ = self.build(complete=True)
        self.assertIn("nitrox",
                      [line.code.value for line in divebooker_lines(itinerary, self.fx())])

    def test_a_code_this_seller_calls_mandatory_beats_the_vessels_optional_one(self):
        """The New Sambo case: one code, two books, two tiers.

        Replacing this seller's required charge with the other's optional copy
        would leave its total short of something it publishes as owed.
        """
        from liveaboard.models import FeeItem, Provenance
        from liveaboard.money import Money
        from liveaboard.pricing import divebooker_lines
        from liveaboard.taxonomy import FeeBasis, FeeCode, FeeTier, SourceKind
        where = Provenance(kind=SourceKind.SCRAPED, source_id="divebooker.com")
        itinerary, _ = self.build(complete=True, fees=[
            FeeItem(code=FeeCode.NITROX, tier=FeeTier.MANDATORY,
                    amount=Money(35, "EUR"), basis=FeeBasis.PER_TRIP,
                    provenance=where)])
        nitrox = [line for line in divebooker_lines(itinerary, self.fx())
                  if line.code.value == "nitrox"]
        self.assertEqual(len(nitrox), 1, "the same charge reached the bill twice")
        self.assertEqual(float(nitrox[0].quoted.amount), 35.0,
                         "the vessel's optional copy replaced a required charge")

    def test_the_berth_line_is_this_sellers_and_says_so(self):
        from liveaboard.pricing import divebooker_base_line
        _, departure = self.build(complete=True)
        line = divebooker_base_line(departure, self.fx())
        self.assertIn("divebooker", line.label)
        self.assertEqual(line.provenance.source_id, "divebooker.com")
        self.assertEqual(float(line.quoted.amount), 640.0)

    def test_a_sailing_this_seller_does_not_list_has_no_berth_line(self):
        """A berth nobody offered has no price, and a zero would read as free."""
        from dataclasses import replace
        from liveaboard.pricing import divebooker_base_line
        _, departure = self.build(complete=True)
        self.assertIsNone(
            divebooker_base_line(replace(departure, divebooker_price=None),
                                 self.fx()))


class TestARowThisSellerFoundedStatesItsHarbours(unittest.TestCase):
    """The 33 hulls only this seller lists, end to end through `promote`.

    Such a row has no liveaboard.com title to parse a port pair out of and no
    PADI trip to ask, so every one of the 69 it founded read **Unknown** at
    both ends — on a page whose *Departs from* bank is what a reader filters
    the fleet with. The source states them, in two fields, in the object its
    fee panel sits in.

    Driven through `promote` rather than asserted on the expression, because
    what was wrong was not an ordering: the fields were being read by nothing
    at all, and a chain test passes over a silence.
    """

    SEASON = (date(2027, 5, 1), date(2027, 8, 31))
    ALIASES = {"aliases": {"aml-hayaty-haz901": "aml-hayaty"}}

    def payload(self, facts):
        from liveaboard.promote import promote
        book = {
            "collected": "2026-09-21",
            "departures": {
                "aml-hayaty-haz901::2027-05-01": {
                    "boat": "aml-hayaty-haz901",
                    "start": "2027-05-01",
                    "end": "2027-05-04",
                    "trip": "Mini Safari: Wrecks & Reefs",
                    "nights": 3,
                    "price": 640.0,
                    "currency": "EUR",
                },
            },
            "vessels": {"aml-hayaty-haz901": {
                "slug": "aml-hayaty-haz901",
                "name": "Aml Hayaty",
                # Keyed by `fee_key`, the trip and its length, exactly as the
                # parser writes it.
                "trips": {"Mini Safari: Wrecks & Reefs::3": facts},
            }},
        }
        return promote(
            {"scraped_at": "2026-09-21", "itineraries": [], "departures": []},
            season=self.SEASON, divebooker=book,
            divebooker_aliases=self.ALIASES)

    def itinerary(self, facts):
        payload = self.payload(facts)
        self.assertEqual(len(payload["itineraries"]), 1,
                         "the sailing this seller alone lists founded no row")
        return payload["itineraries"][0]

    def test_both_harbours_reach_the_row(self):
        found = self.itinerary({"port_from": "Hurghada", "port_to": "Marsa Alam"})
        self.assertEqual(found["port_from"], "Hurghada")
        self.assertEqual(found["port_to"], "Marsa Alam")

    def test_a_harbour_is_folded_the_way_every_other_harbour_is(self):
        """Through `PORT_ALIASES`, or the bank grows a chip for one spelling."""
        found = self.itinerary({"port_from": "Port Ghalib", "port_to": "Hurghada"})
        self.assertEqual(found["port_from"], _port("Port Ghalib"))

    def test_stating_one_end_states_neither(self):
        """A pair is one claim: half of it is a harbour beside a guess."""
        found = self.itinerary({"port_from": "Hurghada"})
        self.assertEqual((found["port_from"], found["port_to"]),
                         ("Unknown", "Unknown"))

    def test_saying_nothing_leaves_the_row_saying_nothing(self):
        found = self.itinerary({"dives": 9})
        self.assertEqual((found["port_from"], found["port_to"]),
                         ("Unknown", "Unknown"))


class TestTheThirdSellerAnswersLastAndTheSafetyBarDoesNot(unittest.TestCase):
    """Where this source sits in each chain, and why the bar is the exception.

    Asserted on the shape of the expression rather than through `promote`,
    which is how this project pins the orderings in `app.js` for the same
    reason: what may not change is the *order*, and the order is a line of
    source an editor can reverse without any number moving on the page until
    a boat somebody is not looking at gets the wrong count.
    """

    PROMOTE = Path(__file__).resolve().parents[1] / "src" / "liveaboard" / "promote.py"

    def setUp(self):
        self.src = self.PROMOTE.read_text(encoding="utf-8")

    def chain(self, start: str, end: str) -> str:
        return self.src.split(start, 1)[1].split(end, 1)[0]

    def test_the_dive_count_is_the_last_resort_of_all(self):
        """Behind ours and behind PADI's.

        It is another seller's account of a number the operator publishes, and
        it may not outrank the operator's own — which is exactly why PADI's is
        behind ours. Ten vessels publish a count and they state 15 to 21 for
        the same seven-night week, so whose answer this is matters.
        """
        chain = self.chain('"dives": trip.get("dives")', '"port_from"')
        self.assertIn("padi_trip", chain)
        self.assertIn("divebooker_trip", chain)
        self.assertLess(chain.index("padi_trip"), chain.index("divebooker_trip"),
                        "the third seller's dive count now outranks PADI's")

    def test_the_reefs_are_last_and_folded(self):
        chain = self.chain("sites = (_sites_from_description", "# The title's")
        self.assertLess(chain.index("padi_trip"), chain.index("divebooker_trip"),
                        "the third seller's reefs now outrank PADI's")
        self.assertIn("_sites_from_regions(divebooker_trip", chain,
                      "this source's reef names reach the site filter unfolded, "
                      "so they mint chips the rest of the fleet does not share")

    def test_the_entry_bar_takes_the_strictest_and_ranks_nobody(self):
        """A safety bar is not a fact about a price.

        The rule is that the stricter claim wins whoever made it, because
        showing the softer one publishes a gate below what somebody stated.
        Putting this source behind the other two would do exactly that on
        whichever trips it is the strict one.
        """
        chain = self.chain("ours, theirs = _requirements(trip)", "if bar:")
        self.assertIn("_strictest(_strictest(ours, theirs)", chain)
        self.assertIn("divebooker_trip", chain)
        self.assertNotIn(" or _requirements", chain,
                         "the third seller's bar fell back into a fallback")
