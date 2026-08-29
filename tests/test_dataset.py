"""Tests for dataset validation, the seed data, and the rendered payload."""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from urllib.parse import unquote
from datetime import datetime, timezone
from pathlib import Path

from liveaboard.dataset import Dataset, DatasetError
from liveaboard.render import build_payload, icon_data_uri, render
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
        self.assertNotIn("__ICON__", html)


class TestIcon(unittest.TestCase):
    """The favicon is part of the page, not a request the page makes."""

    def setUp(self):
        self.svg = (ROOT / "templates" / "icon.svg").read_text(encoding="utf-8")

    def test_the_icon_ships_inside_the_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            html = render(Dataset.load(SEED), tmp).read_text(encoding="utf-8")
        self.assertIn('<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,', html)

    def test_every_hash_is_escaped(self):
        """An unescaped "#" starts a fragment, and the icon stops there.

        Every colour in the file begins with one, so this is the failure that
        renders a blank tab while the markup still looks right.
        """
        uri = icon_data_uri(self.svg)
        self.assertNotIn("#", uri)
        self.assertIn("%23", uri)

    def test_both_themes_travel_in_the_one_file(self):
        """One file, two themes: the dark palette is inside the icon itself."""
        svg = unquote(icon_data_uri(self.svg).split(",", 1)[1])
        self.assertIn("prefers-color-scheme:dark", svg)
        self.assertIn("#0d5c8c", svg)
        self.assertIn("#63b3e3", svg)

    def test_comments_do_not_ship(self):
        """The file is commented for a reader; the attribute is served 878 times."""
        self.assertIn("<!--", self.svg)
        self.assertNotIn("%3C!--", icon_data_uri(self.svg))


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

    def test_the_second_seller_is_reachable_and_explained(self) -> None:
        """A second seller prices these rows, so a reader can get to it.

        There is no Sellers column any more: it named which end of the price
        span was whose, which the expanded row already says under each
        seller's name. What a column cannot replace is the *link* — the money
        columns can price a row on PADI's bill, and somebody checking that
        figure needs the page it came from. So the Source column carries both
        where both sell the sailing, and the footer says so.

        The link is per boat and printed only where PADI prices that date: a
        vessel having a PADI page says nothing about whether one sailing is on
        its calendar.
        """
        source = self.app()
        self.assertIn("padi_urls", source,
                      "the page has no way to link the other seller")
        self.assertIn("PADI ↗", source, "the Seller column never names PADI")

        footer = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn("<h3>Sellers</h3>", footer,
                      "the footer never explains the second seller")
        self.assertIn("<b>Seller</b>", footer,
                      "the footer never says where the second seller is linked")
        self.assertIn("Entry requirements", footer,
                      "the footer never says where the entry bar comes from")

    def test_no_column_is_left_unexplained_by_the_footer(self) -> None:
        """Every column heading a visitor cannot read off its own name has a
        footer section. Removing a column must take its explanation with it, or
        the footer documents something nobody can find."""
        footer = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        headings = set(re.findall(r"<h3>([^<]+)</h3>", footer))
        source = self.app()
        titles = set(re.findall(r'\bt: "([^"]+)"', source))
        for heading in ("Places", "Disclosure", "Per dive"):
            self.assertIn(heading, headings, f"the footer dropped {heading}")
            self.assertIn(heading, titles, f"{heading} is documented but gone")

    def test_a_row_prints_one_seller_and_not_a_blend(self) -> None:
        """Advertised plus Mandatory fees is the Total, on every row.

        The Total can now be the second seller's, and the three money columns
        beside it have to follow it there. Left reading our own source, a row
        won by PADI printed our berth price against PADI's total and the
        arithmetic across the row was simply wrong -- silently, since every
        figure in it is real.
        """
        source = self.app()
        for key in ('k: "base"', 'k: "later"', 'k: "total"'):
            start = source.index(key)
            block = source[start:start + 900]
            with self.subTest(key):
                self.assertIn("best(row)", block,
                              f"{key} does not follow the bill the row prints")

    def test_the_same_price_threshold_is_one_number(self) -> None:
        """Two sellers agreeing is decided in two places -- the column and the
        sentence in the expanded row -- and they have to agree with each other.

        Written as a literal in both, they read the same only until someone
        widens one of them, and the failure is silent: the table says the
        prices are the same while the panel underneath prints a difference.
        """
        source = self.app()
        self.assertIn("var PADI_SAME = ", source, "the threshold has no name")
        # Two places decide it: `best`, which the Total and Sellers columns
        # both read, and the sentence in the expanded row.
        self.assertGreaterEqual(source.count("PADI_SAME"), 3, "declared and unused")
        self.assertIsNone(
            re.search(r"Math\.abs\((?:gap|diff)\)\s*<\s*\d", source),
            "a bare number decides whether two sellers agree; use PADI_SAME "
            "so the column and the panel cannot disagree",
        )


class TestBothSellersSpan(unittest.TestCase):
    """Each end of a printed price span is one seller's whole bill.

    The money columns print `lo-hi` across the two sellers, and Advertised plus
    Mandatory fees has to equal Total at *both* ends. The obvious way to build
    the span breaks that: take the minimum of each component and the maximum of
    each, independently. Our berth is sometimes the cheaper while their fee book
    is, so min(base) + min(fees) is then a bill neither seller quoted -- a
    number this site invented, on a page whose whole argument is that invented
    numbers are the problem.

    Measured before it was believed. The independent-minima version broke the
    sum on 74 of the 108 rows where both sellers' bills add up; naming one
    seller per end fixes all 874 priced rows, single-seller rows included.

    Numbers cannot be checked from Python without a second adder that would
    drift from `metricsOf`, so this checks the shape instead: every endpoint in
    a `best()` return must read its fields off *one* object. That is the
    property, and it is the one an editor can lose without any test noticing.
    """

    APP = ROOT / "templates" / "app.js"

    #: `total`/`base`/`later` for a floor, `totalMax`/`base`/`totalMax - base`
    #: for a ceiling. Both add up given `metricsOf`'s `later = total - base`,
    #: which `test_later_is_total_minus_base` pins.
    SOUND = {
        ("S.total", "S.base", "S.later"),
        ("S.totalMax", "S.base", "S.totalMax - S.base"),
    }

    def best_returns(self) -> list[str]:
        source = self.APP.read_text(encoding="utf-8")
        start = source.index("function best(row) {")
        end = source.index("\n  }", start)
        body = source[start:end]
        body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
        returns = re.findall(r"return \{(.*?)\};", body, re.S)
        self.assertEqual(len(returns), 2, "best() no longer has two returns")
        return returns

    @staticmethod
    def field(block: str, name: str) -> str:
        match = re.search(rf"\b{name}:\s*([^,\n}}]+)", block)
        assert match, f"{name} missing from a best() return"
        return " ".join(match.group(1).split())

    def ends(self, block: str) -> list[tuple[str, str, str]]:
        return [
            (self.field(block, "lo"), self.field(block, "baseLo"),
             self.field(block, "laterLo")),
            (self.field(block, "hi"), self.field(block, "baseHi"),
             self.field(block, "laterHi")),
        ]

    def test_each_end_reads_one_seller(self) -> None:
        for block in self.best_returns():
            for end in self.ends(block):
                names = {re.match(r"\w+", part).group(0)
                         for expr in end for part in expr.split() if re.match(r"\w+", part)}
                self.assertEqual(
                    len(names), 1,
                    f"an endpoint is spliced from {sorted(names)}; each end of "
                    f"the span must be one seller's whole bill",
                )

    def test_each_end_adds_up(self) -> None:
        """Advertised + Mandatory fees = Total, at the floor and the ceiling."""
        for block in self.best_returns():
            for end in self.ends(block):
                seller = re.match(r"\w+", end[0]).group(0)
                shape = tuple(expr.replace(seller + ".", "S.") for expr in end)
                self.assertIn(
                    shape, self.SOUND,
                    f"{shape} is not a form where Advertised + Mandatory "
                    f"equals Total",
                )

    def test_later_is_total_minus_base(self) -> None:
        """The algebra above rests on this one line of `metricsOf`."""
        source = self.APP.read_text(encoding="utf-8")
        self.assertRegex(source, r"\blater:\s*low - base\b")

    def test_the_span_collapses_when_the_ends_round_alike(self) -> None:
        """A pair that prints identically must not print as a range:
        "1,757-1,757" reads as a spread that is not there. Both printers round
        before they compare, because the rounding is what makes them equal."""
        source = self.APP.read_text(encoding="utf-8")
        for name in ("sellerSpan", "sellerPair"):
            body = re.search(rf"function {name}\(lo, hi\) \{{(.*?)\n  \}}", source, re.S)
            self.assertIsNotNone(body, f"{name} is gone")
            self.assertRegex(body.group(1), r"Math\.round\(lo\)[\s\S]*Math\.round\(hi\)", name)
            self.assertRegex(body.group(1), r"if \(\w+ === \w+\) return", name)

    def test_the_component_columns_never_sort_their_pair(self) -> None:
        """Advertised and Mandatory fees print the low-total seller's figure
        then the high-total seller's, and that order is what makes the row add
        up. On 27 of the 108 both-seller rows the pair runs backwards -- the
        seller with the cheaper total advertises the dearer berth -- and
        sorting it to look tidy would break Advertised + Mandatory = Total.

        So those two columns use `sellerPair`, whose separator is an arrow
        rather than an en dash, and the Total keeps `sellerSpan`. Swapping one
        for the other is a silent change: both take the same two arguments and
        both render.
        """
        source = self.APP.read_text(encoding="utf-8")
        for lo, hi, printer in (("baseLo", "baseHi", "sellerPair"),
                                ("laterLo", "laterHi", "sellerPair"),
                                ("lo", "hi", "sellerSpan")):
            self.assertRegex(
                source, rf"{printer}\(b\.{lo}, b\.{hi}\)",
                f"b.{lo}/b.{hi} is no longer printed by {printer}",
            )
        pair = re.search(r"function sellerPair\(lo, hi\) \{(.*?)\n  \}", source, re.S)
        self.assertNotIn("Math.min", pair.group(1))
        self.assertNotIn("Math.max", pair.group(1))


class TestEveryDataCommitReachesThePage(unittest.TestCase):
    """A workflow that commits the published files must trigger the deploy.

    GitHub deliberately does not start a workflow from a push made with the
    default ``GITHUB_TOKEN``, so `pages.yml`'s `push` trigger never sees a
    scheduled job's data commit. `workflow_run` is the only trigger that does,
    and it names the workflows explicitly -- which means the list goes stale
    every time one is added.

    It fails silently and in the worst direction: the commit lands on main, the
    repository says the site was updated, and the published page keeps serving
    an older build. It has already happened once, to three refreshes in a row,
    and again to three workflows at once. Hence a test rather than a comment.
    """

    WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
    PUBLISHED = ("data", "site")

    def commits_published_files(self, text: str) -> bool:
        """Whether the workflow git-adds anything the page is built from."""
        for line in re.findall(r"^\s*git add\s+(.*)$", text, re.M):
            for path in re.findall(r"[\w./-]+", line):
                if path.split("/")[0] in self.PUBLISHED:
                    return True
        return False

    def test_the_deploy_watches_every_workflow_that_writes_the_site(self):
        pages = (self.WORKFLOWS / "pages.yml").read_text(encoding="utf-8")
        block = re.search(r"workflow_run:(.*?)^  \w", pages, re.S | re.M)
        self.assertIsNotNone(block, "pages.yml has no workflow_run trigger")
        watched = set(re.findall(r'"([^"]+)"', block.group(1)))

        missing = []
        for path in sorted(self.WORKFLOWS.glob("*.yml")):
            if path.name == "pages.yml":
                continue
            text = path.read_text(encoding="utf-8")
            if not self.commits_published_files(text):
                continue
            name = re.search(r"^name:\s*(.+)$", text, re.M)
            self.assertIsNotNone(name, f"{path.name} has no name:")
            if name.group(1).strip() not in watched:
                missing.append(f"{name.group(1).strip()} ({path.name})")

        self.assertEqual(
            missing, [],
            "these workflows commit data/ or site/ but do not trigger the "
            "deploy, so their commits land on main and the published page "
            f"keeps serving an older build: {missing}",
        )

    def test_the_watch_list_names_no_workflow_that_does_not_exist(self):
        """The other direction: a renamed workflow leaves a dead entry that
        watches nothing, which looks exactly like a watch that works."""
        pages = (self.WORKFLOWS / "pages.yml").read_text(encoding="utf-8")
        block = re.search(r"workflow_run:(.*?)^  \w", pages, re.S | re.M)
        watched = set(re.findall(r'"([^"]+)"', block.group(1)))
        names = {
            re.search(r"^name:\s*(.+)$", p.read_text(encoding="utf-8"), re.M).group(1).strip()
            for p in self.WORKFLOWS.glob("*.yml")
        }
        self.assertEqual(sorted(watched - names), [],
                         "pages.yml watches workflows that no longer exist")


class TestTheMethodPanelCanBeClosedFromAnywhere(unittest.TestCase):
    """The footer scrolls inside itself, so its own heading must stay reachable.

    `.site-footer` is `max-height:52vh; overflow:auto` — a scroll box, not a
    page section. Without a pinned summary the only control that closes it
    scrolls away with the first paragraph, and shutting it again means
    scrolling back through everything you just read.

    Both halves are asserted because either alone is useless: the sticky rule
    without an opaque background has the text sliding through the heading, and
    the sticky heading without the toggle handler leaves the panel holding a
    stale scroll position and the reader in blank space under the table.
    """

    CSS = Path(__file__).resolve().parents[1] / "templates" / "style.css"
    APP = Path(__file__).resolve().parents[1] / "templates" / "app.js"

    def summary_rule(self) -> str:
        css = self.CSS.read_text(encoding="utf-8")
        match = re.search(r"\.site-footer > summary \{(.*?)\}", css, re.S)
        self.assertIsNotNone(match, "the footer summary has no rule of its own")
        return match.group(1)

    def test_the_panel_is_its_own_scroll_box(self):
        """The premise. If this stops being true the sticky heading is
        pointless and the toggle handler is resetting nothing."""
        css = self.CSS.read_text(encoding="utf-8")
        rule = re.search(r"\.site-footer \{(.*?)\}", css, re.S)
        self.assertIsNotNone(rule)
        self.assertIn("overflow:auto", rule.group(1).replace(" ", ""))

    def test_the_heading_is_pinned(self):
        rule = self.summary_rule().replace(" ", "")
        self.assertIn("position:sticky", rule)
        self.assertIn("top:0", rule)

    def test_the_pinned_heading_is_opaque(self):
        """A sticky element without its own background has the content
        scrolling visibly through it — the panel's background is on the box
        behind, not on the heading."""
        self.assertIn("background", self.summary_rule())

    def test_closing_it_resets_the_panel_and_returns_to_the_heading(self):
        source = self.APP.read_text(encoding="utf-8")
        self.assertIn('.site-footer', source, "app.js never finds the panel")
        self.assertIn("scrollTop = 0", source,
                      "closing leaves a stale scroll position inside the panel")
        self.assertIn("scrollIntoView", source,
                      "closing can leave the reader in blank space below the table")


class TestTheSellerFilter(unittest.TestCase):
    """Who sells a sailing is filterable, and the text box is gone.

    The Sellers *column* was removed when Seller took over linking both sites,
    which left 230 PADI-only rows with no way to be found: nothing in the
    toolbar asked the question and the search box could not either, because
    "who sells this" is not a word in any of the fields it searched.

    The box itself went with it. It was a second way to ask what the chips ask,
    redrawing the table on every keystroke, and the only question it answered
    alone was the operating company -- which the boat bank already answers
    better, a company with six boats being six boats' worth of rows.
    """

    APP = ROOT / "templates" / "app.js"
    PAGE = ROOT / "templates" / "index.html"

    def app(self) -> str:
        return self.APP.read_text(encoding="utf-8")

    def page(self) -> str:
        return self.PAGE.read_text(encoding="utf-8")

    def test_the_text_filter_is_gone_from_both_sides(self) -> None:
        """Half a removal is worse than none: an input with no handler filters
        nothing and says otherwise."""
        self.assertNotIn('id="q"', self.page(), "the search input is still in the page")
        self.assertNotIn("state.q", self.app(), "app.js still reads a text query")
        self.assertNotIn("debounce", self.app(),
                         "debounce outlived its only caller")

    def test_the_bank_exists_on_both_sides(self) -> None:
        self.assertIn('id="sellers"', self.page())
        self.assertIn('chips("sellers"', self.app())

    def test_reset_clears_it(self) -> None:
        """A filter Reset does not reach is one a visitor cannot get out of
        without reloading."""
        app = self.app()
        self.assertIn("state.sellers.clear()", app)
        self.assertRegex(app, r'"boats",\s*"sellers"\]',
                         "the seller bank is never repainted on reset")

    def test_the_three_states_partition_every_row(self) -> None:
        """Three chips, three facts, and no row in two of them or none.

        Read off the same two keys the Seller column branches on -- a second
        derivation of "who sells this" would be a second answer, and the chip
        counts would drift from the links beside them.
        """
        if not (ROOT / "data" / "egypt-2027.json").exists():
            self.skipTest("no dataset in this checkout")
        payload = build_payload(Dataset.load(ROOT / "data" / "egypt-2027.json"))
        counts = {"both": 0, "here": 0, "padi": 0}
        for d in payload["departures"]:
            counts["padi" if d.get("padi_only")
                   else "both" if d.get("padi") is not None
                   else "here"] += 1
        self.assertEqual(sum(counts.values()), len(payload["departures"]))
        for state, n in counts.items():
            self.assertGreater(n, 0, f"the {state!r} chip would render with no rows")

    def test_the_column_is_named_for_what_it_holds(self) -> None:
        """It stopped being one source the day it started linking two."""
        self.assertIn('{ k: "source", t: "Seller",', self.app())


class TestTheBuiltStampIsTheBuild(unittest.TestCase):
    """The toolbar's "built" is the build, to the minute.

    It printed `meta.generated` -- the day the *data* was scraped -- under the
    word "built". Two different facts under one label, and the one it showed
    was not the one it named: they diverge whenever a parser or template change
    ships without a fresh crawl, which is most of them.

    Minutes because the page is rebuilt several times an hour on a busy day and
    a date alone cannot tell two of those apart, which is the whole question
    somebody reading that line is asking.
    """

    def test_the_payload_carries_a_build_stamp(self) -> None:
        if not (ROOT / "data" / "egypt-2027.json").exists():
            self.skipTest("no dataset in this checkout")
        meta = build_payload(Dataset.load(ROOT / "data" / "egypt-2027.json"))["meta"]
        self.assertRegex(meta["built"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC$")

    def test_it_is_not_the_crawl_date_wearing_a_new_name(self) -> None:
        meta = build_payload(Dataset.load(ROOT / "data" / "egypt-2027.json"))["meta"]
        self.assertNotEqual(meta["built"], meta["generated"])
        self.assertRegex(meta["generated"], r"^\d{4}-\d{2}-\d{2}$",
                         "the crawl date must stay a date: it is a day, not a moment")

    def test_the_toolbar_prints_the_build(self) -> None:
        app = (ROOT / "templates" / "app.js").read_text(encoding="utf-8")
        self.assertIn('" · built " + (D.meta.built || D.meta.generated)', app)
