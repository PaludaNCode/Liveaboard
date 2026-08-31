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

import published
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

    def test_nights_followed_by_a_day_count(self) -> None:
        """Live titles PADI ends six different ways. All were fetched and stored,
        then dropped at keying by a pattern anchored on end-of-string."""
        for title, nights in (
            ("Hurghada North (Hurghada - Hurghada) 4 nights/4 days diving", 4),
            ("Fury Shoals (Hamata - Hamata) 7 nights / 8 days", 7),
            ("Overnight Sataya (Hamata - Hamata) 1 night / 2 days", 1),
        ):
            split = PadiComAdapter.split_title(title)
            self.assertIsNotNone(split, title)
            assert split
            self.assertEqual(split[2], nights, title)

    def test_a_hotel_night_is_not_part_of_the_trip(self) -> None:
        split = PadiComAdapter.split_title(
            "Extended North Route (Sharm el Sheikh - Sharm el Sheikh) "
            "7 nights liveabaord + 1 night hotel")
        assert split
        self.assertEqual(split[2], 7)

    def test_disagreeing_night_counts_are_not_resolved(self) -> None:
        """A trip length is the denominator under every per-night price."""
        self.assertIsNone(
            PadiComAdapter.split_title("Brothers Light (Marsa Alam - Hurghada) 4 Nights 7 Nights")
        )

    def test_navigation_without_a_night_count_is_not_a_trip(self) -> None:
        self.assertIsNone(PadiComAdapter.split_title("Liveaboard Deals"))
        self.assertIsNone(PadiComAdapter.split_title("Diving in Egypt"))

    def test_a_count_in_front_of_the_ports_is_still_the_trip_length(self) -> None:
        """The seventh ending form, and the one that leaves nothing to end on.

        Red Sea Aggressor II writes it this way and no other boat does. Read
        only off the tail, the title parses to nothing -- which is not a
        harmless miss now that an unparsable title is a sailing the page does
        not publish.
        """
        split = PadiComAdapter.split_title(
            "Northern Red Sea, Ras Mohamed and Straits of Tiran 7 Nights "
            "(Hurghada -Hurghada)"
        )
        assert split is not None
        name, ports, nights = split
        self.assertEqual(nights, 7)
        self.assertEqual(ports, "Hurghada -Hurghada")
        self.assertEqual(
            name, "Northern Red Sea, Ras Mohamed and Straits of Tiran (Hurghada -Hurghada)",
            "the length is struck out of the name: it is how long the trip is, "
            "not what the trip is called",
        )

    def test_the_tail_still_wins_where_it_speaks(self) -> None:
        """The head is a fallback, never a second opinion. A count in the name
        competing with one after the ports is the ambiguity the tail rule
        already refuses to guess at."""
        split = PadiComAdapter.split_title("Brothers 3 Nights (Marsa Alam - Hurghada) 7 Nights")
        assert split is not None
        self.assertEqual(split[2], 7)
        self.assertEqual(split[0], "Brothers 3 Nights (Marsa Alam - Hurghada)")

    def test_two_counts_in_the_name_are_refused(self) -> None:
        self.assertIsNone(
            PadiComAdapter.split_title("Brothers 4 Nights and 7 Nights (Marsa Alam - Hurghada)")
        )


class TestCompareKey(unittest.TestCase):
    """The conjunction is a separator, so it folds with the punctuation.

    Stripping symbols makes *A & B* and *A, B* one key and leaves *A and B* a
    third, so an operator writing one list three ways reached us as two trips.
    It cost Red Sea Aggressor II and Blue Storm a match each -- and worse than a
    miss, since a near-duplicate itinerary splits a trip's dates and can slugify
    onto the id of the trip it duplicates.

    Counted over all 317 trips of the dataset this was written against: the fold
    merges two pairs and no others, both one trip typed twice.
    """

    def key(self, value: str) -> str:
        return PadiComAdapter.compare_key(value)

    def test_the_three_spellings_of_one_list_agree(self) -> None:
        self.assertEqual(self.key("Brothers & Daedalus"), self.key("Brothers, Daedalus"))
        self.assertEqual(self.key("Brothers & Daedalus"), self.key("Brothers and Daedalus"))

    def test_the_live_pair_it_was_written_for(self) -> None:
        self.assertEqual(
            self.key("Northern Red Sea, Ras Mohamed and Straits of Tiran (Hurghada -Hurghada)"),
            self.key("Northern Red Sea, Ras Mohamed, Straits of Tiran (Hurghada - Hurghada)"),
        )

    def test_it_does_not_fold_two_different_trips(self) -> None:
        """A word cannot separate a title from a different trip unless the two
        are otherwise identical, in which case they were the same trip."""
        self.assertNotEqual(self.key("North & Tiran"), self.key("North & Safaga"))
        self.assertNotEqual(self.key("St John's"), self.key("St John's & Daedalus"))


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
        # Two fields, not one joined string. Two of PADI's eight harbour names
        # contain the " - " a joined string would have to be split on, so
        # `ports` was unreadable on 11 trips and is gone.
        self.assertEqual((record["port_from"], record["port_to"]),
                         ("Marsa Alam", "Marsa Alam"))
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
        """Every key must be a boat we hold, or one we minted for PADI.

        A key that matches nothing fails silently -- vessel_for returns None,
        which is indistinguishable from unreviewed -- and that is exactly how
        the first version of this file went wrong, keying "MY Odyssey
        Liveaboard" as its folded name when its boat_id is "odyssey".

        `padi_only` is the exemption and needs one: those ids are minted in this
        file rather than by the first source, and a vessel with no sailing
        inside the published season never becomes a boat in the dataset. Twelve
        of the 22 are in that state today -- PADI's calendar for them stops
        before May 2027, or in VIP One's case prices every sailing at zero. They
        are still reviewed, still mapped, and still fetched every refresh, so
        the day one of them opens a season it appears without anybody editing
        this file.
        """
        import json
        from pathlib import Path

        aliases = json.loads(Path("data/padi_aliases.json").read_text())
        boats = {b["id"] for b in published.raw()["boats"]}
        minted = set(aliases.get("padi_only") or [])
        unknown = sorted(set(aliases["aliases"]) - boats - minted)
        self.assertEqual(unknown, [], f"alias keys matching no boat: {unknown}")
        stale = sorted(set(aliases.get("absent") or []) - boats)
        self.assertEqual(stale, [], f"absent entries matching no boat: {stale}")

    def test_every_minted_id_is_mapped(self) -> None:
        """The exemption above must not become a place to park a typo.

        A `padi_only` entry that is not also in `aliases` names no PADI slug,
        so nothing would ever fetch it -- and it would read as a reviewed boat
        rather than as the dead string it is.
        """
        import json
        from pathlib import Path

        aliases = json.loads(Path("data/padi_aliases.json").read_text())
        orphan = sorted(set(aliases.get("padi_only") or []) - set(aliases["aliases"]))
        self.assertEqual(orphan, [], f"padi_only entries with no slug: {orphan}")

    def test_no_boat_is_both_ours_and_padi_s_alone(self) -> None:
        """AVO and Blue sat outside every list in this file until 2026-08-29,
        which is the one state it exists to make impossible. Neither may now be
        in two."""
        import json
        from pathlib import Path

        aliases = json.loads(Path("data/padi_aliases.json").read_text())
        both = sorted(set(aliases.get("padi_only") or []) & set(aliases.get("absent") or []))
        self.assertEqual(both, [], f"listed as both PADI-only and absent: {both}")


class TestStricterBar(unittest.TestCase):
    """Where two sources state different gates, neither is softened."""

    from liveaboard.promote import _strictest

    OURS = {"min_level": "open_water", "min_logged_dives": 0, "notes": "Open Water."}
    THEIRS = {"min_level": "advanced", "min_logged_dives": 30, "notes": None}

    def test_the_higher_bar_wins(self) -> None:
        from liveaboard.promote import _strictest

        got = _strictest(self.OURS, self.THEIRS)
        self.assertEqual(got["min_level"], "advanced")

    def test_the_winner_is_taken_whole(self) -> None:
        """Not combined field by field.

        Mixing one source's level with the other's dive count states a bar
        neither operator gave, and PADI's minimalNumberOfDives has semantics
        this repo records as unverified -- a maximum across it would raise a
        stated 15-dive bar to 50 on an unconfirmed number.
        """
        from liveaboard.promote import _strictest

        ours = {"min_level": "advanced", "min_logged_dives": 15, "notes": "15 required."}
        theirs = {"min_level": "open_water", "min_logged_dives": 50, "notes": None}
        got = _strictest(ours, theirs)
        self.assertEqual(got["min_level"], "advanced")
        self.assertEqual(got["min_logged_dives"], 15)

    def test_a_disagreement_is_disclosed(self) -> None:
        from liveaboard.promote import _strictest

        got = _strictest(self.OURS, self.THEIRS)
        self.assertIn("Sources disagree", got["notes"] or "")
        self.assertIn("liveaboard.com", got["notes"])
        self.assertIn("PADI Travel", got["notes"])

    def test_agreement_says_nothing(self) -> None:
        from liveaboard.promote import _strictest

        same = {"min_level": "advanced", "min_logged_dives": 0, "notes": "Advanced."}
        got = _strictest(same, dict(same))
        self.assertNotIn("Sources disagree", got["notes"] or "")

    def test_one_source_alone_passes_through(self) -> None:
        from liveaboard.promote import _strictest

        self.assertEqual(_strictest(self.OURS, None), self.OURS)
        self.assertEqual(_strictest(None, self.THEIRS), self.THEIRS)
        self.assertIsNone(_strictest(None, None))


if __name__ == "__main__":
    unittest.main()


class TestACharePricedForLastYearIsNotThisYearsCharge(unittest.TestCase):
    """`validFrom`/`validTo` sit on every fee entry and nothing read them.

    PADI keeps a charge's old price beside its new one. Grand Sea Explorer
    lists "Route supplement" twice on every trip -- 300 valid to 2026-12-31,
    400 valid from 2027-01-01 -- and DUNE Longara lists "Environmental taxes"
    at 100 and 200, the second taking over on 2026-06-14. Both reached the bill
    and the parser kept whichever it happened to, on the largest mandatory
    lines in the book.

    A comment in `fees_from_payload` used to reason that two entries under one
    title are two charges the operator bills. The dates were in the same
    payload and refute it: across the store, all **69** such pairs resolve to
    exactly one entry valid in the published season, and not one has two.
    """

    def entry(self, title="Route supplement", price=400.0, **extra):
        return {"title": title, "price": price, "payedPer": 30,
                "isMandatory": True, **extra}

    def fees(self, *entries, season=("2027-05-01", "2027-08-31")):
        return PadiComAdapter.fees_from_payload(
            {"mandatoryOnBoard": list(entries)}, "USD", season)

    def test_an_entry_dated_out_before_the_season_is_dropped(self):
        book = self.fees(
            self.entry(price=300.0, validFrom="2025-12-01", validTo="2026-12-31"),
            self.entry(price=400.0, validFrom="2027-01-01", validTo="2030-01-01"),
        )
        self.assertEqual([line["amount"]["amount"] for line in book["lines"]], [400.0])

    def test_an_entry_that_starts_after_the_season_is_dropped_too(self):
        book = self.fees(
            self.entry(price=400.0, validFrom="2027-01-01", validTo="2030-01-01"),
            self.entry(price=500.0, validFrom="2028-01-01", validTo="2030-01-01"),
        )
        self.assertEqual([line["amount"]["amount"] for line in book["lines"]], [400.0])

    def test_an_entry_stating_no_window_is_kept(self):
        """Silence is not expiry -- 750 of the 896 entries state no window, and
        dropping those would empty most of the book."""
        self.assertEqual(len(self.fees(self.entry())["lines"]), 1)

    def test_a_window_that_merely_overlaps_the_season_is_kept(self):
        """The charge applies for part of the window we publish, which is the
        operator saying it applies. Requiring containment would drop a price
        that is live on the day the trip sails."""
        book = self.fees(self.entry(validFrom="2027-06-01", validTo="2027-06-30"))
        self.assertEqual(len(book["lines"]), 1)

    def test_two_undated_entries_under_one_title_are_still_two_charges(self):
        """What the dates cannot settle, the title must not settle either.
        Folding these would halve a real bill, which is the direction this
        project never rounds."""
        book = self.fees(self.entry(price=100.0), self.entry(price=200.0))
        self.assertEqual([line["amount"]["amount"] for line in book["lines"]], [100.0, 200.0])

    def test_an_expired_entry_never_blocks_a_bill(self):
        """Dropped before its title is read, so an unclassifiable charge that
        stopped applying last year cannot hold a trip's total hostage."""
        book = self.fees(
            self.entry(title="Some charge nobody can classify",
                       validFrom="2025-01-01", validTo="2026-01-01"),
            self.entry(),
        )
        self.assertEqual(book["unreadable"], [])
        self.assertTrue(book["complete"])


class TestWhatTheFareAlreadyCovers(unittest.TestCase):
    """`whatsIncludedNew`, present on 447 of 447 itineraries and read by nothing.

    The other half of a disclosure. The site read what PADI charges on top and
    not what it says is already in, and **included fees stay in the breakdown
    at zero** by invariant -- removing them hides the difference between a
    bundled operator and one that bills at the dock. That rule was being kept
    on one seller's side only.
    """

    def payload(self, *included, mandatory=()):
        return {
            "mandatoryOnBoard": [
                {"title": t, "price": p, "payedPer": 30} for t, p in mandatory
            ],
            "whatsIncludedNew": [{"title": t} for t in included],
        }

    def book(self, *included, mandatory=()):
        return PadiComAdapter.fees_from_payload(
            self.payload(*included, mandatory=mandatory), "USD")

    def lines(self, *included, mandatory=()):
        return [(l["code"], l["tier"], bool(l.get("included")))
                for l in self.book(*included, mandatory=mandatory)["lines"]]

    def test_an_inclusion_is_a_line_at_no_amount(self):
        line = self.book("Free nitrox (for certified nitrox divers)")["lines"][0]
        self.assertTrue(line["included"])
        self.assertNotIn("amount", line)
        self.assertEqual(line["note"], "Free nitrox (for certified nitrox divers)")

    def test_the_tier_is_what_the_charge_would_have_been(self):
        """Mandatory unless the site already treats it as something you choose.
        It agrees with the other seller's parser, which is the check that
        matters: nitrox conditional, harbour fees mandatory."""
        self.assertEqual(
            self.lines("Free nitrox (for certified nitrox divers)", "Harbour fees"),
            [("nitrox", "conditional", True), ("port_fees", "mandatory", True)])

    def test_a_visa_service_is_not_the_visa_being_included(self):
        """The expensive one, and the reason `PARENTHETICAL` is a rule rather
        than a table of exceptions. "Airport Meet & Greet (VISA assistance,
        eligible countries only)" classified as `visa`, which would have told
        eight itineraries' readers that the €25 they still pay at the airport
        was covered. Help with the paperwork is not the charge."""
        self.assertEqual(
            self.lines("Airport Meet & Greet (VISA assistance, eligible countries only)"),
            [])

    def test_a_parenthetical_does_not_stop_a_real_inclusion_reading(self):
        """The rule strips a qualifier, not the name. Measured over all 63
        titles the field uses, it changes the answer on the visa one alone."""
        self.assertEqual(
            self.lines("Transfer from/to the airport (round-trip, only on boat "
                       "arrival & departure days)"),
            [("airport_transfer", "conditional", True)])

    def test_an_amenity_is_not_a_fee_and_never_blocks_a_bill(self):
        """4,493 of the 5,662 entries are Water, Coffee, Free WiFi, a shisha
        lounge. Letting those reach `unreadable` would have taken the book from
        259 complete trips to none."""
        book = self.book("Water", "Coffee", "Free WiFi", "Shisha in the shisha lounge onboard")
        self.assertEqual(book["lines"], [])
        self.assertEqual(book["unreadable"], [])
        self.assertTrue(book["complete"])

    def test_a_charge_that_is_billed_is_never_also_reported_as_covered(self):
        """A stated amount is the stronger claim. They do not collide anywhere
        in the book today, and if they ever do the money wins."""
        lines = self.lines("Harbour fees", mandatory=[("Harbour fees", 40.0)])
        self.assertEqual(lines, [("port_fees", "mandatory", False)])

    def test_one_line_per_code_however_many_wordings_state_it(self):
        """Operators list four transfer entries -- airport, hotel, scheduled
        times, night flights. They are one inclusion, and four lines reading
        "Airport transfers - included" is a breakdown repeating itself."""
        lines = self.lines("Transfer from/to the airport (round-trip)",
                           "Transfer from/to local hotels (round-trip)",
                           "Airport transfer to/from the boat")
        self.assertEqual(lines, [("airport_transfer", "conditional", True)])

    def test_an_inclusion_dated_out_of_the_season_is_dropped_like_a_charge(self):
        book = PadiComAdapter.fees_from_payload(
            {"whatsIncludedNew": [{"title": "Harbour fees",
                                   "validFrom": "2025-01-01", "validTo": "2026-01-01"}]},
            "USD")
        self.assertEqual(book["lines"], [])


class TestAPriceStatedAsAString(unittest.TestCase):
    """`price` is null on 236 mandatory entries and `extraValue` says 133 of them.

    Bella 2 states ``price: null, extraValue: "5 EUR"`` for its Coast Guard Fee
    and ``"10 EUR"`` for its Service fees -- two of the three mandatory charges
    on every trip that boat sells, and the only fee book the site has for it,
    since liveaboard.com sells no berth on the vessel. `fees_from_payload` read
    ``price`` alone and called them unpriced.

    Reading the second field takes the store from **259 trips whose mandatory
    bill adds up to 332**.
    """

    def entry(self, **extra):
        return {"title": "Coast Guard Fee", "payedPer": 30, "isMandatory": True, **extra}

    def book(self, *entries, currency="EUR"):
        return PadiComAdapter.fees_from_payload(
            {"mandatoryOnBoard": list(entries)}, currency)

    def line(self, **extra):
        return self.book(self.entry(**extra))["lines"][0]

    def test_the_string_is_read_where_the_number_is_null(self):
        self.assertEqual(self.line(price=None, extraValue="5 EUR")["amount"],
                         {"amount": 5.0, "currency": "EUR"})

    def test_a_bare_number_takes_the_vessels_currency(self):
        """Which is the assumption `price` already carries: nothing in the
        payload states a currency, so the vessel's is passed in."""
        self.assertEqual(self.line(price=None, extraValue="40")["amount"],
                         {"amount": 40.0, "currency": "EUR"})

    def test_the_number_wins_wherever_it_is_one(self):
        """Where the two disagree, `extraValue` is the stale one. Blue Horizon
        states 56 against "8" -- 8 a night over its seven-night trips, kept
        beside the total that replaced it, and still 56 on its ten-night
        sailings."""
        self.assertEqual(self.line(price=56.0, extraValue="8")["amount"],
                         {"amount": 56.0, "currency": "EUR"})

    def test_a_string_with_no_number_in_it_is_not_a_price(self):
        """Blue Melody's fuel surcharge carries ``extraValue: "USD"``."""
        self.assertNotIn("amount", self.line(price=None, extraValue="USD"))

    def test_a_percentage_of_something_else_is_not_a_price(self):
        """"14% GST (on onboard purchases)" carries the 14 as its figure.
        Anchored at both ends so prose cannot become money."""
        line = self.book(self.entry(title="Local fees", price=None,
                                    extraValue="14% GST (on onboard purchases)"))["lines"][0]
        self.assertNotIn("amount", line)

    def test_a_stated_currency_beats_the_vessels(self):
        """Andromeda writes "5 USD" on a vessel PADI prices in EUR. The
        vessel's currency is an assumption; this is the source speaking."""
        self.assertEqual(self.line(price=None, extraValue="5 USD")["amount"],
                         {"amount": 5.0, "currency": "USD"})

    def test_a_currency_this_parser_cannot_name_is_not_assumed_into_one(self):
        """One vessel writes "8 EU". It plainly means euro and is plainly not a
        currency code, and a rule loose enough to take it is loose enough to
        take the next thing that only looks like one."""
        self.assertNotIn("amount", self.line(price=None, extraValue="8 EU"))

    def test_a_range_keeps_both_ends(self):
        """As liveaboard.com's ranges do: a spread reported as its low end
        understates the bill."""
        line = self.line(price=None, extraValue="35-50 EUR")
        self.assertEqual(line["amount"], {"amount": 35.0, "currency": "EUR"})
        self.assertEqual(line["amount_max"], {"amount": 50.0, "currency": "EUR"})

    def test_a_priced_string_completes_a_bill(self):
        self.assertTrue(self.book(self.entry(price=None, extraValue="5 EUR"))["complete"])

    def test_an_unreadable_string_leaves_the_bill_incomplete(self):
        self.assertFalse(self.book(self.entry(price=None, extraValue="ask on board"))["complete"])


class TestTheOptionalHalfOfPadisDisclosure(unittest.TestCase):
    """`optionalOnBoard` and its two siblings, read by nothing until now.

    The module had the mandatory lists and the inclusions and not the Optional
    ones -- the lists holding nitrox and gear hire, the two extras this site
    puts a toggle on. liveaboard.com's parser has read the Required and Optional
    blocks together since the beginning, so one seller's book was being read at
    a shallower depth than the other's.

    Bella 2 is what it cost: PADI states 50 EUR for nitrox and 40 EUR per diving
    day for the full scuba set, and the page showed neither, on a vessel where
    PADI's book is the only fee book there is.
    """

    def entry(self, title, **extra):
        return {"title": title, "payedPer": 30, "isMandatory": False, **extra}

    def book(self, *entries, field="optionalBookableAdvancePaidOnBoard", **kw):
        return PadiComAdapter.fees_from_payload({field: list(entries), **kw}, "EUR")

    def lines(self, *entries, **kw):
        return [(l["code"], l["tier"], l.get("basis"), (l.get("amount") or {}).get("amount"))
                for l in self.book(*entries, **kw)["lines"]]

    def test_nitrox_is_read_and_the_toggle_governs_it(self):
        self.assertEqual(self.lines(self.entry("Nitrox", extraValue="50 EUR")),
                         [("nitrox", "conditional", "per_trip", 50.0)])

    def test_the_gear_set_is_priced_in_the_unit_padi_states(self):
        """``payedPer: 40`` is "Diving day". Bella 2's set is 40 EUR a day, and
        the other seller's page states the same €40 with no unit at all."""
        self.assertEqual(
            self.lines(self.entry("Full scuba set", payedPer=40, extraValue="40 EUR")),
            [("gear_rental", "conditional", "per_day", 40.0)])

    def test_the_set_names_its_own_contents(self):
        """``fullSetDescription`` is on all 401 of these entries, exactly as the
        other seller's bundle row names what is in it."""
        line = self.book(self.entry(
            "Full scuba set", payedPer=40, extraValue="40 EUR",
            fullSetDescription="Wetsuit, BCD, Regulator"))["lines"][0]
        self.assertEqual(line["note"], "Full scuba set: Wetsuit, BCD, Regulator")

    def test_a_qualification_is_not_the_gas(self):
        """"PADI Enriched Air Diver (Nitrox)" is on 313 of these entries and
        matched the nitrox pattern, which would have priced a certification as
        the gas a diver breathes -- on the toggle this site counts."""
        self.assertEqual(
            self.lines(self.entry("PADI Enriched Air Diver (Nitrox)", extraValue="100 EUR")),
            [("nitrox_course", "optional", "per_trip", 100.0)])

    def test_an_unpriced_optional_extra_survives_as_a_line(self):
        """A third state, distinct from zero and from absent, and it has to
        reach the page: "Alcohol" is listed with no figure on 269 trips."""
        self.assertEqual(self.lines(self.entry("Alcohol")),
                         [("alcohol", "optional", "per_trip", None)])

    def test_a_charging_unit_that_will_not_normalise_is_dropped(self):
        """PADI prices its courses per course and its transfers "return, per
        person"; `PAYED_PER` maps neither, and a price in a unit this dataset
        cannot add to a trip's bill is not one it may add."""
        self.assertEqual(self.lines(self.entry("Private dive guide", payedPer=80,
                                              extraValue="500 EUR")), [])

    def test_an_optional_extra_nobody_can_classify_never_blocks_a_bill(self):
        """72 of the 111 distinct titles here are courses, amenities and single
        gear items -- "PADI Deep Diver", "Espresso coffee", "Wetsuit". An
        unrecognised extra costs a line of data; a misrecognised one puts an
        invented charge on the page."""
        book = self.book(self.entry("Espresso coffee", extraValue="3 EUR"),
                         self.entry("Underwater scooter", extraValue="50 EUR"))
        self.assertEqual(book["lines"], [])
        self.assertEqual(book["unreadable"], [])
        self.assertTrue(book["complete"])

    def test_a_parenthetical_is_a_qualifier_here_too(self):
        """The same rule the inclusions keep. "Airport Meet & Greet (VISA
        assistance, eligible countries only)" is help with the paperwork and
        not the €25 a diver still pays at the airport."""
        self.assertEqual(
            self.lines(self.entry("Airport Meet & Greet (VISA assistance, "
                                  "eligible countries only)", extraValue="20 EUR")),
            [])

    def test_one_line_per_code(self):
        """As `parse_extras` keeps on the other seller's Optional block: two
        lines under one code are one charge printed twice, and where the site
        toggles that code they are also that charge counted twice."""
        self.assertEqual(
            self.lines(self.entry("Nitrox", extraValue="50 EUR"),
                       self.entry("Nitrox 15 liter tanks", extraValue="65 EUR")),
            [("nitrox", "conditional", "per_trip", 50.0)])

    def test_a_mandatory_charge_of_the_same_code_wins_outright(self):
        """The mandatory list is a stronger claim about the same charge, and it
        is the one that has to add up."""
        book = PadiComAdapter.fees_from_payload(
            {"mandatoryOnBoard": [{"title": "Nitrox", "payedPer": 30, "price": 60.0}],
             "optionalOnBoard": [self.entry("Nitrox", extraValue="50 EUR")]}, "EUR")
        self.assertEqual([(l["code"], l["tier"], l["amount"]["amount"]) for l in book["lines"]],
                         [("nitrox", "mandatory", 60.0)])

    def test_an_optional_charge_cannot_make_a_bill_incomplete(self):
        """`complete` is a verdict on what a diver must pay. An unpriced
        cocktail says nothing about it."""
        book = PadiComAdapter.fees_from_payload(
            {"mandatoryOnBoard": [{"title": "Harbour fees", "payedPer": 30, "price": 40.0}],
             "optionalOnBoard": [self.entry("Alcohol")]}, "EUR")
        self.assertTrue(book["complete"])

    def test_a_priced_charge_still_beats_an_inclusion(self):
        """PADI prices nitrox on 30 trips whose inclusions also say "Free
        nitrox", and 17 spell out why: "15 LITER tank nitrox (only 12 liter is
        free of chanrge)". Both claims are true; the one with money on it is the
        one a reader must not miss."""
        book = self.book(self.entry("Nitrox", extraValue="50 EUR"),
                         whatsIncludedNew=[{"title": "Free nitrox"}])
        self.assertEqual([(l["code"], bool(l.get("included"))) for l in book["lines"]],
                         [("nitrox", False)])

    def test_but_a_charge_naming_no_figure_loses_to_one(self):
        """15 trips list a transfer with no price *and* state the transfer as
        included, so first-past-the-post published "airport transfer, price
        unknown" where the seller had said it was covered. An unpriced line is
        not a stated amount, and included fees stay in the breakdown at zero."""
        book = self.book(self.entry("Transfer from/to local hotels (round-trip)"),
                         whatsIncludedNew=[{"title": "Transfer to/from the airport"}])
        self.assertEqual([(l["code"], bool(l.get("included"))) for l in book["lines"]],
                         [("airport_transfer", True)])

    def test_an_optional_entry_dated_out_of_the_season_is_dropped(self):
        self.assertEqual(
            self.lines(self.entry("Nitrox", extraValue="50 EUR",
                                  validFrom="2025-01-01", validTo="2026-01-01")),
            [])
