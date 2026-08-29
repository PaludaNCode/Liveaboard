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
        for facet in ("months", "toggles"):
            self.assertTrue(self.payload["facets"][facet], f"{facet} is empty")

    def test_only_the_facets_the_page_renders_are_shipped(self):
        """routes, levels and themes were built for chips that no longer exist.

        app.js reads neither, so they were payload nobody rendered. Asserted
        rather than merely deleted, because the cheapest way for dead payload
        to come back is for nobody to notice it did.
        """
        self.assertEqual(set(self.payload["facets"]), {"months", "toggles"})

    def test_every_departure_resolves_to_an_itinerary(self):
        for departure in self.payload["departures"]:
            self.assertIn(departure["itinerary_id"], self.payload["itineraries"])

    def test_every_departure_has_a_base_fare_line(self):
        for departure in self.payload["departures"]:
            self.assertEqual(departure["base_line"]["code"], "base_fare")

    def test_fee_lines_live_on_the_itinerary(self):
        """The fees are the vessel's, so they are written once, not per sailing.

        878 departures across 314 itineraries meant each fee block shipped 2.8
        times over, and every byte of it reaches every visitor: the page is one
        self-contained file with no CDN to lazy-load a second request from.
        """
        for itinerary in self.payload["itineraries"].values():
            self.assertIn("lines", itinerary)
        # The seed's departures do not price anything differently, so none of
        # them needs its own copy.
        for departure in self.payload["departures"]:
            self.assertNotIn("lines", departure)

    def test_a_departure_that_prices_a_fee_itself_keeps_its_own_lines(self):
        """A departure-level fee replaces the route's, so it must not be shared.

        No sailing in the dataset does this today, but the model allows it, and
        reusing the itinerary's rows for one that did would publish the wrong
        bill on exactly the departure that differs.
        """
        raw = json.loads(SEED.read_text(encoding="utf-8"))
        target = raw["departures"][0]
        target["fees"] = [
            {
                "code": "fuel_surcharge",
                "tier": "mandatory",
                "basis": "per_trip",
                "amount": {"amount": 999.0, "currency": "EUR"},
                "provenance": {
                    "kind": "operator_stated",
                    "source_id": "liveaboard.com",
                    "retrieved": "2026-08-27",
                },
            }
        ]
        payload = build_payload(Dataset.from_dict(raw))

        own = [d for d in payload["departures"] if d["id"] == target["id"]]
        self.assertEqual(len(own), 1)
        self.assertIn("lines", own[0])
        fuel = [x for x in own[0]["lines"] if x["code"] == "fuel_surcharge"]
        self.assertEqual(fuel[0]["display"]["amount"], 999.0)

        # And its siblings on the same itinerary still share the route's.
        siblings = [
            d for d in payload["departures"]
            if d["itinerary_id"] == own[0]["itinerary_id"] and d["id"] != target["id"]
        ]
        self.assertTrue(siblings)
        for sibling in siblings:
            self.assertNotIn("lines", sibling)

    def test_the_page_grades_no_operators(self):
        """The site compares what trips cost; it does not score who sells them."""
        for departure in self.payload["departures"]:
            self.assertNotIn("transparency", departure)


class TestRender(unittest.TestCase):
    def test_site_is_self_contained(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = render(Dataset.load(SEED), tmp)
            html = target.read_text(encoding="utf-8")

        self.assertNotIn("/*STYLE*/", html)
        self.assertNotIn("/*APP*/", html)
        self.assertNotIn('"__DATA__"', html)


    def test_embedded_json_does_not_break_out_of_the_script_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            html = render(Dataset.load(SEED), tmp).read_text(encoding="utf-8")
        payload = html.split('<script id="payload" type="application/json">')[1]
        payload = payload.split("</script>")[0]
        self.assertGreater(len(json.loads(payload)["departures"]), 0)


# Every external host the page is allowed to reach. Empty, and meant to stay
# that way: one self-contained HTML file, no CDN.
#
# It had two entries until #59 -- a webfont stylesheet from Google and the host
# it pulled the fonts from. That was not merely untidy. The link sat in <head>
# where a stylesheet is render-blocking, so the table did not exist until the
# request resolved, and where the host is unreachable it hangs instead of
# failing fast: first row after 13.04s, against 0.58s once it was gone.
#
# Adding an entry here is adding a way for the page to be slow, or blank, on
# somebody else's network. It needs a better reason than convenience.
ALLOWED_EXTERNAL: frozenset[str] = frozenset()


# Any absolute URL in an attribute -- src, href, and the url() of a stylesheet.
EXTERNAL_REF = re.compile(r"""(?:src|href)\s*=\s*["'](https?://[^"']+)["']|url\(\s*["']?(https?://[^"')]+)""")


class TestNoUnexpectedExternalReferences(unittest.TestCase):
    """The page ships as one file; anything it fetches at runtime is a claim.

    CLAUDE.md: "the site stays one self-contained HTML file with no CDN."
    README: "CSS and JS inlined, no CDN."
    """

    def setUp(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.html = render(Dataset.load(SEED), tmp).read_text(encoding="utf-8")

    def _hosts(self) -> set[str]:
        found = set()
        for match in EXTERNAL_REF.finditer(self.html):
            url = match.group(1) or match.group(2)
            scheme, _, rest = url.partition("://")
            found.add(f"{scheme}://{rest.split('/')[0]}")
        return found

    def test_reaches_only_hosts_on_the_list(self):
        unexpected = self._hosts() - ALLOWED_EXTERNAL
        self.assertEqual(
            unexpected,
            set(),
            f"the page reaches {sorted(unexpected)}, which is not on ALLOWED_EXTERNAL. "
            f"One self-contained file with no CDN is an invariant: inline it, or add "
            f"the host deliberately and say why.",
        )

    def test_no_external_script_or_stylesheet_slips_through_as_a_query_url(self):
        """The shape that defeated the old check: an extensionless asset URL.

        Pinned as its own case because the failure was not "we forgot to test
        it" -- it was tested, with a pattern that could not match the thing.
        """
        css2 = re.findall(r'href="(https?://[^"]*css2\?[^"]*)"', self.html)
        for url in css2:
            host = url.split("/")[2]
            self.assertIn(
                f"https://{host}",
                ALLOWED_EXTERNAL,
                f"extensionless stylesheet from an unlisted host: {url}",
            )

    def test_the_page_still_works_with_nothing_external(self):
        """Everything the page needs to function must already be inline.

        The fonts are cosmetic and every stack falls back, so stripping every
        external reference must leave a page that still carries its own data,
        styles and behaviour.
        """
        stripped = EXTERNAL_REF.sub("", self.html)
        self.assertIn('<script id="payload"', stripped)
        # The real stylesheet and app are inlined, not linked.
        self.assertIn("font-family:", stripped)
        self.assertIn("function lineCounts", stripped)
        payload = stripped.split('<script id="payload" type="application/json">')[1]
        self.assertGreater(len(json.loads(payload.split("</script>")[0])["departures"]), 0)

    def test_every_allowed_host_is_actually_used(self):
        """A stale allowlist is how an exception outlives the thing it excused."""
        for host in ALLOWED_EXTERNAL:
            self.assertIn(host, self.html, f"{host} is allowlisted but unused; drop it")


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


class TestDefaultDataset(unittest.TestCase):
    """The default decided what got published, and it pointed at the seed."""

    def test_the_real_dataset_wins_when_it_exists(self):
        from liveaboard.cli import LIVE_DATA, default_data

        if not LIVE_DATA.exists():
            self.skipTest("no scraped dataset in this checkout")
        self.assertEqual(default_data(), LIVE_DATA)

    def test_the_seed_is_the_fallback_not_the_default(self):
        """A fresh checkout that has never scraped is the only time it is right."""
        import liveaboard.cli as cli

        original = cli.LIVE_DATA
        try:
            cli.LIVE_DATA = Path("data/definitely-not-here.json")
            self.assertEqual(cli.default_data(), cli.SEED_DATA)
        finally:
            cli.LIVE_DATA = original

    def test_the_deploy_names_its_dataset(self):
        """Relying on the default published five placeholder boats for hours."""
        workflow = (
            Path(__file__).resolve().parents[1] / ".github/workflows/pages.yml"
        ).read_text(encoding="utf-8")
        build = workflow.split("- name: Build", 1)[1]
        self.assertIn("--data", build.split("- uses:", 1)[0])


if __name__ == "__main__":
    unittest.main()


class TestTheSourceLinkFollowsTheDeparture(unittest.TestCase):
    """The listing link under a row must open that row's listing.

    The Source column used the *itinerary's* `source_url`, which is the vessel
    page at whichever month the crawl was reading when it found the boat -- 310
    of 315 itineraries point at August. So every row used one month's listing:
    881 of 881 departures linked to the wrong one, and a visitor checking a May
    price landed on the August page and saw a different number.
    """

    LIVE = ROOT / "data" / "egypt-2027.json"

    def test_the_column_prefers_the_departure_over_the_vessel_page(self):
        """Asserted on the built page, because this is a choice app.js makes.

        The same shape as the `lineCounts` check above: a rule that lives in
        the JS and has no Python to test, so the guard is that the built page
        still contains it.
        """
        with tempfile.TemporaryDirectory() as tmp:
            html = render(Dataset.load(SEED), tmp).read_text(encoding="utf-8")
        self.assertIn("d.booking_url || i.source_url", html)

    def test_every_linked_departure_names_its_own_month(self):
        """`?m=05/2027` on a May sailing.

        Run against the committed dataset rather than the seed: the seed
        publishes no booking urls, and this is a property of what the scrape
        collected. Both spellings occur in the wild -- `m=5/2027` from a vessel
        page, `m=05/2027` from a departure -- so the comparison is on the
        number, not the text.
        """
        if not self.LIVE.exists():
            self.skipTest("no scraped dataset in this checkout")
        payload = build_payload(Dataset.load(self.LIVE))
        checked = 0
        for departure in payload["departures"]:
            url = departure.get("booking_url") or ""
            match = re.search(r"[?&]m=(\d{1,2})/(\d{4})", url)
            if not match:
                continue
            checked += 1
            self.assertEqual(
                (int(match.group(2)), int(match.group(1))),
                (int(departure["start"][:4]), int(departure["start"][5:7])),
                f"{departure['id']} departs {departure['start']} and links to {url}",
            )
        self.assertTrue(checked, "no booking_url carried a month to check")


class TestTheFooterCountsMatchTheData(unittest.TestCase):
    """Numbers written into the page's prose, checked against the dataset.

    The footer said nitrox was "roughly half" included. It is two thirds --
    44 vessels against 21 -- and the sentence had simply been true once, before
    the fee scrape covered the whole fleet. It also advertised a state the
    column has never once rendered, "extra, no price", while omitting the one
    it does: "not listed", which is the case where nobody has said whether you
    will be charged at all.

    That is the failure this project exists to correct in other people, in our
    own words: a page confidently stating a figure that stopped being true. So
    the prose is now asserted rather than proof-read, the way `promote --check`
    asserts the dataset and `ALLOWED_EXTERNAL` asserts the page fetches nothing.
    """

    LIVE = ROOT / "data" / "egypt-2027.json"
    TEMPLATE = ROOT / "templates" / "index.html"

    def nitrox_by_vessel(self) -> dict[str, int]:
        """How many *boats* fall in each state. Nitrox is a vessel's policy."""
        payload = build_payload(Dataset.load(self.LIVE))
        state: dict[str, str] = {}
        for departure in payload["departures"]:
            itinerary = payload["itineraries"][departure["itinerary_id"]]
            lines = [departure["base_line"]] + (
                departure.get("lines") or itinerary["lines"]
            )
            line = next((x for x in lines if x.get("code") == "nitrox"), None)
            state[itinerary["boat"]] = (
                "absent" if line is None
                else "included" if line.get("included")
                else "priced" if line.get("has_price")
                else "listed_unpriced"
            )
        counts: dict[str, int] = {}
        for value in state.values():
            counts[value] = counts.get(value, 0) + 1
        return counts

    def test_the_stated_vessel_counts_are_the_real_ones(self):
        if not self.LIVE.exists():
            self.skipTest("no scraped dataset in this checkout")
        counts = self.nitrox_by_vessel()
        html = self.TEMPLATE.read_text(encoding="utf-8")
        for number, state in (
            (counts.get("included", 0), "included"),
            (counts.get("priced", 0), "publish a price"),
            (counts.get("absent", 0), "never mention it"),
        ):
            # assertTrue rather than assertIn: the failure message for a
            # missing substring prints the whole haystack, and the haystack
            # here is the entire page.
            self.assertTrue(
                f"<b>{number}</b>" in html,
                f"the footer does not say <b>{number}</b> vessels for "
                f"{state!r}; the committed dataset says it should",
            )

    def test_the_footer_names_only_states_the_column_can_reach(self):
        """It advertised "extra, no price", which no vessel has ever produced.

        Naming a state nobody will see is a smaller sin than the reverse and
        still a claim about the data that the data does not support.
        """
        if not self.LIVE.exists():
            self.skipTest("no scraped dataset in this checkout")
        html = self.TEMPLATE.read_text(encoding="utf-8")
        if not self.nitrox_by_vessel().get("listed_unpriced"):
            self.assertTrue(
                "extra, no price" not in html,
                "the footer offers 'extra, no price' as a nitrox state, and no "
                "vessel in the dataset produces it",
            )

    def test_the_unreachable_branch_is_still_in_the_renderer(self):
        """Unreached is not unnecessary.

        No vessel lists nitrox without a price today, but "Rental Gear" is
        routinely named with no figure, so the shape is real. Without the
        branch the function falls off the end and the cell prints "undefined".
        """
        app = (ROOT / "templates" / "app.js").read_text(encoding="utf-8")
        self.assertTrue(
            "extra, no price" in app,
            "app.js has lost the fallback branch; a nitrox line that is "
            "neither included nor priced would render as 'undefined'",
        )


class TestThePayloadShipsOnlyWhatThePageReads(unittest.TestCase):
    """Every byte here ships inside one HTML file, 838 times over.

    Five fields were serialised on every fee line and read by nothing:
    `charged` and `charged_max` (the browser sums `display` itself), `counted`
    (it re-decides that from the visitor's toggles), `basis` (already resolved
    to per-trip in Python) and `provenance` — a nested object with a URL in it.
    Dropping them took the payload from 2716 KB to 1328 and the page from
    2801 KB to 1501.

    Asserted both ways round: nothing dead comes back, and nothing the page
    actually reads goes missing.
    """

    LIVE = ROOT / "data" / "egypt-2027.json"
    APP = ROOT / "templates" / "app.js"

    NEVER_SHIPPED = ("charged", "charged_max", "counted", "basis", "provenance")

    def lines(self, live=False):
        """The live dataset where asked for: the seed prices no fee as a range,
        so `display_max` legitimately appears on none of its lines."""
        source = self.LIVE if live and self.LIVE.exists() else SEED
        payload = build_payload(Dataset.load(source))
        out = []
        for itinerary in payload["itineraries"].values():
            out.extend(itinerary.get("lines") or [])
        for departure in payload["departures"]:
            if departure.get("base_line"):
                out.append(departure["base_line"])
        return out

    def test_no_fee_line_carries_a_field_the_page_never_reads(self):
        app = self.APP.read_text(encoding="utf-8")
        for field in self.NEVER_SHIPPED:
            with self.subTest(field=field):
                self.assertNotIn(
                    "line." + field, app,
                    f"app.js reads {field}; it must then be shipped",
                )
                present = [x for x in self.lines() if field in x]
                self.assertFalse(
                    present,
                    f"{len(present)} fee lines still ship {field!r}, which "
                    f"app.js never reads",
                )

    def test_the_fields_the_page_does_read_are_all_present(self):
        """The other half of the guard. Trimming further has to fail here."""
        app = self.APP.read_text(encoding="utf-8")
        if not self.LIVE.exists():
            self.skipTest("no scraped dataset in this checkout")
        needed = [f for f in ("code", "label", "tier", "display", "display_max",
                              "has_price", "included", "toggle", "note", "fx")
                  if "line." + f in app or "." + f in app]
        lines = self.lines(live=True)
        for field in needed:
            with self.subTest(field=field):
                self.assertTrue(
                    any(field in x for x in lines),
                    f"app.js reads line.{field} and no line ships it",
                )

    def test_a_quote_already_in_euro_is_not_shipped_twice(self):
        """`quoted` was a byte-for-byte copy of `display` on the 96% of lines
        nobody converted. It ships only where the page prints it, which is
        where a conversion happened."""
        for line in self.lines():
            if "quoted" in line:
                self.assertTrue(
                    line.get("converted"),
                    f"{line['code']} ships a quote it will never print",
                )

    def test_nulls_and_falses_are_omitted_rather_than_serialised(self):
        for line in self.lines():
            for key, value in line.items():
                with self.subTest(code=line["code"], key=key):
                    self.assertIsNotNone(value, f"{key} ships as null")
                    self.assertIsNot(value, False, f"{key} ships as false")


class TestColumnOrders(unittest.TestCase):
    """Every column has a place at every width.

    app.js orders its columns from four lists -- one per breakpoint -- and
    appends anything missing from one, with a console.warn nobody sees unless
    they have devtools open. A column added to COLS and to the widest list only
    looks correct on the laptop it was written on and falls off the right-hand
    edge of a phone, which is the device most people open this page on.

    That is exactly what happened when the "vs PADI" column was added, and it
    reached production. A warning in a console is not a check; this is.
    """

    APP = ROOT / "templates" / "app.js"

    ORDERS = ("ORDER", "COMPACT_ORDER", "PHONE_ORDER", "TINY_ORDER")

    def source(self) -> str:
        return self.APP.read_text(encoding="utf-8")

    def column_keys(self) -> list[str]:
        """The `k` of every entry in COLS."""
        source = self.source()
        start = source.index("var COLS = [")
        end = source.index("\n  ];", start)
        return re.findall(r'\{\s*k:\s*"([^"]+)"', source[start:end])

    def order(self, name: str) -> list[str]:
        source = self.source()
        block = re.search(rf"var {name} = \[(.*?)\];", source, re.S)
        assert block, f"{name} not found in app.js"
        return re.findall(r'"([^"]+)"', block.group(1))

    def test_columns_were_found(self) -> None:
        """A guard on the guard: if COLS stops parsing, the rest passes
        vacuously and the check quietly stops checking."""
        keys = self.column_keys()
        self.assertGreater(len(keys), 10, keys)
        self.assertIn("total", keys)

    def test_every_column_is_placed_at_every_width(self) -> None:
        keys = set(self.column_keys())
        for name in self.ORDERS:
            missing = sorted(keys - set(self.order(name)))
            self.assertEqual(
                missing, [],
                f"{name} does not place {missing}; those columns print last, "
                f"which on a phone means off the right-hand edge",
            )

    def test_no_order_names_a_column_that_does_not_exist(self) -> None:
        """The other direction: a renamed or removed column leaves a dead entry
        that silently orders nothing."""
        keys = set(self.column_keys())
        for name in self.ORDERS:
            unknown = sorted(set(self.order(name)) - keys)
            self.assertEqual(unknown, [], f"{name} names columns that are gone: {unknown}")

    def test_the_money_is_never_last(self) -> None:
        """The Total off the right-hand edge is the failure the phone orders
        exist to prevent, and it would be silent: the row still renders."""
        for name in ("PHONE_ORDER", "TINY_ORDER"):
            order = self.order(name)
            self.assertLess(order.index("total"), order.index("trip"), name)


class TestPayloadIsRead(unittest.TestCase):
    """Every fact the page ships is a fact the page prints.

    The payload is the whole of what a visitor's browser is given, and it is
    written by `render.py` and read by `app.js` with nothing between them to
    say the two still agree. So a key can go on being serialised after the code
    that printed it is deleted, and the page looks finished while quietly
    publishing less than it holds.

    That is not hypothetical. `level_labels` and every itinerary's
    `requirements` shipped in the payload of eleven refreshes with no line of
    `app.js` reading either: 892 departures each carrying a stated entry
    requirement -- the certification and logged dives an operator will turn a
    diver away for -- reachable only by downloading the JSON. The column that
    printed it was removed for good reasons and nothing replaced it, which is
    the exact shape of failure this class exists to catch: not a wrong number,
    a fact silently withdrawn.

    Deliberately a check on the *keys*, not on the rendering. Whether the bar
    reads well is a matter of taste; whether anything reads it at all is not.
    """

    APP = ROOT / "templates" / "app.js"

    #: Payload keys with no reader, and the reason each is allowed to stay.
    #: Empty on purpose -- an entry here is a decision someone made out loud,
    #: and the point of the list is that it is short enough to argue with.
    UNREAD: dict[str, str] = {
        "fee_labels": "one vocabulary defined once; app.js prints each line's "
                      "own label, which is built from this table in Python",
    }

    def app(self) -> str:
        return self.APP.read_text(encoding="utf-8")

    def payload(self) -> dict:
        return build_payload(Dataset.load(SEED))

    def test_every_top_level_key_has_a_reader(self) -> None:
        source = self.app()
        for key in self.payload():
            if key in self.UNREAD:
                continue
            with self.subTest(key=key):
                self.assertIn(
                    key, source,
                    f"the payload ships {key!r} and app.js never reads it; "
                    f"either print it or stop serialising it",
                )

    def test_the_entry_bar_reaches_the_page(self) -> None:
        """The specific fact this class was written for.

        A stated safety requirement is the one kind of number here that is not
        about money, and it is the whole of what the second source was added
        for. It travels from the itinerary record through `level_labels` into
        the expanded row, and every step of that has to be present.
        """
        source, payload = self.app(), self.payload()
        self.assertIn("requirements", source, "app.js reads no entry bar")
        self.assertIn("level_labels", source, "app.js has no vocabulary for it")
        self.assertIn("min_level", source, "app.js reads no certification level")
        bars = [i for i in payload["itineraries"].values() if i.get("requirements")]
        self.assertTrue(bars, "the seed itself states no entry bar to print")

    def test_the_padi_column_explains_itself(self) -> None:
        """The one column whose heading a visitor cannot interpret.

        Every other column is named for what it holds, and each has a section
        in the footer. "vs PADI" is two words for a second seller, prints a
        dash wherever that seller does not sell the date, and read as missing
        data rather than as an absent quote -- so it says so, in the heading
        and in the footer, or this fails.
        """
        self.assertIn("hint:", self.app(), "no column carries an explanation")
        footer = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn("vs PADI", footer,
                      "the footer explains every column but this one")
        self.assertIn("Entry requirements", footer,
                      "the footer never says where the entry bar comes from")
