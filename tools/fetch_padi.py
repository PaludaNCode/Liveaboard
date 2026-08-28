#!/usr/bin/env python3
"""Import what PADI states per trip: the entry bar, and a stated dive count.

Reads `/api/v2/travel/shop/...` for every boat in `data/padi_aliases.json` and
writes two files:

`data/padi_raw.json` -- gitignored
    Every field each response published, less two. Same principle as
    `data/archive.json`: re-parsing must never need a re-crawl, and a field we
    start caring about next month would otherwise arrive attached to next
    month's data. A parser fix that cannot be tested offline does not get
    tested, and the re-crawl it saves is 290 requests against somebody else's
    server.

    Kept out of history all the same. It is 13 MB against archive.json's 1.8 MB
    and rewritten whole on every refresh, and the two files are not comparable
    in what they preserve: the archive holds prices, which are gone tomorrow,
    while this holds an entry bar and a dive count, which are not. Delete it and
    a re-run rebuilds it; delete `archive.json` and yesterday is unrecoverable.

    Rebuild with a plain `python3 tools/fetch_padi.py` -- incremental, so it
    fetches only what is missing.

    `photos` and `marineLife` are dropped on the way in. They are 17% of the
    payload and consist of CDN thumbnail URLs, and this site loads nothing
    external by invariant -- there is no version of it that can render them.
    Every textual field is kept, the fee structures included.

`data/padi.json`
    The book `promote` can merge, keyed on `promote.itinerary_key(boat_id,
    name)` -- the same key `data/itineraries.json` uses, so the two books match
    or fail together rather than each inventing a rule.

**Incremental and crash-safe.** An itinerary already in the raw store is not
re-fetched, and both files are written after every boat, so a run killed
halfway keeps what it got. `--limit N` merges rather than replaces, like
`scrape_fees.py --limit`.

    python3 tools/fetch_padi.py [--limit N] [--refresh]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import http.client
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from liveaboard.promote import itinerary_key  # noqa: E402
from liveaboard.scrape.padi_com import (  # noqa: E402
    HOST,
    ITINERARY_DETAIL,
    ITINERARY_LIST,
    PadiComAdapter,
)

UA = {"User-Agent": "Mozilla/5.0 (compatible; liveaboard-import/1.0; +price-transparency research)"}

DROP = ("photos", "marineLife")
"""Media arrays. See the module docstring: nothing here can ever render them."""
RAW = Path("data/padi_raw.json")
BOOK = Path("data/padi.json")
DEPARTURES = Path("data/padi_departures.json")

TRIP_LIST = "https://travel.padi.com/api/v2/travel/shop/{vessel}/trips/"
"""Every sailing one vessel has on sale: dates, price, availability.

A different endpoint from the itineraries one and answering a different
question. `ITINERARY_DETAIL` describes a trip *template* -- harbours, airports,
the entry bar -- and carries no date and no price at all. This carries both, and
is what makes a sailing comparable rather than a trip merely describable.
"""


def get(url: str, timeout: int = 40) -> dict | None:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
            return json.loads(r.read())
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError,
            http.client.HTTPException, ConnectionError, json.JSONDecodeError):
        # http.client.RemoteDisconnected is not a URLError and killed a run at
        # boat 34 of 38, after 13 MB of responses were already on disk. One
        # dropped connection must cost the itinerary, not the run.
        return None


def shop_facts(slug: str, fallback: str) -> tuple[str, str | None]:
    """The country PADI files this shop under, and the currency it prices in.

    Both come off the vessel page's ``window.shop`` and both are needed:

    ``countrySlug`` is not the cruising ground. All three Red Sea Aggressors are
    filed under `united-states-of-america-usa` while sailing Hurghada, Port
    Ghalib and Hamata; sending "egypt" for them 404s every itinerary.

    ``currency`` is the only place a price's unit is stated. The trips endpoint
    returns a bare ``price`` with no currency beside it, and the ``Currency-code``
    header the app sends does not convert -- EUR, USD and GBP all answered 1473.0
    for the same sailing. So the number is in the vessel's own currency, and a
    boat whose page does not state one has its prices dropped rather than
    assumed: this project does not invent a price, and a price in an unknown
    unit is an invented one.
    """
    import re

    url = f"https://{HOST}/liveaboard/{fallback}/{slug}/"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=35) as r:
            html = r.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 - fall back rather than lose the boat
        return fallback, None
    if "window.shop = " not in html:
        return fallback, None
    block = html[html.index("window.shop = "):][:1200]

    def field(name: str) -> str | None:
        match = re.search(rf'\b{name}:\s*["\']?([^,"\'\n]*)', block)
        value = match.group(1).strip() if match else ""
        return value or None

    return field("countrySlug") or fallback, field("currency")


def shop_country(slug: str, fallback: str) -> str:
    """The country PADI files this shop under, which the detail URL needs.

    Not the cruising ground. All three Red Sea Aggressors are filed under
    `united-states-of-america-usa` -- Aggressor Fleet is American -- while
    sailing Hurghada, Port Ghalib and Hamata. Sending "egypt" for them 404s
    every itinerary, which is exactly the 13 failures this import kept
    reporting.

    Read per boat rather than stored in the alias map: it is PADI's fact about
    its own record, and a copy of it here is a copy that can go stale.
    """
    import re

    url = f"https://{HOST}/liveaboard/{fallback}/{slug}/"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=35) as r:
            html = r.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 - fall back rather than lose the boat
        return fallback
    if "window.shop = " not in html:
        return fallback
    block = html[html.index("window.shop = "):][:1200]
    match = re.search(r'\bcountrySlug:\s*["\']?([^,"\'\n]*)', block)
    return (match.group(1).strip() if match else "") or fallback


def load(path: Path, default: dict) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return dict(default)


def _iso_day(value: str | None) -> str | None:
    """PADI dates are midnight-Z timestamps; a sailing is a day.

    "2027-05-01T00:00:00Z" -> "2027-05-01". Compared against our own departure
    dates, which are plain days, so the timestamp has to go before the two can
    be keyed together -- and it has to go by truncation rather than by parsing
    into a local timezone, which would move a midnight-UTC sailing to the
    previous day for anyone west of Greenwich.
    """
    if not value or len(value) < 10:
        return None
    day = value[:10]
    return day if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) else None


def _fetch_trips(aliases: dict[str, str], raw: dict, args) -> None:
    """Every sailing each mapped vessel sells, with the currency to read it in.

    Stored whole, like the itineraries: `promote` reads four fields today and
    the response carries eighteen, and trimming an archive to what the parser
    happens to want is how a question nobody has asked yet becomes unanswerable.
    """
    trips: dict = raw.setdefault("trips", {})
    shops: dict = raw.setdefault("shops", {})

    boats = sorted(aliases.items())
    if args.limit:
        boats = boats[: args.limit]
    for boat_id, slug in boats:
        country, currency = shop_facts(slug, args.country)
        time.sleep(args.delay)
        shops[slug] = {"country": country, "currency": currency, "boat": boat_id}

        listing = get(TRIP_LIST.format(vessel=slug))
        time.sleep(args.delay)
        if not listing:
            print(f"{boat_id:<24} {slug:<28} trips unavailable")
            continue

        results = listing.get("results") or []
        trips[slug] = results
        dated = sum(1 for r in results if _iso_day(r.get("startDate")))
        note = "" if currency else "   NO CURRENCY -- prices unusable"
        print(f"{boat_id:<24} {slug:<28} {len(results):>3} sailings, "
              f"{dated} dated, {currency or '?'}{note}")

        raw["fetched"] = time.strftime("%Y-%m-%d")
        RAW.write_text(json.dumps(raw, indent=1, sort_keys=True) + "\n")


def _departure_book(aliases: dict[str, str], raw: dict) -> dict:
    """Sailings keyed the way `promote` will look them up: boat and day.

    An exact key, deliberately. The itinerary join needed folding because two
    sites spell a trip differently; a date has no spelling, and 602 of our 627
    departures on these boats match one of PADI's on the day alone.

    A sailing with no price, or a price in a currency the vessel page never
    stated, is left out entirely rather than stored as zero.
    """
    shops = raw.get("shops") or {}
    book: dict[str, dict] = {}
    for slug, results in (raw.get("trips") or {}).items():
        shop = shops.get(slug) or {}
        boat_id, currency = shop.get("boat"), shop.get("currency")
        if not boat_id or not currency:
            continue
        for trip in results:
            day = _iso_day(trip.get("startDate"))
            price = trip.get("price")
            if not day or not isinstance(price, (int, float)) or price <= 0:
                continue
            book[f"{boat_id}::{day}"] = {
                "boat": boat_id,
                "start": day,
                "end": _iso_day(trip.get("endDate")),
                "nights": trip.get("duration"),
                "price": float(price),
                "currency": currency,
                "was": trip.get("compareAtPrice"),
                "availability": trip.get("availability"),
                "padi_id": trip.get("id"),
                "itinerary": (trip.get("itinerary") or {}).get("title"),
            }
    return book


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--aliases", default="data/padi_aliases.json")
    parser.add_argument("--country", default="egypt")
    parser.add_argument("--limit", type=int, default=0, help="boats this run, 0 for all")
    parser.add_argument("--delay", type=float, default=1.2,
                        help="seconds between requests; the edge rate-limits AI agents at 30/min")
    parser.add_argument("--refresh", action="store_true",
                        help="re-fetch itineraries already stored")
    parser.add_argument("--trips", action="store_true",
                        help="fetch sailings and prices as well as itineraries")
    args = parser.parse_args()

    aliases = json.loads(Path(args.aliases).read_text())["aliases"]
    raw = load(RAW, {"fetched": "", "country": args.country, "itineraries": {}})
    book = load(BOOK, {"collected": "", "source": "padi.com", "trips": {}})
    stored: dict = raw["itineraries"]

    boats = sorted(aliases.items())
    if args.limit:
        boats = boats[: args.limit]
    print(f"{len(boats)} boats, {len(stored)} itineraries already stored\n")

    fetched = skipped = failed = 0
    for boat_id, slug in boats:
        listing = get(ITINERARY_LIST.format(vessel=slug))
        time.sleep(args.delay)
        if not listing or not listing.get("count"):
            print(f"{boat_id:<24} {slug:<28} no itineraries")
            continue

        country = args.country
        wanted = [r for r in listing["results"]
                  if f"{slug}::{r['slug']}" not in stored or args.refresh]
        if wanted:
            country = shop_country(slug, args.country)
            time.sleep(args.delay)
        note = "" if country == args.country else f"  (filed under {country})"
        print(f"{boat_id:<24} {slug:<28} {listing['count']} itineraries{note}")
        for row in listing["results"]:
            store_key = f"{slug}::{row['slug']}"
            if store_key in stored and not args.refresh:
                skipped += 1
                continue
            detail = get(ITINERARY_DETAIL.format(
                country=country, vessel=slug, slug=row["slug"]))
            time.sleep(args.delay)
            if not detail:
                failed += 1
                print(f"    FAILED {row['slug']}")
                continue
            stored[store_key] = {k: v for k, v in detail.items() if k not in DROP}
            fetched += 1

        # Written per boat, so a killed run keeps what it got.
        raw["fetched"] = time.strftime("%Y-%m-%d")
        RAW.write_text(json.dumps(raw, indent=1, sort_keys=True) + "\n")

    if args.trips:
        _fetch_trips(aliases, raw, args)

    # The book is rebuilt from the raw store every time, so it is always exactly
    # what the current parser makes of the archive -- the same relationship
    # `promote --check` enforces between the dataset and its inputs.
    trips: dict[str, dict] = {}
    for store_key, detail in stored.items():
        slug = store_key.split("::", 1)[0]
        boat_id = next((b for b, s in aliases.items() if s == slug), None)
        if not boat_id:
            continue
        record = PadiComAdapter.itinerary_from_payload(detail)
        name = record.get("name")
        if not name:
            continue
        record["boat"] = boat_id
        trips[itinerary_key(boat_id, str(name))] = record
    book["trips"] = trips
    book["collected"] = raw["fetched"]
    BOOK.write_text(json.dumps(book, indent=1, sort_keys=True) + "\n")

    sailings = _departure_book(aliases, raw)
    if sailings:
        DEPARTURES.write_text(json.dumps(
            {"collected": raw.get("fetched", ""), "source": "padi.com",
             "departures": sailings}, indent=1, sort_keys=True) + "\n")
        currencies = sorted({s["currency"] for s in sailings.values()})
        print(f"{DEPARTURES}: {len(sailings)} sailings priced in {', '.join(currencies)}")

    with_bar = sum(1 for t in trips.values() if t.get("requirements"))
    with_dives = sum(1 for t in trips.values() if t.get("dives"))
    print(f"\nfetched {fetched}, skipped {skipped}, failed {failed}")
    print(f"{RAW}: {len(stored)} itineraries")
    print(f"{BOOK}: {len(trips)} keyed trips, {with_bar} with an entry bar, "
          f"{with_dives} with a stated dive count")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
