"""Tests for PADI Travel's deals: reading them, placing them, diffing them.

Three properties, and each one is a bug this codebase has already committed
somewhere else and does not intend to commit again.

**Paging terminates on offer identity, never on a page number.** The HTML the
deals endpoint sits behind returns page 1's content for every value of `page`,
page 99 included, so a loop that asked the page whether it had ended would
either never stop or stop at one. The guard is that a page which adds no offer
already unseen is the end of the listing, whatever it says about itself.

**A deal is placed by joining its vessel to a boat of ours, never by PADI's
country field.** That field reads United States of America for all three Red Sea
Aggressors -- which is why the query has to ask for the USA at all -- and asking
for it also returns Bahamas, Belize, Cayman and Roatan. A vessel that does not
join is reported rather than dropped: an Egyptian boat under a USA label and
unmatched is exactly the case worth catching.

**A reading nobody could finish is not a day with no deals on it.** The same
rule that stops an unreadable vessel page emptying a boat's month: absences in a
truncated listing are not withdrawals, and the change log refuses to call them
that.
"""

from __future__ import annotations

import csv
import io
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import published  # noqa: E402
from fetch_deals import prune, season_months  # noqa: E402
from liveaboard.dataset import Dataset  # noqa: E402
from liveaboard.promote import promote  # noqa: E402
from liveaboard.render import build_payload  # noqa: E402
from liveaboard.scrape.padi_com import DEAL_COUNTRIES, PadiComAdapter  # noqa: E402

from test_promote import SEASON, candidate, departure  # noqa: E402


def raw(
    slug: str = "hammerhead-ii",
    country: str = "egypt",
    price: float = 564.4,
    was: float = 664.0,
    currency: str = "EUR",
    start: str = "2027-05-15",
    end: str = "2027-05-18",
    title: str = "Save 15%",
    kind: int = 20,
    value: float = 15.0,
    shop: str = "Hammerhead II",
    **extra,
) -> dict:
    """One row of the promotions listing, shaped as PADI publishes it."""
    payload = {
        "url": f"https://travel.padi.com/liveaboard/{country}/{slug}/",
        "shopTitle": shop,
        "shopId": 94466,
        "countryTitle": "Egypt",
        "price": price,
        "compareAtPrice": was,
        "currency": currency,
        "dateFrom": f"{start}T00:00:00Z",
        "dateTo": f"{end}T00:00:00Z",
        "promotion": {"title": title, "kind": kind, "value": value, "description": "<p>x</p>"},
    }
    payload.update(extra)
    return payload


def page(rows, more: bool = False) -> dict:
    return {"count": len(rows), "next": "…" if more else None, "results": rows}


class TestReadingOneOffer(unittest.TestCase):
    def test_the_vessel_comes_off_the_url_not_the_country_field(self):
        """PADI's country is where the operator is registered, not where it sails.

        All three Red Sea Aggressors read United States of America here while
        sailing Hurghada, Port Ghalib and Hamata.
        """
        deal = PadiComAdapter.deal_from_payload(
            raw(slug="red-sea-aggressor-ii", country="united-states-of-america-usa")
        )
        self.assertEqual(deal["slug"], "red-sea-aggressor-ii")
        self.assertEqual(deal["country"], "united-states-of-america-usa")

    def test_a_timestamp_becomes_the_day_it_names(self):
        deal = PadiComAdapter.deal_from_payload(raw())
        self.assertEqual(deal["start"], "2027-05-15")
        self.assertEqual(deal["end"], "2027-05-18")

    def test_padi_s_own_word_for_the_offer_is_kept(self):
        deal = PadiComAdapter.deal_from_payload(raw(kind=30, value=2.0))
        self.assertEqual(deal["kind_label"], "Free night(s)")
        self.assertEqual(deal["value"], 2.0)

    def test_a_price_with_no_currency_beside_it_is_dropped(self):
        """The trap next door, and the reason this endpoint is worth using.

        `shop/{vessel}/trips/` states a bare number and the app's Currency-code
        header does not convert it -- EUR, USD and GBP all answer the same
        figure. Assuming euro would put every Aggressor out by the FX rate.
        """
        self.assertIsNone(PadiComAdapter.deal_from_payload(raw(currency="")))

    def test_a_discount_with_no_undiscounted_price_is_dropped(self):
        row = raw()
        row["compareAtPrice"] = None
        self.assertIsNone(PadiComAdapter.deal_from_payload(row))

    def test_a_row_that_names_no_vessel_page_is_dropped(self):
        row = raw()
        row["url"] = "https://example.invalid/somewhere/"
        self.assertIsNone(PadiComAdapter.deal_from_payload(row))


class TestPaging(unittest.TestCase):
    """Termination is on offer identity. Nothing else is trusted."""

    def _run(self, pages, **kwargs):
        asked: list[str] = []

        def fetch(url):
            asked.append(url)
            return pages[len(asked) - 1] if len(asked) <= len(pages) else None

        deals, report = PadiComAdapter.collect_deals(fetch, "https://x/?q=1", **kwargs)
        return deals, report, asked

    def test_a_listing_that_repeats_page_one_forever_still_ends(self):
        """The failure the HTML shell would have caused, in miniature.

        Every page is byte-identical, `next` always claims another, and nothing
        in the response ever says stop. Offer identity does.
        """
        one = page([raw()], more=True)
        deals, report, asked = self._run([one] * 40)
        self.assertEqual(len(deals), 1)
        self.assertEqual(report["stopped"], "no new offer")
        self.assertEqual(len(asked), 2)

    def test_it_reads_past_the_first_page_when_there_are_more_offers(self):
        deals, report, asked = self._run([
            page([raw()], more=True),
            page([raw(slug="serenity", shop="Serenity")], more=True),
            page([]),
        ])
        self.assertEqual(sorted(deals), ["hammerhead-ii", "serenity"])
        self.assertEqual(report["stopped"], "empty page")
        self.assertIn("page=2", asked[1])

    def test_a_page_that_fails_after_the_listing_said_it_had_ended_is_the_end(self):
        deals, report, _ = self._run([page([raw()]), None])
        self.assertEqual(report["stopped"], "listing ended")
        self.assertFalse(report["truncated"])

    def test_a_page_that_fails_while_more_was_promised_is_truncated(self):
        """Not "that was everything". The run does not know what was on it."""
        deals, report, _ = self._run([page([raw()], more=True), None])
        self.assertEqual(report["stopped"], "unreadable")
        self.assertTrue(report["truncated"])

    def test_the_page_cap_is_reported_rather_than_passed_off_as_the_end(self):
        rows = [page([raw(slug=f"boat-{n}", shop=f"Boat {n}")], more=True) for n in range(40)]
        _, report, _ = self._run(rows, max_pages=3)
        self.assertEqual(report["stopped"], "page cap")
        self.assertTrue(report["truncated"])

    def test_a_second_offer_on_one_vessel_is_reported_not_silently_dropped(self):
        deals, report, _ = self._run([
            page([raw(), raw(price=500.0, title="Save 25%")]),
            None,
        ])
        self.assertEqual(len(deals), 1)
        self.assertEqual(len(report["crowded"]), 1)
        self.assertIn("hammerhead-ii", report["crowded"][0])

    def test_the_query_spells_the_deals_page_s_own_parameters(self):
        url = PadiComAdapter.deals_url(["2027-05-01", "2027-06-01"], DEAL_COUNTRIES)
        self.assertIn("country=110&country=120", url)
        self.assertIn("date=2027-05-01&date=2027-06-01", url)
        # Every one of these is Disallow on travel.padi.com; plain `date=` is not.
        for banned in ("trip_date=", "departure_date=", "date_from=", "dateStart=",
                       "dateTo=", "date_after=", "activity_date="):
            self.assertNotIn(banned, url)


class TestSeasonMonths(unittest.TestCase):
    def test_the_first_of_every_month_the_season_touches(self):
        self.assertEqual(
            season_months("2027-05-01", "2027-08-31"),
            ["2027-05-01", "2027-06-01", "2027-07-01", "2027-08-01"],
        )

    def test_it_crosses_a_year_boundary(self):
        self.assertEqual(
            season_months("2027-12-01", "2028-02-15"),
            ["2027-12-01", "2028-01-01", "2028-02-01"],
        )

    def test_the_book_keeps_the_most_recent_readings(self):
        days = {f"2026-08-{n:02d}": {"offers": {}} for n in range(1, 11)}
        self.assertEqual(sorted(prune(days, 3)), ["2026-08-08", "2026-08-09", "2026-08-10"])


def book(days: dict) -> dict:
    return {"source": "padi.com", "days": days}


def day(offers, truncated: bool = False) -> dict:
    return {
        "url": "https://travel.padi.com/api/v2/travel/promotions/?country=120",
        "pages": 1,
        "stopped": "listing ended",
        "truncated": truncated,
        "offers": {d["slug"]: d for d in offers},
    }


def deal(slug="hammerhead-ii", **kwargs) -> dict:
    return PadiComAdapter.deal_from_payload(raw(slug=slug, **kwargs))


PADI = {
    "vessels": {
        "alia-soul": {"slug": "hammerhead-ii", "name": "Alia Soul"},
        "serenity": {"slug": "serenity", "name": "Serenity"},
    }
}


def fleet(alia=1450.0, serenity=1450.0):
    """Two boats, because a deal can only be placed on a boat this site carries.

    A one-boat fleet would let every "withdrawn" test pass for the wrong
    reason: the vessel would be absent from the panel because nothing here
    sells it, not because its offer went away.

    The two prices are arguments for the reason ``with_books`` takes one: a
    ladder is only this sailing's while its bottom rung is the price above it,
    so a test that hands these boats a discounted ladder has to hand them the
    row that ladder explains.
    """
    return candidate(
        [departure(price=alia),
         departure(boat="serenity", name="Northern Wrecks", price=serenity)],
        itineraries=[
            {"id": "alia-soul", "name": "Alia Soul", "boat": "Alia Soul"},
            {"id": "serenity", "name": "Serenity", "boat": "Serenity"},
        ],
    )


FLEET = fleet()


def promoted(days: dict) -> dict:
    return promote(FLEET, season=SEASON, padi=PADI, deals=book(days))


class TestPlacingADeal(unittest.TestCase):
    def test_a_deal_joins_through_the_vessel_not_the_label(self):
        payload = promoted({"2026-08-29": day([deal()])})
        offers = payload["deals"]["offers"]
        self.assertEqual([o["boat"] for o in offers], ["alia-soul"])
        self.assertEqual(offers[0]["boat_name"], "Alia Soul")

    def test_an_unmatched_vessel_warns_rather_than_vanishing(self):
        """The case this whole feature exists to catch, in its own test.

        A boat sailing Egypt and filed under the USA reaches the listing and
        matches nothing here. Dropping it silently reproduces the bug; naming
        it is what lets somebody notice.
        """
        payload = promoted({"2026-08-29": day([
            deal(), deal(slug="bahamas-aggressor-ii", shop="Bahamas Aggressor II"),
        ])})
        block = payload["deals"]
        self.assertEqual([o["boat"] for o in block["offers"]], ["alia-soul"])
        self.assertEqual([u["name"] for u in block["unmatched"]], ["Bahamas Aggressor II"])

    def test_both_the_converted_and_the_quoted_figure_survive(self):
        """A converted number presented as the seller's own is a small lie."""
        payload = promoted({"2026-08-29": day([deal(currency="USD", price=1372.0, was=1715.0)])})
        offer = payload["deals"]["offers"][0]
        self.assertEqual(offer["currency"], "USD")
        self.assertEqual(offer["quoted"], 1372.0)
        self.assertLess(offer["price"], 1372)  # euro, and dollars buy fewer of them

    def test_offers_are_ordered_by_when_you_would_sail(self):
        """Not by the size of the discount. That is a best-value ranking."""
        payload = promoted({"2026-08-29": day([
            deal(slug="serenity", shop="Serenity", start="2027-05-01", end="2027-05-08",
                 price=100.0, was=900.0),
            deal(start="2027-08-01", end="2027-08-08"),
        ])})
        self.assertEqual([o["start"] for o in payload["deals"]["offers"]],
                         ["2027-05-01", "2027-08-01"])

    def test_a_first_reading_says_so_rather_than_reporting_no_changes(self):
        payload = promoted({"2026-08-29": day([deal()])})
        self.assertTrue(payload["deals"]["first_reading"])
        self.assertNotIn("changes", payload["deals"])

    def test_a_book_with_no_readable_day_produces_no_panel(self):
        self.assertNotIn("deals", promote(candidate([departure()]), season=SEASON,
                                          padi=PADI, deals=book({})))


class TestTheChangeLog(unittest.TestCase):
    def _changes(self, before, after, **kwargs):
        payload = promoted({
            "2026-08-28": day(before, **kwargs),
            "2026-08-29": day(after),
        })
        return payload["deals"]["changes"]

    def test_a_vessel_that_was_not_there_yesterday_is_new(self):
        moved = self._changes([deal()], [deal(), deal(slug="serenity", shop="Serenity")])
        self.assertEqual(moved["new"], ["serenity"])
        self.assertEqual(moved["withdrawn"], [])
        self.assertEqual(moved["names"]["serenity"], "Serenity")

    def test_a_vessel_that_has_gone_is_withdrawn(self):
        moved = self._changes([deal(), deal(slug="serenity", shop="Serenity")], [deal()])
        self.assertEqual(moved["withdrawn"], ["serenity"])

    def test_a_price_move_is_reported_with_both_figures(self):
        moved = self._changes([deal(price=600.0)], [deal(price=500.0)])
        self.assertEqual(len(moved["changed"]), 1)
        change = moved["changed"][0]
        self.assertIn("price", change["moved"])
        self.assertEqual((change["before"]["price"], change["after"]["price"]), (600, 500))

    def test_the_same_offer_on_a_different_sailing_has_changed(self):
        """PADI names one exemplar sailing per vessel, and it moves.

        Reporting that as unchanged would let a boat's discount relocate to a
        week four months later under a line saying nothing happened.
        """
        moved = self._changes([deal()], [deal(start="2027-08-07", end="2027-08-14")])
        self.assertEqual(moved["changed"][0]["moved"], ["sailing"])

    def test_an_unmoved_offer_is_not_reported_at_all(self):
        moved = self._changes([deal()], [deal()])
        self.assertEqual((moved["new"], moved["withdrawn"], moved["changed"]), ([], [], []))

    def test_a_truncated_reading_yields_no_withdrawals(self):
        """A listing nobody finished knows nothing about what it did not reach.

        The same rule as `carry_unread` and the barren skip list: a page that
        was not read is not a page with nothing on it. Without this, one failed
        fetch reports PADI as having cancelled every offer it publishes.
        """
        moved = self._changes(
            [deal(), deal(slug="serenity", shop="Serenity")], [deal()], truncated=True
        )
        self.assertTrue(moved["partial"])
        self.assertEqual(moved["withdrawn"], [])
        self.assertEqual(moved["new"], [])


class TestItReachesThePage(unittest.TestCase):
    def _payload(self):
        promoted_payload = promoted({"2026-08-29": day([deal()])})
        promoted_payload["fx"] = {
            "base": "EUR", "as_of": "2026-08-28", "source": "test",
            "rates": {"USD": 1.17},
        }
        return build_payload(Dataset.from_dict(promoted_payload))

    def test_the_panel_ships_with_the_page_rather_than_being_fetched(self):
        """One file, nothing lazily loaded. A panel that fetched its own
        numbers would be the first thing here that could arrive blank."""
        page_payload = self._payload()
        self.assertEqual(len(page_payload["deals"]["offers"]), 1)
        self.assertEqual(page_payload["deals"]["read"], "2026-08-29")

    def test_a_dataset_with_no_deals_ships_no_key(self):
        """Page weight is load-bearing, and an empty key is a claim."""
        payload = promote(candidate([departure()]), season=SEASON)
        payload["fx"] = {"base": "EUR", "as_of": "2026-08-28", "source": "test",
                         "rates": {"USD": 1.17}}
        self.assertNotIn("deals", build_payload(Dataset.from_dict(payload)))


class TestPromotionStaysPure(unittest.TestCase):
    def test_the_same_book_promotes_to_the_same_panel_every_time(self):
        """`promote --check` compares byte for byte, so this cannot wobble."""
        days = {"2026-08-28": day([deal(price=600.0)]), "2026-08-29": day([deal()])}
        self.assertEqual(promoted(days)["deals"], promoted(days)["deals"])


if __name__ == "__main__":
    unittest.main()


def ladder(*cabins, currency="USD", boat="alia-soul", start="2027-05-01") -> dict:
    """One booking page's cabin ladder, as `fetch_cabins.py` records it."""
    return {
        "boat": boat, "start": start, "currency": currency,
        "cabins": [{"name": n, "price": p, "list_price": lp} for n, p, lp in cabins],
    }


def cabin_book(*records) -> dict:
    return {"collected": "2026-08-28",
            "departures": {f"{r['boat']}::{r['start']}": r for r in records}}


def padi_book(*rows) -> dict:
    return {"collected": "2026-08-29",
            "departures": {f"{r['boat']}::{r['start']}": r for r in rows}}


def sailing(boat="alia-soul", start="2027-05-01", price=1450.0, was=None,
            currency="USD", nights=7, **extra) -> dict:
    # `end` is derived rather than pinned: a fixed end date with a later start
    # is a negative night count, and `promote` drops such a row rather than
    # publishing a sailing that arrives before it leaves. A helper that can
    # produce one silently tests nothing.
    finish = (date.fromisoformat(start) + timedelta(days=nights)).isoformat()
    row = {"boat": boat, "slug": boat, "start": start, "end": finish,
           "nights": nights, "price": price, "currency": currency, "was": was or price}
    row.update(extra)
    return row


def with_books(cabins=None, padi_departures=None, price=1450.0, currency="USD"):
    """One departure and whichever books this test is about.

    ``price`` exists because **the advertised price is the ladder's bottom
    rung** -- on 864 of 864 -- and a fixture that ignores that is testing a
    sailing whose two halves describe different weeks. `promote` now throws
    such a ladder away rather than reading a discount off it, so a test that
    wants a sale has to state a row price its ladder actually explains. Every
    fixture below that was silently in that state has been made to agree; see
    ``TestAStaleLadderCannotSpeak`` for the case that must not.
    """
    return promote(candidate([departure(price=price, currency=currency)]), season=SEASON,
                   cabins=cabins, padi_departures=padi_departures)


def only_row(payload):
    return payload["departures"][0]


class TestFlaggingASale(unittest.TestCase):
    """A sale is one seller's list price beside the price it charges."""

    def test_the_cheapest_rung_carries_the_answer(self):
        """The advertised price is the bottom of the ladder, on 864 of 864.

        And a discount is a whole-ladder fact: on all 263 discounted sailings
        read, every cabin is marked down by the same percentage.
        """
        payload = with_books(cabin_book(ladder(
            ("Twin", 900.0, 1000.0), ("Suite", 1350.0, 1500.0))), price=900.0)
        self.assertEqual(only_row(payload)["sale"]["pct"], 10)

    def test_the_cheapest_price_is_never_set_against_a_dearer_room_s_list_price(self):
        """The obvious mistake here, pinned so it cannot come back.

        Comparing the cheapest cabin's price to the dearest cabin's list price
        reports Red Sea Aggressor II's 33% sale as 40%.
        """
        payload = with_books(cabin_book(ladder(
            ("Deluxe", 1849.0, 2760.0), ("Master", 1983.0, 2960.0),
            ("Suite", 2050.0, 3060.0))), price=1849.0)
        self.assertEqual(only_row(payload)["sale"]["pct"], 33)

    def test_a_ladder_at_list_price_is_not_a_sale(self):
        payload = with_books(cabin_book(ladder(("Twin", 1000.0, 1000.0))), price=1000.0)
        self.assertNotIn("sale", only_row(payload))

    def test_padi_s_compare_at_price_is_read_too(self):
        """It flags the row, and does not price it.

        The row prints liveaboard.com's fare, so PADI's markdown says a sale
        exists without saying this fare is 20% off — which it is not.
        """
        payload = with_books(padi_departures=padi_book(sailing(price=1000.0, was=1250.0)))
        sale = only_row(payload)["sale"]
        self.assertEqual(sale["sellers"], [1])
        self.assertNotIn("pct", sale)

    def test_padi_prices_the_sale_on_a_row_it_is_the_only_seller_of(self):
        """There PADI *is* the row's own seller, so its percentage is the row's.

        `promote` blanks its PADI-price variable on such a row to keep one
        seller out of the other's field; the sale must not be read from that
        blanked value or a PADI-only row could never show a discount.
        """
        payload = promote(
            candidate([departure()]), season=SEASON,
            padi_departures=padi_book(sailing(
                start="2027-06-05", price=900.0, was=1200.0,
                # A parsable title, because that is what founds the itinerary a
                # PADI-only row hangs on; without one the row is reported and
                # skipped rather than published under a name this code invented.
                itinerary="Northern Wrecks (Hurghada - Hurghada) 7 Nights")),
        )
        row = next(d for d in payload["departures"] if d["start"] == "2027-06-05")
        self.assertTrue(row["padi_only"])
        self.assertEqual(row["sale"], {"sellers": [1], "pct": 25, "was": row["sale"]["was"]})

    def test_a_booking_page_nobody_read_states_nothing(self):
        """Not a "no". Three of the five PADI-only discounts are exactly this.

        An unread ladder must not read as an undiscounted one, and must not
        stop the other seller reporting what it does know.
        """
        payload = with_books(cabins=cabin_book(),
                             padi_departures=padi_book(sailing(price=1000.0, was=1250.0)))
        self.assertEqual(only_row(payload)["sale"]["sellers"], [1])

    def test_both_sellers_are_named_when_both_discount(self):
        payload = with_books(cabin_book(ladder(("Twin", 800.0, 1000.0))),
                             padi_book(sailing(price=800.0, was=1000.0)),
                             price=800.0)
        self.assertEqual(only_row(payload)["sale"]["sellers"], [0, 1])

    def test_one_seller_never_marks_down_the_other_s_price(self):
        """The two Red Sea Aggressor IV sailings, in miniature.

        PADI discounts; the site this row's price comes from does not. The row
        is on sale and says so, and carries no percentage — printing PADI's
        33% against an undiscounted fare would invent a saving.
        """
        payload = with_books(cabin_book(ladder(("Twin", 1000.0, 1000.0))),
                             padi_book(sailing(price=900.0, was=1200.0)),
                             price=1000.0)
        sale = only_row(payload)["sale"]
        self.assertEqual(sale["sellers"], [1])
        self.assertNotIn("pct", sale)
        self.assertNotIn("was", sale)

    def test_the_was_price_is_converted_like_every_other_figure(self):
        """Normalisation happens in Python only; the browser converts nothing."""
        payload = with_books(cabin_book(ladder(("Twin", 900.0, 1200.0), currency="USD")),
                             price=900.0)
        self.assertLess(only_row(payload)["sale"]["was"], 1200)


class TestTheOnSaleSummary(unittest.TestCase):
    def _summary(self):
        payload = promote(
            fleet(alia=900.0, serenity=800.0), season=SEASON, padi=PADI,
            cabins=cabin_book(
                ladder(("Twin", 900.0, 1000.0)),
                ladder(("Twin", 800.0, 1000.0), boat="serenity"),
            ),
        )
        return payload["deals"]["on_sale"], payload

    def test_it_counts_the_very_departures_the_filter_selects(self):
        """Panel and chip cannot disagree, because there is one list.

        A summary computed down a second path is a summary that drifts from
        the thing it summarises.
        """
        summary, payload = self._summary()
        flagged = [d for d in payload["departures"] if d.get("sale")]
        self.assertEqual(summary["sailings"], len(flagged))

    def test_each_boat_states_its_window(self):
        """What PADI's exemplar cannot say: which weeks are actually on sale."""
        summary, _ = self._summary()
        row = next(b for b in summary["boats"] if b["boat"] == "alia-soul")
        self.assertEqual((row["first"], row["last"]), ("2027-05-01", "2027-05-01"))
        self.assertEqual((row["sailings"], row["of"]), (1, 1))
        self.assertEqual(row["pct"], 10)

    def test_it_carries_the_day_each_seller_was_read(self):
        """A sale is what a seller claimed when it was looked at.

        Per seller, not per panel. The two books are read by two jobs on two
        days -- 28 and 30 August in the published data -- and one date over
        both dated ten of the twenty-two boats wrong.
        """
        summary, _ = self._summary()
        self.assertEqual(summary["read"]["0"], "2026-08-28")
        row = next(b for b in summary["boats"] if b["boat"] == "alia-soul")
        self.assertEqual(row["read"], ["2026-08-28"])

    def test_the_dates_stay_in_lockstep_with_the_sellers(self):
        """One entry per seller, `None` included.

        The page reads the two lists in parallel, so a seller with no reading
        date must leave a hole rather than shorten the list: dropping it shifts
        every date after it onto the wrong seller's name, which is the one
        thing this pair exists to prevent.
        """
        summary, _ = self._summary()
        for row in summary["boats"]:
            with self.subTest(boat=row["boat"]):
                self.assertEqual(len(row["read"]), len(row["sellers"]))

    def test_a_fleet_with_nothing_discounted_produces_no_summary(self):
        payload = promote(fleet(alia=1000.0), season=SEASON, padi=PADI,
                          cabins=cabin_book(ladder(("Twin", 1000.0, 1000.0))))
        self.assertNotIn("deals", payload)

    def test_the_panel_ships_without_padi_s_listing(self):
        """The two halves are independent: one file can be absent."""
        _, payload = self._summary()
        self.assertNotIn("offers", payload["deals"])
        self.assertIn("on_sale", payload["deals"])


class TestTheEdgesOfASale(unittest.TestCase):
    def test_a_markdown_too_small_to_state_gets_no_percentage(self):
        """"0% off" beside a price is worse than the nothing it means.

        The row stays on sale on the strength of `sellers`, which is the fact
        that does not round away.
        """
        payload = with_books(cabin_book(ladder(("Twin", 997.0, 1000.0))), price=997.0)
        sale = only_row(payload)["sale"]
        self.assertEqual(sale["sellers"], [0])
        self.assertNotIn("pct", sale)

    def test_a_ladder_with_no_currency_still_yields_a_percentage(self):
        """A ratio needs no unit; a cash figure does.

        The percentage survives because both numbers are in whatever the
        unnamed currency is. `was` is withheld rather than converted at an
        assumed rate — the rule that stops a dollar price being read as euro.
        """
        record = ladder(("Twin", 900.0, 1000.0))
        record.pop("currency")
        payload = with_books(cabin_book(record), price=900.0)
        sale = only_row(payload)["sale"]
        self.assertEqual(sale["pct"], 10)
        self.assertNotIn("was", sale)


def week(start: str, price: float, boat: str = "alia-soul") -> dict:
    """One weekly sailing. The end date is derived, never pinned: a fixed one
    behind a later start is a negative night count and `promote` drops the row
    rather than publishing a trip that arrives before it leaves."""
    finish = (date.fromisoformat(start) + timedelta(days=7)).isoformat()
    return departure(boat=boat, start=start, end=finish, price=price)


WEEKS = ("2027-05-01", "2027-05-08", "2027-05-15", "2027-05-22")


def season_of(discounted, weeks=WEEKS, **kwargs) -> dict:
    """One boat's season, discounted on the weeks named and not on the others.

    A ladder only where there is a sale, and its bottom rung is the row's own
    price -- the rule `_drop_stale_ladder` enforces, so a fixture that ignores
    it is testing a ladder `promote` has thrown away.
    """
    return promote(
        candidate([week(start, price=900.0 if start in discounted else 1000.0)
                   for start in weeks]),
        season=SEASON,
        cabins=cabin_book(*[ladder(("Twin", 900.0, 1000.0), start=start)
                            for start in discounted]),
        **kwargs,
    )


class TestARunIsAWindow(unittest.TestCase):
    """`first` and `last` are printed under From and To, so they are a window.

    They were the first and last of everything a boat had discounted, which is
    a window only where nothing in between is at full price. Three boats in the
    published season have a hole: All Star Scuba Scene's row read "03 May to 05
    Jul" over four June sailings nobody had marked down, so a reader shopping
    June was told a fifth of that boat's year was cut when none of that month
    was. The hover said first-and-last; the column headings said From and To,
    and a heading beats a hover.
    """

    def _rows(self, discounted, **kwargs):
        return season_of(discounted, **kwargs)["deals"]["on_sale"]["boats"]

    def test_an_unbroken_run_is_one_row(self):
        rows = self._rows(WEEKS)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["first"], rows[0]["last"]), (WEEKS[0], WEEKS[-1]))
        self.assertEqual((rows[0]["sailings"], rows[0]["of"]), (4, 4))

    def test_a_discount_that_stops_and_starts_again_is_two_rows(self):
        rows = self._rows((WEEKS[0], WEEKS[1], WEEKS[3]))
        self.assertEqual(
            [(r["first"], r["last"], r["sailings"]) for r in rows],
            [(WEEKS[0], WEEKS[1], 2), (WEEKS[3], WEEKS[3], 1)],
        )

    def test_every_row_still_counts_against_the_whole_season(self):
        """`of` is what the boat sells, not what this run covers: two rows
        reading "2 of 4" and "1 of 4" is the denominator a reader asked for."""
        rows = self._rows((WEEKS[0], WEEKS[1], WEEKS[3]))
        self.assertEqual({r["of"] for r in rows}, {4})
        self.assertEqual(sum(r["sailings"] for r in rows), 3)

    def test_no_sailing_inside_a_window_is_at_full_price(self):
        """The claim the two dates make, asserted as a claim.

        Every row of every shape this fixture can produce: whatever falls
        between `first` and `last` is discounted, which is what lets the page
        print those two dates as a window at all.
        """
        for hole in ((), (WEEKS[1],), (WEEKS[1], WEEKS[2]), (WEEKS[2],)):
            discounted = [w for w in WEEKS if w not in hole]
            with self.subTest(full_price=hole):
                payload = season_of(discounted)
                inside = {
                    d["start"]: bool(d.get("sale"))
                    for row in payload["deals"]["on_sale"]["boats"]
                    for d in payload["departures"]
                    if row["first"] <= d["start"] <= row["last"]
                }
                self.assertTrue(inside)
                self.assertTrue(all(inside.values()), inside)

    def test_the_sailing_count_is_still_the_departures_the_filter_selects(self):
        """One row per run, and the total is unchanged by the split: it counts
        discounted sailings, not rows."""
        payload = season_of((WEEKS[0], WEEKS[1], WEEKS[3]))
        self.assertEqual(payload["deals"]["on_sale"]["sailings"], 3)


class TestAnOfferThatOnlyNamesARun(unittest.TestCase):
    """PADI speaking twice about one sale is not two sales.

    The table is the union of two books because they publish different shapes
    (#145) -- liveaboard.com strikes a list price through beside every
    discounted cabin, so its evidence is a run of sailings; PADI advertises a
    named offer against one sailing and states no window for it. But a run
    row's `sellers` comes from the departures and **both** books feed those, so
    where PADI marks a sailing down and advertises the same campaign it got a
    row of its own as well: "Hammerhead II, 01 May to 28 Aug, 15% off,
    liveaboard.com and padi.com" above "Hammerhead II, 15 to 18 May, 15% off,
    padi.com". Nested, same rate, reading as a second and narrower sale -- all
    eight offers on the published fleet did it.

    So a restatement is folded and a fact of its own is not, and the three
    conditions are what separate them.
    """

    def _promoted(self, *, offer_kwargs=None, padi_was=1000.0, discounted=WEEKS):
        offer = dict(start=WEEKS[0], end=WEEKS[1], value=10.0, title="Save 10%")
        offer.update(offer_kwargs or {})
        return season_of(
            discounted,
            padi=PADI,
            deals=book({"2026-08-29": day([deal(**offer)])}),
            padi_departures=padi_book(*[
                sailing(start=start, price=900.0, was=padi_was) for start in discounted
            ]) if padi_was else None,
        )

    def _rows(self, payload):
        return payload["deals"]["on_sale"]["boats"]

    def test_the_run_takes_padi_s_name_for_it(self):
        payload = self._promoted()
        rows = self._rows(payload)
        self.assertEqual(len(rows), 1)
        self.assertEqual([o["title"] for o in rows[0]["offers"]], ["Save 10%"])
        self.assertTrue(rows[0]["offers"][0]["url"])

    def test_the_offer_is_marked_rather_than_deleted(self):
        """The deals book keeps everything it read: the day-to-day diff above
        the table needs every offer, folded or not."""
        payload = self._promoted()
        offers = payload["deals"]["offers"]
        self.assertEqual(len(offers), 1)
        self.assertTrue(offers[0]["in_run"])

    def test_a_rate_the_run_does_not_state_keeps_its_own_row(self):
        """Two books disagreeing about a sailing is a fact, not a duplicate."""
        payload = self._promoted(offer_kwargs={"value": 30.0, "price": 700.0})
        self.assertNotIn("offers", self._rows(payload)[0])
        self.assertNotIn("in_run", payload["deals"]["offers"][0])

    def test_a_sailing_outside_every_window_keeps_its_own_row(self):
        """A sale the other seller's booking pages do not carry."""
        payload = self._promoted(discounted=WEEKS[:2],
                                 offer_kwargs={"start": WEEKS[3], "end": WEEKS[3]})
        self.assertFalse(any("offers" in r for r in self._rows(payload)))
        self.assertNotIn("in_run", payload["deals"]["offers"][0])

    def test_a_run_padi_did_not_mark_down_keeps_the_offer_separate(self):
        """Folded only where the row already names PADI. Otherwise the fold
        would be putting one seller's campaign on the other's evidence, which
        is the join #145 refused."""
        payload = self._promoted(padi_was=None)
        row = self._rows(payload)[0]
        self.assertEqual(row["sellers"], [0])
        self.assertNotIn("offers", row)
        self.assertNotIn("in_run", payload["deals"]["offers"][0])

    def test_an_offer_with_no_rate_is_never_folded(self):
        """"Free night(s)" takes nothing off a nightly rate, so there is no
        figure to call a restatement of the run's own."""
        payload = self._promoted(offer_kwargs={"kind": 30, "value": 0.0,
                                               "title": "Free night(s)"})
        self.assertNotIn("offers", self._rows(payload)[0])
        self.assertNotIn("in_run", payload["deals"]["offers"][0])


class TestTheShippedSaleRows(unittest.TestCase):
    """The two claims the sale table makes, asserted against what shipped.

    Through `published`, because both are facts about a committed dataset: read
    directly they would sit in front of the fetches and could stop the only
    jobs able to correct whatever they were complaining about.
    """

    def setUp(self):
        self.page = published.raw()
        self.deals = self.page.get("deals") or {}
        if not (self.deals.get("on_sale") or {}).get("boats"):
            self.skipTest("nothing is discounted in the committed dataset")
        boat_of = {i["id"]: i["boat_id"] for i in self.page["itineraries"]}
        self.by_boat: dict[str, list[dict]] = {}
        for row in self.page["departures"]:
            self.by_boat.setdefault(boat_of[row["itinerary_id"]], []).append(row)

    def test_no_full_price_sailing_falls_inside_a_published_window(self):
        """From and To are a window, so what is between them is on sale.

        All Star Scuba Scene shipped "03 May to 05 Jul" over four June
        sailings at €2,395 with nothing off them.
        """
        for run in self.deals["on_sale"]["boats"]:
            inside = [d for d in self.by_boat[run["boat"]]
                      if run["first"] <= d["start"] <= run["last"]]
            with self.subTest(boat=run["boat_name"], window=(run["first"], run["last"])):
                self.assertEqual(len(inside), run["sailings"])
                self.assertEqual(
                    [d["start"] for d in inside if not d.get("sale")], [],
                    "a sailing at full price sits inside a window the page "
                    "prints as discounted",
                )

    def test_no_two_windows_for_one_boat_overlap(self):
        """Runs are maximal and disjoint, so two rows for a boat are two
        separate spells and never one restated."""
        seen: dict[str, list[tuple[str, str]]] = {}
        for run in self.deals["on_sale"]["boats"]:
            seen.setdefault(run["boat"], []).append((run["first"], run["last"]))
        for boat, windows in seen.items():
            windows.sort()
            for (_, ends), (starts, _) in zip(windows, windows[1:]):
                with self.subTest(boat=boat):
                    self.assertLess(ends, starts)

    def test_no_offer_is_both_folded_and_drawn_on_its_own(self):
        """The duplicate this fold removes, asserted at the shipped end.

        A folded offer names a run; an unfolded one is a row. Every offer is
        exactly one of the two, and an offer marked folded has to have landed
        on a run or the campaign name is nowhere at all.
        """
        named = [
            offer["title"]
            for run in self.deals["on_sale"]["boats"]
            for offer in run.get("offers") or []
        ]
        folded = [o["title"] for o in self.deals.get("offers") or [] if o.get("in_run")]
        self.assertEqual(sorted(named), sorted(folded))

    def test_a_folded_offer_restates_the_run_it_names(self):
        """The three conditions, at the shipped end: same boat, PADI already on
        the row, PADI's own rate inside the row's, and the advertised sailing
        inside the window."""
        padi = 1
        runs = {(r["boat"], r["first"]): r for r in self.deals["on_sale"]["boats"]}
        for offer in self.deals.get("offers") or []:
            if not offer.get("in_run"):
                continue
            hosts = [r for key, r in runs.items() if key[0] == offer["boat"]
                     and any(o["title"] == offer["title"] for o in r.get("offers") or [])]
            with self.subTest(boat=offer["boat_name"], offer=offer["title"]):
                self.assertEqual(len(hosts), 1)
                run = hosts[0]
                self.assertIn(padi, run["sellers"])
                self.assertLessEqual(run["pct"], round(offer["value"]))
                self.assertLessEqual(round(offer["value"]),
                                     run.get("pct_max", run["pct"]))
                self.assertLessEqual(run["first"], offer["start"])
                self.assertLessEqual(offer["start"], run["last"])


class TestTheSummaryReadsInOrder(unittest.TestCase):
    def test_boats_are_ordered_by_the_name_the_panel_prints(self):
        """Sorted on the boat id this read "Ocean Lovers, Oceanix, MY Odyssey
        Liveaboard" — alphabetical in a column nobody can see."""
        payload = promote(
            fleet(alia=900.0, serenity=800.0), season=SEASON, padi=PADI,
            cabins=cabin_book(
                ladder(("Twin", 900.0, 1000.0)),
                ladder(("Twin", 800.0, 1000.0), boat="serenity"),
            ),
        )
        names = [b["boat_name"] for b in payload["deals"]["on_sale"]["boats"]]
        self.assertEqual(names, sorted(names, key=str.lower))


class TestTheTakeawayCarriesIt(unittest.TestCase):
    def test_the_csv_states_the_list_price_and_the_cut(self):
        """The page can filter on a sale, so the file a reader takes away has
        to be able to as well."""
        from liveaboard.export import to_csv

        payload = with_books(cabin_book(ladder(("Twin", 900.0, 1000.0), currency="EUR")),
                             price=900.0, currency="EUR")
        payload["fx"] = {"base": "EUR", "as_of": "2026-08-28", "source": "test",
                         "rates": {"USD": 1.17}}
        rows = list(csv.DictReader(io.StringIO(to_csv(Dataset.from_dict(payload)))))
        self.assertEqual(rows[0]["list_price"], "1000")
        self.assertEqual(rows[0]["discount_pct"], "10")

    def test_an_undiscounted_row_leaves_both_columns_empty(self):
        """Not a zero. A 0 in a spreadsheet gets averaged."""
        from liveaboard.export import to_csv

        payload = promote(candidate([departure()]), season=SEASON)
        payload["fx"] = {"base": "EUR", "as_of": "2026-08-28", "source": "test",
                         "rates": {"USD": 1.17}}
        rows = list(csv.DictReader(io.StringIO(to_csv(Dataset.from_dict(payload)))))
        self.assertEqual((rows[0]["list_price"], rows[0]["discount_pct"]), ("", ""))


class TestThePanelSaysWhatItCouldNotRead(unittest.TestCase):
    """Every absence under this heading looks like "not on sale" unless it is
    stated, and three of them are not that at all.

    A ladder thrown away for contradicting its row, a sailing no seller
    published a list price for, and a trip-name banner the seller read for it
    does not support. `promote` counts each, because none can be recovered from
    the rows the panel is drawn from -- that is what makes them absences.
    """

    def promoted(self, **books):
        return promote(candidate([departure(price=books.pop("price", 1450.0))]),
                       season=SEASON, **books)

    def coverage(self, payload):
        return (payload.get("deals") or {}).get("coverage") or {}

    def banner_fleet(self, alia):
        """One boat whose trip name claims a discount, and a second that really
        is on sale — because coverage is attached only to a panel that exists,
        and a fleet with nothing discounted anywhere opens none."""
        return candidate(
            [departure(name="10% Off: Brothers, Daedalus & Elphinstone", price=alia),
             departure(boat="serenity", name="Northern Wrecks", price=800.0)],
            itineraries=[
                {"id": "alia-soul", "name": "Alia Soul", "boat": "Alia Soul"},
                {"id": "serenity", "name": "Serenity", "boat": "Serenity"},
            ],
        )

    def test_a_rejected_ladder_is_counted_and_its_boat_named(self):
        """Named rather than counted, like the deals listing's strangers: only
        a name tells the boat somebody was about to book from one they were
        not."""
        payload = promote(
            fleet(alia=2760.0, serenity=800.0), season=SEASON,
            cabins=cabin_book(
                ladder(("Deluxe", 1849.0, 2760.0)),
                ladder(("Twin", 800.0, 1000.0), boat="serenity"),
            ),
        )
        self.assertEqual(
            self.coverage(payload)["dropped"],
            {"sailings": 1, "boats": ["Alia Soul"]},
        )

    def test_a_sailing_no_seller_priced_is_counted_as_unread(self):
        """Not as undiscounted. Nobody looked."""
        payload = promote(
            fleet(serenity=800.0), season=SEASON,
            cabins=cabin_book(ladder(("Twin", 800.0, 1000.0), boat="serenity")),
        )
        self.assertEqual(self.coverage(payload)["unread"], 1)

    def test_a_banner_no_read_seller_supports_is_counted(self):
        """The operator's own "10% Off" against a seller stating list price.

        The banner stays out of the dataset's answer -- the struck-through
        price is the number and it wins -- but a corroborating field that has
        stopped corroborating is worth a sentence, or it is worth deleting.
        """
        payload = promote(self.banner_fleet(alia=1000.0), season=SEASON,
                          cabins=cabin_book(
                              ladder(("Twin", 1000.0, 1000.0)),
                              ladder(("Twin", 800.0, 1000.0), boat="serenity")))
        row = next(d for d in payload["departures"] if d["id"].startswith("alia"))
        self.assertEqual(row["promotion"], "10% Off")
        self.assertNotIn("sale", row)
        self.assertEqual(self.coverage(payload)["banner_unsupported"], 1)

    def test_a_banner_the_ladder_agrees_with_is_not_counted(self):
        """The other 205 of 209. Corroboration is not a disagreement."""
        payload = promote(self.banner_fleet(alia=900.0), season=SEASON,
                          cabins=cabin_book(
                              ladder(("Twin", 900.0, 1000.0)),
                              ladder(("Twin", 800.0, 1000.0), boat="serenity")))
        self.assertNotIn("banner_unsupported", self.coverage(payload))

    def test_coverage_never_ships_without_a_panel_to_qualify(self):
        """It qualifies an answer. With nothing discounted, nothing advertised
        and nothing moved there is no answer, and a panel headed "on sale"
        opening to say only how many rows went unread is noise."""
        payload = promote(candidate([departure()]), season=SEASON)
        self.assertNotIn("deals", payload)
