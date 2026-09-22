#!/usr/bin/env python3
"""Does divebooker.com state a list price anywhere, and is /specials it?

`docs/sources/divebooker.com.md` records a negative that decides this:

    **No berth count, and no list price.** `Offer.availability` is InStock ...
    and nothing in 219 departures states a struck-through or previous price.
    Whatever this source becomes, *places left* and *on sale* are not
    questions it can answer.

That was measured on **vessel pages**, and the same file's entry-point table
names a page nobody has opened: `/specials?et=3&e=1`, 322 KB, *130 priced
trips in JSON-LD*, with `UnitPriceSpecification×130` beside them — a node type
this project has never read, on a page whose whole subject is discounts. A
negative measured on one page is not a negative about a host, so this asks the
page itself before anything is written against either answer.

Five questions, in the order they stop the next one mattering:

1. **Does a special state two figures?** A markdown is a price beside a higher
   price the seller says it is down from. Census over every node the page
   carries — every key on every `Offer`, `UnitPriceSpecification` and
   `TouristTrip` — plus a verbatim dump of a few, because a parser written
   against a described shape is written against the description.
2. **Can a special be keyed to a sailing?** A rate with no boat and no date is
   a banner, and this project does not publish banners (`liveaboard.com`
   publishes *"Up to 30% OFF"* over a region and it is read by nothing). So:
   which hulls does the page name, how many of them are ours, and does
   anything on it state a date.
3. **Is it scoped, and does it page?** `et=3&e=1` is the sitemap's own
   spelling. `/boatsearch` takes `et=2&e=3881` for Egypt, so the same pair is
   tried here — and a `page=` that answers with page 1 is what PADI's deals
   shell did, which is why paging is measured rather than assumed.
4. **What does `UnitPriceSpecification` state on a vessel page?** The source
   map says it sits wherever an `Offer` does and is unread. If a list price
   lives anywhere in bytes this project already fetches daily, it is there,
   and that costs no new request in the pipeline at all.
5. **Is there a discount-shaped key in the streamed payload?** The 253-key
   sweep that found the fee panel matched `fee|extra|includ|exclud` and
   nothing else — the sweep answers only about the words it was given, which
   is the mistake that file records under *"No fee book on the vessel page"
   was wrong*. This one asks about `discount|old|was|sale|promo|percent|rrp`.

Writes nothing. Every request goes through the polite fetcher at this host's
five-second default, and robots.txt is repaired first (see `repair_robots`) —
this host answers `urllib.robotparser` with a 403 that reads as a blanket
refusal nobody wrote.

    python3 tools/probe_divebooker_specials.py [--vessels alsuraya,amelie]
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
from liveaboard.scrape.divebooker_com import payload  # noqa: E402
from probe_divebooker import repair_robots  # noqa: E402

HOST = "divebooker.com"
HULL = re.compile(r'href="(?:https://[^/"]+)?(/([a-z0-9-]+)-haz(\d+))"')

#: Dumped verbatim rather than counted. These are the only node types that
#: could carry a figure a fare is down from.
MONEY_NODES = ("Offer", "UnitPriceSpecification", "AggregateOffer", "PriceSpecification")

#: What a markdown is called in the wild. Deliberately broad and deliberately
#: not `price`: the question is which of the page's own words means *before*.
DISCOUNT_WORD = re.compile(
    r"discount|old[_ ]?price|price[_ ]?old|was[_ ]?price|prev|previous|"
    r"list[_ ]?price|strike|crossed|rrp|regular|original|percent|pct|"
    r"sale|special|promo|offer[_ ]?price|reduc|save|deal",
    re.I,
)

#: Money-shaped keys, for the payload sweep. A discount is a number.
MONEY_KEY = re.compile(r"price|amount|cost|rate|fare|total|value|percent|pct", re.I)

TRUNCATE = 160


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


def nodes_by_type(html: str) -> dict[str, list[dict]]:
    by_type: dict[str, list[dict]] = {}
    for node in jsonld.walk_documents(html):
        for name in node.get("@type", []) if isinstance(node.get("@type"), list) else [
            node.get("@type")
        ]:
            if isinstance(name, str):
                by_type.setdefault(name.rsplit("/", 1)[-1], []).append(node)
    return by_type


def census(nodes: list[dict], label: str) -> None:
    """Which keys every node of this type carries, and which only some do."""
    if not nodes:
        print(f"  {label}: none")
        return
    counts: Counter[str] = Counter()
    for node in nodes:
        counts.update(k for k in node if not k.startswith("@"))
    total = len(nodes)
    always = sorted(k for k, n in counts.items() if n == total)
    print(f"  {label}: {total} node(s)")
    print(f"    on every one : {', '.join(always) if always else '—'}")
    for key, n in sorted((k, n) for k, n in counts.items() if n < total):
        print(f"    on {n:>4}/{total}  : {key}")


def show(node: dict, label: str) -> None:
    print(f"    one {label}:")
    for key, value in node.items():
        text = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
        if len(text) > TRUNCATE:
            text = text[:TRUNCATE] + "…"
        print(f"      {key:<24} {text}")


def two_figures(by_type: dict[str, list[dict]]) -> None:
    """Does any node on this page state a price and a higher figure beside it?

    The whole question, asked of the data rather than of the key names: a
    markdown is arithmetic, so find every pair of numbers under one node and
    say whether one exceeds the other. A source that discounts and names the
    field something this probe never guessed still fails this test loudly.
    """
    pairs = 0
    seen = 0
    for label in MONEY_NODES:
        for node in by_type.get(label, []):
            figures: dict[str, float] = {}
            for key, value in node.items():
                if key.startswith("@") or not MONEY_KEY.search(key):
                    continue
                number = _number(value)
                if number is not None:
                    figures[key] = number
            if len(figures) < 2:
                continue
            seen += 1
            distinct = sorted(set(figures.values()))
            if len(distinct) > 1:
                pairs += 1
                if pairs <= 6:
                    print(f"    {label}: {figures}")
    print(f"  nodes carrying two money-shaped numbers: {seen}"
          f", of which the two differ: {pairs}")
    if not pairs:
        print("    Nothing here states a figure beside a higher one. On this page,")
        print("    a 'special' is a price with no *before* attached to it.")


def _number(value: object) -> float | None:
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def word_sweep(text: str, label: str) -> None:
    """Which discount words appear in these bytes, and how often."""
    hits = Counter(m.group(0).lower() for m in DISCOUNT_WORD.finditer(text))
    print(f"  {label}: {sum(hits.values())} discount-shaped word(s)")
    for word, n in hits.most_common(20):
        print(f"    {n:>6}  {word}")


def payload_keys(html: str, label: str) -> None:
    """Every key in the streamed payload whose *name* suggests a markdown.

    The fee panel was missed by a sweep over four words. This one prints the
    matching keys and a value apiece, so a key named something nobody would
    guess is still visible in the count of what did not match.
    """
    try:
        text = payload(html)
    except Exception as exc:  # noqa: BLE001
        print(f"  {label}: payload unreadable: {type(exc).__name__}: {exc}")
        return
    keys = Counter(re.findall(r'"([A-Za-z_][A-Za-z0-9_]{2,40})"\s*:', text))
    marked = {k: n for k, n in keys.items() if DISCOUNT_WORD.search(k)}
    money = {k: n for k, n in keys.items() if MONEY_KEY.search(k)}
    print(f"  {label}: {len(text) / 1024:.0f} KB, {len(keys)} distinct key(s)")
    print(f"    discount-shaped names: {len(marked)}")
    for key, n in sorted(marked.items(), key=lambda kv: -kv[1])[:20]:
        sample = re.search(rf'"{re.escape(key)}"\s*:\s*([^,}}]{{0,60}})', text)
        print(f"      {n:>5}x {key:<28} e.g. {sample.group(1).strip() if sample else '?'}")
    print(f"    money-shaped names: {len(money)}")
    for key, n in sorted(money.items(), key=lambda kv: -kv[1])[:20]:
        sample = re.search(rf'"{re.escape(key)}"\s*:\s*([^,}}]{{0,40}})', text)
        print(f"      {n:>5}x {key:<28} e.g. {sample.group(1).strip() if sample else '?'}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--specials", default="/specials?et=3&e=1",
                        help="the deals path, as the sitemap spells it")
    parser.add_argument("--also", default="/specials?et=2&e=3881,/specials",
                        help="other spellings to try, comma separated")
    parser.add_argument("--pages", default="2",
                        help="page numbers to try against the specials path")
    parser.add_argument("--vessels", default="alsuraya,amelie",
                        help="hull slugs whose UnitPriceSpecification to read")
    parser.add_argument("--campaign", default="/black-friday-liveaboard-diving-deals-iaz103",
                        help="one campaign page, or empty to skip")
    parser.add_argument("--aliases", type=Path, default=Path("data/divebooker_aliases.json"))
    parser.add_argument("--delay", type=float, default=5.0)
    parser.add_argument("--snapshots", type=Path, default=Path("data/snapshots"))
    args = parser.parse_args()

    fetcher = PoliteFetcher(snapshot_dir=args.snapshots, delay=args.delay)
    base = f"https://{HOST}"
    for host in (HOST, f"www.{HOST}"):
        repair_robots(fetcher, host)

    ours: dict[str, str] = {}
    if args.aliases.exists():
        book = json.loads(args.aliases.read_text())
        ours = dict(book.get("aliases") or {})

    print("== 1. what /specials states ==")
    html = get_body(fetcher, base + args.specials)
    if html is not None:
        by_type = nodes_by_type(html)
        print("  node types: " + ", ".join(
            f"{name}×{len(nodes)}" for name, nodes in sorted(
                by_type.items(), key=lambda kv: -len(kv[1]))))
        for label in MONEY_NODES + ("TouristTrip", "Event", "Product"):
            census(by_type.get(label, []), label)
            if by_type.get(label):
                show(by_type[label][0], label)

        print("\n-- does anything state a figure beside a higher one --")
        two_figures(by_type)

        print("\n-- 2. can a special be keyed to a sailing --")
        hulls = {m.group(2): m.group(1) for m in HULL.finditer(html)}
        mine = sorted(set(hulls) & set(ours))
        print(f"  hull links: {len(hulls)} distinct, {len(mine)} of them in our book")
        for slug in mine[:12]:
            print(f"    ours: {slug} -> {ours[slug]}")
        dates = re.findall(
            r'"(?:startDate|validThrough|validFrom|endDate)"\s*:\s*"([^"]{4,25})"', html)
        print(f"  date-shaped JSON keys: {len(dates)}"
              + (f", e.g. {dates[:4]}" if dates else " — nothing here states a day"))
        word_sweep(html, "  /specials markup")
        payload_keys(html, "/specials payload")

    print("\n== 3. is the listing scoped, and does it page ==")
    seen: dict[str, int] = {}
    if html is not None:
        seen[args.specials] = len(nodes_by_type(html).get("Offer", []))
    for path in [p for p in args.also.split(",") if p.strip()]:
        other = get_body(fetcher, base + path.strip())
        if other is not None:
            offers = nodes_by_type(other).get("Offer", [])
            hulls = {m.group(2) for m in HULL.finditer(other)}
            seen[path.strip()] = len(offers)
            print(f"    {path.strip()}: Offer×{len(offers)}, {len(hulls)} hull(s),"
                  f" {len(set(hulls) & set(ours))} ours")
    for page in [p for p in args.pages.split(",") if p.strip()]:
        sep = "&" if "?" in args.specials else "?"
        paged = get_body(fetcher, f"{base}{args.specials}{sep}page={page.strip()}")
        if paged is not None:
            offers = nodes_by_type(paged).get("Offer", [])
            first = [str(o.get("name") or o.get("url") or "") for o in offers[:3]]
            print(f"    page={page.strip()}: Offer×{len(offers)}, first: {first}")
            print("    (identical first entries mean the parameter is ignored,")
            print("     which is what PADI's own deals shell did)")

    print("\n== 4. what UnitPriceSpecification states on a vessel page ==")
    for slug in [s for s in args.vessels.split(",") if s.strip()]:
        page = get_body(fetcher, f"{base}/{_hull_path(fetcher, base, slug.strip())}")
        if page is None:
            continue
        by_type = nodes_by_type(page)
        print(f"  {slug}: " + ", ".join(
            f"{name}×{len(nodes)}" for name, nodes in sorted(
                by_type.items(), key=lambda kv: -len(kv[1]))[:10]))
        for label in ("UnitPriceSpecification", "AggregateOffer"):
            census(by_type.get(label, []), f"{slug} {label}")
            if by_type.get(label):
                show(by_type[label][0], label)
        two_figures(by_type)
        print("\n-- 5. discount-shaped keys in the streamed payload --")
        payload_keys(page, f"  {slug}")
        word_sweep(page, f"  {slug} markup")

    if args.campaign.strip():
        print("\n== 6. what a campaign page states ==")
        page = get_body(fetcher, base + args.campaign.strip())
        if page is not None:
            by_type = nodes_by_type(page)
            print("  node types: " + ", ".join(
                f"{name}×{len(nodes)}" for name, nodes in sorted(
                    by_type.items(), key=lambda kv: -len(kv[1]))))
            two_figures(by_type)
            hulls = {m.group(2) for m in HULL.finditer(page)}
            print(f"  hull links: {len(hulls)}, {len(set(hulls) & set(ours))} ours")
    return 0


def _hull_path(fetcher: PoliteFetcher, base: str, slug: str) -> str:
    """This site's own URL for a hull, from the country page rather than guessed.

    A hull path carries a numeric id this project does not hold, so the slug
    alone is not a URL. The country page links every hull it sells, and that
    link is the seller's own spelling of it — which is the rule
    `probe_divebooker_departures.py` already follows and the reason this probe
    does not build `/{slug}-haz{id}` out of a guess.
    """
    key = "_country_links"
    cached = getattr(_hull_path, key, None)
    if cached is None:
        page = get_body(fetcher, f"{base}/egypt-daz3881")
        cached = {m.group(2): m.group(1).lstrip("/") for m in HULL.finditer(page or "")}
        setattr(_hull_path, key, cached)
    return cached.get(slug, f"{slug}-haz0")


if __name__ == "__main__":
    raise SystemExit(main())
