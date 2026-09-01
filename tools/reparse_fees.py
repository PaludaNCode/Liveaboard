#!/usr/bin/env python3
"""Re-read the committed fee book from the disclosure text it was made from.

``tools/scrape_fees.py`` drives a browser at 79 vessel pages and stores, beside
every parse, **the text it parsed** -- `data/fees.json`'s ``disclosure`` block,
kept for exactly this. So a change to `scrape/fees.py` reaches the committed
book without a browser and without asking liveaboard.com for anything: the same
bargain ``tools/reparse_candidate.py`` makes for the departure archive, and for
the same reason. Re-crawling 79 pages to re-read data already in the repository
is both slow and rude.

What it is for is the case where a *classification* changed rather than a
price: a fee code that used to decline, a tier that used to be decided one way.
Those are invisible until the book is rebuilt, and a parser fix that never
reaches `data/` is a green build and a site that is still slightly wrong.

**The gear line is carried, never re-derived.** It comes from the ``#modal-gear``
dialog rather than the disclosure, and the dialog's markup is not stored -- only
its rendered lines. `scrape_fees.py` lets that line replace the extras block's
blank one, so this keeps whatever gear-coded entry the book already holds and
re-derives everything else around it.

Writes nothing unless something changed, and prints every change it makes.

    python3 tools/reparse_fees.py [--check] [--fees data/fees.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from liveaboard.scrape.fees import parse_extras, to_fee_dicts  # noqa: E402
from liveaboard.taxonomy import FeeCode  # noqa: E402

BLOCK_ORDER = ("included", "required", "optional")

CARRIED = frozenset({FeeCode.GEAR_RENTAL.value})
"""Codes whose line did not come from the disclosure and cannot be re-derived.

The gear bundle is read from the vessel's gear dialog, whose markup this file
does not hold. Dropping it on a re-parse would take a priced extra off every
vessel that rents kit -- a silent loss, and the loudest kind of wrong this
project has.
"""


def disclosure_text(blocks: dict[str, Any]) -> str:
    """The three stored blocks, back in the shape ``parse_extras`` reads.

    ``extras_excerpt`` split one paragraph into three on the headings; this
    puts the headings back. Order matters only in that ``Included`` is sorted
    last inside the parser, which does it for itself.
    """
    parts = [f"{name.title()}: {blocks[name]}" for name in BLOCK_ORDER
             if (blocks.get(name) or "").strip()]
    return "\n".join(parts)


def reparse(entry: dict[str, Any]) -> list[dict[str, Any]] | None:
    """This vessel's fee lines, re-derived. ``None`` when it holds no text."""
    blocks = entry.get("disclosure")
    if not isinstance(blocks, dict):
        return None
    text = disclosure_text(blocks)
    if not text.strip():
        return None

    provenance = next(
        (f["provenance"] for f in entry.get("fees") or [] if f.get("provenance")),
        None,
    )
    if provenance is None:
        return None

    lines = to_fee_dicts(parse_extras(text), provenance)
    carried = [f for f in entry.get("fees") or [] if f.get("code") in CARRIED]
    kept = {f["code"] for f in carried}
    return [f for f in lines if f["code"] not in kept] + carried


def summarise(before: list[dict], after: list[dict]) -> list[str]:
    """What moved, one line each, in the vocabulary the fee table prints."""
    was = {f["code"]: f for f in before}
    now = {f["code"]: f for f in after}
    out = []
    for code in sorted(set(was) | set(now)):
        old, new = was.get(code), now.get(code)
        if old is None:
            out.append(f"+ {code} ({new['tier']})")
        elif new is None:
            out.append(f"- {code} ({old['tier']})")
        elif old.get("tier") != new.get("tier"):
            out.append(f"~ {code}: {old['tier']} -> {new['tier']}")
        elif old.get("amount") != new.get("amount"):
            out.append(f"~ {code}: {old.get('amount')} -> {new.get('amount')}")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fees", default=Path("data/fees.json"), type=Path)
    parser.add_argument(
        "--check", action="store_true",
        help="report what would change and write nothing",
    )
    args = parser.parse_args()

    book = json.loads(args.fees.read_text(encoding="utf-8"))
    vessels = book.get("vessels") or {}

    changed = 0
    for slug, entry in sorted(vessels.items()):
        lines = reparse(entry)
        if lines is None:
            continue
        moves = summarise(entry.get("fees") or [], lines)
        if not moves:
            continue
        changed += 1
        print(f"  {slug}")
        for move in moves:
            print(f"      {move}")
        if not args.check:
            entry["fees"] = lines

    if not changed:
        print(f"{args.fees} already matches the parser")
        return 0
    if args.check:
        print(f"\n{changed} vessel(s) would change; run without --check to write")
        return 1

    args.fees.write_text(
        json.dumps(book, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"\nrewrote {args.fees}: {changed} of {len(vessels)} vessels re-parsed")
    print("re-run `promote` -- the dataset is built from this file, not from it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
