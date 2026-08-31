"""Tests for dataset validation, the seed data, and the rendered payload."""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from html import unescape
from urllib.parse import unquote
from datetime import datetime, timezone
from pathlib import Path

from liveaboard.dataset import Dataset, DatasetError
from liveaboard.export import latest_entry, recent_entries
from liveaboard.render import (
    HISTORY_DAYS,
    build_payload,
    icon_data_uri,
    render,
)
from liveaboard.scrape import jsonld, liveaboard_com
from liveaboard.scrape.base import FetchResult
from liveaboard.scrape.diagnose import describe
from liveaboard.scrape.padi_com import PadiComAdapter

import published

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


class TestThePublicationGateIsComplete(unittest.TestCase):
    """No test may open a fetched file for itself; that is what locks a fetcher out.

    `cabins.yml` runs the suite before it fetches, so an assertion about
    committed data sitting outside the gate can stop the only job able to
    refresh that data — which is exactly what happened on 2026-08-30 and left
    the pipeline circling. `tests/published.py` is the one door in, and this is
    what keeps it the only one.

    A convention would not do. The failure it guards against is a *new* test
    written next month, by somebody with no reason to know any of this, that
    reads `data/egypt-2027.json` because that is the obvious thing to do.

    Textual rather than a walk of the syntax tree, because the thing being
    forbidden is a path spelled out in source, and the two spellings of one are
    easy to state. Prose is not matched: a docstring naming `data/fees.json` in
    backticks carries no quote before `data/`, which is what the patterns
    require.
    """

    PATH_LITERAL = re.compile(r"""["']data/(?P<name>[\w.-]+)["']""")
    PATH_JOINED = re.compile(r"""["']data["']\s*/\s*["'](?P<name>[\w.-]+)["']""")

    def test_no_test_opens_a_fetched_file_for_itself(self):
        found: list[str] = []
        for path in sorted((ROOT / "tests").glob("test_*.py")):
            body = path.read_text(encoding="utf-8")
            for pattern in (self.PATH_LITERAL, self.PATH_JOINED):
                for match in pattern.finditer(body):
                    if match.group("name") not in published.PUBLISHED:
                        continue
                    line = body[: match.start()].count("\n") + 1
                    found.append(f"{path.name}:{line} {match.group(0)}")
        self.assertEqual(
            found, [],
            "these reach into data/ directly; go through tests/published.py so "
            "the assertion gates the commit rather than the fetch:\n  "
            + "\n  ".join(found),
        )

    def test_the_gate_is_opted_into_and_never_out_of(self):
        """A flag that must be remembered to get the *full* suite is a
        publication gate that quietly stops running. `LIVEABOARD_TESTS=code` is
        opted into, by the four jobs that fetch, and by nothing else."""
        self.assertFalse(published.code_only({}))
        self.assertFalse(published.code_only({published.GATE: ""}))
        self.assertFalse(published.code_only({published.GATE: "all"}))
        self.assertTrue(published.code_only({published.GATE: "code"}))


class TestThePageAnnouncesTheNewsInTheCommitThatMakesIt(unittest.TestCase):
    """The changelog panel must be the newest entry in `data/CHANGES.md`.

    The page embeds that entry, and both data workflows **built the page before
    appending to the file** — so the commit that produced the news shipped a
    panel still showing the previous refresh's. It self-corrected on the next
    rebuild, so nothing published was ever wrong; it was, on exactly the commit
    that mattered, one refresh behind in saying so. On a site whose argument is
    that published figures should be current, that is the wrong way round.

    Asserted on the committed pair rather than on the order of steps in a YAML
    file, because the property is what matters and a step order is only one way
    to break it.
    """

    def test_the_committed_page_leads_with_the_committed_latest_entry(self):
        log = published.committed("CHANGES.md").read_text(encoding="utf-8")
        newest = latest_entry(log)
        if not newest.strip():
            self.skipTest("no entry in data/CHANGES.md yet")
        page = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
        panels = re.findall(r'<pre class="changelog">(.*?)</pre>', page, re.S)
        self.assertTrue(panels, "the built page has no changelog panel")
        # The view carries a week of refreshes (#140), newest first, so the
        # property is about the *first* block rather than the only one.
        self.assertEqual(
            unescape(panels[0]).strip(), newest.strip(),
            "the published page's changelog does not lead with the newest entry "
            "in data/CHANGES.md -- something built the page before appending to it")

    def test_the_committed_page_carries_the_whole_window(self):
        """Not just the newest: a week of refreshes is the view's default, and
        a page carrying one of them is the bug this replaced."""
        log = published.committed("CHANGES.md").read_text(encoding="utf-8")
        window = recent_entries(log, HISTORY_DAYS)
        if not window:
            self.skipTest("no entry in data/CHANGES.md yet")
        page = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
        panels = [unescape(b).strip() for b in
                  re.findall(r'<pre class="changelog">(.*?)</pre>', page, re.S)]
        self.assertEqual(panels, [body.strip() for _, body in window])


class TestARebuildIsNotNews(unittest.TestCase):
    """The publish action must normalise the same stamp `render` writes.

    `cli build` stamps the page with the minute it ran — deliberately, so two
    builds an hour apart can be told apart — which means `site/index.html`
    differs on every run whether or not any data did. So the "nothing to
    commit" exit could never fire, and seven data jobs a day committed seven
    times a day regardless: a line in `git log --oneline data/` that moved no
    price, and a deploy that published nothing new. Three of fourteen commits
    on `main` were that.

    `.github/actions/publish` now treats a page differing *only* by that stamp
    as nothing to say. The two are coupled by the literal JSON key, and
    silently: rename it in `render` and the action's normalisation stops
    matching, the check stops firing, and the no-op commits come back with
    nothing failing. Same shape as `pricing._is_counted` and `lineCounts`, and
    pinned for the same reason.
    """

    #: The shell moved out of `action.yml` into a file of its own so the
    #: rebase branch could be tested (#127). This guard followed it, and reads
    #: both: the script for the logic, the action for the wiring that reaches
    #: it. Keying on only one of the two is how a check ends up looking at a
    #: file the behaviour no longer lives in.
    ACTION = ROOT / ".github" / "actions" / "publish" / "action.yml"
    SCRIPT = ROOT / ".github" / "actions" / "publish" / "push.sh"

    def test_the_action_actually_runs_the_script(self):
        """Otherwise everything below inspects a file nothing executes."""
        self.assertTrue(self.SCRIPT.exists(), "push.sh is gone")
        self.assertIn("push.sh", self.ACTION.read_text(encoding="utf-8"),
                      "the action no longer runs push.sh, so the checks below "
                      "are reading a script that never runs")

    def test_the_page_carries_a_build_stamp_under_the_key_the_action_strips(self):
        key = re.search(r'"(\w+)": datetime\.now', (ROOT / "src" / "liveaboard"
                        / "render.py").read_text(encoding="utf-8"))
        self.assertIsNotNone(key, "render.py no longer stamps the payload")
        # assertTrue rather than assertIn: a missing substring prints the
        # whole haystack, and the haystack is the entire action.
        self.assertTrue(
            f'"{key.group(1)}":' in self.SCRIPT.read_text(encoding="utf-8"),
            f"render stamps {key.group(1)!r} and the publish action does not "
            f"normalise it; every rebuild would commit again, silently")

    def test_the_action_still_has_the_check(self):
        body = self.SCRIPT.read_text(encoding="utf-8")
        self.assertTrue("site/index.html" in body, "the stamp check is gone")
        self.assertTrue("exclude" in body,
                        "the check must ignore the page when deciding whether "
                        "anything else was staged")


class TestEveryPushingWorkflowChecksItself(unittest.TestCase):
    """A job that pushes is the only CI its own commit will ever get.

    GitHub does not trigger workflows on pushes made with the default
    `GITHUB_TOKEN` -- the standard guard against a job triggering itself -- and
    every scheduled job here pushes with exactly that token. So no scheduled
    data commit has ever had a CI run against it, and on 2026-08-30 that let
    the daily refresh publish 36 sailings advertising a berth nobody could buy
    and leave `main` red for about seven hours. The only thing that noticed was
    a person opening an unrelated pull request.

    So a workflow that pushes must run CI's own list before it does, and it
    must be *the* list rather than a copy: `.github/actions/checks` is used by
    `ci.yml` too, which is what makes "the same bar" a fact instead of an
    intention.

    Textual, because the alternative is a YAML parser and this project has no
    runtime dependencies to spend one on.
    """

    WORKFLOWS = ROOT / ".github" / "workflows"
    CHECKS = "uses: ./.github/actions/checks"
    PUBLISH = "uses: ./.github/actions/publish"
    #: The serialised publish job every data workflow now delegates its tail
    #: to. A caller reaches the push through this rather than containing it,
    #: which is the third spelling this guard has had to learn -- first inline
    #: `git push origin`, then the shared action, now the shared workflow.
    DELEGATE = "uses: ./.github/workflows/publish.yml"

    def test_the_shared_actions_exist(self):
        for name in ("checks", "publish"):
            self.assertTrue((ROOT / ".github" / "actions" / name / "action.yml").exists(), name)

    def test_ci_runs_the_shared_list_rather_than_its_own(self):
        """Otherwise the two drift, and the data jobs are running yesterday's
        idea of what a commit has to satisfy."""
        self.assertIn(self.CHECKS, (self.WORKFLOWS / "ci.yml").read_text(encoding="utf-8"))

    def test_the_guard_can_still_see_a_push(self):
        """This test nearly stopped working the day the push moved.

        It keyed on the string `git push origin`, and #123 factored that into
        `.github/actions/publish` — after which no workflow contained it, every
        workflow trivially passed, and the guard was green because it had
        nothing left to look at. A check that silently stops checking is worse
        than no check, so the thing it looks for is asserted to exist.
        """
        pushing = [p.name for p in sorted(self.WORKFLOWS.glob("*.yml"))
                   if self._pushes(p.read_text(encoding="utf-8"))]
        self.assertGreaterEqual(
            len(pushing), 5,
            "no workflow appears to push; has the mechanism moved again?")

    @staticmethod
    def _pushes(body: str) -> bool:
        """Whether a workflow commits to the repository, however it does it."""
        cls = TestEveryPushingWorkflowChecksItself
        return ("git push origin" in body
                or cls.PUBLISH in body
                or cls.DELEGATE in body)

    def test_only_the_shared_action_carries_the_push(self):
        """One definition of the rebase-and-retry. It is subtle — `-X theirs`
        so the replayed commit wins, and the branch from `GITHUB_REF_NAME`
        rather than main — and six copies of it were six chances to drift."""
        loose = [p.name for p in sorted(self.WORKFLOWS.glob("*.yml"))
                 if "push rejected (attempt" in p.read_text(encoding="utf-8")]
        self.assertEqual(loose, [], f"these re-implement the push loop: {loose}")

    def test_the_shared_publish_workflow_runs_the_checks(self):
        """The delegation below is only sound because this holds.

        Seven callers stopped containing the checks step when their tail moved
        into `publish.yml`; if that workflow ever stops running the list, all
        seven go unchecked at once and every one of them still looks fine.
        """
        body = (self.WORKFLOWS / "publish.yml").read_text(encoding="utf-8")
        self.assertIn(self.CHECKS, body)
        self.assertIn(self.PUBLISH, body)

    def test_every_workflow_that_pushes_runs_them_first(self):
        unchecked = [
            path.name
            for path in sorted(self.WORKFLOWS.glob("*.yml"))
            if self._pushes(body := path.read_text(encoding="utf-8"))
            # Either it runs the list itself, or it hands the whole tail to
            # the one workflow that does -- asserted directly above.
            and self.CHECKS not in body
            and self.DELEGATE not in body
        ]
        self.assertEqual(
            unchecked, [],
            "these push commits that no CI run will ever see; add "
            f"`{self.CHECKS}` before the commit step: {unchecked}",
        )


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
        payload = published.page()
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

    TEMPLATE = ROOT / "templates" / "index.html"

    def nitrox_by_vessel(self) -> dict[str, int]:
        """How many *boats* fall in each state. Nitrox is a vessel's policy."""
        payload = published.page()
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
        counts = self.nitrox_by_vessel()
        html = self.TEMPLATE.read_text(encoding="utf-8")
        if not counts.get("listed_unpriced"):
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

    APP = ROOT / "templates" / "app.js"

    NEVER_SHIPPED = ("charged", "charged_max", "counted", "basis", "provenance")

    def lines(self, live=False):
        """The live dataset where asked for: the seed prices no fee as a range,
        so `display_max` legitimately appears on none of its lines."""
        payload = published.page() if live else build_payload(Dataset.load(SEED))
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


class TestPinnedColumns(unittest.TestCase):
    """A pinned group is only as good as the rule that closes it.

    `pinned()` says how many leading columns are frozen, and the count changes
    with the breakpoint -- four on a wide screen, three on a laptop, one on a
    phone, where freezing Depart, Boat and Guests held 231px of a 390px screen
    still. Each count paints a `pins-N` class on the body, and the CSS draws
    the strong edge on `.pins-N .stickN`. Miss one and nothing throws: the
    columns still freeze, they just stop saying where the identity ends, which
    is the difference between a group and three columns that happen to be
    adjacent.
    """

    APP = ROOT / "templates" / "app.js"
    CSS = ROOT / "templates" / "style.css"

    def counts(self) -> list[int]:
        body = re.search(r"function pinned\(\) \{(.*?)\}", self.APP.read_text(encoding="utf-8"), re.S)
        assert body, "pinned() not found in app.js"
        return sorted({int(n) for n in re.findall(r"\b(\d+)\b", body.group(1))})

    def test_the_counts_were_found(self):
        counts = self.counts()
        self.assertTrue(counts, "pinned() parsed to no counts at all")
        self.assertTrue(all(1 <= n <= 4 for n in counts), counts)

    def test_every_count_paints_its_body_class(self):
        source = self.APP.read_text(encoding="utf-8")
        for n in self.counts():
            self.assertIn(f'classList.toggle("pins-{n}"', source)

    def test_every_count_closes_its_group(self):
        css = self.CSS.read_text(encoding="utf-8")
        for n in self.counts():
            self.assertIn(
                f".pins-{n} .stick{n}", css,
                f"pinned() can return {n}, but no rule closes a group of {n}",
            )

    def test_every_pinned_column_has_an_offset(self):
        css = self.CSS.read_text(encoding="utf-8")
        for n in range(1, max(self.counts()) + 1):
            self.assertIn(f".stick{n} {{", css)


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
    a fact silently withdrawn. The column is back, the vocabulary it reads is
    `entry_bars`, and `level_labels` is gone from the payload rather than left
    in it unread -- which is this class getting the outcome it was written for,
    in both directions.

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

    #: The same, per itinerary and per departure. Also empty, and the emptiness
    #: is worth more here: a top-level key ships once, an itinerary key ships
    #: 402 times and a departure key 1,122, so this is the level where an
    #: unread field is measured in tens of kilobytes rather than in bytes.
    ITINERARY_UNREAD: dict[str, str] = {}
    DEPARTURE_UNREAD: dict[str, str] = {}

    def app(self) -> str:
        return self.APP.read_text(encoding="utf-8")

    def code(self) -> str:
        """`app.js` with its comments removed.

        Searching the raw source cannot tell a reader from a mention, and this
        file is a third prose by weight -- 69 KB of the 190. Four fields were
        unread while every one of them appeared in a comment, and `operator`
        appeared five times in code as an ordinary English word inside a
        sentence the page prints ("This operator publishes no required
        extras"), never once as a property. A guard that accepted those would
        be green for the wrong reason, which is the failure this class is
        about.

        Block comments only, because `app.js` has no line comments at all --
        stripping `//` would cut the tail off any URL in a string literal.
        `test_the_source_has_no_line_comments` holds that assumption up.
        """
        return re.sub(r"/\*.*?\*/", "", self.app(), flags=re.S)

    def reads(self, key: str) -> bool:
        """Whether the page reads `key` as a property, in code rather than prose.

        Dot access or a quoted subscript. A key that appears only as a bare
        word is not a reader.
        """
        pattern = r"\." + re.escape(key) + r"\b|[\[(,]\s*[\"']" + re.escape(key) + r"[\"']"
        return re.search(pattern, self.code()) is not None

    def rows(self, payload: dict, level: str) -> list[dict]:
        source = (payload["itineraries"].values() if level == "itineraries"
                  else payload["departures"])
        return list(source)

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

    def test_the_source_has_no_line_comments(self) -> None:
        """`code()` strips block comments only, and this is why that is enough.

        If `//` comments ever appear, either they start hiding readers from
        the guard or stripping them starts cutting URLs in half. Either way
        somebody has to decide, rather than find out from a green build.
        """
        stripped = re.sub(r"/\*.*?\*/", "", self.app(), flags=re.S)
        offenders = [n for n, line in enumerate(stripped.splitlines(), 1)
                     if re.match(r"\s*//", line)]
        self.assertEqual(offenders, [], "app.js has line comments now")

    def test_every_itinerary_key_has_a_reader(self) -> None:
        """The level where the bytes are.

        `test_every_top_level_key_has_a_reader` walked the payload's top level
        and nothing walked below it, so four fields shipped unread to every
        visitor -- `summary` at 63 KB, `operator`, `one_way` and a
        `spaces_left` that was null on all 1,122 rows. 75 KB of a page that
        lazily fetches nothing, past a guard whose docstring says every fact
        the page ships is a fact the page prints.
        """
        for key in {k for row in self.rows(self.payload(), "itineraries") for k in row}:
            if key in self.ITINERARY_UNREAD:
                continue
            with self.subTest(key=key):
                self.assertTrue(
                    self.reads(key),
                    f"every itinerary ships {key!r} and app.js never reads it; "
                    f"either print it or stop serialising it",
                )

    def test_every_departure_key_has_a_reader(self) -> None:
        """The same, one level down and 1,122 rows wide."""
        for key in {k for row in self.rows(self.payload(), "departures") for k in row}:
            if key in self.DEPARTURE_UNREAD:
                continue
            with self.subTest(key=key):
                self.assertTrue(
                    self.reads(key),
                    f"departures ship {key!r} and app.js never reads it; "
                    f"either print it or stop serialising it",
                )

    def test_the_committed_dataset_ships_no_unread_key_the_seed_lacks(self) -> None:
        """The two tests above run on the seed, which is what keeps them ahead
        of the fetch. The seed carries neither seller's second bill, no cabin
        ladder and no sale, so five keys exist only once real data is loaded --
        and a dead one among them would never be reached. This is the same
        assertion over the committed payload, which makes it a publication gate
        rather than a code test, and it goes through `published` for that.
        """
        payload = published.page()
        for level, allowed in (("itineraries", self.ITINERARY_UNREAD),
                               ("departures", self.DEPARTURE_UNREAD)):
            for key in {k for row in self.rows(payload, level) for k in row}:
                if key in allowed:
                    continue
                with self.subTest(level=level, key=key):
                    self.assertTrue(
                        self.reads(key),
                        f"{level} ship {key!r} and app.js never reads it",
                    )

    def test_the_entry_bar_reaches_the_page(self) -> None:
        """The specific fact this class was written for.

        A stated safety requirement is the one kind of number here that is not
        about money, and it is the whole of what the second source was added
        for. It travels from the itinerary record through `entry_bars` into a
        column and a filter bank, and every step of that has to be present.

        `level_labels` used to be the vocabulary named here, and this assertion
        outlived it: once the phrase was built from the certification and the
        dive count instead, the only "level_labels" left in `app.js` was the
        word inside a comment -- which this test would have accepted. A check
        that passes on its own history is the failure this module exists to
        catch, so it names the reader rather than the string.
        """
        source, payload = self.app(), self.payload()
        self.assertIn("requirements", source, "app.js reads no entry bar")
        self.assertIn("entry_bars", source, "app.js has no vocabulary for it")
        self.assertIn("min_level", source, "app.js reads no certification level")
        self.assertIn("min_logged_dives", source, "app.js reads no dive count")
        self.assertIn('k: "entry"', source, "the page has no Entry bar column")
        bars = [i for i in payload["itineraries"].values() if i.get("requirements")]
        self.assertTrue(bars, "the seed itself states no entry bar to print")

    def test_the_entry_bar_column_is_in_every_column_order(self) -> None:
        """A column missing from an order is appended, so it cannot vanish --
        but it lands after the provenance columns, at the far right of a table
        this wide, which for the one column that says whether a row is bookable
        at all is barely different from being gone. Four orders, four layouts,
        and the phone one is where it matters most."""
        source = self.app()
        for name in ("var ORDER", "var PHONE_ORDER", "var TINY_ORDER",
                     "var COMPACT_ORDER"):
            with self.subTest(order=name):
                body = source[source.index(name):]
                body = body[: body.index("]")]
                self.assertIn('"entry"', body, f"{name} does not place the column")

    def test_the_entry_bar_is_never_softened_by_the_page(self) -> None:
        """The printed dive count is the greater of the two numbers.

        `advanced_50` means fifty dives by definition, and a trip stating a
        smaller `min_logged_dives` beside it must not print the smaller one:
        that would publish a bar below the certification the same record also
        demands. The rule is one `Math.max` in `entryDives`, which is exactly
        the kind of line a later edit reverses without noticing.
        """
        source = self.app()
        body = source[source.index("function entryDives"):]
        body = body[: body.index("}")]
        self.assertIn("Math.max", body,
                      "entryDives must take the greater of the stated and the "
                      "implied dive count, never the stated one alone")

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
        for heading in ("Places", "Mandatory fees", "Per dive"):
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
    PUBLISH = "uses: ./.github/actions/publish"
    DELEGATE = "uses: ./.github/workflows/publish.yml"

    @staticmethod
    def is_reusable(text: str) -> bool:
        """Whether this is a called tail rather than a workflow of its own.

        `workflow_call` workflows fire no `workflow_run` event -- the *caller's*
        run is what completes -- so `publish.yml` must never be required in
        pages.yml's watch list, and adding it would watch nothing.

        Stated rather than left to luck. This guard already passed on it for
        the wrong reason: `commits_published_files` looks for the first
        `paths:` line, and in publish.yml that is the *input declaration*,
        which has no value, so the check fell through and returned False. The
        answer was right and the reasoning was an accident, which is one
        refactor away from being wrong and still green.
        """
        return "workflow_call:" in text

    def commits_published_files(self, text: str) -> bool:
        """Whether the workflow commits anything the page is built from.

        Two spellings, and the second is why this reads the action rather than
        the shell. It used to scan for `git add`, and #123 moved every one of
        those into `.github/actions/publish` — after which no workflow matched,
        every workflow was skipped, and this test passed by having nothing left
        to look at. Same failure as the push guard above, from the same commit.
        """
        marker = max(text.find(self.PUBLISH), text.find(self.DELEGATE))
        if marker >= 0:
            # Only `paths:` *after* the publish step, because `on: push:` has a
            # `paths:` of its own and `re.search` over the whole file finds that
            # one first. fees.yml and promote.yml both have such a trigger, and
            # reading it as a staging list made this guard stop requiring the
            # two of them -- silently, and in the direction where a data commit
            # lands and the page keeps serving an older build.
            staged = re.search(r"^\s*paths:\s*[\"']?([^\n]*?)[\"']?\s*$",
                               text[marker:], re.M)
            value = staged.group(1).strip() if staged else ""
            # An empty or interpolated value is the default, `data site`.
            names = (["data"] if not value or value.startswith("${{")
                     else re.findall(r"[\w./-]+", value))
            if any(n.split("/")[0] in self.PUBLISHED for n in names):
                return True
        for line in re.findall(r"^\s*git add\s+(.*)$", text, re.M):
            for path in re.findall(r"[\w./-]+", line):
                if path.split("/")[0] in self.PUBLISHED:
                    return True
        return False

    def test_the_guard_can_still_see_a_data_commit(self):
        """Asserted, because this test has already been silently blinded once.

        A check that stops checking is worse than no check: it is green for the
        wrong reason, and green is what everybody reads.
        """
        seen = [p.name for p in sorted(self.WORKFLOWS.glob("*.yml"))
                if p.name != "pages.yml"
                and self.commits_published_files(body := p.read_text(encoding="utf-8"))
                and not self.is_reusable(body)]
        self.assertGreaterEqual(
            len(seen), 5,
            f"only {seen} appear to commit data; has the mechanism moved again?")

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
            if not self.commits_published_files(text) or self.is_reusable(text):
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
        payload = published.page()
        counts = {"both": 0, "liveaboard": 0, "padi": 0}
        for d in payload["departures"]:
            counts["padi" if d.get("padi_only")
                   else "both" if d.get("padi") is not None
                   else "liveaboard"] += 1
        self.assertEqual(sum(counts.values()), len(payload["departures"]))
        for state, n in counts.items():
            self.assertGreater(n, 0, f"the {state!r} chip would render with no rows")

    def test_the_column_is_named_for_what_it_holds(self) -> None:
        """It stopped being one source the day it started linking two."""
        self.assertIn('{ k: "source", t: "Seller",', self.app())

    def labels(self) -> str:
        block = re.search(r"var SELLER_LABELS = \{(.*?)\};", self.app(), re.S)
        assert block, "SELLER_LABELS not found in app.js"
        return block.group(1)

    def test_every_chip_names_its_seller(self) -> None:
        """A filter that says who sells a berth has to say who.

        The middle chip read "Here only", which asks the reader to work out
        which of the two sites "here" is -- and this page is neither of them:
        it is a third thing that reads both and compares them.
        """
        labels = self.labels()
        self.assertIn('"liveaboard only"', labels)
        self.assertIn('"PADI only"', labels)
        self.assertNotIn("Here", labels)

    def test_the_chip_and_the_link_call_the_seller_one_thing(self) -> None:
        """The chip filters to rows whose Seller column links that same site.

        Two names for one seller is the drift this whole column exists to
        avoid: a reader who narrows to "liveaboard only" and then reads
        something else in the link beside the row has to work out whether they
        are the same place.
        """
        word = re.search(r'liveaboard: "(\w+)', self.labels()).group(1)
        self.assertIn(f'? "{word}"', self.app(),
                      "the Seller column names that seller something else")


class TestTheBuiltStampIsTheBuild(unittest.TestCase):
    """The colophon's "page built" is the build, to the minute.

    It printed `meta.generated` -- the day the *data* was scraped -- under the
    word "built". Two different facts under one label, and the one it showed
    was not the one it named: they diverge whenever a parser or template change
    ships without a fresh crawl, which is most of them.

    Minutes because the page is rebuilt several times an hour on a busy day and
    a date alone cannot tell two of those apart, which is the whole question
    somebody reading that line is asking.
    """

    def test_the_payload_carries_a_build_stamp(self) -> None:
        meta = published.page()["meta"]
        self.assertRegex(meta["built"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC$")

    def test_it_is_not_the_crawl_date_wearing_a_new_name(self) -> None:
        meta = published.page()["meta"]
        self.assertNotEqual(meta["built"], meta["generated"])
        self.assertRegex(meta["generated"], r"^\d{4}-\d{2}-\d{2}$",
                         "the crawl date must stay a date: it is a day, not a moment")

    def test_the_colophon_prints_the_build_beside_the_crawl(self) -> None:
        """The two dates answer one question -- how current is this -- so they
        sit together. The build stamp was a fourth clause on the toolbar line
        about the fleet, where nobody reading it had asked."""
        app = (ROOT / "templates" / "app.js").read_text(encoding="utf-8")
        page = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn('" · page built " +\n      (D.meta.built || D.meta.generated)', app)
        self.assertIn('id="builtStamp"', page)
        self.assertIn("Prices read __GENERATED__", page)
        self.assertNotIn("boats bookable by the berth · all prices in \" +\n"
                         "    D.meta.currency +", app,
                         "the toolbar is still appending the build stamp")


class TestTheThreeViews(unittest.TestCase):
    """Trips, on sale and history are three views of one document.

    Separate HTML files were the alternative and were rejected on weight: the
    payload is inlined, and the sale view is the trips view's own rows with the
    markdown filter held on, so a second document would ship those megabytes
    again to answer a question the first one already holds the data for. That
    makes the split a set of panes and a rail rather than a set of files, and
    every rule below is a way for that to fail quietly rather than loudly.

    What is asserted here is wiring -- a control that addresses a view, a
    placeholder that lands in one pane and not two. Anything about the *size*
    of what those panes render is in ``tests/test_layout.py``, which drives a
    browser, because this file's way of checking a layout claim was to assert
    that the source text of the rule was present. Eight such assertions passed
    over a table rendered at zero height (#130), including the one named for
    the panel that caused it.
    """

    PAGE = ROOT / "templates" / "index.html"
    APP = ROOT / "templates" / "app.js"
    CSS = ROOT / "templates" / "style.css"

    def page(self) -> str:
        return self.PAGE.read_text(encoding="utf-8")

    def app(self) -> str:
        return self.APP.read_text(encoding="utf-8")

    def css(self) -> str:
        return self.CSS.read_text(encoding="utf-8")

    def test_each_view_has_a_pane_and_a_way_to_reach_it(self) -> None:
        """Half a view is worse than none: a rail item pointing at nothing, or
        a pane nothing can open, is a section that exists only in the markup."""
        page, app = self.page(), self.app()
        for pane in ('id="tablePane"', 'id="historyPane"'):
            self.assertIn(pane, page)
        for item in ('id="navTrips"', 'id="navSale"', 'id="navHistory"'):
            self.assertIn(item, page)
        for target in ('href="#trips"', 'href="#sale"', 'href="#history"'):
            self.assertIn(target, page, "a rail item addresses no view")
        self.assertIn('VIEWS = ["trips", "sale", "history"]', app)
        self.assertIn("hashchange", app, "a view cannot be linked to or reloaded into")

    def test_the_sale_view_is_the_overview_and_not_the_filter(self) -> None:
        """Three questions, three panes.

        The sale view was built as the trips table with the markdown filter
        held down, which was the wrong reading of what it is for. *Which
        departures are discounted* is a table question and the table has a
        control for it -- the On sale chip, filtering those rows like any other
        filter. Asking it a second time as a destination gave the rail an entry
        that duplicated a chip, and left the thing the view should have been
        carrying -- the discount overview: which boats are marked down, by how
        much, what moved since yesterday -- folded into a `details` above the
        table, which is where it already was and where nobody looking for it
        would go.
        """
        page, app = self.page(), self.app()
        self.assertIn('id="salePane"', page, "the sale view has no pane of its own")
        self.assertIn("function saleOnly() { return state.onSaleOnly; }", app,
                      "the sale view is holding the markdown filter down again")
        self.assertIn("saleOnly() && !dep.sale", app,
                      "the row filter no longer reads the chip")
        sale = page.split('id="salePane"', 1)[1].split("</section>", 1)[0]
        self.assertIn('id="dealsBody"', sale, "the overview is not in the sale view")
        table = page.split('id="tablePane"', 1)[1].split("</div><!-- /#tablePane -->", 1)[0]
        self.assertNotIn('id="dealsBody"', table,
                         "the overview is still folded above the trips table")

    def test_the_overview_is_a_document_rather_than_a_fold(self) -> None:
        """It was a `details` capped at a third of the window because the table
        under it was the page. On a view of its own none of that applies, and
        the cap and the fold's flex arithmetic go with it."""
        page, css = self.page(), self.css()
        self.assertNotIn("<details class=\"deals\"", page,
                         "the overview still opens and closes")
        self.assertNotIn("#deals[open]", css,
                         "the fold's sizing rules outlived the fold")
        self.assertIn(".sale-pane { overflow:auto; }", css,
                      "the overview does not scroll as a document")

    def test_a_view_with_nothing_behind_it_is_not_offered(self) -> None:
        """The On sale chip's own rule, applied to a whole section: a control
        that can do nothing must not be dressed as one that can. A checkout
        whose deals book is empty has no overview to show, so it has no sale
        view, and typing its name into the address bar must not conjure one."""
        app = self.app()
        self.assertIn("saleView = drawDeals();", app)
        self.assertIn('if (name === "sale" && !saleView) name = "trips";', app)

    def test_the_address_bar_never_names_a_view_that_was_declined(self) -> None:
        """`showView` rewrites a name it will not honour -- an unknown one, or
        the sale view where there is no sale data -- and the hash has to be
        rewritten with it. Left alone, what the visitor bookmarks or shares is
        a link to a view the page decided not to give them, saying nothing
        about it. `replace`, because a corrected address is not a place to go
        back to."""
        app = self.app()
        self.assertIn("window.location.replace", app,
                      "a declined view stays in the address bar")
        self.assertNotIn("window.location.hash =", app,
                         "correcting the address leaves a history entry to go back to")

    def test_the_history_view_carries_the_report_and_the_files(self) -> None:
        """Both placeholders moved out of the method footer together. Leaving
        one behind would put the downloads under a heading about fee arithmetic
        and the report on a page of its own."""
        page = self.page()
        history = page.split('id="historyPane"', 1)[1].split("</section>", 1)[0]
        self.assertIn("__CHANGES__", history, "the change report is not in the history view")
        self.assertIn("__DOWNLOADS__", history, "the downloads are not in the history view")
        self.assertEqual(page.count("__CHANGES__"), 1, "the report is rendered twice")
        self.assertEqual(page.count("__DOWNLOADS__"), 1, "the downloads are listed twice")

    def test_hidden_beats_display_on_every_pane(self) -> None:
        """Which view is on screen is the `hidden` attribute, and `.pane` sets
        `display:flex`.

        An author `display` beats the user agent's `[hidden] { display:none }`
        whatever its specificity, so without this rule the history view drew on
        top of the table and every pane was visible at once. It failed exactly
        this way once; the rule is one line and the bug is silent.
        """
        self.assertIn("[hidden] { display:none !important; }", self.css())

    def test_every_view_names_itself(self) -> None:
        """Three things read a view's name and none of them is the screen: the
        document outline, the browser tab and whatever announces that the main
        region has been replaced. All three views printed one title, so a
        bookmark of the history view said trips; two of the three had no
        heading, so the outline went from the site's h1 straight to a table."""
        page, app = self.page(), self.app()
        self.assertIn('class="view-heading"', page, "the trips pane has no heading")
        for heading in ("<h2>What is on sale</h2>", "<h2>What changed, refresh by refresh</h2>"):
            self.assertIn(heading, page)
        self.assertIn("document.title =", app, "every view shares one title")
        self.assertIn("tabindex=\"-1\"", page,
                      "no pane can be focused, so a view change is announced by nothing")
        self.assertIn(".focus()", app, "nothing moves focus into the view that appeared")

class TestTheOffersPanelNamesItsSellers(unittest.TestCase):
    """Rules that live in `app.js` and have no Python to test, so the guard is
    that the built page still contains them.

    Two of them, and they are the same rule twice. **One date over two sellers
    dates half of them wrong**: `berths_read` and `padi_berths_read` are two
    crawls two days apart, and the sale marks stamped the first over both --
    on 124 rows resting partly on the second and 2 resting entirely on it.
    And **the two sellers' offers belong on one row**: they were drawn as two
    tables sharing no column, ten boats in each with no way to read across, and
    a merge that keys on either seller's list alone drops the other's.
    """

    APP = ROOT / "templates" / "app.js"

    def source(self) -> str:
        return self.APP.read_text(encoding="utf-8")

    def test_a_sale_mark_dates_each_seller_separately(self):
        app = self.source()
        self.assertIn("function namedReadings(", app)
        self.assertIn("namedReadings(d.sale.sellers)", app)
        self.assertNotIn(
            'var read = D.meta.berths_read ? ", read "', app,
            "the sale mark is stamping one crawl's date over both sellers again",
        )

    def test_the_page_draws_one_table_for_both_sellers(self):
        app = self.source()
        self.assertIn("function offersTable(", app)
        for gone in ("function fleetTable(", "function dealsTable("):
            self.assertNotIn(
                gone, app,
                f"{gone} is back; the two sellers' offers are two tables again",
            )

    def test_the_merge_keeps_a_boat_only_one_seller_names(self):
        """The union, not the intersection.

        Ten boats fill both halves today, so keying on the fleet rows alone
        passes every test and silently drops a PADI offer for a boat no ladder
        has caught -- out of the panel headed "what is discounted", which is
        this site's own reported failure in somebody else.
        """
        app = self.source()
        block = re.search(r"function offerRows\((.*?)\n  \}", app, re.S)
        assert block, "offerRows() not found in app.js"
        self.assertIn("if (!byBoat[o.boat])", block.group(1))

    def test_the_panel_states_what_it_could_not_read(self):
        app = self.source()
        self.assertIn("function coverageNote(", app)
        for field in ("dropped", "unread", "banner_unsupported"):
            with self.subTest(field=field):
                self.assertIn("coverage." + field, app)

    def test_a_lone_advertised_price_says_whether_two_sellers_quote_it(self):
        """One figure in that column means three different things -- both
        sellers quote it, PADI quotes it and cannot be totalled, or nobody
        else was asked -- and they printed identically."""
        app = self.source()
        self.assertIn("function whoAdvertised(", app)
        self.assertIn("whoAdvertised(d, row)", app)


class TestThePageIsWhatItsDataBuilds(unittest.TestCase):
    """The committed page must be what the committed data renders to.

    `promote --check` proves `data/egypt-2027.json` is what the parser makes of
    the committed inputs. Nothing made the same claim about `site/index.html`,
    and the gap is not theoretical: on 2026-08-31 the published page said
    ``berths_read: 2026-08-28`` while `data/cabins.json` committed beside it
    said ``2026-08-31``. The site told visitors the berth counts were three
    days older than the data it shipped them with.

    It arrived through the publish action's rebase. Two data jobs overlapped;
    `-X theirs` favours the commit being replayed, which is right for the
    reading that job took and wrong for the *derived* files, which are nobody's
    reading -- they were built at checkout, before the other job's inputs
    landed. So a stale page overwrote a fresh one. `promote --check` stayed
    green the whole time, because the dataset really did match its inputs; only
    the page was behind. The next run rebuilt and healed it, which is what
    makes this worth a test rather than a fix alone: the window was real,
    deployed, and closed itself before anyone could see it.

    The action now re-derives after a rebase. This is the net under that, and
    the reason it is a test rather than another step in `.github/actions/
    checks`: `checks` runs before the push, and the rebase only happens after
    the push is rejected, so by the time this can go wrong `checks` is over.
    """

    STAMP = re.compile(r'"built":"[^"]*"')

    def test_the_committed_page_matches_a_fresh_render_of_the_committed_data(self):
        committed = published.site_page()
        import tempfile

        from liveaboard.render import render

        with tempfile.TemporaryDirectory() as tmp:
            fresh = render(published.dataset(), tmp, data_dir=published.DATA)
            a = self.STAMP.sub("", committed.read_text(encoding="utf-8"))
            b = self.STAMP.sub("", fresh.read_text(encoding="utf-8"))

        # The payload is one enormous line, so a diff would print the whole
        # page. Report where they part instead, which is what names the field.
        if a != b:
            i = next((k for k in range(min(len(a), len(b))) if a[k] != b[k]),
                     min(len(a), len(b)))
            self.fail(
                "site/index.html is not what data/ builds — the page is behind "
                f"its own data.\n  committed: ...{a[max(0, i - 90):i + 90]}\n"
                f"  rebuilt  : ...{b[max(0, i - 90):i + 90]}"
            )


class TestThePublishTailDoesNotQueueOnConcurrency(unittest.TestCase):
    """`publish.yml` must not carry a `concurrency` group.

    #128 asked for the push to be serialised so two data sources could not
    collide. It was implemented that way -- one group shared across every
    caller, `cancel-in-progress: false` -- and it lost data on the first test.

    GitHub's concurrency is not a queue. A group holds one running job and
    **one** pending; a third arrival cancels the pending one. On three
    simultaneous dispatches, `deals.yml` read PADI's deals, uploaded them, and
    had its publish job cancelled four seconds later without running a step:
    a day's figures for that source fetched and thrown away, the run reading
    *cancelled* rather than failed, which nothing alerts on.

    Strictly worse than the race. The race published a wrong freshness date
    that healed within the hour; this silently drops a reading.

    The race is closed by the split instead -- the publish job derives from the
    branch tip it just checked out -- so the group bought nothing it did not
    also cost. A test, because "we tried the obvious thing and it ate a commit"
    is exactly the knowledge a comment loses to the next refactor.
    """

    WORKFLOW = ROOT / ".github" / "workflows" / "publish.yml"

    def test_the_reusable_tail_has_no_concurrency_group(self):
        body = self.WORKFLOW.read_text(encoding="utf-8")
        live = [line for line in body.splitlines()
                if line.strip().startswith("concurrency:")]
        self.assertEqual(
            live, [],
            "publish.yml has a concurrency group again; GitHub's is not a "
            "queue and will cancel a pending publish, discarding a reading "
            "that was already fetched. See this test's docstring.")

    def test_the_reason_is_written_down_where_somebody_would_add_one(self):
        """The comment is the fix here; a bare absence invites the re-add."""
        body = self.WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("not a queue", body)
        self.assertIn("cancels the pending", body)


class TestTheOnSaleChipCountsWhatIsOnScreen(unittest.TestCase):
    """Every filter count on the page answers "what if I picked this too?".

    The On sale chip did not. It was counted once at load, over the whole
    dataset, and never moved -- so it read "On sale 229" beside a table
    filtered to a boat with no sale on it, and the click then produced an empty
    result. **58 of the 77 boats have sailings and no sale at all**, so that
    was the common case rather than a corner ([#129]).

    The old comment argued the stale number said "how much there is to find"
    rather than how much the filters had left, and that a 0 would read as "no
    sales" rather than "none in June". It does not survive the rest of the
    panel: every other number here is filter-relative, so one that is not
    teaches only that this one lies, and the reader still learns "none in
    June" -- by ending up with an empty table instead of by reading a 0. The
    confusion was deferred past a click, not avoided.

    Two things make the fix work and both are asserted, because either alone
    is wrong:

    * `passes` has to let the sale filter **exclude itself**, the way `months`,
      `ports` and `boats` already do. Without that, switching the chip on makes
      its own count equal the visible rows and it can never guide the way back.
    * the chip has to be a **bank**, so it re-counts on every draw through the
      same `recount()` hook as the rest. Counting it anywhere else is a second
      mechanism to keep in step.
    """

    APP = ROOT / "templates" / "app.js"

    def setUp(self):
        self.app = self.APP.read_text(encoding="utf-8")

    def test_the_sale_filter_can_exclude_itself(self):
        # `saleOnly()` rather than `state.onSaleOnly`: the sale view holds the
        # same filter down as a destination, and the skip has to sit outside
        # both readings or the rail cannot count the view it is not on.
        self.assertIn('if (skip !== "sale" && saleOnly()', self.app,
                      "passes() must accept a `sale` skip, or the chip's own "
                      "count collapses onto the visible rows once it is on")

    def test_the_chip_recounts_with_every_other_bank(self):
        """It must go through `BANKS`, not a count taken once at load."""
        after = self.app[self.app.index("var onSale = document.getElementById"):]
        self.assertIn("BANKS.push(", after)
        self.assertIn('passes(dep, D.itineraries[dep.itinerary_id], "sale")', after)

    def test_a_count_of_zero_keeps_the_chip_rather_than_hiding_it(self):
        """`chips()` drops an unreachable option; a lone toggle must not.

        A bank can drop one because the reader sees the others and infers the
        rule. A single control that vanishes reads as a feature that is gone.
        It stays, disabled, saying 0 -- and stays clickable while it is *on*,
        for the same reason a picked chip survives at zero: the way out must
        not disappear.
        """
        after = self.app[self.app.index("var onSale = document.getElementById"):]
        self.assertIn("n === 0 && !state.onSaleOnly", after)
        self.assertIn("onSale.disabled = dead", after)
        self.assertNotIn("onSale.hidden = true", after)

    def test_the_disabled_chip_is_styled(self):
        """Otherwise "0" and a live count look identical and it invites a
        click that does nothing."""
        css = (ROOT / "templates" / "style.css").read_text(encoding="utf-8")
        self.assertIn("button.chip:disabled", css)


class TestTheEntryBankFoldsAtItsCertificationBoundary(unittest.TestCase):
    """The entry bank has a "show more" now, and it must not cut at a count.

    Every other bank caps at eight, because the eight commonest ports or boats
    are a fair sample of a set with no order of its own. The entry bar is a
    *ladder*, least demanding to most, and a count-based cut there is arbitrary
    and brutal: at eight it folds away 738 of 1122 rows, every Advanced rung,
    and the single biggest bar on the page -- Advanced + 50 dives, on 289 rows
    -- which sorts last precisely because it is the strictest.

    So it cuts where the certification changes instead. That puts every Open
    Water rung on screen and every Advanced rung behind the disclosure, which
    is a fold a reader can predict, and the label says which way it opens.

    Pinned because the obvious tidy-up -- "why does this one bank pass a limit
    function, let us just use chipLimit()" -- silently restores exactly the
    behaviour the fold was designed to avoid, and nothing on the page would
    look wrong afterwards.
    """

    APP = ROOT / "templates" / "app.js"

    def setUp(self):
        self.app = self.APP.read_text(encoding="utf-8")
        self.entry = self.app[self.app.index('chips("entry"'):]
        self.entry = self.entry[:self.entry.index('chips("sellers"')]

    def test_the_entry_bank_passes_a_boundary_limit_rather_than_the_count(self):
        self.assertIn("limit: function (live)", self.entry,
                      "the entry bank no longer computes its own cut; a plain "
                      "chipLimit() hides every Advanced rung, 738 of 1122 rows")
        self.assertIn('split(" + ")[0]', self.entry,
                      "the cut must key on the certification, which is the "
                      "part of the label before the dive count")

    def test_the_disclosure_says_which_way_it_opens(self):
        """"+ 9 more" is uninformative on a ladder; the direction is the point."""
        self.assertIn('moreWord: "stricter"', self.entry)
        self.assertIn('opts.moreWord || "more"', self.app,
                      "chips() must still default to 'more' for the banks that "
                      "are lists rather than ladders")

    def test_a_single_certification_falls_back_to_the_ordinary_cap(self):
        """A fold that hides nothing meaningful should just be the normal one."""
        self.assertIn("chipLimit()", self.entry,
                      "no fallback: if the ladder ever holds one certification "
                      "the boundary rule would show every rung uncapped")


class TestNeitherSellerIsTheHouse(unittest.TestCase):
    """`padi.com` and `liveaboard.com` are both sources this site reads.

    liveaboard.com was read first and PADI Travel second. That is a fact about
    this project's history rather than about either seller, and it had hardened
    into a hierarchy the code stated out loud: one seller was *ours* and the
    other *the other seller* (#139).

    The half a visitor could be misled by was the link. Where both sellers sold
    a sailing the column named them; where only liveaboard.com did, the label
    was the generic **"listing"** -- and no row anywhere read "listing" and
    meant PADI. A visitor following it was handed to a site the page had never
    named.

    What is *not* asserted here is the asymmetries that are real, because every
    one of them is a statement about what a source publishes rather than about
    which came first: the fee panel is the vessel's own and beats a seller's
    account of it, a row states `pct` only from the seller whose fare it
    prints, the two read-dates are two crawls on two days, and PADI's
    `availability` fills the whole-sailing slot because that was measured.
    """

    APP = ROOT / "templates" / "app.js"

    def setUp(self) -> None:
        self.app = self.APP.read_text(encoding="utf-8")

    def test_a_seller_link_names_the_seller_it_opens(self) -> None:
        self.assertIn('(d.padi_only ? "PADI" : "liveaboard") + " ↗</a>"', self.app,
                      "a link label is generic again, so one seller is the "
                      "unmarked default and a visitor cannot tell where it goes")
        column = self.app.split('{ k: "source", t: "Seller",', 1)[1].split("} }", 1)[0]
        self.assertNotIn('"listing"', column,
                         '"listing" is liveaboard.com under a name that does '
                         "not say so")

    def test_the_cheaper_seller_is_named_rather_than_owned(self) -> None:
        """`cheapest: "ours" | "padi"` -- an enum with one value naming a
        company and one naming us -- made a reader decode the project's reading
        order before they could check any arithmetic that used it."""
        self.assertIn('cheaper: same ? "same" : gap < 0 ? "liveaboard" : "padi"', self.app)
        self.assertNotIn('"ours"', self.app.split("function best(", 1)[1])

    def test_both_bills_are_keyed_by_their_seller(self) -> None:
        """`.m` was named for what it is and `.p` for whose it is, and `best()`
        overloaded `.m` again for whichever bill is cheaper -- three meanings
        across two letters."""
        self.assertIn("{ d: dep, i: itin, lav: metricsFor(dep), padi: padiMetricsFor(dep) }",
                      self.app)
        # Anchored: a bare "row.m" is a substring of "narrow.matches".
        self.assertIsNone(re.search(r"\brow\.m\b", self.app),
                          "the row's liveaboard.com bill is `.lav`")
        self.assertIsNone(re.search(r"\brow\.p\b", self.app),
                          "the row's PADI bill is `.padi`")


class TestAnEmptyFeeCellSaysWhy(unittest.TestCase):
    """A blank in a money column reads as zero unless something stops it.

    Whether the operator stated its required extras had a column of its own --
    *Disclosure*, two places right of the fees it was about -- carrying "not
    looked at" or "optional only" as a pill. Two cells for one fact, and the
    one a reader lands on while reading the bill was the mute one: the fee cell
    printed a dash, which in a column of money reads as "no fees". Every
    Egyptian liveaboard pays park and port dues, so that is the opposite of
    what it means.

    The reason is printed in the fee column now. Both states survive because
    they are different failures -- a panel nobody has read, and a panel that
    was read and names only optional extras -- and neither may be rendered as
    a figure or as a category the row belongs to.
    """

    APP = ROOT / "templates" / "app.js"

    def setUp(self) -> None:
        self.app = self.APP.read_text(encoding="utf-8")

    def test_the_disclosure_column_is_gone(self) -> None:
        self.assertNotIn('"disclosure"', self.app,
                         "the reason is a column of its own again, away from "
                         "the fees it is about")
        titles = set(re.findall(r'\bt: "([^"]+)"', self.app))
        self.assertNotIn("Disclosure", titles)
        self.assertIn("Mandatory fees", titles)

    def test_the_fee_cell_prints_the_reason_rather_than_a_dash(self) -> None:
        fees = self.app.split('t: "Mandatory fees"', 1)[1].split("} },", 1)[0]
        self.assertIn("disclosure(d)", fees, "the fee cell does not say why it is empty")
        self.assertIn("FEE_WHY", fees, "the fee cell offers no explanation on hover")
        self.assertNotIn('<span class="dim">—</span>', fees,
                         "the fee cell is a mute dash again")

    def test_both_reasons_are_kept_apart_and_neither_reads_as_no_fees(self) -> None:
        """"Nobody looked" and "the operator did not say" are different
        failures. Each carries a sentence, and each sentence has to rule out
        the reading a blank invites."""
        self.assertIn('none: "Nobody has read', self.app)
        self.assertIn('partial: "The operator publishes only optional', self.app)
        block = self.app.split("var FEE_WHY = {", 1)[1].split("\n  };", 1)[0]
        for state in ("none", "partial"):
            sentence = block.split(f"{state}:", 1)[1].split("\n    partial:", 1)[0]
            self.assertRegex(
                sentence, r"park and port|still charged",
                f"the {state} sentence does not rule out the reading that a "
                f"blank fee cell invites, which is that there are no fees")

    def test_an_unstated_fee_sorts_last_rather_than_cheapest(self) -> None:
        """A trip nobody read the fees for is not a cheap trip, and must not
        collide with a genuine zero at the top of a cheapest-first sort."""
        fees = self.app.split('t: "Mandatory fees"', 1)[1].split("show:", 1)[0]
        self.assertIn("Infinity", fees)

    def test_the_reason_is_not_dressed_as_a_value(self) -> None:
        """`.pill` is a state badge and reads as a value the row *has*. These
        two are the absence of one."""
        css = (ROOT / "templates" / "style.css").read_text(encoding="utf-8")
        self.assertIn(".unstated {", css)
        self.assertNotIn(".pill.full", css, "the disclosure pill outlived its column")
        self.assertNotIn(".pill.partial", css)
