"""Tests for turning a scrape candidate into a publishable dataset."""

from __future__ import annotations

import json
import unittest
from datetime import date

from liveaboard.dataset import Dataset
from liveaboard.promote import promote, slugify
from liveaboard.render import build_payload

SEASON = (date(2027, 5, 1), date(2027, 8, 31))

PROV = {"kind": "scraped", "source_id": "liveaboard.com", "retrieved": "2026-08-27"}


def departure(
    boat: str = "alia-soul",
    name: str = "Brothers, Daedalus & Elphinstone",
    start: str = "2027-05-01",
    end: str = "2027-05-08",
    price: float = 1450.0,
    currency: str = "USD",
    **extra,
) -> dict:
    return {
        "id": f"{boat}-{start}",
        "boat_slug": boat,
        "name": name,
        "start": start,
        "end": end,
        "price": {"amount": price, "currency": currency},
        "provenance": PROV,
        **extra,
    }


def candidate(departures, itineraries=None) -> dict:
    return {
        "scraped_at": "2026-08-27",
        "itineraries": itineraries
        or [{"id": "alia-soul", "name": "Alia Soul", "boat": "Alia Soul"}],
        "departures": departures,
    }


class TestGrouping(unittest.TestCase):
    def test_same_boat_different_trips_stay_separate(self):
        """A boat sells several routes; averaging them hides the difference."""
        payload = promote(
            candidate(
                [
                    departure(name="Brothers, Daedalus & Elphinstone"),
                    departure(name="Northern Wrecks", start="2027-05-15", end="2027-05-22"),
                ]
            ),
            season=SEASON,
        )
        self.assertEqual(len(payload["itineraries"]), 2)
        self.assertEqual(len(payload["boats"]), 1)

    def test_same_trip_across_dates_is_one_itinerary(self):
        payload = promote(
            candidate(
                [
                    departure(start="2027-05-01", end="2027-05-08"),
                    departure(start="2027-06-05", end="2027-06-12"),
                ]
            ),
            season=SEASON,
        )
        self.assertEqual(len(payload["itineraries"]), 1)
        self.assertEqual(len(payload["departures"]), 2)

    def test_nights_are_derived_from_the_dates(self):
        payload = promote(candidate([departure()]), season=SEASON)
        self.assertEqual(payload["itineraries"][0]["nights"], 7)

    def test_port_comes_from_the_event_location(self):
        payload = promote(
            candidate([departure(location="Port Ghalib")]), season=SEASON
        )
        self.assertEqual(payload["itineraries"][0]["port_from"], "Port Ghalib")


class TestRejection(unittest.TestCase):
    def test_departures_outside_the_season_are_dropped(self):
        payload = promote(
            candidate([departure(start="2027-11-06", end="2027-11-13")]), season=SEASON
        )
        self.assertEqual(payload["departures"], [])

    def test_implausible_dates_are_skipped_and_reported(self):
        """A 400-night cruise is a parsing error, not a product."""
        payload = promote(
            candidate([departure(start="2027-05-01", end="2028-06-01")]), season=SEASON
        )
        self.assertEqual(payload["departures"], [])
        self.assertTrue(payload["promotion_skipped"])

    def test_a_departure_with_no_boat_is_skipped(self):
        broken = departure()
        del broken["boat_slug"]
        payload = promote(candidate([broken]), season=SEASON)
        self.assertEqual(payload["departures"], [])


class TestFeesAreUnknownNotZero(unittest.TestCase):
    """The distinction this whole project turns on."""

    def setUp(self):
        payload = promote(candidate([departure()]), season=SEASON)
        self.dataset = Dataset.from_dict(payload)
        self.rendered = build_payload(self.dataset)["departures"][0]

    def test_scraped_itineraries_carry_no_fee_lines(self):
        self.assertEqual(self.dataset.itineraries[
            list(self.dataset.itineraries)[0]
        ].fees, [])

    def test_the_page_is_told_the_fees_are_unknown(self):
        self.assertFalse(self.rendered["fees_known"])

    def test_no_true_cost_is_claimed(self):
        """Nobody has looked, so the advertised price is all we can show."""
        self.assertFalse(self.rendered["mandatory_known"])


class TestClassificationSurvivesTheMissingSiteList(unittest.TestCase):
    def test_dive_sites_are_recovered_from_the_trip_name(self):
        """The source publishes no site list, but names routes after sites."""
        payload = promote(candidate([departure()]), season=SEASON)
        sites = payload["itineraries"][0]["dive_sites"]
        self.assertIn("brothers", sites)
        self.assertIn("daedalus", sites)

    def test_a_scraped_trip_still_gets_a_route(self):
        dataset = Dataset.from_dict(promote(candidate([departure()]), season=SEASON))
        classification = next(iter(dataset.classifications().values()))
        self.assertIsNotNone(classification.route)

    def test_an_unrecognisable_name_yields_no_sites(self):
        payload = promote(
            candidate([departure(name="Summer Special Week")]), season=SEASON
        )
        self.assertEqual(payload["itineraries"][0]["dive_sites"], [])


class TestPayloadValidity(unittest.TestCase):
    def test_promoted_payload_passes_dataset_validation(self):
        payload = promote(
            candidate(
                [
                    departure(),
                    departure(boat="all-star-ghani", start="2027-07-03", end="2027-07-10"),
                ],
                itineraries=[
                    {"id": "alia-soul", "boat": "Alia Soul"},
                    {"id": "all-star-ghani", "boat": "All Star Ghani"},
                ],
            ),
            season=SEASON,
        )
        dataset = Dataset.from_dict(payload)
        self.assertEqual(len(dataset.departures), 2)
        self.assertEqual(len(dataset.boats), 2)

    def test_prices_keep_their_scraped_currency(self):
        payload = promote(candidate([departure(currency="USD")]), season=SEASON)
        self.assertEqual(payload["departures"][0]["price"]["currency"], "USD")


class TestSlugify(unittest.TestCase):
    def test_punctuation_collapses(self):
        self.assertEqual(slugify("Brothers, Daedalus & Elphinstone"), "brothers-daedalus-elphinstone")


class TestFeeMerge(unittest.TestCase):
    """Fees arrive from a separate weekly browser run, keyed by vessel."""

    FEES = {
        "scraped_at": "2026-08-27",
        "vessels": {
            "alia-soul": {
                "source_url": "https://www.liveaboard.com/diving/egypt/alia-soul",
                "fees": [
                    {
                        "code": "marine_park",
                        "tier": "mandatory",
                        "basis": "per_trip",
                        "included": False,
                        "amount": {"amount": 35.0, "currency": "EUR"},
                        "amount_max": {"amount": 100.0, "currency": "EUR"},
                        "provenance": PROV,
                    }
                ],
            }
        },
    }

    def test_fees_reach_the_itinerary(self):
        payload = promote(candidate([departure()]), season=SEASON, fees=self.FEES)
        self.assertEqual(len(payload["itineraries"][0]["fees"]), 1)

    def test_the_same_fees_apply_to_every_trip_that_vessel_sells(self):
        """They are a property of the boat, not the sailing."""
        payload = promote(
            candidate(
                [
                    departure(name="Brothers, Daedalus & Elphinstone"),
                    departure(name="Northern Wrecks", start="2027-06-05", end="2027-06-12"),
                ]
            ),
            season=SEASON,
            fees=self.FEES,
        )
        self.assertEqual(len(payload["itineraries"]), 2)
        for itinerary in payload["itineraries"]:
            self.assertEqual(len(itinerary["fees"]), 1)

    def test_a_vessel_the_fee_run_missed_stays_unknown(self):
        """Absent fees must never render as no fees."""
        payload = promote(
            candidate(
                [departure(boat="unvisited")],
                itineraries=[{"id": "unvisited", "boat": "Unvisited"}],
            ),
            season=SEASON,
            fees=self.FEES,
        )
        self.assertEqual(payload["itineraries"][0]["fees"], [])

    def test_no_fee_file_at_all_is_not_an_error(self):
        payload = promote(candidate([departure()]), season=SEASON, fees=None)
        self.assertEqual(payload["itineraries"][0]["fees"], [])

    def test_merged_fees_produce_a_cost_range(self):
        from liveaboard.pricing import compute

        dataset = Dataset.from_dict(
            promote(candidate([departure()]), season=SEASON, fees=self.FEES)
        )
        itinerary = next(iter(dataset.itineraries.values()))
        breakdown = compute(itinerary, dataset.departures[0], dataset.fx)
        self.assertTrue(breakdown.is_range)
        self.assertGreater(breakdown.total_max.amount, breakdown.total.amount)


class TestPortsFromTitle(unittest.TestCase):
    """The source's Event location is "Egypt"; the title names real harbours."""

    def promote_one(self, name: str, location: str | None = "Egypt") -> dict:
        payload = promote(
            candidate([departure(name=name, location=location)]), season=SEASON
        )
        return payload["itineraries"][0]

    def test_a_round_trip_reads_both_ports(self):
        itinerary = self.promote_one("North & Tiran (Hurghada - Hurghada)")
        self.assertEqual(itinerary["port_from"], "Hurghada")
        self.assertEqual(itinerary["port_to"], "Hurghada")

    def test_a_one_way_keeps_its_two_ports(self):
        """A visitor books two airports off this; averaging them is a lie."""
        itinerary = self.promote_one(
            "Marine Park North: Brothers - Daedalus & Elphinstone "
            "(Port Ghalib - Safaga/Soma Bay)"
        )
        self.assertEqual(itinerary["port_from"], "Port Ghalib")
        # Folded onto Safaga: Soma Bay is a resort bay ten kilometres up the
        # coast and the operator names both because it takes whichever berth
        # it is given. Still two different ports, which is the point here.
        self.assertEqual(itinerary["port_to"], "Safaga")
        self.assertNotEqual(itinerary["port_from"], itinerary["port_to"])

    def test_the_title_beats_the_country_the_source_reports(self):
        itinerary = self.promote_one("North (Hurghada - Hurghada)")
        self.assertNotEqual(itinerary["port_from"], "Egypt")

    def test_a_route_in_brackets_is_not_mistaken_for_ports(self):
        """"(Brothers - Daedalus)" is a route. Neither is a harbour."""
        itinerary = self.promote_one("Red Sea Classic (Brothers - Daedalus)")
        self.assertEqual(itinerary["port_from"], "Egypt")

    def test_no_ports_anywhere_stays_unknown(self):
        itinerary = self.promote_one("Ultimate Red Sea", location=None)
        self.assertEqual(itinerary["port_from"], "Unknown")


class TestPromotionalTitles(unittest.TestCase):
    """A discount banner on the title split one trip across two cards."""

    NAME = "North & Tiran (Hurghada - Hurghada)"

    def setUp(self):
        self.payload = promote(
            candidate(
                [
                    departure(name=f"20% Off: {self.NAME}", start="2027-05-01",
                              end="2027-05-08", price=1100.0),
                    departure(name=self.NAME, start="2027-06-05",
                              end="2027-06-12", price=1450.0),
                ]
            ),
            season=SEASON,
        )

    def test_the_same_route_is_one_itinerary(self):
        self.assertEqual(len(self.payload["itineraries"]), 1)

    def test_the_route_name_carries_no_marketing(self):
        self.assertEqual(self.payload["itineraries"][0]["name"], self.NAME)

    def test_both_departures_survive_the_merge(self):
        self.assertEqual(len(self.payload["departures"]), 2)

    def test_the_discount_moves_to_the_departure_it_applies_to(self):
        """It explains why one date is cheaper, so it is kept, not dropped."""
        by_start = {d["start"]: d for d in self.payload["departures"]}
        self.assertEqual(by_start["2027-05-01"]["promotion"], "20% Off")
        self.assertNotIn("promotion", by_start["2027-06-05"])

    def test_the_discount_never_reaches_the_page(self):
        """It is the operator's claim about a list price we have not seen."""
        dataset = Dataset.from_dict(self.payload)
        self.assertFalse(any(hasattr(d, "promotion") for d in dataset.departures))
        rendered = build_payload(dataset)
        self.assertNotIn("20% Off", json.dumps(rendered))

    def test_a_percentage_inside_the_route_name_is_left_alone(self):
        payload = promote(
            candidate([departure(name="Nitrox 32% Special (Hurghada - Hurghada)")]),
            season=SEASON,
        )
        self.assertEqual(
            payload["itineraries"][0]["name"], "Nitrox 32% Special (Hurghada - Hurghada)"
        )


class TestSiteRecoveryFromTitles(unittest.TestCase):
    """Operators spell sites the way they like; the classifier needs them anyway."""

    def sites(self, name: str) -> list[str]:
        from liveaboard.promote import _sites_from_name

        return _sites_from_name(name)

    def test_an_acute_accent_still_reads_as_st_johns(self):
        """A live title read "St. John´s" and folded to "st john s"."""
        self.assertIn("st johns", self.sites("Deadalus, St. John´s & Elphinstone"))

    def test_a_transposed_daedalus_is_still_daedalus(self):
        self.assertIn("daedalus", self.sites("Deadalus, St. John´s & Elphinstone"))

    def test_rocky_is_rocky_island(self):
        self.assertIn(
            "rocky island", self.sites("Marine Park South: Daedalus - Rocky - Zabargad")
        )

    def test_one_site_is_listed_once_however_it_is_spelled(self):
        """SITE_HINTS carries "st johns" and "st john's"; they are one site."""
        found = self.sites("Daedalus & St. John's")
        self.assertEqual(len(found), len(set(found)))
        self.assertEqual(len(found), 2)

    def test_a_site_name_inside_a_longer_word_is_not_a_match(self):
        """Substring matching is what put invented data on the page once."""
        self.assertEqual(self.sites("Saidian Coast Special"), [])


class TestNotesDescribeTheRun(unittest.TestCase):
    """The page's own note is data too, and it went stale for a week."""

    FEES = TestFeeMerge.FEES

    def test_no_fees_says_so(self):
        payload = promote(candidate([departure()]), season=SEASON)
        self.assertIn("not yet captured", payload["notes"])

    def test_full_coverage_stops_claiming_fees_are_missing(self):
        payload = promote(candidate([departure()]), season=SEASON, fees=self.FEES)
        self.assertNotIn("not yet captured", payload["notes"])
        self.assertIn("fee disclosures", payload["notes"].lower())

    def test_partial_coverage_is_counted_not_rounded(self):
        payload = promote(
            candidate(
                [departure(), departure(boat="unvisited", start="2027-06-05",
                                        end="2027-06-12")],
                itineraries=[{"id": "alia-soul", "boat": "Alia Soul"},
                             {"id": "unvisited", "boat": "Unvisited"}],
            ),
            season=SEASON,
            fees=self.FEES,
        )
        self.assertIn("1 of 2", payload["notes"])

    def test_an_explicit_note_still_wins(self):
        payload = promote(candidate([departure()]), season=SEASON, notes="custom")
        self.assertEqual(payload["notes"], "custom")


class TestGuestCount(unittest.TestCase):
    """Berth price is per person, so how many share the boat is part of it."""

    def guests(self, summary):
        from liveaboard.promote import _guests

        return _guests(summary)

    def test_the_phrasing_the_fleet_actually_uses(self):
        for text, expected in (
            ("a 36m (118ft) diving boat with 10 cabins for 20 guests", 20),
            ("offering 12 cabins for 24 guests, up to 18 dives weekly", 24),
            ("accommodates up to 26 divers", 26),
            ("carries 16 passengers in eight cabins", 16),
        ):
            self.assertEqual(self.guests(text), expected, text)

    def test_a_vessel_that_does_not_say_stays_unknown(self):
        """Half the fleet. Unknown is not zero and must not render as a number."""
        self.assertIsNone(self.guests("Dive the Red Sea aboard a classic safari boat."))
        self.assertIsNone(self.guests(None))
        self.assertIsNone(self.guests(""))

    def test_a_length_is_not_a_guest_count(self):
        """"36m (118ft)" sits in the same sentence as the real number."""
        self.assertIsNone(self.guests("a 118ft steel hull built in 2019"))

    def test_an_implausible_count_is_refused(self):
        self.assertIsNone(self.guests("host 250 guests"))

    def test_it_lands_on_the_boat_not_the_itinerary(self):
        """The same vessel carries the same people whichever week you book."""
        payload = promote(
            candidate(
                [departure(), departure(start="2027-06-05", end="2027-06-12")],
                itineraries=[{
                    "id": "alia-soul", "boat": "Alia Soul",
                    "summary": "a 36m boat with 10 cabins for 20 guests",
                }],
            ),
            season=SEASON,
        )
        self.assertEqual(payload["boats"][0]["guests"], 20)
        self.assertNotIn("guests", payload["itineraries"][0])


class TestPortNames(unittest.TestCase):
    """One harbour under several spellings made ten chips out of six ports."""

    def port(self, name):
        from liveaboard.promote import _port

        return _port(name)

    def test_the_ghalib_marina_is_one_place(self):
        for spelling in ("Port Ghalib", "Marsa Ghalib", "Ras Galep | Port Ghalib"):
            self.assertEqual(self.port(spelling), "Port Ghalib", spelling)

    def test_a_hotel_pickup_is_not_a_port(self):
        self.assertEqual(self.port("Hurghada, Marriott"), "Hurghada")

    def test_marsa_alam_is_not_port_ghalib(self):
        """Sixty kilometres apart, however similar the names look."""
        self.assertEqual(self.port("Marsa Alam"), "Marsa Alam")

    def test_an_unknown_port_keeps_its_own_name(self):
        self.assertEqual(self.port("Berenice"), "Berenice")

    def test_whitespace_does_not_defeat_the_match(self):
        self.assertEqual(self.port("  Marsa   Ghalib "), "Port Ghalib")

    def test_a_missing_port_is_unknown_not_blank(self):
        self.assertEqual(self.port(None), "Unknown")
        self.assertEqual(self.port(""), "Unknown")


class TestDiveCount(unittest.TestCase):
    """Price per dive is what divers compare on; the count is not published."""

    def dives(self, nights, stated=None):
        from liveaboard.promote import _dives

        return _dives(nights, stated)

    def test_a_week_matches_the_only_figure_operators_publish(self):
        """Two vessels state "up to 18 dives per week". Nothing states more."""
        self.assertEqual(self.dives(7), 18)

    def test_the_first_and_last_days_are_not_diving_days(self):
        """Arrival plus a check dive, then a dry day before flying."""
        self.assertEqual(self.dives(4), 9)
        self.assertEqual(self.dives(14), 39)

    def test_an_operator_count_wins_outright(self):
        self.assertEqual(self.dives(7, 22), 22)

    def test_it_errs_low_rather_than_high(self):
        """Assuming more dives divides the bill by a bigger number and makes
        every trip look cheaper per dive than it is."""
        self.assertLess(self.dives(7), 7 * 3)

    def test_the_shortest_trip_still_dives(self):
        self.assertGreaterEqual(self.dives(1), 1)

    def test_the_page_is_told_the_count_was_assumed(self):
        payload = promote(candidate([departure()]), season=SEASON)
        itinerary = payload["itineraries"][0]
        self.assertEqual(itinerary["dives"], 18)
        self.assertTrue(itinerary["dives_estimated"])

    def test_a_scraped_count_is_not_flagged_as_assumed(self):
        payload = promote(
            candidate(
                [departure()],
                itineraries=[{"id": "alia-soul", "boat": "Alia Soul", "dives": 20}],
            ),
            season=SEASON,
        )
        self.assertEqual(payload["itineraries"][0]["dives"], 20)
        self.assertFalse(payload["itineraries"][0]["dives_estimated"])

    def test_the_flag_survives_into_the_rendered_page(self):
        rendered = build_payload(
            Dataset.from_dict(promote(candidate([departure()]), season=SEASON))
        )
        itinerary = next(iter(rendered["itineraries"].values()))
        self.assertTrue(itinerary["dives_estimated"])
        self.assertEqual(itinerary["dives"], 18)


class TestAvailability(unittest.TestCase):
    """127 of 886 departures were sold out and the page could not say so."""

    def avail(self, raw):
        from liveaboard.promote import _availability

        return _availability(raw)

    def test_the_values_the_source_actually_returns(self):
        for raw, expected in (
            ("https://schema.org/SoldOut", "sold_out"),
            ("https://schema.org/OnlineOnly", "available"),
            ("https://schema.org/LimitedAvailability", "limited"),
            ("https://schema.org/InStock", "available"),
        ):
            self.assertEqual(self.avail(raw), expected, raw)

    def test_silence_is_not_a_refusal(self):
        """A source that says nothing has not said the trip is full."""
        self.assertIsNone(self.avail(None))
        self.assertIsNone(self.avail(""))
        self.assertIsNone(self.avail("https://schema.org/SomethingNew"))

    def test_it_reaches_the_departure(self):
        payload = promote(
            candidate([departure(availability="https://schema.org/SoldOut")]),
            season=SEASON,
        )
        self.assertEqual(payload["departures"][0]["availability"], "sold_out")

    def test_the_page_is_told_what_cannot_be_booked(self):
        rendered = build_payload(Dataset.from_dict(promote(
            candidate([departure(availability="https://schema.org/SoldOut")]),
            season=SEASON,
        )))["departures"][0]
        self.assertFalse(rendered["bookable"])

    def test_an_unknown_availability_stays_bookable(self):
        """Hiding a trip because the source was quiet would lose real options."""
        rendered = build_payload(Dataset.from_dict(promote(
            candidate([departure()]), season=SEASON
        )))["departures"][0]
        self.assertIsNone(rendered["availability"])
        self.assertTrue(rendered["bookable"])


if __name__ == "__main__":
    unittest.main()
