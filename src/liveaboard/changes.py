"""What moved between two published datasets.

The refresh commits a whole dataset every night and says nothing about what
changed. Five new departures show up as ``886`` where yesterday said ``881``,
and a two-hundred-euro rise is a two-megabyte JSON diff. For a project whose
whole argument is that advertised prices hide things, the change is the story
and it was the one thing the pipeline never reported.

Four distinctions decide whether the report is worth reading:

* **A sold-out departure and a withdrawn one are different events.** One still
  exists and cannot be booked; the other is gone. ``availability`` says which,
  and merging them would report cancellations that never happened.

* **A euro price moves when the ECB moves.** Every fare is quoted in dollars
  and the site shows euro, so comparing display amounts reports a change on
  every vessel on any day the rate shifts. Comparison is on the *quoted*
  amount in its own currency; the FX move is reported once, separately.

* **A vessel missing from the crawl has not cancelled its season.** A page that
  500s, or a capped run, removes every departure that vessel sells. Reporting
  eighty withdrawals when a fetch failed is worse than reporting nothing, so a
  vessel that loses *all* its departures at once is called out as a vessel that
  went missing rather than as eighty separate losses.

* **A fee is a property of the vessel**, so a changed park fee shows up on
  every sailing that boat runs. Fee changes are reported per vessel, once.

* **A parser learning to read a field is not the world changing.** The first
  run after ``availability`` was parsed compared 886 empty values against real
  ones and reported 126 sailings as having just sold out. They had not; nobody
  had looked before. A field absent on every departure of the older dataset is
  reported as newly read, and its transitions are not counted at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

Dataset = dict[str, Any]

MIN_MOVE = 5.0
"""The floor for a price move worth a reader's attention.

Nothing under five on a four-figure berth is a decision anyone would change,
and most of what sits below it is not a decision anyone made: on the
2026-08-28 refresh 174 fares "changed" and every single one of them by exactly
-1, which is the source re-rounding. Left in, they filled both price blocks
and pushed the real moves past the cap.

The comparison is on the fare in the currency the operator quoted it in, so
nothing here is an exchange-rate artefact -- an FX move is reported on its own
line and never as a reprice.

Suppressed moves are still counted and still stated. A report that quietly
drops rows is the failure this project exists to correct in other people.
"""

MISSING_VESSEL_MIN = 3
"""Departures a vessel must lose *all* of before it counts as gone missing.

A boat selling one or two trips can legitimately sell out of both, and calling
that a failed fetch would hide a real change. Three is where "every single one
vanished at once" stops looking like a coincidence.
"""


@dataclass(frozen=True, slots=True)
class PriceMove:
    """One departure whose quoted fare changed."""

    departure_id: str
    boat: str
    title: str
    start: str
    was: float
    now: float
    currency: str

    @property
    def delta(self) -> float:
        return self.now - self.was

    @property
    def pct(self) -> float:
        return (self.delta / self.was * 100) if self.was else 0.0


@dataclass(frozen=True, slots=True)
class FxMove:
    """One exchange rate that moved, and that something on the page uses."""

    currency: str
    was: float
    now: float

    @property
    def pct(self) -> float:
        return ((self.now - self.was) / self.was * 100) if self.was else 0.0


@dataclass(frozen=True, slots=True)
class Departed:
    """A departure that is new, sold out, or withdrawn."""

    departure_id: str
    boat: str
    title: str
    start: str
    price: float | None
    currency: str
    itinerary_id: str = ""
    """The trip this sailing is one departure of. Carried for the pairing in
    `compare` and deliberately not published: the page has the trip's title,
    and an id nobody reads is payload on every row of every report."""
    sellers: tuple[str, ...] = ()
    """Which site published this sailing, by host, in the dataset it came from.

    A change report with two sellers in it and no seller on any row leaves the
    reader to guess whose event this is: a sailing that appears because PADI
    started listing it is a different fact from one liveaboard.com added, and
    one that goes from a book the other seller still carries is not a
    withdrawal at all. Read off the departure's own provenance rather than
    re-derived, so it says what the row said.
    """


@dataclass(frozen=True, slots=True)
class Relisted:
    """One sailing that only appears to have arrived and gone away.

    A departure id is identity here, so the diff keys on it -- and two things
    change an id under a sailing nobody withdrew:

    * **The seller changed.** liveaboard.com's rows are `blue-2027-05-06-0`
      and a sailing PADI alone lists is `blue-2027-05-06-padi`, so the day
      liveaboard.com starts listing a week PADI had been carrying, one id
      leaves and another arrives.
    * **A sibling moved.** The suffix is the Event node's *position* on the
      vessel-month page it was read from, so one sailing inserted earlier in
      that list renumbers every later one.

    Twelve of Blue's sailings did the first and two did the second in a single
    refresh, published under *New departures* and *Withdrawn* at once -- 24
    lines of news for a fleet that did nothing -- and with the two fares side
    by side, where 1,645 USD against 1,420 EUR reads as a €225 cut rather than
    as a currency the seller re-quoted in.

    So it is one row here, and it says what actually moved. What it must never
    swallow is a boat that really did swap one trip for another on a date:
    that is a different itinerary, and it stays two events.
    """

    boat: str
    start: str
    title: str
    was_title: str
    sellers: tuple[str, ...]
    was_sellers: tuple[str, ...]
    price: float | None
    was_price: float | None
    currency: str
    was_currency: str

    @property
    def sellers_moved(self) -> bool:
        return self.sellers != self.was_sellers

    @property
    def repriced(self) -> bool:
        """A fare that moved in the same currency. A currency switch is not one.

        The same rule the price blocks keep: `changes` refuses to call
        1,645 USD -> 1,420 EUR a price move, and a row that arrived from the
        other seller's book is that case by construction -- the two sellers
        quote in whatever each quotes in.
        """
        return (self.currency == self.was_currency
                and self.price is not None and self.was_price is not None
                and abs(self.price - self.was_price) >= MIN_MOVE)


@dataclass(frozen=True, slots=True)
class FeeMove:
    """One vessel's fee line that changed. Reported once, not once per sailing."""

    boat: str
    code: str
    was: str
    now: str


@dataclass(slots=True)
class Report:
    """Everything that moved, ready to render."""

    added: list[Departed] = field(default_factory=list)
    sold_out: list[Departed] = field(default_factory=list)
    withdrawn: list[Departed] = field(default_factory=list)
    returned: list[Departed] = field(default_factory=list)
    """Sold out yesterday, bookable today -- a cancellation freeing a berth."""
    relisted: list[Relisted] = field(default_factory=list)
    """Sailings that only appear to have arrived and gone. See `Relisted`."""
    renumbered: int = 0
    """Re-listings where the id is the only thing that moved -- counted, not
    listed, the way `price_rounding` is.

    The seller is the same, the fare is the same in the same currency, the trip
    is the same: nothing about the sailing changed and nothing about it is news.
    Stating the count is still not optional -- a report that drops rows without
    admitting it reads as "that was everything" -- and it is what makes the day
    the numbering scheme itself changes legible: 717 sailings kept everything
    but their id, in one line rather than 717."""
    price_up: list[PriceMove] = field(default_factory=list)
    price_down: list[PriceMove] = field(default_factory=list)
    fees: list[FeeMove] = field(default_factory=list)
    vessels_gone: list[str] = field(default_factory=list)
    """Vessels that lost every departure at once -- a failed fetch, most likely."""
    months_gone: list[str] = field(default_factory=list)
    """Vessel-months that emptied while the vessel kept selling other months."""
    vessels_new: list[str] = field(default_factory=list)
    price_rounding: int = 0
    """Fares that moved by less than ``MIN_MOVE`` -- counted, not listed."""
    availability_newly_read: bool = False
    """The older dataset stated availability nowhere, so no transition is real."""
    fx: list[FxMove] = field(default_factory=list)
    """Rates that moved *and* that a price on the page is quoted in."""

    @property
    def fx_moved(self) -> bool:
        return bool(self.fx)

    @property
    def is_quiet(self) -> bool:
        """Nothing a reader would want woken for."""
        return not any(
            (self.added, self.sold_out, self.withdrawn, self.returned,
             self.relisted, self.price_up, self.price_down, self.fees,
             self.vessels_gone, self.months_gone, self.vessels_new)
        )

    @property
    def only_renumbered(self) -> bool:
        """Quiet, and quiet for a reason worth printing rather than hiding."""
        return self.is_quiet and bool(self.renumbered)


def _index(dataset: Dataset) -> tuple[dict, dict, dict]:
    """Departures by id, itineraries by id, boats by id."""
    itineraries = {i["id"]: i for i in dataset.get("itineraries", [])}
    boats = {b["id"]: b for b in dataset.get("boats", [])}
    return ({d["id"]: d for d in dataset.get("departures", [])}, itineraries, boats)


def _describe(dep: dict, itineraries: dict, boats: dict) -> tuple[str, str]:
    """Boat name and trip title for one departure, however thin the data."""
    itinerary = itineraries.get(dep.get("itinerary_id"), {})
    boat = boats.get(itinerary.get("boat_id"), {})
    return (
        boat.get("name") or itinerary.get("boat_id") or "unknown vessel",
        itinerary.get("title") or itinerary.get("name") or "unknown trip",
    )


def _sellers(dep: dict) -> tuple[str, ...]:
    """Which sites published this sailing, by host.

    The berth price names its own source, and a row built from liveaboard.com
    carries PADI's provenance beside it wherever PADI sells the same date --
    which is the same pair the Seller column prints. Taken from there rather
    than worked out again: two answers to "who sells this" is exactly the
    drift this project keeps closing elsewhere.
    """
    hosts = {(dep.get("provenance") or {}).get("source_id")}
    if dep.get("padi_provenance"):
        hosts.add((dep["padi_provenance"] or {}).get("source_id") or "padi.com")
    return tuple(sorted(h for h in hosts if h))


def _as_departed(dep: dict, itineraries: dict, boats: dict) -> Departed:
    boat, title = _describe(dep, itineraries, boats)
    price = dep.get("price") or {}
    return Departed(
        departure_id=dep["id"],
        boat=boat,
        title=title,
        start=dep.get("start", ""),
        price=price.get("amount"),
        currency=price.get("currency", ""),
        itinerary_id=dep.get("itinerary_id", ""),
        sellers=_sellers(dep),
    )


def _sold_out(dep: dict) -> bool:
    """Whether the source says this sailing cannot be booked."""
    return (dep.get("availability") or "").lower() in {"sold_out", "soldout", "sold out"}


def _fee_summary(fee: dict) -> str:
    """One fee as a comparable string: what it costs and whether it is included."""
    amount = fee.get("amount")
    high = fee.get("amount_max")
    if fee.get("included"):
        return "included"
    if amount is None:
        return "listed, no price"
    span = f"{amount['amount']:g}"
    if high and high.get("amount") != amount.get("amount"):
        span += f"-{high['amount']:g}"
    return f"{span} {amount.get('currency', '')} / {fee.get('basis', 'per_trip')}".strip()


def _fees_by_boat(dataset: Dataset) -> dict[str, dict[str, str]]:
    """Each vessel's fee lines, folded once.

    Fees hang off itineraries, and a vessel sells several, so the same park fee
    appears many times. Taking the first itinerary's copy per code is enough:
    they come from one per-vessel fee book.
    """
    itineraries = dataset.get("itineraries", [])
    out: dict[str, dict[str, str]] = {}
    for itinerary in itineraries:
        boat = itinerary.get("boat_id")
        if boat is None:
            continue
        seen = out.setdefault(boat, {})
        for fee in itinerary.get("fees") or []:
            seen.setdefault(fee["code"], _fee_summary(fee))
    return out


def compare(before: Dataset, after: Dataset) -> Report:
    """Diff two published datasets on identity, not on position."""
    old_deps, old_its, old_boats = _index(before)
    new_deps, new_its, new_boats = _index(after)
    report = Report()

    # A vessel that lost every departure at once did not cancel its season; the
    # crawl most likely failed to reach it. Its losses are reported as one
    # missing vessel rather than as a row per sailing.
    def by_boat(deps: dict, its: dict) -> dict[str, set[str]]:
        grouped: dict[str, set[str]] = {}
        for dep in deps.values():
            boat = its.get(dep.get("itinerary_id"), {}).get("boat_id")
            if boat:
                grouped.setdefault(boat, set()).add(dep["id"])
        return grouped

    # The same failure one level down. A vessel page is fetched once per season
    # month, so one unreadable response empties that boat's month while the
    # other three come back fine -- and the vessel-level guard above never
    # fires, because the boat did not lose everything.
    #
    # This is not hypothetical. On 2026-08-28 fourteen vessel-month pages came
    # back with no structured data at all, and DUNE Longara's five May sailings
    # were reported as withdrawn while liveaboard.com was still selling every
    # one of them.
    def by_boat_month(deps: dict, its: dict) -> dict[tuple[str, str], set[str]]:
        grouped: dict[tuple[str, str], set[str]] = {}
        for dep in deps.values():
            boat = its.get(dep.get("itinerary_id"), {}).get("boat_id")
            month = (dep.get("start") or "")[:7]
            if boat and month:
                grouped.setdefault((boat, month), set()).add(dep["id"])
        return grouped

    old_by_boat = by_boat(old_deps, old_its)
    new_by_boat = by_boat(new_deps, new_its)
    vanished = {
        boat for boat, ids in old_by_boat.items()
        if len(ids) >= MISSING_VESSEL_MIN and not new_by_boat.get(boat)
    }
    report.vessels_gone = sorted(
        old_boats.get(b, {}).get("name") or b for b in vanished
    )
    report.vessels_new = sorted(
        new_boats.get(b, {}).get("name") or b
        for b in new_by_boat
        if b not in old_by_boat
    )

    old_months, new_months = by_boat_month(old_deps, old_its), by_boat_month(new_deps, new_its)
    blank_months = {
        (boat, month) for (boat, month), ids in old_months.items()
        if len(ids) >= MISSING_VESSEL_MIN
        and not new_months.get((boat, month))
        and boat not in vanished        # already reported as a missing vessel
        and new_by_boat.get(boat)       # still selling other months, so it is there
    }
    report.months_gone = sorted(
        f"{old_boats.get(boat, {}).get('name') or boat} {month}"
        for boat, month in blank_months
    )

    for dep_id, dep in new_deps.items():
        if dep_id not in old_deps:
            report.added.append(_as_departed(dep, new_its, new_boats))

    for dep_id, dep in old_deps.items():
        if dep_id in new_deps:
            continue
        boat = old_its.get(dep.get("itinerary_id"), {}).get("boat_id")
        if boat in vanished:
            continue  # already reported once, as a missing vessel
        if (boat, (dep.get("start") or "")[:7]) in blank_months:
            continue  # a month that went unread, not sailings that went away
        report.withdrawn.append(_as_departed(dep, old_its, old_boats))

    # THE SAME SAILING UNDER A NEW ID.
    #
    # See `Relisted`: an id carries the seller and the row's position on the
    # page it was read from, so a sailing nobody withdrew can leave under one
    # id and arrive under another. The diff keys on id and must -- that is what
    # identity means here -- so the pairing happens after it.
    #
    # Paired on the boat and the day, the same exact key `promote` merges the
    # two sources on: a date has no spelling. Three conditions, each narrow:
    #
    # * **One on each side.** A boat with two rows starting the same day is a
    #   pairing nothing here can make, and the `padi_key` rule applies: fold
    #   only where the key names exactly one.
    # * **The same trip, or a different seller.** Same itinerary is the same
    #   sailing re-identified; a different seller is the row changing books.
    # * Anything else stays two events. A boat that swapped one trip for
    #   another on a date really did withdraw one and add one, and this must
    #   not be the thing that hides it.
    def seated(rows: list[Departed]) -> dict[tuple[str, str], Departed]:
        seen: dict[tuple[str, str], Departed | None] = {}
        for row in rows:
            key = (row.boat, row.start)
            seen[key] = None if key in seen else row
        return {k: v for k, v in seen.items() if v is not None}

    arrived, left = seated(report.added), seated(report.withdrawn)
    moved_ids: set[str] = set()
    for key in sorted(set(arrived) & set(left)):
        now, was = arrived[key], left[key]
        same_trip = bool(now.itinerary_id) and now.itinerary_id == was.itinerary_id
        if not same_trip and now.sellers == was.sellers:
            continue
        moved_ids.update({now.departure_id, was.departure_id})
        row = Relisted(
            boat=now.boat, start=now.start,
            title=now.title, was_title=was.title,
            sellers=now.sellers, was_sellers=was.sellers,
            price=now.price, was_price=was.price,
            currency=now.currency, was_currency=was.currency,
        )
        # Nothing but the id moved. Counted rather than listed: see
        # `Report.renumbered`.
        if (not row.sellers_moved and row.title == row.was_title
                and row.currency == row.was_currency
                and row.price == row.was_price):
            report.renumbered += 1
        else:
            report.relisted.append(row)
    if moved_ids:
        report.added = [d for d in report.added if d.departure_id not in moved_ids]
        report.withdrawn = [d for d in report.withdrawn
                            if d.departure_id not in moved_ids]

    # A field the older dataset never carried cannot have changed. Before
    # availability was parsed, every departure held None; comparing that
    # against real values reported 126 sailings as having just sold out when
    # the only thing that happened was somebody writing a parser.
    report.availability_newly_read = bool(old_deps) and not any(
        d.get("availability") for d in old_deps.values()
    ) and any(d.get("availability") for d in new_deps.values())

    for dep_id, new in new_deps.items():
        old = old_deps.get(dep_id)
        if old is None:
            continue

        if not report.availability_newly_read:
            was_sold, now_sold = _sold_out(old), _sold_out(new)
            if now_sold and not was_sold:
                report.sold_out.append(_as_departed(new, new_its, new_boats))
            elif was_sold and not now_sold:
                report.returned.append(_as_departed(new, new_its, new_boats))

        # Compare the quoted amount in its own currency. Comparing euro would
        # report every vessel on any day the ECB rate moves.
        old_price, new_price = old.get("price") or {}, new.get("price") or {}
        if old_price.get("currency") != new_price.get("currency"):
            continue
        was, now = old_price.get("amount"), new_price.get("amount")
        if was is None or now is None or was == now:
            continue
        if abs(now - was) < MIN_MOVE:
            report.price_rounding += 1
            continue
        boat, title = _describe(new, new_its, new_boats)
        move = PriceMove(dep_id, boat, title, new.get("start", ""),
                         float(was), float(now), new_price.get("currency", ""))
        (report.price_up if move.delta > 0 else report.price_down).append(move)

    old_fees, new_fees = _fees_by_boat(before), _fees_by_boat(after)
    for boat, fees in new_fees.items():
        previous = old_fees.get(boat)
        # Two different silences. A vessel absent from the older dataset, or one
        # present with no fee lines at all, has not raised anything -- nobody
        # had read its disclosure yet, which is the same distinction the site
        # makes between "no fees" and "no fee lines". Once a vessel has any fee
        # line, a code appearing beside it *is* the operator listing something.
        if not previous:
            continue
        name = new_boats.get(boat, {}).get("name") or boat
        for code, summary in fees.items():
            was = previous.get(code)
            if was is None:
                report.fees.append(FeeMove(name, code, "not listed", summary))
            elif was != summary:
                report.fees.append(FeeMove(name, code, was, summary))
        for code, was in previous.items():
            if code not in fees:
                report.fees.append(FeeMove(name, code, was, "no longer listed"))

    # Only the rates something is actually priced in. The table carries every
    # rate the feed publishes -- GBP among them, which no vessel here quotes --
    # and taking the first key out of it reported a GBP move as the reason
    # every euro figure on the page had shifted. Nothing on the page is
    # converted from GBP; every fare is quoted in dollars.
    old_fx, new_fx = before.get("fx") or {}, after.get("fx") or {}
    old_rates, new_rates = old_fx.get("rates") or {}, new_fx.get("rates") or {}
    display = new_fx.get("display_currency")
    priced_in = {
        (dep.get("price") or {}).get("currency") for dep in new_deps.values()
    } | {
        (fee.get("amount") or {}).get("currency")
        for itinerary in new_its.values()
        for fee in itinerary.get("fees") or []
    }
    for currency in sorted(c for c in priced_in if c and c != display):
        was, now = old_rates.get(currency), new_rates.get(currency)
        if was is None or now is None or abs(now - was) <= 1e-9:
            continue
        report.fx.append(FxMove(currency, float(was), float(now)))

    for bucket in (report.added, report.sold_out, report.withdrawn, report.returned):
        bucket.sort(key=lambda d: (d.start, d.boat))
    report.relisted.sort(key=lambda m: (m.start, m.boat))
    report.price_up.sort(key=lambda m: -abs(m.delta))
    report.price_down.sort(key=lambda m: -abs(m.delta))
    report.fees.sort(key=lambda f: (f.boat, f.code))
    return report


def headline(report: Report) -> str:
    """One line, for the subject of the commit that carries the new dataset.

    The refresh has always committed as "data: daily refresh <date>", which
    tells a reader the pipeline ran and nothing about whether anything
    happened. With this, ``git log --oneline data/`` *is* the changelog: the
    history is already one commit per run, and the only thing missing was a
    subject that said what the run found.

    Ordered by what would make someone look: a vessel that went missing is a
    broken fetch and comes first, then new stock, then money, then
    availability. Everything else the full report has.
    """
    if report.vessels_gone:
        return f"{len(report.vessels_gone)} vessel(s) lost every departure"
    if report.months_gone:
        return f"{len(report.months_gone)} vessel-month(s) went unread"

    bits: list[str] = []
    if report.vessels_new:
        bits.append(f"{len(report.vessels_new)} new vessel(s)")
    if report.added:
        bits.append(f"{len(report.added)} new departures")
    if report.withdrawn:
        bits.append(f"{len(report.withdrawn)} withdrawn")
    if report.relisted:
        bits.append(f"{len(report.relisted)} re-listed")
    moved = len(report.price_up) + len(report.price_down)
    if moved:
        biggest = max(report.price_up + report.price_down, key=lambda m: abs(m.delta))
        bits.append(f"{moved} prices moved (biggest {biggest.boat} "
                    f"{biggest.was:,.0f} -> {biggest.now:,.0f} {biggest.currency})")
    if report.fees:
        # Named, not just counted. The weekly fee run cannot move a fare -- it
        # re-promotes the same candidate -- so a fee change is the only thing
        # it ever reports, and "3 fee change(s)" would make every one of those
        # subjects identical.
        first = report.fees[0]
        rest = f", and {len(report.fees) - 1} more" if len(report.fees) > 1 else ""
        bits.append(f"{len(report.fees)} fee change(s) "
                    f"({first.boat} {first.code}{rest})")
    if report.sold_out:
        bits.append(f"{len(report.sold_out)} sold out")
    if report.returned:
        bits.append(f"{len(report.returned)} bookable again")

    if not bits:
        return "no change to trips, prices or availability"
    return ", ".join(bits[:3]) + (f", and {len(bits) - 3} more" if len(bits) > 3 else "")


# How many rows of one kind the structured report carries.
#
# `render`'s own limit is 12, because a hundred lines of monospace is not read.
# A browser can expand, so this is ten times that -- deep enough that "show the
# other 24" is a real control rather than a promise the data cannot keep -- and
# still a cap, because it is paid for by every visitor: one refresh landed 644
# fare moves in a single report, 136 KB of the 200 the whole week came to.
#
# What is dropped is counted and said out loud, exactly as the text renderer
# says it. A silent truncation reads as "that was everything", which is the
# failure this project exists to correct in other people.
BOOK_LIMIT = 120


def as_dict(report: Report, *, before: str = "", after: str = "",
            limit: int = BOOK_LIMIT) -> dict:
    """The report as data, for a renderer that is not a terminal.

    Everything a page needs is built by :func:`compare` and was thrown away one
    step before it: ``render`` flattened the dataclasses to column-aligned
    text, the CLI wrote that text into a Markdown file, and the site read the
    text back out and escaped it into a ``<pre>``. The visitor got
    ``MY Odyssey Liveaboar`` -- a boat name cut mid-word to fit eighty columns
    -- on a page that has a table renderer, and not one of those lines could
    be clicked through to the sailing it was about (#143).

    So the same report comes out twice, in two shapes, from one comparison. The
    text stays: it is what a workflow log and `data/CHANGES.md` want, and it is
    the durable record. This is what the page reads.

    Capped far higher than the text form and never silently: a browser can
    expand, so the honest shape of a truncation there is showing the other
    twenty-four behind a control rather than confessing they exist -- but the
    page is one file with nothing fetched lazily, so a report is paid for by
    every visitor and cannot be unbounded. What is cut is counted in ``more``.

    ``price_rounding`` is not that: those fares were never listed at all,
    having been excluded as source rounding, which is a different thing from
    being dropped for length.
    """
    def departed(d: Departed) -> dict:
        return {"id": d.departure_id, "boat": d.boat, "title": d.title,
                "start": d.start, "price": d.price, "currency": d.currency,
                "sellers": list(d.sellers)}

    def moved(m: PriceMove) -> dict:
        return {"id": m.departure_id, "boat": m.boat, "title": m.title,
                "start": m.start, "was": m.was, "now": m.now,
                "currency": m.currency, "delta": m.delta, "pct": m.pct}

    more: dict[str, int] = {}

    def capped(name: str, rows: list) -> list:
        if len(rows) > limit:
            more[name] = len(rows) - limit
        return rows[:limit]

    out = {
        "before": before,
        "after": after,
        "added": capped("added", [departed(d) for d in report.added]),
        "sold_out": capped("sold_out", [departed(d) for d in report.sold_out]),
        "returned": capped("returned", [departed(d) for d in report.returned]),
        "withdrawn": capped("withdrawn", [departed(d) for d in report.withdrawn]),
        "relisted": capped("relisted", [
            {"boat": m.boat, "start": m.start, "title": m.title,
             "was_title": m.was_title,
             "sellers": list(m.sellers), "was_sellers": list(m.was_sellers),
             "price": m.price, "was_price": m.was_price,
             "currency": m.currency, "was_currency": m.was_currency,
             "sellers_moved": m.sellers_moved, "repriced": m.repriced}
            for m in report.relisted]),
        "price_up": capped("price_up", [moved(m) for m in report.price_up]),
        "price_down": capped("price_down", [moved(m) for m in report.price_down]),
        "fees": capped("fees", [
            {"boat": f.boat, "code": f.code, "was": f.was, "now": f.now}
            for f in report.fees]),
        "vessels_gone": capped("vessels_gone", list(report.vessels_gone)),
        "months_gone": capped("months_gone", list(report.months_gone)),
        "vessels_new": capped("vessels_new", list(report.vessels_new)),
        "fx": [{"currency": x.currency, "was": x.was, "now": x.now, "pct": x.pct}
               for x in report.fx],
        "price_rounding": report.price_rounding,
        "renumbered": report.renumbered,
        "availability_newly_read": report.availability_newly_read,
        "quiet": report.is_quiet and not report.fx_moved,
    }
    if more:
        out["more"] = more
    return out


def render(report: Report, *, before: str = "", after: str = "", limit: int = 12) -> str:
    """The report as plain text, longest-first and capped.

    Capped rather than complete, because a hundred-line wall is not read. What
    is dropped is always said out loud: a silent truncation reads as "that was
    everything", which is the failure this whole project exists to correct.
    """
    lines: list[str] = []
    header = "changes"
    if before and after:
        header = f"changes: {before} -> {after}"
    lines.append(header)
    lines.append("=" * len(header))

    if report.is_quiet and not report.fx_moved and not report.availability_newly_read:
        # "nothing moved" has to stay true of the things that did. Both of
        # these are suppressed rather than absent -- fares that shifted by less
        # than a unit, and sailings that kept everything but their id -- and a
        # quiet run that hides them is the silent truncation this file exists
        # to refuse.
        aside = []
        if report.price_rounding:
            aside.append(f"{report.price_rounding} fare(s) shifting by under "
                         f"{MIN_MOVE:,.0f}")
        if report.renumbered:
            aside.append(f"{report.renumbered} sailing(s) that kept everything "
                         "but their id")
        lines.append("\nnothing moved." if not aside else
                     "\nnothing moved, beyond " + " and ".join(aside) + ".")
        return "\n".join(lines)

    if report.availability_newly_read:
        lines.append(
            "\nnote: the earlier dataset stated availability nowhere, so sold-out "
            "and bookable-again are not compared. Nobody had looked before."
        )

    def block(title: str, rows: list[str]) -> None:
        if not rows:
            return
        lines.append(f"\n{title} ({len(rows)})")
        for row in rows[:limit]:
            lines.append(f"  {row}")
        if len(rows) > limit:
            lines.append(f"  ... and {len(rows) - limit} more not shown")

    if report.vessels_gone:
        lines.append(
            f"\n!! {len(report.vessels_gone)} vessel(s) lost every departure at once — "
            "most likely a failed fetch, not a cancelled season"
        )
        for name in report.vessels_gone[:limit]:
            lines.append(f"  {name}")

    if report.months_gone:
        lines.append(
            f"\n!! {len(report.months_gone)} vessel-month(s) lost every departure "
            "while the vessel kept selling other months — a page that came back "
            "unreadable, not a withdrawn month. Those sailings are missing from "
            "the site until the next crawl reads them."
        )
        for name in report.months_gone[:limit]:
            lines.append(f"  {name}")
        if len(report.months_gone) > limit:
            lines.append(f"  ... and {len(report.months_gone) - limit} more not shown")

    def money(d: Departed) -> str:
        return f"{d.price:,.0f} {d.currency}" if d.price is not None else "no price"

    def who(hosts: tuple[str, ...]) -> str:
        return "+".join(hosts) or "unknown"

    block("new departures", [
        f"{d.start}  {d.boat:22.22} {d.title:34.34} {money(d):>14.14} "
        f"{who(d.sellers)}" for d in report.added])
    block("now sold out", [
        f"{d.start}  {d.boat:22.22} {d.title:34.34} {who(d.sellers)}"
        for d in report.sold_out])
    block("bookable again", [
        f"{d.start}  {d.boat:22.22} {d.title:34.34} {who(d.sellers)}"
        for d in report.returned])
    block("withdrawn", [
        f"{d.start}  {d.boat:22.22} {d.title:34.34} {who(d.sellers)}"
        for d in report.withdrawn])
    # Not an arrival and not a withdrawal: the same sailing, from the other
    # seller's book. Its two fares are printed under both names, because they
    # are two sellers' prices for one week rather than one price moving.
    def relisted_row(m: Relisted) -> str:
        """Only what moved. A row printing `X -> X` twice says nothing, and the
        rows where nothing moved are counted below rather than listed."""
        moved = []
        if m.sellers_moved:
            moved.append(f"{who(m.was_sellers)} -> {who(m.sellers)}")
        if (m.price, m.currency) != (m.was_price, m.was_currency):
            was = (f"{m.was_price:,.0f} {m.was_currency}"
                   if m.was_price is not None else "no price")
            now = (f"{m.price:,.0f} {m.currency}"
                   if m.price is not None else "no price")
            moved.append(f"{was} -> {now}")
        if m.title != m.was_title:
            moved.append(f"{m.was_title:.24} -> {m.title:.24}")
        return f"{m.start}  {m.boat:22.22} {m.title:28.28} " + "  ".join(moved)

    block("re-listed — same sailing, new row",
          [relisted_row(m) for m in report.relisted])
    if report.renumbered:
        lines.append(
            f"\n{report.renumbered} sailing(s) kept everything but their id — "
            "same seller, same fare, same trip. An id that moved is not news, "
            "and it is counted here rather than listed above"
        )
    def moved(m: PriceMove) -> str:
        """Both ends, the difference, and the trip it is for.

        The percentage carries a decimal: a real 20 move on a 2,400 berth is
        0.8%, and printing that as "+1%" -- or worse, "+0%" -- reads as the
        rounding noise this report is at pains to exclude.
        """
        return (f"{m.start}  {m.boat:20.20} {m.was:>7,.0f} -> {m.now:>7,.0f} "
                f"{m.currency}  {m.delta:+,.0f} ({m.pct:+.1f}%)  {m.title:.34}")

    block("price up", [moved(m) for m in report.price_up])
    block("price down", [moved(m) for m in report.price_down])
    block("fees", [
        f"{f.boat:22.22} {f.code:16.16} {f.was} -> {f.now}" for f in report.fees])
    if report.vessels_new:
        block("vessels seen for the first time", report.vessels_new)

    if report.price_rounding:
        lines.append(
            f"\n{report.price_rounding} further fare(s) moved by less than "
            f"{MIN_MOVE:,.0f} — too small to be a decision, and mostly the "
            "source re-rounding; counted here rather than listed above"
        )

    # Last, and separate: it explains a euro figure moving on every row, and it
    # is not an operator changing anything.
    for move in report.fx:
        lines.append(
            f"\nfx: {move.currency} {move.was:.6f} -> {move.now:.6f} "
            f"({move.pct:+.2f}%) — every euro figure converted from "
            f"{move.currency} moves with it; no operator changed a price"
        )
    return "\n".join(lines)
