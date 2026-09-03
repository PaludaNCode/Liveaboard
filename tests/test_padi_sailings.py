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

import published  # noqa: E402
from fetch_padi import (  # noqa: E402
    MIN_BOOK_RATIO,
    _sailing_counts,
    why_empty,
    _padi_sites,
    _departure_book,
    _iso_day,
    keeps_the_book,
)
from liveaboard.dataset import Dataset  # noqa: E402
from liveaboard.promote import _port, promote  # noqa: E402
from liveaboard.scrape.padi_com import PadiComAdapter  # noqa: E402
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
    """One row per sailing, and the second seller fills it in."""

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

    def test_a_date_we_already_carry_is_never_added_twice(self) -> None:
        """The whole point of keying on the day: one row per sailing."""
        payload = self.payload()
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

    def test_a_gangway_charge_both_sellers_state_is_billed_once(self) -> None:
        """The case the test above could not reach, and the bug it hid.

        Nitrox and gear are the vessel's charge, so PADI's side takes them from
        the vessel's disclosure -- and PADI states them too, in its own
        optional block. Taking PADI's whole book and adding the vessel's
        non-mandatory rows put both copies in: Serenity's PADI bill carried
        EUR 35 of nitrox twice and EUR 210 of gear twice. Every one of the 179
        trips with a PADI bill was doing it, 526 of 1,122 departures, and
        rental gear is on by default -- so half the page quoted a second hire
        nobody would pay, and called the difference a disagreement between the
        sellers.

        The other test puts gear on the vessel alone, which is why it passed
        throughout. This puts it on both, which is what the fleet looks like.
        """
        onboard = [
            {"code": "gear_rental", "label": "Equipment rental",
             "tier": "conditional", "basis": "per_trip", "included": False,
             "amount": {"amount": 200.0, "currency": "USD"},
             "provenance": {"kind": "scraped", "source_id": "liveaboard.com",
                            "retrieved": "2026-08-27"}},
            {"code": "nitrox", "label": "Nitrox", "tier": "conditional",
             "basis": "per_trip", "included": False,
             "amount": {"amount": 40.0, "currency": "USD"},
             "provenance": {"kind": "scraped", "source_id": "liveaboard.com",
                            "retrieved": "2026-08-27"}},
        ]
        fees = {"vessels": {"alia-soul": {"fees": [
            *self.FEES["vessels"]["alia-soul"]["fees"], *onboard]}}}
        # And PADI names the same two, at its own figures, as it does on 172
        # and 169 of the fleet's trips respectively.
        book = self.padi_book(lines=[
            {"code": "marine_park", "tier": "mandatory", "basis": "per_trip",
             "amount": {"amount": 60.0, "currency": "USD"}},
            {"code": "gear_rental", "tier": "conditional", "basis": "per_trip",
             "amount": {"amount": 999.0, "currency": "USD"}},
            {"code": "nitrox", "tier": "conditional", "basis": "per_trip",
             "amount": {"amount": 888.0, "currency": "USD"}},
        ])
        payload = promote(candidate([departure()]), season=SEASON, fees=fees,
                          padi_departures=BOOK, padi=book)
        itinerary = next(iter(
            build_payload(Dataset.from_dict(payload))["itineraries"].values()))
        codes = [line["code"] for line in itinerary["padi_lines"]]

        for code in ("gear_rental", "nitrox"):
            self.assertEqual(codes.count(code), 1,
                             "%s is on PADI's bill %d times" % (code, codes.count(code)))
        # And it is the *vessel's* figure that survives, not PADI's: the boat
        # bills this at the dock, out of one price list, to whoever booked.
        quoted = {line["code"]: line["quoted"]["amount"]
                  for line in itinerary["padi_lines"] if line.get("quoted")}
        self.assertEqual(quoted["gear_rental"], 200.0,
                         "PADI's own gear figure won a charge the vessel bills")
        self.assertEqual(quoted["nitrox"], 40.0,
                         "PADI's own nitrox figure won a charge the vessel bills")

    def test_no_shipped_bill_charges_one_code_twice(self) -> None:
        """The same rule over the fleet that actually ships.

        The fixture above proves the merge; this proves nobody's real book
        gets past it. A bill that names one charge twice is money invented,
        which is the failure this site exists to report in other people -- and
        it shipped on 526 of 1,122 departures before anyone opened Serenity.

        No skip decorator: `published.dataset()` raises the one this file
        already uses, and it also covers the case a decorator cannot see --
        a checkout where the dataset has not been built.
        """
        from liveaboard.pricing import padi_lines

        dataset = published.dataset()
        offenders = []
        for itinerary in dataset.itineraries.values():
            lines = padi_lines(itinerary, dataset.fx)
            if not lines:
                continue
            seen: dict[str, int] = {}
            for line in lines:
                code = line.code.value
                seen[code] = seen.get(code, 0) + 1
            twice = sorted(code for code, n in seen.items() if n > 1)
            if twice:
                offenders.append("%s: %s" % (itinerary.id, ", ".join(twice)))
        self.assertEqual(
            offenders, [],
            "%d shipped PADI bills charge a code more than once:\n  %s"
            % (len(offenders), "\n  ".join(offenders[:10])))

    def test_no_shipped_bill_states_a_total_it_bills_twice(self) -> None:
        """And the fleet-wide half of the bundle rule.

        The test above catches one code appearing twice. This catches the
        other shape: a bundle naming a charge that is *also* its own line, so
        the codes differ and the money does not. Seawolf Dominator is the one
        boat doing it, on 17 departures, and the page withholds those totals
        rather than adding the visa in twice.

        What is asserted is the consequence, not the count: every shipped bill
        that still claims a total adds up without billing anything twice.
        """
        from liveaboard.pricing import overlapping_charges, resolve_fees

        dataset = published.dataset()
        by_itinerary = {}
        for departure in dataset.departures:
            itinerary = dataset.itineraries[departure.itinerary_id]
            clash = overlapping_charges(resolve_fees(itinerary, departure))
            if clash:
                by_itinerary.setdefault(
                    itinerary.id, sorted(code.value for code in clash))

        page = published.page()
        for entry in page["departures"]:
            stated = by_itinerary.get(entry["itinerary_id"])
            if stated:
                # Read, and read as saying something: the missing total has to
                # carry this reason rather than one of the other two.
                self.assertEqual(entry.get("fee_overlap"), stated, entry["id"])
                self.assertTrue(entry["fees_known"], entry["id"])
                self.assertTrue(entry["mandatory_known"], entry["id"])
            else:
                self.assertNotIn("fee_overlap", entry, entry["id"])

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


SOLE = {
    "collected": "2026-08-28",
    "departures": {
        # A date the candidate does not sell. Written the way PADI writes one:
        # a berth count rather than a schema.org token, and a title carrying its
        # ports and its night count.
        "alia-soul::2027-07-04": {
            "boat": "alia-soul", "slug": "my-alia-soul", "start": "2027-07-04",
            "end": "2027-07-11", "nights": 7, "price": 999.0, "currency": "EUR",
            "availability": 6, "padi_id": 2,
            "itinerary": "Deep South (Port Ghalib - Port Ghalib) 7 Nights",
        },
    },
}


class TestTheSecondSellerCanCreateARow(unittest.TestCase):
    """A sailing only PADI lists is still a sailing.

    Promotion used to refuse this outright: the row count was the candidate's,
    and merging a second seller could only ever fill a field. That rule was
    reversed on a count. Of the 654 PADI sailings inside the published season,
    601 land on a row we already had -- and the other 53 are real, bookable
    trips the page was silent about. Blue Storm and Blue Seas contribute 29
    between them: near-complete weekly seasons PADI sells and liveaboard.com
    does not list at all.

    So "one row per sailing" was quietly meaning "one row per sailing
    liveaboard.com happens to list", which is the same mistake as reading an
    unreadable vessel page as an empty one. A trip nobody asked about is not a
    trip that does not exist.

    What must not change is everything else. These tests pin the creation and
    the four things that make it honest: the price is PADI's and says so, no
    row is duplicated, the second-seller field stays empty, and a trip whose
    name cannot be read is reported rather than invented.
    """

    def payload(self, book=SOLE, deps=None):
        return promote(candidate(deps or [departure()]), season=SEASON,
                       padi_departures=book)

    def rows(self, payload):
        return {d["start"]: d for d in payload["departures"]}

    def test_the_sailing_becomes_a_row(self) -> None:
        rows = self.rows(self.payload())
        self.assertEqual(sorted(rows), ["2027-05-01", "2027-07-04"])

    def test_the_price_is_padi_s_and_says_so(self) -> None:
        row = self.rows(self.payload())["2027-07-04"]
        self.assertEqual(row["price"], {"amount": 999.0, "currency": "EUR"})
        self.assertEqual(row["provenance"]["source_id"], "padi.com")
        self.assertEqual(row["provenance"]["retrieved"], "2026-08-28")
        self.assertEqual(
            row["provenance"]["url"],
            "https://travel.padi.com/liveaboard/egypt/my-alia-soul/",
        )

    def test_it_carries_no_second_price(self) -> None:
        """One seller's figure repeated into the second seller's field would
        print on the page as two sellers agreeing about a sailing one of them
        does not offer."""
        row = self.rows(self.payload())["2027-07-04"]
        self.assertNotIn("padi_price", row)
        self.assertTrue(row["padi_only"])

    def test_a_date_the_candidate_sells_is_not_created(self) -> None:
        """The exact key, doing the one job it exists for."""
        both = {"collected": "2026-08-28",
                "departures": {**BOOK["departures"], **SOLE["departures"]}}
        rows = self.rows(self.payload(book=both))
        self.assertEqual(len(rows), 2)
        self.assertNotIn("padi_only", rows["2027-05-01"])
        self.assertEqual(rows["2027-05-01"]["padi_price"],
                         {"amount": 1300.0, "currency": "USD"})

    def test_a_sailing_outside_the_season_is_not_created(self) -> None:
        book = {"collected": "2026-08-28", "departures": {
            "alia-soul::2029-07-04": {**SOLE["departures"]["alia-soul::2027-07-04"],
                                      "start": "2029-07-04", "end": "2029-07-11"}}}
        self.assertEqual(len(self.payload(book=book)["departures"]), 1)

    def test_an_unreadable_title_is_reported_not_invented(self) -> None:
        """A row under "Unnamed itinerary" is a trip whose identity this code
        made up, and the id is built from the name."""
        book = {"collected": "2026-08-28", "departures": {
            "alia-soul::2027-07-04": {**SOLE["departures"]["alia-soul::2027-07-04"],
                                      "itinerary": "Deep South"}}}
        payload = self.payload(book=book)
        self.assertEqual(len(payload["departures"]), 1)
        self.assertTrue(any("does not parse" in s
                            for s in payload.get("promotion_skipped", [])),
                        payload.get("promotion_skipped"))

    def test_a_sold_out_sailing_says_so(self) -> None:
        """PADI states berths left. Zero is sold out; that is what the field
        means, not an inference from it."""
        book = {"collected": "2026-08-28", "departures": {
            "alia-soul::2027-07-04": {**SOLE["departures"]["alia-soul::2027-07-04"],
                                      "availability": 0}}}
        self.assertEqual(self.rows(self.payload(book=book))["2027-07-04"]["availability"],
                         "sold_out")

    def test_it_joins_a_trip_we_already_have_rather_than_founding_a_twin(self) -> None:
        """PADI does not spell our titles. Two itineraries that are one trip
        would split its dates, its fees and its dive count -- and can do it
        silently, since the two names slugify to one id."""
        ours = departure(name="Deep South & Port Ghalib (Port Ghalib - Port Ghalib)")
        book = {"collected": "2026-08-28", "departures": {
            "alia-soul::2027-07-04": {
                **SOLE["departures"]["alia-soul::2027-07-04"],
                "itinerary": "Deep South and Port Ghalib (Port Ghalib- Port Ghalib) 7 Nights"}}}
        payload = self.payload(book=book, deps=[ours])
        self.assertEqual(len(payload["itineraries"]), 1, payload["itineraries"])
        self.assertEqual(len(payload["departures"]), 2)

    def test_the_page_is_told_which_rows_have_one_seller(self) -> None:
        payload = self.payload()
        entries = {d["start"]: d for d in
                   build_payload(Dataset.from_dict(payload))["departures"]}
        self.assertTrue(entries["2027-07-04"]["padi_only"])
        self.assertNotIn("padi_only", entries["2027-05-01"])

    def test_the_note_counts_them(self) -> None:
        """A page that names the wrong seller for a berth is doing the thing
        this project exists to report."""
        self.assertIn("On 1 sailings it is the only seller", self.payload()["notes"])


class TestTwoTripsNeverShareAnId(unittest.TestCase):
    """The failure mode that stays quiet: a collision, not a crash.

    `Dataset.from_dict` keys itineraries by id, so a second itinerary under an
    existing id simply replaces the first, and every departure of the loser is
    then served the winner's reefs, fees and dive count. The row count stays
    right and the page is confidently wrong.

    It happened the day promotion started creating rows. Blue Seas' *Daedalus &
    Fury Shoal (Port Ghalib - Port Ghalib)* and PADI's spelling of it without
    the space slugify to one string; so do Ghazala Explorer's *Deep South & St.
    Johns* and *Deep South - St Johns*. Both are handled upstream now -- the
    name folds onto the trip we already carry -- but the guard is what makes
    the next one a red build instead of a silent merge, and promotion is pure,
    so CI compares its output byte for byte.
    """

    #: An id is `f"{boat}--{slugify(name)}"[:96]`, so two trips whose names
    #: agree for long enough collide however differently they end -- and the
    #: end is where the ports are. "Two sailings differing only by port are two
    #: trips" is this dataset's rule, and truncation is the one thing that can
    #: break it without anybody typing a wrong character. Egypt's reef lists run
    #: long enough for it: DUNE Longara already sells *Mixed South - Daedalus,
    #: Rocky & Zabargad Islands and St John's*, and PADI writes them at its own
    #: length.
    LONG = ("Daedalus, Rocky, Zabargad, Elphinstone, Fury Shoals and Saint Johns "
            "Reef Expedition")

    def test_a_collision_raises(self) -> None:
        book = {"collected": "2026-08-28", "departures": {
            "alia-soul::2027-07-04": {
                **SOLE["departures"]["alia-soul::2027-07-04"],
                "itinerary": f"{self.LONG} (Port Ghalib - Hurghada) 7 Nights"}}}
        ours = departure(name=f"{self.LONG} (Port Ghalib - Port Ghalib)")
        with self.assertRaises(ValueError) as caught:
            promote(candidate([ours]), season=SEASON, padi_departures=book)
        self.assertIn("share an id", str(caught.exception))

    def test_the_names_that_collide_are_genuinely_two_trips(self) -> None:
        """A guard on the guard. If the two folded instead, the test above would
        pass by never building a second itinerary at all -- and would go on
        passing after the guard was deleted."""
        from liveaboard.promote import padi_key
        self.assertNotEqual(
            padi_key("alia-soul", f"{self.LONG} (Port Ghalib - Hurghada)"),
            padi_key("alia-soul", f"{self.LONG} (Port Ghalib - Port Ghalib)"),
        )

    def test_the_real_dataset_has_none(self) -> None:
        payload = promote(candidate([departure()]), season=SEASON, padi_departures=SOLE)
        ids = [i["id"] for i in payload["itineraries"]]
        self.assertEqual(len(ids), len(set(ids)))


VESSEL_BOOK = {
    "collected": "2026-08-28",
    "vessels": {
        "seawolf-steel": {"slug": "seawolf-steel", "name": "Seawolf Steel",
                          "operator": "SEAWOLF DIVING SAFARI",
                          "country": "egypt", "currency": "EUR"},
    },
    "trips": {
        "seawolf-steel::Fury Shoals (PRG - PRG)": {
            "boat": "seawolf-steel", "boat_name": "Seawolf Steel",
            "name": "Fury Shoals (PRG - PRG)", "nights": 7, "dives": 19,
            "fees": {"complete": True, "unreadable": [], "lines": [
                {"code": "combined_fees", "tier": "mandatory",
                 "basis": "per_trip", "note": "Marine Park and harbour fees",
                 "amount": {"amount": 255.0, "currency": "EUR"}},
            ]},
        },
    },
}

VESSEL_SAILINGS = {
    "collected": "2026-08-28",
    "departures": {
        "seawolf-steel::2027-07-04": {
            "boat": "seawolf-steel", "slug": "seawolf-steel",
            "start": "2027-07-04", "end": "2027-07-11", "nights": 7,
            "price": 1400.0, "currency": "EUR", "availability": 8, "padi_id": 9,
            "itinerary": "Fury Shoals (PRG - PRG) 7 Nights",
        },
    },
}


class TestABoatOnlyPadiSells(unittest.TestCase):
    """A vessel the first source has no departures for at all.

    Twenty-two of PADI's Egyptian liveaboards are in this state, ten of them
    with sailings inside the published season. They arrive with no name, no
    operator and, for twelve of the twenty-two, no fee panel either, because
    every one of those normally comes from the site that sells the berths.

    Two rules keep such a boat honest. Its name and operator are PADI's own
    strings, verbatim -- a vessel published under a title-cased slug is one this
    code named rather than one anybody wrote. And PADI's per-itinerary fee book
    becomes the itinerary's own, but only as a fallback: where our per-vessel
    book exists it wins outright, because the two disclose at different
    resolutions and taking a line from each builds a bill neither seller quotes.
    """

    def payload(self):
        return promote(candidate([departure()]), season=SEASON,
                       padi=VESSEL_BOOK, padi_departures=VESSEL_SAILINGS)

    def boat(self, payload):
        return next(b for b in payload["boats"] if b["id"] == "seawolf-steel")

    def trip(self, payload):
        return next(i for i in payload["itineraries"] if i["boat_id"] == "seawolf-steel")

    def test_the_boat_appears(self) -> None:
        payload = self.payload()
        self.assertEqual(len(payload["boats"]), 2)
        self.assertEqual(len(payload["departures"]), 2)

    def test_it_takes_padi_s_name_rather_than_its_slug(self) -> None:
        self.assertEqual(self.boat(self.payload())["name"], "Seawolf Steel")

    def test_it_takes_padi_s_fleet_as_its_operator(self) -> None:
        """Shouted, and left that way. Tidying an operator's capitalisation is
        a short step from deciding what they are called -- the rule
        OPERATOR_ALIASES already states."""
        payload = self.payload()
        operator = next(o for o in payload["operators"]
                        if o["id"] == self.boat(payload)["operator_id"])
        self.assertEqual(operator["name"], "SEAWOLF DIVING SAFARI")

    def test_padi_s_fee_book_becomes_the_trip_s_own(self) -> None:
        """Otherwise the row is a berth price with no total, on a site whose
        subject is the difference between the two."""
        trip = self.trip(self.payload())
        self.assertEqual([f["code"] for f in trip["fees"]], ["combined_fees"])
        self.assertEqual(trip["fees"][0]["provenance"]["source_id"], "padi.com")
        self.assertTrue(trip["padi_sourced_fees"])

    def test_our_own_fee_book_still_wins_where_it_exists(self) -> None:
        """The fallback must not become a merge. Ten of the twenty-two do have
        a liveaboard.com fee panel -- that site carries the vessel and simply
        publishes no departures for it -- and those take it."""
        fees = {"vessels": {"seawolf-steel": {"fees": [
            {"code": "marine_park", "label": "Marine park fees", "tier": "mandatory",
             "amount": {"amount": 150.0, "currency": "EUR"}, "basis": "per_trip",
             "included": False,
             "provenance": {"kind": "scraped", "source_id": "liveaboard.com",
                            "retrieved": "2026-08-27"}},
        ]}}}
        payload = promote(candidate([departure()]), season=SEASON, fees=fees,
                          padi=VESSEL_BOOK, padi_departures=VESSEL_SAILINGS)
        trip = self.trip(payload)
        self.assertEqual([f["code"] for f in trip["fees"]], ["marine_park"])
        self.assertNotIn("padi_sourced_fees", trip)
        self.assertEqual([f["code"] for f in trip["padi_fees"]], ["combined_fees"],
                         "PADI's book stays beside ours under its own name")

    def test_the_page_is_told_where_the_fee_rows_came_from(self) -> None:
        """The sentence under the fee table names a source, and naming the
        wrong one is the failure this project reports in other people."""
        payload = build_payload(Dataset.from_dict(self.payload()))
        trip = next(v for k, v in payload["itineraries"].items() if "seawolf" in k)
        self.assertTrue(trip["padi_sourced_fees"])


class TestAFleetIsNotAnOperator(unittest.TestCase):
    """PADI's fleet label must not rename a boat's operator to one of ours.

    MY Blue and MY Blue Pearl are two hulls -- 24 guests at 43 m against 20 at
    36 m, different shop ids, different sailings -- that PADI files under one
    "BLUE PLANET Fleet". MY Blue is our Blue, whose own liveaboard.com
    departures say "Blue Planet Liveaboards", so folding the fleet label onto
    that company tidied a duplicate off the operator list. It also asserted, on
    nothing but a fleet label, that Blue Pearl is run by a company our own
    source never connected it to.

    A fleet on a booking site is not established to be the operating company.
    Two operator rows that may be one company is a cosmetic cost; naming the
    wrong company is the claim this site exists to catch other people making.

    **The two Blues are now one operator, and the rule above is why that is
    allowed.** They were not folded on the fleet label; the fold was refused on
    exactly this reasoning and stayed refused. What changed is the evidence:
    Blue Pearl's own liveaboard.com page states
    `"brand": {"name": "Blue Planet Liveaboards"}` -- the same source every
    other operator here comes from, naming the company for that hull directly
    (#115). So the assertions below pin the *rule* rather than the outcome it
    happened to produce: the fleet label is still kept verbatim, still folds
    nothing, and the merge rests on a statement instead.
    """

    def test_padi_s_fleet_is_kept_verbatim(self) -> None:
        payload = promote(candidate([departure()]), season=SEASON,
                          padi=VESSEL_BOOK, padi_departures=VESSEL_SAILINGS)
        boat = next(b for b in payload["boats"] if b["id"] == "seawolf-steel")
        operator = next(o for o in payload["operators"]
                        if o["id"] == boat["operator_id"])
        self.assertEqual(operator["name"], "SEAWOLF DIVING SAFARI")

    def test_no_alias_folds_a_fleet_label(self) -> None:
        """The table is for one company under two spellings *of its own name*,
        which is a different claim from two boats sharing a shelf."""
        from liveaboard.promote import OPERATOR_ALIASES
        self.assertEqual(sorted(OPERATOR_ALIASES), ["aggressor fleet& dancer fleet"])

    def test_a_fleet_label_alone_still_folds_nothing(self) -> None:
        """The case that made the rule, with only the evidence it had.

        PADI files both hulls under "BLUE PLANET Fleet" and our own source
        connects Blue Pearl to nobody. That must stay two operators: the tidier
        answer is an assertion the data does not support.
        """
        payload = promote(
            candidate([departure(boat="blue", operator="Blue Planet Liveaboards"),
                       # Blue Pearl sells too, and states no operator of its
                       # own -- which is the whole reason PADI's label is
                       # reached for at all.
                       departure(boat="blue-pearl", name="Deep South")],
                      itineraries=[{"id": "blue", "name": "Blue", "boat": "MY Blue"},
                                   {"id": "blue-pearl", "name": "Blue Pearl",
                                    "boat": "MY Blue Pearl"}]),
            season=SEASON,
            padi={"collected": "2026-08-29", "vessels": {
                "blue-pearl": {"slug": "my-blue-pearl", "name": "MY Blue Pearl",
                               "operator": "BLUE PLANET"}}},
        )
        names = {o["name"] for o in payload["operators"]}
        self.assertIn("Blue Planet Liveaboards", names)
        self.assertIn("BLUE PLANET", names)

    def test_the_two_blues_are_one_company_on_a_stated_one(self) -> None:
        """And the committed dataset shows the fold, because the statement
        arrived. Still two hulls: the guest counts differ, and neither carries
        the other's sailings."""
        data = published.raw()
        boats = {b["id"]: b for b in data["boats"]}
        if "blue" not in boats or "blue-pearl" not in boats:
            self.skipTest("neither Blue is in this checkout's dataset")
        self.assertEqual(boats["blue"]["operator_id"], boats["blue-pearl"]["operator_id"])
        self.assertNotEqual(boats["blue"]["guests"], boats["blue-pearl"]["guests"])

        # And no sailing of one is filed under the other.
        itineraries = {i["id"]: i for i in data["itineraries"]}
        for boat, slug in (("blue", "my-blue"), ("blue-pearl", "my-blue-pearl")):
            urls = {d.get("booking_url") or "" for d in data["departures"]
                    if itineraries[d["itinerary_id"]]["boat_id"] == boat}
            stray = sorted(u for u in urls
                           if "travel.padi.com" in u and f"/{slug}/" not in u)
            self.assertEqual(stray, [], f"{boat} carries another vessel's PADI link: {stray}")


class TestTheSoleSellerIsNamedInSource(unittest.TestCase):
    """A PADI-only row's one link points at PADI, so it must say so.

    The Sellers column was dropped and Source became where both sellers are
    reached from. It labels the first link "liveaboard" when PADI also sells
    the date and "listing" when it does not -- and "does not" is true of a
    PADI-only row for the opposite reason: there is no second seller because
    the *first* one is missing. Left alone, 230 rows would offer one link named
    for the site it does not come from, in the one column whose job is to say
    where a number came from.
    """

    APP = ROOT_APP = None

    def source_column(self) -> str:
        from pathlib import Path
        app = Path(__file__).resolve().parent.parent / "templates" / "app.js"
        text = app.read_text(encoding="utf-8")
        start = text.index('{ k: "source"')
        return text[start:text.index("\n  ];", start)]

    def test_the_label_branches_on_padi_only(self) -> None:
        self.assertIn("d.padi_only ?", self.source_column())

    def test_a_padi_only_row_carries_a_padi_url(self) -> None:
        """The label is only right because the url is. Both come from the same
        provenance, so this fails if either moves."""
        payload = build_payload(Dataset.from_dict(
            promote(candidate([departure()]), season=SEASON,
                    padi=VESSEL_BOOK, padi_departures=VESSEL_SAILINGS)))
        row = next(d for d in payload["departures"] if d.get("padi_only"))
        self.assertTrue(row["booking_url"].startswith("https://travel.padi.com/"),
                        row["booking_url"])
        self.assertNotIn("padi", {k for k in row if k == "padi"},
                         "a sole-seller row must not also claim a second price")


class TestTheBookIsNeverQuietlyEmptied(unittest.TestCase):
    """`data/padi.json` is rebuilt whole from a raw store that is gitignored.

    Fine on a machine that has one; a landmine on a fresh runner, which is
    every scheduled run. An empty store rebuilds the book with zero trips and
    writes it, deleting the entry bar, the stated dive count and the only fee
    book the 22 PADI-only vessels have — with a green job, a valid file, and
    five published facts quietly gone.

    The same rule the other fetchers already keep: a run that read nothing must
    not rewrite the file, and an empty read is not a read that found nothing.
    """

    def test_a_run_that_stored_nothing_never_replaces_the_book(self):
        self.assertFalse(keeps_the_book(0, 441))

    def test_a_first_run_has_nothing_to_lose(self):
        self.assertTrue(keeps_the_book(441, 0))
        self.assertTrue(keeps_the_book(0, 0))

    def test_a_book_that_lost_a_third_is_an_unfinished_crawl(self):
        self.assertFalse(keeps_the_book(300, 441))

    def test_an_itinerary_or_two_moving_is_not(self):
        """PADI's count moves by ones. A guard that fired on those would be a
        guard somebody routes around with --force every week."""
        self.assertTrue(keeps_the_book(439, 441))
        self.assertTrue(keeps_the_book(int(441 * MIN_BOOK_RATIO) + 1, 441))

    def test_force_is_the_way_past_it(self):
        """As `promote --force` is the way past its own ratio guard: the
        shrinkage may be real, and then a person says so."""
        self.assertTrue(keeps_the_book(0, 441, force=True))


class TestPadiIsTheLastWordOnReefs(unittest.TestCase):
    """The dive-site filter is what the page is for, and 47 rows could not be
    reached by it: a trip with no sites is invisible to the handle the route
    labels were removed in favour of.

    PADI-only trips went blank fourteen times more often than the rest, and for
    a structural reason -- the operator's description and region list both come
    from liveaboard.com's archive, which has no entry for a boat it sells no
    berths on, so the title parser answered alone against titles like *Premium
    Expedition* and *Family Safari (HRG - HRG)*.

    PADI describes those trips. It is put **last** because it describes them
    least precisely: against the 180 trips both sellers cover, its blurb adds
    173 reef mentions ours does not, including Elphinstone on a Brothers and
    Safaga week off a sentence saying the two "are quite distant from one
    another". Merged in, that is the BDE-badging failure this project removed
    once already.
    """

    def sites(self, detail):
        return _padi_sites(detail)

    def test_the_day_plan_is_read_before_the_blurb(self):
        """A day says what you dive; a blurb is an essay that will mention
        anywhere. The same distinction promote already draws between a day
        section and a place section on the other source."""
        self.assertEqual(
            self.sites({
                "days": [{"description": "<p>Diving at Ras Mohammed</p>"}],
                "highlightsDescription": "<p>Unlike the Brothers, this week ...</p>",
            }),
            ["ras mohammed"])

    def test_the_blurb_answers_only_where_the_day_plan_is_silent(self):
        """Most day plans carry nothing, or "Up to three dives are offered
        daily" -- so dropping the blurb entirely would leave the trips this
        exists for still blank."""
        self.assertEqual(
            self.sites({
                "days": [{"description": "Up to three dives are offered daily."}],
                "highlightsDescription": "<p>The route includes the Straits of Tiran.</p>",
            }),
            ["tiran"])

    def test_markup_is_stripped_before_the_reefs_are_read(self):
        self.assertEqual(
            self.sites({"days": [{"description":
                                  "<p><span style='x'>Thistlegorm</span></p>"}]}),
            ["thistlegorm"])

    def test_a_reef_named_twice_is_one_site(self):
        self.assertEqual(
            self.sites({"days": [{"description": "Thistlegorm"},
                                 {"description": "Thistlegorm again"}]}),
            ["thistlegorm"])

    def test_a_trip_that_names_no_reef_yields_nothing(self):
        """"Best Of Hurghada", "Specialty Photography Safari" and "Solar
        Eclipse South Tour" name no reef in any field, and stay blank. A trip
        whose sites nobody states has none to show."""
        self.assertEqual(
            self.sites({"days": [{"description": "A relaxed week of photography."}],
                        "highlightsDescription": "<p>Best of Hurghada.</p>"}),
            [])


class TestTheHarbourPadiStates(unittest.TestCase):
    """Every port on this page is parsed out of a trip title. PADI states two.

    `harbourDepartureTitle` and `harbourArrivalTitle` are on **447 of 447**
    itineraries, name eight real places, and were being stored joined into one
    `ports` string that nothing read -- and could not have read, because two of
    those eight names contain the separator:

        Hurghada - Marriott Marina - Hurghada - Marriott Marina

    is either `("Hurghada", "Marriott Marina - Hurghada - Marriott Marina")` or
    `("Hurghada - Marriott Marina", "Hurghada - Marriott Marina")`, and the
    string does not say. 436 of 447 split cleanly and the other 11 cannot be
    split without guessing. So the fix was the record, not a parser: a
    closed-vocabulary split over today's eight names is the rule that breaks
    silently the first time PADI names a ninth marina.
    """

    #: The itinerary name promote looks the book up under. `padi_key` is
    #: computed from the record's own boat and name, never from the dict key --
    #: and the name a PADI record carries keeps its port bracket, because
    #: `itinerary_from_payload` stores the title's head and the head includes
    #: it. Two sailings differing only by port are two trips.
    TRIP = "Brothers, Daedalus & Elphinstone (Hurghada - Hurghada)"

    def trip(self, name=TRIP, **extra):
        return {"collected": "2026-08-28", "trips": {"t": {
            "boat": "alia-soul", "name": name, **extra}}}

    def itinerary(self, name=TRIP,
                  padi=None, padi_only=False):
        extra = {"padi_only": True} if padi_only else {}
        payload = promote(candidate([departure(name=name, **extra)]),
                          season=SEASON, padi=padi)
        return payload["itineraries"][0]

    def test_the_two_harbours_are_stored_as_two_fields(self):
        record = PadiComAdapter.itinerary_from_payload({
            "title": "Brothers (Hurghada - Hurghada) 7 Nights",
            "harbourDepartureTitle": "Hurghada - Marriott Marina",
            "harbourArrivalTitle": "Hurghada - Marriott Marina",
        })
        self.assertEqual(record["port_from"], "Hurghada - Marriott Marina")
        self.assertEqual(record["port_to"], "Hurghada - Marriott Marina")
        self.assertNotIn("ports", record)

    def test_a_marina_folds_onto_the_town_the_filter_already_lists(self):
        """The stated field is more granular than a title: it names berths
        where a title names towns. Unfolded, each would open a second harbour
        chip for a port the filter already has."""
        for stated, expected in (("Hurghada Marina", "Hurghada"),
                                 ("Hurghada - Marriott Marina", "Hurghada"),
                                 ("New Marina Sharm El Sheikh (El Wataneya)",
                                  "Sharm El Sheikh")):
            with self.subTest(stated=stated):
                self.assertEqual(_port(stated), expected)

    def test_a_statement_fills_a_port_the_title_never_named(self):
        """A statement beats nothing at all, whatever the trip's own source.
        This is what stops the next abbreviation waiting to be noticed by hand
        the way "(HRG - PRG)" was, after it had shipped."""
        blind = self.itinerary(name="Premium Expedition")
        self.assertEqual((blind["port_from"], blind["port_to"]), ("Unknown", "Unknown"))
        filled = self.itinerary(
            name="Premium Expedition",
            padi=self.trip(name="Premium Expedition",
                           port_from="Port Ghalib", port_to="Hurghada"))
        self.assertEqual((filled["port_from"], filled["port_to"]),
                         ("Port Ghalib", "Hurghada"))

    def test_it_does_not_overrule_the_other_seller_s_own_title(self):
        """liveaboard.com's title stays authoritative for a liveaboard.com
        trip, the way our fee book beats PADI's where both exist. The second
        source is a check here, not a replacement -- and two independent
        readings agreeing is worth more than one reading nobody can check."""
        row = self.itinerary(padi=self.trip(port_from="Port Ghalib", port_to="Hamata"))
        self.assertEqual((row["port_from"], row["port_to"]), ("Hurghada", "Hurghada"))

    def test_a_padi_titled_trip_takes_the_stated_harbour_over_its_own_parse(self):
        """A statement beating a parse of the *same source's* title is not a
        judgement call."""
        row = self.itinerary(padi_only=True,
                             padi=self.trip(port_from="Port Ghalib", port_to="Hamata"))
        self.assertEqual((row["port_from"], row["port_to"]), ("Port Ghalib", "Hamata"))

    def test_a_trip_padi_has_not_been_read_for_keeps_its_parsed_ports(self):
        row = self.itinerary(padi=self.trip())
        self.assertEqual((row["port_from"], row["port_to"]), ("Hurghada", "Hurghada"))


class TestTheSecondSellersDiveCountFillsAnEmptyColumn(unittest.TestCase):
    """PADI states a per-trip dive count and promotion never consulted it.

    It still cannot outrank ours, and the reason is unchanged: every All Star
    Ghani itinerary says 16 where ours say 17, 19, 20 and 21, and of the 142
    trips where both speak, 113 disagree with PADI the lower one on 90. A number
    less differentiated than ours cannot improve a column ours already fills.

    But on **69 published itineraries nothing of ours answers at all** and PADI
    answers every one of them. 43 are on the vessels PADI alone sells berths on:
    liveaboard.com lists no departure for those boats, so
    `fetch_itineraries.py` has no tour id to ask about and never will. Bella 2's
    mini-safari is the case -- PADI says 9 dives over its three nights and the
    column said "not stated".
    """

    TRIP = "Brothers, Daedalus & Elphinstone (Hurghada - Hurghada)"

    def padi(self, dives=9, name=TRIP):
        return {"collected": "2026-08-28",
                "trips": {"t": {"boat": "alia-soul", "name": name, "dives": dives}}}

    def dives(self, padi=None, trips=None, itineraries=None, nights=3):
        payload = promote(
            candidate([departure(name=self.TRIP, start="2027-05-01",
                                 end=f"2027-05-0{1 + nights}")],
                      **({"itineraries": itineraries} if itineraries else {})),
            season=SEASON, padi=padi, trips=trips)
        return payload["itineraries"][0]["dives"]

    def test_it_fills_a_column_nothing_else_answers(self):
        self.assertEqual(self.dives(), 0)
        self.assertEqual(self.dives(padi=self.padi()), 9)

    def test_our_own_per_trip_count_still_wins(self):
        from test_promote import trip_book

        self.assertEqual(self.dives(padi=self.padi(dives=16),
                                    trips=trip_book(name=self.TRIP, dives=20)), 20)

    def test_the_vessel_level_count_still_wins_too(self):
        """A figure about the hull is ours and is guarded by trip length; that
        guard is what makes it the better answer where it applies."""
        self.assertEqual(
            self.dives(padi=self.padi(dives=16), nights=7,
                       itineraries=[{"id": "alia-soul", "boat": "Alia Soul",
                                     "dives": 20, "dives_for_nights": 7}]),
            20)

    def test_a_trip_padi_has_not_been_read_for_still_states_nothing(self):
        self.assertEqual(self.dives(padi=self.padi(name="Another Trip")), 0)

    def test_zero_is_not_a_count(self):
        self.assertEqual(self.dives(padi=self.padi(dives=0)), 0)


class TestWhyAVesselHasNoRows(unittest.TestCase):
    """Twelve mapped vessels publish nothing and the run could not say why.

    `_departure_book` drops a sailing with no date, with no price, and outside
    the window, and it drops all three the same way: silently. So a mapped
    vessel with no rows was one of several quite different things, and that is
    the shape of every failure this repo has already been bitten by -- the
    barren skip list, `carry_unread`, `PARSE_ATTEMPTS` -- where *not looked at*
    and *nothing there* travelled down one channel until somebody separated
    them.

    Recorded, not acted on. `promote` does not read it; a person reading the
    run does.
    """

    SEASON = ("2027-05-01", "2027-08-31")

    def counts(self, *sailings):
        return _sailing_counts(
            [{"startDate": d, "price": p} for d, p in sailings], self.SEASON)

    def test_a_vessel_selling_in_the_window_is_not_reported(self):
        self.assertEqual(why_empty(self.counts(("2027-06-05", 1200.0))), "")

    def test_an_endpoint_that_answered_nothing_says_so(self):
        self.assertEqual(why_empty(self.counts()),
                         "the trips endpoint returned no sailing at all")

    def test_a_calendar_that_stops_short_names_the_window_it_reaches(self):
        """South Moon 1 sells 22 priced sailings, every one before May 2027.
        The absence *is* the answer there, and a bare zero cannot say so."""
        why = why_empty(self.counts(("2026-09-05", 900.0), ("2027-01-30", 950.0)))
        self.assertIn("none in the season", why)
        self.assertIn("2026-09-05 to 2027-01-30", why)

    def test_a_vessel_that_prices_nothing_is_a_different_kind_of_empty(self):
        """VIP One lists 17 sailings and prices none. Two facts hold at once --
        the window is why there is no row, and the missing prices are what
        would bite the day the calendar does reach us."""
        why = why_empty(self.counts(("2026-09-05", 0.0), ("2026-12-27", None)))
        self.assertIn("none in the season", why)
        self.assertIn("none of them priced", why)

    def test_unpriced_is_counted_over_every_dated_sailing(self):
        """Not only the in-season ones. Counting inside the window alone would
        have hidden VIP One entirely, whose calendar stops in December."""
        self.assertEqual(self.counts(("2026-09-05", 0.0), ("2026-12-27", None))["unpriced"], 2)

    def test_a_sailing_with_no_readable_date_is_neither_dated_nor_in_season(self):
        counts = _sailing_counts([{"startDate": None, "price": 900.0}], self.SEASON)
        self.assertEqual((counts["read"], counts["dated"]), (1, 0))
        self.assertEqual(why_empty(counts),
                         "1 sailing(s), none carrying a readable date")

    def test_a_season_with_unpriced_sailings_in_it_says_that_and_not_the_window(self):
        why = why_empty(self.counts(("2027-06-05", 0.0), ("2027-06-12", None)))
        self.assertEqual(why, "2 sailing(s) in the season, none of them priced")
