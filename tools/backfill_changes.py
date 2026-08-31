"""Rebuild `data/changes.json` from the dataset's own git history.

`data/CHANGES.md` holds a week of reports and the structured book beside it
starts empty, so the page would show one entry until the daily refresh had run
seven times. The reports are not lost, though: every one of them was computed
from two committed datasets, and both are still in the git log.

So this walks the commits that touched `data/egypt-2027.json`, compares each
consecutive pair with the same `changes.compare` the daily job uses, and writes
the same records `cli.append_changes_book` writes. It is a one-off, kept
because the day the book is corrupted or the window is widened it is the tool
that fills it again.

It reads git and writes one file. No network, and nothing here re-derives a
report from prose -- which is the step the structured book exists to delete.

    python3 tools/backfill_changes.py --days 10
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from liveaboard.changes import as_dict, compare  # noqa: E402
from liveaboard.cli import append_changes_book  # noqa: E402

DATA = "data/egypt-2027.json"


# What a scheduled data job's commit looks like. The four refresh workflows all
# write `data: ...`; anything else touching the dataset is a person changing a
# parser and re-promoting.
REFRESH = "data:"


def commits(path: str, limit: int, refreshes_only: bool) -> list[tuple[str, str]]:
    """`(sha, iso-date)` for the commits that touched `path`, newest first.

    Filtered to the scheduled data jobs by default, and that filter is the
    difference between a refresh history and a transcript of our own churn.
    Scanning every commit that touches the dataset, this window holds 53
    reports of which 36 say nothing moved -- because a parser change
    re-promotes the dataset and produces a report identical to a quiet refresh.
    The other 17 include three copies of one change and a 644-fare move that
    was a fee parser landing, none of which any seller did.

    Consecutive refreshes are compared, so whatever a person changed in between
    lands in the next refresh's report. That is not a distortion: it is the
    same comparison the daily job makes against `HEAD~1`, and the report has
    always been "what is different in the dataset", never "what the sellers
    did".
    """
    out = subprocess.run(
        ["git", "log", f"-{limit}", "--format=%H\t%cs\t%s", "--", path],
        cwd=ROOT, capture_output=True, text=True, check=True).stdout
    rows = []
    for line in out.splitlines():
        parts = line.split("\t", 2)
        if len(parts) < 3:
            continue
        sha, day, subject = parts
        if refreshes_only and not subject.startswith(REFRESH):
            continue
        rows.append((sha, day.strip()))
    return rows


def at(sha: str, path: str) -> dict | None:
    got = subprocess.run(["git", "show", f"{sha}:{path}"],
                         cwd=ROOT, capture_output=True, text=True)
    if got.returncode != 0:
        return None
    try:
        return json.loads(got.stdout)
    except json.JSONDecodeError:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=10,
                    help="how far back to reconstruct (default: 10)")
    ap.add_argument("--out", default=str(ROOT / "data" / "changes.json"))
    ap.add_argument("--scan", type=int, default=60,
                    help="commits to look through (default: 60)")
    ap.add_argument("--all-commits", action="store_true",
                    help="compare every commit that touched the dataset, not "
                         "only the scheduled data jobs. Reconstructs our own "
                         "re-promotes as if they were refreshes")
    ap.add_argument("--include-quiet", action="store_true",
                    help="keep reports where nothing moved. Off by default: "
                         "reconstructed from git they cannot be told apart "
                         "from a re-promote, so they assert a refresh cadence "
                         "that did not happen")
    args = ap.parse_args()

    floor = date.today() - timedelta(days=args.days - 1)
    history = commits(DATA, args.scan, not args.all_commits)
    if len(history) < 2:
        print(f"{DATA} has fewer than two commits; nothing to compare")
        return 0

    out = Path(args.out)
    if out.exists():
        out.unlink()

    # Oldest first, so `append_changes_book` -- which inserts at the front --
    # leaves the book newest first, exactly as the daily job does.
    pairs = list(zip(history, history[1:]))          # (newer, older)
    written = 0
    for (sha, day), (parent, _) in reversed(pairs):
        try:
            if date.fromisoformat(day) < floor:
                continue
        except ValueError:
            continue
        after, before = at(sha, DATA), at(parent, DATA)
        if after is None or before is None:
            continue
        report = compare(before, after)
        # Quiet reports are dropped from a *backfill*, and only from a backfill.
        #
        # Live, "nothing moved" is evidence: the daily job ran and found the
        # fleet unchanged, which is exactly what the history view's "a day with
        # no entry is a day the refresh did not run" needs in order to mean
        # anything. Reconstructed from the git log it is not evidence of
        # anything -- 36 of the 53 commits in this window are somebody
        # re-promoting after a parser change, and the report they produce is
        # identical to a quiet refresh. Keeping them would put 36 "nothing
        # moved" blocks on the page and assert a cadence that never happened.
        if not args.include_quiet and report.is_quiet and not report.fx_moved:
            continue
        record = as_dict(report, before=parent[:8], after=after.get("generated") or day)
        # The commit's date, which is when the refresh ran -- not the dataset's
        # `generated`, which is when the *sources* were crawled. They are days
        # apart whenever a parser change ships without a fresh crawl, and
        # keying on the crawl date piled twenty commits onto one heading.
        record["day"] = day
        append_changes_book(out, record)
        written += 1

    book = json.loads(out.read_text(encoding="utf-8")) if out.exists() else []
    print(f"wrote {written} report(s) to {out}; book holds {len(book)}")
    for entry in book:
        moves = len(entry["price_up"]) + len(entry["price_down"])
        print(f"  {entry['day']}  {len(entry['added'])} new  "
              f"{len(entry['withdrawn'])} withdrawn  {moves} fares moved"
              + ("  (quiet)" if entry["quiet"] else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
