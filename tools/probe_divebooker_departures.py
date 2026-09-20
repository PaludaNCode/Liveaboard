#!/usr/bin/env python3
"""What one divebooker departure states, and which hulls are Egyptian.

`probe_divebooker.py` settled that the money is in the served bytes: a vessel
page carries `Event` nodes with an `Offer` apiece. It did not settle what those
nodes *say*, and that is the question stage 0 of `docs/plan-divebooker.md`
turns on — a third seller needs a fare, a currency and a date it can be keyed
on; a third opinion needs none of that and fills blanks instead.

So this reads the Egypt country page for the fleet, opens a few of those
vessels, and prints a **census** rather than one example: which keys appear on
every departure, which on some, and what the values look like. A parser written
against a single node is a parser written against one node's coincidences --
the fee parser proved against one hand-trimmed fixture matched nothing on six
real trips.

Three things it deliberately does not do. It does not guess a URL: the fleet
comes from the country page's own links. It does not read `/boatsearch`, which
robots.txt refuses. And it writes nothing.

    python3 tools/probe_divebooker_departures.py [--vessels 3] [--country egypt-daz3881]
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from liveaboard.scrape import jsonld  # noqa: E402
from liveaboard.scrape.base import FetchBlocked, PoliteFetcher  # noqa: E402
from probe_divebooker import FLAT_ID, repair_robots  # noqa: E402

HOST = "divebooker.com"
HULL = re.compile(r'href="(?:https://[^/"]+)?(/[a-z0-9-]+-haz\d+)"')

#: Printed in full for one node of each; the rest of the run is counts. Chosen
#: because these are the four the dataset would have to fill from.
SHOW = ("Event", "Offer", "TouristTrip", "Place")

TRUNCATE = 120


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
    sometimes = sorted((k, n) for k, n in counts.items() if n < total)
    print(f"  {label}: {total} node(s)")
    print(f"    on every one : {', '.join(always) if always else '—'}")
    for key, n in sometimes:
        print(f"    on {n:>3}/{total}    : {key}")


def show_one(node: dict, label: str) -> None:
    print(f"    one {label}:")
    for key, value in node.items():
        text = str(value)
        if len(text) > TRUNCATE:
            text = text[:TRUNCATE] + "…"
        print(f"      {key:<22} {text}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--country", default="egypt-daz3881",
                        help="the country page whose fleet to read")
    parser.add_argument("--vessels", type=int, default=3)
    parser.add_argument("--delay", type=float, default=5.0)
    parser.add_argument("--snapshots", default=Path("data/snapshots"), type=Path)
    args = parser.parse_args()

    fetcher = PoliteFetcher(snapshot_dir=args.snapshots, delay=args.delay)
    base = f"https://{HOST}"
    for host in (HOST, f"www.{HOST}"):
        repair_robots(fetcher, host)

    def get(url: str):
        try:
            return fetcher.get(url)
        except FetchBlocked as exc:
            print(f"  BLOCKED {url}: {exc}")
        except Exception as exc:  # noqa: BLE001 - one dead page must not end the run
            print(f"  FAILED  {url}: {type(exc).__name__}: {exc}")
        return None

    print(f"== the fleet on /{args.country} ==")
    page = get(f"{base}/{args.country}")
    if page is None:
        return 1
    hulls = list(dict.fromkeys(HULL.findall(page.body)))
    print(f"  {len(page.body) / 1024:.0f} KB · {len(hulls)} distinct -haz link(s)")
    for path in hulls[:12]:
        print(f"    {path}")
    if len(hulls) > 12:
        print(f"    … and {len(hulls) - 12} more")
    if not hulls:
        print("  The country page links no hull. That is a finding, not a bug:")
        print("  the fleet is reached some other way, and the next question is")
        print("  which — the sitemap knows 516 of them and does not say where.")
        return 0

    every_event: list[dict] = []
    for path in hulls[: args.vessels]:
        result = get(base + path)
        if result is None:
            continue
        by_type: dict[str, list[dict]] = {}
        for node in jsonld.walk_documents(result.body):
            raw = node.get("@type")
            for name in raw if isinstance(raw, list) else [raw]:
                if isinstance(name, str):
                    by_type.setdefault(name, []).append(node)

        slug = FLAT_ID.match(path.lstrip("/"))
        print(f"\n== {path} ({slug.group('slug') if slug else path}) ==")
        print(f"  {len(result.body) / 1024:.0f} KB · types: "
              f"{', '.join(f'{k}×{len(v)}' for k, v in sorted(by_type.items()))}")
        for label in SHOW:
            census(by_type.get(label, []), label)
            if by_type.get(label):
                show_one(by_type[label][0], label)
        every_event.extend(by_type.get("Event", []))

    print("\n== across every departure read ==")
    census(every_event, "Event")
    currencies = Counter()
    for event in every_event:
        offer = event.get("offers")
        for one in (offer if isinstance(offer, list) else [offer]):
            if isinstance(one, dict) and one.get("priceCurrency"):
                currencies[one["priceCurrency"]] += 1
    print(f"  currencies stated: {dict(currencies) or 'none'}")
    print("\n  What this decides: a third seller needs a fare, a currency and a")
    print("  date to key on. Whatever is missing above is what it cannot be.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
