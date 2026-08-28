#!/usr/bin/env python3
"""Read one PADI Travel vessel page and hold it against our own dataset.

The question this answers is not "can we fetch padi.com" -- we can -- but
**which of the facts we need actually survive a single plain HTTP GET**, and
whether the trips it names can be joined to the trips we already have. One boat,
one request, both halves of the answer.

Why a vessel page rather than the search or an itinerary page: it is the only
surface on travel.padi.com that is server-rendered *and* carries per-trip
records. The Next.js search at ``/s/liveaboards/<country>/`` renders a count and
nothing else; an itinerary page renders site chrome and fills its body from an
AngularJS XHR whose bundle is on a CDN. The vessel page needs neither browser
nor bundle.

Discovery needs no crawl either. ``sitemap-travel-dive-operators-page_1.xml``
lists all 269 liveaboards, 58 of them under ``/liveaboard/egypt/``.

Writes nothing. See docs/sources/padi.com.md.

    python3 tools/probe_padi_vessel.py --slug hammerhead-ii --boat hammerhead-ii
"""

from __future__ import annotations

import argparse
import html as html_mod
import json
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

HOST = "travel.padi.com"
UA = "Mozilla/5.0 (compatible; liveaboard-probe/1.0; +price-transparency research)"

# The itinerary list in the vessel page's nav: slug and the operator's own title
# for that trip. Both are server-rendered, which is the point.
ITIN_NAV = re.compile(
    r'href="/liveaboard/[a-z0-9-]+/[a-z0-9-]+/(?P<slug>[a-z0-9-]+)/"'
    r'(?:[^>]*)>(?P<title>[^<]+)</a>',
    re.I | re.S,
)
# "Name (Port - Port) N Nights" -- PADI's trip title, and near enough to our own
# Itinerary.name that a join is worth trying.
TRIP_TITLE = re.compile(r'^(?P<name>.+?)\s*\((?P<ports>[^)]*)\)\s*(?P<nights>\d+)\s*Nights?$', re.I)


ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\ufeff"))
NIGHTS_SUFFIX = re.compile(r"\s*\d+\s*Nights?\s*$", re.I)


def text(raw: str) -> str:
    """PADI double-escapes titles into JSON-LD: "Sharks &amp; Dolphins".

    Zero-width spaces are left in place: both sources carry them (our own
    "Red Sea Charm\u200b:" came from the same operator), so they are part of the
    string, and stripping them is the join key's job rather than the reader's.
    """
    return html_mod.unescape(html_mod.unescape(raw)).strip()


def name_of(title: str) -> str:
    """PADI's title minus its night count -- which is our ``Itinerary.name``.

    Our names carry the ports and stop: "Sharks & Dolphins (Marsa Alam -
    Hurghada)". PADI appends " 7 Nights", and sometimes appends it twice, so the
    strip has to loop rather than fire once.
    """
    out = title
    while True:
        trimmed = NIGHTS_SUFFIX.sub("", out)
        if trimmed == out:
            return out.strip()
        out = trimmed


def get(url: str, cache: Path | None = None) -> str:
    """Fetch, or re-read a cached fetch.

    Iterating on a parser must not cost somebody else's server a request per
    attempt -- the same reason ``reparse_candidate.py`` exists. With --cache the
    first run fetches and every run after it is offline.
    """
    if cache and cache.exists():
        print(f"  (cached: {cache})")
        return cache.read_text()
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en"})
    with urllib.request.urlopen(req, timeout=45) as response:
        body = response.read().decode("utf-8", "replace")
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(body)
    return body


def brace_blob(html: str, anchor: str, back: bool = True) -> dict | None:
    """The JSON object enclosing (or following) `anchor`, or None."""
    try:
        i = html.index(anchor)
    except ValueError:
        return None
    if back:
        depth, j = 0, i
        while j > 0:
            if html[j] == "}":
                depth += 1
            elif html[j] == "{":
                if depth == 0:
                    break
                depth -= 1
            j -= 1
        start = j
    else:
        start = html.index("{", i)
    depth, k = 0, start
    while k < len(html):
        if html[k] == "{":
            depth += 1
        elif html[k] == "}":
            depth -= 1
            if depth == 0:
                break
        k += 1
    try:
        return json.loads(html[start : k + 1])
    except json.JSONDecodeError:
        return None


def jsonld(html: str) -> list[dict]:
    out = []
    for m in re.finditer(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html, re.S):
        try:
            out.append(json.loads(m.group(1)))
        except json.JSONDecodeError:
            pass
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True, help="PADI vessel slug, e.g. hammerhead-ii")
    ap.add_argument("--country", default="egypt")
    ap.add_argument("--boat", help="our boat id in data/egypt-2027.json, to join against")
    ap.add_argument("--from-month", default="2027-05")
    ap.add_argument("--to-month", default="2027-08")
    ap.add_argument("--dataset", default="data/egypt-2027.json")
    ap.add_argument("--cache", help="directory to cache the fetched page in")
    args = ap.parse_args()

    url = f"https://{HOST}/liveaboard/{args.country}/{args.slug}/"
    print(f"GET {url}")
    cache = Path(args.cache) / f"{args.country}-{args.slug}.html" if args.cache else None
    html = get(url, cache)
    print(f"  {len(html)} bytes\n")

    # --- 1. what the page states about the vessel -----------------------------
    block = re.search(r"window\.shop\s*=\s*\{(.*?)\n  \};", html, re.S)
    shop = {}
    if block:
        for k, v in re.findall(r"(\w+):\s*\+?[\"']?([^,\"'\n]*)[\"']?,?\s*$",
                               block.group(1), re.M):
            shop[k] = v.strip()
    print("== vessel ==")
    for key in ("id", "title", "slug", "fleetTitle", "itinerariesLength", "minimumPrice", "currency"):
        if key in shop:
            print(f"  {key:20} {shop[key]}")

    # --- 2. the requirement vocabulary --------------------------------------
    enums = brace_blob(html, "ITINERARY_CERTIFICATION_CHOICES")
    print("\n== requirement vocabulary (window.info.shop) ==")
    if enums:
        for key in ("ITINERARY_CERTIFICATION_CHOICES", "EXPERIENCE_REQUIRED_DIVES"):
            print(f"  {key} = {json.dumps(enums.get(key))}")
    else:
        print("  NOT FOUND")

    # --- 3. trips, from JSON-LD ----------------------------------------------
    trips: dict[str, dict] = {}
    for node in jsonld(html):
        for offer in node.get("offers", []) or []:
            name = text(offer.get("name") or offer.get("itemOffered") or "")
            q = re.search(r"trip_id=(\d+)&trip_date=([\d-]+)", offer.get("url", "") or "")
            trip = trips.setdefault(
                name, {"name": name, "dates": [], "low": offer.get("lowPrice"),
                        "high": offer.get("highPrice"), "currency": offer.get("priceCurrency"),
                        "offers": offer.get("offerCount"), "trip_ids": set()}
            )
            if q:
                trip["trip_ids"].add(q.group(1))
                trip["dates"].append(q.group(2))
    print(f"\n== trips in JSON-LD: {len(trips)} ==")
    for t in sorted(trips.values(), key=lambda t: t["name"]):
        m = TRIP_TITLE.match(t["name"])
        parsed = (f"nights={m.group('nights')} ports={m.group('ports')!r} name={m.group('name')!r}"
                  if m else "TITLE DID NOT PARSE")
        print(f"  {t['name']}")
        print(f"    {parsed}")
        print(f"    dates={sorted(t['dates'])} price={t['low']}-{t['high']} {t['currency']} offerCount={t['offers']}")

    # --- 4. itineraries, from the nav ---------------------------------------
    nav: dict[str, str] = {}
    for m in ITIN_NAV.finditer(html):
        title = text(m.group("title"))
        if title and "Nights" in title:
            nav.setdefault(m.group("slug"), title)
    print(f"\n== itineraries in nav: {len(nav)} ==")
    for slug, title in sorted(nav.items(), key=lambda kv: kv[1]):
        parsed = TRIP_TITLE.match(title)
        night = parsed.group("nights") if parsed else "?"
        print(f"  {night:>2}n  {title}")
        print(f"        slug={slug}")

    # --- 5. is any per-trip requirement value in this HTML? -----------------
    print("\n== per-trip requirement values in server HTML? ==")
    vocab = []
    if enums:
        vocab = [t for _, t in enums.get("ITINERARY_CERTIFICATION_CHOICES", [])] + \
                [t for _, t in enums.get("EXPERIENCE_REQUIRED_DIVES", [])]
    stripped = re.sub(r"<script.*?</script>", "", html, flags=re.S)
    hits = [v for v in vocab if v in stripped]
    print(f"  vocabulary terms outside <script>: {hits or 'NONE'}")
    for key in ("certification", "experience_required", "min_certification", "requirements"):
        n = len(re.findall(rf'"{key}"\s*:', html))
        print(f'  "{key}": {n} occurrence(s) in raw HTML')

    # --- 6. join to our own data --------------------------------------------
    if not args.boat:
        return 0
    data = json.loads(Path(args.dataset).read_text())
    ours = [i for i in data["itineraries"] if i["boat_id"] == args.boat]
    deps = {d["itinerary_id"]: d for d in data["departures"]}
    window = [
        d for d in data["departures"]
        if args.from_month <= d["start"][:7] <= args.to_month
        and any(i["id"] == d["itinerary_id"] for i in ours)
    ]
    print(f"\n== our dataset: boat {args.boat!r} ==")
    print(f"  itineraries: {len(ours)}   departures {args.from_month}..{args.to_month}: {len(window)}")

    def key(value: str) -> str:
        """Compare on letters and digits only, zero-width spaces discarded."""
        return re.sub(r"[^a-z0-9]", "", value.translate(ZERO_WIDTH).lower())

    # PADI title minus its night suffix == our Itinerary.name. That is the join.
    padi_by_name: dict[str, list[tuple[str, str]]] = {}
    for slug, title in nav.items():
        padi_by_name.setdefault(key(name_of(title)), []).append((slug, title))

    print("\n== join: our itinerary.name -> PADI title minus night suffix ==")
    matched = 0
    claimed: set[str] = set()
    for i in sorted(ours, key=lambda i: i["name"]):
        k = key(i["name"])
        hits = padi_by_name.get(k, [])
        if hits:
            matched += 1
            claimed.add(k)
            slug, title = hits[0]
            extra = f"  (+{len(hits) - 1} more PADI rows on this name)" if len(hits) > 1 else ""
            ours_n = i.get("nights")
            m = TRIP_TITLE.match(title)
            padi_n = int(m.group("nights")) if m else None
            flag = "" if padi_n in (None, ours_n) else f"  NIGHTS DISAGREE ours={ours_n} padi={padi_n}"
            print(f"  MATCH    {i['name']}{extra}{flag}")
            print(f"           slug={slug}")
        else:
            print(f"  no hit   {i['name']}")
    print(f"\n  matches: {matched}/{len(ours)} ours, against {len(nav)} PADI itineraries")

    unclaimed = sorted(
        title for k, rows in padi_by_name.items() if k not in claimed for _, title in rows
    )
    print(f"\n  PADI itineraries with no counterpart of ours: {len(unclaimed)}")
    for title in unclaimed:
        print(f"    {title}")

    # The slug is not a fact. Say so with evidence rather than in a comment.
    print("\n== slug vs title: does the URL describe the trip it serves? ==")
    lies = 0
    for slug, title in sorted(nav.items(), key=lambda kv: kv[1]):
        m = TRIP_TITLE.match(title)
        if not m:
            continue
        slug_nights = re.search(r"(\d+)-nights?$", slug)
        title_nights = m.group("nights")
        if slug_nights and slug_nights.group(1) != title_nights:
            lies += 1
            print(f"  slug says {slug_nights.group(1)}n, title says {title_nights}n: {slug}")
        stem = key(name_of(title))[:18]
        if stem and key(slug)[:18] != stem:
            lies += 1
            print(f"  slug stem disagrees with title: {slug}")
            print(f"      title: {title}")
    print(f"\n  slugs contradicting their own page: {lies}/{len(nav)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
