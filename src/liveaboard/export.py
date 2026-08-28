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

from .dataset import Dataset
from .money import DISPLAY_CURRENCY, Money
from .pricing import compute, mandatory_known

COLUMNS = [
    "departure_id", "boat", "operator", "trip", "start", "end", "nights",
    "dives", "guests", "port_from", "port_to", "dive_sites",
    "advertised", "mandatory_fees_min", "mandatory_fees_max",
    "total_min", "total_max", "currency",
    "disclosure", "availability", "spaces_left", "booking_url",
]

NO_DISCLOSURE = "required extras not stated"


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
            money(breakdown.surcharge),
            money(surcharge_max),
            money(breakdown.total),
            money(breakdown.total_max),
            DISPLAY_CURRENCY,
            "" if known else NO_DISCLOSURE,
            departure.availability or "",
            departure.spaces_left if departure.spaces_left is not None else "",
            departure.booking_url or "",
        ])
    return buffer.getvalue()


def latest_entry(history: str) -> str:
    """The newest entry out of ``data/CHANGES.md``, without its heading.

    The file grows by one entry a day and the page ships as a single file, so
    inlining the whole history would put every refresh since launch inside
    every visitor's download. The page carries the last one and links to the
    rest.
    """
    body = history.split("\n## ", 1)
    if len(body) < 2:
        return ""
    entry = body[1].split("\n## ", 1)[0]
    # Drop the date heading line and the code fence the CLI wraps entries in.
    lines = entry.split("\n")[1:]
    while lines and lines[0].strip() in ("", "```"):
        lines.pop(0)
    while lines and lines[-1].strip() in ("", "```"):
        lines.pop()
    return "\n".join(lines)
