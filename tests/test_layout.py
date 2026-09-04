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

# Every window a phone might be, and not only the two in SIZES: the fold that
# keeps the Total on screen is a boundary, and a boundary is exactly what a
# sample of two misses. 386 and 419 sat either side of the one that shipped
# broken (#150).
PHONE_WIDTHS = [320, 360, 375, 385, 386, 390, 393, 402, 414, 419, 430]


def shipped_payload() -> dict:
    """The page's own data, read back out of the page.

    A filter test that counts rows in the browser and compares them with a
    number the browser also produced proves only that the page agrees with
    itself. For the four facets the payload settles on its own -- the month,
    the boat, whether a seller marked the sailing down, whether it is still
    bookable -- the expected figure is counted here instead, from the same
    bytes the visitor is served.
    """
    import re

    html = SITE.read_text(encoding="utf-8")
    raw = re.search(r'id="payload"[^>]*>(.*?)</script>', html, re.S).group(1)
    return json.loads(raw.replace("<\\/", "</"))

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
        # The rail item the boot lit, not a table row: the table is drawn by
        # the trips view and only by it, so waiting on a row hangs for thirty
        # seconds on the two views that correctly do not have one.
        page.wait_for_selector('.rail-item[aria-current="page"]')
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

    def pick_filter(self, page, bank: str, selector: str) -> None:
        """Press a row filter, wherever the drawer is keeping it.

        Every filter that picks rows is in `#filterPanel` now -- the season and
        the two sale chips included -- and the panel shows one bank at a time,
        so reaching a chip means opening the drawer and selecting its bank.
        Tests that clicked straight at `#months .chip` were clicking a control
        that is no longer on the toolbar.
        """
        if page.evaluate(
            "()=>document.getElementById('filtersToggle')"
            ".getAttribute('aria-expanded') !== 'true'"
        ):
            page.click("#filtersToggle")
            page.wait_for_timeout(280)
        page.click('.bank-tab[data-bank="%s"]' % bank)
        page.wait_for_timeout(200)
        page.click(selector)
        page.wait_for_timeout(320)

    # -- the money is on screen ----------------------------------------------

    def test_the_total_is_on_screen_without_scrolling(self) -> None:
        """The one number this page exists to publish, at rest, on a phone.

        The whole phone column order exists for this and it did not hold: the
        Total's right edge landed at 420 on a 390px screen and 375 on a 360px
        one, so 360, 390, 393, 402 and 414 all lost it -- most phones in use
        (#150). It failed exactly as the ordering's own comment says it would:
        the row still renders, it just does not answer the question.

        Nothing caught it because nothing asked. This file was already driving
        Chromium at 390x844 and 360x640 -- both of the failing widths -- and
        everything about the ordering was asserted as template text, which is
        right for wiring and worthless for geometry. Same gap as the 0px table
        at 768x600 (#130).

        Measured across the boundary rather than at two sample widths, because
        the thing that broke *is* a boundary: `tiny` was set from a Total column
        155px wide and the column is content-sized -- today by a two-seller
        ranged total with both markers on it -- so the width it needs moves when
        the fleet's dearest sailing moves. A typed breakpoint cannot stay right
        and this is what tells us when it stops.
        """
        page = self.open(PHONE_WIDTHS[0], 720)
        try:
            for width in PHONE_WIDTHS:
                with self.subTest(width=width):
                    page.set_viewport_size({"width": width, "height": 720})
                    page.evaluate("() => { location.hash = '#trips'; }")
                    page.wait_for_timeout(160)
                    seen = page.evaluate("""() => {
                      const table = document.querySelector('.shell table');
                      const drawn = getComputedStyle(table).display !== 'none';
                      /* Measured on whichever layout is actually on screen, and
                         the layout is reported so the assertion cannot pass by
                         measuring a box that is not being drawn: a hidden
                         element's rect is all zeros, which clears every bound
                         below without the number being anywhere. */
                      let cell = null;
                      if (drawn) {
                        const head = [...document.querySelectorAll('#head th')];
                        const n = head.findIndex(h => h.dataset.k === 'total');
                        const row = document.querySelector('tbody tr.row');
                        if (n >= 0 && row) cell = row.children[n];
                      } else {
                        cell = document.querySelector('.card .card-money');
                      }
                      if (!cell) return null;
                      const r = cell.getBoundingClientRect();
                      return {layout: drawn ? 'table' : 'cards',
                              right: Math.round(r.right), left: Math.round(r.left),
                              width: Math.round(r.width), vw: window.innerWidth,
                              scrolled: Math.round(document.querySelector('.shell').scrollLeft),
                              text: (cell.querySelector('b') || cell).textContent};
                    }""")
                    self.assertIsNotNone(seen, "no Total on screen in either layout")
                    self.assertEqual(seen["layout"], "cards",
                                     f"at {width}px the rows are still a table, so "
                                     "this measured the layout a phone does not get")
                    self.assertGreater(seen["width"], 0, "the Total box is not drawn")
                    self.assertEqual(seen["scrolled"], 0, "the rows are not at rest")
                    self.assertLessEqual(
                        seen["right"], seen["vw"],
                        f"at {width}px the Total ({seen['text']}) ends at "
                        f"{seen['right']} and the screen is {seen['vw']} wide: "
                        f"{seen['right'] - seen['vw']}px of the number this page "
                        "exists to publish is off the right-hand edge",
                    )
                    self.assertGreaterEqual(
                        seen["left"], 0,
                        f"at {width}px the Total starts at {seen['left']}",
                    )
        finally:
            page.close()

    def test_the_rows_change_shape_with_the_room_and_change_back(self) -> None:
        """What replaced the measured fold, and the failure it replaced.

        The fold took columns off the front of the row until the Total fit, and
        two things were wrong with it. The money only stayed on screen by
        hiding the boat behind it; and the widths that decided which columns
        went are set by whichever rows are on screen, so it moved when a filter
        changed and the reader lost a column for reasons they could not see
        (#150). Below 760 the rows are cards, which have no columns to fold.

        Swept down and back up on one page, because the half that catches a
        layout that stuck is the second one -- the first version of the guard
        this replaces passed on a build that settled at the narrowest width and
        never moved again.
        """
        page = self.open(1100, 760)
        try:
            def shape() -> dict:
                page.wait_for_timeout(280)
                return page.evaluate("""() => {
                  const table = document.querySelector('.shell table');
                  const cards = document.querySelector('.cards');
                  const seen = (el) => getComputedStyle(el).display !== 'none';
                  const money = document.querySelector(
                    seen(cards) ? '.card .card-money' : 'tbody td.cost');
                  const r = money ? money.getBoundingClientRect() : null;
                  return {table: seen(table), cards: seen(cards),
                          rows: (seen(cards) ? cards.children : document.getElementById('body').children).length,
                          moneyRight: r ? Math.round(r.right) : null,
                          vw: window.innerWidth,
                          sideways: document.body.scrollWidth > document.body.clientWidth};
                }""")

            wide = shape()
            self.assertTrue(wide["table"] and not wide["cards"],
                            "a 1100px window is not being given the table")
            for width in (740, 390, 360):
                page.set_viewport_size({"width": width, "height": 760})
                seen = shape()
                with self.subTest(width=width):
                    self.assertTrue(seen["cards"] and not seen["table"],
                                    f"at {width}px the rows are still a table")
                    self.assertGreater(seen["rows"], 0, "the card list is empty")
                    self.assertLessEqual(seen["moneyRight"], seen["vw"],
                                         "the money is off the right-hand edge")
                    self.assertFalse(seen["sideways"],
                                     "the window scrolls sideways, which is what "
                                     "having no columns to fold was supposed to end")
            # And back up, which is the half that catches a layout that stuck.
            page.set_viewport_size({"width": 1100, "height": 760})
            back = shape()
            self.assertTrue(back["table"] and not back["cards"],
                            "the table was not given back when the room was")
            self.assertGreater(back["rows"], 0,
                               "the table came back empty: the two hosts are no "
                               "longer drawn together")
        finally:
            page.close()

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
                        if view == "#trips":
                            self.assertGreaterEqual(m["shell"]["h"], TABLE_FLOOR,
                                                    "the table is below its floor")
        finally:
            page.close()

    def test_the_sale_view_is_an_overview_and_not_the_table(self) -> None:
        """The sale view was the trips table under a held-down filter, with the
        discount overview folded into a `details` above it -- capped at 34vh,
        and at 768x600 leaving the table itself at zero height (#130).

        It is a document of its own now, so the two never compete for a window
        again: there is no table on that view to squeeze, and the overview has
        the room. What has to hold at every size is that it renders as a
        readable document rather than a strip.
        """
        page = self.open(1440, 900, "#sale")
        try:
            for width, height in SIZES:
                with self.subTest(size=(width, height)):
                    self.sweep(page, width, height, "#sale")
                    m = page.evaluate("""() => {
                      const p = document.getElementById('salePane');
                      const r = p.getBoundingClientRect();
                      return { h: Math.round(r.height),
                               content: Math.round(p.scrollHeight),
                               table: !!document.querySelector('#salePane .deals-table'),
                               tripsTable: document.getElementById('tablePane').hidden,
                               wide: p.scrollWidth > p.clientWidth + 1 };
                    }""")
                    self.assertTrue(m["tripsTable"],
                                    "the trips table is on screen under the sale view")
                    self.assertTrue(m["table"], "the overview drew no boat table")
                    self.assertGreater(m["h"], TABLE_FLOOR,
                                       "the overview is a strip rather than a view")
                    self.assertGreater(m["content"], m["h"],
                                       "the overview fits its pane, so nothing was cut "
                                       "-- but it should be a document that scrolls")
                    self.assertFalse(m["wide"],
                                     "the overview scrolls sideways; its tables must "
                                     "scroll inside themselves instead")
        finally:
            page.close()

    def test_the_sale_view_s_two_tables_fill_the_panel_and_match(self) -> None:
        """Measured, because a width is a layout claim (#147).

        `display:block` on the table made the scrolling work and the width
        wrong: a table told to be a block box hands its rows to an anonymous
        inline-table, which shrink-wraps to its own content. So both tables sat
        crammed against the left of the panel with a third of it blank, and
        they were different widths from each other -- each as wide as its own
        longest cell. The stylesheet said `max-width:100%` throughout, which is
        exactly the kind of source-text assertion this file exists to distrust.

        Two claims, and the second only where there is room for it: each table
        fills its scroller, and where neither scroller has to scroll the two
        are the same width.
        """
        page = self.open(1440, 900, "#sale")
        try:
            for width, height in SIZES:
                with self.subTest(size=(width, height)):
                    self.sweep(page, width, height, "#sale")
                    # The header *row*, not the table element. A table with
                    # `display:block` is a block box and fills its parent like
                    # any other -- it is the anonymous inline-table holding its
                    # rows that shrink-wraps, so measuring the table measures
                    # the wrapper and passes over the exact bug (checked, on
                    # the old rule).
                    m = page.evaluate("""() => {
                      return [...document.querySelectorAll('#salePane .deals-scroll')]
                        .map(box => {
                          const tr = box.querySelector('tr');
                          return { row: Math.round(tr.getBoundingClientRect().width),
                                   room: box.clientWidth,
                                   scrolls: box.scrollWidth > box.clientWidth + 1 };
                        });
                    }""")
                    self.assertEqual(len(m), 2,
                                     "the sale view no longer draws its two tables "
                                     "inside scrollers")
                    for n, box in enumerate(m):
                        self.assertGreaterEqual(
                            box["row"], box["room"] - 1,
                            f"table {n} leaves {box['room'] - box['row']}px of the "
                            "panel blank beside its columns")
                    if not any(box["scrolls"] for box in m):
                        self.assertEqual(
                            m[0]["row"], m[1]["row"],
                            "the two overviews are different widths where both fit")
        finally:
            page.close()

    def test_the_header_is_the_same_height_on_every_view(self) -> None:
        """Switching view must not move the page under the rail.

        The masthead is the taller of the title and the three counts, and the
        counts belong to the one view with a table -- so hiding them resized
        the header on every view change: 72px to 57 on a laptop, 87 to 44 on a
        phone, with the rail, the toolbar and the first row of prices jumping
        with it. They are blanked rather than removed now, which keeps both
        rules: the numbers are off the screen and out of the accessibility
        tree on the views they do not describe, and the box they sit in still
        holds its height.
        """
        page = self.open(1440, 900)
        try:
            for width, height in SIZES:
                with self.subTest(size=(width, height)):
                    seen = []
                    for view in ("#trips", "#sale", "#history"):
                        self.sweep(page, width, height, view)
                        seen.append(page.evaluate("""() => {
                          const m = document.querySelector('.masthead');
                          const r = document.querySelector('.rail');
                          const s = document.getElementById('stats');
                          return { head: Math.round(m.getBoundingClientRect().height),
                                   rail: Math.round(r.getBoundingClientRect().y),
                                   shown: getComputedStyle(s).visibility === 'visible' };
                        }"""))

                    heights = {m["head"] for m in seen}
                    self.assertEqual(len(heights), 1,
                                     f"the masthead changes height across views: {heights}")
                    tops = {m["rail"] for m in seen}
                    self.assertEqual(len(tops), 1,
                                     f"the rail moves when the view changes: {tops}")
                    # And the counts are still only on the view they count.
                    self.assertEqual([m["shown"] for m in seen], [True, False, False],
                                     "the row counts are showing on a view with no table")
        finally:
            page.close()

    def test_the_drawer_hides_every_bank_at_every_width_and_says_what_is_on(self) -> None:
        """One panel, one control to open it -- at every width, not only on a
        phone.

        The four banks folded under 1000px and the toolbar did not, so the fold
        saved a screen of buttons and left a screen of buttons behind it. Both
        went inside it; then the fold itself stopped being about small screens,
        because a 1440x900 window was spending the same quarter of itself on
        filters nobody had chosen. What stays out is one line: the two Include
        switches, the season, and two chips. What must not is a bank -- and
        a filter set in a hidden bank is a table quietly answering a narrower
        question than the one on screen, which is why the count and the pills
        below are load-bearing.
        """
        page = self.open(1440, 900)
        try:
            for width, height in [(1440, 900), (1280, 800), (768, 600),
                                  (390, 844), (360, 640)]:
                with self.subTest(size=(width, height)):
                    page.set_viewport_size({"width": width, "height": height})
                    page.wait_for_timeout(180)
                    shut = self.measure(page)
                    banks = page.evaluate("""()=>[...document.querySelectorAll('.bank')]
                      .reduce((n, b) => n + Math.round(b.getBoundingClientRect().height), 0)""")
                    self.assertEqual(banks, 0,
                                     "a filter bank is on screen with the drawer shut")
                    toolbar = page.evaluate(
                        "()=>Math.round(document.querySelector('.toolbar')"
                        ".getBoundingClientRect().height)")
                    self.assertGreater(toolbar, 0,
                                       "the one line that stays out is not on screen")
                    # One line where there is room for one. A phone wraps it
                    # to three -- the switches, the season, the two chips --
                    # which is 105px against the 216px the toolbar alone cost
                    # there before the banks went in, and against 380px of
                    # banks under it. The cap is what catches a bank leaking
                    # back out: the smallest of them is 60px and Boat is 380.
                    cap = 48 if width >= 900 else 120
                    self.assertLess(toolbar, cap,
                                    f"the toolbar is {toolbar}px at {width}px wide, "
                                    f"over its {cap}px budget: something that belongs "
                                    "in the drawer is out of it")
                    page.click("#filtersToggle")
                    page.wait_for_timeout(280)
                    opened = self.measure(page)
                    self.assertGreater(shut["shell"]["h"], opened["shell"]["h"],
                                       "opening the panel did not cost the rows room, "
                                       "so the panel did not open")
                    # The control that opens it stays above what it opens.
                    self.assertTrue(page.evaluate("""() => {
                      const t = document.getElementById('filtersToggle');
                      const p = document.getElementById('filterPanel');
                      return t.getBoundingClientRect().y < p.getBoundingClientRect().y;
                    }"""), "the toggle sits below the panel it opens")
                    # Exactly one bank is drawn: five stacked is the wall of
                    # filters the drawer was opened to end.
                    drawn = page.evaluate("""()=>[...document.querySelectorAll('.bank')]
                      .filter(b => b.getBoundingClientRect().height > 0).length""")
                    self.assertEqual(drawn, 1, f"{drawn} banks are drawn at once")
                    self.assertFalse(self.measure(page)["docScrolls"],
                                     "the open panel made the window scroll")
                    page.click("#filtersToggle")
                    page.wait_for_timeout(220)
        finally:
            page.close()

    def test_the_drawer_never_hides_an_active_filter_in_silence(self) -> None:
        """Two things say so and both are asserted: the count on the control
        that hides them, and a pill per filter naming it and dropping it.

        And the count measures exactly what is behind that control. The Include
        switches used to be in it, on the reasoning that the number should
        cover every control not as it opened -- but they are never hidden:
        they sit on the toolbar at every width, lit, an inch from the badge. A
        badge reading "2" over a drawer holding nothing that put it there is a
        worse lie than the one it guards against.
        """
        page = self.open(390, 844)
        self.addCleanup(page.close)
        count = lambda: page.evaluate(
            "()=>document.getElementById('filtersCount').textContent")
        pills = lambda: page.evaluate(
            "()=>[...document.querySelectorAll('#activePills .pill-drop')]"
            ".map(b => b.textContent.replace(/\s+/g, ' ').trim())")
        self.assertEqual(count(), "")
        self.assertEqual(pills(), [])

        for n, (bank, selector) in enumerate(
            (("months", "#months .chip"), ("flags", "#hideSold")), start=1
        ):
            self.pick_filter(page, bank, selector)
            self.assertEqual(count(), str(n))
            self.assertEqual(len(pills()), n,
                             "the count moved and the pills did not, so what is "
                             "filtering is a number and not a name")

        # A switch is not a filter and is not counted as one. It filters no
        # rows -- it changes what every total means -- and it is on screen
        # saying so, which is the whole reason it is on the toolbar.
        before = count()
        page.click("#toggles .chip")
        page.wait_for_timeout(320)
        self.assertEqual(count(), before,
                         "turning a total switch off moved the filter count")
        self.assertEqual(len(pills()), 2,
                         "a total switch put a pill in the bar that names filters")
        self.assertFalse(
            page.evaluate("()=>document.getElementById('toggles')"
                          ".querySelector('.chip').getAttribute('aria-pressed') === 'true'"),
            "the switch did not actually turn off")

        # And all of it survives the drawer closing, which is the whole point.
        page.click("#filtersToggle")
        page.wait_for_timeout(280)
        self.assertEqual(count(), "2")
        self.assertEqual(len(pills()), 2)

        # A pill drops its own filter, so undoing one does not mean opening the
        # drawer to hunt for the chip that set it.
        page.click("#activePills .pill-drop")
        page.wait_for_timeout(320)
        self.assertEqual(count(), "1")
        self.assertEqual(len(pills()), 1)

        # Clear all clears what the bar lists, and stops there: the switch the
        # visitor turned off is not on that bar, so putting it back would be an
        # unnamed side effect moving every total on the page.
        off = page.evaluate("()=>document.getElementById('toggles')"
                            ".querySelector('.chip').getAttribute('aria-pressed')")
        page.click("#reset")
        page.wait_for_timeout(320)
        self.assertEqual(count(), "")
        self.assertEqual(pills(), [])
        self.assertEqual(
            page.evaluate("()=>document.getElementById('toggles')"
                          ".querySelector('.chip').getAttribute('aria-pressed')"), off,
            "Clear all silently switched a total back on")

    # -- the router ----------------------------------------------------------

    def test_leaving_the_trips_view_and_returning_keeps_your_place(self) -> None:
        """The table is drawn once and nothing else disturbs it: the other two
        views filter nothing, so both what was drawn and where it was scrolled
        to survive the round trip. It used to be redrawn on every crossing,
        because the sale view was the same table under a filter -- and the
        scroller was never reset with it, so arriving there put you 5,368px
        down a freshly drawn 120-row page."""
        page = self.open(1440, 900)
        self.addCleanup(page.close)
        page.evaluate("document.querySelector('.shell').scrollTop = 5000")
        page.wait_for_timeout(300)
        held = self.measure(page)["scrollTop"]
        self.assertGreater(held, 1000, "the table did not scroll")
        drawn = page.evaluate("()=>document.querySelectorAll('#body tr:not(.detail)').length")

        for item in ("#navSale", "#navHistory", "#navTrips"):
            page.click(item)
            page.wait_for_timeout(250)
        m = self.measure(page)
        self.assertEqual(m["scrollTop"], held, "the round trip lost your place")
        self.assertEqual(
            page.evaluate("()=>document.querySelectorAll('#body tr:not(.detail)').length"),
            drawn, "the round trip threw away the rows you had scrolled to load")

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

    def test_the_rail_counts_what_each_view_actually_answers(self) -> None:
        """Two views, counted two ways, because they answer differently.

        **Trips** is the filtered table, so its number is filter-relative and
        agrees with `rows shown` -- it read "Trips 1,122" beside "12 rows
        shown" before. **On sale** is an overview of the whole deals book that
        no filter touches, so its number must *not* move with the filters: a
        rail item's number is a promise about what opening it gives you, and a
        narrowing the view does not perform is a promise broken by the click.
        """
        page = self.open(1440, 900)
        self.addCleanup(page.close)
        before = self.measure(page)
        self.assertEqual(before["railTrips"], before["shown"])

        self.pick_filter(page, "months", "#months .chip")  # any one month
        after = self.measure(page)
        self.assertNotEqual(after["shown"], before["shown"], "the filter did nothing")
        self.assertEqual(after["railTrips"], after["shown"],
                         "the rail is still counting the unfiltered season")
        self.assertEqual(after["railSale"], before["railSale"],
                         "the overview's count moved with a filter that does not "
                         "reach it")

    def test_a_phone_can_sort_the_table_it_cannot_see_the_header_of(self) -> None:
        """The sort control survives the width that deletes the table.

        Below 760px `.shell > table` is `display:none` and the rows are cards,
        so the header row -- which is the sort control -- is not on screen at
        all. The page could not be sorted on a phone, which is the same shape
        of failure as the money fold: a control that exists only in the layout
        it was developed in.

        Measured at every phone width rather than at one, and the layout it
        measured is asserted: a hidden element's rect is all zeros, so a test
        that forgot to check *which* layout it was in would pass over a
        toolbar that had disappeared.
        """
        page = self.open(PHONE_WIDTHS[0], 720)
        self.addCleanup(page.close)
        for width in PHONE_WIDTHS:
            page.set_viewport_size({"width": width, "height": 720})
            page.wait_for_timeout(120)
            shape = page.evaluate("""() => {
              const r = s => { const e = document.querySelector(s);
                if (!e) return null; const b = e.getBoundingClientRect();
                return { w: Math.round(b.width), h: Math.round(b.height),
                         top: Math.round(b.top) }; };
              const head = document.querySelector('.shell > table thead');
              return { cards: !!document.querySelector('.cards .card'),
                       header: head ? Math.round(head.getBoundingClientRect().height) : 0,
                       by: r('#sortBy'), dir: r('#sortDir'),
                       bar: r('.toolbar'),
                       name: document.getElementById('sortBy').getAttribute('aria-label'),
                       switches: [].slice.call(document.querySelectorAll('#toggles button'))
                                   .map(e => e.getAttribute('aria-label')),
                       dirName: document.getElementById('sortDir').getAttribute('aria-label') };
            }""")
            where = "at %dpx" % width
            self.assertTrue(shape["cards"], "not the card layout " + where)
            self.assertEqual(shape["header"], 0,
                             "there is a table header after all " + where)
            for key in ("by", "dir"):
                self.assertIsNotNone(shape[key], "no sort control " + where)
                self.assertGreater(shape[key]["w"], 0, "sort control collapsed " + where)
                self.assertGreater(shape[key]["h"], 20,
                                   "sort control too small to press " + where)
            # The visible label is hidden here, so the accessible one is all
            # there is -- and a triangle with no name is not a control.
            self.assertTrue(shape["name"], "the dropdown has no accessible name " + where)
            # The switches lose the word INCLUDE here, so their names have to
            # say what they do rather than what they are called.
            for name in shape["switches"]:
                self.assertIn("Include", name,
                              "a total switch reads as a filter " + where)
            self.assertIn("press for", shape["dirName"] or "",
                          "the direction button does not say what pressing does " + where)
            # And it costs no row at all on a phone anybody still owns. The
            # labels give way instead -- INCLUDE, SORT, "Rental", "Mandatory
            # fees" -- so Filters, the column, the direction and the two
            # switches sit on one line from 360px up. Below that they wrap,
            # which is the graceful half; a third row is not.
            limit = 48 if width >= 360 else 80
            self.assertLessEqual(shape["bar"]["h"], limit,
                                 "the toolbar wrapped " + where)

        # And picking from it reorders the cards, which is the whole point.
        page.set_viewport_size({"width": 390, "height": 720})
        page.wait_for_timeout(120)
        first = lambda: page.eval_on_selector_all(
            ".cards .card", "es => es.slice(0, 4).map(e => e.textContent)")
        before = first()
        page.select_option("#sortBy", "total")
        page.wait_for_timeout(250)
        self.assertNotEqual(first(), before, "the dropdown did not reorder the cards")
        cheapest = first()
        page.click("#sortDir")
        page.wait_for_timeout(250)
        self.assertNotEqual(first(), cheapest, "the direction button did nothing")

    def test_the_sort_dropdown_and_the_header_are_one_control(self) -> None:
        """Two renderings of `state.sort`, never two sorts.

        The table header is still clickable on a laptop, so there are two ways
        to sort and one place the answer lives. A dropdown that did not follow
        a heading click would be a control stating an order the table is not
        in -- and the reader would have no way to know which of the two to
        believe.
        """
        page = self.open(1440, 900)
        self.addCleanup(page.close)
        read = lambda: page.evaluate("""() => ({
          value: document.getElementById('sortBy').value,
          dir: document.getElementById('sortDir').textContent.trim(),
          arrow: (document.querySelector('#head th[aria-sort]') || {}).dataset
                   ? document.querySelector('#head th[aria-sort]').dataset.k : null,
          order: document.querySelector('#head th[aria-sort]').getAttribute('aria-sort')
        })""")

        boot = read()
        self.assertEqual(boot["value"], boot["arrow"],
                         "the dropdown and the header disagree at boot")

        page.click('#head th[data-k="total"]')
        page.wait_for_timeout(200)
        clicked = read()
        self.assertEqual(clicked["value"], "total",
                         "clicking a heading did not move the dropdown")
        self.assertEqual(clicked["order"], "ascending")
        self.assertIn("Cheapest", clicked["dir"],
                      "the direction button does not say what cheap means here")

        page.click('#head th[data-k="total"]')
        page.wait_for_timeout(200)
        self.assertEqual(read()["order"], "descending")
        self.assertIn("Dearest", read()["dir"])

        # And the other way: the dropdown moves the header's own mark.
        page.select_option("#sortBy", "start")
        page.wait_for_timeout(200)
        back = read()
        self.assertEqual(back["arrow"], "start",
                         "picking a column did not move the header's mark")
        self.assertEqual(back["order"], "ascending",
                         "a new column did not start ascending, as a click does")
        self.assertIn("Earliest", back["dir"],
                      "a date is being described as cheap or dear")

    def test_the_toolbar_is_one_line_wherever_there_is_a_table(self) -> None:
        """Adding the sort may not cost the rows a line of chrome.

        Above the card breakpoint the toolbar does not wrap and the meta line
        ellipsises instead -- that rule was written for a toolbar holding two
        fewer controls, at a breakpoint (901px) that was the width the meta
        happened to stop fitting at. The sort pushed 761..900 onto two rows
        until the rule was tied to `narrow` instead, and this is what says so.
        """
        page = self.open(1440, 900)
        self.addCleanup(page.close)
        for width in (761, 768, 800, 900, 901, 1024, 1280, 1440):
            page.set_viewport_size({"width": width, "height": 800})
            page.wait_for_timeout(120)
            bar = page.evaluate("""() => {
              const t = document.querySelector('.toolbar');
              return { h: Math.round(t.getBoundingClientRect().height),
                       over: t.scrollWidth > t.clientWidth,
                       sort: Math.round(
                         document.getElementById('sortBy').getBoundingClientRect().width) };
            }""")
            where = "at %dpx" % width
            self.assertLessEqual(bar["h"], 48, "the toolbar wrapped " + where)
            self.assertFalse(bar["over"], "the toolbar overflows sideways " + where)
            self.assertGreater(bar["sort"], 0, "the sort dropdown is not drawn " + where)

    def test_the_phone_says_what_the_per_dive_figure_is(self) -> None:
        """The card's second euro figure, named by where it sits.

        On the card this was a bare number on the meta line, one gap from the
        mandatory-fee figure and in the same weight -- two euro amounts, and
        the only thing telling them apart was a column heading a phone does
        not draw. It sits under the total now, inside the same tinted box, and
        says "a dive" in words.

        Asserted on the card layout and on the table together: the table keeps
        its Per dive column, so a change that moved the figure on both would
        pass a test that only looked at one.
        """
        page = self.open(PHONE_WIDTHS[0], 720)
        self.addCleanup(page.close)
        for width in PHONE_WIDTHS:
            page.set_viewport_size({"width": width, "height": 720})
            page.wait_for_timeout(120)
            seen = page.evaluate("""() => {
              const card = document.querySelector('.cards .card');
              const money = card.querySelector('.card-money');
              const per = money.querySelector('.perline');
              return { cards: getComputedStyle(
                         document.querySelector('.shell > table')).display === 'none',
                       per: per ? per.textContent.trim() : null,
                       perRight: per ? Math.round(per.getBoundingClientRect().right) : 0,
                       vw: window.innerWidth,
                       strayInMeta: !!card.querySelector('.card-meta .perdive'),
                       metaEuros: (card.querySelector('.card-meta').textContent
                                   .match(/\u20ac/g) || []).length };
            }""")
            where = "at %dpx" % width
            self.assertTrue(seen["cards"], "not the card layout " + where)
            self.assertIsNotNone(seen["per"], "no per-dive line in the money box " + where)
            self.assertRegex(seen["per"], r"a dive|dives:",
                             "the per-dive line does not say what it is " + where)
            self.assertFalse(seen["strayInMeta"],
                             "per dive is still on the meta line too " + where)
            # One unnamed euro figure left on that line, and it is the fee --
            # which is why the second one had to go somewhere it is named.
            self.assertLessEqual(seen["metaEuros"], 1,
                                 "the meta line carries two euro figures again " + where)
            self.assertLessEqual(seen["perRight"], seen["vw"],
                                 "the per-dive line runs off the edge " + where)

        # The table is untouched: its own Per dive column still prints it.
        page.set_viewport_size({"width": 1440, "height": 900})
        page.wait_for_timeout(160)
        self.assertRegex(
            page.eval_on_selector("tbody tr.row td.perdive", "e => e.textContent"),
            r"\u20ac|stated",
            "the table lost the Per dive column the card no longer duplicates")

    def test_the_shell_is_the_window_and_nothing_pans_it(self) -> None:
        """The masthead, the rail and the footer stay where they are put.

        On iOS they did not. `html, body { height:100% }` resolves against the
        initial containing block, which there is the viewport with the URL bar
        *hidden* -- so with the bar showing, the shell was about 120px taller
        than the area it was being looked at through, and those 120px were
        slack the page could be panned through. Panned, the masthead and the
        rail went off the top and the footer floated over bare canvas at the
        bottom: the two things an app shell exists to pin, both unpinned.

        Headless Chromium has no collapsing browser chrome, so it cannot
        reproduce the pan. What it can hold is the invariant underneath it --
        the shell is exactly the window, the document has no scroll of its own,
        and the footer's bottom edge is the window's. Asserted as used values
        off the live layout, not as source text.

        It used to assert the `overscroll-behavior` declarations too, and those
        are gone: what they were for is asserted directly below instead, by
        flicking past the end of the table and checking the page did not move.
        A property is not the invariant -- the page staying put is.
        """
        page = self.open(PHONE_WIDTHS[0], 720)
        self.addCleanup(page.close)
        for width in PHONE_WIDTHS + [1440]:
            page.set_viewport_size({"width": width, "height": 760})
            page.wait_for_timeout(140)
            seen = page.evaluate("""() => {
              const root = document.documentElement;
              const shell = document.querySelector('.shell');
              const box = s => Math.round(
                document.querySelector(s).getBoundingClientRect().height);
              return {
                innerH: window.innerHeight,
                bodyH: Math.round(document.body.getBoundingClientRect().height),
                slack: root.scrollHeight - root.clientHeight,
                footerBottom: Math.round(
                  document.querySelector('.site-footer').getBoundingClientRect().bottom),
                masthead: box('.masthead'),
                rail: box('.rail'),
                shellScrolls: shell.scrollHeight > shell.clientHeight,
              };
            }""")
            where = "at %dpx" % width
            self.assertEqual(seen["bodyH"], seen["innerH"],
                             "the shell is not the window " + where)
            self.assertEqual(seen["slack"], 0,
                             "the document has room to be panned " + where)
            self.assertEqual(seen["footerBottom"], seen["innerH"],
                             "the footer is not on the bottom edge " + where)
            self.assertGreater(seen["masthead"], 0, "no masthead " + where)
            self.assertGreater(seen["rail"], 0, "no rail " + where)

            # What `overscroll-behavior` was declared for, asserted as the
            # outcome: drive the shell to its end and keep flicking, and the
            # page must not move. It cannot, because `overflow:hidden` on
            # `body` propagates to the viewport -- so the property was buying
            # nothing, and on WebKit it was what every dead scroller had in
            # common.
            self.assertTrue(seen["shellScrolls"],
                            "nothing to flick past " + where)
            page.evaluate("() => { const s = document.querySelector('.shell');"
                          " s.scrollTop = s.scrollHeight; }")
            page.mouse.move(width // 2, 400)
            page.mouse.wheel(0, 4000)
            page.wait_for_timeout(120)
            self.assertEqual(
                0, page.evaluate("Math.round(scrollY) + "
                                 "document.documentElement.scrollTop + "
                                 "document.body.scrollTop"),
                "a flick past the end of the table panned the page " + where)

        # And the shell asks for the visible viewport, not the tallest one it
        # could ever be. Chromium reports the two as equal, so the declaration
        # is what says a phone was thought about -- with `100%` kept under it.
        css = SITE.read_text(encoding="utf-8")
        self.assertIn("height:100dvh", css,
                      "the shell no longer sizes to the visible viewport")
        # Nothing goes looking for slack either. `dvh` removes it on iOS 15.4
        # and up; this covers every browser and every version below that.
        self.assertIn('history.scrollRestoration = "manual"', css,
                      "the browser may restore a scroll into a shell that has none")

    def test_the_shell_re_measures_when_the_window_changes(self) -> None:
        """A stale shell is the bug; a reflow is the fix, so reflow on cue.

        Backgrounding the app and returning fixed it, which says the layout was
        not wrong but *stale* -- one forced reflow and it snapped right. So the
        height is measured off `window.innerHeight` and re-measured on resize,
        rotation and `pageshow`, rather than declared once and trusted. `dvh`
        does this in CSS from Safari 15.4; this is what covers everything
        older, where the fallback is `100%` and `100%` is the wrong viewport.

        Resizing here drives the same path a rotating phone does.
        """
        page = self.open(390, 800)
        self.addCleanup(page.close)
        for w, h in ((390, 800), (390, 640), (844, 390), (390, 844), (1440, 900)):
            page.set_viewport_size({"width": w, "height": h})
            page.wait_for_timeout(420)  # past the second, settled measurement
            seen = page.evaluate("""() => ({
              innerH: window.innerHeight,
              bodyH: Math.round(document.body.getBoundingClientRect().height),
              footer: Math.round(
                document.querySelector('.site-footer').getBoundingClientRect().bottom),
              masthead: Math.round(
                document.querySelector('.masthead').getBoundingClientRect().top),
            })""")
            where = "at %dx%d" % (w, h)
            self.assertEqual(seen["bodyH"], seen["innerH"],
                             "the shell did not re-measure " + where)
            self.assertEqual(seen["footer"], seen["innerH"],
                             "the footer is off the bottom edge " + where)
            self.assertEqual(seen["masthead"], 0, "the masthead moved " + where)

    def test_opening_a_view_by_its_address_keeps_the_masthead(self) -> None:
        """Every entry point lands on the same page, chrome included.

        `#trips`, `#sale` and `#history` are addresses for this page's own
        router and match no element id, so the browser has nothing to scroll
        to -- and the two things that *can* move an `overflow:hidden` shell go
        through here: a fragment on load, and `showView` focusing the pane it
        just revealed. On iOS the second one took the masthead and the rail off
        the top, because focusing an element asks the browser to bring it into
        view and it obliged with the only box it could move.

        Driven both ways round: loaded cold at each address, and tapped through
        the rail, because only the second path focuses.
        """
        for hash_ in ("", "#trips", "#sale", "#history"):
            page = self.open(390, 800, hash_)
            try:
                seen = page.evaluate("""() => ({
                  masthead: Math.round(
                    document.querySelector('.masthead').getBoundingClientRect().top),
                  rail: Math.round(
                    document.querySelector('.rail').getBoundingClientRect().top),
                  footer: Math.round(
                    document.querySelector('.site-footer').getBoundingClientRect().bottom),
                  innerH: window.innerHeight,
                  scrolled: Math.round(window.scrollY
                    + document.body.scrollTop + document.documentElement.scrollTop),
                })""")
                where = "loading %s" % (hash_ or "with no hash")
                self.assertEqual(seen["masthead"], 0, "the masthead moved " + where)
                self.assertGreater(seen["rail"], 0, "the rail moved " + where)
                self.assertEqual(seen["footer"], seen["innerH"],
                                 "the footer left the bottom edge " + where)
                self.assertEqual(seen["scrolled"], 0, "something scrolled " + where)
            finally:
                page.close()

        # And through the rail, which is the path that focuses a pane.
        page = self.open(390, 800)
        self.addCleanup(page.close)
        for name in ("sale", "history", "trips"):
            page.click('.rail-item[href="#%s"]' % name)
            page.wait_for_timeout(220)
            seen = page.evaluate("""() => ({
              masthead: Math.round(
                document.querySelector('.masthead').getBoundingClientRect().top),
              scrolled: Math.round(window.scrollY
                + document.body.scrollTop + document.documentElement.scrollTop),
              focused: document.activeElement.className,
            })""")
            self.assertEqual(seen["masthead"], 0,
                             "the masthead moved on tapping " + name)
            self.assertEqual(seen["scrolled"], 0, "focus scrolled the shell " + name)
            self.assertIn("pane", seen["focused"],
                          "preventScroll cost the pane its focus, on " + name)

    def test_a_count_ordered_bank_re_ranks_when_its_counts_move(self) -> None:
        """A bank ordered by popularity is ordered by the *live* popularity.

        Dive sites is where it bites, because that bank is ANDed: pick
        Brothers and every other reef's number becomes "trips that visit
        both", which reshuffles the list completely. The chips stayed in the
        order they booted in, so the reefs that actually combine with Brothers
        sat behind "+34 more" while ones that barely do led the bank.

        And only where the order was a count. Months are chronological and the
        entry bar is ranked by how strict it is; re-ranking either by
        popularity would replace a meaning with a ranking, so the other half
        of this asserts they hold still while their numbers move.
        """
        page = self.open(1440, 900)
        self.addCleanup(page.close)
        read = lambda bank: page.eval_on_selector_all(
            "#%s .chip:not(.more)" % bank,
            "es => es.map(e => ({ v: e.dataset.v, n: +e.querySelector('.dim').textContent,"
            " on: e.getAttribute('aria-pressed') === 'true' }))")

        self.pick_filter(page, "sites", "#sites .chip")  # the most popular reef
        after = read("sites")
        self.assertTrue(after[0]["on"], "the chosen reef does not lead its bank")
        rest = [c["n"] for c in after if not c["on"]]
        self.assertEqual(rest, sorted(rest, reverse=True),
                         "the reefs left are not in the order of what is left")
        self.assertTrue(any(c["n"] for c in after[1:]),
                        "the bank came back empty, so this measured nothing")

        # The two banks whose order means something hold it, while the same
        # filter moves their numbers.
        page.click('.bank-tab[data-bank="months"]')
        page.wait_for_timeout(200)
        months = [c["v"] for c in read("months")]
        page.click('.bank-tab[data-bank="entry"]')
        page.wait_for_timeout(200)
        entry = [c["v"] for c in read("entry")]

        self.pick_filter(page, "sites", "#sites .chip:nth-of-type(2)")
        page.click('.bank-tab[data-bank="months"]')
        page.wait_for_timeout(200)
        self.assertEqual([c["v"] for c in read("months")], months,
                         "the months re-sorted themselves out of the calendar")
        page.click('.bank-tab[data-bank="entry"]')
        page.wait_for_timeout(200)
        self.assertEqual([c["v"] for c in read("entry")], entry,
                         "the entry bar re-sorted itself out of its ladder")

    def test_three_cells_that_read_as_one_value_stay_on_one_line(self) -> None:
        """Places, Seller and the entry bar, each on a line of its own.

        All three were stacked to keep a column narrow, and all three bought
        that narrowness with a second line on most of the rows in the table --
        row height is paid 1,122 times and column width once, out of the
        spacer. Worse, each pair reads as two values when it is one: "0" over
        "at this price" is a count and its unit, and "liveaboard" over "PADI"
        is the two sellers named. The entry bar was a third: a bar over a
        "2 sellers" footnote, one claim on two lines. That footnote has since
        gone -- the sentence is in the panel the bar opens -- so the cell is
        one line by having one thing in it, and this still measures it.

        Measured rather than grepped, and measured on the cells rather than on
        the stylesheet: the entry bar's mark is a *sibling* of the button, so
        `white-space` on the cell never governed it -- what put it on its own
        line was `display:flex` making the button a block box, which no source
        string about wrapping would have shown.

        Counted over every rendered row at three desktop widths, one in each
        of the table's three regimes.
        """
        page = self.open(1900, 1000)
        self.addCleanup(page.close)
        lines = """(sel) => {
          const rows = [...document.querySelectorAll('.shell tbody tr.row')];
          let worst = 0, where = '';
          for (const tr of rows) {
            const td = tr.querySelector(sel);
            if (!td) continue;
            const tops = [];
            const walk = document.createTreeWalker(td, NodeFilter.SHOW_TEXT);
            let n;
            while ((n = walk.nextNode())) {
              if (!n.textContent.trim()) continue;
              const r = document.createRange();
              r.selectNodeContents(n);
              for (const q of r.getClientRects()) {
                if (q.height < 2 || q.width < 1) continue;
                const mid = q.top + q.height / 2;
                if (!tops.some(t => Math.abs(t - mid) < 5)) tops.push(mid);
              }
            }
            if (tops.length > worst) { worst = tops.length; where = td.textContent.trim(); }
          }
          return { rows: rows.length, worst: worst, where: where };
        }"""
        for width in (1440, 1700, 1900):
            page.set_viewport_size({"width": width, "height": 1000})
            page.wait_for_timeout(250)
            for sel, name in (("td.places", "Places"),
                              ("td.source", "Seller"),
                              ("td.entry-col", "the entry bar")):
                seen = page.evaluate(lines, sel)
                self.assertTrue(seen["rows"], "no rows drawn, so this measured nothing")
                self.assertLessEqual(
                    seen["worst"], 1,
                    "%s wrapped onto %d lines at %dpx: %r"
                    % (name, seen["worst"], width, seen["where"]))

    def test_a_wide_window_stretches_the_spacer_and_not_the_money(self) -> None:
        """The columns hold their width, however much room there is.

        `min-width:100%` on the table means a window wider than the table
        stretches it, and auto layout hands the surplus to whatever is not
        pinned to a width. The five descriptive columns are pinned -- so the
        money was the only thing that could grow, and at 2560px the Total was
        478px wide for a 60px figure while Advertised took 312. Every row's
        figures drifted apart from the fees they are the sum of, on the widest
        screens, which is the one thing this table exists to line up.

        Measured across four widths against the narrowest: every real column
        identical to the pixel, the Total's right edge unmoved, and the spacer
        holding the whole difference. The row rule has to reach the edge too,
        which is why the spacer is a body column rather than a short row.
        """
        page = self.open(1440, 900)
        self.addCleanup(page.close)
        read = lambda: page.evaluate("""() => {
          const row = document.querySelector('tbody tr.row');
          const cols = {};
          [...document.querySelectorAll('#head tr:last-child th[data-k]')].forEach(
            th => { cols[th.dataset.k] = Math.round(th.getBoundingClientRect().width); });
          const sp = document.querySelector('#head tr:last-child th.sp');
          const last = row.querySelector('td.sp');
          return {
            cols: cols,
            spacer: Math.round(sp.getBoundingClientRect().width),
            totalRight: Math.round(row.querySelector('td.cost').getBoundingClientRect().right),
            rowRight: Math.round(row.getBoundingClientRect().right),
            ruleRight: last ? Math.round(last.getBoundingClientRect().right) : null,
            tableW: Math.round(
              document.querySelector('.shell > table').getBoundingClientRect().width),
          };
        }""")

        base = read()
        self.assertGreater(base["spacer"], 0, "no spacer column, so nothing absorbs a wide window")
        # From the widest regime up, because the columns are deliberately wider
        # above each of the two room steps -- what must not move is anything
        # *within* a regime, and 1900 is where the last of them lands.
        page.set_viewport_size({"width": 1900, "height": 900})
        page.wait_for_timeout(200)
        base = read()
        for width in (2000, 2560, 3200):
            page.set_viewport_size({"width": width, "height": 900})
            page.wait_for_timeout(200)
            seen = read()
            where = "at %dpx" % width
            self.assertEqual(seen["cols"], base["cols"],
                             "a real column changed width " + where)
            self.assertEqual(seen["totalRight"], base["totalRight"],
                             "the Total slid sideways " + where)
            self.assertGreater(seen["spacer"], base["spacer"],
                               "the spacer did not take the extra room " + where)
            # The row's own rule reaches the edge of the row, or the table is
            # underlined in one place and appears to end in another.
            self.assertIsNotNone(seen["ruleRight"], "the rows are a cell short " + where)
            self.assertEqual(seen["ruleRight"], seen["rowRight"],
                             "the row rule stops short of the row " + where)
            self.assertEqual(seen["tableW"], width - 148,  # less the rail
                             "the table is not filling the window " + where)

    def test_a_roomy_window_is_used_rather_than_left_beside_the_table(self) -> None:
        """Density set by the narrowest window, applied to the widest one.

        Twelve columns huddled into 1,329px of a 2560px screen at 5px padding
        and 47px rows, with a thousand pixels of nothing beside them. The table
        takes some of that back in two steps: at 1700 the two columns truncated
        at every width -- the trip name and the reefs -- get room to be
        truncated less, and at 1900 the padding and the row height go up too
        and those columns get the rest of theirs.

        Two steps because one no longer fits. Places, Seller and the entry bar
        stopped stacking their second line, which is 141px the table needs that
        1700px of window does not hold; splitting the step is what keeps those
        windows from falling back to the laptop table entirely.

        Both are numbers derived from the table's own content width, which is
        the mistake #150 was -- so what is asserted is not the numbers but the
        thing they are chosen to protect: at and above each, that step's table
        still fits its shell. If the fleet's names grow enough to eat the
        margin, this goes red rather than the Total going off the edge.
        """
        page = self.open(1440, 900)
        self.addCleanup(page.close)
        read = lambda: page.evaluate("""() => {
          const shell = document.querySelector('.shell');
          const row = document.querySelector('tbody tr.row');
          const w = s => Math.round(
            document.querySelector(s).getBoundingClientRect().width);
          return { rowH: Math.round(row.getBoundingClientRect().height),
                   trip: w('#head th[data-k="trip"]'),
                   sites: w('#head th[data-k="sites"]'),
                   later: w('#head th[data-k="later"]'),
                   tableW: w('.shell > table'),
                   shellW: shell.clientWidth,
                   sideways: shell.scrollWidth > shell.clientWidth };
        }""")

        tight = read()
        for width in (1500, 1699):
            page.set_viewport_size({"width": width, "height": 900})
            page.wait_for_timeout(200)
            self.assertEqual(read()["rowH"], tight["rowH"],
                             "the rows changed height below the first step, at %dpx" % width)

        # The first step buys the two truncated columns room and nothing else,
        # which is what makes it affordable at 1700: it costs the table no rows.
        for width in (1700, 1800, 1899):
            page.set_viewport_size({"width": width, "height": 900})
            page.wait_for_timeout(200)
            seen = read()
            where = "at %dpx" % width
            self.assertEqual(seen["rowH"], tight["rowH"],
                             "the first step cost the table rows " + where)
            self.assertGreater(seen["trip"], tight["trip"],
                               "the trip name got none of the first step " + where)
            self.assertGreater(seen["sites"], tight["sites"],
                               "the reefs got none of the first step " + where)
            self.assertFalse(seen["sideways"],
                             "the first step overflows its shell " + where +
                             " (%dpx of table in %dpx)" % (seen["tableW"], seen["shellW"]))

        first = read()
        for width in (1900, 2000, 2560, 3200):
            page.set_viewport_size({"width": width, "height": 900})
            page.wait_for_timeout(200)
            seen = read()
            where = "at %dpx" % width
            self.assertGreater(seen["rowH"], tight["rowH"],
                               "the rows are as tight as on a laptop " + where)
            self.assertGreater(seen["trip"], first["trip"],
                               "the trip name got none of the second step " + where)
            self.assertGreater(seen["sites"], first["sites"],
                               "the reefs got none of the second step " + where)
            # The whole point of the breakpoint: the roomier table still fits.
            self.assertFalse(seen["sideways"],
                             "the roomier table overflows its shell " + where +
                             " (%dpx of table in %dpx)" % (seen["tableW"], seen["shellW"]))
            # And the money did not become the roomy part. Mandatory fees is a
            # column of figures; it has no business taking a quarter of a row.
            self.assertLess(seen["later"], seen["tableW"] // 8,
                            "Mandatory fees is soaking up the width again " + where)

    def test_a_phone_can_open_all_three_panels_and_mark_a_row(self) -> None:
        """The triggers came across; the events did not.

        Every listener hung off `#body`, and below 760px that element is
        `display:none` and the rows are `#cards` — so on a phone not one of
        the three panels was wired to anything, and the fee bill, the cabin
        ladder and the entry bar were all dead. The row mark went the same way
        twice over: bound to the same `tbody`, and matching `tr.row` where a
        card is `article.card.row`.

        Nothing looked wrong, which is why this needs measuring rather than
        reading: a card cell is the same column's renderer, so the buttons
        rendered exactly right and only the clicks went nowhere.

        Asserted on the layout that has no table, and each panel has to be a
        *modal* as well as open — the sheet is what makes the list behind it
        inert, and a non-modal panel over a live scroller is the bug this
        mechanism replaced rather than fixed.
        """
        panels = ((".fees-open", "feePanel"), (".berths", "berths"),
                  (".entry-open", "entryPanel"))
        for width, height in ((360, 640), (390, 844), (430, 932)):
            page = self.open(width, height)
            try:
                self.assertTrue(
                    page.evaluate("()=>getComputedStyle("
                                  "document.querySelector('.shell > table'))"
                                  ".display === 'none'"),
                    "not the card layout at %dpx" % width)

                for selector, host in panels:
                    trigger = page.query_selector(".cards .card " + selector)
                    self.assertIsNotNone(
                        trigger, "no %s on a card at %dpx" % (selector, width))
                    trigger.click()
                    page.wait_for_timeout(320)
                    seen = page.evaluate("""(id) => {
                      const el = document.getElementById(id);
                      const box = el.getBoundingClientRect();
                      return { open: el.open, modal: el.matches(':modal'),
                               fits: box.left >= -1 && box.right <= innerWidth + 1
                                     && box.top >= -1 && box.bottom <= innerHeight + 1,
                               w: Math.round(box.width),
                               text: el.querySelector('.pbody').textContent.trim().length,
                               head: el.querySelector('.phead').textContent.trim().length };
                    }""", host)
                    where = "%s at %dx%d" % (selector, width, height)
                    self.assertTrue(seen["open"], "the panel did not open, " + where)
                    self.assertTrue(seen["modal"],
                                    "the panel is not modal, so the list behind "
                                    "it is still live, " + where)
                    self.assertGreater(seen["w"], 0, "the panel is empty, " + where)
                    self.assertGreater(seen["text"], 0, "nothing was filled in, " + where)
                    self.assertTrue(seen["fits"], "the panel is off screen, " + where)
                    # The heading sits in the bar beside the way out. Left in
                    # the body it put a blank band above itself — two headers,
                    # one of them empty, at the top of every sheet.
                    self.assertGreater(seen["head"], 0,
                                       "the panel's header is empty, " + where)
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(180)
                    self.assertFalse(
                        page.evaluate("(id) => document.getElementById(id).open", host),
                        "Escape did not close the panel, " + where)

                # And the row mark, on the surface a card actually has.
                page.click(".cards .card .card-trip")
                page.wait_for_timeout(280)
                self.assertTrue(
                    page.eval_on_selector(
                        ".cards .card", "e => e.classList.contains('marked')"),
                    "tapping a card does not mark it at %dpx" % width)
            finally:
                page.close()


    def test_a_phone_bill_needs_no_sideways_drag(self) -> None:
        """The fee table's five columns want 460px and a phone panel has 353.

        So the bill opened with its tier and its provenance past the right
        edge, and reading them meant dragging the table inside its own
        `.fee-scroll`: 107px of travel in a box narrow enough that a flick
        reaching the end handed itself to the panel behind. Below 760px a fee
        line is stacked instead — the money beside what it is for, the tier
        and the note under it — so nothing in the panel scrolls sideways at
        all.

        Measured on the panel's own boxes rather than read off the
        stylesheet: the claim is geometry, and the row that would break it is
        one long operator name pushing the amount past the edge.

        The second seller's bill is a second `table.fees` in the same panel,
        so a bill with two of them is what this opens: a rule that reaches
        only the first table is a rule that fixes half the panel.
        """
        for width, height in ((320, 640), (360, 640), (390, 844), (430, 932)):
            page = self.open(width, height)
            try:
                found = page.evaluate("""() => {
                  const opens = [...document.querySelectorAll('.cards .card .fees-open')];
                  for (const open of opens) {
                    open.click();
                    if (document.querySelectorAll('#feePanel table.fees').length > 1)
                      return true;
                  }
                  return false;
                }""")
                self.assertTrue(found, "no two-seller bill to open at %dpx" % width)
                page.wait_for_timeout(320)
                seen = page.evaluate("""() => {
                  const panel = document.getElementById('feePanel');
                  const wide = [...panel.querySelectorAll('*')]
                    .filter(el => el.scrollWidth > el.clientWidth + 1)
                    .map(el => String(el.className) + ' ' + el.clientWidth +
                               '->' + el.scrollWidth);
                  const box = panel.getBoundingClientRect();
                  const right = Math.round(box.right), left = Math.round(box.left);
                  const amounts = [...panel.querySelectorAll('.famt')]
                    .map(el => Math.round(el.getBoundingClientRect().right));
                  const labels = [...panel.querySelectorAll('.flabel')]
                    .map(el => Math.round(el.getBoundingClientRect().left));
                  return { open: panel.open, wide: wide,
                           tables: panel.querySelectorAll('table.fees').length,
                           right: right, past: amounts.filter(x => x > right).length,
                           amounts: amounts.length,
                           gapRight: amounts.length ? Math.min(...amounts.map(x => right - x)) : -1,
                           gapLeft: labels.length ? Math.min(...labels.map(x => x - left)) : -1 };
                }""")
                where = "at %dx%d" % (width, height)
                self.assertTrue(seen["open"], "the bill did not open " + where)
                self.assertGreater(seen["tables"], 1, "one bill only " + where)
                self.assertGreater(seen["amounts"], 0, "no amounts " + where)
                self.assertEqual([], seen["wide"],
                                 "the bill scrolls sideways " + where)
                self.assertEqual(0, seen["past"],
                                 "%d amount(s) past the panel's edge %s"
                                 % (seen["past"], where))
                # And not flush against it either. The rewrite that made these
                # dialogs dropped the bill's horizontal padding, and every
                # amount sat hard against the right edge of the screen — which
                # the assertion above could not see, because it compared the
                # amounts with the panel's edge and the panel ended exactly
                # there. A phone screenshot is what found it. So the claim is
                # clearance, on both sides, in the panel's own boxes.
                self.assertGreaterEqual(seen["gapRight"], 6,
                                        "the amounts are flush against the "
                                        "panel's right edge (%dpx) %s"
                                        % (seen["gapRight"], where))
                self.assertGreaterEqual(seen["gapLeft"], 6,
                                        "the fee labels are flush against the "
                                        "panel's left edge (%dpx) %s"
                                        % (seen["gapLeft"], where))
            finally:
                page.close()


    def test_the_scroll_pays_for_one_host_and_the_other_catches_up(self) -> None:
        """A page appended on scroll was parsed twice, once for nothing.

        Both hosts are always filled — that is the contract that lets a
        rotation cross the breakpoint with no redraw and `Ctrl+F` find
        either — but below 760px `#body` is `display:none` and above it
        `#cards` is, so half of every append was built inside the frame the
        reader was waiting on, for a layout nobody was looking at.

        So: the host on screen grows in the scroll, the other one a task
        later, and `Ctrl+F` flushes both synchronously because a host a page
        behind is the silent truncation the append exists to avoid. All three
        halves need asserting — the split is invisible once it has settled,
        which is exactly how it would come back.
        """
        for width, host, other in ((390, "cards", "body"), (1440, "body", "cards")):
            page = self.open(width, 844)
            try:
                count = ("(id) => document.getElementById(id).children.length")
                before = (page.evaluate(count, host), page.evaluate(count, other))
                # Scroll to the trigger and read both hosts before the flush.
                grew = page.evaluate("""(hosts) => {
                  const [host, other] = hosts;
                  const shell = document.querySelector('.shell');
                  const n = id => document.getElementById(id).children.length;
                  const was = [n(host), n(other)];
                  shell.scrollTop = shell.scrollHeight;
                  shell.dispatchEvent(new Event('scroll'));
                  return { host: n(host) - was[0], other: n(other) - was[1] };
                }""", [host, other])
                where = "at %dpx" % width
                self.assertGreater(grew["host"], 0,
                                   "the host on screen did not grow " + where)
                self.assertEqual(0, grew["other"],
                                 "the off-screen host was filled in the "
                                 "scroll " + where)
                page.wait_for_timeout(200)
                after = (page.evaluate(count, host), page.evaluate(count, other))
                self.assertEqual(after[0], after[1],
                                 "the hosts disagree after the flush " + where)
                self.assertGreater(after[0], before[0],
                                   "nothing was appended " + where)

                # And find-in-page leaves neither host short.
                page.keyboard.press("Control+f")
                page.wait_for_timeout(60)
                whole = page.evaluate("""() => {
                  const n = id => document.getElementById(id).children.length;
                  return [n('body'), n('cards')];
                }""")
                self.assertEqual(whole[0], whole[1],
                                 "Ctrl+F left the hosts disagreeing " + where)
                self.assertGreater(whole[0], after[0],
                                   "Ctrl+F drew no more rows " + where)
            finally:
                page.close()


    def test_a_swipe_off_a_panel_trigger_does_not_open_it(self) -> None:
        """A finger has no hover state, and `pointerover` fires for one anyway.

        It arrives on touchstart, before the drag that follows is known to be
        a drag — so a swipe beginning on one of the three panel buttons opened
        that panel 120ms later, over the list, and the scroll died under it. A
        card's meta row is three of those buttons, so most swipes on a phone
        started on one: five to ten cards a gesture.

        Only reachable since the listeners moved off the `tbody` onto
        `.shell`; before that nothing on a phone was wired at all. Both halves
        are asserted, because the cure for the swipe is exactly what would
        break the tap: touch has no other way in.
        """
        page = self._browser.new_page(
            viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
        cdp = page.context.new_cdp_session(page)
        try:
            page.goto(self._url)
            page.wait_for_selector(".cards .card .berths")
            page.evaluate("""() => {
              window.__opened = [];
              ["berths", "feePanel", "entryPanel"].forEach(id => {
                const el = document.getElementById(id);
                new MutationObserver(() => {
                  if (el.open) window.__opened.push(id);
                }).observe(el, { attributes: true, attributeFilter: ["open"] });
              });
            }""")
            spot = page.evaluate("""() => {
              const r = document.querySelector('.cards .card .berths')
                .getBoundingClientRect();
              return { x: Math.round(r.x + r.width / 2),
                       y: Math.round(r.y + r.height / 2) };
            }""")

            def finger(kind, y=None):
                points = [] if kind == "touchEnd" else [{"x": spot["x"], "y": y}]
                cdp.send("Input.dispatchTouchEvent",
                         {"type": kind, "touchPoints": points})

            # A swipe: down on the trigger, then away, over more than the
            # 120ms the hover timer waits.
            finger("touchStart", spot["y"])
            page.wait_for_timeout(200)
            for dy in (40, 120, 240, 400):
                finger("touchMove", spot["y"] - dy)
                page.wait_for_timeout(40)
            finger("touchEnd")
            page.wait_for_timeout(400)
            self.assertEqual([], page.evaluate("window.__opened"),
                             "a swipe off the Places button opened its panel")
            self.assertGreater(page.evaluate("document.querySelector('.shell').scrollTop"),
                               0, "the swipe did not scroll at all")

            # And a tap on the same button still opens it, which is the only
            # way in on a touch screen.
            page.evaluate("() => { document.querySelector('.shell').scrollTop = 0; }")
            page.wait_for_timeout(200)
            spot = page.evaluate("""() => {
              const r = document.querySelector('.cards .card .berths')
                .getBoundingClientRect();
              return { x: Math.round(r.x + r.width / 2),
                       y: Math.round(r.y + r.height / 2) };
            }""")
            page.touchscreen.tap(spot["x"], spot["y"])
            page.wait_for_timeout(300)
            self.assertTrue(page.evaluate("document.getElementById('berths').open"),
                            "a tap on the Places button no longer opens it")
        finally:
            page.close()


    def test_every_panel_can_be_closed_without_a_keyboard(self) -> None:
        """These are dialogs, and a phone has no Escape key.

        The old overlay was dismissed by Escape, by tapping the trigger again,
        or by tapping outside — and on a phone the first does not exist and
        the other two are *underneath* the panel. At 402x684 the bill is 479px
        over a 452px list, so it covers the rows whole; a swipe there scrolled
        a few hundred pixels of bill and then nothing, which from the outside
        is a page whose scrolling has died.

        Two ways out now, and both are asserted at the size the device
        reported. The close control is a real tap target (44px, because a
        multiplication sign is not something a thumb can aim at) and it cannot
        scroll away — it is a flex sibling of the scroll box rather than a
        sticky child of it, which is the same bug one layer down. And the
        backdrop closes it, which is the platform's own light-dismiss and the
        gesture a sheet invites: no handler of ours decides what counts as
        outside.
        """
        page = self._browser.new_page(
            viewport={"width": 402, "height": 684}, has_touch=True, is_mobile=True)
        try:
            page.goto(self._url)
            page.wait_for_selector("article.card")
            panels = ((".fees-open", "feePanel"), (".berths", "berths"),
                      (".entry-open", "entryPanel"))
            for selector, host in panels:
                page.locator(".cards .card " + selector).first.click()
                page.wait_for_timeout(280)
                seen = page.evaluate("""(id) => {
                  const panel = document.getElementById(id);
                  const shut = panel.querySelector('.pshut');
                  const body = panel.querySelector('.pbody');
                  if (!shut || !body) return { open: panel.open, missing: true };
                  const before = shut.getBoundingClientRect();
                  body.scrollTop = body.scrollHeight;
                  const after = shut.getBoundingClientRect();
                  return {
                    open: panel.open, missing: false,
                    w: Math.round(before.width), h: Math.round(before.height),
                    drifted: Math.round(Math.abs(after.top - before.top)),
                    inside: after.top >= panel.getBoundingClientRect().top - 1,
                  };
                }""", host)
                where = "%s at 402x684" % selector
                self.assertTrue(seen["open"], "the panel did not open, " + where)
                self.assertFalse(seen["missing"],
                                 "no way to close the panel, " + where)
                self.assertGreaterEqual(min(seen["w"], seen["h"]), 44,
                                        "the close control is %dx%d, too small "
                                        "for a thumb, %s"
                                        % (seen["w"], seen["h"], where))
                self.assertEqual(0, seen["drifted"],
                                 "the way out scrolled away with the bill, "
                                 + where)
                self.assertTrue(seen["inside"],
                                "the close control left the panel, " + where)

                page.locator("#" + host + " .pshut").click()
                page.wait_for_timeout(240)
                self.assertFalse(
                    page.evaluate("(id) => document.getElementById(id).open", host),
                    "pressing close did not close the panel, " + where)

                # And the backdrop, which is the only way out a reader finds
                # without being told. A press on it targets the dialog element
                # itself, so the sheet's own content cannot be mistaken for it.
                page.locator(".cards .card " + selector).first.click()
                page.wait_for_timeout(280)
                self.assertTrue(
                    page.evaluate("(id) => document.getElementById(id).open", host),
                    "the panel did not reopen, " + where)
                page.touchscreen.tap(200, 30)   # the masthead, behind the backdrop
                page.wait_for_timeout(280)
                self.assertFalse(
                    page.evaluate("(id) => document.getElementById(id).open", host),
                    "pressing the backdrop did not close the panel, " + where)
        finally:
            page.close()


    def test_the_list_behind_a_panel_is_inert_and_survives_it(self) -> None:
        """The bug every earlier fix was aimed at, asserted as an outcome.

        A panel that covers the rows without being modal leaves them live
        underneath it: the swipe lands on the panel, scrolls the panel to its
        end, and stops. Nothing on screen says a dialog is open, so what the
        reader sees is a list that has stopped moving — and four fixes went
        out against that report aimed at hover timers, listener hosts and
        `overscroll-behavior`, all of them true findings about other bugs.

        `showModal()` is what makes it unreachable rather than fixed, so what
        is measured is the property that does it: with a panel open the list
        behind must not move at all, and after it closes it must scroll
        exactly as it did before. Three rounds, because a mechanism that
        leaks state leaks it on the second one.
        """
        page = self._browser.new_page(
            viewport={"width": 402, "height": 684}, has_touch=True, is_mobile=True)
        cdp = page.context.new_cdp_session(page)
        try:
            page.goto(self._url)
            page.wait_for_selector("article.card")

            def swipe(x: int, y: int, dist: int) -> None:
                cdp.send("Input.dispatchTouchEvent",
                         {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]})
                for step in range(1, 7):
                    cdp.send("Input.dispatchTouchEvent",
                             {"type": "touchMove",
                              "touchPoints": [{"x": x, "y": y - dist * step // 6}]})
                    page.wait_for_timeout(25)
                cdp.send("Input.dispatchTouchEvent",
                         {"type": "touchEnd", "touchPoints": []})
                page.wait_for_timeout(320)

            def top() -> int:
                return page.evaluate(
                    "()=>Math.round(document.querySelector('.shell').scrollTop)")

            for round_ in range(3):
                where = "on round %d" % (round_ + 1)
                before = top()
                swipe(200, 500, 300)
                self.assertGreater(top(), before,
                                   "the list did not scroll " + where)

                page.locator(".cards .card .fees-open").nth(round_).click()
                page.wait_for_timeout(300)
                self.assertTrue(
                    page.evaluate("()=>document.getElementById('feePanel').open"),
                    "the bill did not open " + where)
                held = top()
                swipe(200, 500, 300)
                self.assertEqual(held, top(),
                                 "the list scrolled underneath an open panel "
                                 + where)
                self.assertTrue(
                    page.evaluate("()=>document.getElementById('feePanel').open"),
                    "swiping the sheet closed it " + where)

                page.locator("#feePanel .pshut").click()
                page.wait_for_timeout(260)

            before = top()
            swipe(200, 500, 300)
            self.assertGreater(top(), before,
                               "the list stopped scrolling after three panels")
        finally:
            page.close()


    def test_a_desktop_bill_is_read_at_its_own_width(self) -> None:
        """The bill wants 460px of columns, and it shipped in 336.

        `.panel.bill-pop` was written up beside the fee table and won its
        30–46em on specificity against `.panel-pop`; the rewrite that made
        these dialogs turned that shared rule into `.panel:not(:modal)`, which
        is a class's worth as well. At 0,2,0 apiece the later of the two won,
        so on a 1440px screen the whole bill — both sellers' tables, the tier,
        the provenance — was read by dragging it sideways inside a 336px box.
        A screenshot found it; every string assertion about the panel passed
        over it, which is what this file is for.

        Measured on the panel's own boxes at three desktop widths, on a bill
        with *both* sellers' tables: a rule reaching only the first table
        fixes half the panel.
        """
        page = self.open(1440, 900)
        try:
            for width, height in ((1440, 900), (1280, 800), (900, 800)):
                page.set_viewport_size({"width": width, "height": height})
                page.wait_for_timeout(150)
                found = page.evaluate("""() => {
                  document.querySelectorAll('dialog').forEach(d => d.close());
                  for (const open of document.querySelectorAll('#body .fees-open')) {
                    open.click();
                    if (document.querySelectorAll('#feePanel table.fees').length > 1)
                      return true;
                  }
                  return false;
                }""")
                where = "at %dx%d" % (width, height)
                self.assertTrue(found, "no two-seller bill to open " + where)
                page.wait_for_timeout(280)
                seen = page.evaluate("""() => {
                  const panel = document.getElementById('feePanel');
                  const wide = [...panel.querySelectorAll('*')]
                    .filter(el => el.scrollWidth > el.clientWidth + 1)
                    .map(el => String(el.className) + ' ' + el.clientWidth +
                               '->' + el.scrollWidth);
                  const tables = [...panel.querySelectorAll('table.fees')]
                    .map(t => Math.round(t.getBoundingClientRect().right));
                  const box = panel.getBoundingClientRect();
                  return { open: panel.open, wide: wide,
                           tables: tables.length,
                           w: Math.round(box.width),
                           past: tables.filter(x => x > Math.round(box.right)).length };
                }""")
                self.assertTrue(seen["open"], "the bill did not open " + where)
                self.assertGreater(seen["tables"], 1, "one bill only " + where)
                self.assertEqual([], seen["wide"],
                                 "the desktop bill scrolls sideways " + where)
                self.assertEqual(0, seen["past"],
                                 "a fee table runs past the panel's edge " + where)
                # The number the width has to clear is the fee table's own
                # `min-width`, which is what makes the columns line up.
                self.assertGreaterEqual(seen["w"], 460,
                                        "the bill is %dpx wide, narrower than "
                                        "the table inside it %s"
                                        % (seen["w"], where))

            # And in the state the bill is not shown in. Nothing opens it as a
            # peek today — it is pressed, like the entry bar — so this is a
            # claim about the rule rather than about a gesture: `.bill-pop` is
            # the wide panel in either of the dialog's two states, and it is
            # the peek that shipped at 336px. A peek coming back for a fourth
            # panel, or for this one, finds the width already settled.
            peeked = page.evaluate("""() => {
              const panel = document.getElementById('feePanel');
              panel.close();
              panel.show();
              const box = panel.getBoundingClientRect();
              const wide = [...panel.querySelectorAll('*')]
                .filter(el => el.scrollWidth > el.clientWidth + 1).length;
              const modal = panel.matches(':modal');
              panel.close();
              return { w: Math.round(box.width), wide: wide, modal: modal };
            }""")
            self.assertFalse(peeked["modal"], "show() opened a modal")
            self.assertGreaterEqual(peeked["w"], 460,
                                    "shown as a peek the bill is %dpx, "
                                    "narrower than the table inside it"
                                    % peeked["w"])
            self.assertEqual(0, peeked["wide"],
                             "shown as a peek the bill scrolls sideways")
        finally:
            page.close()

    def test_the_bill_opens_on_a_press_and_the_ladder_still_peeks(self) -> None:
        """Both halves, because the cure for one would be wrong for the other.

        The bill is the widest thing this page draws, and a peek of it lands
        over the rows a reader is comparing from a pointer that was on its way
        somewhere else — so it opens on a press, like the entry bar. The cabin
        ladder is 21em beside the cell it belongs to and comparing ladders
        down a column is what it is for, so its peek stays. A rule that turns
        hover off for the panels would pass half of this test.
        """
        page = self.open(1440, 900)
        try:
            page.hover("#body .fees-open")
            page.wait_for_timeout(600)
            self.assertFalse(
                page.evaluate("()=>document.getElementById('feePanel').open"),
                "the bill opened on a hover")
            page.click("#body .fees-open")
            page.wait_for_timeout(300)
            self.assertTrue(
                page.evaluate("()=>document.getElementById('feePanel').open"),
                "the bill did not open on a press")
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)

            page.hover("#body .berths")
            page.wait_for_timeout(600)
            self.assertTrue(
                page.evaluate("()=>document.getElementById('berths').open"),
                "the cabin ladder no longer peeks on hover")
            self.assertFalse(
                page.evaluate("()=>document.getElementById('berths').matches(':modal')"),
                "the hover opened the ladder pinned rather than as a peek")
        finally:
            page.close()

    def test_a_cabin_ladder_fits_the_panel_it_opens_in(self) -> None:
        """A ladder is three columns of its own and it grew a scrollbar.

        `tbody td { white-space: nowrap }` is the *table's* rule — a
        departure's cells stay one line down a column — and the ladder is a
        `tbody` too, so "Standard Sea View Cabin - Main Deck" set the table's
        minimum at 403px inside a 334px peek. Nine of the first forty
        sailings did it, under ladders with room to spare.

        The peek is the size that finds this: the pinned panel is 618px and
        every ladder fits it.
        """
        page = self.open(1712, 864)
        try:
            bad = []
            for n in range(24):
                page.hover("#body .berths >> nth=%d" % n)
                page.wait_for_timeout(200)
                seen = page.evaluate("""() => {
                  const panel = document.getElementById('berths');
                  if (!panel.open) return null;
                  const body = panel.querySelector('.pbody');
                  return { c: body.clientWidth, s: body.scrollWidth,
                           modal: panel.matches(':modal') };
                }""")
                if seen is None:
                    continue
                self.assertFalse(seen["modal"], "a hover pinned the ladder")
                if seen["s"] > seen["c"] + 1:
                    bad.append("row %d: %d->%d" % (n, seen["c"], seen["s"]))
            self.assertEqual([], bad, "the ladder scrolls sideways in its panel")
        finally:
            page.close()

    def test_a_discounted_row_prints_the_fare_the_rate_came_off(self) -> None:
        """And prints it inside the column, which is why this is measured.

        The rate sat alone under the fare for want of 36px, and the fare it
        came off was in a `title` — a claim the reader could not check on the
        page that exists to make prices checkable. The struck figure goes on
        the same line, to the left of the rate so the rate keeps its place
        down the column, and it has to fit: `.marks` is right-aligned, so
        anything too wide grows out of the left of the cell and over the
        column beside it.

        Measured at the three widths the table has regimes at, because the
        money columns are content-sized and what fits at 1440 is not what fits
        at 900.
        """
        page = self.open(1440, 900)
        try:
            for width, height in ((1440, 900), (1280, 800), (900, 800)):
                page.set_viewport_size({"width": width, "height": height})
                page.wait_for_timeout(180)
                seen = page.evaluate("""() => {
                  const rows = [...document.querySelectorAll('#body td.money')]
                    .filter(td => td.querySelector('.sale-mark'));
                  const withWas = rows.filter(td => td.querySelector('.sale-was'));
                  const out = withWas.filter(td => {
                    const cell = td.getBoundingClientRect();
                    const was = td.querySelector('.sale-was').getBoundingClientRect();
                    return was.left < cell.left + 1 || was.right > cell.right + 1;
                  }).length;
                  const order = withWas.filter(td => {
                    const was = td.querySelector('.sale-was').getBoundingClientRect();
                    const pct = td.querySelector('.sale-mark').getBoundingClientRect();
                    return was.right > pct.left + 1;
                  }).length;
                  return { marked: rows.length, was: withWas.length,
                           out: out, order: order,
                           text: withWas.length
                             ? withWas[0].querySelector('.sale-was').textContent : "" };
                }""")
                where = "at %dx%d" % (width, height)
                self.assertGreater(seen["marked"], 0,
                                   "no discounted row on the first page " + where)
                self.assertGreater(seen["was"], 0,
                                   "a rate is printed with no fare beside it "
                                   + where)
                self.assertRegex(seen["text"], r"^€[\d,]+$",
                                 "the struck figure is not a price " + where)
                self.assertEqual(0, seen["out"],
                                 "%d struck fare(s) sit outside the Advertised "
                                 "column %s" % (seen["out"], where))
                self.assertEqual(0, seen["order"],
                                 "the struck fare overlaps the rate " + where)
        finally:
            page.close()

        # And on a card, where there is no Advertised column at all: the berth
        # price reaches the reader inside the total's split and the markdown
        # reached them nowhere, so a discounted sailing looked exactly like a
        # full-price one on the device this page is built for.
        for width in (320, 390, 430):
            page = self.open(width, 844)
            try:
                seen = page.evaluate("""() => {
                  const card = [...document.querySelectorAll('#cards .card')]
                    .find(c => c.querySelector('.sale-was'));
                  if (!card) return null;
                  const money = card.querySelector('.card-money');
                  const line = card.querySelector('.saleline');
                  const box = money.getBoundingClientRect();
                  const r = line.getBoundingClientRect();
                  const gap = card.querySelector('.sale-mark')
                    .getBoundingClientRect().left
                    - card.querySelector('.sale-was').getBoundingClientRect().right;
                  return { inMoney: money.contains(line),
                           text: line.textContent.replace(/\\s+/g, " ").trim(),
                           out: r.left < box.left - 1 || r.right > box.right + 1,
                           gap: Math.round(gap),
                           wide: [...card.querySelectorAll('*')]
                             .filter(e => e.scrollWidth > e.clientWidth + 1).length };
                }""")
                where = "at %dpx" % width
                self.assertIsNotNone(seen, "no discounted card " + where)
                self.assertTrue(seen["inMoney"],
                                "the markdown is not in the money block " + where)
                # No spaces expected between them: the separation is the
                # flex gap asserted below, and the text is three elements.
                self.assertRegex(seen["text"], r"^was\s*€[\d,]+\s*−\d+%$",
                                 "the card's markdown reads %r %s"
                                 % (seen["text"], where))
                self.assertFalse(seen["out"],
                                 "the markdown sits outside the money block "
                                 + where)
                # The table's gap is written on `td.money .marks`, which a card
                # has no cells to match: without one of its own the struck fare
                # and the rate print as one word.
                self.assertGreaterEqual(seen["gap"], 3,
                                        "the struck fare and the rate are "
                                        "printed as one word " + where)
                self.assertEqual(0, seen["wide"],
                                 "the card scrolls sideways " + where)
            finally:
                page.close()

    def test_every_filter_counts_what_it_leaves(self) -> None:
        """Eight filters, and the On sale chip was the one that lied.

        `countRail` counted the trips with the sale facet skipped — which is
        the arithmetic a *chip's* own count needs, so that its number answers
        "what if I picked this too?" rather than "what did I already pick".
        The rail is not a chip: it is the table's own size printed beside the
        item that opens the table, and with On sale down it read 1,145 over
        237 rows, a few inches from a `rows shown` saying 237. Hide sold out
        sits in the same bank and collapsed onto the rows correctly, which is
        what makes this a slip rather than a policy.

        So every bank is pressed here, and each one is asked three questions
        rather than "did anything change":

        1. The three numbers about one table agree — the chip's own count of
           what pressing it leaves, `rows shown`, and the rail.
        2. The rows left really are the rows asked for, checked against what
           each row itself prints (or, for the month, against the ISO date in
           its own `data-id`).
        3. The result is neither the whole table nor nothing. A filter that
           keeps everything and one that keeps nothing both pass any
           assertion phrased as "the count changed", and both are bugs.

        And for the four facets the payload settles by itself, the expected
        figure is counted from the shipped bytes rather than taken from the
        page: a browser agreeing with itself is not evidence.
        """
        payload = shipped_payload()
        deps = payload["departures"]
        total = len(deps)

        # Counted here, from the data — not read off the page.
        month = 5
        boat_id = "snefro-pearl"
        expected = {
            "months": sum(1 for d in deps if d["month"] == month),
            "boats": sum(1 for d in deps if d["boat_id"] == boat_id),
            "flags-sale": sum(1 for d in deps if d.get("sale")),
            "flags-sold": sum(1 for d in deps if d.get("bookable")),
        }
        for key, n in expected.items():
            self.assertGreater(n, 0, "no departures behind the %s filter" % key)
            self.assertLess(n, total,
                            "the %s filter would keep the whole table" % key)

        # Each bank: the chip to press, and what every row it leaves must show
        # for itself. The predicate reads the row rather than the payload —
        # what is being checked is that the rows on screen are the right ones.
        banks = [
            ("months", '#months .chip[data-v="%d"]' % month, "months",
             """(row, v) => row.dataset.id.indexOf("-2027-0" + v + "-") > 0
                          || row.dataset.id.indexOf("-2027-" + v + "-") > 0""",
             str(month)),
            ("ports", '#ports .chip[data-v="Port Ghalib"]', None,
             """(row, v) => (row.querySelector('.trip .sub')||{}).textContent
                            .trim().indexOf(v) === 0""", "Port Ghalib"),
            ("sites", '#sites .chip[data-v="elphinstone"]', None,
             """(row, v) => row.querySelector('td.sites').textContent
                            .indexOf(v) >= 0""", "elphinstone"),
            ("boats", '#boats .chip[data-v="Snefro Pearl"]', "boats",
             """(row, v) => row.querySelector('.b-name').textContent.trim() === v""",
             "Snefro Pearl"),
            # The printed phrase, exactly: "OW + 10" is a different bar from
            # "OW", and a prefix test would call one the other.
            ("entry", '#entry .chip[data-v="OW + 10"]', None,
             """(row, v) => row.querySelector('.entry-open')
                            .firstChild.textContent.trim() === v""", "OW + 10"),
            # Both sellers list it, so both name themselves in the Seller
            # column — one link is the other two chips' answer.
            ("sellers", '#sellers .chip[data-v="both"]', None,
             """(row) => row.querySelectorAll('td.source a').length === 2""",
             "both"),
            ("flags", "#onSale", "flags-sale",
             """(row) => !!row.querySelector('.sale-mark')""", None),
            ("flags", "#hideSold", "flags-sold",
             """(row) => !row.classList.contains('gone')""", None),
        ]

        for bank, selector, counted, predicate, value in banks:
            page = self.open(1440, 900)
            try:
                self.pick_filter(page, bank, selector)
                seen = page.evaluate(
                    """([selector, source, value]) => {
                      const chip = document.querySelector(selector);
                      const label = chip.textContent.trim();
                      const stated = (label.match(/([\\d,]+)\\s*$/) || [])[1];
                      const test = eval(source);
                      const rows = [...document.querySelectorAll('#body tr.row')];
                      const v = value === null ? label.replace(/\\s*[\\d,]+$/, "") : value;
                      return {
                        label: label,
                        stated: stated ? Number(stated.replace(/,/g, "")) : null,
                        shown: Number(document.getElementById('shown')
                                 .textContent.replace(/,/g, "")),
                        rail: Number(document.getElementById('navTripsCount')
                                 .textContent.replace(/,/g, "")),
                        drawn: rows.length,
                        wrong: rows.filter(r => !test(r, v)).length,
                        first: rows.length ? rows[0].dataset.id : null,
                        value: v,
                      };
                    }""", [selector, predicate, value])
                where = "%s (%s)" % (selector, seen["label"])
                self.assertIsNotNone(seen["stated"],
                                     "the chip states no count: " + where)
                self.assertEqual(seen["stated"], seen["shown"],
                                 "the chip promised %s and the table shows %s: %s"
                                 % (seen["stated"], seen["shown"], where))
                self.assertEqual(seen["shown"], seen["rail"],
                                 "the rail says %s over a table of %s: %s"
                                 % (seen["rail"], seen["shown"], where))
                self.assertGreater(seen["shown"], 0, "nothing left: " + where)
                self.assertLess(seen["shown"], total,
                                "the filter kept the whole table: " + where)
                self.assertGreater(seen["drawn"], 0, "no rows drawn: " + where)
                self.assertEqual(0, seen["wrong"],
                                 "%d of the %d rows drawn do not match %r: %s"
                                 % (seen["wrong"], seen["drawn"],
                                    seen["value"], where))
                if counted:
                    self.assertEqual(expected[counted], seen["shown"],
                                     "the page keeps %s rows where the data "
                                     "has %s: %s"
                                     % (seen["shown"], expected[counted], where))
            finally:
                page.close()

    def test_one_row_is_marked_at_a_time_unless_ctrl_is_held(self) -> None:
        """A mark is where you are, not everywhere you have been.

        Every press added another and only a second press on that same row
        took one away, so a reader who had kept their place four times had
        four rows lit and no way to tell which was this one. A plain press
        collapses the set onto the row pressed; Ctrl — Cmd on a Mac — toggles
        one and leaves the rest.

        Pressing the only marked row still clears it, which is the one way
        back to no marks at all on a phone: there is no modifier to hold
        there, and the tap is asserted at the end for that reason.
        """
        marked = ("()=>[...document.querySelectorAll('#body .row.marked')]"
                  ".map(r => r.dataset.id)")
        page = self.open(1440, 900)
        try:
            def cell(n: int) -> str:
                return "#body .row[data-id] >> nth=%d >> td.trip" % n

            page.click(cell(0))
            first = page.evaluate(marked)
            self.assertEqual(1, len(first), "a press did not mark one row")

            page.click(cell(1))
            second = page.evaluate(marked)
            self.assertEqual(1, len(second),
                             "a second press left two rows marked")
            self.assertNotEqual(first, second, "the mark did not move")

            page.click(cell(2), modifiers=["Control"])
            page.click(cell(3), modifiers=["Meta"])
            three = page.evaluate(marked)
            self.assertEqual(3, len(three),
                             "Ctrl and Cmd did not add to the marked set")
            self.assertEqual(second[0], three[0],
                             "adding a mark dropped the one already there")

            page.click(cell(2), modifiers=["Control"])
            self.assertEqual(2, len(page.evaluate(marked)),
                             "Ctrl on a marked row did not take it off")

            page.click(cell(3))
            self.assertEqual(1, len(page.evaluate(marked)),
                             "a plain press did not collapse the set")
            page.click(cell(3))
            self.assertEqual([], page.evaluate(marked),
                             "pressing the only marked row did not clear it")

            # And the press that is not the row's: a panel trigger is a
            # button the reader used to read something.
            page.click(cell(0))
            held = page.evaluate(marked)
            page.click("#body .row[data-id] >> nth=5 >> .fees-open")
            page.wait_for_timeout(280)
            self.assertEqual(held, page.evaluate(marked),
                             "opening a bill moved the mark")
        finally:
            page.close()

        phone = self._browser.new_page(
            viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
        try:
            phone.goto(self._url)
            phone.wait_for_selector("article.card")
            cards = ("()=>[...document.querySelectorAll('#cards .row.marked')]"
                     ".map(r => r.dataset.id)")
            phone.locator(".cards .card .t-name").first.click()
            phone.wait_for_timeout(200)
            self.assertEqual(1, len(phone.evaluate(cards)),
                             "a tap does not mark a card")
            phone.locator(".cards .card .t-name").nth(1).click()
            phone.wait_for_timeout(200)
            self.assertEqual(1, len(phone.evaluate(cards)),
                             "a second tap left two cards marked")
        finally:
            phone.close()


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps({"sizes": SIZES, "floor": TABLE_FLOOR}))
    unittest.main()
