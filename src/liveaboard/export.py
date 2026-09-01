"""The dataset in forms a visitor can take away.

The page is the argument; this is the evidence. A reader who does not believe
a total, or who wants to ask a question the filters do not answer, should be
able to have the numbers rather than re-type them off a table -- so the build
writes a CSV and the change log next to `index.html` and links to both.

Two rules carry over from the page, because a file that leaves the site is
quoted more freely than a screen that stays on it:

* **A trip whose operator never stated its required extras has no total**, in
  the CSV exactly as on the page. Writing the advertised price into a `total`
  column would turn "we do not know" into a number the moment it reached a
  spreadsheet -- and it is the least forthcoming operators that would come out
  cheapest. The column is empty and `disclosure` says why.

* **A range stays a range.** `total_min` and `total_max` differ where the
  operator quotes a spread, and picking the middle would be inventing the
  figure this project exists to expose.

The CSV is generated from the dataset at build time rather than committed, so
it cannot fall behind the page it sits beside.
"""

from __future__ import annotations

import csv
import io
from datetime import date, timedelta

from .dataset import Dataset
from .money import DISPLAY_CURRENCY, Money
from .pricing import compute, mandatory_known

COLUMNS = [
    "departure_id", "boat", "operator", "trip", "start", "end", "nights",
    "dives", "guests", "port_from", "port_to", "dive_sites",
    "advertised", "list_price", "discount_pct",
    "mandatory_fees_min", "mandatory_fees_max",
    "total_min", "total_max", "currency",
    "disclosure", "availability", "places_at_price", "berths_aboard",
    "booking_url",
]
"""The columns of the published CSV.

``spaces_left`` was here and was empty on all 1,122 rows: a `Departure` field
whose job the per-seller `berths` block took, still exporting the word itself
and never a number. It is replaced by the two counts the page actually prints,
under the two names the page prints them by -- *places* at the advertised price
and *berths aboard* the sailing. They answer different questions and only a
cabin ladder answers the first, so one column called `spaces_left` could not
have carried both honestly even once it was filled.
"""

NO_DISCLOSURE = "required extras not stated"


def _count(value: int | None) -> str:
    """A stated berth count, or blank. Never zero for "nobody said"."""
    return "" if value is None else str(value)


def to_csv(dataset: Dataset) -> str:
    """One row per departure, with the totals the page would show.

    Totals are on the page's defaults -- nitrox and rental gear counted --
    because that is what most visitors are actually charged and what the table
    is sorted by when it loads. Anyone wanting the bare berth has the
    `advertised` column beside it.
    """
    if dataset.fx is None:
        raise ValueError("dataset has no FX table; cannot export euro prices")

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(COLUMNS)

    for departure in sorted(dataset.departures, key=lambda d: (d.start, d.id)):
        itinerary = dataset.itinerary_for(departure)
        boat = dataset.boat_for(itinerary)
        operator = dataset.operator_for(itinerary)
        breakdown = compute(itinerary, departure, dataset.fx)
        known = mandatory_known(itinerary, departure)

        def money(value):
            return f"{value.rounded:.2f}" if known else ""

        # Money deliberately has no __sub__ -- subtracting across currencies is
        # the bug it exists to prevent. Both sides are already display currency
        # here, so the top of the fee range comes off the amounts.
        surcharge_max = Money(
            breakdown.total_max.amount - breakdown.base.amount, DISPLAY_CURRENCY
        )

        writer.writerow([
            departure.id,
            boat.name,
            operator.name,
            itinerary.title or itinerary.name,
            departure.start.isoformat(),
            departure.end.isoformat(),
            itinerary.nights,
            # Zero means the operator published no count. An empty cell says
            # that; a 0 in a spreadsheet gets averaged.
            itinerary.dives or "",
            boat.guests or "",
            itinerary.port_from,
            itinerary.port_to,
            "; ".join(itinerary.dive_sites),
            f"{breakdown.base.rounded:.2f}",
            # What the advertised price is down from, and by how much, where a
            # seller publishes a list price above what it charges. Empty on the
            # 854 rows nobody has marked down -- and empty, too, where the
            # discount belongs to the *other* seller, since `advertised` beside
            # it is this one's. The page can filter on a sale, so the file a
            # reader takes away has to be able to as well; a spreadsheet that
            # cannot answer a question the table can is evidence with a hole in
            # it.
            departure.sale.get("was", ""),
            departure.sale.get("pct", ""),
            money(breakdown.surcharge),
            money(surcharge_max),
            money(breakdown.total),
            money(breakdown.total_max),
            DISPLAY_CURRENCY,
            "" if known else NO_DISCLOSURE,
            departure.availability or "",
            # Both, because they are different claims. Empty where no seller
            # stated one; `0` is an answer and means nothing is left.
            _count(departure.spots_at_advertised),
            _count(departure.berths_aboard),
            departure.booking_url or "",
        ])
    return buffer.getvalue()


def _strip_entry(entry: str) -> tuple[str, str]:
    """One ``## `` block of ``data/CHANGES.md`` as ``(date, body)``."""
    lines = entry.split("\n")
    date = lines[0].strip()
    lines = lines[1:]
    # Drop the code fence the CLI wraps entries in.
    while lines and lines[0].strip() in ("", "```"):
        lines.pop(0)
    while lines and lines[-1].strip() in ("", "```"):
        lines.pop()
    return date, "\n".join(lines)


def parse_entries(history: str) -> list[tuple[str, str]]:
    """Every entry in ``data/CHANGES.md``, newest first, as ``(date, body)``.

    A date can repeat: the refresh runs more than once a day, and each run
    writes its own entry. They are separate readings and stay separate.
    """
    blocks = history.split("\n## ")[1:]
    return [_strip_entry(block.split("\n## ", 1)[0]) for block in blocks]


def latest_entry(history: str) -> str:
    """The newest entry out of ``data/CHANGES.md``, without its heading."""
    entries = parse_entries(history)
    return entries[0][1] if entries else ""


def recent_entries(history: str, days: int = 7) -> list[tuple[str, str]]:
    """Every refresh within ``days`` calendar days of the newest one recorded.

    The page carried one entry and linked to the file for the rest, which made
    the ordinary question hard to ask: the refresh runs daily and a lot moves,
    so "has this boat been repricing all week" needed a raw markdown file. It
    also made the noisiest possible window the default -- a run that read
    nothing showed a view saying nothing moved, with six days of real movement
    one link away.

    **Anchored to the log, never to the clock.** "The last seven days" read
    against today would make the rendered page a function of when it was built:
    the same committed inputs would produce a different page tomorrow, an
    entry would silently drop out of the window, and
    `TestThePageIsWhatItsDataBuilds` -- which compares the committed page
    against a fresh render and normalises only the build stamp -- would turn
    `main` red with nobody having changed anything. `render` is pure and stays
    pure. A log that has gone stale is visible in the dates the view prints
    beside each entry, which is a fact a reader can check rather than a warning
    that appears and disappears with the calendar.

    Dates that do not parse are kept rather than dropped: an entry nobody can
    place in time is still a refresh that happened, and silently discarding it
    would be this file deciding a reading never occurred.
    """
    entries = parse_entries(history)
    if not entries:
        return []

    def when(entry: tuple[str, str]) -> date | None:
        try:
            return date.fromisoformat(entry[0])
        except ValueError:
            return None

    newest = next((d for d in map(when, entries) if d), None)
    if newest is None:
        return entries
    floor = newest - timedelta(days=days - 1)
    return [e for e in entries if (when(e) or newest) >= floor]
