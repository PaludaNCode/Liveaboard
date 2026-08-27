"""Command line entry points.

    python3 -m liveaboard.cli build    # dataset -> static site
    python3 -m liveaboard.cli check    # validate and summarise
    python3 -m liveaboard.cli scrape   # refresh from the source sites
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

from .classify import classify
from .dataset import Dataset, DatasetError
from .pricing import compute, mandatory_known, resolve_fees
from .promote import promote
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
    target = render(dataset, args.out)
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

    print()
    for key, itinerary in dataset.itineraries.items():
        c = classify(itinerary)
        route = c.route.value if c.route else "unclassified"
        print(f"  {itinerary.name[:40]:42} {route:20} {c.level.value}")
    return 0


def cmd_scrape(args: argparse.Namespace) -> int:
    """Run the source adapters and write their raw output.

    Deliberately does not overwrite the live dataset: a scrape writes a
    candidate file, and promoting it is a separate, reviewable step.
    """
    fetcher = PoliteFetcher(snapshot_dir=Path(args.snapshots), diagnose=args.diagnose)
    selected = [args.source] if args.source else list(ADAPTERS)

    combined = ScrapeOutput()
    blocked = 0

    for name in selected:
        adapter_cls = ADAPTERS.get(name)
        if adapter_cls is None:
            print(f"unknown source {name!r}; known: {', '.join(ADAPTERS)}", file=sys.stderr)
            return 2

        adapter = adapter_cls(fetcher)
        if args.max_pages:
            adapter.max_pages = args.max_pages
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

    payload = promote(candidate, season=season, fees=fees, fx=fx, facts=facts)

    if facts:
        covered = len(facts.get("vessels") or {})
        print(f"  hand-read figures applied for {covered} vessels from {facts_path}")

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

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
