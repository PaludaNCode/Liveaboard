#!/usr/bin/env python3
"""What PADI's itinerary payload says about money that the parser was not reading.

Three findings, all from the same endpoint the fee book already comes from, and
all of them a field nobody had opened.

**`price` is null and `extraValue` states the figure anyway.** Bella 2's Coast
Guard Fee is `price: null, extraValue: "5 EUR"` and its Service fees `"10 EUR"`
-- two of the three mandatory charges on every trip that boat sells, read as
unpriced, on a vessel whose PADI book is the only fee book this site has. And
`price` must still win: where the two disagree it is a repricing the string did
not follow, so this prints every disagreement rather than a count of them.

**The optional lists were read by nothing.** `optionalOnBoard`,
`optionalInAdvance` and `optionalBookableAdvancePaidOnBoard` hold nitrox and
gear hire, which are the two extras this site puts a toggle on.

**Two traps in those lists.** "PADI Enriched Air Diver (Nitrox)" is a
certification that matched the nitrox pattern -- a course priced as the gas --
and "Full scuba set" is the bundle row, carrying `fullSetDescription` with its
contents.

Writes nothing. One request per trip, so the default is the whole book and
`--limit` is how to try it on a few. Trips come from `data/padi.json`, which
means this reads what the committed book was built from rather than a guess at
a slug.

    python3 tools/probe_padi_extras.py --limit 20
    python3 tools/probe_padi_extras.py --boats bella-2,blue-horizon
    python3 tools/probe_padi_extras.py --summary
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from liveaboard.scrape.fees import classify_label  # noqa: E402
from liveaboard.scrape.padi_com import (  # noqa: E402
    ITINERARY_DETAIL,
    MANDATORY_FIELDS,
    OPTIONAL_FIELDS,
    PARENTHETICAL,
    SEASON,
    PadiComAdapter,
    _money,
)

UA = {"User-Agent": "Mozilla/5.0 (compatible; liveaboard-import/1.0; +price-transparency research)"}

BOOK = Path("data/padi.json")


def get(url: str) -> dict | None:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:
            return json.load(r)
    except Exception as exc:  # noqa: BLE001 - one bad page must not end the probe
        print(f"  FAILED {url}: {exc}", file=sys.stderr)
        return None


def money_fields(entry: dict, currency: str, counts: Counter, disagreements: list) -> str:
    """Report what the two money fields say, and whether they agree."""
    price, raw = entry.get("price"), entry.get("extraValue")
    stated = isinstance(price, (int, float))
    string = isinstance(raw, str) and raw.strip() != ""
    if stated and string and str(raw).strip() not in (str(price), f"{price:g}", f"{price:.2f}"):
        counts["price and extraValue DISAGREE"] += 1
        disagreements.append((entry.get("title"), price, raw, entry.get("payedPer")))
    elif not stated:
        counts["price null, extraValue answers" if _money(entry, currency)
               else "price null, extraValue answers nothing either"] += 1
    return f"price={price!r:<8} extraValue={raw!r}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--boats", help="comma-separated boat ids of ours")
    parser.add_argument("--limit", type=int, help="stop after this many trips")
    parser.add_argument("--summary", action="store_true", help="counts only")
    parser.add_argument("--delay", type=float, default=0.25)
    parser.add_argument("--season-start", default=SEASON[0])
    parser.add_argument("--season-end", default=SEASON[1])
    args = parser.parse_args()

    if not BOOK.exists():
        print(f"{BOOK} not found; it is where the trip slugs come from", file=sys.stderr)
        return 1
    book = json.loads(BOOK.read_text())
    vessels, trips = book.get("vessels") or {}, book.get("trips") or {}
    wanted = set(args.boats.split(",")) if args.boats else None

    season = (args.season_start, args.season_end)
    counts: Counter = Counter()
    disagreements: list = []
    titles: Counter = Counter()
    read = 0

    for key, trip in sorted(trips.items()):
        if wanted is not None and trip.get("boat") not in wanted:
            continue
        if args.limit and read >= args.limit:
            break
        vessel = vessels.get(trip.get("boat")) or {}
        currency = vessel.get("currency") or "EUR"
        detail = get(ITINERARY_DETAIL.format(
            country=vessel.get("country") or "egypt",
            vessel=vessel.get("slug") or trip.get("boat"),
            slug=trip.get("padi_slug"),
        ))
        time.sleep(args.delay)
        if not detail:
            continue
        read += 1

        if not args.summary:
            print(f"\n=== {key}")
            print(f"  dives {detail.get('totalNumberOfDives')}"
                  f"-{detail.get('totalNumberOfDivesMax')}"
                  f"   logged-dive bar {detail.get('minimalNumberOfDives')}")

        for field in MANDATORY_FIELDS + OPTIONAL_FIELDS:
            for entry in detail.get(field) or []:
                if not isinstance(entry, dict):
                    continue
                title = str(entry.get("title") or "").strip()
                titles[(field, title)] += 1
                money = money_fields(entry, currency, counts, disagreements)
                optional = field in OPTIONAL_FIELDS
                code = classify_label(
                    PARENTHETICAL.sub("", title).strip() if optional else title, prose=False)
                basis = PadiComAdapter.basis_for(entry.get("payedPer"))
                counts[(field, code.value if code else "UNCLASSIFIED",
                        basis.value if basis else "BASIS WILL NOT NORMALISE")] += 1
                if not args.summary:
                    contents = str(entry.get("fullSetDescription") or "").strip()
                    print(f"  {field:<36} {title[:40]:<42} {money}")
                    print(f"      -> {code.value if code else 'unclassified':<18}"
                          f" {basis.value if basis else 'no basis'}"
                          + (f"  contents: {contents[:60]}" if contents else ""))

        book_now = PadiComAdapter.fees_from_payload(detail, currency, season)
        counts["bill adds up" if book_now["complete"] else "bill incomplete"] += 1

    print(f"\n--- {read} trip(s)")
    for key, n in counts.most_common():
        print(f"  {n:>5} {key}")
    if disagreements:
        print("\nprice vs extraValue, every disagreement (price wins):")
        for title, price, raw, payed_per in disagreements:
            print(f"  {title[:44]:<46} price={price} extraValue={raw!r} payedPer={payed_per}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
