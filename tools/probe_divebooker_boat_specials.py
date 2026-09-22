#!/usr/bin/env python3
"""What `boatSpecials` holds, on a page this project already fetches daily.

`probe_divebooker_specials.py` asked whether this seller states a list price
and got two answers worth following. `/specials` publishes **125 offers, one
per boat**, each a `UnitPriceSpecification` with a price, a currency, a unit of
*trip* and a sentence of the seller's own words — and **no date and no figure
it is down from**, on any node, on any of the three spellings of that URL. So
the listing is a from-price and a campaign line, not a markdown.

The other answer is the one this probe is for. The vessel page's streamed
payload carries a key called **`boatSpecials`**, `[{"id":"3407", …}]`, and it
matched nothing any previous sweep asked about: the fee sweep asked for
`fee|extra|includ|exclud` and the currency work read `currencies` and `rates`.
It sits in bytes `fetch_divebooker.py` already downloads for every hull every
morning, so whatever it states costs no request at all to start reading.

What has to be known before a parser is written, and in this order:

1. **What an entry states.** Printed verbatim, then a census of the keys over
   every entry on every hull read. A name is a banner; a name with a rate and a
   window is a sale this site can publish.
2. **Whether it reaches a sailing.** This project's whole sale mechanism is a
   markdown against *a departure* — `_sale_for` reads one seller's price beside
   the figure that seller says it is down from. An entry that names the boat
   and nothing else can only ever be a row in the sale table, like PADI's
   named offers, and never a `−15%` on a departure row.
3. **Whether the `Offer.price` beside it is already the discounted one.** If
   the seller applies its own special to the fares it publishes, the fare this
   site prints is the sale price and the special is the *before* — and if it
   does not, the two are different claims about one berth and neither may be
   subtracted from the other. Nothing here can settle that alone; what it can
   do is print both, on the same hull, for a person to read.

Writes nothing, and reads each hull once.

    python3 tools/probe_divebooker_boat_specials.py [--vessels 6]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from liveaboard.scrape import jsonld  # noqa: E402
from liveaboard.scrape.base import FetchBlocked, PoliteFetcher  # noqa: E402
from liveaboard.scrape.divebooker_com import balanced, payload_parts  # noqa: E402
from probe_divebooker import repair_robots  # noqa: E402

HOST = "divebooker.com"
EGYPT = "/specials?et=2&e=3881"
HULL = re.compile(r'href="(?:https://[^/"]+)?(/([a-z0-9-]+)-haz(\d+))"')

#: The keys worth opening whole. `boatSpecials` is the find; the other two are
#: here because a sweep that looks only at the key it expects is the mistake
#: this whole probe family exists to avoid.
KEYS = ("boatSpecials", "specials", "promotions")

TRUNCATE = 2000


def get_body(fetcher: PoliteFetcher, url: str) -> str | None:
    try:
        result = fetcher.get(url)
    except FetchBlocked as exc:
        print(f"  BLOCKED {url}: {exc}")
        return None
    except Exception as exc:  # noqa: BLE001 - one dead page must not end the run
        print(f"  FAILED  {url}: {type(exc).__name__}: {exc}")
        return None
    print(f"  {url} — {len(result.body) / 1024:.0f} KB")
    return result.body


def values_of(text: str, key: str) -> list[object]:
    """Every value the payload states under `key`, parsed.

    The payload is one enormous line, so the value is found by matching what
    opened it — `balanced` — rather than by looking for where it ends.
    """
    found: list[object] = []
    for match in re.finditer(rf'"{re.escape(key)}"\s*:\s*', text):
        start = match.end()
        if start >= len(text):
            continue
        if text[start] in "{[":
            raw = balanced(text, start)
        else:
            raw = re.match(r'("(?:[^"\\]|\\.)*"|[^,}\]]+)', text[start:])
            raw = raw.group(0) if raw else None
        if raw is None:
            continue
        try:
            found.append(json.loads(raw))
        except json.JSONDecodeError:
            found.append(raw)
    return found


def entries(value: object) -> list[dict]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return [value] if isinstance(value, dict) else []


def census(rows: list[dict], label: str) -> None:
    if not rows:
        print(f"  {label}: no entries")
        return
    counts: Counter[str] = Counter()
    for row in rows:
        counts.update(row)
    total = len(rows)
    print(f"  {label}: {total} entr{'y' if total == 1 else 'ies'}")
    for key, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        sample = next((row[key] for row in rows if row.get(key) not in (None, "")), "")
        text = json.dumps(sample, ensure_ascii=False)
        print(f"    on {n:>3}/{total}  {key:<26} e.g. {text[:90]}")


def offer_lines(html: str) -> list[tuple[str, str, str]]:
    """Every offer the listing states, as (boat, what it says, the from-price)."""
    out: list[tuple[str, str, str]] = []
    for node in jsonld.walk_documents(html):
        types = node.get("@type")
        types = types if isinstance(types, list) else [types]
        if "Offer" not in types:
            continue
        trip = node.get("itemOffered") or {}
        spec = node.get("priceSpecification") or {}
        out.append((
            str((trip or {}).get("name") or "?"),
            str(node.get("description") or ""),
            f"{spec.get('price', '?')} {spec.get('priceCurrency', '')}"
            f" per {spec.get('unitText', '?')}",
        ))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listing", default=EGYPT,
                        help="the specials listing, Egypt-scoped")
    parser.add_argument("--vessels", type=int, default=6,
                        help="hulls to open, taken from the listing's own links")
    parser.add_argument("--also", default="",
                        help="extra hull slugs to open, comma separated")
    parser.add_argument("--delay", type=float, default=5.0)
    parser.add_argument("--snapshots", type=Path, default=Path("data/snapshots"))
    args = parser.parse_args()

    fetcher = PoliteFetcher(snapshot_dir=args.snapshots, delay=args.delay)
    base = f"https://{HOST}"
    for host in (HOST, f"www.{HOST}"):
        repair_robots(fetcher, host)

    print("== the offers the listing states, one line each ==")
    listing = get_body(fetcher, base + args.listing)
    paths: list[str] = []
    if listing is not None:
        for boat, says, price in offer_lines(listing):
            print(f"  {boat:<32} {price:<22} {says}")
        seen: dict[str, str] = {}
        for match in HULL.finditer(listing):
            seen.setdefault(match.group(2), match.group(1))
        paths = [seen[slug] for slug in list(seen)[: args.vessels]]
        print(f"  {len(seen)} hull(s) linked; opening {len(paths)}")

    for extra in [s.strip() for s in args.also.split(",") if s.strip()]:
        paths.append(f"/{extra}")

    every: dict[str, list[dict]] = {key: [] for key in KEYS}
    for path in paths:
        print(f"\n== {path} ==")
        page = get_body(fetcher, base + path)
        if page is None:
            continue
        text, dropped = payload_parts(page)
        print(f"  payload {len(text) / 1024:.0f} KB, {dropped} chunk(s) undecodable")
        for key in KEYS:
            values = values_of(text, key)
            rows = [row for value in values for row in entries(value)]
            every[key].extend(rows)
            if not values:
                print(f"  {key}: absent")
                continue
            print(f"  {key}: {len(values)} occurrence(s), {len(rows)} entr(y/ies)")
            for value in values[:2]:
                dumped = json.dumps(value, ensure_ascii=False, indent=1)
                print("    " + dumped[:TRUNCATE].replace("\n", "\n    "))
                if len(dumped) > TRUNCATE:
                    print(f"    … {len(dumped) - TRUNCATE} more character(s)")

        # What the same page charges, so the special and the fare can be read
        # against each other rather than one at a time. A special that is
        # already inside `Offer.price` is a *before* this site does not hold;
        # one that is not is a claim about a different number.
        prices = sorted({
            number
            for node in jsonld.walk_documents(page)
            for number in [_number(node.get("price"))]
            if number is not None
        })
        if prices:
            print(f"  fares on this page: {len(prices)} distinct,"
                  f" {prices[0]:.0f} to {prices[-1]:.0f}")

    print("\n== across every hull read ==")
    for key in KEYS:
        census(every[key], key)
    print("\n  What this decides: an entry naming the boat and nothing else is a")
    print("  campaign line — the sale table can print it, a departure row cannot.")
    print("  An entry stating a rate and a window is a markdown, and the next")
    print("  question is whether the fares beside it already have it taken off.")
    return 0


def _number(value: object) -> float | None:
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
