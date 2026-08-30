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
    fetches only what is missing. `--rebuild` goes further and fetches nothing
    at all: both books are pure functions of this store, so a parser change is
    proved against it offline, the way `reparse_candidate.py` fills a
    newly-read field onto the committed candidate rather than re-crawling.

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

**`padi.yml` runs it daily, with `--trips`.** It ran in no workflow at all
until then, and by that point five published facts depended on it -- two of
them prices. Everything else in the pipeline has a cadence and reports its own
failures; this one's failure mode was "nobody ran it", which nothing reported.
The runner caches the raw store rather than committing it, so an ordinary run
re-fetches only the itinerary *listings* and the sailings: ~80 requests against
~530 from cold.

**The book is never quietly emptied.** It is rebuilt whole from a raw store
that is gitignored, so a cold runner would rebuild it with zero trips and write
it -- a green job, a valid file, and five published facts gone. See
`MIN_BOOK_RATIO`.

    python3 tools/fetch_padi.py [--limit N] [--refresh] [--trips] [--force]
    python3 tools/fetch_padi.py --rebuild          # re-parse, no requests
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
    SEASON,
    PadiComAdapter,
)

UA = {"User-Agent": "Mozilla/5.0 (compatible; liveaboard-import/1.0; +price-transparency research)"}

DROP = ("photos", "marineLife")
"""Media arrays. See the module docstring: nothing here can ever render them."""
RAW = Path("data/padi_raw.json")
BOOK = Path("data/padi.json")
DEPARTURES = Path("data/padi_departures.json")

MIN_BOOK_RATIO = 0.9
"""How far `data/padi.json` may shrink in one run before it is refused.

**The book is rebuilt whole from the raw store, and the raw store is
gitignored.** That is fine on a machine that has one and a landmine on a fresh
runner, which is every scheduled run: an empty store rebuilds the book with
*zero* trips and writes it, deleting the entry bar, the dive count and the only
fee book the 22 PADI-only vessels have. Nothing about that looks like a
failure -- the job is green, the file is valid JSON, and the page quietly loses
five published facts.

So the same rule the other fetchers already keep. `fetch_cabins.py`: a run that
read nothing must not rewrite the file. `fetch_deals.py`: an empty read is not
a read that found no deals. Here: a book that has lost a tenth of its trips is
reporting a crawl that did not finish, not a fleet that shrank.

Ten percent because the real thing this catches is a book collapsing to a
fraction of itself, and PADI's itinerary count moves by ones. `--force` is the
way past it, the way `promote --force` is the way past its own ratio guard.
"""

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


def shop_facts(slug: str, fallback: str) -> dict[str, str | None]:
    """What the vessel page states about the vessel: country, currency, names.

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

    ``title`` and ``fleetTitle`` matter only for the boats PADI sells and
    liveaboard.com does not. Every other vessel takes its name and its operator
    from the source the prices come from, and should: those are the strings the
    rest of the dataset is keyed and sorted on. But a PADI-only vessel has no
    such entry, and naming it from its slug would print "Seawolf Steel" as
    *Seawolf Steel* by luck and "Bella 2" as *Bella 2* by luck too -- until the
    first slug that does not survive the round trip. `fleetTitle` is the
    operator: "SEAWOLF DIVING SAFARI Fleet" for Seawolf Steel, and the trailing
    word is PADI's own furniture rather than part of the company's name.
    """
    import re

    url = f"https://{HOST}/liveaboard/{fallback}/{slug}/"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=35) as r:
            html = r.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 - fall back rather than lose the boat
        return {"country": fallback, "currency": None, "name": None, "operator": None}
    if "window.shop = " not in html:
        return {"country": fallback, "currency": None, "name": None, "operator": None}
    block = html[html.index("window.shop = "):][:1600]

    def field(name: str) -> str | None:
        match = re.search(rf'\b{name}:\s*["\']?([^,"\'\n]*)', block)
        value = match.group(1).strip() if match else ""
        return value or None

    fleet = field("fleetTitle")
    return {
        "country": field("countrySlug") or fallback,
        "currency": field("currency"),
        "name": field("title"),
        "operator": re.sub(r"\s+Fleet$", "", fleet) if fleet else None,
    }


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


def keeps_the_book(rebuilt: int, held: int, *, force: bool = False) -> bool:
    """Whether a rebuilt book may replace the one on disk.

    Pure, and separate from the run, so the rule can be asserted rather than
    reasoned about. A first run has nothing to lose and always passes; a run
    that came back with nothing at all never does, whatever the ratio says,
    because zero trips is not a fleet that shrank.
    """
    if force or not held:
        return True
    return bool(rebuilt) and rebuilt >= held * MIN_BOOK_RATIO


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
        facts = shop_facts(slug, args.country)
        currency = facts["currency"]
        time.sleep(args.delay)
        shops[slug] = {**facts, "boat": boat_id}

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
                # PADI's own slug for the vessel, which is the only thing that
                # builds a link back to the page this price came from. `promote`
                # has been reading a `slug` key here since the sailings landed
                # and nothing was writing one, so every PADI provenance URL in
                # the dataset read ".../liveaboard/egypt//" -- a link to
                # nothing, on the one field whose job is to let a reader check
                # the claim.
                "slug": slug,
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
    parser.add_argument("--force", action="store_true",
                        help="write data/padi.json even if it loses trips (see MIN_BOOK_RATIO)")
    parser.add_argument("--rebuild", action="store_true",
                        help="rebuild both books from the raw store, fetching nothing")
    # The fee book has to be read against the season, because PADI keeps a
    # charge's old price beside its new one and only the dates tell them apart.
    # Spelled here like `fetch_deals.py` so a season that moves moves in both.
    parser.add_argument("--season-start", default=SEASON[0])
    parser.add_argument("--season-end", default=SEASON[1])
    args = parser.parse_args()

    aliases = json.loads(Path(args.aliases).read_text())["aliases"]
    raw = load(RAW, {"fetched": "", "country": args.country, "itineraries": {}})
    book = load(BOOK, {"collected": "", "source": "padi.com", "trips": {}})
    stored: dict = raw["itineraries"]
    # Counted before anything overwrites it: the book below is rebuilt whole,
    # and the guard needs to know what it is replacing.
    held = len(book.get("trips") or {})

    boats = sorted(aliases.items())
    if args.limit:
        boats = boats[: args.limit]
    if args.rebuild:
        # Both books are pure functions of the raw store, so a parser change
        # needs no request at all -- the same discipline as
        # `reparse_candidate.py`, which fills a newly-read field onto the
        # committed candidate rather than re-crawling 320 pages to read data
        # already in the repository. Here it is 530 pages, and the fields the
        # open issues want (`whatsIncludedNew`, the unclassified charge
        # labels, PADI's own reef descriptions) are all in the store already.
        boats = []
        print(f"--rebuild: no requests; {len(stored)} itineraries and "
              f"{len(raw.get('trips') or {})} vessels' sailings from {RAW}, "
              f"collected {raw.get('fetched') or 'undated'}\n")
    else:
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

    if args.trips and not args.rebuild:
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
        # The vessel's own currency, not the payload's: nothing in an itinerary
        # or a sailing states one, and the app's Currency-code header does not
        # convert -- EUR, USD and GBP all answer the same number. A vessel whose
        # page stated none gets no fees rather than fees assumed to be euro,
        # which is the same rule `_departure_book` applies to its prices.
        currency = (raw.get("shops", {}).get(slug) or {}).get("currency")
        if currency:
            record["fees"] = PadiComAdapter.fees_from_payload(
                detail, currency, (args.season_start, args.season_end))
        trips[itinerary_key(boat_id, str(name))] = record
    book["trips"] = trips
    # What PADI says about each vessel, as opposed to about one of its trips.
    #
    # Read for every boat and used for almost none: a vessel liveaboard.com
    # also sells takes its name and its operator from there, because those are
    # the strings the rest of the dataset is keyed and sorted on and one source
    # of them is the point. It is the ten boats PADI sells alone that have no
    # other name to take -- and a boat published under a title-cased slug is a
    # vessel this code named rather than one anybody did.
    book["vessels"] = {
        shop["boat"]: {
            "slug": slug,
            "name": shop.get("name"),
            "operator": shop.get("operator"),
            "country": shop.get("country"),
            "currency": shop.get("currency"),
        }
        for slug, shop in sorted((raw.get("shops") or {}).items())
        if shop.get("boat")
    }
    book["collected"] = raw["fetched"]
    if not keeps_the_book(len(trips), held, force=args.force):
        # Reported and refused, never quietly written. Every one of these
        # trips is a fee book, an entry bar or a dive count the page is
        # publishing, and a run whose raw store came up short knows nothing
        # about the ones it is missing -- exactly as an unreadable vessel page
        # knows nothing about the month behind it.
        print(f"::error::{BOOK} holds {held} trips and this run rebuilt only "
              f"{len(trips)}; the raw store is incomplete, so the book is left "
              f"as it was. Re-run without --limit to fill {RAW}, or pass "
              f"--force if the fleet really did shrink", file=sys.stderr)
        return 1
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
    # Charged lines only. An included line is a charge the fare *covers*, and
    # counting those here would report a trip that states no mandatory charge
    # at all as one that states several.
    with_fees = sum(1 for t in trips.values()
                    if any(not line.get("included")
                           for line in (t.get("fees") or {}).get("lines") or []))
    with_included = sum(1 for t in trips.values()
                        if any(line.get("included")
                               for line in (t.get("fees") or {}).get("lines") or []))
    complete = sum(1 for t in trips.values() if (t.get("fees") or {}).get("complete"))
    print(f"\nfetched {fetched}, skipped {skipped}, failed {failed}")
    print(f"{RAW}: {len(stored)} itineraries")
    print(f"{BOOK}: {len(trips)} keyed trips, {with_bar} with an entry bar, "
          f"{with_dives} with a stated dive count")
    # Both numbers, because they are different claims: a trip can state four
    # charges and leave one of them unpriced, and only the second number says
    # whether a total may be built from what it states.
    print(f"{'':>{len(str(BOOK))}}  {with_fees} state a mandatory charge, "
          f"{complete} state a bill that adds up")
    # The other half of the disclosure, and a different claim: what the fare
    # already covers. Printed beside the charges because two bills that state
    # only one of the two are not disclosing at the same depth.
    print(f"{'':>{len(str(BOOK))}}  {with_included} state what the fare includes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
