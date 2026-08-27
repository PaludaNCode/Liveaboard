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

from .classify import classify
from .dataset import Dataset, DatasetError
from .pricing import compute, resolve_fees, transparency_score
from .promote import promote
from .render import render
from .scrape.base import FetchBlocked, PoliteFetcher, ScrapeOutput
from .scrape.liveaboard_com import LiveaboardComAdapter
from .scrape.padi_com import PadiComAdapter

DEFAULT_DATA = Path("data/seed/egypt-2027.json")
DEFAULT_OUT = Path("site")
DEFAULT_SNAPSHOTS = Path("data/snapshots")

ADAPTERS = {
    "liveaboard.com": LiveaboardComAdapter,
    "padi.com": PadiComAdapter,
}


def cmd_build(args: argparse.Namespace) -> int:
    dataset = Dataset.load(args.data)
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
    try:
        dataset = Dataset.load(args.data)
    except DatasetError as exc:
        print(f"dataset invalid: {exc}", file=sys.stderr)
        return 1

    print(f"{args.data}: valid")
    print(f"  sources: {', '.join(sorted(k.value for k in dataset.source_kinds))}")
    print()

    rows: list[tuple[str, float, float, float | None, float | None]] = []
    unknown = 0
    for key, itinerary in dataset.itineraries.items():
        departures = [d for d in dataset.departures if d.itinerary_id == key]
        if not departures:
            continue
        first = departures[0]
        breakdown = compute(itinerary, first, dataset.fx)
        # Same rule as the rendered page: no fee lines means nobody has looked,
        # not that the advertised price is the whole bill.
        known = bool(resolve_fees(itinerary, first))
        if not known:
            unknown += 1
        rows.append(
            (
                itinerary.name,
                float(breakdown.base.rounded),
                float(breakdown.total.rounded),
                breakdown.markup_pct if known else None,
                transparency_score(itinerary, first, dataset.fx) * 100 if known else None,
            )
        )

    width = min(max((len(r[0]) for r in rows), default=20), 44)
    print(f"  {'itinerary'.ljust(width)}  {'advertised':>11} {'true cost':>10} {'markup':>8} {'honesty':>8}")
    for name, base, total, markup, honesty in sorted(rows, key=lambda r: -(r[3] or -1)):
        if markup is None:
            print(f"  {name[:width].ljust(width)}  {base:>11,.0f} {'unknown':>10} {'—':>8} {'—':>8}")
        else:
            print(
                f"  {name[:width].ljust(width)}  {base:>11,.0f} {total:>10,.0f} "
                f"{markup:>7.0f}% {honesty:>7.0f}%"
            )

    if unknown:
        print(f"\n  {unknown} of {len(rows)} itineraries have no fee data captured yet")

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
    return 1 if blocked else 0


def cmd_promote(args: argparse.Namespace) -> int:
    """Turn a scrape candidate into a dataset, refusing to publish a worse one.

    The size check is the guard that matters: a source redesign that halves the
    departure count should stop the pipeline, not quietly shrink the site.
    """
    candidate = json.loads(Path(args.candidate).read_text(encoding="utf-8"))
    season = (date.fromisoformat(args.season_start), date.fromisoformat(args.season_end))

    # Optional: fees collected by the weekly browser run. Absent is normal on a
    # fresh checkout and must not fail the daily pipeline.
    fees = None
    fee_path = Path(args.fees)
    if fee_path.exists():
        fees = json.loads(fee_path.read_text(encoding="utf-8"))

    payload = promote(candidate, season=season, fees=fees)

    priced = sum(1 for i in payload["itineraries"] if i["fees"])
    if fees:
        print(f"  fees applied to {priced}/{len(payload['itineraries'])} itineraries")
    else:
        print(f"  no {fee_path} found; fees will render as unknown")

    incoming = len(payload["departures"])
    if incoming == 0:
        print("candidate contains no departures in the season window", file=sys.stderr)
        return 1

    target = Path(args.out)
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
    build.add_argument("--data", default=DEFAULT_DATA, type=Path)
    build.add_argument("--out", default=DEFAULT_OUT, type=Path)
    build.set_defaults(func=cmd_build)

    check = sub.add_parser("check", help="validate the dataset and summarise it")
    check.add_argument("--data", default=DEFAULT_DATA, type=Path)
    check.set_defaults(func=cmd_check)

    scrape = sub.add_parser("scrape", help="refresh from the source sites")
    scrape.add_argument("--source", choices=sorted(ADAPTERS), default=None)
    scrape.add_argument("--out", default=Path("data/candidate.json"), type=Path)
    scrape.add_argument("--snapshots", default=DEFAULT_SNAPSHOTS, type=Path)
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
    promote_cmd.add_argument("--season-start", default="2027-05-01")
    promote_cmd.add_argument("--season-end", default="2027-08-31")
    promote_cmd.add_argument(
        "--min-ratio",
        type=float,
        default=0.6,
        help="refuse to publish below this fraction of the current departure count",
    )
    promote_cmd.add_argument("--force", action="store_true", help="publish despite a large drop")
    promote_cmd.set_defaults(func=cmd_promote)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
