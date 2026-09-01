#!/usr/bin/env python3
"""Fetch each trip's itinerary fragment and build the itinerary book.

The vessel pages say where a boat sails all year. This says where *one trip*
goes, in the operator's own words, and it also states that trip's dive count,
its group size and its entry bar. See ``liveaboard.scrape.itinerary`` for the
markup and ``docs/sources/liveaboard.com.md`` for how the endpoint was found.

Written as its own tool rather than folded into the crawl, for three reasons:

**It needs no crawl to know what to ask for.** ``data/archive.json`` is
committed and holds every ``Event`` node, and each one carries an ``@id`` of
the form ``LA-{x}-{boatID}-{tourID}``. Both ids come straight out of the
repository.

**It is incremental.** A trip's sites and dive count do not change from night
to night, so a fragment already in the book is not fetched again. The first run
costs about 340 requests; the steady state costs one per genuinely new trip,
which is the difference between a polite tool and a rude one.

**It is one request per itinerary, not per departure.** 878 tour ids exist, one
per sailing, but every departure of one trip returns the same answer. Fetching
all of them would spend 538 requests re-reading what was already in hand.

    python3 tools/fetch_itineraries.py [--limit N] [--refresh] [--delay 2]
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

from liveaboard.promote import (  # noqa: E402
    _sites_from_name,
    _sites_from_regions,
    itinerary_key,
)
from liveaboard.scrape.base import PoliteFetcher  # noqa: E402
from liveaboard.scrape.itinerary import min_logged_dives, parse_trip  # noqa: E402
from liveaboard.scrape.liveaboard_com import HOST  # noqa: E402

EVENT_ID = re.compile(r"^LA-(\d+)-(\d+)-(\d+)$")

TOUR_ID = re.compile(r"tour(?:ID|id)[\"'=: ]+(\d+)")
"""Every tour the vessel page mentions, however it spells the parameter.

The page carries them in ``?tourID=`` links and in the modal ids beside them,
and the two spellings are the same number. Matched loosely on purpose: this is
a discovery pass whose output is checked by fetching, so a false id costs one
request and a missed one costs a trip.
"""

BOAT_ID = re.compile(r"boat(?:ID|id)[\"'=: ]+(\d+)")


def vessel_page(slug: str) -> str:
    return f"https://{HOST}/diving/egypt/{slug}"


def endpoint(boat_id: str, tour_id: str) -> str:
    return (
        f"https://{HOST}/itinerary/getpopupv2"
        f"?boatID={boat_id}&tourID={tour_id}&languageID=1&curr=USD&showPrices=false"
    )


def wanted(archive: dict[str, Any]) -> dict[str, tuple[str, str, str, str]]:
    """``{key: (slug, boatID, tourID, name)}``, one tour per itinerary.

    Keyed by :func:`liveaboard.promote.itinerary_key`, so the book this writes
    is looked up by promote with the identical rule rather than a copy of it.
    """
    out: dict[str, tuple[str, str, str, str]] = {}
    for page in archive.get("pages", []):
        slug = page["url"].split("?")[0].rstrip("/").rsplit("/", 1)[-1]
        for node in page.get("nodes", []):
            if node.get("@type") != "Event":
                continue
            match = EVENT_ID.match(node.get("@id") or "")
            if not match:
                continue
            _, boat_id, tour_id = match.groups()
            name = " ".join((node.get("name") or "").split())
            if not name:
                continue
            out.setdefault(itinerary_key(slug, name), (slug, boat_id, tour_id, name))
    return out


def unfragmented(dataset: Path, book: dict[str, Any]) -> dict[str, int]:
    """``{vessel slug: itineraries with no fragment}``, from what shipped.

    The honest question rather than a guess at which boats to visit. 85 of the
    402 published itineraries have no fragment, and they are not scattered:
    they are the boats whose sailings come from PADI, because
    :func:`wanted` reads tour ids out of archived ``Event`` nodes and a vessel
    liveaboard.com sells no in-season berth on contributes none. Twenty-one
    boats today, so the discovery pass costs twenty-one requests rather than
    one per hull in the fleet.

    Missing or unreadable dataset means no discovery rather than a crash: this
    is an optimisation on which pages to open, and the fetcher's ordinary path
    does not depend on it.
    """
    try:
        payload = json.loads(dataset.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    short: dict[str, int] = {}
    for itinerary in payload.get("itineraries", []):
        slug, name = itinerary.get("boat_id"), itinerary.get("name")
        if not slug or not name:
            continue
        if itinerary_key(slug, name) not in book:
            short[slug] = short.get(slug, 0) + 1
    return short


def load(path: Path) -> dict[str, Any]:
    """The book, re-keyed from each record's own vessel and trip name.

    Never trusted as written. The file's keys are :func:`itinerary_key`'s
    output *as of the run that wrote them*, and the day that rule changes --
    it just did, to stop a port pair's spacing splitting one trip into two --
    every stored key stops matching, `todo` counts all 317 trips as new, and
    the tool spends a full crawl re-reading what it already has. `promote`
    re-keys the same way, off `boat` and `name`, so this is the file agreeing
    with the two things that read it rather than a migration to remember.

    A re-key that collides is reported and refused: two records under one key
    means one trip's dive count and reefs would answer for another, which is
    the failure this book exists to prevent rather than a tidy-up.
    """
    if not path.exists():
        return {}
    try:
        stored = json.loads(path.read_text(encoding="utf-8")).get("trips") or {}
    except (OSError, ValueError) as exc:
        print(f"could not read {path} ({exc}); starting fresh", file=sys.stderr)
        return {}

    book: dict[str, Any] = {}
    moved = 0
    for key, record in stored.items():
        if not (record.get("boat") and record.get("name")):
            book[key] = record
            continue
        fresh = itinerary_key(record["boat"], record["name"])
        if fresh in book:
            raise SystemExit(
                f"two trips re-key onto {fresh!r}: {book[fresh]['name']!r} and "
                f"{record['name']!r}. One would serve the other's dive count "
                f"and reefs; fix the key rule rather than dropping either."
            )
        moved += fresh != key
        book[fresh] = record
    if moved:
        print(f"re-keyed {moved} of {len(book)} trips onto the current rule")
    return book


def record(slug, boat_id, tour_id, name, detail, sites) -> dict[str, Any]:
    """One book entry, built the same way whichever pass found the tour.

    Two now do: the archive names a trip beside its id, and the vessel page
    states only the id, so a discovered fragment supplies its own ``name`` from
    the heading it carries. The shape must not depend on which -- a record the
    fetcher writes two ways is a record `promote` reads one way.
    """
    return {
        "boat": slug,
        "name": name,
        "tour_id": tour_id,
        "collected": date.today().isoformat(),
        "regions": list(detail.regions),
        "dive_sites": sites,
        "dives": detail.dives,
        "guests": detail.guests,
        "experience": detail.experience,
        "min_logged_dives": min_logged_dives(detail.experience),
        # The operator's own prose, verbatim. Stored rather than
        # interpreted: which words in it are dive sites is a question for
        # one vocabulary in `promote`, asked offline, so that improving
        # that vocabulary never means fetching these pages again -- the
        # same reason `data/archive.json` keeps nodes nothing parses yet.
        #
        # It is headed "Sample Itinerary" and its days are not contiguous,
        # so it is a sketch of the week. Anything that renders it should
        # say so rather than presenting it as a log.
        "intro": detail.intro,
        "sections": [
            {"heading": x.heading, "text": x.text, "is_day": x.is_day}
            for x in detail.sections
        ],
        "source_url": endpoint(boat_id, tour_id),
    }


def listed_vessels(fees: Path) -> set[str]:
    """The vessels liveaboard.com has a page for, per the fee book.

    Read for one thing only: whether a boat's vessel page exists to harvest
    tour ids from. Six of the boats short of a fragment have none -- they are
    hulls PADI sells and this site does not -- so asking costs a 404 or a soft
    search page, and both of those look exactly like a page with no new tours.

    Empty when unreadable, which turns the gate off rather than closing it: a
    missing fee book must not silently stop the discovery pass.
    """
    try:
        return set(json.loads(fees.read_text(encoding="utf-8")).get("vessels") or {})
    except (OSError, ValueError):
        return set()


def report_coverage(args, book: dict[str, Any]) -> None:
    """What is still not covered, said out loud, on every run.

    Every field this book fills has a fallback in `promote`, so a key that
    matches nothing fails silently and the page goes on answering from the trip
    title. That is how 71 of 314 itineraries once went unread under their
    banner spellings, and how 85 went unread because their tour ids were never
    in the archive to begin with -- a vessel liveaboard.com sells no in-season
    berth on publishes no `Event` node to carry one.

    So the number is printed whether or not the run fetched anything. A count
    that should fall and does not is the only signal there is, and a signal
    nobody prints is not one.
    """
    still = unfragmented(args.dataset, book)
    if still:
        print(f"\n{sum(still.values())} published itinerar(ies) on {len(still)} "
              f"boat(s) still have no fragment:")
        for slug, count in sorted(still.items(), key=lambda kv: (-kv[1], kv[0]))[:20]:
            print(f"    {count:3}  {slug}")
        if not args.discover:
            print("    (--discover harvests tour ids off these boats' vessel pages)")
    elif args.dataset.exists():
        print("\nevery published itinerary has a fragment")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", default=Path("data/archive.json"), type=Path)
    parser.add_argument("--out", default=Path("data/itineraries.json"), type=Path)
    parser.add_argument("--snapshots", default=Path("data/snapshots"), type=Path)
    parser.add_argument("--limit", type=int, default=0, help="cap fetches (0 = all)")
    parser.add_argument("--delay", type=float, default=2.0)
    parser.add_argument(
        "--refresh", action="store_true",
        help="re-fetch trips already in the book, rather than only the new ones",
    )
    parser.add_argument(
        "--dataset", default=Path("data/egypt-2027.json"), type=Path,
        help="what shipped, read to find which boats still have unfragmented trips",
    )
    parser.add_argument(
        "--fees", default=Path("data/fees.json"), type=Path,
        help="the fee book, read only to know which vessels liveaboard.com lists",
    )
    parser.add_argument(
        "--boat", default="", help="restrict --discover to one vessel slug")
    parser.add_argument(
        "--discover", action="store_true",
        help="also harvest tour ids off the vessel pages of boats whose "
             "published itineraries have no fragment (one request per boat)",
    )
    args = parser.parse_args()

    if not args.archive.exists():
        print(f"no {args.archive}; run a scrape first", file=sys.stderr)
        return 1

    archive = json.loads(args.archive.read_text(encoding="utf-8"))
    trips = wanted(archive)

    # The book is always loaded and always merged into, even on --refresh.
    #
    # --refresh used to start from an empty book, which is right for a full run
    # and destroys the file on a capped one: `--refresh --limit 6` re-read six
    # trips and wrote a book containing only those six, dropping 309 records
    # and taking 247 dive counts and 305 entry bars out of the dataset with
    # them. A run knows nothing about the trips it did not visit, which is the
    # same rule `scrape_fees.py --limit` already follows.
    book = load(args.out)

    todo = list(trips) if args.refresh else [k for k in trips if k not in book]
    if args.limit:
        todo = todo[: args.limit]
    print(f"{len(trips)} itineraries in the archive, {len(book)} already read, "
          f"{len(todo)} to {'re-read' if args.refresh else 'fetch'}")

    fetcher = PoliteFetcher(snapshot_dir=args.snapshots, delay=args.delay)
    unknown: dict[str, int] = {}
    failed = 0
    added = 0

    # Tour ids the archive cannot know about, harvested from the vessel page.
    #
    # An `Event` node is a sailing, so a boat liveaboard.com lists no in-season
    # departure for publishes none, and `wanted()` above finds nothing for it
    # however many times it runs. Those are exactly the boats whose rows come
    # from the second seller -- 85 published itineraries, 210 sailings, a fifth
    # of the season, every one with no dive count, group size, entry bar or
    # reef list of its own. The ids are on the vessel page regardless: MY Blue
    # Pearl states twelve while selling no season berth at all.
    #
    # These arrive without a name, which is why `TripDetail.name` exists: an
    # id harvested this way is keyed on the trip the fragment says it is.
    discovered: dict[str, tuple[str, str]] = {}
    if args.discover:
        short = unfragmented(args.dataset, book)
        known_tours = {str(t.get("tour_id")) for t in book.values() if t.get("tour_id")}
        listed = listed_vessels(args.fees)
        # A boat with no liveaboard.com vessel page has no page to harvest,
        # and asking for one is a 404 or a soft search page -- both of which
        # read as "nothing new" and cost a request to learn. The fee book is
        # the record of which hulls that site carries, so it is the gate.
        # Six boats and 41 of the 85 itineraries are on the far side of it:
        # PADI is the only source those trips will ever have.
        elsewhere = {s: n for s, n in short.items() if listed and s not in listed}
        short = {s: n for s, n in short.items() if s not in elsewhere}
        if args.boat:
            # One vessel, for proving the pass against real trips before
            # pointing it at fifteen boats' pages -- the same reason
            # `scrape_fees.py --limit` exists. A cap alone does not do it: the
            # ids sort by boat, so `--limit` reads whichever vessel comes first
            # alphabetically whether or not that is the one being checked.
            short = {s: n for s, n in short.items() if s == args.boat}
            elsewhere = {}
        print(f"\ndiscovery: {sum(short.values())} unfragmented itineraries on "
              f"{len(short)} boats with a liveaboard.com page")
        if elsewhere:
            print(f"    ({sum(elsewhere.values())} more on {len(elsewhere)} boat(s) "
                  f"that site does not list: {', '.join(sorted(elsewhere))})")
        for slug in sorted(short):
            try:
                page = fetcher.get(vessel_page(slug)).body
            except Exception as exc:  # noqa: BLE001 - one bad page, not the run
                print(f"    {slug:24.24} {exc}", flush=True)
                failed += 1
                continue
            boat_ids = set(BOAT_ID.findall(page))
            tour_ids = set(TOUR_ID.findall(page)) - known_tours
            if not boat_ids or not tour_ids:
                # Not a failure. The page answered and named no tour we do not
                # already hold, which is the difference between a boat whose
                # trips are all read and one nobody asked about.
                print(f"    {slug:24.24} {short[slug]:2} short, nothing new on the page",
                      flush=True)
                continue
            boat_id = sorted(boat_ids)[0]
            for tour_id in sorted(tour_ids):
                discovered[tour_id] = (slug, boat_id)
            print(f"    {slug:24.24} {short[slug]:2} short, {len(tour_ids)} new tour id(s)",
                  flush=True)
        if args.limit:
            # Capped by boat, not by id. Sorting on the tour id alone spread a
            # six-request cap across six vessels and read whichever trips
            # happened to carry the lowest numbers -- which on the first run
            # were all trips the book already held under another id, so the
            # capped run proved nothing and wrote nothing.
            order = sorted(discovered.items(), key=lambda kv: (kv[1][0], kv[0]))
            discovered = dict(order[: args.limit])
        print(f"discovery: {len(discovered)} tour(s) to read\n")

    if not todo and not discovered:
        print("nothing to do")
        report_coverage(args, book)
        return 0

    for index, entry in enumerate(todo, 1):
        slug, boat_id, tour_id, name = trips[entry]
        try:
            result = fetcher.get(endpoint(boat_id, tour_id))
        except Exception as exc:  # noqa: BLE001 - one bad trip must not end the run
            print(f"  [{index}/{len(todo)}] {slug}: {exc}", flush=True)
            failed += 1
            continue

        detail = parse_trip(result.body)
        if not detail:
            print(f"  [{index}/{len(todo)}] {slug}: {name[:40]}: nothing parsed", flush=True)
            failed += 1
            continue

        # The regions are proper names -- "The Brothers" -- and the page filters
        # on canonical site keys. Folding them through the same recogniser the
        # titles use keeps one vocabulary on the page; anything it does not know
        # is reported rather than dropped silently, because a reef the operator
        # names and we cannot is a gap in SITE_HINTS worth closing deliberately.
        sites = _sites_from_regions(detail.regions)
        for region in detail.regions:
            if not _sites_from_name(region):
                unknown[region] = unknown.get(region, 0) + 1

        book[entry] = record(slug, boat_id, tour_id, name, detail, sites)
        added += 1
        print(
            f"  [{index}/{len(todo)}] {slug:22.22} {name[:34]:34} "
            f"sites={sites or '-'} dives={detail.dives or '-'} "
            f"sections={len(detail.sections) or '-'}",
            flush=True,
        )

    # The harvested ids, which arrive with no name attached. Each fragment is
    # asked what trip it is and keyed on its own answer -- the one place a
    # discovered tour states it. A fragment that heads itself with nothing
    # cannot be filed, and is counted rather than guessed at.
    unnamed = 0
    for index, (tour_id, (slug, boat_id)) in enumerate(sorted(discovered.items()), 1):
        try:
            result = fetcher.get(endpoint(boat_id, tour_id))
        except Exception as exc:  # noqa: BLE001 - one bad trip must not end the run
            print(f"  [d{index}/{len(discovered)}] {slug}: {exc}", flush=True)
            failed += 1
            continue
        detail = parse_trip(result.body)
        if not detail:
            print(f"  [d{index}/{len(discovered)}] {slug:22.22} tour {tour_id}: "
                  f"nothing parsed", flush=True)
            failed += 1
            continue
        if not detail.name:
            # Read, and unfileable. Counted separately from a failure because
            # it is not one: the fragment answered and did not say which trip
            # it was, and a name inferred from the boat and the id would be
            # this code naming a trip nobody wrote.
            print(f"  [d{index}/{len(discovered)}] {slug:22.22} tour {tour_id}: "
                  f"no trip name in the fragment", flush=True)
            unnamed += 1
            continue
        entry = itinerary_key(slug, detail.name)
        if entry in book:
            # Two tour ids for one trip, which is ordinary: the vessel page
            # lists a tour per departure pattern and several sell the same
            # week. Already read is already read.
            continue
        sites = _sites_from_regions(detail.regions)
        for region in detail.regions:
            if not _sites_from_name(region):
                unknown[region] = unknown.get(region, 0) + 1
        book[entry] = record(slug, boat_id, tour_id, detail.name, detail, sites)
        added += 1
        print(
            f"  [d{index}/{len(discovered)}] {slug:22.22} {detail.name[:34]:34} "
            f"sites={sites or '-'} dives={detail.dives or '-'} "
            f"sections={len(detail.sections) or '-'}",
            flush=True,
        )
    if unnamed:
        print(f"\n{unnamed} discovered fragment(s) named no trip and were not filed")


    if not added:
        # A run that read nothing must not rewrite the file: the only thing
        # that would change is the collected date, which would report the book
        # as fresh on the strength of 340 failed requests.
        print(f"\nnothing read; {args.out} left as it was ({failed} failed)")
        return 1 if failed else 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "collected": date.today().isoformat(),
                "source": "liveaboard.com",
                "note": (
                    "One itinerary fragment per trip, from /itinerary/getpopupv2. "
                    "Regions, dive count, group size, entry bar and the "
                    "operator's own sample itinerary are its words about this "
                    "trip rather than about the boat. `days` is kept verbatim; "
                    "which of those words are dive sites is decided in promote. "
                    "Incremental: a trip already here is not re-fetched, so a "
                    "parser change needs --refresh to reach trips already read."
                ),
                "trips": dict(sorted(book.items())),
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\nwrote {args.out}: {len(book)} trips ({added} new), {failed} failed")

    if unknown:
        # Not an error. A region the operator names and SITE_HINTS does not is
        # a reef worth adding by hand, and printing it is how that gets noticed.
        print(f"\n{len(unknown)} region(s) no site hint recognises:")
        for region, count in sorted(unknown.items(), key=lambda kv: -kv[1])[:20]:
            print(f"    {count:3}  {region}")

    report_coverage(args, book)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
