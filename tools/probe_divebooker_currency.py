#!/usr/bin/env python3
"""What currency is a divebooker price in? The label says one thing; the digits say another.

Against the two sellers this project already reads, **the number matches the
dollar figure and the label often does not**: of the 618 joined rows where
divebooker says EUR and liveaboard.com says USD, 554 carry the same number to
the cent, and where liveaboard.com itself states EUR the divebooker figure is
1.148x it — which is 1/0.8726, the euro-dollar rate. Either divebooker charges
a 14.6% premium that lands exactly on the other seller's dollar price across a
dozen operators, or `Offer.priceCurrency` is not describing `Offer.price`.

An agreement with another seller is evidence, not a source. This asks the page
itself, three ways:

* **The payload's own currency table.** `currencies.current` is what the
  server rendered in and `rates` is keyed by the same numeric `currencyId` the
  boat cards carry (`docs/sources/divebooker.com.md`). If `current` is USD on a
  page whose JSON-LD says EUR, the label is not the money.
* **`minPrice` against the JSON-LD.** The boat card states a minimum and a
  `currencyId`. If that figure equals the cheapest `Offer.price` on the page,
  the offer is denominated in whatever the `currencyId` names — read off the
  page rather than inferred from us.
* **Asking for another currency.** Query variants are tried against a known
  answer, the way `p=` was: a wrong one changes nothing and says so. What
  matters is *which half moves* — a number that changes while the label holds
  is a display conversion; a label that changes while the number holds is a
  label that never described the number.

Writes nothing. Vessel ids come from the committed book, never typed.

    python3 tools/probe_divebooker_currency.py [--vessels seawolf-steel,red-sea-aggressor-iv]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from liveaboard.scrape import divebooker_com as db, jsonld  # noqa: E402
from liveaboard.scrape.base import FetchBlocked, PoliteFetcher  # noqa: E402
from probe_divebooker import repair_robots  # noqa: E402

#: One hull whose offers are labelled EUR, one labelled USD, one of each fleet
#: that disagrees. Named rather than spelled: the id comes from the book.
VESSELS = "seawolf-steel,red-sea-aggressor-iv,unity,iceberg"

FLIGHT = re.compile(r'self\.__next_f\.push\(\[\d+,\s*(".*?")\]\)', re.S)
CURRENCIES = re.compile(r'"currencies"\s*:\s*(\{.{0,400}?\})', re.S)
RATES = re.compile(r'"rates"\s*:\s*(\{.{0,600}?\})', re.S)
MIN_PRICE = re.compile(r'"minPrice"\s*:\s*([0-9.]+)')
MIN_PRICE_DAY = re.compile(r'"minPriceDay"\s*:\s*([0-9.]+)')
CURRENCY_ID = re.compile(r'"currencyId"\s*:\s*"?(\d+)"?')
CURRENT = re.compile(r'"current"\s*:\s*"([A-Z]{3})"')
ANY_CCY = re.compile(r'"(?:currency|currencyCode|currencyIso)"\s*:\s*"([A-Z]{3})"')


def payload_of(html: str) -> str:
    """The streamed RSC chunks, decoded and joined. It is in the HTML."""
    return "".join(json.loads(chunk) for chunk in FLIGHT.findall(html))


def offers(html: str) -> list[tuple[float, str]]:
    out = []
    for node in jsonld.walk_documents(html):
        if node.get("@type") == "Offer" and node.get("price") is not None:
            try:
                out.append((float(node["price"]), node.get("priceCurrency") or "?"))
            except (TypeError, ValueError):
                continue
    return out


def report(name: str, html: str) -> dict:
    body = payload_of(html)
    priced = offers(html)
    cheapest = min(priced)[0] if priced else None
    labels = sorted({c for _, c in priced})
    current = CURRENT.findall(body)
    facts = {
        # The parser's own function, against the bytes the server sent, so
        # what ships is what was proved rather than a regex that looked right
        # in a scratch file. The payload arrives backslash-escaped inside the
        # RSC chunks and this reads the raw HTML, not the decoded body.
        "page_currency() says": db.page_currency(html),
        "offers": len(priced),
        "cheapest offer": cheapest,
        "priceCurrency labels": labels,
        "currencies.current": sorted(set(current)) or "not stated",
        "currency codes anywhere in payload": sorted(set(ANY_CCY.findall(body)))[:8],
        "currencyId values": sorted(set(CURRENCY_ID.findall(body)))[:8],
        "minPrice": sorted({float(v) for v in MIN_PRICE.findall(body)})[:6],
        "minPriceDay": sorted({float(v) for v in MIN_PRICE_DAY.findall(body)})[:6],
    }
    print(f"\n== {name} ==")
    for key, value in facts.items():
        print(f"  {key:<38} {value}")
    block = CURRENCIES.search(body)
    if block:
        print(f"  currencies block: {block.group(1)[:300]}")
    rates = RATES.search(body)
    if rates:
        print(f"  rates block:      {rates.group(1)[:300]}")
    return facts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vessels", default=VESSELS)
    parser.add_argument("--book", default=Path("data/divebooker.json"), type=Path)
    parser.add_argument("--variants", default="currency=USD,currency=EUR,cur=USD",
                        help="query fragments to try against the first vessel")
    parser.add_argument("--delay", type=float, default=5.0)
    parser.add_argument("--snapshots", default=Path("data/snapshots"), type=Path)
    args = parser.parse_args()

    fetcher = PoliteFetcher(snapshot_dir=args.snapshots, delay=args.delay)
    for host in (db.HOST, f"www.{db.HOST}"):
        repair_robots(fetcher, host)

    vessels = json.loads(args.book.read_text(encoding="utf-8")).get("vessels") or {}
    base = f"https://{db.HOST}"
    first_path = None
    for slug in args.vessels.split(","):
        slug = slug.strip()
        record = vessels.get(slug)
        if not record or not record.get("divebooker_id"):
            print(f"\n== {slug}: not in {args.book}, and typing an id reads "
                  f"another boat ==")
            continue
        path = f"/{slug}-{record['divebooker_id']}"
        first_path = first_path or path
        try:
            result = fetcher.get(base + path)
        except FetchBlocked as exc:
            print(f"BLOCKED {path}: {exc}")
            continue
        report(f"{path} ({record.get('name')})", result.body)

    if first_path and args.variants:
        print(f"\n== asking {first_path} for another currency ==")
        for variant in args.variants.split(","):
            variant = variant.strip()
            try:
                other = fetcher.get(f"{base}{first_path}?{variant}")
            except FetchBlocked as exc:
                print(f"  {variant:<16} BLOCKED: {exc}")
                continue
            except Exception as exc:  # noqa: BLE001 - a 404 is an answer
                print(f"  {variant:<16} {type(exc).__name__}: {exc}")
                continue
            priced = offers(other.body)
            current = sorted(set(CURRENT.findall(payload_of(other.body))))
            print(f"  {variant:<16} cheapest {min(priced)[0] if priced else '-'}"
                  f"  labels {sorted({c for _, c in priced})}"
                  f"  currencies.current {current or '-'}")
    print("\n  What this decides: whether `price` is the dollar figure the other "
          "two\n  sellers state, in which case the label is the thing to "
          "distrust — and\n  a fare can be published — or whether this seller "
          "really charges 14.6% more.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
