"""Tests for dataset validation, the seed data, and the rendered payload."""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from liveaboard.dataset import Dataset, DatasetError
from liveaboard.render import build_payload, render
from liveaboard.scrape import jsonld, liveaboard_com
from liveaboard.scrape.base import FetchResult
from liveaboard.scrape.diagnose import describe
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


class TestSearchPaths(unittest.TestCase):
    def test_one_path_per_season_month(self):
        paths = liveaboard_com.search_paths()
        self.assertEqual(len(paths), len(liveaboard_com.SEASON_MONTHS))

    def test_paths_match_the_published_url_shape(self):
        self.assertIn("/diving/search/egypt/may/2027", liveaboard_com.search_paths())
        self.assertIn("/diving/search/egypt/august/2027", liveaboard_com.search_paths())

    def test_boat_links_match_relative_and_absolute(self):
        """The first attempt anchored on a literal prefix and matched nothing live."""
        html = (
            '<a href="/diving/egypt/blue-horizon">a</a>'
            '<a href="https://www.liveaboard.com/diving/egypt/sea-serpent">b</a>'
        )
        found = {m.group(1) for m in liveaboard_com.BOAT_LINK.finditer(html)}
        self.assertEqual(
            found, {"/diving/egypt/blue-horizon", "/diving/egypt/sea-serpent"}
        )


class TestDiagnose(unittest.TestCase):
    HTML = (
        "<html><head><title>Egypt May 2027</title>"
        '<script type="application/ld+json">{"@type":"Product",'
        '"offers":{"@type":"Offer","price":"1200","priceCurrency":"EUR"}}</script>'
        '</head><body><a href="/diving/egypt/a-boat">x</a>'
        "<span>From &euro;1,335</span></body></html>"
    )

    def setUp(self):
        self.result = FetchResult(
            url="https://example.test/x",
            status=200,
            body=self.HTML,
            fetched_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
        )

    def test_reports_structured_data_types(self):
        self.assertIn("Product", describe(self.result))

    def test_reports_link_shapes(self):
        self.assertIn("/diving/egypt/*", describe(self.result))

    def test_flags_a_client_rendered_page(self):
        """No links at all is the signature of a listing built in the browser."""
        bare = FetchResult(
            url="https://example.test/y",
            status=200,
            body="<html><body><div id=root></div></body></html>",
            fetched_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
        )
        self.assertIn("client-side", describe(bare))


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


class TestFxHonesty(unittest.TestCase):
    """Every euro figure on the page rests on one rate."""

    def table(self, source):
        from liveaboard.money import FxTable

        return FxTable.from_dict(
            {"display_currency": "EUR", "as_of": "2026-08-27",
             "source": source, "rates": {"USD": 0.92}}
        )

    def test_the_shipped_default_admits_it_is_a_placeholder(self):
        from liveaboard.promote import _default_fx
        from liveaboard.money import FxTable

        self.assertFalse(FxTable.from_dict(_default_fx()).is_sourced)

    def test_a_named_source_counts_as_sourced(self):
        self.assertTrue(
            self.table("European Central Bank daily reference rate").is_sourced
        )

    def test_the_ways_a_table_admits_it_is_a_stand_in(self):
        for source in ("placeholder", "unknown", "TODO: real rates",
                       "example rate", "stand-in"):
            self.assertFalse(self.table(source).is_sourced, source)

    def test_the_page_is_told_whether_the_rate_is_sourced(self):
        import json
        from liveaboard.dataset import Dataset
        from liveaboard.render import build_payload

        payload = build_payload(Dataset.from_dict(json.loads(
            (Path(__file__).resolve().parents[1] / "data/seed/egypt-2027.json")
            .read_text(encoding="utf-8")
        )))
        self.assertIn("sourced", payload["meta"]["fx"])


if __name__ == "__main__":
    unittest.main()
