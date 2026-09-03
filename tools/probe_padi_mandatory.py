#!/usr/bin/env python3
"""Whether two of PADI's mandatory entries can be the same money twice.

Serenity's double-counted nitrox was ours to fix -- `padi_lines` added the
vessel's gangway charges to PADI's whole book, both copies. Seawolf Dominator
is a different shape and not ours at all: PADI publishes **two mandatory
entries whose text overlaps**, and we read both.

    mandatoryOnBoard   Visa fees                                          250
    mandatoryOnBoard   Visa, dive permit, taxes, marine park fees,
                       harbour fee and fuel surcharges                225-255

The second names the first. Six itineraries, 17 departures, and the vessel has
no liveaboard.com fee panel, so PADI's book is the only fee book those rows
have -- the total they show is inflated by whichever of the two is the
duplicate.

**What this probe settled, and what it did not.**

1. Both entries are genuinely in `mandatoryOnBoard`. Distinct catalogue items:
   `extraId` 36579 against 94745, `kind` 580 against 600, `section` 30 against
   10. Nothing here is a parser reading one disclosure twice.
2. Neither states `validFrom` or `validTo`, so the rule that resolves PADI's
   repricing pairs -- one entry valid in the published season, one not -- has
   nothing to work with.
3. **`kind` and `section` do not encode "package" against "component".** That
   was the obvious hypothesis and it is wrong: Galaxy publishes a package at
   `section` 30 (*Port fees, fuel surcharges and VAT*, 200) and Amelie
   publishes three bare components there. A negative result, written down so
   the next person does not follow it.
4. What the entry fields leave is the figure. **Every other boat that prices a
   visa prices it at 25-30** -- Blue 30, Freedom III 25 -- which is what an
   Egyptian visa costs. Seawolf's is 250, ten times that and inside the same
   range as its own package. On that alone the likeliest reading is an operator
   entering a package total against a visa-titled catalogue item, and it is
   only a reading: the first answer taken here was to withhold the sum.

**What settled it, and where to look next time.** The entries are not the whole
disclosure. Two more readings of the same payload answer what they could not:

5. `--prose` reads the operator's own `whatsNotIncluded`, which itemises the
   bundle: *"Fee for marina Marsa Ghaleb 25 Euro ... Fuel surcharge: 30 Euro
   per person ... Visa, dive permission and taxes 43 Euro ... Fee for marine
   parks: South: 80 Euro"* -- on 10 of the vessel's 13 itineraries, summing to
   the bundle's own 180-255, pricing the visa *inside* the 43, and stating
   nothing at 250 on any of the 13.
6. `--fleet` asks the operator's other hulls. Seawolf Steel publishes the same
   bundle at the same figures under the same `kind`/`section`, and prices *Visa
   fees* at 30 as an **optional** extra. Same company, same seller, same season.

So the bundle is the money and the standalone entry is a copy of part of it.
Still no line is dropped -- `pricing.subsumed_charges` marks the component
`subsumed_by` the bundle, the panel prints it at 250 and names what covers it,
and only the arithmetic changes. A published charge is not ours to delete;
which of two entries carries the money is a question the source can answer, and
the lesson for the next pair is to read the prose and the sister ship before
concluding that nothing separates them.

Writes nothing. One request per trip.

    python3 tools/probe_padi_mandatory.py                    # the whole book
    python3 tools/probe_padi_mandatory.py --boats seawolf-dominator
    python3 tools/probe_padi_mandatory.py --enums            # kind/section map
    python3 tools/probe_padi_mandatory.py --prose --boats seawolf-dominator
    python3 tools/probe_padi_mandatory.py --fleet --boats seawolf-steel
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sys
import textwrap
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from liveaboard.scrape.padi_com import ITINERARY_DETAIL  # noqa: E402

UA = {"User-Agent": "Mozilla/5.0 (compatible; liveaboard-import/1.0; "
                    "+price-transparency research)"}
BOOK = Path("data/padi.json")

#: A word in a bundle's title, and the charge it would also be on its own.
COMPONENT = {
    "visa": "visa", "park": "park", "marine": "park", "harbour": "harbour",
    "harbor": "harbour", "port": "harbour", "fuel": "fuel",
    "environment": "environment", "coast": "coast guard", "service": "service",
}


def get(url: str) -> dict | None:
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, headers=UA), timeout=35
        ) as response:
            return json.load(response)
    except Exception as exc:  # pragma: no cover - a probe reports and moves on
        print(f"  ! {exc}")
        return None


#: Where a seller states a charge a diver can decline. `--fleet` reads these as
#: well as the mandatory block, because the sister ship's answer to "what does
#: this operator charge for a visa" was filed under the first of them.
OPTIONAL_BLOCKS = (
    "optionalOnBoard", "optionalInAdvance",
    "optionalBookableAdvancePaidOnBoard",
)


def names(title: str) -> set[str]:
    """The components a title mentions, by this probe's own vocabulary."""
    low = (title or "").lower()
    return {COMPONENT[word] for word in COMPONENT if word in low}


def plain(html: str | None) -> str:
    """The operator's own prose, as prose.

    Tags stripped and entities unwound only far enough to read money out of
    it by eye. Nothing here parses a figure into the dataset: the whole reason
    this text is worth a flag is that a *person* reads it and decides, and a
    parser over somebody's paragraph would be the joined-string mistake in a
    new costume.
    """
    text = re.sub(r"<[^>]+>", " ", html or "")
    for entity, char in (("&nbsp;", " "), ("&euro;", "EUR"), ("&ndash;", "-"),
                         ("&amp;", "&"), ("&quot;", '"'), ("&#39;", "'")):
        text = text.replace(entity, char)
    return re.sub(r"\s+", " ", text).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--boats", help="comma-separated boat ids of ours")
    parser.add_argument("--limit", type=int, help="stop after this many trips")
    parser.add_argument("--enums", action="store_true",
                        help="tabulate kind/section against titles instead")
    parser.add_argument("--prose", action="store_true",
                        help="print the operator's own whatsNotIncluded text, "
                             "and whether a standalone figure appears in it")
    parser.add_argument("--fleet", action="store_true",
                        help="every block a vessel prices a bundle component "
                             "in, optional ones included -- what a sister ship "
                             "charges is the answer the entries do not carry")
    parser.add_argument("--delay", type=float, default=0.25)
    args = parser.parse_args()

    if not BOOK.exists():
        print(f"{BOOK} is not here; run tools/fetch_padi.py first")
        return 1
    book = json.loads(BOOK.read_text(encoding="utf-8"))
    vessels, trips = book.get("vessels") or {}, book.get("trips") or {}
    wanted = set(args.boats.split(",")) if args.boats else None

    enums: dict[tuple, collections.Counter] = collections.defaultdict(collections.Counter)
    visas: list[tuple[str, float | None]] = []
    overlaps: list[tuple[str, str, str, float | None, str, float | None]] = []
    prose: list[tuple[str, str, list[str], list[float]]] = []
    fleet: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    seen = 0

    for key, trip in trips.items():
        boat = trip.get("boat")
        if wanted is not None and boat not in wanted:
            continue
        vessel = vessels.get(boat) or {}
        detail = get(ITINERARY_DETAIL.format(
            country=vessel.get("country") or "egypt",
            vessel=vessel.get("slug") or boat,
            slug=trip.get("padi_slug"),
        ))
        time.sleep(args.delay)
        if not detail:
            continue
        seen += 1

        entries = detail.get("mandatoryOnBoard") or []
        for entry in entries:
            title, price = entry.get("title") or "", entry.get("price")
            enums[(entry.get("kind"), entry.get("section"))][title] += 1
            if "visa" in title.lower() and len(names(title)) == 1:
                visas.append((boat, price))

        # A bundle naming a component that is also its own priced entry.
        priced = {}
        for entry in entries:
            got = names(entry.get("title") or "")
            if len(got) == 1 and entry.get("price") is not None:
                priced[next(iter(got))] = (entry.get("title"), entry.get("price"))
        for entry in entries:
            title, price = entry.get("title") or "", entry.get("price")
            if price is None or len(names(title)) < 2:
                continue
            for component in sorted(names(title) & set(priced)):
                other, amount = priced[component]
                overlaps.append((boat, key, title, price, other, amount))

        # The operator's own account of the same money, and whether the
        # standalone figure turns up anywhere in it. An absence is the finding
        # here: a charge the operator itemises without ever writing 250 is a
        # charge the 250 entry is not.
        if args.prose:
            text = plain(detail.get("whatsNotIncluded"))
            # Wrapped rather than split into sentences: the operator writes
            # this as one run of clauses with barely a full stop in it, so a
            # sentence split returns the whole paragraph and truncating it
            # cuts off the clause worth reading.
            said = textwrap.wrap(text, 96)
            missing = sorted({
                amount for _, amount in priced.values()
                if amount is not None
                and f"{amount:g}" not in text.replace(",", "")
            })
            prose.append((boat, key, said, missing))

        # What every block this vessel uses says about a bundle component --
        # the mandatory one and the three optional ones, because an operator
        # answering "what is your visa worth" answered in an optional block.
        if args.fleet:
            for block in ("mandatoryOnBoard", *OPTIONAL_BLOCKS):
                for entry in detail.get(block) or []:
                    if not names(entry.get("title") or ""):
                        continue
                    fleet[boat][(
                        block, entry.get("title") or "", entry.get("price"),
                        entry.get("kind"), entry.get("section"),
                    )] += 1

        if args.limit and seen >= args.limit:
            break

    print(f"trips read: {seen}")

    if args.enums:
        print("\nkind/section against the titles that carry them:")
        for (kind, section), titles in sorted(
            enums.items(), key=lambda kv: -sum(kv[1].values())
        ):
            print(f"\n  kind={kind} section={section} -> {sum(titles.values())} entries")
            for title, count in titles.most_common(6):
                print(f"      {count:>3}  {title}")
        return 0

    if args.prose:
        print("\nthe operator's own whatsNotIncluded, per trip:")
        for boat, key, said, missing in prose:
            print(f"\n  {boat}  {key.split('::')[-1][:52]}")
            for line in said[:14]:
                print(f"      {line}")
            if missing:
                print("      -> states none of its own standalone figures: "
                      + ", ".join(f"{amount:g}" for amount in missing))
        return 0

    if args.fleet:
        print("\nevery block that prices a bundle component, per vessel:")
        for boat, rows in sorted(fleet.items()):
            print(f"\n  {boat}")
            for (block, title, price, kind, section), count in sorted(
                rows.items(), key=lambda kv: (kv[0][0], kv[0][1])
            ):
                print(f"      {block:<36} kind={kind} sec={section} "
                      f"{str(price):>7}  x{count:<3} {title[:60]}")
        return 0

    print("\nwhat a visa costs where PADI prices one on its own:")
    for boat, price in sorted(set(visas), key=lambda row: (row[1] is None, row[1] or 0)):
        print(f"   {boat:<24} {price}")

    print(f"\nbundles naming a charge that is also priced on its own: {len(overlaps)}")
    for boat, key, title, price, other, amount in overlaps:
        print(f"   {boat}  {key.split('::')[-1][:44]}")
        print(f"        {price:>8}  {title[:70]}")
        print(f"        {amount:>8}  {other}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
