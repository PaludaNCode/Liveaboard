"""A carried input must be the reading the run in front of it just took.

`publish.yml` rebuilds the dataset from the branch tip plus the files a
fetching job handed over as an artifact. Between 2026-08-31 and 2026-09-03 it
unpacked that artifact one directory above where the pipeline reads, so
`promote` went on reading the committed copy: four daily refreshes, the PADI
read, the deals read and the cabin read all succeeded, all committed, and not
one of them changed a carried input. From the outside a discarded reading and a
quiet day are the same commit, which is why it took a person four days to see.

The guards written with that fix assert the *workflow file* -- that the download
says `path: data`, that an upload stays under `data/`. They are the right
guards for that mistake and they cannot see the next one: what failed was the
file on disk, and nothing looked at it.

So this looks at it. Every book a fetch rewrites whole stamps the day it was
read -- `scraped_at` on the crawl's candidate, `collected` on the cabin, deals,
itinerary and PADI books, `retrieved` on the rates -- and after a fetch that
date is today or the reading did not arrive. It is the one assertion in this
repository that reads the clock, and it has to be here rather than in the
suite for exactly that reason: `render` and `promote` are pure, and a test that
compares committed data against today's date turns `main` red overnight with
nobody having changed anything.

A stale date fails the publish, which loses nothing. The fetch has already
happened, the branch tip is still the last good dataset, and tomorrow's run
reads again -- against a commit that would have published a rebuild of
yesterday claiming to be today's.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

STAMPS = ("scraped_at", "collected", "retrieved")
"""The field names a fetcher writes the reading's date into.

Three rather than one because the books were written at different times and
the names are in their committed payloads; renaming them is a migration this
check is not worth. Looked for in order, and the first one present is the
answer -- `data/fees.json` carries both a book-level `scraped_at` and a
`retrieved` inside each vessel's entry, and it is the book's own date that
says whether the book arrived.
"""


def stamped(payload: object) -> tuple[str | None, str | None]:
    """The date a book says it was read, and which field said so."""
    if not isinstance(payload, dict):
        return None, None
    for field in STAMPS:
        value = payload.get(field)
        if isinstance(value, str) and value:
            return value, field
    return None, None


def check(path: Path, today: str) -> str | None:
    """`None` if this file is today's reading, else why it is not."""
    if not path.exists():
        return f"{path}: not here at all — the artifact did not unpack"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return f"{path}: unreadable ({exc})"

    said, field = stamped(payload)
    if said is None:
        # Not a pass. A book with no date cannot be checked, and this check
        # exists because "cannot be checked" is how four days went missing.
        return (f"{path}: states none of {', '.join(STAMPS)}, so nothing here "
                f"can tell today's reading from last week's")
    if said != today:
        return (f"{path}: {field} is {said}, not {today} — the fetch ran and "
                f"its reading did not reach this file, so publishing would "
                f"commit a rebuild of {said} under today's subject")
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", help="Carried inputs to check.")
    # Taken as an argument so the check itself can be tested. The default is
    # UTC because a runner is on UTC and so is the date a fetcher stamps.
    parser.add_argument("--today", default=datetime.now(timezone.utc).date().isoformat(),
                        help="The day the reading should be dated (default: UTC today).")
    args = parser.parse_args(argv)

    complaints = [c for c in (check(Path(p), args.today) for p in args.paths) if c]
    for complaint in complaints:
        print(complaint)
    if complaints:
        return 1
    print(f"{len(args.paths)} carried input(s) dated {args.today}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
