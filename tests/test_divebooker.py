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

    def test_every_in_season_row_is_accounted_for(self):
        """Matched, on a hull we do not map, or a sailing we do not carry.

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
            block["matched"] + block["on_unmapped_vessels"] + block["unmatched"],
            block["in_season"],
            "an in-season sailing is in none of the three buckets")
        self.assertIn(str(block["unmatched"]), block["note"])

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
        self.aliases = json.loads(
            (self.ROOT / "data" / "divebooker_aliases.json").read_text(
                encoding="utf-8"))["aliases"]
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

    def test_every_alias_names_a_boat_this_site_carries(self):
        ids = {boat["id"] for boat in self.boats}
        for slug, boat_id in self.aliases.items():
            self.assertIn(boat_id, ids, f"{slug} maps to a boat that is not here")


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
