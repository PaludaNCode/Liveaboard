#!/usr/bin/env python3
"""Does a seller raise a sailing's price as its berths sell? Read off the git log.

Every reading is a commit: `data/egypt-2027.json` holds each sailing's quoted
fare and, per seller, the berths left on it, and the log has kept one a day
since 2026-08-27. So the question #6 left behind is answerable with no crawl
and no new file: walk the log, set each sailing's readings in order, and ask
whether a fare moves more often after its places fall than after they do not.

**A finding, never a feature.** The history view reports what the sellers did
and does not grade it (CLAUDE.md, *The history view shows what happened*), so
what this prints goes to `docs/fill-and-price.md` and nowhere near the page.
It writes nothing.

Six rules, each a mistake the change log has already made once:

* **A sailing is `(seller, boat, start)`**, never the departure id. Ids moved
  under sailings nobody touched -- 717 at once when the suffix stopped being
  positional -- and a second seller listing a week moves the id too. Two rows
  one seller starts on one day are a pairing nothing can make, and both are
  set aside: `padi_key`'s rule.
* **A reading is that seller's own.** A fare counts only where the seller's
  read date for it moved; a re-promote of the same inputs moved this code and
  not the market, and a carried row keeps the day it was read. Places count
  only where that seller's ladder was read again (`BLOCK_READ`, or the book's
  own `*_berths_read`).
* **One seller at a time.** Three crawls on three days with three fares; a
  fare is only ever set against the same seller's places.
* **Quoted amount, own currency** -- and that is not enough on its own. The
  first two sellers quote in dollars a fare the operator set in euro, so a
  boat's whole season moves with the rate. A move every sailing of the boat
  made by one ratio between the same two readings is the boat's (a
  conversion or a repricing of its year) and never a fill, so a sailing moves
  only by what it moved **beyond its boat's median**. Under `MIN_MOVE` is the
  source re-rounding, as in `changes`.
* **A markdown is not a rise.** A step where this seller's sale started,
  ended or changed rate is set aside.
* **Unread is not sold out.** A sailing absent from a reading has no step
  across the gap; a count the seller did not state is not a fall.

The window is the pair of readings the places moved across and the next pair,
because a reprice the morning after a booking is the shape being asked about.
The control is the same window on a sailing whose places did not move -- and,
the one that decides it, on a sailing whose places *grew*: a seller that
touches a sailing moves its price and its count together, and only a rise more
likely after a fall than after either is evidence a fill drove it.

    python3 tools/fill_and_price.py [--rev HEAD] [--since 2026-08-27]

The answer is printed last, where a probe's answer is read from.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from liveaboard.changes import MIN_MOVE  # noqa: E402

DATASET = "data/egypt-2027.json"

SELLERS = ("liveaboard.com", "padi.com", "divebooker.com")
"""The fare each seller states sits in a field of its own beside the row's."""

OWN_FARE = {
    "padi.com": ("padi_price", "padi_provenance"),
    "divebooker.com": ("divebooker_price", "divebooker_provenance"),
}
"""Where a seller's fare sits when the row's own `price` is another seller's."""

BOOK_READ = {
    "liveaboard.com": "berths_read",
    "padi.com": "padi_berths_read",
    "divebooker.com": "divebooker_berths_read",
}
"""The day a seller's ladders were read, for a block that carries no day."""

MIN_SIBLINGS = 3
"""Sailings of one boat re-read across one pair of readings, at the least.

Below it the boat's median is the sailing's own move, so a conversion and a
reprice of one sailing cannot be told apart and the step is set aside rather
than guessed at.
"""

FARE_ASIDE = ("currency", "markdown", "boat-wide", "too few siblings", "unpriced")
"""Fare verdicts that say nothing about this sailing, in the order printed."""

PLACES_ROWS = ("fell", "sold out", "unchanged", "grew")
"""The places verdicts the table is built over, in the order printed."""

LADDER_IS_THE_FARE = {"liveaboard.com"}
"""Sellers whose advertised fare is the bottom rung of the ladder they state.

liveaboard.com's is, on every sailing read. divebooker's rooms overlap -- three
options on one Argo Egypt sailing each state the same 8 berths -- so its
cheapest room selling out says nothing about the rest, and PADI states no
ladder at all.
"""


@dataclass(frozen=True, slots=True)
class Reading:
    """What one seller said about one sailing, as one snapshot carries it."""

    day: str
    fare_read: str
    amount: float | None
    currency: str
    places_read: str | None
    places: int | None
    sale: tuple[bool, int | None]
    ladder: tuple[tuple[str, float, int | None], ...] | None


@dataclass(frozen=True, slots=True)
class Step:
    """Two consecutive readings of one sailing by one seller."""

    seller: str
    boat: str
    start: str
    before: Reading
    after: Reading


def readings(dataset: dict[str, Any], day: str
             ) -> tuple[dict[tuple[str, str, str], Reading], set[tuple[str, str, str]]]:
    """Every seller's reading of every sailing in one dataset.

    Returns the readings and the keys set aside because one seller starts two
    rows on one boat on one day.
    """
    boats = {i["id"]: i.get("boat_id") for i in dataset.get("itineraries") or []}
    pool = dataset.get("sellers") or []
    names = dataset.get("cabin_names") or []
    out: dict[tuple[str, str, str], Reading] = {}
    clashes: set[tuple[str, str, str]] = set()
    for dep in dataset.get("departures") or []:
        boat = boats.get(dep.get("itinerary_id"))
        if not boat:
            continue
        blocks = {pool[b[0]]: b for b in dep.get("berths") or []
                  if isinstance(b, list) and b and isinstance(b[0], int)
                  and b[0] < len(pool)}
        sale = dep.get("sale") or {}
        for seller in SELLERS:
            if (dep.get("provenance") or {}).get("source_id") == seller:
                price, prov = dep.get("price") or {}, dep.get("provenance") or {}
            elif seller in OWN_FARE and OWN_FARE[seller][0] in dep:
                price = dep[OWN_FARE[seller][0]] or {}
                prov = dep.get(OWN_FARE[seller][1]) or {}
            else:
                continue
            key = (seller, boat, dep.get("start", ""))
            if key in out or key in clashes:
                out.pop(key, None)
                clashes.add(key)
                continue
            block = blocks.get(seller)
            on = seller in pool and pool.index(seller) in (sale.get("sellers") or [])
            ladder = None
            if block and len(block) > 2 and isinstance(block[2], list):
                ladder = tuple(
                    (names[c[0]] if isinstance(c[0], int) and c[0] < len(names)
                     else str(c[0]), c[1], c[2])
                    for c in block[2])
            out[key] = Reading(
                day=day,
                fare_read=prov.get("retrieved") or "",
                amount=price.get("amount"),
                currency=price.get("currency") or "",
                places_read=((block[4] if len(block) > 4 else dataset.get(BOOK_READ[seller]))
                             if block else None),
                places=block[3] if block and len(block) > 3 else None,
                sale=(on, sale.get("pct") if on else None),
                ladder=ladder,
            )
    return out, clashes


def series(snapshots: Iterable[tuple[str, dict[str, Any]]]
           ) -> tuple[dict[tuple[str, str, str], list[Reading]], Counter]:
    """Each sailing's readings in order, one per day its seller read the fare.

    A snapshot in which the fare was not read again replaces the last reading
    rather than adding one: its fare is the same reading, and its places may
    be fresher. Returns the series and how many snapshot-rows were set aside
    as clashes, per seller.
    """
    out: dict[tuple[str, str, str], list[Reading]] = defaultdict(list)
    clashed: Counter = Counter()
    for day, dataset in snapshots:
        found, clashes = readings(dataset, day)
        for key in clashes:
            clashed[key[0]] += 1
        for key, reading in found.items():
            rows = out[key]
            if rows and rows[-1].fare_read == reading.fare_read:
                rows[-1] = reading
            else:
                rows.append(reading)
    return dict(out), clashed


def steps(by_sailing: dict[tuple[str, str, str], list[Reading]]) -> list[Step]:
    """Every pair of consecutive readings, in each sailing's own order."""
    return [Step(seller, boat, start, a, z)
            for (seller, boat, start), rows in sorted(by_sailing.items())
            for a, z in zip(rows, rows[1:])]


def places_verdict(step: Step) -> str:
    """What happened to the berths left, by the seller's own count."""
    a, z = step.before, step.after
    if not a.places_read or not z.places_read or a.places_read == z.places_read:
        return "not re-read"
    if a.places is None or z.places is None:
        return "not stated"
    if z.places < a.places:
        return "sold out" if z.places == 0 else "fell"
    return "grew" if z.places > a.places else "unchanged"


def fare_verdicts(all_steps: list[Step]) -> list[str]:
    """What the fare did on each step, beyond what its boat did."""
    ratios: dict[tuple[str, str, str, str], list[float]] = defaultdict(list)
    for s in all_steps:
        a, z = s.before, s.after
        if a.currency == z.currency and a.amount and z.amount:
            ratios[(s.seller, s.boat, a.fare_read, z.fare_read)].append(z.amount / a.amount)
    out = []
    for s in all_steps:
        a, z = s.before, s.after
        if a.currency != z.currency:
            out.append("currency")
        elif not a.amount or not z.amount:
            out.append("unpriced")
        elif a.sale != z.sale:
            out.append("markdown")
        elif abs(z.amount - a.amount) < MIN_MOVE:
            out.append("same")
        else:
            boat = ratios[(s.seller, s.boat, a.fare_read, z.fare_read)]
            if len(boat) < MIN_SIBLINGS:
                out.append("too few siblings")
                continue
            beyond = z.amount - a.amount * statistics.median(boat)
            if abs(beyond) < MIN_MOVE:
                out.append("boat-wide")
            else:
                out.append("rose" if beyond > 0 else "fell")
    return out


def sold_through(step: Step) -> bool:
    """The cheapest rooms sold out between the readings while a dearer one sells.

    Which rooms are cheapest is asked of the earlier ladder alone: its rungs
    are converted into the display currency at one rate, so the minimum
    survives the conversion, where a rung set against a later ladder's would
    drift with the rate. Those rooms are then looked up by name.
    """
    a, z = step.before.ladder, step.after.ladder
    if not a or not z:
        return False
    low = min(price for _, price, _ in a)
    cheapest = {name for name, price, _ in a if price == low}
    now = {name: places for name, _, places in z}
    return (all((places or 0) > 0 for name, price, places in a if name in cheapest)
            and all(now.get(name) == 0 for name in cheapest)
            and any((places or 0) > 0 for name, _, places in z if name not in cheapest))


@dataclass
class Tally:
    """The answer for one seller."""

    table: Counter
    places: Counter
    rises: list[tuple[Step, Step]]
    through: Counter

    @staticmethod
    def empty() -> "Tally":
        return Tally(Counter(), Counter(), [], Counter())


def tally(all_steps: list[Step]) -> dict[str, Tally]:
    """Per seller: what the fare did after each kind of places move."""
    verdicts = fare_verdicts(all_steps)
    following: dict[int, int] = {}
    for i in range(len(all_steps) - 1):
        a, z = all_steps[i], all_steps[i + 1]
        if (a.seller, a.boat, a.start) == (z.seller, z.boat, z.start):
            following[i] = i + 1
    out: dict[str, Tally] = defaultdict(Tally.empty)
    for i, step in enumerate(all_steps):
        t = out[step.seller]
        places = places_verdict(step)
        t.places[places] += 1
        if step.seller in LADDER_IS_THE_FARE and sold_through(step):
            t.through[verdicts[i]] += 1
        if places not in PLACES_ROWS:
            continue
        own = verdicts[i]
        if own in FARE_ASIDE:
            t.table[(places, own)] += 1
            continue
        then = verdicts[following[i]] if i in following else "same"
        outcome = own if own in ("rose", "fell") else then if then in ("rose", "fell") else "neither"
        t.table[(places, outcome)] += 1
        if outcome == "rose" and places in ("fell", "sold out"):
            t.rises.append((step, step if own == "rose" else all_steps[following[i]]))
    return dict(out)


def rate(t: Tally, places: str, outcome: str) -> tuple[int, int]:
    """How many steps of one places verdict ended in one outcome, of how many counted."""
    counted = sum(t.table[(places, o)] for o in ("rose", "fell", "neither"))
    return t.table[(places, outcome)], counted


def render(tallies: dict[str, Tally], *, first: str, last: str, days: int,
           clashed: Counter, limit: int = 20) -> str:
    lines = [f"{days} day(s) of {DATASET}, {first} to {last}; one snapshot a day, "
             f"the day's last commit"]
    verdict = []
    for seller in SELLERS:
        t = tallies.get(seller)
        if t is None:
            continue
        lines.append(f"\n== {seller}")
        lines.append("  places across a pair of readings: "
                     + ", ".join(f"{k} {t.places[k]}" for k in
                                 (*PLACES_ROWS, "not re-read", "not stated")))
        if clashed[seller]:
            lines.append(f"  {clashed[seller]} row(s) set aside: two sailings one day")
        lines.append(f"  {'places':10} {'counted':>8} {'rose':>6} {'fell':>6} "
                     f"{'neither':>8}  set aside")
        for places in PLACES_ROWS:
            rose, counted = rate(t, places, "rose")
            aside = ", ".join(f"{k} {t.table[(places, k)]}" for k in FARE_ASIDE
                              if t.table[(places, k)])
            lines.append(f"  {places:10} {counted:>8} {rose:>6} "
                         f"{t.table[(places, 'fell')]:>6} "
                         f"{t.table[(places, 'neither')]:>8}  {aside or '-'}")
        if t.through:
            lines.append("  cheapest rooms sold out while a dearer one sells: "
                         f"{sum(t.through.values())}, and the fare then "
                         + ", ".join(f"{k} {v}" for k, v in sorted(t.through.items())))
        for fill, rise in t.rises[:limit]:
            a, z = fill.before, fill.after
            ra, rz = rise.before, rise.after
            lines.append(f"  rose after a fill: {fill.boat} {fill.start}, "
                         f"places {a.places} -> {z.places} ({a.places_read} -> "
                         f"{z.places_read}), fare {ra.amount:g} -> {rz.amount:g} "
                         f"{rz.currency} ({ra.fare_read} -> {rz.fare_read})")
        if len(t.rises) > limit:
            lines.append(f"  ... and {len(t.rises) - limit} more")

        # A fill is either kind of fall; sold out is kept apart in the table
        # because a whole sailing going at once is a charter more often than
        # the berth-by-berth selling the question is about.
        fills = [rate(t, "fell", "rose"), rate(t, "sold out", "rose")]
        f_hit, f_n = sum(h for h, _ in fills), sum(n for _, n in fills)
        (g_hit, g_n), (h_hit, h_n) = rate(t, "grew", "rose"), rate(t, "unchanged", "rose")
        f_rate, g_rate, h_rate = (h / n if n else 0.0 for h, n in
                                  ((f_hit, f_n), (g_hit, g_n), (h_hit, h_n)))
        if not f_n:
            answer = "no fill was read twice"
        elif f_rate <= g_rate:
            answer = "no likelier after a fill than after places grew"
        elif f_rate <= h_rate:
            answer = "no likelier after a fill than where places held"
        else:
            answer = "likelier after a fill than after either"
        verdict.append(
            f"{seller}: rose after {f_hit} of {f_n} fills ({f_rate:.1%}), "
            f"after {g_hit} of {g_n} where places grew ({g_rate:.1%}), "
            f"after {h_hit} of {h_n} where they held ({h_rate:.2%}) -- {answer}")
    lines.append("\n== finding")
    lines.extend(f"  {line}" for line in verdict)
    return "\n".join(lines)


def snapshots(rev: str, since: str) -> Iterable[tuple[str, dict[str, Any]]]:
    """The day's last commit of the dataset, for each day it was committed."""
    log = subprocess.run(
        ["git", "log", "--format=%H %ad", "--date=short", rev, "--", DATASET],
        capture_output=True, text=True, check=True).stdout.split("\n")
    last: dict[str, str] = {}
    for line in log:
        if line.strip():
            sha, day = line.split()
            last.setdefault(day, sha)
    for day, sha in sorted(last.items()):
        if day < since:
            continue
        body = subprocess.run(["git", "show", f"{sha}:{DATASET}"],
                              capture_output=True, text=True, check=True).stdout
        yield day, json.loads(body)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--rev", default="HEAD")
    parser.add_argument("--since", default="")
    args = parser.parse_args()
    if subprocess.run(["git", "rev-parse", "--is-shallow-repository"],
                      capture_output=True, text=True).stdout.strip() == "true":
        print("::warning::a shallow clone holds part of the log; "
              "git fetch --unshallow first")
    days: list[str] = []

    def walked() -> Iterable[tuple[str, dict[str, Any]]]:
        for day, dataset in snapshots(args.rev, args.since):
            days.append(day)
            yield day, dataset
    by_sailing, clashed = series(walked())
    if not days:
        print("no commit of the dataset in range")
        return 1
    print(render(tally(steps(by_sailing)), first=days[0], last=days[-1],
                 days=len(days), clashed=clashed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
