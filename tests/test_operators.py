"""Tests for reading who runs a trip.

Every itinerary in the dataset was filed under ``unknown-operator`` -- 317 of
317 -- on the belief that an agency listing names the vessel and not the
company. It names both. ``Event.organizer.name`` is present on 878 of 878
archived events across 42 operators, and the parser read the node and dropped
the field.

Two things are under test: that the field survives from the Event node to the
dataset, and that folding one company's several spellings stays a table rather
than becoming fuzzy matching.
"""

from __future__ import annotations

import unittest
from datetime import date

from liveaboard.dataset import Dataset
from liveaboard.promote import OPERATOR_ALIASES, UNKNOWN_OPERATOR, operator_record, promote
from liveaboard.scrape.vessel import operator_from_markup
from liveaboard.scrape.liveaboard_com import organizer_name

SEASON = (date(2027, 5, 1), date(2027, 8, 31))
PROV = {"kind": "scraped", "source_id": "liveaboard.com", "retrieved": "2026-08-27"}


def departure(boat="alia-soul", name="Brothers & Daedalus", start="2027-05-01",
              end="2027-05-08", operator="Orca Dive Clubs", **extra):
    entry = {
        "id": f"{boat}-{start}",
        "boat_slug": boat,
        "name": name,
        "start": start,
        "end": end,
        "price": {"amount": 1450.0, "currency": "USD"},
        "provenance": PROV,
        **extra,
    }
    if operator is not None:
        entry["operator"] = operator
    return entry


def candidate(departures, itineraries=None):
    return {
        "scraped_at": "2026-08-27",
        "itineraries": itineraries or [{"id": "alia-soul", "name": "Alia Soul", "boat": "Alia Soul"}],
        "departures": departures,
    }


class TestOrganizerName(unittest.TestCase):
    """Reading the field off an Event node."""

    def test_reads_the_organizer(self):
        node = {"organizer": {"@type": "Organization", "name": "Emperor Divers"}}
        self.assertEqual(organizer_name(node), "Emperor Divers")

    def test_collapses_whitespace(self):
        """"Tauch Safari Aegypten " is on the listing with a trailing space.

        A name differing from another only by whitespace is not a second
        company, so this is folded at the point of reading rather than left for
        the alias table to enumerate.
        """
        self.assertEqual(organizer_name({"organizer": {"name": "Tauch Safari Aegypten "}}),
                         "Tauch Safari Aegypten")
        self.assertEqual(organizer_name({"organizer": {"name": "Sea  Serpent\tFleet"}}),
                         "Sea Serpent Fleet")

    def test_takes_the_first_of_a_list(self):
        node = {"organizer": [{"name": "Sadko Travel"}, {"name": "Another"}]}
        self.assertEqual(organizer_name(node), "Sadko Travel")

    def test_absent_is_none_not_a_guess(self):
        for node in ({}, {"organizer": None}, {"organizer": {}}, {"organizer": []},
                     {"organizer": "Emperor Divers"}, {"organizer": {"name": "   "}},
                     {"organizer": {"name": 42}}):
            with self.subTest(node=node):
                self.assertIsNone(organizer_name(node))


class TestOperatorRecord(unittest.TestCase):
    def test_slugifies_the_name(self):
        self.assertEqual(operator_record("King Snefro Group"),
                         {"id": "king-snefro-group", "name": "King Snefro Group"})

    def test_folds_the_aggressor_typo(self):
        """A missing space in the source, and 50 departures behind it."""
        record = operator_record("Aggressor Fleet& Dancer Fleet")
        self.assertEqual(record["name"], "Aggressor Fleet & Dancer Fleet")
        self.assertEqual(record["id"], "aggressor-fleet-dancer-fleet")

    def test_folding_is_a_table_not_a_similarity_test(self):
        """Red Sea Explorers and Red Sea Relax are two companies, not one.

        Any fuzzy matcher loose enough to merge the Aggressor typo also merges
        these, which is why the folding is an explicit table.
        """
        self.assertNotEqual(operator_record("Red Sea Explorers")["id"],
                            operator_record("Red Sea Relax")["id"])

    def test_names_are_otherwise_verbatim(self):
        """Tidying someone's capitalisation is deciding what they are called."""
        for name in ("XPLORER AQUARIUS Safari", "MV Legends II", "Dune-World", "Divers & Co"):
            with self.subTest(name=name):
                self.assertEqual(operator_record(name)["name"], name)

    def test_alias_keys_are_lowercase(self):
        """Lookup lowercases the name, so an uppercase key would never match."""
        for key in OPERATOR_ALIASES:
            self.assertEqual(key, key.lower())


class TestPromoteAssignsOperators(unittest.TestCase):
    def test_operator_reaches_boat_and_itinerary(self):
        payload = promote(candidate([departure(operator="Orca Dive Clubs")]), season=SEASON)
        self.assertEqual([o["name"] for o in payload["operators"]], ["Orca Dive Clubs"])
        self.assertEqual(payload["boats"][0]["operator_id"], "orca-dive-clubs")
        self.assertEqual(payload["itineraries"][0]["operator_id"], "orca-dive-clubs")

    def test_one_operator_across_several_boats_is_one_record(self):
        payload = promote(
            candidate(
                [
                    departure(boat="alia-soul", operator="Emperor Divers"),
                    departure(boat="emperor-elite", operator="Emperor Divers"),
                ],
                itineraries=[
                    {"id": "alia-soul", "boat": "Alia Soul"},
                    {"id": "emperor-elite", "boat": "Emperor Elite"},
                ],
            ),
            season=SEASON,
        )
        self.assertEqual(len(payload["operators"]), 1)
        self.assertEqual({b["operator_id"] for b in payload["boats"]}, {"emperor-divers"})

    def test_spelling_variants_land_on_one_record(self):
        payload = promote(
            candidate(
                [
                    departure(boat="a", operator="Aggressor Fleet& Dancer Fleet"),
                    departure(boat="b", operator="Aggressor Fleet & Dancer Fleet"),
                ],
                itineraries=[{"id": "a", "boat": "A"}, {"id": "b", "boat": "B"}],
            ),
            season=SEASON,
        )
        self.assertEqual(len(payload["operators"]), 1)
        self.assertEqual(payload["operators"][0]["name"], "Aggressor Fleet & Dancer Fleet")

    def test_a_silent_source_falls_back_rather_than_inventing(self):
        payload = promote(candidate([departure(operator=None)]), season=SEASON)
        self.assertEqual(payload["operators"], [dict(UNKNOWN_OPERATOR)])
        self.assertEqual(payload["itineraries"][0]["operator_id"], UNKNOWN_OPERATOR["id"])

    def test_the_fallback_row_is_absent_when_nothing_uses_it(self):
        """"Operator not captured" on a page where every trip has a company
        behind it is a company that does not exist."""
        payload = promote(candidate([departure(operator="Sea Pirates")]), season=SEASON)
        self.assertNotIn(UNKNOWN_OPERATOR["id"], {o["id"] for o in payload["operators"]})

    def test_a_mixed_fleet_keeps_both_kinds(self):
        payload = promote(
            candidate(
                [
                    departure(boat="a", operator="Sea Pirates"),
                    departure(boat="b", operator=None),
                ],
                itineraries=[{"id": "a", "boat": "A"}, {"id": "b", "boat": "B"}],
            ),
            season=SEASON,
        )
        self.assertEqual(
            [o["id"] for o in payload["operators"]], ["sea-pirates", UNKNOWN_OPERATOR["id"]]
        )

    def test_disagreeing_departures_warn_rather_than_pick_silently(self):
        """One boat under two companies is a fact worth noticing, not averaging."""
        payload = promote(
            candidate(
                [
                    departure(start="2027-05-01", end="2027-05-08", operator="Emperor Divers"),
                    departure(start="2027-05-15", end="2027-05-22", operator="Emperor Divers"),
                    departure(start="2027-06-01", end="2027-06-08", operator="Sadko Travel"),
                ]
            ),
            season=SEASON,
        )
        warnings = payload.get("promotion_skipped", [])
        self.assertTrue(any("more than one operator" in w for w in warnings), warnings)
        # It still resolves, to the answer most departures gave.
        self.assertEqual(payload["itineraries"][0]["operator_id"], "emperor-divers")

    def test_every_referenced_operator_resolves(self):
        """dataset refuses to load an itinerary pointing at a missing operator."""
        payload = promote(
            candidate(
                [
                    departure(boat="a", operator="Sea Pirates"),
                    departure(boat="b", operator="Golden Dolphin"),
                    departure(boat="c", operator=None),
                ],
                itineraries=[{"id": x, "boat": x.upper()} for x in ("a", "b", "c")],
            ),
            season=SEASON,
        )
        dataset = Dataset.from_dict(payload)
        for itinerary in dataset.itineraries.values():
            self.assertIn(itinerary.operator_id, dataset.operators)


if __name__ == "__main__":
    unittest.main()


class TestTheOperatorAVesselPageNames(unittest.TestCase):
    """`Product.brand.name`, and the reason it had to be looked for.

    Every operator on this page comes from an `Event.organizer`. A vessel
    **liveaboard.com sells no berths on** has no `Event`, so 22 hulls fell back
    to PADI's `fleetTitle` — a shelf on a booking site rather than a company,
    and shouted: `BELLA LIVEABOARDS` where this field says `Bella Liveaboard`.

    Measured on the 10 PADI-only vessels that have a liveaboard.com page at
    all: **10 of 10** state a brand, and every one is the operating company.
    """

    def brand(self, name):
        return (
            '<script type="application/ld+json">'
            '{"@type": "Product", "name": "MY Blue Pearl", "brand": '
            '{"@type": "Brand", "name": "%s"}}</script>' % name
        )

    def test_the_brand_is_the_operator(self):
        self.assertEqual(operator_from_markup(self.brand("Blue Planet Liveaboards")),
                         "Blue Planet Liveaboards")

    def test_a_page_with_no_product_node_states_nothing(self):
        """Not a guess from the slug. A boat published under a name this code
        invented is worse than one published under none."""
        self.assertIsNone(operator_from_markup("<html><body>Blue Pearl</body></html>"))

    def test_a_brand_with_no_name_states_nothing(self):
        self.assertIsNone(operator_from_markup(
            '<script type="application/ld+json">'
            '{"@type": "Product", "brand": {"@type": "Brand"}}</script>'))

    def test_whitespace_is_collapsed_and_nothing_else_is_touched(self):
        """Verbatim otherwise, on the standing rule: tidying a company's
        capitalisation is a short step from deciding what it is called."""
        # The escape stays an escape: a raw newline inside a JSON string is
        # invalid JSON, and the reader would rightly return None for it.
        self.assertEqual(operator_from_markup(self.brand(r"Sea  Queen\n Fleet")),
                         "Sea Queen Fleet")

    def test_it_settles_the_two_blues_with_evidence_rather_than_tidiness(self):
        """PADI shelves MY Blue and MY Blue Pearl under one "BLUE PLANET
        Fleet", and folding them on that alone asserted a company for a hull
        our own source connected to nobody — so the alias was written and then
        removed, correctly. Blue Pearl's own page states the company outright,
        which is what was missing."""
        self.assertEqual(operator_from_markup(self.brand("Blue Planet Liveaboards")),
                         "Blue Planet Liveaboards")

    def test_the_page_stated_operator_beats_padi_s_fleet_label(self):
        """Not a judgement call: it is the same source every other operator on
        this page comes from, against a shelf on the other one."""
        from test_promote import candidate, departure

        payload = promote(
            candidate([departure(boat="blue-pearl")],
                      itineraries=[{"id": "blue-pearl", "name": "Blue Pearl",
                                    "boat": "MY Blue Pearl"}]),
            season=SEASON,
            fees={"vessels": {"blue-pearl": {"operator": "Blue Planet Liveaboards"}}},
            padi={"collected": "2026-08-29", "vessels": {
                "blue-pearl": {"slug": "my-blue-pearl", "name": "MY Blue Pearl",
                               "operator": "BLUE PLANET"}}},
        )
        names = {o["name"] for o in payload["operators"]}
        self.assertIn("Blue Planet Liveaboards", names)
        self.assertNotIn("BLUE PLANET", names)

    def test_padi_s_label_still_answers_where_no_page_states_one(self):
        """12 of the 22 have no liveaboard.com page at all. A fleet label is
        weaker than a stated company and better than a title-cased slug."""
        from test_promote import candidate, departure

        payload = promote(
            candidate([departure(boat="seawolf-steel")],
                      itineraries=[{"id": "seawolf-steel", "name": "Seawolf Steel",
                                    "boat": "Seawolf Steel"}]),
            season=SEASON,
            padi={"collected": "2026-08-29", "vessels": {
                "seawolf-steel": {"slug": "seawolf-steel", "name": "Seawolf Steel",
                                  "operator": "SEAWOLF DIVING SAFARI"}}},
        )
        self.assertIn("SEAWOLF DIVING SAFARI", {o["name"] for o in payload["operators"]})
