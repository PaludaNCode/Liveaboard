"""Load the built page in a real browser and check it works.

Every other test in this suite is Python. That leaves `templates/app.js` — the
whole interactive layer — checked only by reading its source, and two recent
changes went in on exactly that basis:

* #49 changed the page's data contract. Fee lines moved onto the itinerary and
  departures grew a ``base_line``, so ``metricsFor`` and ``feeTable`` now read
  ``[dep.base_line].concat(dep.lines || itin.lines)``. A typo there yields
  ``undefined`` and every total on the page becomes ``NaN`` — which no Python
  test can see.
* #35 added an Operator column and a 42-chip filter.

Both were verified by driving a browser by hand. This is that check, written
down.

**Skipped, not failed, when Playwright is absent.** The project's rule is zero
runtime dependencies and a suite that runs on the standard library, so
``python3 -m unittest discover -s tests`` must stay green on a bare checkout.
Playwright is a development tool — CI already installs it for ``probe.yml`` and
``fees.yml`` — so where it exists these run, and where it does not the rest of
the suite is unaffected.

A skip is silent, which is its own risk: a permanently skipped test is
indistinguishable from a passing one on a glance at CI. ``ci.yml`` therefore
installs Playwright and runs this file explicitly, so the skip is a local
convenience rather than the normal outcome.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from liveaboard.dataset import Dataset
from liveaboard.render import render

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "data" / "seed" / "egypt-2027.json"
LIVE = ROOT / "data" / "egypt-2027.json"

try:  # pragma: no cover - availability is the thing being branched on
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None


def _dataset() -> Path:
    """Prefer the real dataset: it is what visitors actually get.

    The seed is five placeholder boats. It exercises the code paths but not the
    scale, and #49 was a problem that only appears at 878 departures.
    """
    return LIVE if LIVE.exists() else SEED


# Rendered once and shared. Building the real dataset takes a second and
# launching Chromium takes longer; doing either per class turned a smoke test
# into three and a half minutes. Each test still gets a fresh page, so state
# does not leak between them.
_STATE: dict = {}


def setUpModule() -> None:
    if sync_playwright is None:
        return
    _STATE["tmp"] = tempfile.TemporaryDirectory()
    _STATE["url"] = render(Dataset.load(_dataset()), _STATE["tmp"].name).as_uri()
    _STATE["pw"] = sync_playwright().start()

    launch: dict = {"args": ["--no-sandbox"]}
    # Same resolution rule as tools/pw_browser.py (#56): Playwright's own
    # browser, falling back to the environment's versionless symlink only when
    # Playwright's build is not installed. Duplicated rather than imported
    # because tools/ is not on the package path.
    managed = getattr(_STATE["pw"].chromium, "executable_path", None)
    if not (managed and Path(managed).exists()):
        root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
        if root and (Path(root) / "chromium").exists():
            launch["executable_path"] = str(Path(root) / "chromium")
    _STATE["browser"] = _STATE["pw"].chromium.launch(**launch)


def tearDownModule() -> None:
    if not _STATE:
        return
    _STATE["browser"].close()
    _STATE["pw"].stop()
    _STATE["tmp"].cleanup()


# A resource the browser could not fetch is a network fact, not a code fault.
# With every external request blocked (see below) that is exactly one message
# per load: the webfont stylesheet the page still links to (#59). Filtering it
# here keeps it from failing every assertion about the page's own behaviour --
# while page errors, which are real JavaScript exceptions, stay fatal.
RESOURCE_LOAD_FAILURE = "Failed to load resource"


def _block_external(route) -> None:
    """Refuse every off-machine request the page makes.

    Two reasons, and the second is the important one.

    **It is the property under test.** "One self-contained HTML file" means the
    page must work with nothing but the file. Blocking the network is how you
    check that rather than assume it, and it means these tests would still pass
    on a laptop with no connection.

    **It is the difference between a 13-second suite and a 4-minute one.** The
    stylesheet in <head> is render-blocking, and a script cannot run until the
    CSSOM is ready, so the table does not exist until that request resolves.
    Where fonts.googleapis.com is unreachable it does not fail fast -- it hangs
    until the connection resets. Measured on this page:

        font fetch left to hang     first row after 13.04s, FCP 12,952 ms
        font fetch aborted          first row after  0.58s, FCP     96 ms

    That is #59 costing thirteen seconds of blank screen, not a stylistic
    quibble, and it is why these tests block the request rather than waiting
    it out.
    """
    route.abort()


@unittest.skipIf(sync_playwright is None, "playwright not installed")
class PageTestCase(unittest.TestCase):
    """Opens the shared build in a fresh page."""

    def setUp(self) -> None:
        self.errors: list[str] = []
        self.page = _STATE["browser"].new_page(viewport={"width": 1900, "height": 1000})
        # A predicate, not a glob. "https://**" matches nothing here --
        # Playwright's glob does not cross the scheme boundary the way it looks
        # like it should -- and a bare "**" aborts the file:// navigation
        # itself. Both fail quietly: the suite still passes, thirteen seconds
        # per test slower, which is exactly how this went unnoticed the first
        # time.
        self.page.route(
            lambda url: url.startswith(("http://", "https://")), _block_external
        )
        # A JavaScript exception is always a failure.
        self.page.on("pageerror", lambda exc: self.errors.append(f"pageerror: {exc}"))
        self.page.on("console", self._note_console_error)
        self.page.goto(_STATE["url"])
        self.page.wait_for_selector("#body tr.row", timeout=30000)
        self.addCleanup(self.page.close)

    def _note_console_error(self, message) -> None:
        if message.type != "error":
            return
        if RESOURCE_LOAD_FAILURE in message.text:
            return
        self.errors.append(f"console: {message.text}")

    def columns(self) -> list[str]:
        return self.page.eval_on_selector_all(
            "#head th", "els => els.map(e => e.textContent.trim().split(' ')[0])"
        )

    def column(self, name: str) -> int:
        return self.columns().index(name)

    def cells(self, index: int) -> list[str]:
        return self.page.eval_on_selector_all(
            "#body tr.row", f"els => els.map(r => r.children[{index}].textContent.trim())"
        )

    def shown(self) -> int:
        return int(self.page.inner_text("#shown").replace(",", ""))


class TestPageLoads(PageTestCase):
    """Every test in this file runs with the network blocked; see _block_external."""

    def test_no_javascript_errors(self):
        self.assertEqual(self.errors, [])

    def test_the_page_works_with_the_network_switched_off(self):
        """The invariant, stated as a test rather than as a sentence in a README.

        Nothing external resolves in any of these tests, so a table full of
        priced rows here *is* the proof that the data, styles and behaviour all
        travel inside the one file.
        """
        self.assertGreater(self.shown(), 0)
        self.assertTrue(self.page.inner_text("#body").strip())
        # Styles are inlined, so the layout is real rather than unstyled markup.
        sticky = self.page.eval_on_selector(
            "#head th", "el => getComputedStyle(el).position"
        )
        self.assertEqual(sticky, "sticky")

    def test_rows_render(self):
        self.assertGreater(self.shown(), 0)
        self.assertEqual(self.shown(), len(self.cells(0)))

    def test_the_row_count_matches_the_payload(self):
        payload = json.loads(self.page.inner_text("#payload"))
        self.assertEqual(self.shown(), len(payload["departures"]))


class TestTotalsAreNumbers(PageTestCase):
    """The failure mode #49 could have introduced, and Python cannot see.

    If ``linesFor`` returns anything unexpected, the arithmetic in
    ``metricsFor`` produces NaN and the page fills with "€NaN" while every
    Python test still passes.
    """

    def test_no_cell_reads_nan_or_undefined(self):
        text = self.page.inner_text("#body")
        for poison in ("NaN", "undefined", "null", "[object Object]"):
            self.assertNotIn(poison, text, f"{poison} rendered into the table")

    def test_true_cost_reads_as_a_figure_or_says_it_cannot(self):
        """Five shapes, and every one of them is deliberate.

            €1,566          a fixed total
            €1,530–1,600    the operator quoted a range; collapsing it would be
                            the site's own hidden cost
            €560 + tips     gratuities expected with no amount stated, so a real
                            cost sits outside the arithmetic and the total says so
            €560–600 + tips both at once
            —               no required extras published, so no true cost is
                            claimed at all (79 rows)

        The last one is the point of the column, not a gap in it: "unstated is
        not zero". A regex that rejected it would be pressuring the page to
        invent a number.
        """
        allowed = r"^€[\d,]+(–[\d,]+)?( \+ tips)?$|^—$"
        values = self.cells(self.column("True"))
        for value in values:
            self.assertRegex(value, allowed, value)
        # And the em dash must be the minority reading, or something has broken
        # in fee resolution rather than in the source's disclosure.
        priced = [v for v in values if v != "—"]
        self.assertGreater(len(priced), len(values) // 2)

    def test_advertised_price_is_always_a_figure(self):
        """Unlike true cost, this one has no honest "cannot say": it is scraped."""
        for value in self.cells(self.column("Advertised")):
            self.assertRegex(value, r"^€[\d,]+$", value)

    def test_true_cost_is_never_below_the_advertised_price(self):
        """Fees add; they never subtract. A shared-line bug could break this."""
        def euros(text: str) -> float | None:
            digits = text.replace("€", "").replace(",", "").split("–")[0].split(" ")[0]
            try:
                return float(digits)
            except ValueError:
                return None

        base = [euros(v) for v in self.cells(self.column("Advertised"))]
        total = [euros(v) for v in self.cells(self.column("True"))]
        compared = 0
        for advertised, true_cost in zip(base, total):
            if advertised is None or true_cost is None:
                continue
            compared += 1
            self.assertGreaterEqual(true_cost, advertised)
        self.assertGreater(compared, 0, "no rows had both figures to compare")


class TestFeeBreakdown(PageTestCase):
    """Expanding a row must build its table from the shared itinerary lines."""

    def rows_of_the_open_breakdown(self) -> list[list[str]]:
        return self.page.eval_on_selector_all(
            ".detail .fees tr",
            "els => els.map(r => [...r.children].map(c => c.textContent.trim()))",
        )

    def test_expanding_a_row_shows_a_breakdown_led_by_the_berth(self):
        self.page.click("#body tr.row:first-child .expand")
        self.page.wait_for_selector(".detail .fees tr")
        rows = self.rows_of_the_open_breakdown()
        self.assertGreater(len(rows), 1, "a breakdown with only a base line is not a breakdown")
        self.assertIn("Berth", rows[0][1])
        self.assertEqual(self.errors, [])

    def test_every_fee_row_has_a_tier_and_an_amount(self):
        self.page.click("#body tr.row:first-child .expand")
        self.page.wait_for_selector(".detail .fees tr")
        for row in self.rows_of_the_open_breakdown():
            label, amount, tier = row[1], row[2], row[3]
            self.assertTrue(label)
            self.assertTrue(tier)
            self.assertRegex(amount, r"^€[\d,]+(–[\d,]+)?$|^unstated$", f"{label}: {amount}")

    def test_two_departures_of_one_trip_show_the_same_fees(self):
        """The dedup's core claim, checked through the page rather than the payload.

        Fee lines are stored once per itinerary. If the browser resolved them
        against the wrong itinerary, two sailings of one trip would disagree.
        """
        payload = json.loads(self.page.inner_text("#payload"))
        by_itinerary: dict[str, list[str]] = {}
        for departure in payload["departures"]:
            by_itinerary.setdefault(departure["itinerary_id"], []).append(departure["id"])
        shared = next((ids for ids in by_itinerary.values() if len(ids) > 1), None)
        if shared is None:
            self.skipTest("no itinerary in this dataset has two departures")

        seen = []
        for departure_id in shared[:2]:
            index = next(
                n for n, d in enumerate(payload["departures"]) if d["id"] == departure_id
            )
            self.page.evaluate(
                """(n) => {
                     const rows = document.querySelectorAll('#body tr.row .expand');
                     if (rows[n]) rows[n].click();
                   }""",
                index,
            )
            self.page.wait_for_timeout(150)
            rows = self.rows_of_the_open_breakdown()
            # Drop the base fare: it is the one line that legitimately differs.
            seen.append([r[1:] for r in rows[1:]])
        self.assertEqual(seen[0], seen[1])


class TestToggles(PageTestCase):
    """A switch that changes no number answers the visitor with a figure that
    ignored them — the #47 bug. Guarded here in the browser, not just in Python."""

    def totals(self) -> list[str]:
        return self.cells(self.column("True"))

    def test_each_toggle_moves_at_least_one_total(self):
        chips = self.page.query_selector_all("#toggles .chip")
        self.assertTrue(chips, "no toggles rendered")
        for chip in chips:
            label = chip.inner_text().strip()
            with self.subTest(toggle=label):
                before = self.totals()
                chip.click()
                self.page.wait_for_timeout(200)
                after = self.totals()
                self.assertNotEqual(before, after, f"{label!r} changed no total")
                chip.click()
                self.page.wait_for_timeout(200)
                self.assertEqual(self.totals(), before, f"{label!r} did not restore")


class TestFilters(PageTestCase):
    def test_operator_filter_narrows_to_one_operator(self):
        """#35: the column and the chips, neither of which Python can see."""
        index = self.column("Operator")
        self.assertGreater(len(set(self.cells(index))), 1, "only one operator on the page")

        chip = self.page.query_selector("#operators .chip")
        self.assertIsNotNone(chip, "no operator chips rendered")
        name = chip.inner_text().strip().rsplit(" ", 1)[0]
        chip.click()
        self.page.wait_for_timeout(250)

        self.assertEqual(set(self.cells(index)), {name})
        self.assertGreater(self.shown(), 0)

    def test_search_matches_the_operator_name(self):
        index = self.column("Operator")
        name = sorted(set(self.cells(index)))[0]
        self.page.fill("#q", name)
        self.page.wait_for_timeout(250)
        self.assertGreater(self.shown(), 0)
        self.assertEqual(set(self.cells(index)), {name})

    def test_reset_restores_every_row(self):
        total = self.shown()
        self.page.fill("#q", "zzz-nothing-matches-this")
        self.page.wait_for_timeout(200)
        self.assertEqual(self.shown(), 0)
        self.page.click("#reset")
        self.page.wait_for_timeout(250)
        self.assertEqual(self.shown(), total)
        self.assertEqual(self.errors, [])


class TestSorting(PageTestCase):
    def test_clicking_a_header_sorts_and_reverses(self):
        header = f"#head th:nth-child({self.column('Advertised') + 1})"
        index = self.column("Advertised")

        self.page.click(header)
        self.page.wait_for_timeout(200)
        ascending = self.cells(index)

        self.page.click(header)
        self.page.wait_for_timeout(200)
        descending = self.cells(index)

        self.assertEqual(ascending, list(reversed(descending)))
        self.assertEqual(self.errors, [])

    def test_sorting_by_true_cost_does_not_error(self):
        self.page.click(f"#head th:nth-child({self.column('True') + 1})")
        self.page.wait_for_timeout(200)
        self.assertEqual(self.errors, [])
        self.assertGreater(self.shown(), 0)


if __name__ == "__main__":
    unittest.main()
