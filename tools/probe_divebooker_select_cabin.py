#!/usr/bin/env python3
"""Watch what a divebooker departure row does when *Select cabin* is pressed.

#152. The cabin ladder is `/boatorder/booking?tripId=N`, and the id is the
fragment on a sailing's JSON-LD Event `@id`. The Events are a **capped ten per
hull**, the `TouristTrip` chain carrying every other sailing states no id, and
the streamed payload holds no trip id at all — asked with the ids we already
had as the probe, which is the one way that cannot be answered by guessing
(run 35734945024, `tools/probe_divebooker_trip_ids.py`). So the ladder reaches
**21 of 906** sailings and the link for the other 885 is not in the served
bytes.

One place is left: the page itself, once its own JavaScript has run. This is
the probe that looks there, and it is a different kind from every other one in
this directory — a browser with the network log open rather than a reader over
what `urllib` was handed.

It asks three things, in this order, because each can close the question
without the next:

1. **Is the id already in the rendered DOM?** If the row's *Select cabin* is an
   anchor whose `href` carries `tripId`, there is no request to watch: the
   answer is a second read of the page with a browser, and the cost is one page
   load per hull rather than one per sailing.
2. **If not, what does pressing it ask for?** Every request the page makes from
   that press is printed — method, URL, and the body if it posts one — with the
   ids we already hold highlighted where they appear.
3. **Does the answer cover the sailings the Events do not?** The rows are
   counted, the ids found are counted, and the two are printed against the
   hull's own season. A field that answers only the capped ten is the same
   ceiling wearing a different name.

**Nothing here may derive an id.** An id is read from the page or done without:
a sequence, a neighbouring sailing and a date are all forbidden, and a row this
finds no id for is reported as a row with no id.

Writes nothing. One page load, then at most `--clicks` presses paced by
`--delay`, which is the five seconds this project gives a host that states no
`Crawl-delay`. robots.txt allows `/boatorder/booking`.

    python3 tools/probe_divebooker_select_cabin.py [--vessel blue-haz441]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pw_browser import resolve as resolve_browser  # noqa: E402

from liveaboard.scrape import divebooker_com as db  # noqa: E402

TRIP_ID = re.compile(r"tripId=(\d+)", re.I)
"""The one parameter the booking page takes."""

#: What a *Select cabin* control might be called. Cast wide on purpose: the
#: point of a probe is to find out what the markup is called, not to assume it
#: — `probe_gear.py`'s rule, and it is how the fee panel was found under
#: `details` after a keyword sweep had ruled it out.
CANDIDATES = (
    "a[href*='boatorder']",
    "a[href*='tripId']",
    "[href*='booking']",
    "button",
    "[role='button']",
    "[class*='select']",
    "[class*='cabin']",
    "[class*='book']",
)

PRESSABLE = re.compile(r"select\s*cabin|choose\s*cabin|book\s*now|select\s*room",
                       re.I)


def described(request: Any) -> str:
    """One request, as a line -- with its body where the body is text.

    `request.post_data` decodes the raw bytes as UTF-8 and **raises** where
    they are not: this page posts compressed bodies, and the first run of this
    probe died inside Playwright's own event dispatch on
    `'utf-8' codec can't decode byte 0x8b`. A probe that cannot print a request
    it does not understand is a probe that stops at the first one, so the body
    is asked for defensively and its absence is reported rather than raised.
    """
    line = f"{request.method} {request.url}"
    try:
        body = request.post_data
    except Exception:  # noqa: BLE001 - a body that is not text is still data
        raw = None
        try:
            raw = request.post_data_buffer
        except Exception:  # noqa: BLE001
            pass
        return line + f"  <<{len(raw or b'')} byte(s), not text>>"
    return line + (f"  <<{body[:400]}>>" if body else "")


def hull_url(book: dict[str, Any], slug: str) -> str | None:
    record = (book.get("vessels") or {}).get(slug)
    if isinstance(record, dict) and record.get("url"):
        return str(record["url"])
    return None


def known_ids(book: dict[str, Any], slug: str) -> dict[str, str]:
    """The booking ids this project already holds for one hull, by date.

    These are the probe's own control: a field claiming to be the id has to
    reproduce them, and one that does not is a different number.
    """
    out = {}
    for row in (book.get("departures") or {}).values():
        if row.get("boat") == slug and row.get("booking_id"):
            out[row["start"]] = str(row["booking_id"])
    return out


def anchors(page: Any, cap: int) -> list[tuple[str, str]]:
    """Every rendered control that could open a booking page, with its text.

    Capped per selector, because the wide ones are wide on purpose: `button`
    and `[class*='book']` match most of a page on a site whose name is one of
    the words, and `inner_text()` on each is a layout per node. A ceiling on a
    scan nobody has watched, not a measurement -- and an argument, so a run
    that wants the whole page can ask for it.
    """
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for selector in CANDIDATES:
        for node in page.query_selector_all(selector)[:cap]:
            try:
                text = " ".join((node.inner_text() or "").split())[:60]
                href = node.get_attribute("href") or ""
            except Exception:  # noqa: BLE001 - a detached node is not a failure
                continue
            mark = f"{href}::{text}"
            if mark in seen:
                continue
            seen.add(mark)
            if href or PRESSABLE.search(text):
                found.append((href, text))
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book", default=Path("data/divebooker.json"), type=Path)
    parser.add_argument("--vessel", default="",
                        help="hull slug as the book names it; the default is "
                             "the hull with the most sailings")
    parser.add_argument("--clicks", type=int, default=3,
                        help="presses to make, after the DOM has been read")
    parser.add_argument("--delay", type=float, default=5.0)
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument("--max-nodes", type=int, default=600,
                        help="nodes to read per selector; the wide selectors "
                             "match most of a page and each read is a layout")
    parser.add_argument("--executable", default=None)
    parser.add_argument("--dump-html", action="store_true",
                        help="print the markup of one departure row")
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    book = json.loads(args.book.read_text(encoding="utf-8"))
    slug = args.vessel
    if not slug:
        # The busiest hull **that already holds an id**, not the busiest hull.
        # The ids we hold are this probe's control -- a field claiming to be
        # the id has to reproduce one -- and the first run picked Red Sea
        # Aggressor II, 18 sailings and not one id, which is the one shape
        # that can answer nothing either way.
        counts: dict[str, int] = {}
        for row in (book.get("departures") or {}).values():
            counts[row.get("boat", "")] = counts.get(row.get("boat", ""), 0) + 1
        with_ids = {boat for boat, count in counts.items()
                    if known_ids(book, boat)}
        pool = {boat: count for boat, count in counts.items()
                if boat in with_ids} or counts
        slug = max(pool, key=lambda k: pool[k]) if pool else ""
    url = hull_url(book, slug)
    if not url:
        print(f"?? {slug}: no vessel url in the committed book")
        return 2

    held = known_ids(book, slug)
    sailings = sum(1 for row in (book.get("departures") or {}).values()
                   if row.get("boat") == slug)
    print(f"== {slug}: {sailings} sailing(s) in the committed book, "
          f"{len(held)} of them with a booking id")
    print(f"   {url}")

    asked: list[str] = []
    with sync_playwright() as playwright:
        executable, why = resolve_browser(playwright, args.executable)
        print(f"   browser: {why}")
        browser = playwright.chromium.launch(
            headless=True, executable_path=executable)
        page = browser.new_page()

        # Every request the page makes, from the first byte. A press that
        # navigates and a press that fetches are different findings and only
        # the log tells them apart.
        page.on("request", lambda request: asked.append(described(request)))

        # `networkidle` is what a probe wants -- the question is what the page
        # asks for once its own JavaScript has run -- and it is also what a
        # page with a poller never reaches. Falling back to `load` keeps the
        # probe answering something rather than failing on the wait.
        try:
            page.goto(url, timeout=args.timeout * 1000, wait_until="networkidle")
        except Exception as exc:  # noqa: BLE001
            print(f"   the page never went idle ({exc}); reading it at load")
            page.goto(url, timeout=args.timeout * 1000, wait_until="load")
            page.wait_for_timeout(4000)

        # 1. The rendered DOM, which is the cheapest answer there could be.
        rendered = anchors(page, args.max_nodes)
        with_id = [(href, text) for href, text in rendered if TRIP_ID.search(href)]
        print(f"\n-- rendered controls: {len(rendered)}, "
              f"{len(with_id)} carrying a tripId")
        for href, text in with_id[:20]:
            found = TRIP_ID.search(href).group(1)
            where = [day for day, one in held.items() if one == found]
            print(f"   {found:>8}  {text[:40]!r}  "
                  f"{'= ' + where[0] if where else 'not one we hold'}")
        ids = {TRIP_ID.search(href).group(1) for href, _ in with_id}
        if ids:
            print(f"   {len(ids)} distinct id(s) rendered against {sailings} "
                  f"sailing(s) and {len(held)} held")

        if args.dump_html:
            row = page.query_selector("[class*='departure'], [class*='trip-row']")
            if row:
                print("\n-- one row's markup\n" + (row.inner_html() or "")[:3000])

        # 2. What a press asks for, where the DOM carries no id.
        pressed = 0
        while pressed < args.clicks:
            # Re-queried each round and taken by **position**, not by text: a
            # press can navigate, which detaches every other handle, and every
            # row's control says the same words, so a set of seen labels would
            # press one row and call it the page.
            matched = []
            for candidate in page.query_selector_all(
                    ",".join(CANDIDATES))[:args.max_nodes]:
                try:
                    words = " ".join((candidate.inner_text() or "").split())
                except Exception:  # noqa: BLE001
                    continue
                if PRESSABLE.search(words):
                    matched.append((candidate, words))
            if not matched:
                if not pressed:
                    print("\n-- nothing on the page reads as *Select cabin*")
                break
            if pressed >= len(matched):
                print(f"\n-- {len(matched)} pressable row(s), all pressed")
                break
            node, text = matched[pressed]
            if not pressed:
                print(f"\n-- {len(matched)} row(s) read as pressable")
            before = len(asked)
            print(f"\n-- pressing {text[:50]!r}")
            try:
                node.click(timeout=args.timeout * 1000)
                page.wait_for_timeout(2500)
            except Exception as exc:  # noqa: BLE001 - a refused click is data
                print(f"   the press did not complete: {exc}")
            for line in asked[before:]:
                if db.HOST in line:
                    print(f"   {line[:300]}")
            print(f"   landed on {page.url}")
            pressed += 1
            time.sleep(args.delay)
            if page.url != url:
                # The press navigated, which is itself the answer for that row
                # -- and it leaves every other control on a page that is no
                # longer there. Back to the hull before the next one, so each
                # press is asked of the same page.
                try:
                    page.goto(url, timeout=args.timeout * 1000,
                              wait_until="load")
                    page.wait_for_timeout(3000)
                except Exception as exc:  # noqa: BLE001
                    print(f"   could not return to the hull page: {exc}")
                    break
                time.sleep(args.delay)

        browser.close()

    # 3. The finding, last, because a probe's answer is read from the end of a
    # job log. Three shapes it can take and each is a different next step.
    print("\n== finding")
    if ids and len(ids) >= sailings:
        print(f"every one of {slug}'s {sailings} sailing(s) states a tripId in "
              f"the rendered DOM: the ladder needs a browser per hull, not a "
              f"request per sailing")
    elif ids:
        print(f"the rendered DOM carries {len(ids)} id(s) for {sailings} "
              f"sailing(s) — more than the {len(held)} the Events state"
              if len(ids) > len(held) else
              f"the rendered DOM carries {len(ids)} id(s), the same ceiling as "
              f"the {len(held)} the Events state")
    else:
        print(f"no tripId is in the rendered DOM for {slug}; what the presses "
              f"above asked for is the whole of what there is to go on")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
