"""Tests for dataset validation, the seed data, and the rendered payload."""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from liveaboard.dataset import Dataset, DatasetError
from liveaboard.render import build_payload, render
from liveaboard.scrape import jsonld
from liveaboard.scrape.padi_com import PadiComAdapter

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "data" / "seed" / "egypt-2027.json"

MINIMAL = {
    "default_currency": "EUR",
    "fx": {"display_currency": "EUR", "as_of": "2026-08-27", "source": "test", "rates": {}},
    "operators": [{"id": "op", "name": "Operator"}],
    "boats": [{"id": "boat", "name": "Boat", "operator_id": "op"}],
    "itineraries": [
        {
            "id": "itin",
            "name": "Trip",
            "operator_id": "op",
            "boat_id": "boat",
            "nights": 7,
            "port_from": "Hurghada",
        }
    ],
    "departures": [
        {
            "id": "dep",
            "itinerary_id": "itin",
            "start": "2027-05-01",
            "end": "2027-05-08",
            "price": {"amount": 1000, "currency": "EUR"},
            "provenance": {"kind": "scraped", "source_id": "test"},
        }
    ],
}


def with_change(**changes) -> dict:
    payload = json.loads(json.dumps(MINIMAL))
    payload.update(changes)
    return payload


class TestValidation(unittest.TestCase):
    def test_minimal_dataset_loads(self):
        self.assertEqual(len(Dataset.from_dict(MINIMAL).departures), 1)

    def test_departure_pointing_at_missing_itinerary_is_rejected(self):
        """Silently dropping it would make a trip vanish from the site unnoticed."""
        payload = with_change(
            departures=[{**MINIMAL["departures"][0], "itinerary_id": "nope"}]
        )
        with self.assertRaises(DatasetError):
            Dataset.from_dict(payload)

    def test_unknown_boat_is_rejected(self):
        payload = with_change(
            itineraries=[{**MINIMAL["itineraries"][0], "boat_id": "ghost"}]
        )
        with self.assertRaises(DatasetError):
            Dataset.from_dict(payload)

    def test_duplicate_departure_ids_are_rejected(self):
        payload = with_change(departures=[MINIMAL["departures"][0]] * 2)
        with self.assertRaises(DatasetError):
            Dataset.from_dict(payload)

    def test_backwards_dates_are_rejected(self):
        payload = with_change(
            departures=[{**MINIMAL["departures"][0], "end": "2027-04-01"}]
        )
        with self.assertRaises(DatasetError):
            Dataset.from_dict(payload)


class TestSeedDataset(unittest.TestCase):
    def setUp(self):
        self.dataset = Dataset.load(SEED)

    def test_seed_is_valid(self):
        self.assertGreater(len(self.dataset.departures), 0)

    def test_every_departure_falls_in_the_target_season(self):
        for departure in self.dataset.departures:
            self.assertEqual(departure.start.year, 2027)
            self.assertIn(departure.start.month, (5, 6, 7, 8))

    def test_seed_is_marked_unverified(self):
        """The banner depends on this; if it ever silently flips, the page lies."""
        self.assertFalse(self.dataset.is_fully_verified)

    def test_all_four_months_are_covered(self):
        months = {d.start.month for d in self.dataset.departures}
        self.assertEqual(months, {5, 6, 7, 8})


class TestPayload(unittest.TestCase):
    def setUp(self):
        self.payload = build_payload(Dataset.load(SEED))

    def test_facets_are_populated(self):
        for facet in ("routes", "levels", "themes", "months", "toggles"):
            self.assertTrue(self.payload["facets"][facet], f"{facet} is empty")

    def test_every_departure_resolves_to_an_itinerary(self):
        for departure in self.payload["departures"]:
            self.assertIn(departure["itinerary_id"], self.payload["itineraries"])

    def test_every_departure_has_a_base_fare_line(self):
        for departure in self.payload["departures"]:
            codes = [line["code"] for line in departure["lines"]]
            self.assertEqual(codes[0], "base_fare")

    def test_transparency_is_a_fraction(self):
        for departure in self.payload["departures"]:
            self.assertGreaterEqual(departure["transparency"], 0.0)
            self.assertLessEqual(departure["transparency"], 1.0)


class TestRender(unittest.TestCase):
    def test_site_is_self_contained(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = render(Dataset.load(SEED), tmp)
            html = target.read_text(encoding="utf-8")

        self.assertNotIn("/*STYLE*/", html)
        self.assertNotIn("/*APP*/", html)
        self.assertNotIn('"__DATA__"', html)

        # No external requests: the page must work from a file:// URL offline.
        for pattern in (r'src="https?://', r'href="https?://[^"]*\.css'):
            self.assertIsNone(re.search(pattern, html), f"external reference: {pattern}")

    def test_embedded_json_does_not_break_out_of_the_script_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            html = render(Dataset.load(SEED), tmp).read_text(encoding="utf-8")
        payload = html.split('<script id="payload" type="application/json">')[1]
        payload = payload.split("</script>")[0]
        self.assertGreater(len(json.loads(payload)["departures"]), 0)


class TestJsonLd(unittest.TestCase):
    HTML = """
    <html><head>
    <script type="application/ld+json">{"@type":"Product","name":"A Trip",
      "offers":{"@type":"Offer","price":"1200","priceCurrency":"EUR"}}</script>
    <script type="application/ld+json">{ this is not json }</script>
    </head><body></body></html>
    """

    def test_products_are_found(self):
        found = jsonld.of_type(self.HTML, "Product")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["name"], "A Trip")

    def test_malformed_block_does_not_lose_the_good_one(self):
        self.assertEqual(len(jsonld.extract_blocks(self.HTML)), 1)

    def test_offer_is_extracted(self):
        offer = jsonld.first_offer(jsonld.of_type(self.HTML, "Product")[0])
        self.assertEqual(offer["priceCurrency"], "EUR")


class TestRequirementExtraction(unittest.TestCase):
    def test_logged_dive_minimum_is_read(self):
        html = "<p>Divers need Advanced Open Water and a minimum of 50 logged dives.</p>"
        result = PadiComAdapter.extract_requirements(html)
        self.assertEqual(result["min_logged_dives"], 50)
        self.assertEqual(result["min_level"], "advanced")

    def test_drift_wording_sets_the_current_flag(self):
        html = "<p>Open Water certified. Expect strong currents on the offshore reefs.</p>"
        self.assertTrue(PadiComAdapter.extract_requirements(html)["strong_current"])

    def test_page_stating_nothing_yields_nothing(self):
        """Never invent a safety requirement that the source did not state."""
        self.assertIsNone(PadiComAdapter.extract_requirements("<p>A lovely boat.</p>"))


if __name__ == "__main__":
    unittest.main()
