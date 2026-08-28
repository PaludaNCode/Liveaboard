#!/usr/bin/env python3
"""Read the booking step-1 page, which is where a berth count could still be.

The vessel page does not state how many spots are left (#79): not in the
`Offer`, not in the description, not in 43 rendered rows, not in any XHR. The
one place left is the booking flow, and the site's owner pointed at it:

    https://www.liveaboard.com/BookingStep1?tourid=415714&boatid=6240

which is Iceberg's June 2027 "Hurghada North" week.

**Both ids are already in the repository.** Every `Event.@id` is
`LA-{x}-{boatID}-{tourID}` on all 889 archived events, so this URL is built
rather than crawled -- the same trick that made the itinerary fragment
affordable. If the count is here, filling `spaces_left` costs one request per
*departure*, which is 892 a night and is a real cost to weigh rather than a
free one.

**This reads, it does not book.** Step one of a booking flow is a page with a
form on it; a GET renders it and submits nothing. Nothing here posts, and the
polite fetcher still asks robots.txt first -- if the site disallows the path,
the probe refuses and that refusal is the answer.

What it reports, per departure:

* the status and size of what came back, and whether it is a booking page at
  all rather than a redirect to the vessel or a login wall;
* every phrasing a remaining-berth count could take;
* the cabin or occupancy options offered, since "how many spots at the listed
  price" may be a per-cabin-type answer rather than one number;
* any JSON embedded in the page carrying an inventory-shaped key.

Writes nothing and parses nothing into the dataset.

    python3 tools/probe_booking.py [--limit 4] [--chars 4000]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from liveaboard.scrape.base import FetchBlocked, PoliteFetcher  # noqa: E402
from liveaboard.scrape.liveaboard_com import HOST  # noqa: E402

ARCHIVE = Path("data/archive.json")
EVENT_ID = re.compile(r"^LA-(\d+)-(\d+)-(\d+)$")
TAG = re.compile(r"<[^>]+>")

COUNT = re.compile(
    r"""(\d{1,2}\s*(?:spaces?|places?|berths?|spots?|seats?)\b
        |\b(?:only|last|just)\s+\d{1,2}\b
        |\d{1,2}\s*(?:left|remaining|available)\b
        |\b(?:spaces?|places?|berths?|spots?)\s+(?:left|remaining|available)
        |\bfully\s+booked\b|\bsold\s*out\b|\bno\s+more\s+spaces\b)""",
    re.I | re.X,
)

# "How many at the listed price" may be answered per cabin type rather than
# once, so what the form offers is as interesting as any single number.
OPTION = re.compile(r"<option[^>]*>(.*?)</option>", re.I | re.S)
INVENTORY_KEY = re.compile(
    r'"[^"]*(?:avail|space|place|berth|spot|seat|capacit|remain|slot|vacan|'
    r'occupanc|quantity|stock|cabin)[^"]*"\s*:\s*[^,}]{0,40}', re.I
)


def departures(limit: int) -> list[tuple[str, str, str]]:
    """(boatID, tourID, name) for a few real departures, from the archive."""
    archive = json.loads(ARCHIVE.read_text(encoding="utf-8"))
    seen: dict[str, tuple[str, str, str]] = {}
    for page in archive["pages"]:
        for node in page.get("nodes", []):
            if node.get("@type") != "Event":
                continue
            match = EVENT_ID.match(node.get("@id") or "")
            if not match:
                continue
            _, boat, tour = match.groups()
            name = " ".join((node.get("name") or "").split())
            # Sold-out sailings are the most likely to say something explicit,
            # and the least likely to be a booking anyone could complete.
            seen.setdefault(tour, (boat, tour, name))
    picked = list(seen.values())
    return picked[:limit] if limit else picked


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=4)
    parser.add_argument("--chars", type=int, default=4000,
                        help="page text to print per departure")
    parser.add_argument("--tours", default="",
                        help="explicit boatid:tourid pairs, e.g. 6240:415714")
    args = parser.parse_args()

    if args.tours:
        targets = []
        for pair in args.tours.split(","):
            boat, _, tour = pair.strip().partition(":")
            targets.append((boat, tour, "(named on the command line)"))
    else:
        targets = departures(args.limit)

    fetcher = PoliteFetcher(snapshot_dir=Path("data/snapshots"))
    print(f"{len(targets)} booking page(s); reading only, nothing is submitted\n")

    found, blocked, read = [], 0, 0

    for boat, tour, name in targets:
        url = f"https://{HOST}/BookingStep1?tourid={tour}&boatid={boat}"
        print("=" * 78)
        print(f"{url}\n   {name[:70]}")
        try:
            result = fetcher.get(url)
        except FetchBlocked as exc:
            # robots.txt refusing this path is a complete answer, not a hurdle.
            print(f"    refused: {exc}")
            blocked += 1
            continue
        except Exception as exc:  # noqa: BLE001 - one bad page must not end the run
            print(f"    failed: {exc}")
            continue

        read += 1
        text = " ".join(TAG.sub(" ", result.body).split())
        print(f"    {result.status}, {len(result.body):,} bytes, "
              f"{len(text):,} chars of text")
        print(f"    looks like a booking page: "
              f"{'book' in text.lower()[:4000] or 'cabin' in text.lower()}")

        hits = {m.group(0) for m in COUNT.finditer(text)}
        print(f"    count phrasings: {sorted(hits) if hits else 'none'}")
        if hits:
            found.append((url, sorted(hits)))

        options = [" ".join(TAG.sub(" ", o).split()) for o in OPTION.findall(result.body)]
        options = [o for o in options if o]
        if options:
            print(f"    {len(options)} <option>(s), first 15:")
            for option in options[:15]:
                print(f"      {option[:90]}")

        keys = sorted({m.group(0)[:90] for m in INVENTORY_KEY.finditer(result.body)})
        if keys:
            print(f"    inventory-shaped keys in embedded JSON ({len(keys)}):")
            for key in keys[:15]:
                print(f"      {key}")

        print(f"    --- first {args.chars} chars of page text ---")
        print("    " + text[: args.chars])

    print("\n" + "=" * 78)
    print("SUMMARY")
    print(f"  booking pages read       : {read}")
    print(f"  refused by robots.txt    : {blocked}")
    print(f"  pages naming a count     : {len(found)}")
    for url, hits in found:
        print(f"    {hits} in {url}")
    if not read:
        print("\n  NO CONCLUSION: nothing was read, so this says nothing about")
        print("  what the booking flow contains.")
    elif not found:
        print("\n  Read the page text above before concluding. A count could be")
        print("  rendered client-side here too, in which case this plain GET")
        print("  would not see it and a browser pass is the next step.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
