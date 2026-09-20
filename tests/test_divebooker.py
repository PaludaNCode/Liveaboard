"""What divebooker.com's pages state, read from bytes it really served.

The fixture is the JSON-LD of `/bella-2-haz432`, kept verbatim from a runner
on 2026-09-20 — a boat selling three weeks states the same shapes as one
selling fifty, and costs a tenth of the log to carry back. The parser reads
nothing outside those blocks, so nothing is lost by keeping them rather than
300 KB of markup.

Every assertion below is a fact somebody read, and two of them are bugs this
fixture caught before they shipped.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

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
        """Every offer read states `InStock` and nothing states a number.

        Kept as the source's own word so nothing downstream can read a berth
        count out of it: *places left* is a question this source cannot
        answer, and the page may not imply otherwise.
        """
        for row in self.book.departures:
            self.assertEqual(row.availability, "InStock")
            self.assertNotIsInstance(row.availability, int)


class TestTheFleetIsDiscoveredNotTyped(unittest.TestCase):
    """Hulls come from the country page's own links.

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

    def test_no_fare_reaches_the_block(self):
        import json as _json
        printed = _json.dumps(self.block())
        for fare in ("576", "1200", "900", "600"):
            self.assertNotIn(fare, printed, f"a fare reached the dataset: {fare}")
        self.assertEqual(self.block()["fares"], "withheld")

    def test_no_book_means_no_block(self):
        from liveaboard.promote import divebooker_coverage
        self.assertIsNone(divebooker_coverage(None, self.ALIASES, self.DEPARTURES, self.BOAT_OF))
        self.assertIsNone(divebooker_coverage({"departures": {}}, self.ALIASES,
                                              self.DEPARTURES, self.BOAT_OF))


class TestTheShippedDatasetStatesNoDivebookerFare(unittest.TestCase):
    """The publication gate for the rule above.

    A guard over the code can be satisfied and the data still wrong, which is
    the whole reason this project separates the two.
    """

    def setUp(self):
        from published import raw
        self.payload = raw()

    def test_the_block_is_there_and_withholds(self):
        block = self.payload.get("divebooker")
        if block is None:
            self.skipTest("no divebooker book is committed on this checkout")
        self.assertEqual(block["fares"], "withheld")
        self.assertEqual(block["matched"], block["in_season"],
                         "a sailing this source lists in season that we do not "
                         "carry is a row promote would have to create")

    def test_no_departure_carries_a_divebooker_price(self):
        for row in self.payload.get("departures", []):
            for key in row:
                self.assertNotIn("divebooker", key,
                                 f"a divebooker figure reached a departure: {key}")


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
