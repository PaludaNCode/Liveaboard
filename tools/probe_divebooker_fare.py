#!/usr/bin/env python3
"""What divebooker's `Offer.price` actually counts.

`promote` withholds every divebooker fare, and this is the probe that can end
that. The reason for withholding is one row: Red Sea Aggressor IV on
2027-07-24 states **5,398 USD against our 2,699** for the same seven nights —
exactly twice — while 57 of the 70 comparable in-season rows agree to the
cent. So the figure is a per-person berth on most rows and something else on
at least one, and a figure whose unit this site cannot state is a figure it
does not publish.

Three readings would each explain it, and they need opposite answers:

* **Two cabin classes.** The page states more than one offer for that sailing
  and the book kept whichever came first. Then the rule is *take the lowest*,
  the way the advertised price is the bottom of a cabin ladder.
* **A cabin rather than a berth.** The figure is for two people. Then nothing
  can be published without knowing which rows are which.
* **A mistake on the seller's page.** Then it is one row and the other 976
  stand.

`AggregateOffer` is the witness: it states `lowPrice`, `highPrice` and
`offerCount` for the whole vessel. If the page carries one offer per sailing,
`offerCount` equals the sailing count; if the disputed fare is a second class,
the count exceeds it.

So this prints, per vessel: the AggregateOffer, the offer count against the
sailing count, and every offer whose start date is one this run was asked
about — with its name, its price and what it is an offer for. Text only, no
payload to carry back.

Writes nothing.

    python3 tools/probe_divebooker_fare.py --dates 2027-07-24,2027-07-31
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from liveaboard.scrape import divebooker_com as db, jsonld  # noqa: E402
from liveaboard.scrape.base import FetchBlocked, PoliteFetcher  # noqa: E402
from probe_divebooker import repair_robots  # noqa: E402

#: The two hulls every disagreement sits on, and one that agrees everywhere as
#: the control. A probe pointed only at the anomaly cannot tell an anomaly
#: from the site's normal shape.
#:
#: **Named, never spelled.** The first version of this probe typed
#: `red-sea-aggressor-iv-haz285` and `blue-horizon-haz133` out of an earlier
#: run's samples, and both are other boats: haz285 is Red Sea Aggressor *II*
#: and haz133 is *Perjuangan Liveaboard, Indonesia*. The id is the site's and
#: is not derivable from a name, so it comes out of the committed book — which
#: got its own from the country page's links, which is the whole discipline.
VESSELS = "red-sea-aggressor-iv,red-sea-aggressor-ii,bella-2"
BOOK = Path("data/divebooker.json")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vessels", default=VESSELS,
                        help="boat slugs as the committed book names them")
    parser.add_argument("--book", default=BOOK, type=Path)
    parser.add_argument("--dates", default="2027-07-24,2027-07-31,2027-05-08",
                        help="start dates to print every offer for")
    parser.add_argument("--delay", type=float, default=5.0)
    parser.add_argument("--snapshots", default=Path("data/snapshots"), type=Path)
    args = parser.parse_args()

    fetcher = PoliteFetcher(snapshot_dir=args.snapshots, delay=args.delay)
    for host in (db.HOST, f"www.{db.HOST}"):
        repair_robots(fetcher, host)
    wanted = {d.strip() for d in args.dates.split(",") if d.strip()}

    import json
    vessels = json.loads(args.book.read_text(encoding="utf-8")).get("vessels") or {}

    for slug in args.vessels.split(","):
        slug = slug.strip()
        record = vessels.get(slug)
        if not record or not record.get("divebooker_id"):
            print(f"\n== {slug}: not in {args.book}, so there is no id to ask "
                  f"for — and typing one lands on another boat ==")
            continue
        path = f"/{slug}-{record['divebooker_id']}"
        try:
            result = fetcher.get(f"https://{db.HOST}{path}")
        except FetchBlocked as exc:
            print(f"BLOCKED {path}: {exc}")
            continue

        book = db.vessel(result.body, path)
        starts = Counter(row.start for row in book.departures)
        print(f"\n== {path} ({book.name}) ==")
        print(f"  sailings read        : {len(book.departures)}")
        print(f"  dates stated twice   : "
              f"{[d for d, n in starts.items() if n > 1] or 'none'}")

        for node in jsonld.walk_documents(result.body):
            if node.get("@type") == "AggregateOffer":
                print(f"  AggregateOffer       : low {node.get('lowPrice')} "
                      f"high {node.get('highPrice')} {node.get('priceCurrency')} "
                      f"over {node.get('offerCount')} offer(s)")
                break

        # Every offer for the dates asked about, however the page nests it.
        # Printed rather than folded: the fold is what is under suspicion.
        for node in jsonld.walk_documents(result.body):
            if node.get("@type") != "Offer":
                continue
            trip = node.get("itemOffered")
            trip = trip[0] if isinstance(trip, list) else trip
            event = (trip or {}).get("subjectOf") if isinstance(trip, dict) else None
            event = event[0] if isinstance(event, list) else event
            start = (event or {}).get("startDate") if isinstance(event, dict) else None
            if start is None and node.get("validThrough"):
                start = node["validThrough"]
            if start not in wanted:
                continue
            print(f"    {start}  {node.get('price')} {node.get('priceCurrency')}"
                  f"  valid {node.get('validFrom') or '-'}..{node.get('validThrough') or '-'}"
                  f"  name={node.get('name')!r}")

        for event, offer in [(n, n.get("offers")) for n in jsonld.walk_documents(result.body)
                             if n.get("@type") == "Event" and n.get("offers")]:
            if event.get("startDate") not in wanted:
                continue
            one = offer[0] if isinstance(offer, list) else offer
            print(f"    {event.get('startDate')}  event-level "
                  f"{one.get('price')} {one.get('priceCurrency')}  "
                  f"url={event.get('url')}  id={event.get('id')}")

    print("\n  What this decides: whether a divebooker fare can be published at "
          "all,\n  and if so under which rule. Until then `promote` writes "
          "`fares: withheld`.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
