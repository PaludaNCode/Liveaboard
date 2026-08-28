"""Tests for the padi.com adapter.

Every string in here was served by travel.padi.com on 2026-08-28, including the
malformed ones. The doubled night suffix and the slugs that contradict their own
pages are not invented edge cases; they are what one Egyptian vessel's page
publishes today, and each test below is the note that stops a future reader
"tidying" the handling away.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from liveaboard.scrape.base import FetchResult
from liveaboard.scrape.padi_com import PadiComAdapter
from liveaboard.taxonomy import DiverLevel

NOW = datetime(2026, 8, 28, tzinfo=timezone.utc)
VESSEL_URL = "https://travel.padi.com/liveaboard/egypt/hammerhead-ii/"


def result(body: str, url: str = VESSEL_URL) -> FetchResult:
    return FetchResult(url=url, status=200, body=body, fetched_at=NOW)


class StubFetcher:
    """Returns canned bodies, so discovery is testable without the network."""

    def __init__(self, bodies: dict[str, str]) -> None:
        self.bodies = bodies
        self.requested: list[str] = []

    def get(self, url: str) -> FetchResult:
        self.requested.append(url)
        return result(self.bodies[url], url)


class TestSplitTitle(unittest.TestCase):
    """PADI's title is "Name (Port - Port) N Nights"."""

    def test_keeps_ports_in_the_name(self) -> None:
        split = PadiComAdapter.split_title("Sharks & Dolphins (Marsa Alam - Hurghada) 7 Nights")
        self.assertEqual(split, ("Sharks & Dolphins (Marsa Alam - Hurghada)", "Marsa Alam - Hurghada", 7))

    def test_two_ports_are_two_trips(self) -> None:
        """The same reefs from a different port is a different sailing."""
        one = PadiComAdapter.split_title("Sharks & Dolphins (Marsa Alam - Hurghada) 7 Nights")
        two = PadiComAdapter.split_title("Sharks & Dolphins (Marsa Alam - Marsa Alam) 7 Nights")
        assert one and two
        self.assertNotEqual(one[0], two[0])

    def test_doubled_night_suffix(self) -> None:
        """Live title: PADI appends the night count twice on this one trip."""
        title = (
            "Red Sea Charm​: Abu Nuhas - SS Thistlegorm - The Brothers Islands - Safaga "
            "(Marsa Alam - Hurghada) 7 Nights 7 Nights"
        )
        split = PadiComAdapter.split_title(title)
        assert split
        self.assertEqual(split[2], 7)
        self.assertTrue(split[0].endswith("(Marsa Alam - Hurghada)"), split[0])
        self.assertNotIn("Nights", split[0])

    def test_disagreeing_night_counts_are_not_resolved(self) -> None:
        """A trip length is the denominator under every per-night price."""
        self.assertIsNone(
            PadiComAdapter.split_title("Brothers Light (Marsa Alam - Hurghada) 4 Nights 7 Nights")
        )

    def test_navigation_without_a_night_count_is_not_a_trip(self) -> None:
        self.assertIsNone(PadiComAdapter.split_title("Liveaboard Deals"))
        self.assertIsNone(PadiComAdapter.split_title("Diving in Egypt"))


class TestJoinRepairs(unittest.TestCase):
    """Two bugs that lost matches on boats that were paired correctly.

    Both were found by reading a real side-by-side rather than by a failing
    assertion, which is why they are pinned here with the live strings.
    """

    def test_en_dash_before_the_night_count(self) -> None:
        """Unity's titles cost it all three matches on a correct pairing.

        PADI writes "Name (Hurghada - Hurghada) - 7 nights" with en-dashes. The
        suffix pattern matched only the digits, left the dash behind, and the
        ports pattern then failed on a string not ending in ")".
        """
        split = PadiComAdapter.split_title(
            "North and Brothers (Hurghada \u2013 Hurghada) \u2013 7 nights")
        self.assertIsNotNone(split)
        assert split
        self.assertEqual(split[0], "North and Brothers (Hurghada - Hurghada)")
        self.assertEqual(split[2], 7)

    def test_every_dash_shape_gives_one_name(self) -> None:
        keys = {
            PadiComAdapter.compare_key(PadiComAdapter.split_title(title)[0])
            for title in (
                "Fury Shoal (Port Ghalib - Port Ghalib) 7 nights",
                "Fury Shoal (Port Ghalib \u2013 Port Ghalib) \u2013 7 nights",
                "Fury Shoal (Port Ghalib \u2014 Port Ghalib) 7 Nights",
            )
        }
        self.assertEqual(len(keys), 1, keys)

    def test_port_spelling_inside_a_trip_name(self) -> None:
        """Emperor Asmaa's seven trips matched nothing over one harbour's name.

        Ours say "Marsa Ghalib", PADI's say "Port Ghalib". PORT_ALIASES folds
        that pair for the port columns; nothing was folding it inside a title.
        """
        ours = PadiComAdapter.fold_ports("South & St Johns (Marsa Ghalib - Marsa Ghalib)")
        theirs = PadiComAdapter.split_title(
            PadiComAdapter.fold_ports("South & St Johns (Port Ghalib - Port Ghalib) - 7 nights"))
        assert theirs
        self.assertEqual(
            PadiComAdapter.compare_key(ours), PadiComAdapter.compare_key(theirs[0]))

    def test_folding_leaves_an_unaliased_port_alone(self) -> None:
        self.assertEqual(
            PadiComAdapter.fold_ports("Fury Shoal (Hamata - Hamata)"),
            "Fury Shoal (Hamata - Hamata)",
        )


class TestCompareKey(unittest.TestCase):
    """The join: our itinerary name against PADI's title minus its nights."""

    def test_our_name_matches_their_title(self) -> None:
        ours = "Sharks & Dolphins (Marsa Alam - Hurghada)"
        theirs = PadiComAdapter.split_title("Sharks & Dolphins (Marsa Alam - Hurghada) 7 Nights")
        assert theirs
        self.assertEqual(PadiComAdapter.compare_key(ours), PadiComAdapter.compare_key(theirs[0]))

    def test_zero_width_space_does_not_break_the_join(self) -> None:
        """Both sources carry it, mid-word, in the same operator's title."""
        self.assertEqual(
            PadiComAdapter.compare_key("Red Sea Charm​: Abu Nuhas"),
            PadiComAdapter.compare_key("Red Sea Charm: Abu Nuhas"),
        )


class TestRequirementsFromChoices(unittest.TestCase):
    """PADI codes the entry bar; this maps the codes, and only the codes."""

    def test_open_water(self) -> None:
        self.assertEqual(
            PadiComAdapter.requirements_from_choices(10),
            {"min_level": DiverLevel.OPEN_WATER.value},
        )

    def test_nitrox_is_a_gas_not_an_entry_bar(self) -> None:
        """30 and 40 differ only by nitrox, so they are the same level."""
        self.assertEqual(
            PadiComAdapter.requirements_from_choices(30),
            PadiComAdapter.requirements_from_choices(40),
        )
        self.assertEqual(
            PadiComAdapter.requirements_from_choices(40),
            {"min_level": DiverLevel.ADVANCED.value},
        )

    def test_tec_diver(self) -> None:
        self.assertEqual(
            PadiComAdapter.requirements_from_choices(50),
            {"min_level": DiverLevel.EXPERIENCED_100.value},
        )

    def test_recommended_dives_are_not_a_requirement(self) -> None:
        """PADI's wording is "50+ dives recommended". It stays a recommendation."""
        got = PadiComAdapter.requirements_from_choices(30, 20)
        self.assertEqual(got["recommended_logged_dives"], 50)
        self.assertNotIn("min_logged_dives", got)
        self.assertEqual(got["min_level"], DiverLevel.ADVANCED.value)

    def test_no_minimum_recommended(self) -> None:
        """Code 0 is "No min. logged dives required" -- nothing to report."""
        self.assertEqual(
            PadiComAdapter.requirements_from_choices(10, 0),
            {"min_level": DiverLevel.OPEN_WATER.value},
        )

    def test_unknown_code_is_not_guessed(self) -> None:
        """A new enum member is something to go and read, not to default."""
        self.assertIsNone(PadiComAdapter.requirements_from_choices(60))
        self.assertIsNone(PadiComAdapter.requirements_from_choices(10, 99))

    def test_nothing_stated(self) -> None:
        self.assertIsNone(PadiComAdapter.requirements_from_choices(None, None))


# Trimmed from the live response for
# /api/v2/travel/shop/egypt/hammerhead-ii/itineraries/red-sea-charm-abu-nuhas-ss-thistlegorm-the-brother/
# on 2026-08-28. 95 fields came back; these are the ones read.
DETAIL = {
    "title": "Deepest South: Abu Fandira - Sataya\u200b\u200b (Fury Shoals) - St. John's "
             "(Marsa Alam - Marsa Alam) 7 Nights",
    "slug": "red-sea-charm-abu-nuhas-ss-thistlegorm-the-brother",
    "id": 21681,
    "shopTitle": "Hammerhead II",
    "length": 7,
    "harbourDepartureTitle": "Marsa Alam",
    "harbourArrivalTitle": "Marsa Alam",
    "totalNumberOfDives": 17,
    "totalNumberOfDivesMax": 18,
    "requiredCertification": 30,
    "experienceRequiredDives": 20,
    "minimalNumberOfDives": 50,
    "experienceRequired": None,
}


class TestPayload(unittest.TestCase):
    """What the JSON endpoint states, once the bundle gave up its address."""

    def test_entry_bar(self) -> None:
        got = PadiComAdapter.requirements_from_payload(DETAIL)
        self.assertEqual(got["min_level"], DiverLevel.ADVANCED.value)
        self.assertEqual(got["recommended_logged_dives"], 50)
        self.assertEqual(got["min_logged_dives"], 50)

    def test_minimum_is_not_the_enum_restated(self) -> None:
        """Blue Melody states 30, which the enum cannot produce.

        The enum resolves to 0, 20, 50 or 100 only, so a 30 proves the integer
        field is the operator's own number and not a rendering of the code beside
        it. Folding the two together would lose that.
        """
        payload = {**DETAIL, "requiredCertification": 10,
                   "experienceRequiredDives": 10, "minimalNumberOfDives": 30}
        got = PadiComAdapter.requirements_from_payload(payload)
        self.assertEqual(got["min_level"], DiverLevel.OPEN_WATER.value)
        self.assertEqual(got["recommended_logged_dives"], 20)
        self.assertEqual(got["min_logged_dives"], 30)

    def test_nothing_stated(self) -> None:
        self.assertIsNone(PadiComAdapter.requirements_from_payload(
            {"requiredCertification": None, "minimalNumberOfDives": 0}))

    def test_dive_count_keeps_the_low_end(self) -> None:
        """17-18 is reported as 17. A range shown as its ceiling flatters
        the price per dive, which is the number this site exists to get right."""
        self.assertEqual(PadiComAdapter.itinerary_from_payload(DETAIL)["dives"], 17)

    def test_facts_are_stated_not_parsed(self) -> None:
        record = PadiComAdapter.itinerary_from_payload(DETAIL)
        self.assertEqual(record["nights"], 7)
        self.assertEqual(record["ports"], "Marsa Alam - Marsa Alam")
        self.assertEqual(record["boat_name"], "Hammerhead II")
        self.assertEqual(record["padi_id"], 21681)

    def test_name_still_joins_to_ours(self) -> None:
        """The payload title minus its nights is our Itinerary.name."""
        record = PadiComAdapter.itinerary_from_payload(DETAIL)
        ours = ("Deepest South: Abu Fandira - Sataya\u200b\u200b (Fury Shoals) - "
                "St. John's (Marsa Alam - Marsa Alam)")
        self.assertEqual(
            PadiComAdapter.compare_key(str(record["name"])),
            PadiComAdapter.compare_key(ours),
        )

    def test_url_shapes(self) -> None:
        from liveaboard.scrape.padi_com import ITINERARY_DETAIL, ITINERARY_LIST

        self.assertEqual(
            ITINERARY_LIST.format(vessel="hammerhead-ii"),
            "https://travel.padi.com/api/v2/travel/shop/hammerhead-ii/itineraries/?kind=10",
        )
        self.assertEqual(
            ITINERARY_DETAIL.format(country="egypt", vessel="hammerhead-ii", slug="a-trip"),
            "https://travel.padi.com/api/v2/travel/shop/egypt/hammerhead-ii/itineraries/a-trip/",
        )


class TestItineraryTitles(unittest.TestCase):
    """Titles come from the anchor. Slugs are ids and nothing more."""

    NAV = """
      <li><a ng-click="operator.setItinerary({ itinerary: { slug: 'x' } })"
             href="/liveaboard/egypt/hammerhead-ii/mini-wrecks-and-nature-hurghada-hurghada-5-nights/"
             max-width="1150">Brothers Light 3 (Marsa Alam - Marsa Alam) 3 Nights</a></li>
      <li><a href="/liveaboard/egypt/hammerhead-ii/sharks-dolphins-marsa-alam-marsa-alam-7-nights/"
             >Sharks &amp;amp; Dolphins (Marsa Alam - Hurghada) 7 Nights</a></li>
      <li><a href="/liveaboard-deals/">Liveaboard Deals</a></li>
    """

    def test_reads_the_title_not_the_slug(self) -> None:
        """This slug says 5 nights from Hurghada. Its page says 3, from Marsa Alam.

        Both live, both on the same anchor. Parsing the URL here would have
        produced a confident wrong answer, which is worse than none.
        """
        titles = PadiComAdapter.itinerary_titles(self.NAV)
        title = titles["mini-wrecks-and-nature-hurghada-hurghada-5-nights"]
        self.assertEqual(title, "Brothers Light 3 (Marsa Alam - Marsa Alam) 3 Nights")
        split = PadiComAdapter.split_title(title)
        assert split
        self.assertEqual(split[2], 3)

    def test_decodes_double_escaped_entities(self) -> None:
        titles = PadiComAdapter.itinerary_titles(self.NAV)
        self.assertIn(
            "Sharks & Dolphins (Marsa Alam - Hurghada) 7 Nights",
            titles.values(),
        )

    def test_drops_navigation_that_is_not_a_trip(self) -> None:
        self.assertEqual(len(PadiComAdapter.itinerary_titles(self.NAV)), 2)


class TestDiscover(unittest.TestCase):
    """One sitemap, one country, no crawl."""

    SITEMAP = """<?xml version="1.0"?><urlset>
      <url><loc>https://travel.padi.com/liveaboard/egypt/hammerhead-ii/</loc></url>
      <url><loc>https://travel.padi.com/de/tauchsafari-tauchen/aegypten/hammerhead-ii/</loc></url>
      <url><loc>https://travel.padi.com/liveaboard/sudan/my-blue-melody/</loc></url>
      <url><loc>https://travel.padi.com/dive-resort/croatia/najada-diving-apartments/</loc></url>
      <url><loc>https://travel.padi.com/liveaboard/egypt/hammerhead-ii/</loc></url>
    </urlset>"""

    def urls(self) -> list[str]:
        from liveaboard.scrape.padi_com import OPERATOR_SITEMAP

        adapter = PadiComAdapter(StubFetcher({OPERATOR_SITEMAP: self.SITEMAP}))
        return list(adapter.discover())

    def test_one_country_only(self) -> None:
        self.assertEqual(self.urls(), ["https://travel.padi.com/liveaboard/egypt/hammerhead-ii/"])

    def test_costs_one_request(self) -> None:
        from liveaboard.scrape.padi_com import OPERATOR_SITEMAP

        fetcher = StubFetcher({OPERATOR_SITEMAP: self.SITEMAP})
        list(PadiComAdapter(fetcher).discover())
        self.assertEqual(len(fetcher.requested), 1)


class TestParse(unittest.TestCase):
    """A vessel page states identity. It does not state the entry bar."""

    PAGE = """
      <title>Hammerhead II | Liveaboard | PADI Travel</title>
      <script type="application/ld+json">
        {"@context":"https://schema.org/","@type":"Product","name":"Hammerhead II"}
      </script>
      <a href="/liveaboard/egypt/hammerhead-ii/red-sea-charm-abu-nuhas-ss-thistlegorm-the-broth-4/"
        >Wrecks &amp;amp; Reefs: Abu Nuhas - SS Thistlegorm - Ras Mohamed (Hurghada - Hurghada) 7 Nights</a>
    """

    def output(self):
        return PadiComAdapter(StubFetcher({})).parse(result(self.PAGE))

    def test_names_the_boat_and_its_trips(self) -> None:
        output = self.output()
        self.assertEqual([b["name"] for b in output.boats], ["Hammerhead II"])
        self.assertEqual(len(output.itineraries), 1)
        itinerary = output.itineraries[0]
        self.assertEqual(
            itinerary["name"],
            "Wrecks & Reefs: Abu Nuhas - SS Thistlegorm - Ras Mohamed (Hurghada - Hurghada)",
        )
        self.assertEqual(itinerary["nights"], 7)
        self.assertEqual(itinerary["boat_name"], "Hammerhead II")

    def test_missing_entry_bar_warns_rather_than_raising(self) -> None:
        """Twenty-two named trips is not a failed fetch."""
        output = self.output()
        self.assertNotIn("requirements", output.itineraries[0])
        self.assertTrue(any("no stated entry bar" in w for w in output.warnings), output.warnings)

    def test_slug_is_carried_but_never_read(self) -> None:
        itinerary = self.output().itineraries[0]
        self.assertEqual(
            itinerary["padi_slug"], "red-sea-charm-abu-nuhas-ss-thistlegorm-the-broth-4"
        )
        # The slug says "red sea charm"; the title says "Wrecks & Reefs".
        self.assertNotIn("red sea charm", str(itinerary["name"]).lower())


class TestAliasMap(unittest.TestCase):
    """The committed map, and the distinction it exists to keep."""

    MAP = {
        "aliases": {"iceberg": "my-iceberg", "amelie": "amelie-safari"},
        "absent": ["golden-dolphin"],
    }

    def test_a_pair_resolves(self) -> None:
        self.assertEqual(PadiComAdapter.vessel_for("iceberg", self.MAP), "my-iceberg")

    def test_absent_and_unreviewed_both_resolve_to_nothing(self) -> None:
        self.assertIsNone(PadiComAdapter.vessel_for("golden-dolphin", self.MAP))
        self.assertIsNone(PadiComAdapter.vessel_for("sea-serpent", self.MAP))

    def test_but_they_are_not_the_same_state(self) -> None:
        """Somebody looked and found nothing, versus nobody looked yet.

        Both give no slug, so only this tells a finished review from an
        unstarted one -- and a tail that passes as settled never gets finished.
        """
        self.assertTrue(PadiComAdapter.is_reviewed("golden-dolphin", self.MAP))
        self.assertFalse(PadiComAdapter.is_reviewed("sea-serpent", self.MAP))

    def test_the_committed_map_keys_on_real_boat_ids(self) -> None:
        """Every key must be a boat we actually hold.

        A key that matches nothing fails silently -- vessel_for returns None,
        which is indistinguishable from unreviewed -- and that is exactly how
        the first version of this file went wrong, keying "MY Odyssey
        Liveaboard" as its folded name when its boat_id is "odyssey".
        """
        import json
        from pathlib import Path

        aliases = json.loads(Path("data/padi_aliases.json").read_text())
        boats = {b["id"] for b in json.loads(Path("data/egypt-2027.json").read_text())["boats"]}
        unknown = sorted(set(aliases["aliases"]) - boats)
        self.assertEqual(unknown, [], f"alias keys matching no boat: {unknown}")
        stale = sorted(set(aliases.get("absent") or []) - boats)
        self.assertEqual(stale, [], f"absent entries matching no boat: {stale}")


if __name__ == "__main__":
    unittest.main()
