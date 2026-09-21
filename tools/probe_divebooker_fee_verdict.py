#!/usr/bin/env python3
"""How many of divebooker's bills actually add up, asked of the shipped reader?

`probe_divebooker_fee_shape.py` counted what the fleet *states*, with its own
copy of the reading. This asks the opposite question of the code that ships:
run `divebooker_com.fee_blocks` over every hull and report its own verdict.

The number that matters is `complete`. A bill this project cannot total prints
no third column at all -- `pricing.divebooker_lines` returns `None` unless the
book names, prices and scales every charge a diver cannot decline -- so this is
the count of trips where the third seller reaches the Total, and the reasons
are the count of what it would take to raise it.

Three ways a bill goes incomplete and they want different fixes, so they are
counted apart:

* **unnamed** -- a priced obligatory line whose label `fees.LABEL_PATTERNS`
  declined. The fix is a word, and the labels are printed rather than counted,
  because a number cannot say which word.
* **no figure** -- a charge of unknown size. Nothing to do: the seller did not
  state one and this project does not invent prices.
* **no unit** -- a figure with a payer and no period, *50 USD per person*.
  `FeeItem.span_for_trip` refuses it, so the line is kept, marked and never
  totalled. Also nothing to do, and the reason this census exists: it is the
  one that could plausibly be most of the fleet, and nobody has counted it
  with the reader that decides it.

Also prints what the trip object states beside each panel -- the dive count,
the entry bar, the reefs and the two harbours -- since those are read from the
same object and a silence there is invisible on the page.

Writes nothing. ~92 requests at the five-second pace, about eight minutes.

    python3 tools/probe_divebooker_fee_verdict.py [--limit 0]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from liveaboard.scrape import divebooker_com as db  # noqa: E402
from liveaboard.scrape.base import FetchBlocked, PoliteFetcher  # noqa: E402
from liveaboard.promote import _port  # noqa: E402
from liveaboard.taxonomy import FeeTier  # noqa: E402
from probe_divebooker import repair_robots  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book", default=Path("data/divebooker.json"), type=Path)
    parser.add_argument("--limit", type=int, default=0, help="hulls to read, 0 for all")
    parser.add_argument("--sample", type=int, default=30,
                        help="unnamed priced labels to print verbatim")
    parser.add_argument("--delay", type=float, default=5.0)
    parser.add_argument("--snapshots", default=Path("data/snapshots"), type=Path)
    args = parser.parse_args()

    vessels = json.loads(args.book.read_text(encoding="utf-8")).get("vessels") or {}
    slugs = sorted(vessels)
    if args.limit:
        slugs = slugs[: args.limit]

    fetcher = PoliteFetcher(snapshot_dir=args.snapshots, delay=args.delay)
    for host in (db.HOST, f"www.{db.HOST}"):
        repair_robots(fetcher, host)

    pages = 0
    blocks = complete = 0
    no_owed = 0
    why: Counter[str] = Counter()
    unnamed: Counter[str] = Counter()
    states: Counter[str] = Counter()
    harbours: Counter[str] = Counter()
    owed_per_block: Counter[int] = Counter()
    warnings: list[str] = []

    for slug in slugs:
        record = vessels[slug]
        if not record.get("divebooker_id"):
            continue
        path = f"/{slug}-{record['divebooker_id']}"
        try:
            result = fetcher.get(f"https://{db.HOST}{path}")
        except FetchBlocked as exc:
            print(f"BLOCKED {path}: {exc}", flush=True)
            continue
        except Exception as exc:  # noqa: BLE001 - one dead hull must not end a census
            print(f"FAILED  {path}: {type(exc).__name__}: {exc}", flush=True)
            continue
        pages += 1
        found, notes = db.fee_blocks(result.body)
        warnings.extend(f"{path}: {note}" for note in notes)

        good = 0
        for block in found:
            blocks += 1
            owed = [fee for fee in block.fees
                    if fee.tier is FeeTier.MANDATORY and not fee.included]
            owed_per_block[len(owed)] += 1
            if block.complete:
                complete += 1
                good += 1
                if not owed:
                    # The seller saying the fare covers everything, which is an
                    # answer and not a gap -- but it is a different answer from
                    # a priced bill and must not be read as one.
                    no_owed += 1
            else:
                if block.unnamed:
                    why["a label nothing could name"] += 1
                if any(not fee.has_price for fee in owed):
                    why["a charge with no figure"] += 1
                if any(fee.unit_unstated for fee in owed):
                    why["a figure with no unit"] += 1
            for line in block.unnamed:
                # The hull too, because the fix for a name is sometimes a
                # judgement about *which* charge it is, and that is decided by
                # what else the same operator bills on the same panel. Reading
                # "Government fees" off a census with no boat beside it is how
                # a code gets guessed.
                unnamed[f"{slug}  {line.strip()[:64]}"] += 1
            for harbour in (block.port_from, block.port_to):
                if harbour:
                    # Verbatim, because the question is whether `_port` folds
                    # them onto harbours the fleet already names. A count says
                    # a port was stated; only the spelling says whether the
                    # *Departs from* bank grows a chip for one seller's wording.
                    harbours[harbour] += 1
            for name, value in (("trip named", block.trip), ("nights", block.nights),
                                ("dives", block.dives), ("entry bar", block.requirements),
                                ("certification", block.certification),
                                ("reefs", block.sites or None),
                                ("port_from", block.port_from),
                                ("port_to", block.port_to)):
                if value:
                    states[name] += 1
        print(f"  {path:<44} {len(found)} block(s), {good} complete", flush=True)

    print(f"\n== {pages} page(s), {blocks} panel(s) ==")
    if blocks:
        print(f"  the bill adds up      : {complete}  ({complete / blocks:.0%})")
        print(f"     of which state no obligatory charge at all: {no_owed}")
        print(f"  it does not           : {blocks - complete}")
    print("\n== why not (a panel can fail more than one way) ==")
    for reason, count in why.most_common():
        print(f"  {count:>5}  {reason}")
    print(f"\n  obligatory lines per panel: {dict(sorted(owed_per_block.items()))}")

    print(f"\n== what the trip states beside its panel, of {blocks} ==")
    for name, count in states.most_common():
        print(f"  {count:>5}  {name}")

    # `FeeBlock.unnamed` spans every column, so this is wider than the set
    # that made a bill incomplete: a course nobody can name in *Extra cost*
    # says nothing about what a diver must pay. Both are worth a word.
    print(f"\n== harbours stated, verbatim, and what `_port` folds each onto ==")
    for harbour, count in harbours.most_common():
        folded = _port(harbour)
        mark = "" if folded == harbour else f"  ->  {folded}"
        print(f"  {count:>5}  {harbour}{mark}")

    print(f"\n== priced labels this project's vocabulary declined "
          f"({sum(unnamed.values())} line(s), {len(unnamed)} spelling(s)) ==")
    for label, count in unnamed.most_common(args.sample):
        print(f"  {count:>4}  {label}")

    # And what each hull that still cannot total states in full, so the next
    # word added to the vocabulary is decided against a whole panel rather
    # than against one line of it.
    owed_only = Counter({k: v for k, v in unnamed.items() if "[owed]" in k})
    print(f"\n== of those, the obligatory ones "
          f"({sum(owed_only.values())} line(s), {len(owed_only)} spelling(s)) ==")
    for label, count in owed_only.most_common(args.sample):
        print(f"  {count:>4}  {label}")

    if warnings:
        print(f"\n== {len(warnings)} warning(s) ==")
        for note in warnings[:20]:
            print(f"  {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
