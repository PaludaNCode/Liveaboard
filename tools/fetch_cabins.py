#!/usr/bin/env python3
"""Fetch each departure's cabin ladder: what every berth costs and how many are left.

The dataset stores one price per sailing, the figure the vessel page
advertises. This reads what that figure is the bottom of -- every cabin's
price, its list price where it is discounted, how many berths the operator
claims remain, and what a solo diver is charged to have the cabin alone. See
``liveaboard.scrape.cabins`` for the markup and ``docs/sources/liveaboard.com``
for how the page was found.

Written as its own tool rather than folded into the crawl, and unlike
``fetch_itineraries.py`` it is **not incremental**:

**It needs no crawl to know what to ask for.** ``data/archive.json`` is
committed and every ``Event`` carries an ``@id`` of the form
``LA-{x}-{boatID}-{tourID}``. Both ids come straight out of the repository.

**Every departure is re-read within three days, and the ones that moved the
same day.** A berth count changes the moment somebody books, and a discount
ends -- but it was measured doing so far less than a nightly census assumed:
between the books of 2026-09-28 and 09-29, 697 of 1,030 ladders "changed" and
676 of those moved price only, almost all by a factor of 0.999 -- the booking
page re-converting into the session currency overnight. **21 sailings changed
berth count** (#154). So ``--triggered`` reads what the crawl says moved and a
rotating third of the rest (:func:`triggered`), and ``--limit 0`` without it is
still the full census.

**A capped run merges, it never replaces.** ``--limit N`` visits N departures
and leaves the rest of the book alone: a run knows nothing about the sailings
it did not visit. Same rule as ``scrape_fees.py --limit`` and for the same
reason -- an earlier version of the itinerary fetcher rewrote its whole book
from a six-trip run and dropped 309 records.

**What it reads is a claim with a date on it.** "only 2 spaces left!" is red
marketing text as much as inventory, and even the attribute behind it is the
operator's word. Every record carries the day it was read, and anything
rendering it has to say so.

    python3 tools/fetch_cabins.py [--limit N] [--delay 2] [--tours 6240:415714]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from liveaboard.scrape.base import PoliteFetcher  # noqa: E402
from liveaboard.scrape.cabins import parse_cabins  # noqa: E402
from liveaboard.scrape.liveaboard_com import HOST  # noqa: E402

EVENT_ID = re.compile(r"^LA-(\d+)-(\d+)-(\d+)$")


def endpoint(boat_id: str, tour_id: str) -> str:
    return f"https://{HOST}/BookingStep1?tourid={tour_id}&boatid={boat_id}"


def wanted(archive: dict[str, Any]) -> dict[str, dict[str, str]]:
    """``{tour_id: {...}}``, one entry per sailing.

    Keyed by tour id because that is what the booking page takes and what the
    archive states; a departure id is built later, in promote, from the boat
    and the date.
    """
    out: dict[str, dict[str, str]] = {}
    for page in archive.get("pages", []):
        slug = page["url"].split("?")[0].rstrip("/").rsplit("/", 1)[-1]
        for node in page.get("nodes", []):
            if node.get("@type") != "Event":
                continue
            match = EVENT_ID.match(node.get("@id") or "")
            if not match:
                continue
            _, boat_id, tour_id = match.groups()
            offer = node.get("offers") or {}
            if isinstance(offer, list):
                offer = offer[0] if offer else {}
            out.setdefault(tour_id, {
                "boat": slug,
                "boat_id": boat_id,
                "tour_id": tour_id,
                "name": " ".join((node.get("name") or "").split()),
                "start": (node.get("startDate") or "")[:10],
                # What the vessel page advertised, so the cheapest cabin can be
                # checked against it rather than assumed to match.
                "advertised": offer.get("price"),
                # The currency the page was asked for. Never re-derived from
                # the glyph beside the price: "$" is four currencies this site
                # sells in and the booking page renders the session's.
                "currency": offer.get("priceCurrency") or "USD",
                # Kept so the next run can tell a sailing the crawl says sold
                # out, or re-opened, from one that did nothing.
                "availability": offer.get("availability") or "",
            })
    return out


ROTATE_DAYS = 3
"""The oldest a ladder may get when nothing about its sailing moved.

A rotating third of the fleet is read every day, so each quiet sailing is read
every third day and its count is at most two days old -- dated per sailing on
the page, never under the day the run happened. Anything the crawl says moved
is read the same day regardless.
"""

STALE_LADDER = 0.03
"""`promote.STALE_LADDER`'s figure, asked here before the fact: a ladder that
would be dropped for contradicting its row is re-read rather than dropped."""


def triggered(
    sailings: dict[str, dict[str, Any]],
    book: dict[str, dict[str, Any]],
    today: date,
) -> dict[str, str]:
    """``{tour_id: why}`` for every sailing this run should read.

    - **never read** -- nothing in the book to be stale;
    - **moved** -- the crawl now states a different fare, currency or
      availability from the one this ladder was read against, which is exactly
      the sailing whose ladder went wrong overnight;
    - **stale** -- its bottom rung sits past ``STALE_LADDER`` from the fare,
      so `promote` would drop it: reading it again is the fix, and the only
      one;
    - **its turn** -- ``int(tour_id) % ROTATE_DAYS`` names the day, so a
      third of the rest is read daily and no quiet ladder is older than
      ``ROTATE_DAYS - 1`` days;
    - **overdue** -- read ``ROTATE_DAYS`` or more days ago, which is the net
      under a run that failed on a sailing's turn.
    """
    turn = today.toordinal() % ROTATE_DAYS
    why: dict[str, str] = {}
    for tour, entry in sailings.items():
        record = book.get(tour)
        if not record:
            why[tour] = "never read"
            continue
        for field in ("advertised", "currency", "availability"):
            then, now = record.get(field), entry.get(field)
            if then is not None and str(then) != str(now or ""):
                why[tour] = f"{field} moved"
                break
        if tour in why:
            continue
        cheapest = min((c["price"] for c in record.get("cabins") or []
                        if c.get("price") is not None), default=None)
        try:
            fare = float(entry.get("advertised") or 0)
        except ValueError:
            fare = 0
        if (cheapest is not None and fare
                and record.get("currency") == entry.get("currency")
                and abs(cheapest - fare) / fare > STALE_LADDER):
            why[tour] = "stale"
            continue
        try:
            age = (today - date.fromisoformat(record.get("collected") or "")).days
        except ValueError:
            age = ROTATE_DAYS
        if age >= ROTATE_DAYS:
            why[tour] = "overdue"
        elif tour.isdigit() and int(tour) % ROTATE_DAYS == turn and age > 0:
            why[tour] = "its turn"
    return why


#: The one setting the booking page takes its currency from (#154): a cookie,
#: set on the first response from the visitor's origin. A runner is not always
#: where the last one was -- on 2026-10-07 one was answered in pounds on 884
#: pages (#157) -- so the fetch states the currency rather than inherit it.
CURRENCY_COOKIE = "boardCookie_prefs_v2=currency%3D{}"


def session_currency(currency: str) -> dict[str, str]:
    """The header that makes a booking page answer in ``currency``."""
    return {"Cookie": CURRENCY_COOKIE.format(currency)}


def currency_refusal(read: int, elsewhere: int) -> str | None:
    """Why this run may not write its book, when the session lost its currency.

    One page in another currency is a page; most of a run is the site
    answering the session in a currency it was not asked for, which is a fault
    of the run and not of any sailing (#157 -- pounds for dollars on nearly
    every page of 2026-10-07). Each such page is already skipped, so nothing
    false would be written; the refusal is so the red job names the cause
    rather than a ladder floor in the publish gate two steps later.
    """
    if elsewhere and elsewhere > read:
        return (f"REFUSED: {elsewhere} booking page(s) answered in a currency "
                f"other than the one asked for, against {read} read. The "
                f"session's currency did not hold; nothing written.")
    return None


def load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("departures") or {}
    except (OSError, ValueError) as exc:
        print(f"could not read {path} ({exc}); starting fresh", file=sys.stderr)
        return {}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", default=Path("data/archive.json"), type=Path)
    parser.add_argument("--out", default=Path("data/cabins.json"), type=Path)
    parser.add_argument("--snapshots", default=Path("data/snapshots"), type=Path)
    parser.add_argument("--limit", type=int, default=0, help="cap fetches (0 = all)")
    parser.add_argument("--delay", type=float, default=2.0)
    parser.add_argument("--triggered", action="store_true",
                        help="read only what moved, what is stale and a rotating "
                             f"third of the rest, so no ladder is older than "
                             f"{ROTATE_DAYS - 1} days (#154)")
    parser.add_argument("--tours", default="",
                        help="explicit boatid:tourid pairs, for proving a change")
    args = parser.parse_args()

    if not args.archive.exists():
        print(f"no {args.archive}; run a scrape first", file=sys.stderr)
        return 1

    sailings = wanted(json.loads(args.archive.read_text(encoding="utf-8")))
    book = load(args.out)

    if args.tours:
        todo = []
        for pair in args.tours.split(","):
            boat, _, tour = pair.strip().partition(":")
            todo.append(tour)
            sailings.setdefault(tour, {
                "boat": "(named on the command line)", "boat_id": boat,
                "tour_id": tour, "name": "", "start": "",
                "advertised": None, "currency": "USD",
            })
    else:
        # Soonest first: a berth count matters most on the sailings people are
        # booking now, and a capped run should spend its requests there.
        todo = sorted(sailings, key=lambda t: (sailings[t]["start"], t))
        if args.triggered:
            why = triggered(sailings, book, date.today())
            todo = [t for t in todo if t in why]
            counts: dict[str, int] = {}
            for reason in why.values():
                counts[reason] = counts.get(reason, 0) + 1
            print("triggered: " + ", ".join(
                f"{n} {reason}" for reason, n in sorted(counts.items())))
        if args.limit:
            todo = todo[: args.limit]

    print(f"{len(sailings)} sailing(s) in the archive, {len(book)} already read, "
          f"{len(todo)} to fetch")
    if not todo:
        print("nothing to do")
        return 0

    fetcher = PoliteFetcher(snapshot_dir=args.snapshots, delay=args.delay)
    today = date.today().isoformat()
    read = failed = nothing = disagreed = elsewhere = 0

    for index, tour in enumerate(todo, 1):
        entry = sailings[tour]
        url = endpoint(entry["boat_id"], tour)
        try:
            result = fetcher.get(url, headers=session_currency(entry["currency"]))
        except Exception as exc:  # noqa: BLE001 - one bad page must not end the run
            print(f"  [{index}/{len(todo)}] {entry['boat']}: {exc}", flush=True)
            failed += 1
            continue

        reading = parse_cabins(result.body, entry["currency"])
        for warning in reading.warnings:
            print(f"      ! {warning}", flush=True)
            disagreed += 1

        if reading.shown:
            # Figures in a currency nobody asked for are not this sailing's
            # ladder, and labelling them with the one that was asked for is
            # how 2026-10-07 published pounds as dollars (#157). The record
            # from the last good reading stays, dated the day it was read.
            print(f"  [{index}/{len(todo)}] {entry['boat']:22.22} "
                  f"{entry['start']}  page shows {reading.shown}, not "
                  f"{entry['currency']}; left as it was", flush=True)
            elsewhere += 1
            continue

        if not reading.cabins:
            # A page with no cabin markup at all answers nothing, and writing
            # it as "no berths" would publish a sold-out sign for a page that
            # merely failed. A full boat is not this case: it lists every
            # cabin, prices them, and marks each FULL, so it reads normally
            # below with zero berths.
            print(f"  [{index}/{len(todo)}] {entry['boat']:22.22} "
                  f"{entry['start']}  no cabin markup; left as it was",
                  flush=True)
            nothing += 1
            continue

        cheapest = reading.cheapest
        advertised = entry.get("advertised")
        note = ""
        if advertised and cheapest and cheapest.price is not None:
            # The claim this whole tool rests on: the advertised price is the
            # cheapest cabin's. Checked on every sailing rather than assumed
            # from the two it was established on.
            if abs(float(advertised) - cheapest.price) > 0.5:
                note = f"  (advertised {advertised}, cheapest {cheapest.price:g})"

        book[tour] = {
            "boat": entry["boat"],
            "tour_id": tour,
            "start": entry["start"],
            "name": entry["name"],
            "collected": today,
            "currency": reading.currency,
            "advertised": advertised,
            "availability": entry.get("availability") or "",
            "cabins": [c.as_dict() for c in reading.cabins],
            "source_url": url,
        }
        if reading.nothing_bookable:
            book[tour]["nothing_bookable"] = True
        read += 1
        # "from -" rather than a crash: a cabin listed with no readable figure
        # is a parse worth seeing in the log, not one worth ending the run on.
        from_price = f"{cheapest.price:g}" if cheapest and cheapest.price else "-"
        berths = reading.berths_at_cheapest
        print(f"  [{index}/{len(todo)}] {entry['boat']:22.22} {entry['start']}  "
              f"{len(reading.cabins)} cabin(s), from {from_price} "
              f"{reading.currency}, "
              f"{berths if berths is not None else 'unknown'} berth(s) "
              f"at it{note}"
              f"{'  FULL' if reading.nothing_bookable else ''}", flush=True)

    refusal = currency_refusal(read, elsewhere)
    if refusal:
        print(f"\n{refusal}")
        return 1

    if not read:
        # A run that read nothing must not rewrite the file. The only thing
        # that would change is the collected date, which would report the book
        # as fresh on the strength of a few hundred failed requests.
        print(f"\nnothing read; {args.out} left as it was ({failed} failed)")
        return 1 if failed else 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "collected": today,
                "source": "liveaboard.com",
                "note": (
                    "One cabin ladder per departure, from /BookingStep1. Every "
                    "price, the list price where the cabin is discounted, the "
                    "berths the operator claims remain (data-allocation, not "
                    "the red banner, which only appears at four or fewer) and "
                    "the stated single-occupancy surcharge. Berth counts are "
                    "the operator's claim on the day in `collected`, not "
                    "verified inventory, and they go stale within hours. Each "
                    "record's own `collected` is the day it was read: a "
                    "sailing is re-read the day the crawl says it moved, and "
                    "every third day otherwise."
                ),
                "departures": dict(sorted(book.items())),
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\nwrote {args.out}: {len(book)} departure(s) ({read} read this run), "
          f"{failed} failed, {nothing} unreadable, {elsewhere} in another "
          f"currency, {disagreed} warning(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
