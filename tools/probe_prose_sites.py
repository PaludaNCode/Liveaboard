#!/usr/bin/env python3
"""What would reading dive sites out of the operator's prose actually change?

The prose is stored verbatim in ``data/itineraries.json``, so this asks nothing
of liveaboard.com: it is a **wholly offline** probe over the committed book,
and it can be re-run after every change to ``SITE_HINTS`` for free. That is the
point of keeping the words rather than a conclusion drawn from them.

Three questions, in the order they have to be answered:

**What would it add?** Against the regions list already in use, per trip.
Prose that only restates the regions is not worth a third source.

**What would it get wrong?** Free sentences are not a curated list. A reef can
appear because the trip visits it, because the boat passes it, because the
operator is describing what the region contains, or inside "we may not reach".
The regions list has none of those failure modes, so the added sites are
printed to be read rather than counted.

**What is being missed?** Capitalised phrases the vocabulary does not know,
ranked by how many trips write them. This is how ``SITE_HINTS`` grows from what
operators actually say instead of from what seems likely.

    python3 tools/probe_prose_sites.py [--show 25] [--unknown 40]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from liveaboard.promote import (  # noqa: E402
    SITE_ALIASES,
    SITE_HINTS,
    _sites_from_name,
)

BOOK = Path("data/itineraries.json")

# A capitalised run, optionally joined by the small words Egyptian reef names
# use: "Sha'ab el Erg", "Ras Mohammed", "Gota Abu Ramada". Deliberately greedy
# about apostrophes, which the source writes three different ways.
PROPER = re.compile(
    r"\b[A-Z][\w’'`-]+(?:\s+(?:el|al|abu|of|the|and)\s+[A-Z\w’'`-]+|\s+[A-Z][\w’'`-]+)*"
)

# Words that begin a capitalised run without naming a place. Without these the
# unknown list is mostly "The Boat", "Dive 1" and the days of the week.
STOPWORDS = frozenset("""
    a an and the of at on in to for from with by we you our your it its
    day days dive dives diving diver divers night morning afternoon evening
    breakfast lunch dinner snack tea coffee
    monday tuesday wednesday thursday friday saturday sunday
    january february march april may june july august september october
    november december
    boat ship vessel crew guide guides guest guests cabin cabins deck
    arrival departure check checkin check-in embarkation disembarkation
    transfer airport hotel harbour harbor marina port jetty
    briefing safety equipment gear nitrox tank tanks cylinder
    please note important information highlights itinerary sample route
    optional included extra extras approximately up to about around
    we will there is this that these those
    open water advanced nitrox padi ssi certification certified
    am pm h hrs
""".split())

# Whole phrases that are not dive sites however capitalised they look. Kept
# separate from STOPWORDS because these are exact matches, not first words.
NOT_A_SITE = frozenset({
    "red sea", "egypt", "egyptian", "national park", "marine park",
    "sample itinerary", "important information", "dive sites", "daily routine",
    "open water", "advanced open water", "the red sea",
})


def normalise_loose(text: str) -> str:
    """Fold for comparison only: case, punctuation and the three apostrophes."""
    text = text.replace("’", "'").replace("`", "'")
    return " ".join(re.sub(r"[^a-z0-9' ]+", " ", text.lower()).split())


def prose_of(trip: dict) -> str:
    """Everything the operator wrote about this trip, as one string."""
    parts = [trip.get("intro") or ""]
    for section in trip.get("sections") or []:
        parts.append(section.get("heading") or "")
        parts.append(section.get("text") or "")
    return " . ".join(p for p in parts if p)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book", default=BOOK, type=Path)
    parser.add_argument("--show", type=int, default=25,
                        help="trips to print the added sites for")
    parser.add_argument("--unknown", type=int, default=40,
                        help="unrecognised phrases to list")
    args = parser.parse_args()

    if not args.book.exists():
        print(f"no {args.book}", file=sys.stderr)
        return 1
    trips = json.loads(args.book.read_text(encoding="utf-8")).get("trips") or {}
    print(f"{len(trips)} trips in the book\n")

    known = {normalise_loose(h) for h in SITE_HINTS}
    known |= {normalise_loose(a) for a in SITE_ALIASES}
    known |= NOT_A_SITE

    with_prose = 0
    would_add: list[tuple[str, list[str], list[str]]] = []
    added_counts: Counter[str] = Counter()
    unknown: Counter[str] = Counter()
    unknown_example: dict[str, str] = {}
    no_sites_before = no_sites_after = 0

    for key, trip in sorted(trips.items()):
        text = prose_of(trip)
        if text:
            with_prose += 1

        regions = trip.get("regions") or []
        before = _sites_from_name(" , ".join(regions)) if regions else []
        from_prose = _sites_from_name(text) if text else []

        seen = {normalise_loose(s) for s in before}
        added = [s for s in from_prose if normalise_loose(s) not in seen]
        if added:
            would_add.append((key, before, added))
            for site in added:
                added_counts[site] += 1
        if not before:
            no_sites_before += 1
            if not from_prose:
                no_sites_after += 1

        # Capitalised phrases nothing recognises, once per trip: a reef named
        # eight times in one week's prose is still one trip's evidence, and
        # counting mentions would rank a chatty operator over a common reef.
        for phrase in {
            m.group(0) for m in PROPER.finditer(text)
        }:
            folded = normalise_loose(phrase)
            if not folded or folded in known or len(folded) < 4:
                continue
            if folded.split()[0] in STOPWORDS:
                continue
            if any(f" {k} " in f" {folded} " for k in known):
                continue  # a known reef with a word stuck to it
            unknown[folded] += 1
            unknown_example.setdefault(folded, phrase)

    print("================ WHAT PROSE WOULD ADD ================")
    print(f"trips with prose stored     : {with_prose}/{len(trips)}")
    print(f"trips it adds a site to     : {len(would_add)}")
    print(f"trips with no site from the regions list : {no_sites_before}")
    print(f"   ... still none after the prose        : {no_sites_after}")

    print(f"\nmost-added sites:")
    for site, count in added_counts.most_common(20):
        print(f"   {count:4}  {site}")

    print(f"\nfirst {args.show} trips, to be read rather than counted:")
    for key, before, added in would_add[: args.show]:
        print(f"\n   {key}")
        print(f"      regions give : {', '.join(before) or '(nothing)'}")
        print(f"      prose adds   : {', '.join(added)}")

    print(f"\n================ WHAT IT DOES NOT KNOW ================")
    print(f"{len(unknown)} distinct capitalised phrases go unrecognised. "
          f"Top {args.unknown} by trips writing them:")
    for folded, count in unknown.most_common(args.unknown):
        print(f"   {count:4}  {unknown_example[folded]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
