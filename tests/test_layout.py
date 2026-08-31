"""What the three views actually render, measured in a browser.

Every other test of the split reads the templates as text. That is the right
tool for wiring -- a control that addresses a view, a placeholder that lands in
one pane and not two -- and the wrong one for the claim the split was sold on,
which is a claim about size: measured at 1440x900, 1280x800, 900x800, 768x600,
390x844 and 360x640, the table gains rows and loses none.

Asserting the source text of a layout rule is not a measurement of it. Eight
such assertions passed over a sale view whose table rendered at **zero height**
at 768x600 (#130) -- among them the one named for the panel that caused it,
which asserted that the offending rule was present in the stylesheet.

So this file opens the built page and measures boxes. It is the only test in
the suite that needs anything outside the standard library, and it skips rather
than fails when that thing is absent: `python3 -m unittest discover -s tests`
must keep working on a checkout with no browser, which is what the rest of CI
runs. The job that installs one runs it for real -- see `layout` in ci.yml. A
test that only ever skips protects nothing.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site" / "index.html"
sys.path.insert(0, str(ROOT / "tools"))

try:  # pragma: no cover - depends on the environment, not on the code
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

# The six windows the split was measured at, smallest common phone last.
SIZES = [(1440, 900), (1280, 800), (900, 800), (768, 600), (390, 844), (360, 640)]

# What a table must be left, in pixels, on any view at any of those sizes with
# the deals panel closed. Three rows and a header at the compact row height --
# the point being not that 150px is a good table but that a table below it has
# stopped being the answer on a page whose whole subject is the rows.
TABLE_FLOOR = 150

MEASURE = """() => {
  const box = s => {
    const e = document.querySelector(s);
    if (!e) return null;
    const r = e.getBoundingClientRect();
    return { w: Math.round(r.width), h: Math.round(r.height),
             bottom: Math.round(r.bottom) };
  };
  const panes = [...document.querySelectorAll('.pane')];
  return {
    shell: box('.shell'),
    deals: box('#deals'),
    visiblePanes: panes.filter(p => p.getBoundingClientRect().height > 0).length,
    docScrolls: document.documentElement.scrollHeight
                > document.documentElement.clientHeight + 1,
    footerBottom: box('.site-footer').bottom,
    viewport: window.innerHeight,
    title: document.title,
    hash: location.hash,
    current: [...document.querySelectorAll('[aria-current]')].map(e => e.id).join(','),
    focus: document.activeElement.id,
    railTrips: document.getElementById('navTripsCount').textContent,
    railSale: document.getElementById('navSaleCount').textContent,
    shown: document.getElementById('shown').textContent,
    scrollTop: Math.round(document.querySelector('.shell').scrollTop)
  };
}"""


def _browser_reason() -> str | None:
    if sync_playwright is None:
        return "playwright is not installed"
    if not SITE.exists():
        return "site/index.html is not built"
    return None


@unittest.skipIf(_browser_reason() is not None, _browser_reason() or "")
class TestTheViewsAtEverySize(unittest.TestCase):
    """One browser for the whole class: launching Chromium is the expensive
    part and none of these tests writes anything the next one can read."""

    @classmethod
    def setUpClass(cls) -> None:
        from pw_browser import resolve

        cls._pw = sync_playwright().start()
        executable, reason = resolve(cls._pw)
        print(f"layout tests: {reason}", flush=True)
        cls._browser = cls._pw.chromium.launch(
            **({"executable_path": executable} if executable else {})
        )
        cls._url = SITE.resolve().as_uri()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._browser.close()
        cls._pw.stop()

    def open(self, width: int, height: int, hash_: str = ""):
        """A fresh load. Costs about a second -- the payload is inlined and it
        is 2.4MB -- so it is for the tests that are *about* loading."""
        page = self._browser.new_page(viewport={"width": width, "height": height})
        page.goto(self._url + hash_)
        page.wait_for_selector("#body tr")
        return page

    def sweep(self, page, width: int, height: int, view: str):
        """The same page resized and re-addressed, for the tests that are about
        geometry rather than about boot. Resizing and setting the hash drive
        every code path a reload would -- the media queries and `showView` --
        without paying for the payload 60 times, which is the difference
        between this file running in nine seconds and timing out."""
        page.set_viewport_size({"width": width, "height": height})
        page.evaluate("v => { location.hash = v; }", view)
        page.wait_for_timeout(120)
        return self.measure(page)

    def measure(self, page) -> dict:
        return page.evaluate(MEASURE)

    # -- the shell holds, on every view and every size -----------------------

    def test_the_shell_holds_on_every_view_at_every_size(self) -> None:
        """Three invariants that have to hold together, so they are measured
        together rather than paying for three sweeps of the same six windows.

        *One pane on screen*: `[hidden]` beating `display:flex` is what makes
        that true, and the way it failed was every pane drawing at once.

        *The window does not scroll and the footer is reachable*: an app shell,
        not a document. A pane that overruns its share paints over the footer
        instead of shrinking -- which is the failure a floor under the table
        has to avoid causing, and the reason #130's fix is a flex basis rather
        than a `min-height`.

        *The table has room*: the claim the split was sold on, stated as a
        floor rather than as a table of row counts, because a row count is a
        fact about one dataset and this has to keep holding as the fleet
        changes.
        """
        page = self.open(1440, 900)
        try:
            for width, height in SIZES:
                for view in ("#trips", "#sale", "#history"):
                    with self.subTest(size=(width, height), view=view):
                        m = self.sweep(page, width, height, view)
                        self.assertEqual(m["visiblePanes"], 1,
                                         "more than one view is on screen")
                        self.assertFalse(m["docScrolls"], "the window itself scrolls")
                        self.assertLessEqual(m["footerBottom"], m["viewport"] + 1,
                                             "the footer is off the bottom edge")
                        if view != "#history":
                            self.assertGreaterEqual(m["shell"]["h"], TABLE_FLOOR,
                                                    "the table is below its floor")
        finally:
            page.close()

    def test_the_table_survives_the_deals_panel(self) -> None:
        """#130. The panel is an index and the table is what it indexes, so a
        shortfall comes out of the panel, which scrolls inside itself and loses
        nothing by being shorter. Measured before this rule: 159px of table at
        1440x900 and **0px** at 768x600."""
        page = self.open(1440, 900, "#sale")
        try:
            for width, height in SIZES:
                with self.subTest(size=(width, height)):
                    before = self.sweep(page, width, height, "#sale")
                    page.evaluate("document.getElementById('deals').open = true")
                    page.wait_for_timeout(150)
                    after = self.measure(page)
                    page.evaluate("document.getElementById('deals').open = false")
                    page.wait_for_timeout(100)
                    self.assertGreater(after["deals"]["h"], before["deals"]["h"],
                                       "the panel did not visibly open")
                    # An even split, stated as "the index never outgrows what
                    # it indexes". Scale-free on purpose: a pixel floor here
                    # would be a floor the smallest window cannot meet, and
                    # what went wrong was a priority rather than a number.
                    # `- 2` is the panel's own bottom rule and rounding.
                    self.assertGreaterEqual(
                        after["shell"]["h"], after["deals"]["h"] - 2,
                        "the deals panel is given more room than the table")
                    self.assertGreater(
                        after["shell"]["h"], 0,
                        "opening the deals panel left no table at all")
        finally:
            page.close()

    # -- the router ----------------------------------------------------------

    def test_a_view_change_starts_a_different_list_at_the_top(self) -> None:
        """#131. Crossing between trips and sale is a different list and starts
        at the top; the history round trip filtered nothing and keeps both the
        page and the place."""
        page = self.open(1440, 900)
        self.addCleanup(page.close)
        page.evaluate("document.querySelector('.shell').scrollTop = 5000")
        page.wait_for_timeout(300)
        moved = self.measure(page)["scrollTop"]
        self.assertGreater(moved, 1000, "the table did not scroll")

        page.click("#navSale")
        page.wait_for_timeout(300)
        self.assertEqual(self.measure(page)["scrollTop"], 0,
                         "the sale view opened part-way down the trips view's scroll")

        page.evaluate("document.querySelector('.shell').scrollTop = 900")
        page.wait_for_timeout(300)
        held = self.measure(page)["scrollTop"]
        page.click("#navHistory")
        page.wait_for_timeout(200)
        page.click("#navSale")
        page.wait_for_timeout(300)
        self.assertEqual(self.measure(page)["scrollTop"], held,
                         "the history round trip lost the sale view's place")

    def test_the_address_bar_agrees_with_the_view_on_screen(self) -> None:
        """#132. A name the page will not honour is corrected in the address
        bar too, so a bookmark cannot name a view its owner was never shown."""
        page = self.open(1440, 900, "#nonsense")
        m = self.measure(page)
        self.assertEqual(m["hash"], "#trips")
        self.assertEqual(m["current"], "navTrips")
        page.close()

        page = self.open(1440, 900)
        self.assertEqual(self.measure(page)["hash"], "",
                         "a bare address was rewritten, claiming nothing wrongly")
        page.close()

    def test_each_view_has_its_own_title(self) -> None:
        """#132. Three history entries with one name between them."""
        page = self.open(1440, 900)
        base = self.measure(page)["title"]
        seen = {base}
        for item in ("#navSale", "#navHistory"):
            page.click(item)
            page.wait_for_timeout(200)
            seen.add(self.measure(page)["title"])
        page.close()
        self.assertEqual(len(seen), 3, f"views share a title: {sorted(seen)}")

    def test_a_view_change_moves_focus_into_the_view(self) -> None:
        """#133. The rail's hrefs match no element id, so the browser moves
        focus nowhere and the whole content area is replaced behind a focus
        ring that never left the link."""
        page = self.open(1440, 900)
        self.assertEqual(self.measure(page)["focus"], "",
                         "the page took focus at boot, which nobody asked it to")
        page.click("#navHistory")
        page.wait_for_timeout(200)
        self.assertEqual(self.measure(page)["focus"], "historyPane")
        page.click("#navTrips")
        page.wait_for_timeout(300)
        self.assertEqual(self.measure(page)["focus"], "tablePane")
        page.close()

    def test_the_rail_counts_track_the_filters_beside_them(self) -> None:
        """#134 [sic #135]. The rail said "Trips 1,122" beside a stat block
        saying "12 rows shown" -- two numbers about one table, one frozen at
        boot. The On sale *chip* keeps its fixed count for its own stated
        reason; a rail item is a destination and its number is a promise about
        what opening it gives you."""
        page = self.open(1440, 900)
        before = self.measure(page)
        self.assertEqual(before["railTrips"], before["shown"])

        page.click("#months .chip")  # any one month
        page.wait_for_timeout(400)
        after = self.measure(page)
        page.close()
        self.assertNotEqual(after["shown"], before["shown"], "the filter did nothing")
        self.assertEqual(after["railTrips"], after["shown"],
                         "the rail is still counting the unfiltered season")
        self.assertNotEqual(after["railSale"], before["railSale"],
                            "the sale count did not move with the filter")


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps({"sizes": SIZES, "floor": TABLE_FLOOR}))
    unittest.main()
