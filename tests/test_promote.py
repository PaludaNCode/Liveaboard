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


class TestSitesAreRecoveredFromTheTitle(unittest.TestCase):
    def test_dive_sites_are_recovered_from_the_trip_name(self):
        """The source publishes no site list, but names routes after sites."""
        payload = promote(candidate([departure()]), season=SEASON)
        sites = payload["itineraries"][0]["dive_sites"]
        self.assertIn("brothers", sites)
        self.assertIn("daedalus", sites)

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


class TestPortsWrittenTight(unittest.TestCase):
    """A hyphen with no space around it is still a hyphen between two ports.

    Thirteen titles are punctuated this way and every one of them was read as
    having no ports at all: 32 bookable sailings printed "Unknown" under the
    column a visitor books flights from, and kept the bracket in their trip
    name where the other 389 had it cut. Nobody withheld the harbour -- it is
    written there in full.
    """

    def promote_one(self, name: str) -> dict:
        payload = promote(
            candidate([departure(name=name, location="Egypt")]), season=SEASON
        )
        return payload["itineraries"][0]

    def test_no_spaces_at_all(self):
        itinerary = self.promote_one("North & Tiran (Hurghada-Hurghada)")
        self.assertEqual(itinerary["port_from"], "Hurghada")
        self.assertEqual(itinerary["port_to"], "Hurghada")

    def test_a_space_on_the_left_only(self):
        itinerary = self.promote_one("Fury Shoals (Port Ghalib -Port Ghalib)")
        self.assertEqual(itinerary["port_from"], "Port Ghalib")

    def test_a_space_on_the_right_only(self):
        itinerary = self.promote_one("Fury Shoals (Hurghada- Port Ghalib)")
        self.assertEqual(itinerary["port_from"], "Hurghada")
        self.assertEqual(itinerary["port_to"], "Port Ghalib")

    def test_the_ports_leave_the_title_however_they_are_spaced(self):
        """The other half of the same bug: From and To are columns already."""
        itinerary = self.promote_one("Famous Five (Hurghada-Hurghada)")
        self.assertNotIn("(", itinerary["title"])
        self.assertEqual(itinerary["title"], "Famous Five")

    def test_a_spaced_dash_is_still_the_separator(self):
        """Order matters, and it is fixed before a title needs it to be.

        Read tight-first, "(Sharm el-Sheikh - Hurghada)" splits into "Sharm el"
        and "Sheikh - Hurghada" -- a harbour that does not exist and a return
        port that is two. The strict pattern is asked first for this reason.
        """
        itinerary = self.promote_one("Deep South (Sharm el-Sheikh - Hurghada)")
        self.assertEqual(itinerary["port_from"], "Sharm el-Sheikh")
        self.assertEqual(itinerary["port_to"], "Hurghada")

    def test_a_tight_route_is_still_a_route(self):
        """Loosening the spacing does not loosen what counts as a port."""
        itinerary = self.promote_one("Red Sea Classic (Brothers-Daedalus)")
        self.assertEqual(itinerary["port_from"], "Egypt")


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
        from liveaboard.promote import guests_in_prose

        return guests_in_prose(summary)

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

    def test_the_airport_codes_are_the_harbours_the_source_states(self):
        """PADI states the harbour in a field beside the title it shortens."""
        self.assertEqual(self.port("HRG"), "Hurghada")
        self.assertEqual(self.port("PRG"), "Port Ghalib")

    def test_an_abbreviation_nothing_states_stays_its_own_name(self):
        """A code is folded because a source names the harbour, not because
        it looks like a code.

        RMF is Marsa Alam's airport and no title in the fleet uses it. Folding
        it would be guessing which harbour a stranger meant, which is the one
        thing this table does not do.
        """
        self.assertEqual(self.port("RMF"), "RMF")

    def test_the_misspelt_marina_is_the_same_marina(self):
        self.assertEqual(self.port("Port Galib"), "Port Ghalib")

    def test_sharm_settles_on_one_spelling_and_one_capital(self):
        for spelling in ("Sharm El Sheikh", "Sharm El sheikh", "Sharm El Sheik"):
            self.assertEqual(self.port(spelling), "Sharm El Sheikh", spelling)


class TestHarbourSpellingWinsBackMatches(unittest.TestCase):
    """The table folds the join key too, and that is not a side effect.

    ``fold_ports`` runs ``PORT_ALIASES`` over a title before ``padi_key`` is
    taken, which is why "Marsa Ghalib" against "Port Ghalib" stopped costing
    Emperor Asmaa all seven of its matches. Adding harbours to the table
    therefore recovers pairings as well as chips: Blue Horizon went from four
    of its nine trips matched to nine, and those five now carry the second
    seller's fee panel and its recommended-dives note where they carried
    nothing. Pinned here so the gain is a decision rather than a surprise.
    """

    def key(self, slug: str, name: str) -> str:
        from liveaboard.promote import padi_key

        return padi_key(slug, name)

    def test_the_misspelt_marina_keys_onto_ours(self):
        self.assertEqual(
            self.key("blue-horizon",
                     "Brothers, Daedalus & Elphinstone (Hurghada - Port Galib)"),
            self.key("blue-horizon",
                     "Brothers, Daedalus & Elphinstone (Hurghada - Port Ghalib)"),
        )

    def test_a_misspelling_and_a_tight_dash_at_once(self):
        """PADI writes both in one bracket: "(Port Galib -Port Galib)"."""
        self.assertEqual(
            self.key("blue-horizon",
                     "Rocky, Zabargad & St. Johns (Port Galib -Port Galib)"),
            self.key("blue-horizon",
                     "Rocky, Zabargad & St. Johns (Port Ghalib - Port Ghalib)"),
        )

    def test_the_codes_key_onto_the_harbours_they_stand_for(self):
        self.assertEqual(
            self.key("seawolf-steel", "Red Sea Classic (HRG - PRG)"),
            self.key("seawolf-steel", "Red Sea Classic (Hurghada - Port Ghalib)"),
        )

    def test_the_fold_does_not_merge_a_trip_with_its_reverse(self):
        """Two sailings differing only by port are two trips, folded or not."""
        self.assertNotEqual(
            self.key("seawolf-steel", "Red Sea Classic (HRG - PRG)"),
            self.key("seawolf-steel", "Red Sea Classic (PRG - HRG)"),
        )


class TestDiveCount(unittest.TestCase):
    """The count is the operator's or it is nothing.

    It used to be worked out from nights at three dives a full day, which was
    a defensible average and still the wrong thing to publish: price per dive
    is total over dives, so at a fixed rate that is a constant multiple of
    price per night. 292 of 317 trips run seven nights, so the column ranked
    them in exactly the same order as the one beside it while looking like an
    independent measurement.
    """

    def dives(self, stated=None):
        from liveaboard.promote import _dives

        return _dives(stated)

    def test_an_operator_count_is_kept(self):
        self.assertEqual(self.dives(22), 22)

    def test_no_count_is_zero_not_a_guess(self):
        self.assertEqual(self.dives(None), 0)
        self.assertEqual(self.dives(0), 0)

    def test_an_unstated_count_reaches_the_page_as_zero(self):
        """The page prints "not stated" rather than dividing by an assumption."""
        payload = promote(candidate([departure()]), season=SEASON)
        self.assertEqual(payload["itineraries"][0]["dives"], 0)

    def test_a_scraped_count_survives_promotion(self):
        payload = promote(
            candidate(
                [departure()],
                itineraries=[{"id": "alia-soul", "boat": "Alia Soul", "dives": 20}],
            ),
            season=SEASON,
        )
        self.assertEqual(payload["itineraries"][0]["dives"], 20)

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


class TestTitleTidying(unittest.TestCase):
    """A column of trip names reads as a column only if the separators match."""

    def tidy(self, name):
        from liveaboard.promote import _tidy

        return _tidy(name)

    def test_a_tab_mid_title_becomes_a_space(self):
        self.assertEqual(
            self.tidy("Get Wrecked (Hurghada\t- Hurghada)"),
            "Get Wrecked (Hurghada - Hurghada)",
        )

    def test_a_space_before_the_bracket_goes(self):
        self.assertEqual(
            self.tidy("Golden Triangle (Safaga - Safaga )"),
            "Golden Triangle (Safaga - Safaga)",
        )

    def test_one_dash_not_three(self):
        self.assertEqual(self.tidy("Big fish – Hammerheads"), "Big fish - Hammerheads")

    def test_wording_is_never_touched(self):
        """Presentation only. Changing an operator's words is not formatting."""
        for name in ("Simply The Best", "Elba Reef Expedition!", "Tec only Safari Trip"):
            self.assertEqual(self.tidy(name), name)


class TestRegionWhenNoSiteIsNamed(unittest.TestCase):
    """Fifty-one trips name a direction and no reef."""

    def region(self, name):
        from liveaboard.promote import _region_from_name

        return _region_from_name(name)

    def test_it_transcribes_the_operators_own_word(self):
        self.assertEqual(self.region("North (Hurghada - Hurghada)"), "northern route")
        self.assertEqual(self.region("Deep South (Hamata - Hamata)"), "southern route")
        self.assertEqual(self.region("Get Wrecked"), "wreck route")

    def test_a_title_naming_nothing_gets_nothing(self):
        self.assertIsNone(self.region("Yachtiano Deluxe"))
        self.assertIsNone(self.region("Famous Five"))

    def test_it_is_absent_whenever_real_sites_were_found(self):
        """A list of reefs beats a direction, so the direction is not carried."""
        payload = promote(candidate([departure()]), season=SEASON)
        itinerary = payload["itineraries"][0]
        self.assertTrue(itinerary["dive_sites"])
        self.assertIsNone(itinerary["region"])

    def test_the_vessel_summary_is_never_used_for_sites(self):
        """It is the boat's brochure: Aphrodite's names St John's, so its
        northern week would have been tagged with a southern site."""
        payload = promote(
            candidate(
                [departure(name="North Wrecks (Hurghada - Hurghada)")],
                itineraries=[{
                    "id": "alia-soul", "boat": "Alia Soul",
                    "summary": "Sails to Brothers, Daedalus and St John's.",
                }],
            ),
            season=SEASON,
        )
        self.assertEqual(payload["itineraries"][0]["dive_sites"], [])
        self.assertEqual(payload["itineraries"][0]["region"], "northern route")


class TestDisplayTitle(unittest.TestCase):
    """The name column should not reprint what From and To already say."""

    def title(self, name):
        from liveaboard.promote import _display_title

        return _display_title(name)

    def test_the_port_pair_goes(self):
        # Deliberately not the Brothers/Daedalus/Elphinstone route: that one is
        # also folded onto a house spelling, which would make this pass or fail
        # for a reason that has nothing to do with the ports.
        # The dashes become house separators on the way through -- it is a
        # route list -- so the assertion is about the bracket, not the commas.
        self.assertEqual(
            self.title("Daedalus - Rocky - Zabargad (Hurghada - Hurghada)"),
            "Daedalus, Rocky & Zabargad",
        )

    def test_it_survives_a_port_being_aliased(self):
        """The regression this field exists to prevent.

        The browser used to cut the suffix by matching the bracket text against
        ``port_from``. Fold "Ras Galep | Port Ghalib" down to "Port Ghalib" and
        that comparison fails, so the ports come back on exactly the titles the
        alias table was added to tidy.
        """
        for name in (
            "Golden Triangle (Ras Galep | Port Ghalib - Ras Galep | Port Ghalib)",
            "Golden Triangle (Safaga - Ras Galep | Port Ghalib)",
        ):
            self.assertEqual(self.title(name), "Golden Triangle")
        # "Ras Mohamed" also folds to "Ras Mohammed" on the way through; what
        # this case is about is the port suffix, which goes either way.
        self.assertEqual(
            self.title("Tiran & North Ras Mohamed (Hurghada, Marriott - Hurghada, Marriott)"),
            "Tiran & North Ras Mohammed",
        )

    def test_a_route_in_brackets_stays(self):
        """Cutting it would delete what the trip actually is."""
        self.assertEqual(
            self.title("Sataya (Fury Shoals) - St. John's (Marsa Alam - Marsa Alam)"),
            "Sataya (Fury Shoals) - St. John's",
        )

    def test_it_tidies_and_drops_the_discount_too(self):
        self.assertEqual(
            self.title("20% Off: Get Wrecked (Hurghada\t- Hurghada)"), "Get Wrecked"
        )

    def test_a_title_that_is_only_ports_keeps_them(self):
        """Better a redundant name than an empty cell."""
        self.assertEqual(self.title("(Hurghada - Hurghada)"), "(Hurghada - Hurghada)")

    def test_it_reaches_the_itinerary(self):
        payload = promote(
            candidate([departure(name="Brothers & Daedalus (Hurghada - Hurghada)")]),
            season=SEASON,
        )
        itinerary = payload["itineraries"][0]
        self.assertEqual(itinerary["title"], "Brothers & Daedalus")
        # The full name is the trip's identity and stays whole.
        self.assertIn("Hurghada", itinerary["name"])


if __name__ == "__main__":
    unittest.main()


def fee_book(slug="alia-soul", *, specs=None, fees=None) -> dict:
    """A fee-scrape result, in the shape ``promote`` reads it."""
    entry = {"source_url": "https://example.invalid", "fees": fees or []}
    if specs is not None:
        entry["specs"] = specs
    return {"scraped_at": "2026-08-27", "source": "liveaboard.com",
            "vessels": {slug: entry}}


class TestTheSpecificationTable(unittest.TestCase):
    """The guest count the marketing prose does not carry."""

    def test_the_table_beats_the_prose(self):
        payload = promote(
            candidate(
                [departure()],
                itineraries=[{"id": "alia-soul", "boat": "Alia Soul",
                              "summary": "A fine boat for 12 guests."}],
            ),
            season=SEASON,
            fees=fee_book(specs={"guests": 20, "cabins": 9}),
        )
        boat = payload["boats"][0]
        self.assertEqual(boat["guests"], 20)
        self.assertEqual(boat["cabins"], 9)

    def test_the_prose_still_answers_when_the_table_does_not(self):
        payload = promote(
            candidate(
                [departure()],
                itineraries=[{"id": "alia-soul", "boat": "Alia Soul",
                              "summary": "A fine boat for 12 guests."}],
            ),
            season=SEASON,
            fees=fee_book(specs={"guests": None}),
        )
        self.assertEqual(payload["boats"][0]["guests"], 12)


class TestFreeNitrox(unittest.TestCase):
    """"Free Nitrox" is a real statement, and a weaker one than a price."""

    def nitrox(self, payload):
        fees = {f["code"]: f for f in payload["itineraries"][0]["fees"]}
        return fees.get("nitrox")

    def test_a_ticked_box_marks_nitrox_included(self):
        payload = promote(
            candidate([departure()]), season=SEASON,
            fees=fee_book(specs={"nitrox_free": True}),
        )
        nitrox = self.nitrox(payload)
        self.assertIsNotNone(nitrox)
        self.assertTrue(nitrox["included"])
        self.assertIsNone(nitrox["amount"])

    def test_it_never_overwrites_a_stated_price(self):
        """The one direction of error this site must not make.

        A vessel that both ticks "Free Nitrox" and quotes a figure has
        contradicted itself; the figure is the operator typing a number and
        the tick is a checkbox, so turning the cost into "free" would
        understate the bill on the strength of the weaker claim.
        """
        priced = {
            "code": "nitrox", "tier": "conditional", "included": False,
            "basis": "per_trip", "amount": {"amount": 30.0, "currency": "EUR"},
        }
        payload = promote(
            candidate([departure()]), season=SEASON,
            fees=fee_book(specs={"nitrox_free": True}, fees=[priced]),
        )
        nitrox = self.nitrox(payload)
        self.assertFalse(nitrox["included"])
        self.assertEqual(nitrox["amount"]["amount"], 30.0)

    def test_availability_alone_claims_nothing(self):
        payload = promote(
            candidate([departure()]), season=SEASON,
            fees=fee_book(specs={"nitrox_free": False, "nitrox_available": True}),
        )
        self.assertIsNone(self.nitrox(payload))


class TestHandReadFactsDoNotOutliveTheScrape(unittest.TestCase):
    """A typed-in figure fills a gap; it does not beat a fresher reading.

    ``data/operator_facts.json`` covers ten vessels and never refreshes, while
    the weekly browser run rewrites the fee book. Left as an unconditional
    override, last month's hand-read number quietly wins over this week's
    scrape, and nothing says so -- a stale fact that wins is worse than none.
    """

    def book(self, amount, collected):
        return {
            "scraped_at": collected,
            "vessels": {"alia-soul": {
                "collected": collected,
                "fees": [{"code": "marine_park", "tier": "mandatory",
                          "basis": "per_trip", "included": False,
                          "amount": {"amount": amount, "currency": "EUR"}}],
            }},
        }

    def facts(self, amount, collected):
        return {
            "collected": collected,
            "vessels": {"alia-soul": {
                "fees": [{"code": "marine_park", "tier": "mandatory",
                          "basis": "per_trip", "included": False,
                          "amount": {"amount": amount, "currency": "EUR"}}],
            }},
        }

    def park(self, payload):
        fees = {f["code"]: f for f in payload["itineraries"][0]["fees"]}
        return fees["marine_park"]["amount"]["amount"]

    def test_a_fresher_scrape_beats_a_hand_read_figure(self):
        payload = promote(
            candidate([departure()]), season=SEASON,
            fees=self.book(250.0, "2026-08-27"),
            facts=self.facts(150.0, "2026-08-01"),
        )
        self.assertEqual(self.park(payload), 250.0)
        self.assertIn("alia-soul/marine_park", payload["facts_superseded"])

    def test_a_hand_read_figure_still_wins_when_it_is_newer(self):
        payload = promote(
            candidate([departure()]), season=SEASON,
            fees=self.book(250.0, "2026-08-01"),
            facts=self.facts(150.0, "2026-08-27"),
        )
        self.assertEqual(self.park(payload), 150.0)
        self.assertNotIn("facts_superseded", payload)

    def test_it_still_fills_a_gap_the_scrape_left_unpriced(self):
        """The case the file exists for: the disclosure names the charge and
        gives no number, so there is nothing fresher to lose to."""
        book = self.book(250.0, "2026-08-27")
        book["vessels"]["alia-soul"]["fees"][0]["amount"] = None
        payload = promote(
            candidate([departure()]), season=SEASON,
            fees=book, facts=self.facts(150.0, "2026-08-01"),
        )
        self.assertEqual(self.park(payload), 150.0)


class TestADiveCountIsAFloorForOneTripLength(unittest.TestCase):
    """Operators quote a range and the dataset keeps its low end.

    The range is not sloppiness. A week that crosses further, or spends longer
    inside the marine parks where night dives are not permitted, fits fewer
    dives into the same seven nights. So the count is the fewest stated, price
    per dive is a ceiling, and the figure belongs to the trip length it was
    quoted for and no other.
    """

    def dives(self, stated, nights=None, for_nights=None):
        from liveaboard.promote import _dives

        return _dives(stated, nights=nights, for_nights=for_nights)

    def test_a_count_applies_to_the_length_it_was_quoted_for(self):
        self.assertEqual(self.dives(17, nights=7, for_nights=7), 17)

    def test_a_weekly_count_is_not_applied_to_a_mini_safari(self):
        """Seventeen dives in three nights would be nearly six a day."""
        self.assertEqual(self.dives(17, nights=3, for_nights=7), 0)

    def test_nor_is_it_stretched_over_a_longer_trip(self):
        """Ten nights is not seven, and the operator did not say."""
        self.assertEqual(self.dives(17, nights=10, for_nights=7), 0)

    def test_a_count_with_no_stated_length_is_taken_at_face_value(self):
        """Older entries predate the field; they are not silently discarded."""
        self.assertEqual(self.dives(17, nights=7), 17)

    def test_the_page_gets_zero_rather_than_a_derivation(self):
        payload = promote(
            candidate(
                [departure(start="2027-05-01", end="2027-05-04")],
                itineraries=[{"id": "alia-soul", "boat": "Alia Soul"}],
            ),
            season=SEASON,
            facts={"collected": "2026-08-27", "vessels": {
                "alia-soul": {"dives": 17, "dives_for_nights": 7}}},
        )
        itinerary = payload["itineraries"][0]
        self.assertEqual(itinerary["nights"], 3)
        self.assertEqual(itinerary["dives"], 0)


def trip_book(
    boat="alia-soul",
    name="Brothers, Daedalus & Elphinstone",
    **fields,
) -> dict:
    """One itinerary book entry, as ``tools/fetch_itineraries.py`` writes it."""
    return {
        "collected": "2026-08-27",
        "source": "liveaboard.com",
        "trips": {
            f"{boat}::{name}": {"boat": boat, "name": name, **fields},
        },
    }


class TestTheItineraryKeyIsWhatJoinsTheTwoSides(unittest.TestCase):
    """The book is built from archived ``Event`` names; promote has tidied them.

    Every field the book fills has a fallback, so a key that matches nothing
    fails silently -- the page keeps yesterday's answers and nothing is red.
    That makes these the tests that matter most in this file.
    """

    def key(self, slug, name):
        from liveaboard.promote import itinerary_key

        return itinerary_key(slug, name)

    def test_a_discount_banner_is_not_part_of_the_trip(self):
        """A week on sale is the same week.

        The archive stores "20% Off: Ultimate Red Sea (...)"; promote strips
        the banner before grouping. Keyed on the raw string, 71 of 314
        itineraries matched nothing and the fetcher spent 97 requests
        re-reading trips it already had under their banner spellings.
        """
        self.assertEqual(
            self.key("all-star-red-sea", "20% Off: Ultimate Red Sea (Port Ghalib - Hurghada)"),
            self.key("all-star-red-sea", "Ultimate Red Sea (Port Ghalib - Hurghada)"),
        )

    def test_the_port_pair_is_part_of_the_trip(self):
        """Two sailings differing only by port are two trips, and the itinerary
        id is built from the whole name."""
        self.assertNotEqual(
            self.key("alsuraya", "Brothers, Daedalus and Elphinstone (Hurghada - Hurghada)"),
            self.key("alsuraya", "Brothers, Daedalus and Elphinstone (Hurghada - Port Ghalib)"),
        )

    def test_operator_spacing_does_not_make_a_second_trip(self):
        self.assertEqual(
            self.key("alia-soul", "Brothers , Daedalus  &  Elphinstone"),
            self.key("alia-soul", "Brothers, Daedalus & Elphinstone"),
        )

    def test_the_vessel_is_part_of_it(self):
        self.assertNotEqual(
            self.key("alia-soul", "North & Tiran"),
            self.key("blue-seas", "North & Tiran"),
        )


class TestWhatOneTripSaysAboutItself(unittest.TestCase):
    """The itinerary fragment, merged the way the fee book is.

    Everything here is the operator's own words about a single trip, where the
    dataset previously had a guess off the title or a figure about the hull.
    """

    def promoted(self, trips=None, **kwargs):
        payload = promote(
            candidate([departure(name="Simply the Best (Hurghada - Hurghada)")]),
            season=SEASON,
            trips=trips,
            **kwargs,
        )
        return payload["itineraries"][0]

    def test_the_operators_own_reefs_beat_the_title(self):
        """"Simply the Best" names no reef. Its description names three."""
        self.assertEqual(self.promoted()["dive_sites"], [])
        itinerary = self.promoted(
            trips=trip_book(
                name="Simply the Best (Hurghada - Hurghada)",
                sections=[section("Day 3:", "Dive 4 at Brothers. Dive 5 at "
                                            "Daedalus. Dive 6 at Elphinstone.",
                                  is_day=True)],
            )
        )
        self.assertEqual(
            itinerary["dive_sites"], ["brothers", "daedalus", "elphinstone"]
        )

    def test_the_fragment_replaces_the_title_rather_than_joining_it(self):
        """A title is wrong about some trips -- a St John's week matched two of
        BDE's three reefs and was badged accordingly. Unioning the two would
        reimport exactly the error this source removes."""
        itinerary = self.promoted(
            trips=trip_book(
                name="Simply the Best (Hurghada - Hurghada)",
                sections=[section("Day 2:", "Dive 1 at St John's.", is_day=True)],
            )
        )
        payload = promote(
            candidate([departure(name="Brothers & Daedalus (Hurghada - Hurghada)")]),
            season=SEASON,
            trips=trip_book(
                name="Brothers & Daedalus (Hurghada - Hurghada)",
                sections=[section("Day 2:", "Dive 1 at St John's.", is_day=True)],
            ),
        )
        self.assertEqual(itinerary["dive_sites"], ["st johns"])
        self.assertEqual(payload["itineraries"][0]["dive_sites"], ["st johns"])

    def test_an_unread_trip_keeps_the_sites_the_title_gave_it(self):
        """A fetch that has not reached a trip must not blank it."""
        payload = promote(
            candidate([departure(name="Brothers & Daedalus (Hurghada - Hurghada)")]),
            season=SEASON,
            trips=trip_book(boat="another-boat", name="Something Else"),
        )
        self.assertEqual(
            payload["itineraries"][0]["dive_sites"], ["brothers", "daedalus"]
        )

    def test_a_region_is_dropped_once_the_fragment_names_reefs(self):
        """The region exists only for titles that name no site at all."""
        payload = promote(
            candidate([departure(name="Northern Red Sea (Hurghada - Hurghada)")]),
            season=SEASON,
        )
        self.assertIsNotNone(payload["itineraries"][0]["region"])
        payload = promote(
            candidate([departure(name="Northern Red Sea (Hurghada - Hurghada)")]),
            season=SEASON,
            trips=trip_book(
                name="Northern Red Sea (Hurghada - Hurghada)",
                sections=[section("Day 2:", "Dive 1 at Thistlegorm.", is_day=True)],
            ),
        )
        self.assertIsNone(payload["itineraries"][0]["region"])

    def test_a_per_trip_dive_count_needs_no_trip_length_guard(self):
        """The vessel-level count is a standard week's, so it is withheld from
        every other length that boat sells. This one is stated for this trip."""
        payload = promote(
            candidate([departure(start="2027-05-01", end="2027-05-04")]),
            season=SEASON,
            trips=trip_book(dives=9),
        )
        self.assertEqual(payload["itineraries"][0]["nights"], 3)
        self.assertEqual(payload["itineraries"][0]["dives"], 9)

    def test_the_vessel_count_remains_the_fallback(self):
        payload = promote(
            candidate(
                [departure()],
                itineraries=[{"id": "alia-soul", "boat": "Alia Soul", "dives": 20}],
            ),
            season=SEASON,
            trips=trip_book(dives=None),
        )
        self.assertEqual(payload["itineraries"][0]["dives"], 20)

    def test_a_trip_the_fragment_is_silent_about_still_says_nothing(self):
        payload = promote(candidate([departure()]), season=SEASON, trips=trip_book())
        self.assertEqual(payload["itineraries"][0]["dives"], 0)


class TestTheStatedEntryBar(unittest.TestCase):
    """A safety requirement is the operator's claim and is never softened."""

    def bar(self, **fields):
        payload = promote(candidate([departure()]), season=SEASON,
                          trips=trip_book(**fields))
        return payload["itineraries"][0].get("requirements")

    def test_a_certification_and_a_dive_count_are_both_kept(self):
        bar = self.bar(
            experience="Advanced Open Water - 50 minimum logged dives required.",
            min_logged_dives=50,
        )
        self.assertEqual(bar["min_level"], "advanced_50")
        self.assertEqual(bar["min_logged_dives"], 50)
        self.assertEqual(
            bar["notes"], "Advanced Open Water - 50 minimum logged dives required."
        )

    def test_a_certification_with_no_number_asks_for_none(self):
        """Filling in a plausible count would soften a stated requirement."""
        bar = self.bar(experience="Advanced Open Water required.", min_logged_dives=0)
        self.assertEqual(bar["min_level"], "advanced")
        self.assertEqual(bar["min_logged_dives"], 0)

    def test_a_hundred_dive_level_needs_a_hundred_dives_stated(self):
        """"Advanced + 100 dives" is what that level says. Reading it out of
        the word "experienced" puts a bar on a trip nobody set one for."""
        bar = self.bar(experience="Suitable for experienced divers.",
                       min_logged_dives=0)
        self.assertEqual(bar["min_level"], "advanced")
        self.assertEqual(bar["min_logged_dives"], 0)
        self.assertEqual(self.bar(experience="x", min_logged_dives=120)["min_level"],
                         "experienced_100")

    def test_an_unread_trip_states_no_bar_at_all(self):
        """Absent means nobody looked, not that the operator asks for nothing
        -- the same distinction this module makes about fees. The key is left
        out rather than written as null, so a diff that gains one is somebody
        having read a safety requirement.
        """
        self.assertIsNone(self.bar())
        payload = promote(candidate([departure()]), season=SEASON)
        self.assertNotIn("requirements", payload["itineraries"][0])

    def test_the_wording_survives_the_dataset(self):
        payload = promote(
            candidate([departure()]),
            season=SEASON,
            trips=trip_book(
                experience="Advanced Open Water - 50 minimum logged dives required.",
                min_logged_dives=50,
            ),
        )
        itinerary = next(iter(Dataset.from_dict(payload).itineraries.values()))
        self.assertEqual(itinerary.requirements.min_logged_dives, 50)
        self.assertIn("50 minimum logged", itinerary.requirements.notes)


class TestGuestsFromATripAreAFallback(unittest.TestCase):
    """The specification table states the hull's maximum; a fragment states one
    sailing's. The table wins, and this fills the rows it is missing."""

    def test_the_specification_table_still_wins(self):
        payload = promote(
            candidate([departure()]),
            season=SEASON,
            fees=fee_book(specs={"guests": 26}),
            trips=trip_book(guests=20),
        )
        self.assertEqual(payload["boats"][0]["guests"], 26)

    def test_a_trip_beats_a_regex_over_the_marketing_copy(self):
        payload = promote(
            candidate(
                [departure()],
                itineraries=[{"id": "alia-soul", "boat": "Alia Soul",
                              "summary": "A 36m boat for 34 guests."}],
            ),
            season=SEASON,
            trips=trip_book(guests=20),
        )
        self.assertEqual(payload["boats"][0]["guests"], 20)

    def test_the_answer_does_not_depend_on_the_order_of_the_file(self):
        """A boat's trips can disagree; the most common answer wins, not
        whichever one sorts first."""
        book = trip_book(name="Trip A", guests=12)
        book["trips"]["alia-soul::Trip B"] = {
            "boat": "alia-soul", "name": "Trip B", "guests": 20}
        book["trips"]["alia-soul::Trip C"] = {
            "boat": "alia-soul", "name": "Trip C", "guests": 20}
        payload = promote(
            candidate([departure(name="Trip A"), departure(name="Trip B",
                                                          start="2027-05-08",
                                                          end="2027-05-15")]),
            season=SEASON,
            trips=book,
        )
        self.assertEqual(payload["boats"][0]["guests"], 20)


def section(heading, text="Some description of the place.", is_day=False):
    """One section of the operator's prose, as the fetcher stores it."""
    return {"heading": heading, "text": text, "is_day": is_day}


class TestTheDescriptionIsTheSource(unittest.TestCase):
    """Dive sites come from the operator's description. Not from "Key regions".

    The regions list is a summary somebody typed once, and it is wrong on real
    trips: All Star Red Sea sells a "North & Brothers" week whose regions name
    Daedalus, 180 km from anywhere its own day plan goes. Across 293 trips the
    regions claim a site the description never mentions on 42 of them.

    The description wins because it is what the buyer reads. A diver books on
    the sentences, not on the sidebar, so the sentences are the operator's
    actual claim -- and reporting the operator's claim is all this site does.
    """

    def promoted(self, name="Brothers & Daedalus (Hurghada - Hurghada)", **fields):
        payload = promote(
            candidate([departure(name=name)]),
            season=SEASON,
            trips=trip_book(name=name, **fields),
        )
        return payload["itineraries"][0]

    def test_the_key_regions_list_is_not_read_at_all(self):
        """The whole point. Regions naming a reef the description does not is
        the case this change exists to fix, so it must not leak back in."""
        itinerary = self.promoted(
            name="North & Brothers (Hurghada - Hurghada)",
            regions=["Abu Nuhas", "Thistlegorm", "Daedalus"],
            sections=[section("Day 2:", "Dive 1 at Abu Nuhas. Dive 2 at "
                                        "Thistlegorm.", is_day=True)],
        )
        # Hint order, not text order: one string is matched against the
        # vocabulary in the order the vocabulary lists it.
        self.assertEqual(itinerary["dive_sites"], ["thistlegorm", "abu nuhas"])
        self.assertNotIn("daedalus", itinerary["dive_sites"])

    def test_a_days_text_is_read_now(self):
        """It was excluded once, and the reason is recorded rather than lost:
        Aphrodite sells a "North - Straits of Tiran" week whose day plan is a
        deep-south expedition pasted from another trip, and reading it claims
        eight reefs across the whole sea. That is one trip against the 42 the
        regions get wrong, and it is the operator's own text either way."""
        itinerary = self.promoted(
            sections=[section("Day 4:", "Dive 7 at Daedalus Reef.", is_day=True)],
        )
        self.assertEqual(itinerary["dive_sites"], ["daedalus"])

    def test_a_place_heading_counts_too(self):
        """Seven vessels head every section with a place rather than a day."""
        itinerary = self.promoted(
            sections=[section("Gubal Island", "Small Gubal sits between the "
                                              "mainland and the Sinai.")],
        )
        self.assertEqual(itinerary["dive_sites"], ["gubal"])

    def test_the_lead_paragraph_counts_too(self):
        itinerary = self.promoted(intro="A week at Elphinstone and Daedalus.")
        self.assertEqual(itinerary["dive_sites"], ["daedalus", "elphinstone"])

    def test_a_description_naming_no_place_falls_back_to_the_title(self):
        """Most sections are "Highlights" or "Marine Life". A trip whose whole
        description names no reef must not be blanked -- the title is the older
        and weaker source, but it is better than an empty cell."""
        itinerary = self.promoted(
            sections=[section("Highlights"), section("Marine Life")],
        )
        self.assertEqual(itinerary["dive_sites"], ["brothers", "daedalus"])

    def test_the_regions_are_the_last_resort_and_never_a_merge(self):
        """Six trips have a description that names no reef and a title that
        names none either -- "Famous Five", "Get Wrecked". Publishing an empty
        cell where the operator did say something is worse than using the
        weaker source, so the regions are reached for last. They are never
        merged into a description that spoke: that is the whole point."""
        empty = self.promoted(
            name="Famous Five (Hurghada - Hurghada)",
            regions=["Ras Mohammed"],
            sections=[section("Highlights")],
        )
        self.assertEqual(empty["dive_sites"], ["ras mohammed"])

        spoke = self.promoted(
            name="Famous Five (Hurghada - Hurghada)",
            regions=["Ras Mohammed"],
            sections=[section("Day 2:", "Dive 1 at Thistlegorm.", is_day=True)],
        )
        self.assertEqual(spoke["dive_sites"], ["thistlegorm"])

    def test_a_place_sections_body_is_not_read(self):
        """All Star Red Sea describes Daedalus as "Much like the Brothers
        Islands, Daedalus also sits in open water" -- a comparison, on a trip
        that goes nowhere near the Brothers. A heading names a place and a day
        says what you dive; the body of a place section is an essay and will
        mention anywhere."""
        itinerary = self.promoted(
            name="Daedalus & Fury Shoal (Port Ghalib - Port Ghalib)",
            sections=[section("Daedalus Reef",
                              "Much like the Brothers Islands, Daedalus also "
                              "sits in open water.")],
        )
        self.assertEqual(itinerary["dive_sites"], ["daedalus"])

    def test_a_site_is_not_assembled_across_two_sections(self):
        """Sections are read one at a time. `normalise` reduces punctuation to
        spaces, so joining them first would let a section headed "Ras" and one
        headed "Mohammed" invent a reef neither names."""
        itinerary = self.promoted(
            name="Simply the Best (Hurghada - Hurghada)",
            sections=[section("Ras", "."), section("Mohammed", ".")],
        )
        self.assertEqual(itinerary["dive_sites"], [])

    def test_the_same_reef_spelled_two_ways_stays_one_chip(self):
        itinerary = self.promoted(
            sections=[section("Elphinstone Reef", "A legendary wall."),
                      section("Day 7:", "Dive 18 at Elphinstone.", is_day=True)],
        )
        self.assertEqual(itinerary["dive_sites"], ["elphinstone"])


class TestADiveOnAPlaceFoldsIntoIt(unittest.TestCase):
    """A hint is a destination; a dive on one is an alias.

    Forced by the prose, which names the individual dives rather than the
    destination -- "Dive 2: Giannis D". Left unfolded, an Abu Nuhas week showed
    five chips for one reef. This is the rule that already folded Jackson,
    Gordon, Woodhouse and Thomas into Tiran.
    """

    def sites(self, text):
        from liveaboard.promote import _sites_from_name

        return _sites_from_name(text)

    def test_the_four_abu_nuhas_wrecks_are_abu_nuhas(self):
        for wreck in ("Giannis D", "Carnatic", "Chrisoula K", "Kimon M"):
            with self.subTest(wreck=wreck):
                self.assertEqual(self.sites(f"The {wreck} lies here"), ["abu nuhas"])

    def test_both_brothers_and_their_wrecks_are_the_brothers(self):
        for name in ("Big Brother", "Little Brother", "Numidia", "Aida"):
            with self.subTest(name=name):
                self.assertEqual(self.sites(f"Diving the {name} today"), ["brothers"])

    def test_ras_mohammeds_own_dives_are_ras_mohammed(self):
        for name in ("Shark Reef", "Yolanda Reef", "Dunraven"):
            with self.subTest(name=name):
                self.assertEqual(self.sites(f"A dive at {name}"), ["ras mohammed"])

    def test_a_destination_is_not_folded_away(self):
        """Thistlegorm and the Salem Express are wrecks too, and stay their own
        chips: a week is sold to reach them, and nothing else on the list
        contains them."""
        self.assertEqual(self.sites("SS Thistlegorm"), ["thistlegorm"])
        self.assertEqual(self.sites("The Salem Express"), ["salem express"])

    def test_the_arabic_word_for_reef_is_not_a_dive_site(self):
        """"Sha'ab" was a hint and matched inside Sha'ab Sheer, Sha'ab Abu
        Nuhas and Sha'ab el Erg alike, putting a chip reading "reef" on 113 of
        315 trips. The named reefs still resolve."""
        self.assertEqual(self.sites("Sha'ab Sheer"), ["shaab sheer"])
        self.assertEqual(self.sites("Sha'ab Abu Nuhas"), ["abu nuhas"])
        self.assertEqual(self.sites("Sha'ab el Erg"), ["sha'ab el erg"])
        self.assertEqual(self.sites("a lovely sha'ab"), [])

    def test_dolphin_house_is_two_reefs_and_resolves_to_neither(self):
        """Sha'ab el Erg off Hurghada and Sha'ab Samadai off Marsa Alam are
        both sold under the name, 400 km apart. A southern trip's own prose
        lists it beside Sataya and Fury Shoal."""
        self.assertEqual(self.sites("Dolphin House"), [])


class TestTheReefsDescriptionsName(unittest.TestCase):
    """Vocabulary read out of the operators' own descriptions.

    A description names the dive; a key-regions list names the destination.
    Until these existed, comparing the two measured our ignorance as much as
    the operator's mistakes: "Daedalus & Fury Shoal" appeared to visit no Fury
    Shoal, because its week is spent at Shaab Claudio, Abu Galawa and
    Shilineat and nothing here knew those are Fury Shoal. That comparison is
    what decides whether a region is wrong, so a gap in it is not cosmetic.
    """

    def sites(self, text):
        from liveaboard.promote import _sites_from_name

        return _sites_from_name(text)

    def test_abu_dabbab_and_abu_dabab_are_one_reef(self):
        """The single most expensive spelling in the dataset: descriptions
        write two b's, region lists write one, and the same reef read as two
        different places on 16 trips."""
        self.assertEqual(self.sites("Dive 1 at Abu Dabbab 3"), ["abu dabab"])
        self.assertEqual(self.sites("Abu Dabab"), ["abu dabab"])

    def test_the_fury_shoals_are_named_reef_by_reef(self):
        for reef in ("Shaab Maksur", "Shaab Claudio", "Abu Galawa",
                     "Shaab Hamam", "El Malahi", "Shilineat", "Abu Fendera"):
            with self.subTest(reef=reef):
                self.assertEqual(self.sites(f"Dive 4 at {reef}"), ["fury shoals"])

    def test_st_johns_reefs_fold_into_st_johns(self):
        for reef in ("Umm Aruk", "Cave Reef", "Small Gota"):
            with self.subTest(reef=reef):
                self.assertEqual(self.sites(f"Dive 8 at {reef}"), ["st johns"])

    def test_the_straits_of_gubal_dives_fold_into_gubal(self):
        """Shag Rock carries the Kingston; the Barge and Bluff Point are Small
        Gubal's two best-known dives."""
        for reef in ("Shag Rock", "Kingston", "Bluff Point",
                     "Small Gubal Isl.", "Big Gubal Isl."):
            with self.subTest(reef=reef):
                self.assertEqual(self.sites(f"Dive 13 at {reef}"), ["gubal"])

    def test_ras_mohammeds_park_reaches_past_the_headland(self):
        """Shaab Mahmoud and the Alternatives are dived on the same day as
        Shark and Yolanda, and half the fleet spells Yolanda with a J."""
        for reef in ("Shaab Mahmoud", "The Alternatives", "Jolanda Reef",
                     "Beacon Rock"):
            with self.subTest(reef=reef):
                self.assertEqual(self.sites(f"Dive 9 at {reef}"), ["ras mohammed"])

    def test_the_abu_nuhas_wrecks_without_their_letter(self):
        """"Dive 4 at Abu Nuhas - Giannis D" matches the full name; elsewhere
        the prose drops the letter."""
        for wreck in ("Giannis", "Chrisoula", "Kimon"):
            with self.subTest(wreck=wreck):
                self.assertEqual(self.sites(f"the {wreck} wreck"), ["abu nuhas"])

    def test_a_typo_in_the_operators_own_day_plan(self):
        self.assertEqual(self.sites("Dive 1 at Gota Abu Ramad"), ["gota abu ramada"])

    def test_safagas_house_reef(self):
        """Its region lists name Safaga and its descriptions name the reef."""
        self.assertEqual(self.sites("Ras Abu Soma"), ["safaga"])

    def test_none_of_this_folds_a_destination_away(self):
        """The rule stays: a hint is a destination, an alias is a dive on one.
        These additions are all dives; nothing they touch was a chip before."""
        for name, expected in (("Daedalus Reef", "daedalus"),
                               ("Elphinstone", "elphinstone"),
                               ("St John's", "st johns"),
                               ("Fury Shoal", "fury shoals"),
                               ("Gubal Island", "gubal")):
            with self.subTest(name=name):
                self.assertEqual(self.sites(name), [expected])


class TestTheFleetsSevenSpellingsOfOneWeek(unittest.TestCase):
    """One route, written seven ways, sitting next to each other in the widest
    column on the page. A visitor comparing them has to work out that they are
    the same trip before they can compare anything about them.

    Titles only. The name is identity -- the itinerary id is built from it and
    data/itineraries.json keys on it -- so the operator's own wording stays
    exactly where the rest of the pipeline reads it.
    """

    VARIANTS = [
        "Brother - Daedalus - Elphinstone",
        "Brother Islands - Daedalus - Elphinstone",
        "Brother Islands, Daedalus & Elphinstone",
        "Brothers - Daedalus - Elphinstone",
        "Brothers, Daedalus & Elphinstone",
        "Brothers, Daedalus and Elphinstone",
        "Brothers, Daedalus, Elphinstone",
    ]

    def _titles(self, names, boat="alia-soul"):
        deps = [departure(boat=f"boat{n}", name=name, start=f"2027-05-{n + 1:02}")
                for n, name in enumerate(names)]
        its = [{"id": f"boat{n}", "name": f"Boat {n}", "boat": f"Boat {n}"}
               for n in range(len(names))]
        data = promote(candidate(deps, its), season=SEASON)
        return [i["title"] for i in data["itineraries"]]

    def test_every_spelling_reaches_one_title(self):
        self.assertEqual(set(self._titles(self.VARIANTS)),
                         {"Brothers, Daedalus & Elphinstone"})

    def test_the_operators_own_wording_survives_as_the_name(self):
        deps = [departure(name="Brother - Daedalus - Elphinstone")]
        data = promote(candidate(deps), season=SEASON)
        itinerary = data["itineraries"][0]
        self.assertEqual(itinerary["title"], "Brothers, Daedalus & Elphinstone")
        self.assertEqual(itinerary["name"], "Brother - Daedalus - Elphinstone")

    def test_the_id_is_still_built_from_the_name(self):
        """A title that rewrote the id would break every key that matches on
        it -- the per-trip book among them, silently."""
        deps = [departure(name="Brother - Daedalus - Elphinstone")]
        data = promote(candidate(deps), season=SEASON)
        self.assertIn("brother-daedalus-elphinstone", data["itineraries"][0]["id"])

    def test_the_port_suffix_is_still_cut(self):
        titles = self._titles(["Brothers, Daedalus and Elphinstone (Hurghada - Safaga)"])
        self.assertEqual(titles, ["Brothers, Daedalus & Elphinstone"])


class TestNoOtherRouteIsRewritten(unittest.TestCase):
    """Twelve other groups differ the same way and are deliberately left as
    their operators wrote them. A rule that rewrote every title would be a
    house style imposed on somebody else's words, and would eventually merge
    two trips that only look alike."""

    UNTOUCHED = [
        "North & Brothers",
        "Daedalus, Rocky, Zabargad & Elphinstone",
        "North & Tiran",
    ]

    def test_other_routes_keep_their_own_words(self):
        """Only Brothers/Daedalus/Elphinstone is folded onto a house *name*.
        Other routes take the house separators and keep their own reefs, in
        their own order."""
        for name in self.UNTOUCHED:
            with self.subTest(name):
                data = promote(candidate([departure(name=name)]), season=SEASON)
                self.assertEqual(data["itineraries"][0]["title"], name)

    def test_a_longer_route_containing_the_three_is_not_matched(self):
        """The pattern is anchored at both ends. A week that adds Safaga is a
        different week, and renaming it would delete where it goes."""
        for name in ("Brothers, Daedalus, Elphinstone & Safaga",
                     "Brothers, Daedalus and Elphinstone plus Zabargad",
                     "Deep South: Brothers, Daedalus, Elphinstone"):
            with self.subTest(name):
                data = promote(candidate([departure(name=name)]), season=SEASON)
                self.assertEqual(data["itineraries"][0]["title"], name)

    def test_the_reverse_order_is_left_alone(self):
        """Word order is the operator's. Nothing here can verify it means
        something, and nothing here may assume it means nothing."""
        name = "Elphinstone, Daedalus & Brothers"
        data = promote(candidate([departure(name=name)]), season=SEASON)
        self.assertEqual(data["itineraries"][0]["title"], name)

    def test_two_of_the_three_is_a_different_trip(self):
        for name in ("Brothers & Daedalus", "Daedalus & Elphinstone"):
            with self.subTest(name):
                data = promote(candidate([departure(name=name)]), season=SEASON)
                self.assertEqual(data["itineraries"][0]["title"], name)


class TestOneSpellingWhereOnlyTheCaseDiffers(unittest.TestCase):
    """Emperor Divers sells "Simply The Best" and "Simply the Best" -- two real
    trips with different ports, written two ways, printed a row apart as though
    the difference meant something."""

    def _promote(self, *names):
        deps = [departure(boat=f"boat{n}", name=name, start=f"2027-05-{n + 1:02}")
                for n, name in enumerate(names)]
        its = [{"id": f"boat{n}", "name": f"Boat {n}", "boat": f"Boat {n}"}
               for n in range(len(names))]
        return promote(candidate(deps, its), season=SEASON)

    def test_one_spelling_wins(self):
        data = self._promote("Simply The Best", "Simply the Best", "Simply the Best")
        self.assertEqual({i["title"] for i in data["itineraries"]},
                         {"Simply the Best"})

    def test_the_winner_is_one_the_operator_actually_used(self):
        """Never title-cased into a spelling nobody wrote. The fleet is full of
        names a casing rule would ruin: MY Odyssey, St. John's, SS Turkia."""
        data = self._promote("SS Turkia & Ras Mohammed", "ss turkia & ras mohammed",
                             "SS Turkia & Ras Mohammed")
        self.assertEqual({i["title"] for i in data["itineraries"]},
                         {"SS Turkia & Ras Mohammed"})

    def test_a_tie_is_broken_the_same_way_every_run(self):
        """promote is pure and CI compares its output byte for byte, so a tie
        settled by dict order would be a build that fails at random."""
        first = self._promote("Deep South", "deep south")
        second = self._promote("deep south", "Deep South")
        self.assertEqual({i["title"] for i in first["itineraries"]},
                         {i["title"] for i in second["itineraries"]})

    def test_titles_differing_by_a_word_are_left_alone(self):
        data = self._promote("North & Tiran", "North & Dahab")
        self.assertEqual({i["title"] for i in data["itineraries"]},
                         {"North & Tiran", "North & Dahab"})


class TestErrorsInATitleAreCorrected(unittest.TestCase):
    """Errors, as opposed to a style we happen not to share.

    That distinction is the whole point: separators and word order are the
    operators' own and are left alone. These are things nobody intended. The
    three reefs the fleet spells several ways are folded separately, by
    REEF_ALIASES, because they are differences rather than mistakes.
    """

    def title(self, name):
        from liveaboard.promote import _display_title

        return _display_title(name)

    def test_zero_width_spaces_are_removed(self):
        """Invisible on the page and not invisible to anything else: they
        defeat a search for the words either side."""
        self.assertEqual(self.title("Red Sea Charm​: Abu Nuhas"),
                         "Red Sea Charm: Abu Nuhas")
        self.assertEqual(self.title("Sataya​​ (Fury Shoals)"),
                         "Sataya (Fury Shoals)")

    def test_they_are_removed_not_turned_into_spaces(self):
        self.assertEqual(self.title("Deep​South"), "DeepSouth")

    def test_one_apostrophe(self):
        """Three characters for one reef, so the same saint sorts in three
        places and matches in one."""
        for written in ("St. John’s", "St. John´s", "St. John‘s"):
            with self.subTest(written):
                self.assertEqual(self.title(written), "St. John's")

    def test_the_two_misspellings_of_daedalus(self):
        self.assertEqual(self.title("Daedulus, Sataya, Elphinstone"),
                         "Daedalus, Sataya & Elphinstone")
        self.assertEqual(self.title("Deadalus & Elba Reef"),
                         "Daedalus & Elba Reef")

    def test_the_one_misspelling_of_zabargad(self):
        self.assertEqual(self.title("Rocky, Zarbagad, St. John's"),
                         "Rocky, Zabargad & St. John's")

    def test_the_correction_is_listed_not_guessed(self):
        """A near-miss rule that catches those also catches a reef that only
        looks like another. Nothing else is touched."""
        for name in ("Dadalus", "Daedalos", "Deadelus", "Zabargad", "Sataya",
                     "Zabagad", "Zarbagard", "Zabargad Islands"):
            with self.subTest(name):
                self.assertEqual(self.title(name), name)

    def test_a_correct_title_is_left_exactly_as_it_is(self):
        for name in ("Daedalus & Elphinstone", "St. John's & Fury Shoals",
                     "North & Tiran"):
            with self.subTest(name):
                self.assertEqual(self.title(name), name)

    def test_the_operators_own_text_survives_as_the_name(self):
        """Correcting a name would re-key the per-trip book, and what the
        operator published is the identity."""
        raw = "Deadalus, St. John´s & Elphinstone"
        data = promote(candidate([departure(name=raw)]), season=SEASON)
        itinerary = data["itineraries"][0]
        self.assertEqual(itinerary["title"], "Daedalus, St. John's & Elphinstone")
        self.assertEqual(itinerary["name"], raw)


class TestFourReefsGetOneSpelling(unittest.TestCase):
    """One spelling for the four reefs the fleet writes several ways.

    Not mistakes, unlike the corrections above -- "Fury Shoal" and "Brother
    Islands" are what those operators call those reefs. Folded anyway, for the
    reason BDE folds one route: a visitor comparing two rows should not have to
    work out that the reefs are the same reef first.
    """

    def title(self, name):
        from liveaboard.promote import _display_title

        return _display_title(name)

    def test_six_spellings_of_st_johns(self):
        for written in ("St. John's", "St. Johns", "St Johns", "St John's",
                        "St. John", "Saint John's", "Saint Johns"):
            with self.subTest(written):
                self.assertEqual(self.title(written), "St. John's")

    def test_five_spellings_of_brothers(self):
        for written in ("Brothers", "Brother Islands", "Brothers Islands",
                        "Brother Island", "Brothers Island", "Brother"):
            with self.subTest(written):
                self.assertEqual(self.title(written), "Brothers")

    def test_the_plural_does_not_strand_the_second_word(self):
        """Written as an alternation, "s" wins on "Brothers Islands" and leaves
        "Islands" behind -- which reads as a different reef, not a tidied one."""
        self.assertEqual(self.title("North Reefs, Tiran & Brothers Islands"),
                         "North Reefs, Tiran & Brothers")
        self.assertNotIn("Islands", self.title("Brothers Islands"))

    def test_two_spellings_of_fury_shoals(self):
        for written in ("Fury Shoals", "Fury Shoal"):
            with self.subTest(written):
                self.assertEqual(self.title(written), "Fury Shoals")

    def test_the_chosen_spelling_is_one_an_operator_used(self):
        """Never a spelling invented to be consistent. Each of the three is
        the plurality of what the fleet actually wrote."""
        for chosen in ("St. John's", "Brothers", "Fury Shoals", "Ras Mohammed"):
            with self.subTest(chosen):
                self.assertEqual(self.title(chosen), chosen)

    def test_the_reef_is_folded_mid_title(self):
        self.assertEqual(
            self.title("Daedalus, Rocky, Zabargad, St. Johns, Fury Shoal & Elba"),
            "Daedalus, Rocky, Zabargad, St. John's, Fury Shoals & Elba")

    def test_big_and_little_brother_are_not_folded_into_the_pair(self):
        """They name the two islands separately, and folding one to the pair
        would delete which island the trip dives."""
        for name in ("Big Brother & Daedalus", "Little Brother Wall",
                     "Big Brother, Little Brother & Elphinstone"):
            with self.subTest(name):
                self.assertEqual(self.title(name), name)

    def test_three_spellings_of_ras_mohammed(self):
        """All three are real transliterations, so none is a misspelling --
        this is a fold, not a correction. What settles it is not the title
        count (15 / 6 / 2) but the dive sites parsed from the operators' own
        descriptions, which say "ras mohammed" 101 times out of 101: the
        column was disagreeing with the filter chip beside it."""
        # All three the fleet writes. "Ras Muhammad" is the one a narrower
        # pattern missed, and it took reading the whole title list to find:
        # two trips, a different vowel in both halves of the word.
        for written in ("Ras Mohamed", "Ras Mohammed", "Ras Muhammad"):
            with self.subTest(written):
                self.assertEqual(self.title(written), "Ras Mohammed")

    def test_four_reefs_and_nothing_generalised(self):
        """The reef names the fleet also splits are left exactly as written.

        Separators are no longer in this list: route lists take the house
        punctuation. Word order still is -- these differ from their twins by
        sequence and stay two titles.
        """
        for name in ("North Brothers", "Rocky & Rocky Island",
                     "Zabargad Islands", "Elphinstone & Daedalus"):
            with self.subTest(name):
                self.assertEqual(self.title(name), name)

    def test_the_operators_own_text_survives_as_the_name(self):
        raw = "North Reef, Safaga & Brother Islands"
        data = promote(candidate([departure(name=raw)]), season=SEASON)
        itinerary = data["itineraries"][0]
        self.assertEqual(itinerary["title"], "North Reef, Safaga & Brothers")
        self.assertEqual(itinerary["name"], raw)

    def test_the_folded_route_still_reaches_the_one_bde_title(self):
        """Brother Islands folds to Brothers before BDE is matched, and BDE
        accepts both, so the route still prints once."""
        self.assertEqual(self.title("Brother Islands - Daedalus - Elphinstone"),
                         "Brothers, Daedalus & Elphinstone")


class TestHouseSeparatorsOnRouteLists(unittest.TestCase):
    """One punctuation for a list of stops: commas, then & before the last.

    The fleet writes the same two-stop route as "North & Brothers", "North -
    Brothers" and "North and Brothers", and prints all three a row apart.
    """

    def title(self, name):
        from liveaboard.promote import _display_title

        return _display_title(name)

    def test_every_separator_reaches_one_punctuation(self):
        for written in ("North & Brothers", "North - Brothers",
                        "North and Brothers", "North + Brothers",
                        "North, Brothers", "North | Brothers"):
            with self.subTest(written):
                self.assertEqual(self.title(written), "North & Brothers")

    def test_a_longer_list_takes_commas_and_one_ampersand(self):
        self.assertEqual(
            self.title("Daedalus - Rocky - Zabargad - Elphinstone"),
            "Daedalus, Rocky, Zabargad & Elphinstone")

    def test_a_doubled_separator_collapses(self):
        """"Tiran, & Dahab" is a comma and an ampersand doing one job."""
        self.assertEqual(self.title("North Reefs, Wrecks, Tiran, & Dahab"),
                         "North Reefs, Wrecks, Tiran & Dahab")

    def test_word_order_is_still_the_operators(self):
        """The line held from the start. Two titles naming the same reefs in a
        different sequence stay two titles: nothing here can verify the order
        means something, and nothing may assume it means nothing."""
        self.assertEqual(self.title("St. John's & Daedalus"), "St. John's & Daedalus")
        self.assertEqual(self.title("Daedalus & St. John's"), "Daedalus & St. John's")

    def test_a_single_stop_is_not_a_list(self):
        for name in ("Fury Shoals", "North", "Brothers"):
            with self.subTest(name):
                self.assertEqual(self.title(name), name)


class TestProseIsNotRepunctuated(unittest.TestCase):
    """The boundary that makes house separators safe.

    "Daedalus - Rocky - Zabargad" is a list whose dashes are separators.
    "Dancing with Dolphins - Dolphin Liveaboard Safari" is a sentence whose
    dash is not, and "Best of Dahab and Tiran" is English rather than two
    stops joined by "and". Rewriting either would be editing prose.
    """

    def title(self, name):
        from liveaboard.promote import _display_title

        return _display_title(name)

    def test_sentences_keep_their_own_punctuation(self):
        for name in ("Best of Dahab and Tiran",
                     "Dancing with Dolphins - Dolphin Liveaboard Safari",
                     "Deep South Expedition: Secrets of Zabargad",
                     "Northern Red Sea - Best Wreck Diving",
                     "Best of the North",
                     "Tec only Safari Trip: Northern Wrecks and Reefs"):
            with self.subTest(name):
                self.assertEqual(self.title(name), name)

    def test_a_labelled_route_keeps_its_label_and_its_dashes(self):
        """A colon means the operator is naming the trip, not listing stops.
        The label is theirs, and the part after it is not a bare list."""
        for name in ("Marine Park North: Brothers - Daedalus & Elphinstone",
                     "Marine Park South: Daedalus - Rocky - Zabargad - Elphinstone"):
            with self.subTest(name):
                self.assertEqual(self.title(name), name)

    def test_one_unrecognised_word_makes_it_prose(self):
        """The test is that every part is something the dataset already knows.
        A single unknown stop means we are not reading a list of stops."""
        self.assertEqual(self.title("Daedalus - Somewhere Nobody Parses"),
                         "Daedalus - Somewhere Nobody Parses")

    def test_the_vocabulary_is_the_one_promote_already_reads_titles_with(self):
        """A second list would drift from SITE_HINTS and start repunctuating
        prose the moment a reef was added to one and not the other."""
        from liveaboard.promote import SITE_HINTS, _is_place_list

        self.assertTrue(_is_place_list(f"{SITE_HINTS[0]} - {SITE_HINTS[1]}"))


class TestEverySpecTheFeeBookHoldsIsPublished(unittest.TestCase):
    """A `specs` key that goes nowhere looks exactly like one the source
    does not publish, and that is how `length_m` stayed null on all 77 boats
    while 71 lengths sat in `data/fees.json`. The fee scrape had read them
    since the specification table was first parsed; `promote` took `guests`,
    `cabins` and `nitrox_free` out of that block and dropped the rest.

    So the contract is stated here rather than trusted: every field
    `VesselFacts` can produce is either published on the boat record or named
    below with the reason it is not. Adding a row to the scraper's table now
    fails this test until somebody decides which of the two it is.
    """

    CONSUMED_ELSEWHERE = {
        # Folded into the vessel's fee book as an included nitrox line, which
        # is a stronger place for it than a boat attribute: the page prices
        # nitrox, it does not describe boats.
        "nitrox_free",
        # Read by nothing. Kept in the fee book deliberately -- "Free Nitrox"
        # and "Nitrox available" are different claims and the second is the
        # only evidence that a boat fills nitrox at all -- but it is not a
        # fact about the hull and has no column.
        "nitrox_available",
    }

    def boat_fields(self) -> set[str]:
        from dataclasses import fields

        from liveaboard.models import Boat

        return {f.name for f in fields(Boat)}

    def test_every_vessel_fact_is_published_or_accounted_for(self):
        from dataclasses import fields

        from liveaboard.scrape.vessel import VesselFacts

        emitted = {f.name for f in fields(VesselFacts) if f.name != "amenities"}
        emitted |= {"nitrox_free", "nitrox_available"}  # the derived properties
        unaccounted = emitted - self.boat_fields() - self.CONSUMED_ELSEWHERE
        self.assertEqual(
            unaccounted, set(),
            f"the fee scrape reads {sorted(unaccounted)} and nothing publishes "
            f"it; put it on Boat or name it in CONSUMED_ELSEWHERE",
        )

    def test_promote_writes_the_whole_table_onto_the_boat(self):
        payload = promote(
            candidate([departure()]),
            season=SEASON,
            fees=fee_book(specs={"guests": 20, "cabins": 9,
                                 "length_m": 36, "year_built": 2014}),
        )
        boat = payload["boats"][0]
        self.assertEqual(boat["length_m"], 36)
        self.assertEqual(boat["year_built"], 2014)

    def test_a_boat_the_fee_run_has_not_covered_states_neither(self):
        """Absent, never zero: a hull nobody measured has no length."""
        payload = promote(candidate([departure()]), season=SEASON)
        boat = payload["boats"][0]
        self.assertIsNone(boat["length_m"])
        self.assertIsNone(boat["year_built"])


class TestTheSecondSellerFillsASpecThePanelLeavesBlank(unittest.TestCase):
    """PADI's vessel page states cabins, length and build year, and is the
    only source of them for the boats liveaboard.com does not sell -- those
    have no specification panel for the fee run to read, which is why 6 hulls
    published no length at all.

    A fallback, never a merge, and never an override: two sellers describing
    one hull, and the one publishing a table about the boat outranks the one
    publishing a strip beside a price.
    """

    def payload(self, *, ours=None, theirs=None):
        return promote(
            candidate([departure()]),
            season=SEASON,
            fees=fee_book(specs=ours) if ours is not None else None,
            padi={"vessels": {"alia-soul": {"slug": "alia-soul",
                                            "specs": theirs or {}}}},
        )

    def test_padi_answers_where_our_panel_is_silent(self):
        boat = self.payload(ours={"guests": 20},
                            theirs={"length_m": 45, "year_built": 2022})["boats"][0]
        self.assertEqual(boat["length_m"], 45)
        self.assertEqual(boat["year_built"], 2022)

    def test_padi_answers_where_there_is_no_panel_at_all(self):
        """The case it exists for: a vessel the fee run can never visit."""
        boat = self.payload(theirs={"cabins": 16, "length_m": 45})["boats"][0]
        self.assertEqual(boat["cabins"], 16)
        self.assertEqual(boat["length_m"], 45)

    def test_our_panel_wins_outright_where_both_speak(self):
        boat = self.payload(ours={"length_m": 36, "year_built": 2014},
                            theirs={"length_m": 45, "year_built": 2022})["boats"][0]
        self.assertEqual(boat["length_m"], 36)
        self.assertEqual(boat["year_built"], 2014)

    def test_neither_speaking_states_nothing(self):
        boat = self.payload(ours={"guests": 20})["boats"][0]
        self.assertIsNone(boat["length_m"])

    def test_the_strip_is_not_a_source_for_the_guest_count(self):
        """The strip has no such row -- the *description* does, and that is a
        different claim read from a different place (see the class below). A
        `specs.guests` would be a table figure PADI never printed, so it stays
        ignored and a future one is a decision rather than a drift."""
        boat = self.payload(theirs={"guests": 99})["boats"][0]
        self.assertIsNone(boat["guests"])


class TestTheSecondSellerFillsAGuestCountAndNeverMovesOne(unittest.TestCase):
    """PADI's vessel page states the guest count in prose, and it is last.

    `specs_from_page` recorded the opposite as settled -- searched in full,
    zero hits -- and the search was of the specification strip. The page says
    it in the description: MY Independence II shipped as *guests not stated*
    beside a PADI page reading *"a 40-meter vessel designed for just 20
    guests"*, and a reader found it.

    Last in the chain, behind the specification table, the hand-read figures,
    the per-trip book and our own summary. Measured over the 50 mapped vessels:
    34 agree, 5 disagree, 4 hulls had no count from anywhere. Last is what
    makes those 5 keep our number -- the fallback can fill a blank and can
    never move a figure a seller stated.
    """

    def payload(self, *, ours=None, summary=None, theirs=None):
        # The vessel description travels on the candidate's itinerary record,
        # which is where the crawl puts what a boat says about itself.
        trip = {"id": "alia-soul", "name": "Alia Soul", "boat": "Alia Soul"}
        if summary:
            trip["summary"] = summary
        return promote(
            candidate([departure()], itineraries=[trip]),
            season=SEASON,
            fees=fee_book(specs=ours) if ours is not None else None,
            padi={"vessels": {"alia-soul": {"slug": "alia-soul", "guests": theirs}}},
        )

    def test_it_fills_a_hull_nothing_else_answers(self):
        self.assertEqual(self.payload(theirs=20)["boats"][0]["guests"], 20)

    def test_the_specification_table_still_wins(self):
        boat = self.payload(ours={"guests": 22}, theirs=20)["boats"][0]
        self.assertEqual(boat["guests"], 22)

    def test_our_own_prose_still_wins(self):
        """Bella 2's case: two descriptions, two numbers, and ours is read
        from the seller whose panel the rest of that boat's bill comes from."""
        boat = self.payload(summary="12 cabins for 22 guests", theirs=20)["boats"][0]
        self.assertEqual(boat["guests"], 22)

    def test_silence_on_both_sides_stays_unknown(self):
        self.assertIsNone(self.payload(theirs=None)["boats"][0]["guests"])


class TestThePortPairDoesNotSplitOneTripInTwo(unittest.TestCase):
    """`itinerary_key` used to carry the port pair exactly as typed, and the
    two sellers do not type it the same way. Our name for a sailing PADI sells
    is PADI's spelling; the itinerary fragment's is liveaboard.com's. Two of MY
    Blue Pearl's first three trips differed by one space and were one trip
    under two keys, so the fragment answered nothing.
    """

    def key(self, name, slug="blue-pearl"):
        from liveaboard.promote import itinerary_key

        return itinerary_key(slug, name)

    def test_the_spacing_around_the_dash_stops_mattering(self):
        canonical = self.key("North & Tiran (Hurghada - Hurghada)")
        for written in ("North & Tiran (Hurghada-Hurghada)",
                        "North & Tiran (Hurghada- Hurghada)",
                        "North & Tiran (Hurghada -Hurghada)",
                        "North & Tiran (Hurghada – Hurghada)"):
            with self.subTest(written):
                self.assertEqual(self.key(written), canonical)

    def test_two_harbours_stay_two_trips(self):
        """The spacing is punctuation; the harbour is not. Folding names
        through PORT_ALIASES was measured and refused -- it bought one match
        and collided two of Blue Horizon's own itineraries onto one key, which
        would serve one trip's dive count and reefs for the other."""
        self.assertNotEqual(
            self.key("Brothers (Hurghada - Port Ghalib)"),
            self.key("Brothers (Port Ghalib - Hurghada)"),
        )
        self.assertNotEqual(
            self.key("South & St Johns (Marsa Ghalib - Marsa Ghalib)"),
            self.key("South & St Johns (Port Ghalib - Port Ghalib)"),
        )

    def test_a_parenthetical_that_is_not_a_port_pair_is_left_alone(self):
        """"(Brothers - Daedalus)" is a route. Rewriting it would be this
        function editing a trip name rather than keying one."""
        self.assertEqual(self.key("Marine Park (Brothers - Daedalus)"),
                         "blue-pearl::Marine Park (Brothers - Daedalus)")

    def test_a_name_with_no_ports_is_untouched(self):
        self.assertEqual(self.key("St. John's & Daedalus"),
                         "blue-pearl::St. John's & Daedalus")


class TestAFragmentReachesATripNamedByTheOtherSeller(unittest.TestCase):
    """A vessel liveaboard.com sells no berth on takes its trip *names* from
    PADI, and its itinerary fragments -- now that they are reachable at all --
    are liveaboard.com's, spelling the same week differently: "St. Johns"
    against "St. John's". The exact key cannot join those and must not learn
    to; `padi_key` exists for looking a foreign record up and is used here.
    """

    def trips(self, *names, boat="blue-seas"):
        return {"trips": {
            f"{boat}::{n}": {"boat": boat, "name": n, "dives": 21,
                             "regions": [], "guests": 18}
            for n in names
        }}

    def payload(self, ours, book):
        return promote(
            candidate([departure(boat="blue-seas", name=ours)]),
            season=SEASON, trips=book,
        )["itineraries"][0]

    def test_a_spelling_apart_still_joins(self):
        got = self.payload("St John's (Port Ghalib- Port Ghalib)",
                           self.trips("St. Johns (Port Ghalib - Port Ghalib)"))
        self.assertEqual(got["dives"], 21)

    def test_two_trips_folding_together_are_both_refused(self):
        """Emperor Asmaa really does sell "South & St Johns" and "South and
        St. Johns" as two trips. Nothing here can say which one a PADI row
        meant, and answering with either would put one week's dive count on
        the other."""
        got = self.payload(
            "South & St Johns (Port Ghalib - Port Ghalib)",
            self.trips("South & St Johns (Marsa Ghalib - Marsa Ghalib)",
                       "South and St. Johns (Marsa Ghalib - Marsa Ghalib)"),
        )
        self.assertEqual(got["dives"], 0)

    def test_the_exact_key_still_wins(self):
        """The loose key is a fallback, never a replacement: it is reached
        only where the strict one found nothing."""
        book = self.trips("St. Johns (Port Ghalib - Port Ghalib)")
        book["trips"]["exact"] = {"boat": "blue-seas", "dives": 7, "regions": [],
                                  "name": "St. Johns (Port Ghalib - Port Ghalib)"}
        got = self.payload("St. Johns (Port Ghalib - Port Ghalib)", book)
        self.assertEqual(got["dives"], 7)


class TestAskedAndAnsweredIsNotTheSameAsNeverAsked(unittest.TestCase):
    """`dives: 0` was two answers wearing one face. liveaboard.com prints the
    Dives row as a dash for exactly one trip of 352 -- Aphrodite's *North
    Dolphins*, a snorkelling week whose entry bar is "No Certificate needed" --
    and that is the seller stating no count. The 74 itineraries with no
    fragment at all were never asked, and 41 of those are on boats it
    publishes no vessel page for.

    Same distinction as `fees_known` and `not_asked`: neither can produce a
    price per dive, and only one of them is a fact about the trip.
    """

    def itinerary(self, book=None):
        return promote(
            candidate([departure()]), season=SEASON, trips=book,
        )["itineraries"][0]

    def book(self, **fields):
        return {"trips": {"k": {
            "boat": "alia-soul", "regions": [],
            "name": "Brothers, Daedalus & Elphinstone", **fields}}}

    def test_a_fragment_that_states_no_count_is_marked_read(self):
        got = self.itinerary(self.book(dives=None))
        self.assertEqual(got["dives"], 0)
        self.assertTrue(got["dives_read"])

    def test_no_fragment_at_all_is_not(self):
        got = self.itinerary()
        self.assertEqual(got["dives"], 0)
        self.assertNotIn("dives_read", got)

    def test_it_is_written_only_where_true(self):
        """A false on 74 itineraries is 74 keys saying nothing, which is what
        `summary` was deleted from the payload for."""
        payload = promote(candidate([departure()]), season=SEASON)
        self.assertNotIn("dives_read", payload["itineraries"][0])

    def test_the_page_is_told_only_where_it_changes_the_cell(self):
        """A trip with a count needs no explanation of why it has none."""
        from liveaboard.render import build_payload
        from liveaboard.dataset import Dataset

        payload = promote(candidate([departure()]), season=SEASON,
                          trips=self.book(dives=18))
        rows = build_payload(Dataset.from_dict(payload))["itineraries"]
        row = next(iter(rows.values())) if isinstance(rows, dict) else rows[0]
        self.assertEqual(row["dives"], 18)
        self.assertNotIn("dives_read", row)
