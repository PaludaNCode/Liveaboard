#!/usr/bin/env python3
"""Does PADI's vessel page state how many guests a boat carries?

`padi_com.specs_from_page` says it does not, and says so as a settled
negative: *"The strip has no such row and neither does the rest of the page --
searched in full, every numeric form of guests, divers, passengers, people and
pax, zero hits."* That was measured over the **specification strip's** own
`o-title`/`o-value` pairs, and the conclusion was written about the whole page.

MY Independence II is the counter-example. The site prints *guests not stated*
for it, and the page's own description says *"designed for just 20 guests"* --
in the body copy, in `<meta name="description">`, and in the JSON-LD `Product`
node. Prose rather than a field, which is exactly what `promote._guests`
already reads from liveaboard.com's vessel description for half the fleet.

So the question this probe answers is not "is it there" -- one page settles
that -- but the two that decide whether it may be used:

* **Coverage.** How many of the boats with no guest count get one from PADI,
  and where on the page it comes from.
* **Agreement.** Where our number and PADI's both exist, do they match? A
  fallback that contradicts the operator's own "Max guests" row on the boats
  that have one is not a fallback, it is a second opinion -- and this project
  does not publish two answers to one question.

Writes nothing. Run it on a runner, read what comes back, then parse.

    python3 tools/probe_padi_guests.py            # every mapped vessel
    python3 tools/probe_padi_guests.py --missing  # only the boats with no count
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from liveaboard.promote import GUESTS, MAX_GUESTS  # noqa: E402

HOST = "travel.padi.com"
UA = {"User-Agent": "Mozilla/5.0 (compatible; liveaboard-price-transparency/1.0)"}

META = re.compile(
    r"""<meta[^>]+name=['"]description['"][^>]+content=['"](.*?)['"]""", re.S | re.I)
DESC = re.compile(
    r"""<div[^>]+id=['"]description-text['"][^>]*>(.*?)</div>""", re.S | re.I)
LD = re.compile(r"""<script[^>]+application/ld\+json[^>]*>(.*?)</script>""", re.S | re.I)
TAGS = re.compile(r"<[^>]+>")


def unescape(text: str) -> str:
    from html import unescape as _u

    return " ".join(_u(text).split())


def count(text: str | None) -> int | None:
    """`promote._guests`, inlined so the probe reads what the parser would."""
    if not text:
        return None
    for pattern in GUESTS:
        match = pattern.search(text)
        if match:
            value = int(match.group(1))
            if 0 < value <= MAX_GUESTS:
                return value
    return None


def ld_description(html: str) -> str | None:
    for block in LD.findall(html):
        try:
            node = json.loads(block)
        except Exception:  # noqa: BLE001 - a probe reports, it does not fail
            continue
        for candidate in node if isinstance(node, list) else [node]:
            if isinstance(candidate, dict) and candidate.get("description"):
                return unescape(str(candidate["description"]))
    return None


def body_text(html: str) -> str:
    stripped = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S | re.I)
    return unescape(TAGS.sub(" ", stripped))


def fetch(slug: str, country: str) -> str | None:
    url = f"https://{HOST}/liveaboard/{country}/{slug}/"
    try:
        request = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(request, timeout=35) as response:
            return response.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        print(f"  ! {slug}: {exc}")
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--missing", action="store_true",
                        help="only the boats the dataset has no guest count for")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    aliases = json.loads((ROOT / "data" / "padi_aliases.json").read_text("utf-8"))
    padi = json.loads((ROOT / "data" / "padi.json").read_text("utf-8"))
    dataset = json.loads((ROOT / "data" / "egypt-2027.json").read_text("utf-8"))

    ours = {boat["id"]: boat.get("guests") for boat in dataset["boats"]}
    fallback = aliases.get("country", "egypt")
    # `aliases` is the only map from our id to PADI's slug. `padi_only` lists
    # *our* ids for the boats PADI alone sells berths on, and those ids are in
    # `aliases` too -- reading it as a list of PADI slugs fetches pages for
    # vessels that do not exist under that name, which answer 200 and say
    # nothing.
    pairs = [(our_id, padi_slug) for our_id, padi_slug in aliases["aliases"].items()
             if our_id in ours]
    if args.missing:
        pairs = [(o, p) for o, p in pairs if not ours.get(o)]
    if args.limit:
        pairs = pairs[: args.limit]

    print(f"{len(pairs)} vessel page(s)\n")
    agree = differ = only_padi = silent = 0
    for our_slug, padi_slug in sorted(pairs):
        country = ((padi.get("vessels") or {}).get(padi_slug) or {}).get("country") or fallback
        html = fetch(padi_slug, country)
        if html is None:
            continue
        described = DESC.search(html)
        readings = {
            # The vessel's own description block, which is the bounded region
            # `promote._guests` reads on the other seller. The rest are here to
            # say what a looser parse would have bought.
            "desc": count(unescape(TAGS.sub(" ", described.group(1))) if described else None),
            "ld": count(ld_description(html)),
            "meta": count(unescape(META.search(html).group(1)) if META.search(html) else None),
            "body": count(body_text(html)),
        }
        theirs = readings["desc"]
        mine = ours.get(our_slug)
        if mine and theirs:
            verdict = "agree" if mine == theirs else "DIFFER"
            agree += verdict == "agree"
            differ += verdict == "DIFFER"
        elif theirs:
            verdict = "fills"
            only_padi += 1
        else:
            verdict = "silent"
            silent += 1
        where = ",".join(k for k, v in readings.items() if v) or "-"
        print(f"  {verdict:6} {our_slug:38} ours={mine or '-':>4}  padi={theirs or '-':>4}  [{where}]")

    print(f"\nagree {agree}   differ {differ}   fills a hole {only_padi}   silent {silent}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
