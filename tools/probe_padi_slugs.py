#!/usr/bin/env python3
"""Find which PADI vessel record is one of our boats, on evidence not on names.

The naive approach fails twice over. Similarity scoring ranks garbage first --
"Destiny" against PADI's "eriny" scores 0.67 -- and substring containment is no
better, which three wrong pairs in `data/padi_aliases.json` demonstrated before
being removed. Sharing a word with a vessel's name says nothing about being that
vessel, because dive centres, boats, dive sites and fleets all borrow each
other's names.

So this probes candidate slugs and then **verifies the record is a boat**:

- ``window.shop.kind`` must be 10. `SHOP_KIND` is ``0`` Dive center, ``10``
  Liveaboard, ``20`` Dive resort, and the distinction is not cosmetic: "Iceberg"
  exists twice, as `deep-breath-diving-safari-the-iceberg` (kind 0, no
  itineraries) and as `my-iceberg` (kind 10, the boat). A page's ``<title>`` is
  no help -- the boat's says "PADI Dive Center".
- ``countrySlug`` must be the country we are mapping.
- The trip lengths it sells are printed against the ones we hold, so a person
  can reject a pairing the slug and the kind both accept.

Nothing here decides. It gathers evidence for a review, and the map it feeds is
hand-maintained for that reason.

**Two traps.** The itineraries endpoint answers 200 with ``{"count": 0}`` for a
slug that does not exist, so an empty list is not evidence of a boat without
trips. And the operator sitemap is *not* a complete inventory -- `my-iceberg`,
`my-odyssey` and three Aggressors are absent from it -- so discovery cannot rely
on it alone.

Writes nothing.

    python3 tools/probe_padi_slugs.py [--limit N] [--json OUT]
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from liveaboard.classify import normalise  # noqa: E402
from liveaboard.scrape.padi_com import ITINERARY_LIST, HOST  # noqa: E402

UA = {"User-Agent": "Mozilla/5.0 (compatible; liveaboard-probe/1.0; +price-transparency research)"}
LIVEABOARD_KIND = 10


def get(url: str, timeout: int = 25) -> str | None:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 - a dead candidate must not end the run
        return None


def fold(name: str) -> str:
    text = re.sub(r"\b(m ?y|m ?v|ms|sy)\b", "", normalise(name))
    return re.sub(r"[^a-z0-9]", "", text)


def slug_candidates(name: str) -> list[str]:
    """Slugs a vessel might live under.

    PADI prefixes many hulls with "my-" and suffixes some with "-safari" or a
    disambiguating number. Ordered cheapest-first: the plain slug, then the
    prefixed forms.
    """
    base = re.sub(r"[^a-z0-9]+", "-", normalise(name)).strip("-")
    stripped = re.sub(r"^(my|mv|ms|ss|sy)-", "", base)
    trimmed = re.sub(r"-(liveaboard|safari|boat)$", "", stripped)
    out: list[str] = []
    for candidate in (base, stripped, trimmed, f"my-{stripped}", f"my-{trimmed}",
                      f"mv-{stripped}", f"ms-{stripped}"):
        if candidate and candidate not in out:
            out.append(candidate)
    return out


def shop_facts(slug: str, country: str) -> dict[str, str] | None:
    """`window.shop` off the vessel page, or None if it is not a boat here."""
    html = get(f"https://{HOST}/liveaboard/{country}/{slug}/", timeout=35)
    if not html or "window.shop = " not in html:
        return None
    block = html[html.index("window.shop = "):][:1200]
    facts: dict[str, str] = {}
    for key in ("id", "slug", "kind", "title", "countrySlug", "itinerariesLength", "fleetTitle"):
        match = re.search(rf'\b{key}:\s*\+?["\']?([^,"\'\n]*)', block)
        if match:
            facts[key] = match.group(1).strip()
    return facts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="data/egypt-2027.json")
    parser.add_argument("--aliases", default="data/padi_aliases.json")
    parser.add_argument("--country", default="egypt")
    parser.add_argument("--limit", type=int, default=0, help="boats to probe, 0 for all")
    parser.add_argument("--delay", type=float, default=0.9)
    parser.add_argument("--json", help="write the candidate pairs here")
    args = parser.parse_args()

    data = json.loads(Path(args.dataset).read_text())
    aliases = json.loads(Path(args.aliases).read_text())
    mapped = set(aliases.get("aliases") or {})
    absent = set(aliases.get("absent") or [])

    operators = {o["id"]: o["name"] for o in data["operators"]}
    itineraries = {i["id"]: i for i in data["itineraries"]}
    departures: collections.Counter = collections.Counter()
    nights: dict[str, set] = collections.defaultdict(set)
    ports: dict[str, set] = collections.defaultdict(set)
    for departure in data["departures"]:
        itinerary = itineraries[departure["itinerary_id"]]
        boat = itinerary["boat_id"]
        departures[boat] += 1
        nights[boat].add(itinerary["nights"])
        for key in ("port_from", "port_to"):
            if itinerary.get(key):
                ports[boat].add(itinerary[key])

    todo = [b for b in data["boats"] if b["id"] not in mapped and b["id"] not in absent]
    todo.sort(key=lambda b: -departures[b["id"]])
    if args.limit:
        todo = todo[: args.limit]
    print(f"probing {len(todo)} unmapped boats in {args.country}\n")

    found = []
    for boat in todo:
        for slug in slug_candidates(boat["name"]):
            body = get(ITINERARY_LIST.format(vessel=slug))
            time.sleep(args.delay)
            if not body:
                continue
            payload = json.loads(body)
            if not payload.get("count"):
                continue

            facts = shop_facts(slug, args.country) or {}
            time.sleep(args.delay)
            kind = facts.get("kind")
            if kind != str(LIVEABOARD_KIND):
                print(f"skip {boat['name']:<26} {slug:<28} shop kind {kind!r}, not a liveaboard")
                continue
            if facts.get("countrySlug") not in (args.country, None):
                print(f"skip {boat['name']:<26} {slug:<28} country {facts.get('countrySlug')!r}")
                continue

            theirs = sorted({
                int(m.group(1))
                for r in payload["results"]
                if (m := re.search(r"(\d+)\s*[Nn]ights?", r["title"]))
            })
            ours = sorted(nights[boat["id"]])
            found.append({
                "boat_id": boat["id"], "our_name": boat["name"], "padi_slug": slug,
                "padi_title": facts.get("title"), "padi_id": facts.get("id"),
                "trips": payload["count"], "padi_nights": theirs, "our_nights": ours,
                "our_guests": boat.get("guests"), "our_cabins": boat.get("cabins"),
                "our_operator": operators.get(boat["operator_id"]),
                "our_ports": sorted(ports[boat["id"]]),
                "our_departures": departures[boat["id"]],
                "nights_agree": bool(set(ours) & set(theirs)),
            })
            print(f"BOAT {boat['name']:<26} -> {slug:<28} "
                  f"{payload['count']:>2} trips  nights {theirs} vs ours {ours}"
                  f"{'' if set(ours) & set(theirs) else '   NIGHTS DISAGREE'}")
            print(f"     PADI {facts.get('title')!r} id={facts.get('id')} kind=10 | "
                  f"ours {boat.get('guests')}g/{boat.get('cabins')}c, "
                  f"{operators.get(boat['operator_id'])}")
            break

    print(f"\n{len(found)} candidate boats for review; "
          f"{len(todo) - len(found)} found nothing")
    if args.json:
        Path(args.json).write_text(json.dumps(found, indent=1) + "\n")
        print(f"written to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
