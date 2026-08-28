"""Command line entry points.

    python3 -m liveaboard.cli build    # dataset -> static site
    python3 -m liveaboard.cli check    # validate and summarise
    python3 -m liveaboard.cli scrape   # refresh from the source sites
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import Any

from .dataset import Dataset, DatasetError
from .pricing import compute, mandatory_known, resolve_fees
from .promote import itinerary_key, promote
from .render import render
from .scrape.base import FetchBlocked, PoliteFetcher, ScrapeOutput
from .scrape.liveaboard_com import LiveaboardComAdapter
from .scrape.padi_com import PadiComAdapter

SEED_DATA = Path("data/seed/egypt-2027.json")
LIVE_DATA = Path("data/egypt-2027.json")


def default_data() -> Path:
    """The dataset to use when nobody names one.

    This was a constant pointing at the seed, and the deploy job builds with no
    --data. So every published page was built from five placeholder boats while
    the repository beside it held sixty-seven scraped ones, and the seed banner
    on the live site was telling the truth about a page nobody meant to ship.

    Verifying it did not catch this: rebuilding from main with an explicit
    --data reproduces what the *commit* step builds, not what the *deploy* step
    builds. The two only diverged because of this default.

    Preferring the real dataset means the seed is what you get on a fresh
    checkout that has never scraped, which is the only time it is the right
    answer.
    """
    return LIVE_DATA if LIVE_DATA.exists() else SEED_DATA
DEFAULT_OUT = Path("site")
DEFAULT_SNAPSHOTS = Path("data/snapshots")

ADAPTERS = {
    "liveaboard.com": LiveaboardComAdapter,
    "padi.com": PadiComAdapter,
}


def cmd_build(args: argparse.Namespace) -> int:
    data = args.data or default_data()
    dataset = Dataset.load(data)
    print(f"building from {data}")
    # The dataset's own folder is where the downloadable copies come from, so a
    # build from the seed publishes the seed's files and never the live ones.
    target = render(dataset, args.out, data_dir=Path(data).parent)
    payload_size = target.stat().st_size
    print(f"built {target} ({payload_size / 1024:.0f} KB)")
    print(
        f"  {len(dataset.departures)} departures, "
        f"{len(dataset.itineraries)} itineraries, {len(dataset.boats)} boats"
    )
    if not dataset.is_fully_verified:
        print("  note: dataset contains seed estimates; the page says so prominently")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Validate the dataset and print what the engine makes of it."""
    data = args.data or default_data()
    try:
        dataset = Dataset.load(data)
    except DatasetError as exc:
        print(f"dataset invalid: {exc}", file=sys.stderr)
        return 1

    print(f"{data}: valid")
    print(f"  sources: {', '.join(sorted(k.value for k in dataset.source_kinds))}")
    print()

    rows: list[tuple[str, float, float, float | None]] = []
    unknown = 0
    for key, itinerary in dataset.itineraries.items():
        departures = [d for d in dataset.departures if d.itinerary_id == key]
        if not departures:
            continue
        first = departures[0]
        breakdown = compute(itinerary, first, dataset.fx)
        # Same rule as the rendered page. No fee lines means nobody has
        # looked; only optional ones means the operator did not state its
        # unavoidable costs. Neither means the advertised price is the whole
        # bill, and scoring them as if it were ranked the least forthcoming
        # operators highest.
        known = bool(resolve_fees(itinerary, first)) and mandatory_known(itinerary, first)
        if not known:
            unknown += 1
        rows.append(
            (
                itinerary.name,
                float(breakdown.base.rounded),
                float(breakdown.total.rounded),
                breakdown.markup_pct if known else None,
            )
        )

    width = min(max((len(r[0]) for r in rows), default=20), 44)
    print(f"  {'itinerary'.ljust(width)}  {'advertised':>11} {'true cost':>10} {'lands later':>12}")
    for name, base, total, markup in sorted(rows, key=lambda r: -(r[3] or -1)):
        if markup is None:
            print(f"  {name[:width].ljust(width)}  {base:>11,.0f} {'unknown':>10} {'—':>12}")
        else:
            print(
                f"  {name[:width].ljust(width)}  {base:>11,.0f} {total:>10,.0f} "
                f"{total - base:>7,.0f} ({markup:>2.0f}%)"
            )

    if unknown:
        print(
            f"\n  {unknown} of {len(rows)} itineraries have no stated mandatory "
            f"fees, so no true cost is claimed for them"
        )

    # Dive sites rather than a route label: the label was ours, the sites are
    # the operator's, and the page filters on the sites.
    print()
    for itinerary in dataset.itineraries.values():
        sites = ", ".join(itinerary.dive_sites) or itinerary.region or "not named"
        print(f"  {itinerary.title or itinerary.name:44.44} {sites:44.44}")
    return 0


BARREN_RECHECK_DAYS = 7
"""How long a "sells nothing this season" verdict is trusted.

Long enough to save the forty-eight daily fetches those vessels cost, short
enough that a boat opening a season is picked up within a week. Never
permanent: a cache that stops expiring is a fleet that quietly shrinks.
"""


def _barren(path: Path) -> tuple[set[str], dict[str, str]]:
    """Vessels to skip this run, and the full record they came from."""
    if not path.exists():
        return set(), {}
    try:
        record = json.loads(path.read_text(encoding="utf-8")).get("vessels") or {}
    except (OSError, ValueError):
        return set(), {}
    today = date.today()
    fresh = set()
    for slug, checked in record.items():
        try:
            age = (today - date.fromisoformat(checked)).days
        except (TypeError, ValueError):
            continue
        if 0 <= age < BARREN_RECHECK_DAYS:
            fresh.add(slug)
    return fresh, record


CARRY_MAX_DAYS = 14
"""How long a departure may be carried through unreadable pages.

Long enough to ride out a bad week -- a page that fails today usually reads
fine tomorrow -- and short enough that we stop asserting a sailing exists on
the strength of a fortnight-old reading. After this the departures drop out
and the change report says the vessel-month emptied, which is then true: we
have not been able to see it for two weeks and should not keep implying we can.
"""


def carry_unread(
    previous: dict[str, Any] | None,
    unread: Iterable[str],
    today: date,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Departures and vessels the last run read and this one did not.

    A vessel page is fetched once per season month, so one unreadable response
    empties that boat's month while the other three come back fine. Publishing
    that absence deleted five real, bookable DUNE Longara sailings from the
    site and reported them as withdrawn.

    The barren skip list did the same thing without anything going wrong: it
    holds a vessel back for a week to save four requests, and while it does,
    that vessel's departures were dropped and reported as withdrawn. AVO and
    Blue lost three real, bookable sailings that way, and a probe found them
    still on sale.

    The same rule the fee book already follows: a run that did not look at
    something knows nothing about it, and knowing nothing is not the same as
    knowing there is nothing. Carried rows keep their original ``retrieved``
    date, so the page still says exactly when each price was last read.

    ``CARRY_MAX_DAYS`` deliberately outlasts ``BARREN_RECHECK_DAYS``: a skipped
    vessel is re-read within a week, so the carry never has to hold longer than
    the skip does.

    Returns the departures, the vessel records they need, and one note per
    page carried.
    """
    if not previous:
        return [], [], []
    pages = {url for url in unread if url}
    if not pages:
        return [], [], []

    def fresh(row: dict[str, Any]) -> bool:
        stamp = (row.get("provenance") or {}).get("retrieved")
        try:
            return (today - date.fromisoformat(stamp)).days <= CARRY_MAX_DAYS
        except (TypeError, ValueError):
            return False

    departures = [
        row for row in previous.get("departures", [])
        if (row.get("provenance") or {}).get("url") in pages and fresh(row)
    ]
    # The vessel record too, for any boat something was carried for: a boat
    # whose every page failed has no record in this run either, and a departure
    # promote cannot find a vessel for is a departure dropped. Offered rather
    # than imposed -- the caller keeps this run's record wherever it has one,
    # so a stale summary never displaces a fresh one.
    # One per vessel. The candidate holds a record per *month page*, so a boat
    # whose whole season is carried would otherwise arrive four times over.
    carried_slugs = {row.get("boat_slug") for row in departures}
    itineraries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in previous.get("itineraries", []):
        slug = row.get("id")
        if slug in carried_slugs and slug not in seen and fresh(row):
            seen.add(slug)
            itineraries.append(row)

    notes = []
    for url in sorted(pages):
        kept = sum(
            1 for row in departures
            if (row.get("provenance") or {}).get("url") == url
        )
        if kept:
            notes.append(
                f"carried {kept} departure(s) forward from the last run: "
                f"{url} was not read this run, and a page nobody looked at is "
                f"not an empty one"
            )
    return departures, itineraries, notes


def cmd_scrape(args: argparse.Namespace) -> int:
    """Run the source adapters and write their raw output.

    Deliberately does not overwrite the live dataset: a scrape writes a
    candidate file, and promoting it is a separate, reviewable step.
    """
    fetcher = PoliteFetcher(snapshot_dir=Path(args.snapshots), diagnose=args.diagnose)
    selected = [args.source] if args.source else list(ADAPTERS)

    combined = ScrapeOutput()
    blocked = 0

    barren_path = Path(args.barren)
    skip_vessels, barren_record = _barren(barren_path)
    if skip_vessels:
        print(
            f"-- skipping {len(skip_vessels)} vessel(s) that sold nothing this season "
            f"when last checked; re-checked after {BARREN_RECHECK_DAYS} days"
        )

    for name in selected:
        adapter_cls = ADAPTERS.get(name)
        if adapter_cls is None:
            print(f"unknown source {name!r}; known: {', '.join(ADAPTERS)}", file=sys.stderr)
            return 2

        adapter = adapter_cls(fetcher)
        if args.max_pages:
            adapter.max_pages = args.max_pages
        if skip_vessels and hasattr(adapter, "skip_vessels"):
            adapter.skip_vessels = frozenset(skip_vessels)
        print(f"-- {name}")
        try:
            output = adapter.run()
        except FetchBlocked as exc:
            print(f"   blocked: {exc}", file=sys.stderr)
            blocked += 1
            continue

        print(
            f"   {len(output.itineraries)} itineraries, "
            f"{len(output.departures)} departures, {len(output.warnings)} warnings"
        )
        for warning in output.warnings[: args.warnings]:
            print(f"   ! {warning}")
        remaining = len(output.warnings) - args.warnings
        if remaining > 0:
            print(f"   ! ... and {remaining} more")
        combined.extend(output)

    # Before the empty check: a run whose pages all failed has carried rows to
    # publish, and dropping them because "the scrape produced nothing" is the
    # same deletion one level up.
    out = Path(args.out)
    previous = None
    if out.exists():
        try:
            previous = json.loads(out.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = None

    carried, carried_boats, carry_notes = carry_unread(
        previous, combined.unread, date.today()
    )
    if carried:
        seen_deps = {d.get("id") for d in combined.departures}
        combined.departures.extend(d for d in carried if d.get("id") not in seen_deps)
        seen_boats = {i.get("id") for i in combined.itineraries}
        combined.itineraries.extend(
            i for i in carried_boats if i.get("id") not in seen_boats
        )
        combined.warnings.extend(carry_notes)
        print(f"   carried {len(carried)} departure(s) forward from "
              f"{len(carry_notes)} unreadable page(s)")

    if combined.is_empty:
        # No candidate file is written, so an empty scrape leaves nothing for
        # CI to commit. A run that fetched nothing must not look like a quiet
        # day on which nothing changed.
        print("scrape produced no itineraries or departures", file=sys.stderr)
        return 1

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "scraped_at": date.today().isoformat(),
                "itineraries": combined.itineraries,
                "departures": combined.departures,
                "warnings": combined.warnings,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote candidate {out}")

    # Which vessels this run fetched and got nothing from. Only recorded for
    # boats actually visited: a vessel skipped this run keeps its earlier date,
    # so the seven-day clock runs from when it was last really looked at rather
    # than being reset by the skip itself -- otherwise the cache would renew
    # its own verdict forever and the boat would never be checked again.
    visited = {i.get("id") for i in combined.itineraries if i.get("id")}
    selling = {d.get("boat_slug") for d in combined.departures}
    for slug in sorted(visited - selling):
        barren_record[slug] = date.today().isoformat()
    for slug in sorted(visited & selling):
        barren_record.pop(slug, None)
    if barren_record:
        barren_path.parent.mkdir(parents=True, exist_ok=True)
        barren_path.write_text(
            json.dumps(
                {
                    "note": (
                        "Vessels that published no departure when last fetched. "
                        "Skipped for a week to save the requests, then re-checked. "
                        "Delete this file to force a full crawl."
                    ),
                    "vessels": dict(sorted(barren_record.items())),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"wrote {barren_path}: {len(barren_record)} vessel(s) selling nothing")

    # Written beside the candidate rather than inside it: promote reads the
    # candidate on every run and has no use for this, while this exists purely
    # so a later question can be asked of today's data.
    if combined.archive:
        archive = Path(args.archive)
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_text(
            json.dumps(
                {
                    "scraped_at": date.today().isoformat(),
                    "source": "liveaboard.com",
                    "note": (
                        "Structured data exactly as the source published it, "
                        "including fields nothing parses yet. Kept because "
                        "current prices can be re-scraped and past ones cannot."
                    ),
                    "pages": combined.archive,
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        nodes = sum(len(page["nodes"]) for page in combined.archive)
        print(
            f"archived {len(combined.archive)} pages, {nodes} nodes "
            f"-> {archive} ({archive.stat().st_size / 1024:.0f} KB)"
        )

    return 1 if blocked else 0


def _describe_drift(committed: Any, fresh: Any, path: str = "") -> list[str]:
    """Say where two datasets disagree, in terms a reader can act on.

    "The committed dataset differs" is not an actionable message -- the useful
    part is *which* itinerary gained a fee, or that every operator id changed.
    So this walks both structures and names the first handful of differences
    rather than dumping two megabytes of JSON.
    """
    if type(committed) is not type(fresh):
        return [f"{path or '<root>'}: {type(committed).__name__} -> {type(fresh).__name__}"]

    if isinstance(committed, dict):
        out: list[str] = []
        for key in sorted(set(committed) | set(fresh)):
            here = f"{path}.{key}" if path else key
            if key not in committed:
                out.append(f"{here}: added")
            elif key not in fresh:
                out.append(f"{here}: removed")
            else:
                out.extend(_describe_drift(committed[key], fresh[key], here))
            if len(out) > 40:
                break
        return out

    if isinstance(committed, list):
        if len(committed) != len(fresh):
            return [f"{path}: {len(committed)} entries -> {len(fresh)}"]
        out = []
        for index, (a, b) in enumerate(zip(committed, fresh)):
            # Name the entry rather than its index where the data gives it one:
            # "itineraries[142]" sends a reader counting, "itineraries[eagle--
            # north-wrecks]" sends them to the trip.
            tag = b.get("id") if isinstance(b, dict) and b.get("id") else index
            out.extend(_describe_drift(a, b, f"{path}[{tag}]"))
            if len(out) > 40:
                break
        return out

    if committed != fresh:
        return [f"{path}: {committed!r} -> {fresh!r}"]
    return []


def cmd_promote(args: argparse.Namespace) -> int:
    """Turn a scrape candidate into a dataset, refusing to publish a worse one.

    The size check is the guard that matters: a source redesign that halves the
    departure count should stop the pipeline, not quietly shrink the site.

    With ``--check`` nothing is written: the freshly promoted payload is
    compared against what is committed, and a difference is an error. Promotion
    is pure -- candidate, fees, facts and FX in, dataset out, no network -- so
    the two agreeing is exactly the statement "the published dataset is what
    this code produces from the committed inputs". When they disagree, somebody
    changed the parser and the dataset is still the old parser's output.
    """
    candidate = json.loads(Path(args.candidate).read_text(encoding="utf-8"))
    season = (date.fromisoformat(args.season_start), date.fromisoformat(args.season_end))

    # Optional: fees collected by the weekly browser run. Absent is normal on a
    # fresh checkout and must not fail the daily pipeline.
    fees = None
    fee_path = Path(args.fees)
    if fee_path.exists():
        fees = json.loads(fee_path.read_text(encoding="utf-8"))

    # Exchange rates from the ECB, fetched by CI. Absent means the fetch has
    # never run, and promote falls back to a rate it labels as a placeholder --
    # which the page then warns about rather than passing off as sourced.
    fx = None
    fx_path = Path(args.fx)
    if fx_path.exists():
        fx = json.loads(fx_path.read_text(encoding="utf-8"))

    # Hand-read figures for vessels whose extras block names a charge without
    # a number. Same source as the scrape, read where the scrape cannot see.
    facts = None
    facts_path = Path(args.facts)
    if facts_path.exists():
        facts = json.loads(facts_path.read_text(encoding="utf-8"))

    # What the operator says about each trip -- reefs, dive count, group size,
    # entry bar -- read from the itinerary fragment by its own incremental
    # crawl. Absent means nothing has been fetched yet, and every field it
    # fills has a fallback, so the pipeline degrades to what it read before.
    trips = None
    trips_path = Path(args.trips)
    if trips_path.exists():
        trips = json.loads(trips_path.read_text(encoding="utf-8"))

    payload = promote(
        candidate, season=season, fees=fees, fx=fx, facts=facts, trips=trips
    )

    if trips:
        book = {
            itinerary_key(t["boat"], t["name"])
            for t in (trips.get("trips") or {}).values()
            if t.get("boat") and t.get("name")
        }
        read = len(trips.get("trips") or {})
        total = len(payload["itineraries"])
        matched = sum(
            1 for i in payload["itineraries"]
            if itinerary_key(i["boat_id"], i["name"]) in book
        )
        with_dives = sum(1 for i in payload["itineraries"] if i["dives"])
        with_bar = sum(1 for i in payload["itineraries"] if i.get("requirements"))
        print(
            f"  itinerary fragments read for {read} trips from {trips_path}"
            f" (collected {trips.get('collected', 'undated')});"
            f" matched {matched}/{total} itineraries,"
            f" {with_dives} with a dive count, {with_bar} with an entry bar"
        )
        if read and not matched:
            # The book keys on vessel plus trip name. A book full of trips
            # that matches nothing is a rename at the source, not an empty
            # file, and it fails silently by falling back everywhere.
            print(
                f"::warning::{read} trips in {trips_path} matched no itinerary;"
                f" the trip names it was built from no longer exist"
            )

    if facts:
        covered = len(facts.get("vessels") or {})
        print(
            f"  hand-read figures applied for {covered} vessels from {facts_path}"
            f" (read {facts.get('collected', 'undated')})"
        )
        stale = payload.get("facts_superseded") or []
        if stale:
            print(
                f"::warning::{len(stale)} hand-read figure(s) in {facts_path} have been "
                f"overtaken by a fresher scrape and are no longer used; delete them"
            )
            for entry in stale[:10]:
                print(f"    {entry}")

    if fx:
        print(f"  rates from {fx.get('source', 'an unnamed source')}, quoted {fx.get('as_of')}")
    else:
        print(f"  no {fx_path} found; euro figures use a placeholder rate and say so")

    priced = sum(1 for i in payload["itineraries"] if i["fees"])
    if fees:
        # The date matters as much as the count. The fee book wins over this
        # run's own parse -- rightly, a browser sees extras the raw HTML does
        # not -- which also means a fee-parser fix cannot reach the site
        # through this command at all. A daily refresh once ran green and
        # changed nothing while the published page kept four invented charges
        # per vessel, because the book that held them was a week old and
        # silent about it.
        print(
            f"  fees applied to {priced}/{len(payload['itineraries'])} itineraries"
            f" from {fee_path} collected {fees.get('scraped_at', 'unknown date')}"
        )

        # The book stores the disclosure text each parse was made from, so
        # whether it is what today's parser would produce is answerable here,
        # offline. A run that reports drift is a run whose fee parsing did not
        # reach the page: the fix is to re-run fees.yml, not to change this.
        from .scrape.fees import drift

        drifted = drift(fees)
        if drifted:
            print(
                f"::warning::{len(drifted)} vessel(s) in {fee_path} were parsed by an "
                f"older version of scrape/fees.py; re-run fees.yml to apply the change"
            )
            for slug, (gained, lost) in sorted(drifted.items())[:10]:
                print(f"    {slug}: +{gained or '-'} -{lost or '-'}")
    else:
        print(f"  no {fee_path} found; fees will render as unknown")

    incoming = len(payload["departures"])
    if incoming == 0:
        print("candidate contains no departures in the season window", file=sys.stderr)
        return 1

    target = Path(args.out)

    if args.check:
        # Deliberately before the size guard and before validation: this is a
        # question about whether two files agree, and answering "they differ"
        # is useful even on a candidate that would refuse to publish.
        if not target.exists():
            print(f"{target} does not exist; nothing to compare against", file=sys.stderr)
            return 1
        committed = json.loads(target.read_text(encoding="utf-8"))
        if committed == payload:
            print(
                f"{target} matches what promote produces from the committed inputs "
                f"({incoming} departures, {len(payload['itineraries'])} itineraries)"
            )
            return 0
        drift = _describe_drift(committed, payload)
        print(
            f"{target} is not what promote produces from the committed inputs.\n"
            f"  The dataset was built by an older parser. Re-promote and commit "
            f"the result:\n"
            f"    PYTHONPATH=src python3 -m liveaboard.cli promote "
            f"--candidate {args.candidate} --out {target}\n"
            f"  {len(drift)} difference(s), first {min(len(drift), 20)}:",
            file=sys.stderr,
        )
        for line in drift[:20]:
            print(f"    {line}", file=sys.stderr)
        return 1

    if target.exists() and not args.force:
        existing = json.loads(target.read_text(encoding="utf-8"))
        previous = len(existing.get("departures", []))
        floor = int(previous * args.min_ratio)
        if previous and incoming < floor:
            print(
                f"refusing to promote: {incoming} departures against {previous} already "
                f"published (floor {floor}). Re-run, or pass --force if the drop is real.",
                file=sys.stderr,
            )
            return 1

    Dataset.from_dict(payload)  # validates, raising DatasetError on a bad shape

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        f"promoted {target}: {incoming} departures, "
        f"{len(payload['itineraries'])} itineraries, {len(payload['boats'])} boats"
    )
    for skipped in payload.get("promotion_skipped", [])[:10]:
        print(f"  ! {skipped}")
    return 0


def _git_show(revision: str, path: Path) -> dict[str, Any] | None:
    """A committed version of a file, or ``None`` when there is not one.

    The refresh commits the dataset on every run, so the previous state is
    already in the history and nothing extra has to be stored. ``HEAD~1`` is
    the usual argument; a run that is the repository's first commit has no
    predecessor and gets ``None`` rather than an error.
    """
    import subprocess

    try:
        result = subprocess.run(
            ["git", "show", f"{revision}:{path.as_posix()}"],
            capture_output=True, text=True, timeout=60, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        return json.loads(result.stdout)
    except ValueError:
        return None


HISTORY_HEADER = (
    "# What changed\n\n"
    "One entry per refresh, newest first, written by `liveaboard.cli changes`.\n"
    "Do not edit by hand — the next run rewrites the file around this header.\n"
)


def cmd_changes(args: argparse.Namespace) -> int:
    """Report what moved between two datasets."""
    from .changes import compare, headline, render as render_changes

    after = json.loads(Path(args.data).read_text(encoding="utf-8"))

    if args.before is not None:
        before = json.loads(Path(args.before).read_text(encoding="utf-8"))
        before_label = str(args.before)
    else:
        before = _git_show(args.revision, Path(args.data))
        before_label = args.revision
        if before is None:
            # Not a failure. A first run has nothing to compare against, and
            # saying so beats printing an empty report that reads as "nothing
            # changed" when the truth is "nothing to compare".
            print("first dataset; nothing to compare against" if args.headline
                  else f"no earlier {args.data} at {args.revision}; nothing to compare")
            return 0

    report = compare(before, after)
    if args.headline:
        print(headline(report))
        return 0
    text = render_changes(
        report,
        before=before_label,
        after=after.get("generated") or str(args.data),
        limit=args.limit,
    )
    print(text)

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"\nwrote {args.out}")

    if args.append:
        # Newest first, and committed: the workflow run summary vanishes with
        # the run, so the only durable copy is the one in the repository.
        path = Path(args.append)
        path.parent.mkdir(parents=True, exist_ok=True)
        previous = path.read_text(encoding="utf-8") if path.exists() else ""
        body = previous.split(HISTORY_HEADER, 1)[-1].lstrip("\n")
        entry = f"## {after.get('generated') or before_label}\n\n```\n{text}\n```\n"
        path.write_text(f"{HISTORY_HEADER}\n{entry}\n{body}", encoding="utf-8")
        print(f"\nappended to {args.append}")

    # A vessel losing every departure at once is the one finding worth a
    # non-zero exit: it usually means a fetch failed rather than a season
    # ending, and a silent green run would bury it.
    if report.vessels_gone and args.fail_on_missing:
        print(
            f"\n::error::{len(report.vessels_gone)} vessel(s) lost every departure",
            file=sys.stderr,
        )
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="liveaboard", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="render the static site")
    build.add_argument("--data", default=None, type=Path)
    build.add_argument("--out", default=DEFAULT_OUT, type=Path)
    build.set_defaults(func=cmd_build)

    check = sub.add_parser("check", help="validate the dataset and summarise it")
    check.add_argument("--data", default=None, type=Path)
    check.set_defaults(func=cmd_check)

    scrape = sub.add_parser("scrape", help="refresh from the source sites")
    scrape.add_argument("--source", choices=sorted(ADAPTERS), default=None)
    scrape.add_argument("--out", default=Path("data/candidate.json"), type=Path)
    scrape.add_argument("--snapshots", default=DEFAULT_SNAPSHOTS, type=Path)
    scrape.add_argument("--archive", default=Path("data/archive.json"), type=Path)
    scrape.add_argument(
        "--barren", default=Path("data/barren.json"), type=Path,
        help="cache of vessels selling nothing this season; delete to force a full crawl",
    )
    scrape.add_argument(
        "--warnings", type=int, default=10, help="how many warnings to print"
    )
    scrape.add_argument(
        "--max-pages",
        type=int,
        default=0,
        help="cap detail pages fetched per source (0 keeps the adapter default)",
    )
    scrape.add_argument(
        "--diagnose",
        action="store_true",
        help="print each page's structure: JSON-LD types, link shapes, price hints",
    )
    scrape.set_defaults(func=cmd_scrape)

    promote_cmd = sub.add_parser("promote", help="turn a scrape candidate into the dataset")
    promote_cmd.add_argument("--candidate", default=Path("data/candidate.json"), type=Path)
    promote_cmd.add_argument("--out", default=Path("data/egypt-2027.json"), type=Path)
    promote_cmd.add_argument("--fees", default=Path("data/fees.json"), type=Path)
    promote_cmd.add_argument("--fx", default=Path("data/fx.json"), type=Path)
    promote_cmd.add_argument("--facts", default=Path("data/operator_facts.json"), type=Path)
    promote_cmd.add_argument("--trips", default=Path("data/itineraries.json"), type=Path)
    promote_cmd.add_argument("--season-start", default="2027-05-01")
    promote_cmd.add_argument("--season-end", default="2027-08-31")
    promote_cmd.add_argument(
        "--min-ratio",
        type=float,
        default=0.6,
        help="refuse to publish below this fraction of the current departure count",
    )
    promote_cmd.add_argument("--force", action="store_true", help="publish despite a large drop")
    promote_cmd.add_argument(
        "--check",
        action="store_true",
        help="write nothing; fail if --out is not what promote produces",
    )
    promote_cmd.set_defaults(func=cmd_promote)

    changes = sub.add_parser("changes", help="report what moved since the last run")
    changes.add_argument("--data", default=LIVE_DATA, type=Path)
    changes.add_argument(
        "--before", default=None, type=Path,
        help="an earlier dataset file; defaults to reading one out of the git history",
    )
    changes.add_argument(
        "--revision", default="HEAD~1",
        help="which commit to compare against when --before is not given",
    )
    changes.add_argument("--out", default=None, type=Path, help="also write the report here")
    changes.add_argument(
        "--append", default=None, type=Path,
        help="prepend this run's entry to a running history file, newest first",
    )
    changes.add_argument(
        "--headline", action="store_true",
        help="print one line instead of the report, for a commit subject",
    )
    changes.add_argument("--limit", type=int, default=12, help="rows per section")
    changes.add_argument(
        "--fail-on-missing", action="store_true",
        help="exit non-zero when a vessel loses every departure at once",
    )
    changes.set_defaults(func=cmd_changes)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
