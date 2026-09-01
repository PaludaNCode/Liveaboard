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

        The count is against each control's *default* rather than its
        emptiness, which is how the Include switches get in -- both start on,
        and turning one off changes every total without touching a row.
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

        # A switch is not a filter and is counted anyway: it changes what every
        # total on the page means rather than which rows are on it.
        page.click("#toggles .chip")
        page.wait_for_timeout(320)
        self.assertEqual(count(), "3")

        # And all of it survives the drawer closing, which is the whole point.
        page.click("#filtersToggle")
        page.wait_for_timeout(280)
        self.assertEqual(count(), "3")
        self.assertEqual(len(pills()), 3)

        # A pill drops its own filter, so undoing one does not mean opening the
        # drawer to hunt for the chip that set it.
        page.click("#activePills .pill-drop")
        page.wait_for_timeout(320)
        self.assertEqual(count(), "2")
        self.assertEqual(len(pills()), 2)

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


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps({"sizes": SIZES, "floor": TABLE_FLOOR}))
    unittest.main()
