"""Tests for route, theme and experience-level derivation."""

from __future__ import annotations

import unittest

from liveaboard.classify import classify, infer_route, normalise, themes_in_season
from liveaboard.models import Itinerary, Requirements
from liveaboard.taxonomy import DiverLevel, Route, Theme


def make(sites: list[str], **kwargs) -> Itinerary:
    return Itinerary(
        id="itin",
        name="Test",
        operator_id="op",
        boat_id="boat",
        nights=kwargs.pop("nights", 7),
        dives=20,
        port_from="Hurghada",
        port_to="Hurghada",
        dive_sites=sites,
        **kwargs,
    )


class TestNormalise(unittest.TestCase):
    def test_transliteration_variants_collapse(self):
        self.assertEqual(normalise("Sha'ab Maksur"), normalise("Shaab Maksur"))
        self.assertEqual(normalise("St. John's"), normalise("St Johns"))

    def test_accents_are_folded(self):
        self.assertEqual(normalise("Ras Mohamméd"), "ras mohammed")


class TestRouteInference(unittest.TestCase):
    def test_northern_wrecks(self):
        itinerary = make(["SS Thistlegorm", "Abu Nuhas", "Giannis D", "Carnatic"])
        self.assertEqual(infer_route(itinerary), Route.NORTH_WRECKS_REEFS)

    def test_offshore_sharks(self):
        itinerary = make(["Big Brother", "Little Brother", "Daedalus Reef", "Elphinstone Reef"])
        self.assertEqual(infer_route(itinerary), Route.BDE)

    def test_deep_south_beats_st_johns_when_rocky_and_zabargad_appear(self):
        """The industry sells this as Deep South; St John's is the narrower label."""
        itinerary = make(
            ["Rocky Island", "Zabargad", "St John's", "Habili Ali", "Dangerous Reef"]
        )
        self.assertEqual(infer_route(itinerary), Route.DEEP_SOUTH)

    def test_three_cruising_grounds_make_a_combination(self):
        itinerary = make(
            [
                "SS Thistlegorm", "Abu Nuhas",
                "Big Brother", "Little Brother", "Daedalus Reef",
                "Rocky Island", "Zabargad",
            ]
        )
        self.assertEqual(infer_route(itinerary), Route.COMBINATION)

    def test_two_grounds_resolve_to_the_stronger_one(self):
        """Overlap is ordinary; only a genuine three-region crossing is a combination."""
        itinerary = make(
            ["SS Thistlegorm", "Abu Nuhas", "Giannis D", "Carnatic", "Ras Mohammed"]
        )
        self.assertEqual(infer_route(itinerary), Route.NORTH_WRECKS_REEFS)

    def test_explicit_route_overrides_inference(self):
        itinerary = make(["SS Thistlegorm"], route=Route.FURY_SHOAL)
        self.assertEqual(infer_route(itinerary), Route.FURY_SHOAL)

    def test_unknown_sites_yield_no_route(self):
        self.assertIsNone(infer_route(make(["Somewhere Unlisted"])))

    def test_empty_site_list_yields_no_route(self):
        self.assertIsNone(infer_route(make([])))


class TestThemes(unittest.TestCase):
    def test_wrecks_are_detected(self):
        result = classify(make(["SS Thistlegorm", "Giannis D", "Carnatic"]))
        self.assertIn(Theme.WRECKS, result.themes)

    def test_shark_umbrella_is_added_when_earned(self):
        result = classify(make(["Daedalus Reef", "Elphinstone Reef"]))
        self.assertIn(Theme.SHARKS_PELAGIC, result.themes)
        self.assertIn(Theme.HAMMERHEADS, result.themes)

    def test_gentle_reef_trip_gets_no_shark_tag(self):
        result = classify(make(["Fury Shoal", "Abu Galawa", "Claudia"]))
        self.assertNotIn(Theme.SHARKS_PELAGIC, result.themes)
        self.assertIn(Theme.DOLPHINS, result.themes)


class TestSeasonality(unittest.TestCase):
    def test_hammerheads_peak_in_high_summer(self):
        self.assertIn(Theme.HAMMERHEADS, themes_in_season([Theme.HAMMERHEADS], 7))
        self.assertNotIn(Theme.HAMMERHEADS, themes_in_season([Theme.HAMMERHEADS], 5))

    def test_aseasonal_themes_are_never_flagged_in_season(self):
        """A wreck is a wreck in February; the badge would be noise."""
        self.assertEqual(themes_in_season([Theme.WRECKS, Theme.REEF], 7), [])


class TestLevelInference(unittest.TestCase):
    def test_offshore_routes_default_to_fifty_dives(self):
        result = classify(make(["Big Brother", "Daedalus Reef", "Elphinstone Reef"]))
        self.assertEqual(result.level, DiverLevel.ADVANCED_50)

    def test_sheltered_reefs_stay_open_water(self):
        result = classify(make(["Fury Shoal", "Sataya", "Abu Galawa"]))
        self.assertEqual(result.level, DiverLevel.OPEN_WATER)

    def test_stated_dive_count_wins(self):
        itinerary = make(
            ["Fury Shoal"], requirements=Requirements(min_logged_dives=100)
        )
        self.assertEqual(classify(itinerary).level, DiverLevel.EXPERIENCED_100)

    def test_stated_requirement_is_never_softened(self):
        """Safety gates come from the operator, never from our own inference."""
        itinerary = make(
            ["Fury Shoal"], requirements=Requirements(min_level=DiverLevel.ADVANCED)
        )
        self.assertEqual(classify(itinerary).level, DiverLevel.ADVANCED)


if __name__ == "__main__":
    unittest.main()


class TestBdeIsAllThreeOrNothing(unittest.TestCase):
    """BDE is a named set of places, not a score.

    A week reaching two of the three is not a weaker BDE; it is a different
    trip. Counting made the label flip on a single word -- see #36.
    """

    def test_all_three_is_bde(self):
        self.assertIs(
            infer_route(make(["brothers", "daedalus", "elphinstone"])), Route.BDE
        )

    def test_a_wreck_on_big_brother_names_the_brothers(self):
        """Numidia and Aida lie on Big Brother, so naming either is naming it."""
        self.assertIs(
            infer_route(make(["numidia", "daedalus", "elphinstone"])), Route.BDE
        )

    def test_st_johns_with_two_pillars_is_southern_not_offshore(self):
        """The bug this rule exists for.

        "Daedalus & St. John's" read as deep south correctly. Adding
        Elphinstone gave the offshore family two hits against the south's one
        and flipped it to BDE -- though St John's is 150 nautical miles further
        south and is what the week is for.
        """
        self.assertIs(
            infer_route(make(["daedalus", "elphinstone", "st johns"])),
            Route.DEEP_SOUTH,
        )

    def test_the_two_site_version_was_already_right_and_stays_right(self):
        self.assertIs(infer_route(make(["daedalus", "st johns"])), Route.DEEP_SOUTH)

    def test_the_brothers_alone_are_not_bde(self):
        """"North & Brothers" names one pillar. It is a northern week that adds
        the Brothers, and calling it BDE was wrong."""
        self.assertIsNot(infer_route(make(["brothers"])), Route.BDE)

    def test_an_offshore_pair_gets_no_label_rather_than_a_wrong_one(self):
        """There is no honest name for Daedalus and Elphinstone without the
        Brothers, and the dive-site column already says where the trip goes."""
        self.assertIsNone(infer_route(make(["daedalus", "elphinstone"])))

    def test_a_real_bde_week_is_not_swallowed_by_the_combination_rule(self):
        """A genuine cross-coast run stays a combination; BDE plus a passing
        northern day does not become one."""
        self.assertIs(
            infer_route(make(["brothers", "daedalus", "elphinstone", "thistlegorm"])),
            Route.BDE,
        )

    def test_an_explicit_route_still_wins(self):
        """A value stated in the dataset is never second-guessed."""
        self.assertIs(
            infer_route(make(["daedalus", "elphinstone"], route=Route.BDE)), Route.BDE
        )

    def test_a_cross_coast_run_keeps_its_combination(self):
        """#36's own warning: a decisive-site rule must not swallow these.

        Where a trip has *been* and what it should be *called* are different
        questions. Brothers and Daedalus without Elphinstone is not a BDE week,
        but the boat was still offshore -- so the crossing still counts three
        cruising grounds.
        """
        self.assertIs(
            infer_route(make([
                "SS Thistlegorm", "Abu Nuhas",
                "Big Brother", "Daedalus Reef",
                "Rocky Island", "Zabargad",
            ])),
            Route.COMBINATION,
        )
