#!/usr/bin/env python3
"""Propose divebooker hull -> boat id pairs, on exact name equality and nothing else.

Ten pairs were confirmed by hand. The seller lists about **75** Egyptian
hulls, and doing seventy of those by eye is where a wrong pair gets made — so
the rule that made the ten is written down here instead: **the vessel name
divebooker states, normalised, equals one of ours, normalised.** Nothing
weaker proposes anything. A near miss is printed for a person to read and is
never written, because the names this fleet carries are exactly the kind that
punish a near-miss rule: *Blue*, *Blue Pearl*, *Blue Melody*, *Blue Seas* and
*Blue Storm* are five boats, and *Discovery I* and *Discovery II* are two.

This is the `padi_aliases.json` discipline, not a replacement for it: the file
stays hand-maintained, and what a tool may do is apply one stated rule
repeatably and show its work. A pair it cannot make is a boat a person maps or
leaves unmapped — and unmapped is a safe answer, because `promote` names an
unmapped hull rather than guessing at it.

    python3 tools/match_divebooker.py [--write]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from liveaboard.promote import slugify  # noqa: E402

BOOK = Path("data/divebooker.json")
DATASET = Path("data/egypt-2027.json")
ALIASES = Path("data/divebooker_aliases.json")

#: Words that say what kind of boat it is rather than which boat it is. Only
#: ever stripped when comparing, never when a name is printed or stored.
NOISE = {"my", "mv", "ss", "m", "y", "liveaboard", "liveaboards", "boat"}


def compare(name: str) -> str:
    """The form two names are equal in, or are not."""
    return "-".join(w for w in slugify(name).split("-") if w and w not in NOISE)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book", default=BOOK, type=Path)
    parser.add_argument("--dataset", default=DATASET, type=Path)
    parser.add_argument("--aliases", default=ALIASES, type=Path)
    parser.add_argument("--write", action="store_true",
                        help="merge the exact pairs into the alias file")
    args = parser.parse_args()

    book = json.loads(args.book.read_text(encoding="utf-8"))
    boats = json.loads(args.dataset.read_text(encoding="utf-8"))["boats"]
    alias_file = json.loads(args.aliases.read_text(encoding="utf-8"))
    alias = dict(alias_file.get("aliases") or {})

    ours: dict[str, list[str]] = {}
    for boat in boats:
        for key in (compare(boat["id"]), compare(boat["name"])):
            ours.setdefault(key, []).append(boat["id"])

    proposed: dict[str, str] = {}
    unmatched: list[tuple[str, str]] = []
    for slug, vessel in sorted((book.get("vessels") or {}).items()):
        if slug in alias:
            continue
        name = (vessel.get("name") or "").strip()
        key = compare(name or slug)
        hits = sorted(set(ours.get(key, [])))
        if len(hits) == 1:
            proposed[slug] = hits[0]
            print(f"  = {slug:<30} {name:<34} -> {hits[0]}")
        elif hits:
            # Two of ours normalising to one name is our own ambiguity, and a
            # tool may not pick a side of it.
            print(f"  ? {slug:<30} {name:<34} -> {hits} (ours are ambiguous)")
            unmatched.append((slug, name))
        else:
            unmatched.append((slug, name))

    print(f"\n{len(proposed)} exact pair(s); {len(unmatched)} for a person")
    tokens = {tok: boat["id"] for boat in boats
              for tok in compare(boat["name"]).split("-") if len(tok) > 3}
    for slug, name in unmatched:
        near = sorted({tokens[t] for t in compare(name or slug).split("-")
                       if t in tokens})
        print(f"  ! {slug:<30} {name or 'states no name':<34} "
              f"near: {', '.join(near) if near else 'nothing'}")

    if args.write and proposed:
        alias_file["aliases"] = dict(sorted(alias | proposed).items())
        args.aliases.write_text(json.dumps(alias_file, indent=1,
                                           ensure_ascii=False) + "\n",
                                encoding="utf-8")
        print(f"\n{args.aliases}: {len(alias_file['aliases'])} pair(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
