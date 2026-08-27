#!/usr/bin/env python3
"""Apply a parser improvement to the committed candidate, without a crawl.

``data/archive.json`` holds every JSON-LD node each page published, parsed or
not, and it is committed for exactly this reason: *add fields to the parser
freely; do not trim the archive to match what the parser happens to read.* The
implied second half of that rule is that when the parser learns to read a new
field, the archive already contains it for every page of the last crawl.

Without this tool that second half is unavailable. A field added to the parser
reaches the site only when the next full crawl runs -- 320 requests over half
an hour against someone else's server -- to re-read data already sitting in the
repository. That is both slow and rude, and it is the same shape of gap as #53:
code and data drifting because the only path between them is a scheduled job.

So this re-reads the archive through the adapter's own field readers and fills
what is missing on ``data/candidate.json``. Two properties make it safe to run:

**It only ever fills, never overwrites.** A departure that already carries a
field keeps it. So a run after a fresh crawl is a no-op, and the tool cannot
quietly replace scraped data with archived data.

**It joins on identity, not position.** Archived ``Event`` nodes are matched to
candidate departures by boat slug and date, both of which the archive states
directly. A reordered candidate is not a changed one.

It is a bridge, not a pipeline stage: once a full crawl has run with the parser
that reads a field, this has nothing left to do for that field.

    python3 tools/reparse_candidate.py                 # report what is missing
    python3 tools/reparse_candidate.py --write         # fill it in

Then re-promote and rebuild, which CI checks anyway:

    PYTHONPATH=src python3 -m liveaboard.cli promote
    PYTHONPATH=src python3 -m liveaboard.cli build
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from liveaboard.scrape.liveaboard_com import organizer_name  # noqa: E402

# The fields this tool knows how to recover, each mapped to the adapter
# function that reads it. Reusing the adapter's own reader rather than a second
# implementation is the point: it makes the backfill provably identical to what
# a crawl would have produced, instead of merely similar.
READERS: dict[str, Any] = {
    "operator": organizer_name,
}


def slug_of(url: str) -> str:
    """The vessel slug in an archived page URL."""
    return urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]


def archived_events(archive: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    """Archived ``Event`` nodes, keyed by (boat slug, start date).

    A boat sails one trip on one date, so the pair is an identity. The Event's
    own ``@id`` would be tighter still, but the candidate does not record it --
    departure ids are built from slug and date, which is what this can join on.
    """
    events: dict[tuple[str, str], dict[str, Any]] = {}
    for page in archive.get("pages", []):
        slug = slug_of(page.get("url", ""))
        if not slug:
            continue
        for node in page.get("nodes", []):
            if node.get("@type") != "Event":
                continue
            start = str(node.get("startDate") or "")[:10]
            if start:
                events.setdefault((slug, start), node)
    return events


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", default=Path("data/candidate.json"), type=Path)
    parser.add_argument("--archive", default=Path("data/archive.json"), type=Path)
    parser.add_argument(
        "--write", action="store_true", help="apply the fill (default: report only)"
    )
    parser.add_argument(
        "--field",
        action="append",
        choices=sorted(READERS),
        help="only this field (default: every field this tool knows)",
    )
    args = parser.parse_args()

    for path in (args.candidate, args.archive):
        if not path.exists():
            print(f"{path} not found", file=sys.stderr)
            return 1

    candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
    archive = json.loads(args.archive.read_text(encoding="utf-8"))
    events = archived_events(archive)
    fields = args.field or sorted(READERS)

    print(f"{args.archive}: {len(events)} archived events, collected {archive.get('scraped_at')}")
    print(f"{args.candidate}: {len(candidate.get('departures', []))} departures")

    filled: Counter[str] = Counter()
    already: Counter[str] = Counter()
    unmatched = 0
    silent: Counter[str] = Counter()

    for departure in candidate.get("departures", []):
        key = (departure.get("boat_slug") or "", str(departure.get("start") or "")[:10])
        node = events.get(key)
        if node is None:
            unmatched += 1
            continue
        for field in fields:
            if departure.get(field) is not None:
                already[field] += 1
                continue
            value = READERS[field](node)
            if value is None:
                # The archive is what the page published, so a missing value
                # here means the source never stated it. Counted, not invented.
                silent[field] += 1
                continue
            departure[field] = value
            filled[field] += 1

    for field in fields:
        print(
            f"  {field}: {filled[field]} to fill, {already[field]} already set, "
            f"{silent[field]} not stated by the source"
        )
        if filled[field]:
            values = Counter(
                d[field] for d in candidate.get("departures", []) if d.get(field) is not None
            )
            print(f"    {len(values)} distinct; most common:")
            for value, count in values.most_common(8):
                print(f"      {count:4d}  {value}")

    if unmatched:
        # Expected when the candidate is newer than the archive, and a warning
        # worth reading when it is not.
        print(f"  {unmatched} departures had no archived event (candidate newer than archive?)")

    if not sum(filled.values()):
        print("nothing to fill")
        return 0

    if not args.write:
        print("\nreport only; pass --write to apply")
        return 0

    args.candidate.write_text(
        json.dumps(candidate, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"\nwrote {args.candidate}; re-promote and rebuild to publish it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
