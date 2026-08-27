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
from .pricing import compute, transparency_score
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

    rows: list[tuple[str, float, float, float, float]] = []
    for key, itinerary in dataset.itineraries.items():
        departures = [d for d in dataset.departures if d.itinerary_id == key]
        if not departures:
            continue
        first = departures[0]
        breakdown = compute(itinerary, first, dataset.fx)
        rows.append(
            (
                itinerary.name,
                float(breakdown.base.rounded),
                float(breakdown.total.rounded),
                breakdown.markup_pct,
                transparency_score(itinerary, first, dataset.fx) * 100,
            )
        )

    width = max(len(r[0]) for r in rows) if rows else 20
    print(f"  {'itinerary'.ljust(width)}  {'advertised':>11} {'true cost':>10} {'markup':>8} {'honesty':>8}")
    for name, base, total, markup, honesty in sorted(rows, key=lambda r: -r[3]):
        print(f"  {name.ljust(width)}  {base:>11,.0f} {total:>10,.0f} {markup:>7.0f}% {honesty:>7.0f}%")

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
    fetcher = PoliteFetcher(snapshot_dir=Path(args.snapshots))
    selected = [args.source] if args.source else list(ADAPTERS)

    combined = ScrapeOutput()
    failures = 0

    for name in selected:
        adapter_cls = ADAPTERS.get(name)
        if adapter_cls is None:
            print(f"unknown source {name!r}; known: {', '.join(ADAPTERS)}", file=sys.stderr)
            return 2

        adapter = adapter_cls(fetcher)
        print(f"-- {name}")
        try:
            output = adapter.run()
        except FetchBlocked as exc:
            print(f"   blocked: {exc}", file=sys.stderr)
            failures += 1
            continue

        print(
            f"   {len(output.itineraries)} itineraries, "
            f"{len(output.departures)} departures, {len(output.warnings)} warnings"
        )
        for warning in output.warnings[: args.limit or 5]:
            print(f"   ! {warning}")
        combined.extend(output)

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
    return 1 if failures and combined.is_empty else 0


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
    scrape.add_argument("--limit", type=int, default=0, help="cap warnings printed")
    scrape.set_defaults(func=cmd_scrape)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
