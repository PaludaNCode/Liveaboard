"""Tests for dataset validation, the seed data, and the rendered payload."""

from __future__ import annotations

import json
import re
import tempfile
import unittest
import unittest.mock
from html import unescape
from urllib.parse import unquote
from datetime import date, datetime, timezone
from pathlib import Path

from liveaboard.dataset import Dataset, DatasetError
from liveaboard.export import latest_entry, recent_entries
from liveaboard.render import (
    HISTORY_DAYS,
    _recent_reports,
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

    def test_the_committed_page_leads_with_the_committed_latest_report(self):
        book = published.committed("changes.json")
        if not book.exists():
            self.skipTest("no data/changes.json yet")
        reports = json.loads(book.read_text(encoding="utf-8"))
        if not reports:
            self.skipTest("no report in data/changes.json yet")
        shipped = published.shipped_payload().get("changes") or []
        self.assertTrue(shipped, "the built page carries no change reports")
        self.assertEqual(
            shipped[0]["day"], reports[0]["day"],
            "the published page does not lead with the newest report in "
            "data/changes.json -- something built the page before appending to it")

    def test_the_committed_page_carries_the_whole_window(self):
        """Not just the newest: a week of refreshes is the view's default, and
        a page carrying one of them is the bug this replaced."""
        book = published.committed("changes.json")
        if not book.exists():
            self.skipTest("no data/changes.json yet")
        reports = json.loads(book.read_text(encoding="utf-8"))
        if not reports:
            self.skipTest("no report in data/changes.json yet")
        window = _recent_reports(reports, HISTORY_DAYS)
        shipped = published.shipped_payload().get("changes") or []
        self.assertEqual([r["day"] for r in shipped], [r["day"] for r in window])

    def test_the_report_reaches_the_page_as_data_and_not_as_prose(self):
        """`changes.compare` builds the report, `changes.render` flattened it to
        text, the CLI wrote the text to Markdown and `render` read it back out
        and escaped it into a `<pre>` -- a terminal transcript served to a
        browser, with boat names cut mid-word to fit eighty columns (#143)."""
        html = published.site_page().read_text(encoding="utf-8")
        if not (published.committed("changes.json").exists()
                and json.loads(published.committed("changes.json")
                               .read_text(encoding="utf-8"))):
            self.skipTest("no structured book yet, so the prose fallback is right")
        self.assertNotIn('<pre class="changelog">', html,
                         "the page is serving the change report as a transcript")
        self.assertIn('id="changeLog"', html)


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


class TestAScheduledRunReadsEverything(unittest.TestCase):
    """A cap meant for a dispatch must not be what the schedule inherits.

    `inputs.limit` is null on a scheduled run, so `${{ inputs.limit || 3 }}`
    resolves to **3** there -- the dispatch default, silently applied to the
    nightly job. `cabins.yml` writes the trap out in its `env:` block, having
    been built to avoid it, and `padi.yml` resolves it the same way; it reached
    `itineraries.yml` anyway, where the nightly incremental read took three
    trips a night against a book 58 fragments short of the fleet.

    It cannot be seen from outside the run. The job succeeds, the capped read
    merges into the book rather than replacing it -- correctly -- and a run
    that read 3 of what it was short of is indistinguishable from one that had
    almost nothing to do.

    So a scheduled workflow either resolves the cap against `github.event_name`
    or falls back to the uncapped value. Only the inputs that decide *how much
    of a source is read* are checked: `delay` has a politeness default that is
    right on both paths, and this guard would be wrong to call that a cap.
    """

    WORKFLOWS = ROOT / ".github" / "workflows"
    CAPS = ("limit", "max_pages")

    def test_no_schedule_inherits_a_dispatch_cap(self):
        checked = 0
        for workflow in sorted(self.WORKFLOWS.glob("*.yml")):
            text = workflow.read_text(encoding="utf-8")
            if "schedule:" not in text:
                continue
            for cap in self.CAPS:
                for use in re.finditer(
                    r"\$\{\{[^}]*\binputs\." + cap + r"\b[^}]*\}\}", text
                ):
                    expression = use.group(0)
                    checked += 1
                    with self.subTest(workflow=workflow.name, cap=cap):
                        self.assertTrue(
                            "event_name" in expression
                            or re.search(r"\|\|\s*'?0'?\s*\)?\s*\}\}",
                                         expression),
                            f"{workflow.name} reads `{cap}` as "
                            f"{expression} — on a scheduled run `inputs."
                            f"{cap}` is null, so this is the dispatch cap "
                            f"applied to the nightly job. Resolve it against "
                            f"github.event_name, as cabins.yml and padi.yml "
                            f"do, or fall back to 0",
                        )
        self.assertGreaterEqual(
            checked, 3, "this guard found fewer capped inputs than the "
                        "pipeline has scheduled sources; the pattern is stale")


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
    the prose was asserted rather than proof-read, the way `promote --check`
    asserts the dataset and `ALLOWED_EXTERNAL` asserts the page fetches nothing.

    **Asserting it was not enough, and the reason is a timing one.** The counts
    move only when a *fetch* runs, so on any machine the committed books are the
    old ones and the prose still matches -- the mismatch cannot appear locally.
    It appears on a runner, in a data job, *after* the crawl: on 2026-08-31 it
    refused `padi.yml` after eight minutes of PADI's API and `fees.yml` after a
    browser pass over 79 vessel pages, discarding both correct datasets over a
    sentence, twice. Meanwhile the entry-bar figures two sections down, which
    nothing asserted, had gone stale unnoticed: the page claimed PADI asks for
    more on 19 trips and for less on 41, and the data said 30 and 30.

    So the numbers are **substituted at build time** now, from
    `render._stated_figures`, and what these tests check is that the template
    states them that way and that the rendering fills them in. A derived number
    cannot drift, and there is nothing left to remember to edit.
    """

    TEMPLATE = ROOT / "templates" / "index.html"

    #: Every figure the footer states about the dataset, and the token it is
    #: written as. A count added to the prose without a token here is a count
    #: that will be wrong one refresh later.
    TOKENS = (
        "__BOATS__",
        "__NITROX_INCLUDED__", "__NITROX_PRICED__", "__NITROX_ABSENT__",
        "__NITROX_LOW__", "__NITROX_HIGH__", "__NITROX_MEDIAN__",
        "__NITROX_PER_TRIP__",
        "__BAR_BOTH__", "__BAR_DISAGREE__",
        "__BAR_PADI_STRICTER__", "__BAR_OURS_STRICTER__",
        "__PADI_ONLY__",
        "__FEE_BOTH__", "__FEE_DISAGREE__", "__FEE_WIDEST__", "__FARE_GAP__",
        "__FULL_DISAGREE__", "__ZERO_BOOKABLE__",
        "__ZERO_DEARER_LOW__", "__ZERO_DEARER_HIGH__",
        "__DIVES_VESSELS__", "__DIVES_LOW__", "__DIVES_HIGH__", "__DIVES_SPREAD__",
        "__GEAR_VESSELS__", "__GEAR_WEEK__",
        "__GEAR_LOW_THIRD__", "__GEAR_TOP_THIRD__",
        "__GEAR_DEAREST__", "__GEAR_DEAREST_BOAT__",
        "__GEAR_CHEAPEST__", "__GEAR_CHEAPEST_BOAT__",
        "__GEAR_ESTIMATE__", "__GEAR_ESTIMATED_VESSELS__",
        "__GEAR_ESTIMATED_TRIPS__",
    )

    def nitrox_by_vessel(self) -> dict[str, int]:
        """How many *boats* fall in each state. Nitrox is a vessel's policy."""
        from liveaboard.render import nitrox_by_vessel

        counts: dict[str, int] = {}
        for value in nitrox_by_vessel(published.page()).values():
            counts[value] = counts.get(value, 0) + 1
        return counts

    def test_the_prose_states_its_figures_as_tokens(self):
        """The fix for the timing problem, asserted so it cannot be undone by
        somebody typing a number back in during an edit."""
        html = self.TEMPLATE.read_text(encoding="utf-8")
        for token in self.TOKENS:
            self.assertIn(token, html, f"the footer no longer substitutes {token}")

    def test_every_token_the_template_uses_is_filled_in(self):
        """A token the renderer does not know stays on the page as its own name,
        which is worse than a stale number: it is visibly broken and it is also
        a figure nobody can read."""
        from liveaboard.render import _stated_figures, build_payload

        html = self.TEMPLATE.read_text(encoding="utf-8")
        dataset = published.dataset()
        known = _stated_figures(dataset, build_payload(dataset))
        for token in re.findall(r"__[A-Z_]+__", html):
            if token in ("__DATA__", "__ICON__", "__GENERATED__",
                         "__DOWNLOADS__", "__CHANGES__"):
                continue
            self.assertIn(token, known, f"{token} is in the template and has no figure")

    def test_the_rendered_page_carries_the_real_counts(self):
        """What the visitor actually reads, against the committed dataset."""
        from liveaboard.render import _stated_figures, build_payload

        dataset = published.dataset()
        figures = _stated_figures(dataset, build_payload(dataset))
        counts = self.nitrox_by_vessel()
        self.assertEqual(figures["__NITROX_INCLUDED__"], str(counts.get("included", 0)))
        self.assertEqual(figures["__NITROX_PRICED__"], str(counts.get("priced", 0)))
        self.assertEqual(figures["__NITROX_ABSENT__"], str(counts.get("absent", 0)))

    def test_no_figure_is_left_typed_into_the_prose(self):
        """The half of this that was still open after the tokens landed.

        The nitrox and entry-bar counts were substituted; every other number in
        the footer was still a literal, and by 2026-09-01 five of them were
        wrong -- 230 PADI-only sailings against 225, "43 of 74" fee books
        against 84 of 179, "the ten vessels that do publish a figure" against
        69, and a "sixteen trips by more than 150 euro" whose widest gap had
        fallen to 140. The page was making its own argument with numbers that
        had stopped being true, which is the failure it exists to report in
        other people.

        So this asserts the *shape*: a sentence in the footer that states a
        count about the dataset states it as a token. Written as the sentences
        the mismatches were found in, because a bare "no digits in the footer"
        rule cannot hold -- a threshold, a currency and a date are all numbers
        that belong in prose.
        """
        html = self.TEMPLATE.read_text(encoding="utf-8")
        for phrase in (
            "43 of 74", "230 sailings", "24 sailings where they disagree",
            "15 to 21", "across 54 vessels", "4&ndash;25%",
            "within &euro;5 nine times", "more than &euro;150",
        ):
            self.assertNotIn(
                phrase, html,
                f"the footer states {phrase!r} as a literal; it is a function "
                "of the committed data and belongs in _stated_figures",
            )

    def test_the_figures_a_fetch_moves_are_all_derived(self):
        """Every helper behind a token answers, on the committed dataset.

        A helper that returns an empty dict renders every figure it feeds as an
        em dash -- honest, and on a full dataset also a sign that its definition
        stopped matching the data it reads. `padi_fees_complete` moving would
        empty `seller_gap` silently, and the page would quietly go from stating
        a comparison to stating nothing.
        """
        from liveaboard.render import (
            berth_count_gaps, build_payload, gear_prices, seller_gap,
            week_dive_counts,
        )

        dataset = published.dataset()
        payload = build_payload(dataset)
        self.assertTrue(gear_prices(dataset).get("vessels"))
        self.assertTrue(week_dive_counts(dataset).get("vessels"))
        self.assertTrue(seller_gap(dataset).get("fee_both"))
        self.assertTrue(seller_gap(dataset).get("fare_pairs"))
        self.assertIsNotNone(berth_count_gaps(payload).get("full_disagree"))

    def test_a_figure_nobody_measured_is_a_dash_not_a_zero(self):
        """"0 vessels publish a price" is a claim; a dataset promoted before
        `entry_bar` existed has not made it. The same rule the rest of this
        project keeps about an unread page."""
        from liveaboard.render import _stated_figures, build_payload

        dataset = published.dataset()
        dataset.entry_bar = {}
        figures = _stated_figures(dataset, build_payload(dataset))
        self.assertEqual(figures["__BAR_BOTH__"], "&mdash;")

    def test_the_footer_names_only_states_the_column_can_reach(self):
        """It advertised "extra, no price", which no vessel has ever produced.

        Naming a state nobody will see is a smaller sin than the reverse and
        still a claim about the data that the data does not support.

        This is the half of this class a token cannot replace: it is about a
        *word* on the page rather than a figure, and no substitution can tell
        you that a state named in prose is one the column never renders.
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

    app.js orders its columns from three lists -- one per breakpoint -- and
    appends anything missing from one, with a console.warn nobody sees unless
    they have devtools open. A column added to COLS and to the widest list only
    looks correct on the laptop it was written on and falls off the right-hand
    edge of a phone, which is the device most people open this page on.

    That is exactly what happened when the "vs PADI" column was added, and it
    reached production. A warning in a console is not a check; this is.

    It was four lists, then three, and it is two. The phone orders and the
    measured fold that built them are gone with the phone table itself: below
    760px the rows are cards, which have no columns to order and nothing to
    fold. What is left is the two orders a table is drawn in, and the rule
    that every column is in both -- a column missing from one is a fact the
    page stops publishing at that width only, which is the hardest kind of gap
    to notice, because the laptop it was written on looks right.

    Where the Total actually lands is a measurement and lives in
    `test_layout.py`; nothing here can see a pixel.
    """

    APP = ROOT / "templates" / "app.js"

    ORDERS = ("ORDER", "COMPACT_ORDER")

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

    def test_the_money_leads_the_descriptive_columns_when_room_is_short(self) -> None:
        """The compact order is what a window narrower than the table gets, and
        the whole of what it changes is that the price block moves in front of
        Trip, Dive sites and Entry bar. The Total off the right-hand edge is
        the failure it exists to prevent, and it would be silent: the row still
        renders."""
        order = self.order("COMPACT_ORDER")
        for behind in ("trip", "sites", "entry"):
            with self.subTest(column=behind):
                self.assertLess(order.index("total"), order.index(behind))

    def test_the_two_orders_hold_the_same_columns(self) -> None:
        """Not merely each complete against COLS -- the same set. A column in
        one and not the other is a fact that appears and disappears with the
        window width, which is worse than one that is always absent."""
        self.assertEqual(sorted(self.order("ORDER")), sorted(self.order("COMPACT_ORDER")))

    def test_the_date_leads_both_orders(self) -> None:
        """It is what a row is looked up by and the sort the table opens on, so
        it is the one identifier that stays in front of everything at every
        width -- and it is the first of the two pinned columns."""
        for name in self.ORDERS:
            with self.subTest(order=name):
                self.assertEqual(self.order(name)[0], "start")

    def test_every_zone_is_contiguous_in_both_orders(self) -> None:
        """The band over the header is built from runs of `zone`, so a zone
        broken into two runs prints its label twice -- "The trip" over the
        dates and again over the reefs, with the bill between them. Nothing
        throws; the header just starts lying about what it is naming."""
        source = self.source()
        start = source.index("var COLS = [")
        end = source.index("\n  ];", start)
        block = source[start:end]
        zones = dict(
            re.findall(r'\{\s*k:\s*"([^"]+)".*?zone:\s*"([^"]+)"', block, re.S | re.M)
        )
        self.assertGreaterEqual(len(zones), 10, f"zones did not parse: {zones}")
        for name in self.ORDERS:
            runs: list[str] = []
            for key in self.order(name):
                zone = zones.get(key)
                self.assertIsNotNone(zone, f"{key} has no zone, so the band cannot name it")
                if not runs or runs[-1] != zone:
                    runs.append(zone)
            with self.subTest(order=name):
                self.assertEqual(len(runs), len(set(runs)), f"{name} splits a zone: {runs}")


class TestPinnedColumns(unittest.TestCase):
    """A pinned group is only as good as the rule that closes it.

    `pinned()` says how many leading columns are frozen, and the count changes
    with the breakpoint -- two where there is a table, and none below 760px,
    where the rows are cards and there is nothing to pin. Each count above
    zero paints a `pins-N` class on the body, and the CSS draws the strong
    edge on `.pins-N .stickN`. Miss one and nothing throws: the columns still
    freeze, they just stop saying where the identity ends, which is the
    difference between a group and two columns that happen to be adjacent.

    Zero is a real answer and is checked as one: it must paint no class and
    close no group, or a card list would carry the table's edges.
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
        self.assertTrue(all(0 <= n <= 4 for n in counts), counts)

    def test_every_count_paints_its_body_class(self):
        source = self.APP.read_text(encoding="utf-8")
        for n in self.counts():
            if n == 0:
                continue
            self.assertIn(f'classList.toggle("pins-{n}"', source)

    def test_no_class_is_painted_for_a_count_pinned_cannot_return(self):
        """The other direction, and it is the one a refactor breaks: dropping a
        count from `pinned()` leaves the class it painted behind, so the body
        can carry `pins-4` with four columns' worth of edges and two columns
        pinned."""
        source = self.APP.read_text(encoding="utf-8")
        painted = {int(n) for n in re.findall(r'classList\.toggle\("pins-(\d+)"', source)}
        self.assertEqual(painted, {n for n in self.counts() if n},
                         "a pins-N class is painted for a count pinned() cannot return")

    def test_every_count_closes_its_group(self):
        css = self.CSS.read_text(encoding="utf-8")
        for n in self.counts():
            if n == 0:
                continue
            self.assertIn(
                f".pins-{n} .stick{n}", css,
                f"pinned() can return {n}, but no rule closes a group of {n}",
            )

    def test_every_pinned_column_has_an_offset(self):
        css = self.CSS.read_text(encoding="utf-8")
        for n in range(1, max(self.counts()) + 1):
            self.assertIn(f".stick{n}", css)

    def test_no_offset_survives_a_column_that_is_no_longer_pinned(self):
        """`--st3` and `--st4` were the widths of Return and Guests, which are
        second lines inside their neighbours now. A stale offset variable is a
        left edge computed from a column that is not there."""
        css = self.CSS.read_text(encoding="utf-8")
        highest = max(self.counts())
        for n in range(highest + 1, 6):
            with self.subTest(column=n):
                self.assertNotIn(f"--st{n}:", css)


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
    #:
    #: `fee_labels` sat here for the reason the list exists to make arguable --
    #: every label on the panel is built from it in Python, so nothing needed
    #: the table itself. A subsumed line does: it names the bundle that covers
    #: it, and that name has to be the one the covering row prints. So the
    #: exception is gone rather than reworded, and the list is empty again.
    UNREAD: dict[str, str] = {}

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
        at all is barely different from being gone. Two orders now: below 760px
        there is no table to order, and the card carries the bar in its own
        meta line."""
        source = self.app()
        for name in ("var ORDER", "var COMPACT_ORDER"):
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
        self.assertRegex(app, r'"entry",\s*"sellers"\]',
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
        settle = app.split("function settleHash(", 1)[1].split("\n  }", 1)[0]
        self.assertIn("window.location.replace", settle,
                      "a declined view stays in the address bar")
        # Scoped to `settleHash`: elsewhere on the page assigning the hash is
        # right, because a boat name in a change report *navigating* to that
        # boat's sailings is a place a reader should be able to come back from.
        self.assertNotIn("window.location.hash =", settle,
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
        # With `preventScroll`, because focusing asks the browser to bring the
        # element into view and the only box it can move is the shell -- which
        # is the window, does not scroll, and on iOS went with it. The focus is
        # what a screen reader needs and stays; the scroll never had a reason.
        self.assertRegex(app, r"\.focus\(\{ preventScroll: true \}\)",
                         "nothing moves focus into the view that appeared")

class TestTheOffersPanelNamesItsSellers(unittest.TestCase):
    """Rules that live in `app.js` and have no Python to test, so the guard is
    that the built page still contains them.

    Two of them, and they are the same rule twice. **One date over two sellers
    dates half of them wrong**: `berths_read` and `padi_berths_read` are two
    crawls two days apart, and the sale marks stamped the first over both --
    on 124 rows resting partly on the second and 2 resting entirely on it.
    And **neither seller's sales may go missing**: they were drawn as two
    tables sharing no column, ten boats in each with no way to read across.

    That was fixed by joining them onto one boat-keyed row, and the join was
    itself wrong (#145): what the two publish are not two halves of one record.
    liveaboard.com strikes a list price through on a booking page, so its
    evidence is a *run* of that boat's discounted sailings; PADI publishes a
    named offer against one sailing. So it is one table with a row per sale,
    sorted by boat -- both sellers in it, a boat both of them discount getting
    two rows, and no row asserting a join nobody made.
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
        self.assertIn("function salesTable(", app)
        for gone in ("function fleetTable(", "function dealsTable(",
                     "function offersTable("):
            self.assertNotIn(
                gone, app,
                f"{gone} is back; the two sellers' offers are two tables again",
            )

    def test_neither_seller_s_sales_can_go_missing(self):
        """The union, not the intersection.

        Ten boats appear in both books today, so a table keyed on either
        seller's list passes every test and silently drops the other's -- a
        PADI offer for a boat no ladder caught, out of the panel headed "what
        is on sale", which is this site's own reported failure in somebody
        else.

        Structural now rather than a branch that has to remember: the rows are
        appended from each book in turn and neither pass can skip an entry.
        """
        app = self.source()
        block = re.search(r"function salesRows\((.*?)\n  \}", app, re.S)
        assert block, "salesRows() not found in app.js"
        body = block.group(1)
        self.assertIn("(deals.on_sale || {}).boats || []", body)
        self.assertIn("(deals.offers || []).forEach", body)

    def test_a_padi_offer_states_the_sailing_and_not_a_window(self):
        """PADI publishes no validity dates with an offer -- only the sailing it
        advertises it against -- so From and To on such a row are one sailing.
        Printed under the same headings as a real discount run, that has to say
        which it is or the row claims a window the source never stated."""
        app = self.source()
        table = app.split("function salesTable(", 1)[1].split("\n  }", 1)[0]
        self.assertIn("if (r.exemplar)", table)
        self.assertIn("It publishes no dates for the offer itself", table)

    def test_padi_speaking_twice_about_one_sale_is_one_row(self):
        """A run row's `sellers` comes from the departures, and both books feed
        those -- so a boat PADI both marks down and advertises had its sale
        stated twice, the second time as one sailing nested inside the first's
        window at the same rate. All eight offers on the published fleet did
        it. `promote` decides which offers are restatements (`in_run`) and puts
        the campaign name on the run itself; the page's job is to skip the
        second row and draw the name where it landed."""
        app = self.source()
        block = re.search(r"function salesRows\((.*?)\n  \}", app, re.S)
        assert block, "salesRows() not found in app.js"
        body = block.group(1)
        self.assertIn(
            "if (o.in_run) return;", body,
            "a folded offer is drawing its own row again, nested inside the "
            "run that already states it",
        )
        self.assertIn("names: r.offers || []", body)

    def test_a_run_s_two_dates_are_a_window_and_say_so(self):
        """`promote` splits a run where the discount stops, so From and To
        bound a span in which every sailing is cut. They used to be the first
        and last of everything discounted -- "03 May to 05 Jul" over a boat
        whose four June weeks were at full price -- and the hover was the only
        thing that admitted it."""
        app = self.source()
        table = app.split("function salesTable(", 1)[1].split("\n  }", 1)[0]
        self.assertIn("Every sailing this boat runs between these", table)
        self.assertNotIn(
            "these are the first and last of them", table,
            "the two dates are the ends of a list again rather than a window",
        )

    def test_the_strip_counts_boats_rather_than_rows(self):
        """`on_sale.boats` is one row per run, so a boat whose discount stops
        and starts again is two of them: counting rows said 22 boats over a
        fleet of 19 on sale."""
        app = self.source()
        strip = app.split("function saleStrip(", 1)[1].split("\n  }", 1)[0]
        self.assertNotIn(
            "var boats = (sale.boats || []).length", strip,
            "the boat count is a row count again",
        )
        self.assertIn("seen[row.boat] = 1", strip)

    def test_the_panel_states_what_it_could_not_read(self):
        """The counts stay on the page; the three paragraphs explaining them do
        not (#145). The invariant is that the panel states what it could not
        read -- a ladder dropped as stale, a sailing with no list price, a
        banner the seller contradicts -- and a muted line of counts states it.
        The reasoning is a hover per count."""
        app = self.source()
        self.assertIn("function coverageNote(", app)
        for field in ("dropped", "unread", "banner_unsupported"):
            with self.subTest(field=field):
                self.assertIn("coverage." + field, app)

    def test_an_unmatched_vessel_is_named_where_its_reader_is(self):
        """Named, not counted -- and to the maintainer rather than the visitor.

        The query asks PADI for the USA as well as Egypt because three Egyptian
        boats are filed there, and the same breadth returns Caribbean ones, so
        an unmatched vessel is ordinarily a boat from another sea and
        occasionally an Egyptian one nothing here has paired yet. Only a name
        tells those apart, which is why the name may not be dropped. But the
        reader of the sale view is shopping the sales: a list of boats the page
        does not carry is the pipeline talking past them. So `promote` keeps
        the names and `cli` prints one `::warning::` each, and the panel draws
        none of it.
        """
        app = self.source()
        self.assertNotIn(
            "Not carried here", app,
            "the unmatched vessels are being drawn on the sale view again",
        )
        cli = (ROOT / "src" / "liveaboard" / "cli.py").read_text(encoding="utf-8")
        self.assertIn(
            "::warning::PADI deal on a vessel this site does not carry:", cli,
            "nothing names an unmatched vessel any more, on the page or off it",
        )
        self.assertIn("row['name']", cli, "the warning counts rather than names")

    def test_the_trips_on_sale_read_down_in_sailing_order(self):
        """By date, and the date first.

        Deepest-first was the wrong order for a list somebody reads down: a
        discount on a week they cannot take is not a cheaper trip, so the date
        is the first thing checked and the sort and the first column have to
        agree about it. Depth is the tie-break inside a day, which keeps the
        old order where the dates cannot separate two rows.
        """
        app = self.source()
        block = app.split("function tripsOnSale(", 1)[1].split("\n  }", 1)[0]
        self.assertIn("a.d.start < b.d.start ? -1 : 1", block)
        self.assertNotIn(
            "if (a.pct !== b.pct) return b.pct - a.pct;", block,
            "the discount is the primary sort again, not the sailing date",
        )
        table = app.split("function tripsOnSaleTable(", 1)[1].split("\n  }", 1)[0]
        self.assertIn(
            '["Sails", "Boat", "Trip", "Off", "Was", "Now"]', table,
            "the columns are not date-first, then what came off and the two "
            "prices in the order the cut happened",
        )

    def test_a_lone_advertised_price_says_whether_two_sellers_quote_it(self):
        """One figure in that column means three different things -- both
        sellers quote it, PADI quotes it and cannot be totalled, or nobody
        else was asked -- and they printed identically.

        It said so in a `2 sellers` mark beside the figure and now says so on
        the figure, because three columns had each grown that same phrase for
        three different facts. The mechanism is what moved; the distinction is
        what may not be lost, so this names the branch rather than the mark.
        """
        app = self.source()
        self.assertIn("function advertisedNote(", app)
        self.assertIn("advertisedNote(d, row)", app)
        # The two cases it tells apart, still told apart: PADI quoting the same
        # figure, and PADI quoting a different one.
        self.assertIn("PADI Travel advertises this berth at the same price", app)
        self.assertIn("PADI Travel advertises this berth at \" + eur(d.padi)", app)
        # And it reaches the reader: a title on the figure, not a dropped string.
        self.assertIn("var why = advertisedNote(d, row);", app)
        self.assertIn('figure = \'<span title="\'', app)


class TestTheRenderedPageHasNoClockInIt(unittest.TestCase):
    """`build_payload` may read no date but the one the data was read on.

    `render` is documented as pure: the same committed inputs must produce the
    same page tomorrow, which is what lets `TestThePageIsWhatItsDataBuilds`
    normalise the build stamp and compare the rest byte for byte. One field
    was not — `fx.age_days()` and `is_stale()` both default their reference
    date to `date.today()`, so the payload counted the rate's age to *now*.

    It cost a day. On the first midnight after a build, `site/index.html`
    differed from the committed one in a real field 135 kB into a
    single-line payload: a dirty tree with nothing behind it, a rebuild
    committed as if it were data, `--merge` refusing until somebody did, and
    this file's own byte-comparison red on a checkout nobody had touched.

    The reference is `generated` — the day the crawl read the prices. The rate
    and the fares it converts were assembled together, so the gap between them
    is a fact about the dataset rather than about when the page is opened, and
    it holds still because the dataset does.
    """

    def payload(self, as_of: str, generated: str) -> dict:
        raw = json.loads(json.dumps(MINIMAL))
        raw["generated"] = generated
        raw["fx"] = {
            "display_currency": "EUR",
            "as_of": as_of,
            "source": "European Central Bank euro foreign exchange reference rates",
            "rates": {"USD": 0.92},
        }
        return build_payload(Dataset.from_dict(raw))

    def test_the_rate_age_is_counted_to_the_day_the_data_was_read(self):
        fx = self.payload("2026-08-20", "2026-08-27")["meta"]["fx"]
        self.assertEqual(fx["age_days"], 7)

    def test_nothing_in_the_payload_asks_what_day_it_is(self):
        """The direct statement of the property, since one caller is enough.

        Asserting the age instead would pass again the day some other field
        starts reading the clock, and the failure mode is a page that differs
        from itself overnight rather than an error anywhere.
        """
        import liveaboard.money as money_mod
        import liveaboard.render as render_mod

        class NoClock(date):
            @classmethod
            def today(cls):
                raise AssertionError(
                    "build_payload read the wall clock; the page it produces "
                    "is now different tomorrow from what it is today"
                )

        # `money` as well as `render`, because that is where the clock was:
        # `age_days()` defaults its own reference, so a guard watching only the
        # caller's module is green on the very bug it is named for.
        with unittest.mock.patch.object(render_mod, "date", NoClock), \
                unittest.mock.patch.object(money_mod, "date", NoClock):
            self.payload("2026-08-20", "2026-08-27")

    def test_a_rate_that_stopped_moving_is_still_reported_stale(self):
        """Freezing the reference must not freeze the signal.

        `as_of` stands still when the fetch fails and `generated` goes on
        moving with every crawl, so the gap opens exactly as it did against
        today's date -- which is the whole reason this reference works.
        """
        self.assertFalse(self.payload("2026-08-24", "2026-08-27")["meta"]["fx"]["stale"])
        self.assertTrue(self.payload("2026-08-01", "2026-09-04")["meta"]["fx"]["stale"])


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


class TestTheHideSoldOutChipCarriesItsCount(unittest.TestCase):
    """The last filter chip with no number on it, and the one whose effect was
    hardest to guess: 162 of 1,122 published departures are sold out, so
    pressing it removes about one row in seven and nothing said so until you
    pressed it and counted what moved (#141).

    Counted the way the On sale chip is -- live, against what the *other*
    filters leave. The issue asked for a season total and for the chip to
    vanish at zero, quoting rules that were true of the On sale chip before
    #129 replaced both: every other number on this page answers "what if I
    picked this too?", and "nothing here is sold out" is an answer a
    disappearing control cannot give. The issue's own headline -- the way every
    other filter carries one -- is what settled it.
    """

    APP = ROOT / "templates" / "app.js"

    def setUp(self) -> None:
        self.app = self.APP.read_text(encoding="utf-8")
        self.chip = self.app.split('document.getElementById("hideSold")', 1)[1]

    def test_sold_out_is_a_stated_sold_out_and_nothing_else(self) -> None:
        """`bookable` is "anything but a stated sold-out -- unknown is not a
        refusal", so `!bookable` keeps that distinction. Counting "not
        available" would fold the limited sailings into a figure they are not
        part of."""
        self.assertIn(
            'var soldOutCount = D.departures.filter(function (d) { return !d.bookable; })',
            self.app)
        self.assertNotIn('availability === "sold_out"', self.chip.split("addEventListener")[0])

    def test_the_filter_can_exclude_itself(self) -> None:
        """Without the skip, switching the chip on takes its own count to zero
        and the way back disappears."""
        self.assertIn('if (skip !== "soldout" && state.hideSoldOut && !dep.bookable)',
                      self.app)
        self.assertIn('passes(dep, D.itineraries[dep.itinerary_id], "soldout")', self.chip)

    def test_it_recounts_with_every_other_bank(self) -> None:
        """Through `BANKS`, not a count taken once at load: any other mechanism
        is a second one to keep in step."""
        self.assertIn("BANKS.push(", self.chip)
        self.assertIn('soldOut.textContent = "Hide sold out " + n;', self.chip)

    def test_the_number_is_what_pressing_it_leaves(self) -> None:
        """What every other chip on this page means by a number: On sale says
        how many rows you get, a month chip says how many rows you get.

        This shipped once counting what it *removes* -- the sold-out sailings
        themselves -- on the reading that a control labelled "hide" should be
        sized by what it hides. That makes it the one chip whose number has to
        be subtracted from something before it means anything, and it puts the
        largest number on the emptiest result.
        """
        self.assertIn("if (!dep.bookable) return;", self.chip,
                      "the chip is counting what it hides rather than what "
                      "pressing it leaves")

    def test_a_count_of_zero_keeps_the_chip_rather_than_hiding_it(self) -> None:
        """Dimmed and unclickable, saying 0 -- still clickable while it is
        switched *on*, so the way out never disappears.

        Zero means something different here than on the other chips now that
        the number is what pressing it leaves: every trip these filters leave
        is sold out, and pressing would empty the table. The title says that
        rather than the other chips' "nothing here matches".
        """
        self.assertIn("var dead = n === 0 && !state.hideSoldOut;", self.chip)
        self.assertIn("soldOut.disabled = dead;", self.chip)
        self.assertIn("would empty ", self.chip,
                      "zero here is 'everything left is sold out', which is "
                      "not what the other chips' zero means")

    def test_a_checkout_with_nothing_sold_out_is_not_offered_the_chip(self) -> None:
        """Unlike On sale it was rendered unconditionally, so it needed the
        gate rather than inheriting one."""
        page = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="hideSold" aria-pressed="false" hidden', page)
        self.assertIn("if (soldOutCount) {", self.app)


class TestTheSaleViewIsDesignedRatherThanRelocated(unittest.TestCase):
    """The sale content was moved out of a `details` and not laid out (#142).

    It read as the old panel in a new place -- one run-on sentence, one
    alphabetical fleet table, one list of moves -- and used about half of the
    richest data on the site. Figures first, then the discounts by boat --
    two things, because they answer two questions: what the sale *is*, and
    which boats and weeks carry it.

    It was three, and the middle one was wrong: a table of discount brackets,
    one row per percentage. Nobody books by bracket, and a row grouping
    sailings that share only a rate made every column on it an aggregate over
    an arbitrary set -- a date span that was the whole season, a price range
    across five boats, a seller list saying only that at least one sailing
    somewhere in the bucket came from each (#147). The range across the
    brackets was the one fact in it and is a figure in the strip.
    """

    APP = ROOT / "templates" / "app.js"

    def setUp(self) -> None:
        self.app = self.APP.read_text(encoding="utf-8")

    def test_the_view_opens_on_figures_rather_than_a_sentence(self) -> None:
        """A reader skims a strip and reads a sentence, and these are numbers
        to be skimmed. The reading date is one of the figures because a
        discount is a claim with a date and this one can end overnight."""
        self.assertIn("function saleStrip(", self.app)
        strip = self.app.split("function saleStrip(", 1)[1].split("\n  }", 1)[0]
        self.assertIn('"sailings cut"', strip)
        self.assertIn('"off"', strip)
        self.assertIn('"read"', strip)

    def test_the_panel_states_what_is_on_sale_and_not_what_moved(self) -> None:
        """The one-line "2 moved on liveaboard.com since 28 Aug · 6 moved on
        padi.com since 30 Aug" went with the two blocks it counted (#146).

        Not in the issue's cut list, and neither is it in #145's -- so it would
        have survived both by omission, which is why it is asserted here. It
        restated the two headings verbatim, dates included, from a strip whose
        job is what is on sale today; a reader who wants the movement has a
        view for it, and a signpost that repeats the thing it points at is the
        split this move was made to close.
        """
        self.assertNotIn("function movedLine(", self.app)
        self.assertNotIn("function dealsSummary(", self.app,
                         "the summary sentence outlived the fold it was for")
        self.assertNotIn("sale-moved", self.app)

    def test_the_depth_is_a_range_and_not_a_table_of_brackets(self) -> None:
        """The rates survive as one figure; the bracket rows do not.

        Asserted as an absence as well as a presence, because this is the
        deletion and a deletion is what a later rebuild of the panel is most
        likely to undo by accident (#145 rebuilds around it).
        """
        self.assertIn("function discountRates(", self.app)
        rates = self.app.split("function discountRates(", 1)[1].split("\n  }", 1)[0]
        self.assertIn("return b - a;", rates, "the rates are not deepest first")
        self.assertNotIn("function spreadTable(", self.app)
        self.assertNotIn("How deep the cuts go", self.app)
        for column in ('"Sailings", "Boats"', '"Cut by"'):
            self.assertNotIn(column, self.app, "the bracket table is back")

    def test_a_sailing_with_no_stated_rate_is_not_a_dash_and_not_a_nought(self) -> None:
        """Two sailings are marked down by a seller that stated no percentage
        against the fare this page prints. That is the honest edge of the data
        -- it is not a dash, and it is certainly not 0%.

        The bracket table carried this as a row of its own and the row went with
        the table. `saleTag` is the surviving carrier and the better one: the
        claim is about one sailing, so it belongs on that sailing's row.
        """
        tag = self.app.split("function saleTag(", 1)[1].split("\n  }", 1)[0]
        self.assertIn("if (!d.sale.pct)", tag)
        self.assertIn(">on sale<", tag)
        rates = self.app.split("function discountRates(", 1)[1].split("\n  }", 1)[0]
        self.assertIn("dep.sale.pct", rates,
                      "a sale with no stated rate is being counted as a rate")

    def test_the_rate_is_printed_with_the_fare_it_came_off(self) -> None:
        """"−15%" off a figure the reader has to work out is a claim they
        cannot check, which is the thing this site reports in other people.
        The fare was in the tooltip on 236 of the 237 discounted sailings and
        nowhere on the row.

        Printed, and **stated rather than reconstructed**: it is the seller's
        own struck-through list price. Dividing the fare by the rate would
        round a number into existence and put this site's arithmetic where an
        operator's price belongs — and it would answer for the one sailing
        that has no `was` precisely because no seller stated one.
        """
        tag = self.app.split("function saleTag(", 1)[1].split("\n  }", 1)[0]
        self.assertIn('class="sale-was"', tag,
                      "the rate is printed with no fare beside it")
        self.assertIn("d.sale.was", tag)
        css = (ROOT / "templates" / "style.css").read_text(encoding="utf-8")
        self.assertIn(".sale-was", css, "the struck fare has no styling")
        for invented in ("1 - d.sale.pct", "(1 - ", "/ (1-", "pct / 100",
                         "pct/100"):
            with self.subTest(invented=invented):
                self.assertNotIn(invented, self.app,
                                 "a list price is being computed from the rate")

    def test_the_saving_is_never_was_minus_the_quoted_price(self) -> None:
        """`sale.was` is already converted to the display currency and the
        payload's `price` is the sailing's own, so subtracting them is nonsense
        on every row quoted in dollars. `base` is the converted one.

        Asserted over the whole file rather than over the one function that did
        the arithmetic. The rule is about the two fields and not about a
        carrier: the function it was written against has gone, and the next
        place a saving is computed is as able to get it wrong.
        """
        for wrong in ("sale.was - d.price", "sale.was - dep.price",
                      "d.price - d.sale.was", "dep.price - dep.sale.was"):
            self.assertNotIn(wrong, self.app,
                             "a saving is being taken against the unconverted price")

class TestTheBillOpensFromItsOwnColumn(unittest.TestCase):
    """The per-row dropdown became a panel on the Mandatory fees cell (#149).

    Every row carried a `+` that opened a full-width detail row. It pushed every
    row below it down to answer a question about one row, and it cost 26px of
    pinned width on all 1,122 rows including the ones nobody ever opened.

    **The whole breakdown travels, not a summary.** The fee table with its
    included lines at zero, the caveat that applies, and the second seller's
    bill with the three-state wording that keeps it from reading as a
    comparison between two figures that are not the same kind of number. A
    tooltip holding only the line items would be a total claimed on part of a
    disclosure, which is the failure this site exists to report in other
    people. What gives instead is height: the panel caps and scrolls inside
    itself.

    **The entry bar is not a fee and does not go in it.** It was the head of
    that dropdown -- deliberately, because whether a diver may board at all is
    prior to what boarding costs -- and it now opens from the Entry bar column,
    which is the column it is about.

    **One mechanism, three panels.** The cabin ladder already did
    hover-to-peek, click-to-pin, focus-to-open and Escape-to-close, with the
    click half kept because hover does not exist on a phone and this page is
    built to work on one in a dive shop. Writing a second copy of that would
    have been three implementations of one interaction drifting apart on the
    parts that are easy to get wrong.
    """

    APP = ROOT / "templates" / "app.js"
    CSS = ROOT / "templates" / "style.css"
    PAGE = ROOT / "templates" / "index.html"

    def setUp(self) -> None:
        self.app = self.APP.read_text(encoding="utf-8")
        self.css = self.CSS.read_text(encoding="utf-8")
        self.page = self.PAGE.read_text(encoding="utf-8")

    def test_the_dropdown_and_its_column_are_gone(self) -> None:
        for token in ("state.open", 'class="expand"', "tr.detail",
                      'class="expander"'):
            self.assertNotIn(token, self.app, f"{token} outlived the dropdown")
        self.assertNotIn("--st0", self.css,
                         "the pinned group still reserves the expander's width")
        self.assertNotIn("tr.detail", self.css)

    def test_the_row_mark_moved_to_the_cell_that_is_pinned_now(self) -> None:
        """The mark is a bar down the pinned *first* cell, which is the one
        column on screen at every scroll position and every width. That cell
        was the expander, and the expander was the 26px being reclaimed -- so
        without this the column went and the mark went quietly with it."""
        self.assertIn("tbody tr.row.marked .stick1", self.css)
        self.assertIn("var(--row-marked-edge)", self.css)

    def test_the_whole_breakdown_is_in_the_panel(self) -> None:
        """Every line, not a summary of them.

        Asserted as two fragments rather than one call, because the panel now
        holds the lines in a local: the estimate warning is built from the
        same list the table is, so that the figure in the sentence and the
        figure in the cell cannot come from two readings. What this guards is
        that the rows come from the departure's own `linesFor` and go through
        `feeRows` — a summary passed here would fail both halves.
        """
        panel = self.app.split("function billPanel(", 1)[1].split("\n  }", 1)[0]
        self.assertIn("linesFor(row.d)", panel)
        self.assertIn("feeRows(", panel)
        self.assertIn("caveat", panel)
        self.assertIn("row.i.padi_lines", panel)
        self.assertNotIn("entryBar(", panel,
                         "the entry bar is back inside the fee panel")

    def test_the_entry_bar_opens_from_its_own_column(self) -> None:
        self.assertIn('<dialog id="entryPanel"', self.page)
        self.assertIn('class="entry-open"', self.app)
        self.assertIn("entryBar(itin)", self.app)

    def test_the_panels_are_dialogs_and_the_browser_owns_them(self) -> None:
        """One mechanism, three panels, and most of it is the platform's (#78).

        What was here was a fixed-position div placed by hand, raised by a
        z-index, dismissed by four listeners, over a list that stayed live
        underneath it — the modal contract written out longhand, missing the
        one clause that matters. `showModal()` supplies all of it: the top
        layer, the inert page behind, Escape, the backdrop, the focus trap.

        So what is asserted here is that the page really does delegate. A
        hand-rolled overlay coming back would show up as these tokens, and it
        would pass every behavioural test on a desktop.
        """
        self.assertEqual(
            self.app.count("function panelDialog("), 1,
            "there is more than one implementation of the panel interaction")
        self.assertEqual(self.app.count("panelDialog(document.getElementById("), 3)
        self.assertEqual(self.page.count("<dialog id="), 3,
                         "a panel host is not a <dialog>")
        wiring = self.app.split("function panelDialog(", 1)[1].split(
            "\n  panelDialog(", 1)[0]
        self.assertIn("dialog.showModal()", wiring,
                      "the pinned panel is not modal, so the rows behind it "
                      "are still live")
        for gone in ("position: fixed", 'setAttribute("hidden"',
                     "host.hidden", "z-index"):
            with self.subTest(gone=gone):
                self.assertNotIn(gone, wiring,
                                 gone + " — the hand-built overlay is back")

    def test_the_peek_is_the_same_dialog_shown_the_other_way(self) -> None:
        """Hover stays, because a diver comparing cabin ladders or fee books
        wants them without a click each time — but as `show()` on the same
        element, told apart in the stylesheet by `:modal`. Two implementations
        of one panel is what the last mechanism spent itself on."""
        wiring = self.app.split("function panelDialog(", 1)[1].split(
            "\n  panelDialog(", 1)[0]
        self.assertIn("dialog.show()", wiring)
        self.assertIn(":modal", self.css, "nothing separates the two states")
        # Hover is asked twice: the device, then the gesture. `pointerover`
        # fires for a finger on touchstart, before the drag after it is known
        # to be a drag, and a card's meta row is three of these buttons.
        self.assertIn("(hover: hover) and (pointer: fine)", self.app)
        self.assertIn('event.pointerType !== "mouse"', wiring)

    def test_focus_no_longer_opens_a_panel(self) -> None:
        """Every trigger is a `<button>`, so Enter and Space are already a
        click: the keyboard was reachable through the press path the whole
        time. Opening on `focusin` bought nothing and cost a third piece of
        state — and a *modal* opened by focus would trap a keyboard user the
        moment they tabbed across a row."""
        wiring = self.app.split("function panelDialog(", 1)[1].split(
            "\n  panelDialog(", 1)[0]
        # The listener, not the word: a prose "focusing the dialog" is not a
        # focus handler, and a guard that cannot tell them apart goes red on
        # a comment.
        for gone in ('addEventListener("focusin"', 'addEventListener("focusout"'):
            with self.subTest(gone=gone):
                self.assertNotIn(gone, wiring)

    def test_each_trigger_is_a_button_and_says_it_opens_a_dialog(self) -> None:
        """The dropdown's `+` was a `<button>` and accessible by default. A cell
        that opens a panel may not be a step back from it."""
        for cls in ("fees-open", "entry-open"):
            with self.subTest(cls=cls):
                markup = self.app.split('class="' + cls + '"', 1)[1][:200]
                self.assertIn('type="button"', markup)
                self.assertIn('aria-haspopup="dialog"', markup)
                self.assertIn('aria-expanded="false"', markup)

    def test_the_panel_is_built_on_demand_and_not_per_departure(self) -> None:
        """Nothing on this page is lazily fetched, so anything written per row
        ships 1,122 times. One host in the markup, filled from the payload when
        a cell is used -- and the row rebuilt rather than cached, because the
        bill depends on the toggles."""
        self.assertEqual(self.page.count('id="feePanel"'), 1)
        self.assertIn("function rowFor(", self.app)
        rebuilt = self.app.split("function rowFor(", 1)[1].split("\n  }", 1)[0]
        self.assertIn("metricsFor(dep)", rebuilt)

    def test_opening_one_panel_closes_the_others(self) -> None:
        """Two panels anchored to two cells of the same row would overlap, and
        the second would read as belonging to whichever cell it landed on."""
        wiring = self.app.split("function panelDialog(", 1)[1].split(
            "\n  panelDialog(", 1)[0]
        self.assertIn("function others()", wiring)
        self.assertIn("panel.shut()", wiring)

    def test_a_wide_table_scrolls_inside_the_panel_and_not_the_panel(self) -> None:
        """The fee table needs 460px for its columns and the panel is narrower
        than that on a phone. `overflow` on the table itself does not do it --
        `min-width` wins and the table overflows, taking the panel's own header
        sideways with it."""
        self.assertIn('class="fee-scroll"', self.app)
        self.assertIn(".fee-scroll { overflow-x:auto", self.css)
        # Capped and scrolling. The cap is written twice -- `vh` then `dvh` --
        # because on a phone `vh` is a height the reader cannot see all of, so
        # the assertion is that the panel is bounded at all rather than that it
        # is bounded in one unit. The scroll box is `.pbody`, which is why the
        # close control above it cannot scroll away with the bill.
        self.assertRegex(self.css, r"max-height: 85vh; max-height: 85dvh")
        self.assertRegex(self.css, r"\.pbody \{[^}]*overflow: auto")


class TestTheTotalStatesWhatItIsTheSumOf(unittest.TestCase):
    """The bar under the Total came off, and the figures it encoded went in its
    place (#148) -- not a deletion leaving the cell with the total alone.

    The bar said two things. Its *length* was this total against the dearest
    total on screen, which is a comparison down the column that the table's own
    sort already answers; nothing replaces it. Its two *segments* were the
    advertised fare's share and the required extras on top of it, which is the
    whole argument of this site expressed as a proportion, and the part a reader
    loses if the graphic simply goes.

    So the split is printed as money rather than as a percentage: a percentage
    re-introduces the proportion being removed.
    """

    APP = ROOT / "templates" / "app.js"
    CSS = ROOT / "templates" / "style.css"

    def setUp(self) -> None:
        self.app = self.APP.read_text(encoding="utf-8")
        self.css = self.CSS.read_text(encoding="utf-8")

    def test_the_bar_is_gone_from_the_row_and_the_stylesheet(self) -> None:
        for token in ("barMax", "BAR_TRACK", "BAR_TITLE", '"anchor"'):
            self.assertNotIn(token, self.app, f"{token} outlived the bar")
        self.assertNotIn(".anchor", self.css, "the bar's rules are still here")

    def test_the_split_is_the_two_figures_and_not_a_proportion(self) -> None:
        """`d.base` and the extras on top, both already in hand. A percentage is
        more compact and is the thing being removed."""
        self.assertIn('class="split"', self.app)
        cell = self.app.split('{ k: "total"', 1)[1].split("} },", 1)[0]
        self.assertIn("sellerSpan(b.baseLo, b.baseHi)", cell)
        self.assertIn("sellerSpan(b.laterLo, b.laterHi)", cell)
        self.assertNotIn("advertised.toFixed", cell,
                         "the split is back to being a proportion")

    def test_the_split_is_a_span_wherever_the_total_is_one(self) -> None:
        """Each end of `best` is a whole bill, so the split spans exactly where
        the total spans. One split under a ranged total would claim a precision
        the row does not have."""
        cell = self.app.split('{ k: "total"', 1)[1].split("} },", 1)[0]
        for one_ended in ("b.bill.base", "m.total - d.base", "m.total - m.base"):
            self.assertNotIn(one_ended, cell,
                             "the split is being taken from one end of a span")

    def test_it_is_a_line_of_its_own_and_costs_the_row_nothing(self) -> None:
        """It was absolutely positioned over the cell, and the reason was that
        it was the only second line in the table: nine pixels on every row,
        three of the twelve a laptop held. It is not the only one any more --
        the dates, the boat and the trip each carry one, so the row is two
        lines tall whatever this does -- and out of flow it overlapped the
        total on a ranged row at any narrow width.

        What replaces the old constraint is the reason it existed: the split
        may not make the row taller than the cells beside it, so it is one line
        and it does not wrap."""
        rule = self.css.split(".split {", 1)[1].split("}", 1)[0]
        self.assertNotIn("position:absolute", rule)
        self.assertIn("display:block", rule)
        self.assertIn("white-space:nowrap", rule,
                      "a split that wraps is a third line on the rows that have one")
        self.assertIn(".sub {", self.css,
                      "the second line the other cells carry is gone, so the "
                      "split is back to being the only one")

    def test_the_marker_beside_the_total_is_not_the_split(self) -> None:
        """`+ tips` stays. It is a different claim -- the operator states tips
        are payable and gives no figure -- and it is text rather than a
        graphic, so it was never what came off."""
        self.assertIn("+ tips</span>", self.app)
        self.assertIn('class="plus"', self.app)


class TestRefreshNewsIsReportedInOnePlace(unittest.TestCase):
    """The discount moves belong to the history, not to the sale panel (#146).

    They were drawn inside the sale panel, so one page reported refresh news
    twice: the panel said what is on sale *and* what moved, and the history
    section said what moved about everything else. The panel keeps the first
    half.

    What the move may not do is fold them into `changes.py`'s report, and the
    reason is the clocks. That report is a diff between two committed datasets
    (`HEAD~1`). Each of these is a diff between the last two readings of *one
    seller*, and the two sellers are crawled days apart -- 28 Aug and 30 Aug on
    the dataset this was written against. Neither is the commit boundary and
    neither is the other's, so each keeps its own seller's name and its own
    "since" date. Merging them under one date would date at least one of them
    wrong, which is the mistake the berth counts and the sale marks have each
    already made once.
    """

    APP = ROOT / "templates" / "app.js"
    PAGE = ROOT / "templates" / "index.html"

    def setUp(self) -> None:
        self.app = self.APP.read_text(encoding="utf-8")

    def test_the_moves_are_drawn_under_the_history_and_not_the_panel(self) -> None:
        self.assertIn('<div id="saleMoves"></div>', self.PAGE.read_text(encoding="utf-8"))
        self.assertIn("drawSaleMoves();", self.app)
        moves = self.app.split("function drawSaleMoves(", 1)[1].split("\n  }", 1)[0]
        self.assertIn("salesChanges(shifted)", moves)
        self.assertIn("dealsChanges(deals)", moves)
        panel = self.app.split("function drawDeals(", 1)[1].split("\n  }", 1)[0]
        self.assertNotIn("salesChanges(", panel,
                         "the sale panel is reporting movement again")
        self.assertNotIn("dealsChanges(", panel,
                         "the sale panel is reporting movement again")

    def test_each_block_carries_its_own_seller_and_its_own_date(self) -> None:
        """One date over two books read days apart dates half of them wrong."""
        for name, seller in (("salesChanges", "liveaboard.com"), ("dealsChanges", "padi.com")):
            block = self.app.split("function " + name + "(", 1)[1].split("\n  }", 1)[0]
            self.assertIn("What moved on " + seller + " since ", block)
            self.assertIn("shortDate(", block)

    def test_neither_block_drops_a_row_without_saying_so(self) -> None:
        """The two notes that travelled with them: a sailing read on only one of
        the two days is not a sailing that came off sale, and a listing the
        fetcher could not finish knows nothing about the offers it did not
        reach."""
        sales = self.app.split("function salesChanges(", 1)[1].split("\n  }", 1)[0]
        self.assertIn("not compared", sales)
        self.assertIn("shifted.compared", sales)
        deals = self.app.split("function dealsChanges(", 1)[1].split("\n  }", 1)[0]
        self.assertIn("moved.partial", deals)

    def test_a_day_with_no_discount_anywhere_has_no_sale_view(self) -> None:
        """The moves used to make the view exist on their own. They report
        elsewhere now, so "what is on sale" with nothing on sale is not a
        page -- and `showView` may not leave the address naming it."""
        panel = self.app.split("function drawDeals(", 1)[1].split("\n  }", 1)[0]
        self.assertIn("if (!offers.length && !fleet.length) return false;", panel)


class TestTheChangeReportIsRenderedRatherThanTranscribed(unittest.TestCase):
    """`changes.compare` builds a report out of dataclasses. `changes.render`
    flattened it to column-aligned text, the CLI wrote that text into a
    Markdown file, and the page read the text back out and escaped it into a
    `<pre>` — so everything a real interface needs was built and discarded one
    step before the page, and the visitor got a terminal transcript with boat
    names cut mid-word to fit eighty columns (#143).

    The text stays: it is what a workflow log wants and it is the durable human
    record. What changed is that the same comparison now also comes out as
    data, and neither shape is derived from the other.
    """

    APP = ROOT / "templates" / "app.js"

    def test_the_report_comes_out_twice_from_one_comparison(self) -> None:
        from liveaboard.changes import as_dict, compare

        before = json.loads(published.committed().read_text(encoding="utf-8"))
        report = compare(before, before)
        record = as_dict(report, before="a", after="b")
        self.assertTrue(record["quiet"], "a dataset against itself moved something")
        for key in ("added", "withdrawn", "price_up", "price_down", "fees"):
            self.assertIn(key, record)

    def test_what_the_book_drops_for_weight_is_counted(self) -> None:
        """A browser can expand, so a truncation's honest form there is showing
        the rest behind a control -- but the page is one file with nothing
        fetched lazily, so a report is paid for by every visitor and cannot be
        unbounded. One refresh landed 644 fare moves, 136 KB of the 200 the
        whole week came to. What is cut is counted, never silent."""
        from liveaboard.changes import BOOK_LIMIT, as_dict, compare
        from liveaboard.changes import Departed, Report

        report = Report(added=[
            Departed(f"d{n}", "Boat", "Trip", "2027-05-01", 100.0, "EUR")
            for n in range(BOOK_LIMIT + 7)])
        record = as_dict(report)
        self.assertEqual(len(record["added"]), BOOK_LIMIT)
        self.assertEqual(record["more"]["added"], 7)

    def test_the_page_does_not_read_its_own_prose_back(self) -> None:
        """The step this deletes. `render` reading `CHANGES.md` and escaping it
        is the fallback for a checkout whose refresh predates the book, and
        must not be how a page with the book renders."""
        render_py = (ROOT / "src" / "liveaboard" / "render.py").read_text(encoding="utf-8")
        self.assertIn('if structured:', render_py)
        self.assertIn('return \'<div id="changeLog"></div>\'', render_py)

    def test_every_row_can_reach_the_sailing_it_is_about(self) -> None:
        """Each line names a boat that is a row in the trips table, and none of
        them could be clicked: the panel answering "did this get cheaper" could
        not get you to the thing that did."""
        app = self.APP.read_text(encoding="utf-8")
        self.assertIn("function boatLink(", app)
        link = app.split("function boatLink(", 1)[1].split("\n  }", 1)[0]
        self.assertIn("state.boats.add(name)", link)
        self.assertIn('window.location.hash = "#trips"', link)
        # The bank has to be rebuilt, or a boat in its hidden tail is filtering
        # with no visible chip to take the filter off again.
        self.assertIn("bank.repaint()", link)


class TestOneGate(unittest.TestCase):
    """The bar a person clears before pushing is the bar the workflows run.

    It was not. `.github/actions/checks` listed the steps itself, so the only
    way to run the real list was to push and wait — and anything run locally
    was a second list that could drift from it without either side noticing.
    That is the same failure the action's own comment describes about the five
    workflows, one level up: a copy drifts, and a check that drifts is one
    nobody is really running.

    So there is one definition, `tools/ship.py`, and both call it.
    """

    ACTION = ROOT / ".github" / "actions" / "checks" / "action.yml"
    SHIP = ROOT / "tools" / "ship.py"

    def test_the_shared_action_runs_the_same_command_a_person_does(self) -> None:
        action = self.ACTION.read_text(encoding="utf-8")
        self.assertIn("python3 tools/ship.py", action,
                      "the workflows are running their own list again")
        # And nothing else, or the list has started growing back in two places.
        steps = [line for line in action.splitlines()
                 if line.strip().startswith("run:")]
        self.assertEqual(len(steps), 1, f"the action has grown extra steps: {steps}")

    def test_the_gate_still_holds_everything_it_replaced(self) -> None:
        """Delegating is only safe if nothing was dropped on the way."""
        ship = self.SHIP.read_text(encoding="utf-8")
        for command in ('"liveaboard.cli", "build"',
                        '"liveaboard.cli", "check"',
                        '"liveaboard.cli", "promote", "--check"',
                        '"tools" / "check_seed.py"'):
            self.assertIn(command, ship, f"the gate no longer runs {command}")

    def test_every_test_module_is_in_the_gate(self) -> None:
        """Sharded by module for the parallelism, which is a way to lose one:
        `unittest discover` finds them and a hand-written list would not."""
        ship = self.SHIP.read_text(encoding="utf-8")
        self.assertIn('(ROOT / "tests").glob("test_*.py")', ship,
                      "the gate names its modules instead of discovering them")

    def test_work_does_not_land_on_the_trunk_without_a_branch(self) -> None:
        """Eleven changes went straight onto `main` before the flow had this:
        no branch, nothing to look at before it shipped, and "merge to prod"
        reporting that the work was already there rather than doing anything.

        Not a warning and not a flag to override -- `--push` branches on its
        own, because a default that has to be remembered is not a default.
        """
        ship = self.SHIP.read_text(encoding="utf-8")
        push = ship.split("if not git(\"status\", \"--porcelain\")", 1)[1]
        self.assertIn("if branch == TRUNK:", push,
                      "--push commits wherever it happens to be standing")
        self.assertIn("checkout", push, "--push does not create the branch")
        self.assertIn("def merge(", ship, "there is no way back to the trunk")
        self.assertIn('"--no-ff"', ship,
                      "a fast-forward merge hides that the work was a branch")

    def test_the_merge_gates_against_the_trunk_it_is_merging_into(self) -> None:
        """A branch that was green alone can be red against a trunk that moved
        under it -- and the scheduled data jobs move it several times a day."""
        ship = self.SHIP.read_text(encoding="utf-8")
        merge = ship.split("def merge(", 1)[1]
        self.assertIn('git("rev-list", "--count", f"HEAD..origin/{TRUNK}")', merge)
        self.assertIn("not merging: the gate is red against the merged trunk", merge)

    def test_the_merge_does_not_claim_a_cleanup_it_did_not_do(self) -> None:
        """Deleting the merged branch is allowed to fail -- this environment's
        token returns 403 on it -- and the first version reported success
        anyway, because the call was fire-and-forget."""
        merge = self.SHIP.read_text(encoding="utf-8").split("def merge(", 1)[1]
        self.assertIn('if run_git("push", "-q", "origin", "--delete", branch) != 0:',
                      merge, "the branch delete is fire-and-forget again")

    def test_the_fast_loop_says_it_is_not_the_gate(self) -> None:
        """`--fast` drops the two slowest modules, so it can pass over a real
        failure. A shortcut that does not admit it is one people ship on."""
        ship = self.SHIP.read_text(encoding="utf-8")
        self.assertIn("this is not the full gate", ship)


class TestTheTwoBerthCountsAreDifferentColumns(unittest.TestCase):
    """The CSV carried `spaces_left`, empty on all 1,122 rows, and the model
    property behind it read `block.get("spots")` -- against blocks that have
    been lists since they gained a second seller. It could only ever have
    raised, and nothing noticed because nothing called it.

    Replaced by the two counts under the two names the page prints: only a
    cabin ladder answers *places at this price*, PADI publishes only *berths
    aboard*, and one column could not have carried both honestly.
    """

    def departure(self, berths):
        from datetime import date

        from liveaboard.models import Departure, Money, Provenance
        from liveaboard.taxonomy import SourceKind

        return Departure(
            id="d", itinerary_id="i",
            start=date(2027, 5, 1), end=date(2027, 5, 8),
            price=Money(1000, "EUR"),
            price_provenance=Provenance(SourceKind.SCRAPED, "liveaboard.com"),
            berths=berths,
        )

    def test_it_reads_the_list_shape_the_dataset_actually_ships(self):
        dep = self.departure([[0, 12, [], 20]])
        self.assertEqual(dep.spots_at_advertised, 12)
        self.assertEqual(dep.berths_aboard, 20)

    def test_zero_is_an_answer_and_absent_is_not(self):
        """Nothing left at that price is a fact; nobody stating one is not."""
        dep = self.departure([[0, 0, [], None]])
        self.assertEqual(dep.spots_at_advertised, 0)
        self.assertIsNone(dep.berths_aboard)

    def test_a_seller_with_no_ladder_still_fills_the_second_count(self):
        """PADI states berths aboard and publishes no ladder, which is the
        whole reason these are two columns."""
        dep = self.departure([[1, None, [], 22]])
        self.assertIsNone(dep.spots_at_advertised)
        self.assertEqual(dep.berths_aboard, 22)

    def test_no_block_states_neither(self):
        dep = self.departure([])
        self.assertIsNone(dep.spots_at_advertised)
        self.assertIsNone(dep.berths_aboard)

    def test_the_csv_carries_both_and_not_the_dead_one(self):
        from liveaboard.export import COLUMNS

        self.assertIn("places_at_price", COLUMNS)
        self.assertIn("berths_aboard", COLUMNS)
        self.assertNotIn("spaces_left", COLUMNS)


class TestTheEstimatedGearFigureIsMarkedWhereverItAppears(unittest.TestCase):
    """The one number on this page neither seller published (see
    `pricing.GEAR_ESTIMATE`), asserted against what actually shipped.

    The rule itself is tested over fixtures in `test_pricing.py`. What is
    checked here is the part that makes it publishable rather than merely
    correct: the flag reaches the payload, the exception stays confined to
    gear, the note admits whose figure it is, and the fleet-wide gear
    paragraph in the footer is still computed from the operators' own quotes.
    An estimate the page does not distinguish from a quote is the failure this
    site reports in other people.
    """

    APP = ROOT / "templates" / "app.js"
    CSS = ROOT / "templates" / "style.css"

    def lines(self):
        """Every fee line on every bill the page ships, both sellers'."""
        payload = published.page()
        out = []
        for itinerary in payload["itineraries"].values():
            out.extend(itinerary.get("lines") or [])
            out.extend(itinerary.get("padi_lines") or [])
        return out

    def test_the_rule_fires_on_what_shipped(self):
        """Otherwise every assertion below is vacuously true."""
        estimated = [x for x in self.lines() if x.get("estimated")]
        self.assertTrue(estimated, "no shipped line exercises the gear estimate")

    def test_nothing_but_gear_is_ever_this_projects_own_figure(self):
        for line in self.lines():
            if line.get("estimated"):
                self.assertEqual(
                    line["code"], "gear_rental",
                    "the one exception to 'never invent a price' has spread",
                )

    def test_every_estimated_line_carries_a_figure_and_admits_it_is_ours(self):
        for line in self.lines():
            if not line.get("estimated"):
                continue
            with self.subTest(label=line["label"]):
                self.assertTrue(line.get("has_price"))
                self.assertIn("estimated by this site", line.get("note", ""))

    def test_the_fleet_wide_gear_figure_is_the_operators_and_not_ours(self):
        """"A full set costs about EUR X a week" is a claim about what the
        boats charge. Folding this project's own figure into that average
        would put our number inside it on a sixth of the fleet, invisibly."""
        from liveaboard.pricing import itinerary_lines
        from liveaboard.render import gear_estimates, gear_prices
        from liveaboard.taxonomy import FeeCode

        dataset = published.dataset()
        self.assertTrue(gear_estimates(dataset).get("vessels"))

        quoted, ours = set(), set()
        for itinerary in dataset.itineraries.values():
            if itinerary.nights != 7:
                continue
            for line in itinerary_lines(itinerary, dataset.fx):
                if line.code is not FeeCode.GEAR_RENTAL or line.display is None:
                    continue
                (ours if line.estimated else quoted).add(itinerary.boat_id)
        self.assertTrue(ours, "no seven-night trip exercises the rule")
        self.assertEqual(
            gear_prices(dataset)["vessels"], len(quoted - ours),
            "the footer's fleet gear figure counts vessels this site priced",
        )

    def test_the_bill_says_so_in_words_as_well_as_in_a_marker(self):
        """A `~` on a figure is a marker a reader has to already understand.
        The paragraph under the table is what explains it, and it is built
        from the same lines the table is so the two figures cannot differ."""
        app = self.APP.read_text(encoding="utf-8")
        panel = app.split("function billPanel(", 1)[1].split("\n  }", 1)[0]
        self.assertIn("estimateWarning(lines)", panel)
        self.assertIn("this site's own estimate", app)
        self.assertIn(".caveat.est", self.CSS.read_text(encoding="utf-8"))

    def test_an_included_fee_reads_as_included_and_never_as_unstated(self):
        """The other half of what #152 reported. An included fee stays in the
        breakdown at zero -- the oldest rule in `pricing.py` -- but the amount
        cell asked `has_price`, which an included line does not have, so
        Sunshine's nitrox read **unstated** beside a note saying *Nitrox:
        stated as included*. The panel contradicted itself on the line where
        the operator had been most forthcoming, and filed generosity under the
        same word as silence."""
        app = self.APP.read_text(encoding="utf-8")
        rows = app.split("function feeRows(", 1)[1].split("\n  }", 1)[0]
        self.assertLess(
            rows.index("line.included"), rows.index("line.has_price"),
            "feeRows must ask `included` before `has_price`, or a bundled fee "
            "prints as unstated",
        )
        # And both shapes of it reach that branch, which is why asking
        # `included` first is the fix rather than one of two fixes: an
        # inclusion arrives from liveaboard.com with no amount at all and from
        # PADI as a stated zero, so the cell used to print "unstated" for one
        # and "€0" for the other about the same fact.
        included = [x for x in self.lines() if x.get("included")]
        self.assertTrue(any("has_price" not in x for x in included))
        self.assertTrue(any(x.get("has_price") for x in included))
