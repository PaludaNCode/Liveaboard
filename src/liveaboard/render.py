"""Turn the dataset into a self-contained static site.

All normalisation — fee basis to per-trip, source currency to euro — happens
here in Python, so the page ships pre-resolved euro amounts and the browser
only has to add up the lines the visitor has switched on. That keeps one
authoritative implementation of the cost rules instead of a Python one and a
JavaScript one that drift apart.

The output is a single HTML file with its CSS and JavaScript inlined: no build
step, no dependencies, no CDN. Open it from disk or serve it from anywhere.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .dataset import Dataset
from .export import recent_entries, to_csv
from .money import DISPLAY_CURRENCY
from .pricing import (
    DEFAULT_TOGGLES,
    base_line,
    compute,
    itinerary_lines,
    mandatory_known,
    overlapping_charges,
    padi_base_line,
    padi_lines,
    resolve_fees,
)
from .taxonomy import DIVER_LEVEL_BARS, FEE_LABELS, FeeCode, FeeTier

TEMPLATE_DIR = Path(__file__).resolve().parent.parent.parent / "templates"

MONTH_NAMES = {
    5: "May", 6: "June", 7: "July", 8: "August",
    1: "January", 2: "February", 3: "March", 4: "April",
    9: "September", 10: "October", 11: "November", 12: "December",
}

TOGGLE_LABELS: dict[str, str] = {
    "nitrox": "Nitrox",
    "gear": "Rental gear",
}

# What the same switch is called where the toolbar is one phone wide. Only the
# two-word one needs it; a switch with no entry here prints its full name at
# every width, rather than an abbreviation invented to fill the table.
TOGGLE_SHORT: dict[str, str] = {
    "gear": "Gear",
}


def build_payload(dataset: Dataset) -> dict[str, Any]:
    """Flatten the dataset into the JSON the page consumes."""
    if dataset.fx is None:
        raise ValueError("dataset has no FX table; cannot render euro prices")

    itineraries: dict[str, Any] = {}
    # The resolved fee rows, once per itinerary. Reused below to decide whether
    # a departure needs its own copy, which is why they are kept as dicts here
    # rather than re-serialised per departure.
    shared_lines: dict[str, list[dict[str, Any]]] = {}
    for key, itinerary in dataset.itineraries.items():
        boat = dataset.boat_for(itinerary)
        shared_lines[key] = [line.as_dict() for line in itinerary_lines(itinerary, dataset.fx)]
        itineraries[key] = {
            "id": key,
            # Fees belong to the vessel's disclosure, not to the sailing: the
            # extras do not change with the month, which is why the fee book is
            # collected weekly and keyed by boat. Writing them per departure
            # wrote the same ten rows 878 times for 314 distinct answers -- 4.4
            # MB of a 5.6 MB page, and every byte shipped to every visitor on a
            # site that is deliberately one file with no CDN to lazy-load from.
            "lines": shared_lines[key],
            "name": itinerary.name,
            # What the trip-name column prints. Falls back to the full name so
            # a dataset promoted before this field existed still renders.
            "title": itinerary.title or itinerary.name,
            "boat_id": boat.id,
            # The vessel, and not the company behind it. The operating
            # company shipped here for eleven refreshes with nothing reading
            # it: the operator filter bank was removed because a company with
            # six boats returned six boats' worth of rows and no way to tell
            # them apart, the search box that reached it went too, and a
            # per-operator score was removed before both for reading as a
            # league table. A diver picks the boat.
            "boat": boat.name,
            "nights": itinerary.nights,
            # Zero where the operator publishes no count. The page prints
            # nothing rather than dividing by an assumption.
            "dives": itinerary.dives,
            # ...and whether that zero is an answer. Shipped only on the rows
            # where it changes what the cell says -- a trip with a count needs
            # no explanation of why it has none.
            **({"dives_read": True} if itinerary.dives_read and not itinerary.dives
               else {}),
            "port_from": itinerary.port_from,
            "port_to": itinerary.port_to,
            # `one_way` was here, and it was `port_from != port_to` computed
            # over the two fields immediately above it. A derived field is
            # cheap once and 402 times it is not.
            "dive_sites": itinerary.dive_sites,
            "region": itinerary.region,
            "guests": boat.guests,
            # `summary` was here: the vessel's year-round brochure, 63 KB
            # across 347 itineraries, read by no line of the page. It is the
            # boat's prose rather than the trip's -- this file's own rule says
            # it can never be a source for where one sailing goes -- so there
            # was never a column it belonged in. `promote` still reads it, off
            # the *dataset*, to pull a guest count out of the sentence.
            "source_url": itinerary.source_url,
            # What the operator states about the entry bar. Kept because it
            # is their claim; the route, theme and level the site used to
            # infer beside it were ours, unread by the page, and gone.
            "requirements": itinerary.requirements.as_dict(),
        }

        # The second seller's bill for the same trip, in the same shape as the
        # rows above it, so the browser adds it up with the same code and the
        # visitor's toggles reach both sides. Written only where PADI's
        # disclosure is complete; where it is not there is no key, and the page
        # shows its berth price with a note instead of a total it cannot stand
        # behind.
        second = padi_lines(itinerary, dataset.fx)
        if second is not None:
            itineraries[key]["padi_lines"] = [line.as_dict() for line in second]
            # And whether that bill can be added up at all. Written only where
            # it cannot, like every other per-itinerary key: this is one boat.
            clash = overlapping_charges(
                [fee for fee in itinerary.padi_fees if fee.tier is FeeTier.MANDATORY]
                + [fee for fee in itinerary.fees if fee.tier is not FeeTier.MANDATORY]
            )
            if clash:
                itineraries[key]["padi_overlap"] = [code.value for code in clash]

        # Where the rows above came from, on the trips whose answer is not the
        # usual one. Written only where true: a key written per itinerary is a
        # key written 402 times, and this is the answer for 22 boats.
        if itinerary.padi_sourced_fees:
            itineraries[key]["padi_sourced_fees"] = True

    # Where the other seller lists each boat. A PADI listing url is a fact
    # about the vessel, not about the sailing -- it is built from the boat's
    # slug and its country -- so it ships once per boat rather than on each of
    # the 601 departures PADI sells, which is 4 KB against 33 KB.
    padi_urls: dict[str, str] = {}
    for departure in dataset.departures:
        if departure.padi_provenance and departure.padi_provenance.url:
            itinerary = dataset.itinerary_for(departure)
            padi_urls.setdefault(itinerary.boat_id, departure.padi_provenance.url)

    departures: list[dict[str, Any]] = []
    for departure in sorted(dataset.departures, key=lambda d: (d.start, d.id)):
        itinerary = dataset.itinerary_for(departure)
        first = base_line(departure, dataset.fx)

        # An itinerary with no fee lines is not one with no fees; it is one
        # nobody has looked at yet. Reporting a true cost equal to the
        # advertised price, and a perfect honesty score, would make this site
        # commit exactly the omission it exists to expose.
        fees_known = bool(resolve_fees(itinerary, departure))
        # Listing only optional extras is not the same as having no required
        # ones, and scoring it as such put the least forthcoming operators at
        # the top of the honesty ranking. See pricing.mandatory_known.
        mandatory = mandatory_known(itinerary, departure)
        # Said something, and said it twice. A bundle naming a charge that is
        # also its own line means the disclosure does not add up, which is the
        # same verdict as a mandatory line with no figure -- so the page shows
        # the berth price and no total. Written only where it fires.
        clash = overlapping_charges(resolve_fees(itinerary, departure))

        entry: dict[str, Any] = {
            "id": departure.id,
            "fees_known": fees_known,
            "mandatory_known": mandatory,
            **({"fee_overlap": [code.value for code in clash]} if clash else {}),
            "itinerary_id": itinerary.id,
            "boat_id": itinerary.boat_id,
            "start": departure.start.isoformat(),
            "end": departure.end.isoformat(),
            "month": departure.start.month,
            "nights": itinerary.nights,
            # `spaces_left` was here and was `null` on all 1,122 rows -- the
            # key, not a number, 4.4 KB of the word itself. The cabin ladder in
            # `berths` answers this now, per seller and with the date it was
            # read beside it.
            "availability": departure.availability,
            "bookable": departure.bookable,
            "booking_url": departure.booking_url,
            "base": float(first.display.rounded),
            "base_line": first.as_dict(),
            "verified": departure.price_provenance.is_verified,
        }

        # What PADI advertises for this same sailing, converted like every other
        # price on the page.
        #
        # This used to be compared against ``base`` and never against the total,
        # on the stated grounds that PADI publishes no fee book. It does. It
        # publishes one per itinerary rather than beside the price, which is
        # why it looked absent -- and it is not a formality: of the 74 trips
        # where both books can be added up, 43 disagree and 16 by more than
        # €150. So a berth-to-berth column was comparing the two sellers on the
        # half of the bill they agree about most and hiding the half they do
        # not, which is the failure this site reports in operators.
        #
        # The line, not just the number, because the browser adds this up beside
        # the fee rows and needs the same shape. Only where PADI sells the date:
        # a field written per departure ships 892 times.
        second_base = padi_base_line(departure, dataset.fx)
        if second_base is not None:
            entry["padi"] = float(second_base.display.rounded)
            entry["padi_base_line"] = second_base.as_dict()

        # Who lists this sailing at all, where the answer is "PADI, and only
        # PADI". A row like this has one seller and one bill, so the Sellers
        # column must not read it as the state it looks like -- a dash, meaning
        # PADI does not sell the date, when PADI is the reason the row exists.
        if departure.padi_only:
            entry["padi_only"] = True

        # The cabin ladder, one block per seller, exactly as promote wrote it.
        # Passed through rather than reshaped: it is already normalised and
        # converted, and a second shaping here would be a second place for the
        # page and the dataset to disagree about what a sailing costs.
        #
        # Absent where the booking page could not be read. That is not a
        # sailing with no cabins, and the page has to be able to tell those
        # apart -- the same distinction the crawl draws for an unread page.
        if departure.berths:
            entry["berths"] = departure.berths

        # Whether this berth is marked down, as promote read it off the two
        # sellers' own list prices. Written only where one of them says so --
        # 268 of 1,122 rows — because a key written per departure is a key
        # written 1,122 times, and "not on sale" is the absence.
        if departure.sale:
            entry["sale"] = departure.sale

        # A departure-level fee replaces the route's for its code, so a sailing
        # can genuinely price a fee differently. No departure in the dataset
        # does today, but the possibility is in the model, and silently reusing
        # the itinerary's rows would publish the wrong bill on the one sailing
        # that ever exercises it. So compare, and give that departure its own.
        own = [line.as_dict() for line in compute(itinerary, departure, dataset.fx).lines[1:]]
        if own != shared_lines[itinerary.id]:
            entry["lines"] = own

        departures.append(entry)

    months = sorted({d["month"] for d in departures})

    return {
        "meta": {
            "generated": (dataset.generated or date.today()).isoformat(),
            # When this page was rendered, to the minute, in UTC.
            #
            # Distinct from `generated`, which is the day the *data* was
            # scraped, and the toolbar had been printing that under the word
            # "built" -- two different facts under one label, and the one it
            # showed was not the one it named. They diverge whenever a parser
            # or template change is promoted without a fresh crawl, which is
            # most of them.
            #
            # To the minute because the page is rebuilt several times an hour
            # on a busy day and a date alone cannot tell two of those apart --
            # which is the whole question somebody reading this line is asking.
            # UTC, and stamped as such: the runner's clock is not the reader's.
            "built": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "currency": DISPLAY_CURRENCY,
            "verified": dataset.is_fully_verified,
            "source_kinds": sorted(k.value for k in dataset.source_kinds),
            "notes": dataset.notes,
            "fx": {
                "as_of": dataset.fx.as_of.isoformat() if dataset.fx.as_of else None,
                # Every euro figure on the page rests on this rate, so the page
                # says where it came from — or admits that it did not.
                "source": dataset.fx.source,
                "sourced": dataset.fx.is_sourced,
                # Sourced but no longer refreshed is a third state. The fetcher
                # keeps the last good rate when a fetch fails, so a broken feed
                # looks exactly like a quiet one unless the date is watched.
                "age_days": dataset.fx.age_days(),
                "stale": dataset.fx.is_stale(),
            },
            # The day the berth counts were read. On the page beside every
            # count, because a count without its date is presented as a fact
            # when it is a claim with a shelf life.
            "berths_read": dataset.berths_read,
            # PADI's counts are a separate crawl on a separate day. Written
            # only where it ran: a null beside a real date reads as "unknown",
            # and an absent key reads as "that seller is not here", which is
            # the true one on a dataset built without its book.
            **({"padi_berths_read": dataset.padi_berths_read}
               if dataset.padi_berths_read else {}),
            # `operators` was counted here and printed in the line under the
            # filters -- "46 operators". It was the last of the operating
            # company on the page, and it said nothing a reader could act on:
            # a diver picks the boat, and the number of companies behind 77
            # hulls is not a fact about any trip.
            "counts": {
                "departures": len(departures),
                "itineraries": len(itineraries),
                "boats": len(dataset.boats),
            },
        },
        # Months and toggles only. The route, level and theme facets were built
        # here for filter chips that have since been removed from the table --
        # app.js references none of them. Small beside the fee duplication, but
        # it is payload nobody reads, and a facet list that nothing renders is
        # also a thing a reader has to check before changing.
        #
        # The per-itinerary route, level and themes stay: they are what a route
        # badge would render from, and #34 is about filling them in.
        "facets": {
            "months": [{"id": m, "label": MONTH_NAMES[m]} for m in months],
            "toggles": [
                {"id": key, "label": TOGGLE_LABELS[key],
                 "short": TOGGLE_SHORT.get(key, TOGGLE_LABELS[key]),
                 "default": DEFAULT_TOGGLES[key]}
                for key in TOGGLE_LABELS
            ],
        },
        # The pools every cabin ladder indexes into. Shipped once for the same
        # reason the fee labels are: one vocabulary, defined in one place.
        "cabin_names": dataset.cabin_names,
        "sellers": dataset.sellers,
        # Keyed by boat, and read only for departures the other seller prices:
        # the vessel having a PADI page says nothing about whether PADI sells
        # a given date, and a link that lands on a calendar without the sailing
        # on it is worse than no link.
        "padi_urls": padi_urls,
        "fee_labels": {code.value: label for code, label in FEE_LABELS.items()},
        # The entry bar's vocabulary: each level split into the certification
        # and the dive count it implies, which is what the Entry bar column
        # prints and what its filter chips are keyed on.
        #
        # This replaced `level_labels`, which shipped the four full level names
        # and was read by exactly one line of the page -- and then by none,
        # once the column and the expanded row started building the phrase from
        # the pair instead. `DIVER_LEVEL_LABELS` is still where a level's own
        # name comes from; it is `promote` that needs it, for the sentence
        # naming which seller stated what, and that sentence arrives already
        # written in `requirements.notes`. Nothing on the page assembles it,
        # so nothing on the page needs the table.
        #
        # A list, not a mapping, and in `DIVER_LEVEL_ORDER`: the column's rank
        # is the position, so shipping the order with the labels keeps the
        # browser from carrying a second copy of which bar is the harder one.
        "entry_bars": [[level.value, cert, dives]
                       for level, cert, dives in DIVER_LEVEL_BARS],
        # What PADI Travel is discounting on these boats, and what moved since
        # the day before. Passed through exactly as `promote` wrote it, for the
        # same reason the cabin ladder is: it is already joined, converted and
        # diffed, and a second shaping here would be a second place for the page
        # and the dataset to disagree. Absent until the daily read has run.
        **({"deals": dataset.deals} if dataset.deals else {}),
        "itineraries": itineraries,
        "departures": departures,
    }


NITROX_STATES = ("included", "priced", "listed_unpriced", "absent")
"""The four things this page can say about nitrox, in the order it says them."""


def nitrox_by_vessel(payload: dict[str, Any]) -> dict[str, str]:
    """Each boat's nitrox state, from the payload the browser gets.

    Nitrox is a vessel's policy rather than a trip's, so this is keyed on the
    boat and the last departure read wins -- which is safe because every
    itinerary of a boat carries the same vessel fee book.

    Lives here rather than in the test that used to own it. The footer states
    these counts in prose and the test asserted the prose against numbers it
    computed itself; two copies of one definition, and the page's copy was a
    literal somebody had to remember to edit. Now the page is *rendered* from
    this and the test checks the rendering, so the definition exists once.
    """
    state: dict[str, str] = {}
    for departure in payload["departures"]:
        itinerary = payload["itineraries"][departure["itinerary_id"]]
        lines = [departure["base_line"]] + (
            departure.get("lines") or itinerary["lines"]
        )
        line = next((x for x in lines if x.get("code") == "nitrox"), None)
        state[itinerary["boat"]] = (
            "absent" if line is None
            else "included" if line.get("included")
            else "priced" if line.get("has_price")
            else "listed_unpriced"
        )
    return state


def nitrox_prices(dataset: Dataset) -> dict[str, Any]:
    """The spread of what the fleet charges for nitrox, where it charges.

    The floor of each quote, because a ranged fee reported at its ceiling
    flatters nothing here but reads as the price -- and because the fleet's
    ranges are wide: 40-80, 70-125, 100-150. Median rather than mode: the mode
    moved from 70 to 100 the moment a second seller's book was read, on a
    distribution that barely changed, which is the kind of figure prose should
    not rest on.
    """
    seen: dict[str, tuple[float, str, str | None]] = {}
    for itinerary in dataset.itineraries.values():
        for fee in itinerary.fees:
            if fee.code is not FeeCode.NITROX or fee.included or fee.amount is None:
                continue
            seen[itinerary.boat_id] = (
                float(fee.amount.amount), fee.amount.currency, fee.basis.value
                if fee.basis else None,
            )
    if not seen:
        return {}
    lows = sorted(v[0] for v in seen.values())
    middle = len(lows) // 2
    return {
        "vessels": len(seen),
        "low": lows[0],
        "high": lows[-1],
        "median": lows[middle] if len(lows) % 2 else (lows[middle - 1] + lows[middle]) / 2,
        "per_trip": sum(1 for v in seen.values() if v[2] == "per_trip"),
    }


def _median(values: list[float]) -> float:
    """The middle of a sorted list, for the same reason `nitrox_prices` uses one:
    a mean here is one 400-euro outlier away from being a figure nothing in the
    fleet resembles."""
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return (values[middle - 1] + values[middle]) / 2


def gear_prices(dataset: Dataset) -> dict[str, Any]:
    """What a full set costs, and what it costs *as a share of the berth*.

    Normalised to a seven-night week through `itinerary_lines`, never from the
    raw amount: operators quote the same set per day, per trip and per week, so
    a figure compared across vessels has to go through the basis resolution
    first -- which is this project's oldest rule and the reason the number is
    computed here rather than typed.

    The terciles are the argument, not decoration. A flat charge on a fare that
    spans four figures lands hardest on the cheapest trips, and stating it as
    one average would hide exactly that.
    """
    quoted: dict[str, float] = {}
    for itinerary in dataset.itineraries.values():
        if itinerary.nights != 7:
            continue
        for line in itinerary_lines(itinerary, dataset.fx):
            if line.code is FeeCode.GEAR_RENTAL and line.display is not None:
                quoted[itinerary.boat_id] = float(line.display.amount)
    if not quoted:
        return {}

    # One row per sailing, so a boat selling twenty weeks weighs twenty times
    # in the terciles -- these are shares of what a visitor is choosing between,
    # not an average over hulls.
    rows: list[tuple[float, float, str]] = []
    for departure in dataset.departures:
        itinerary = dataset.itineraries.get(departure.itinerary_id)
        if itinerary is None or itinerary.boat_id not in quoted:
            continue
        if itinerary.nights != 7:
            continue
        fare, _ = dataset.fx.to_display(departure.price)
        rows.append((float(fare.amount), quoted[itinerary.boat_id], itinerary.boat_id))
    if not rows:
        return {"vessels": len(quoted), "week": round(_median(sorted(quoted.values())))}
    rows.sort()

    def share(chunk: list[tuple[float, float, str]]) -> float:
        return 100 * sum(g for _, g, _ in chunk) / sum(f for f, _, _ in chunk)

    third = max(1, len(rows) // 3)
    extremes = sorted((gear / fare * 100, boat) for fare, gear, boat in rows)

    def named(entry: tuple[float, str]) -> str:
        boat = dataset.boats.get(entry[1])
        return boat.name if boat else entry[1]

    return {
        "vessels": len(quoted),
        # To the nearest ten, because "about" is what the sentence says and a
        # euro of precision on a median invites a reader to check arithmetic
        # the claim is not making.
        "week": int(round(_median(sorted(quoted.values())) / 10) * 10),
        "low_third": round(share(rows[:third])),
        "top_third": round(share(rows[-third:])),
        "dearest": round(extremes[-1][0]),
        "dearest_boat": named(extremes[-1]),
        "cheapest": round(extremes[0][0]),
        "cheapest_boat": named(extremes[0]),
    }


def week_dive_counts(dataset: Dataset) -> dict[str, Any]:
    """The spread of stated dive counts over one trip length, per vessel.

    Seven nights only, which is the whole point: the figure exists to refute
    "three dives a day", and comparing counts across trip lengths would refute
    nothing. Per vessel rather than per trip, because a boat stating 17 on four
    of its weeks is one data point about that boat.
    """
    week: dict[str, set[int]] = {}
    for itinerary in dataset.itineraries.values():
        if itinerary.nights == 7 and itinerary.dives:
            week.setdefault(itinerary.boat_id, set()).add(itinerary.dives)
    stated = sorted(n for counts in week.values() for n in counts)
    if not stated:
        return {}
    return {
        "vessels": len(week),
        "low": stated[0],
        "high": stated[-1],
        "spread": stated[-1] - stated[0],
    }


def seller_gap(dataset: Dataset) -> dict[str, Any]:
    """How far apart the two sellers are, on the fare and on the fee book.

    The section's whole argument, in numbers, and both halves of it move
    whenever either parser learns to read something: the comparable set is
    `padi_fees_complete`, which grew from 74 trips to 179 when PADI's string
    prices were read, and the fare gap moves on every refresh.

    Mandatory lines only. Nitrox and rental gear are the vessel's own charge on
    both sides -- the same rows, from the same price list -- so including them
    would add a constant to both totals and dilute the difference the sentence
    is about.

    The fare gap is a **median**, not a share within a threshold. A threshold
    is a second number in the claim and the wrong one moves invisibly: the page
    said "within 5 euro nine times in ten", which was true at 5 and became true
    at 10 instead, with nothing to notice that the sentence had drifted from
    the data to somewhere near it.
    """
    def mandatory(lines: list[Any]) -> float:
        return sum(
            float(line.display.amount) for line in lines
            if line.tier is FeeTier.MANDATORY and line.display is not None
        )

    comparable = disagree = 0
    widest = 0.0
    for itinerary in dataset.itineraries.values():
        theirs = padi_lines(itinerary, dataset.fx)
        if theirs is None:
            continue
        comparable += 1
        gap = abs(mandatory(itinerary_lines(itinerary, dataset.fx)) - mandatory(theirs))
        if round(gap) > 0:
            disagree += 1
        widest = max(widest, gap)

    fares = sorted(
        abs(float(dataset.fx.to_display(departure.price)[0].amount)
            - float(dataset.fx.to_display(departure.padi_price)[0].amount))
        for departure in dataset.departures if departure.padi_price is not None
    )
    figures: dict[str, Any] = {}
    if comparable:
        figures.update(fee_both=comparable, fee_disagree=disagree,
                       fee_widest=round(widest))
    if fares:
        figures.update(fare_pairs=len(fares), fare_gap=round(_median(fares)))
    return figures


def berth_count_gaps(payload: dict[str, Any]) -> dict[str, Any]:
    """The two places the Places column contradicts itself, counted.

    Both are statements about disagreement rather than about a boat, so both are
    read off the shipped payload -- the same blocks `app.js` reads, in the same
    slots, so the page and the sentence about the page cannot part company.

    `at_price` and `aboard` are different questions, so they are only ever
    compared on the one thing they must agree about: whether anything is left
    at all. A boat one seller calls full while the other still sells berths is
    the disagreement worth naming; two counts differing by three is not.
    """
    SELLER, SPOTS, CABINS, ABOARD = 0, 1, 2, 3
    disagree = 0
    dearer: list[int] = []
    for departure in payload["departures"]:
        blocks = departure.get("berths") or []
        spots = next((b[SPOTS] for b in blocks if b[SPOTS] is not None), None)
        aboard = next((b[ABOARD] for b in blocks if b[ABOARD] is not None), None)
        if spots is not None and aboard is not None and (spots == 0) != (aboard == 0):
            disagree += 1
        if spots != 0 or not departure.get("base"):
            continue
        rungs = next((b[CABINS] for b in blocks if b[CABINS]), [])
        left = [rung for rung in rungs if rung[2]]
        if left:
            cheapest = min(rung[1] for rung in left)
            dearer.append(round((cheapest / departure["base"] - 1) * 100))
    figures: dict[str, Any] = {"full_disagree": disagree}
    if dearer:
        figures.update(zero_bookable=len(dearer),
                       dearer_low=min(dearer), dearer_high=max(dearer))
    return figures


def _stated_figures(dataset: Dataset, payload: dict[str, Any]) -> dict[str, str]:
    """Every number the footer's prose states about the dataset.

    One dict, so `render` substitutes and the test asserts against the same
    source. A token with no figure behind it -- an entry bar nobody has
    measured, a fleet that charges for nitrox nowhere -- resolves to an em
    dash rather than to a plausible zero: "0 vessels publish a price" is a
    claim, and the honest output where the pass never ran is not a number.
    """
    counts: dict[str, int] = {}
    for value in nitrox_by_vessel(payload).values():
        counts[value] = counts.get(value, 0) + 1
    prices = nitrox_prices(dataset)
    bar = dataset.entry_bar
    gear = gear_prices(dataset)
    dives = week_dive_counts(dataset)
    gap = seller_gap(dataset)
    berths = berth_count_gaps(payload)
    padi_only = sum(1 for d in payload["departures"] if d.get("padi_only"))

    def figure(value: object) -> str:
        return "&mdash;" if value is None else f"{value:g}" if isinstance(
            value, float) else str(value)

    return {
        "__NITROX_INCLUDED__": figure(counts.get("included", 0)),
        "__NITROX_PRICED__": figure(counts.get("priced", 0)),
        "__NITROX_UNPRICED__": figure(counts.get("listed_unpriced", 0)),
        "__NITROX_ABSENT__": figure(counts.get("absent", 0)),
        "__NITROX_LOW__": figure(prices.get("low")),
        "__NITROX_HIGH__": figure(prices.get("high")),
        "__NITROX_MEDIAN__": figure(prices.get("median")),
        "__NITROX_PER_TRIP__": figure(prices.get("per_trip")),
        "__BOATS__": figure(payload["meta"]["counts"]["boats"]),
        "__BAR_BOTH__": figure(bar.get("both")),
        "__BAR_DISAGREE__": figure(bar.get("disagree")),
        "__BAR_PADI_STRICTER__": figure(bar.get("padi_stricter")),
        "__BAR_OURS_STRICTER__": figure(bar.get("ours_stricter")),

        # The sellers section. Every one of these had drifted: 230 PADI-only
        # sailings against 225, "43 of 74" fee books against 84 of 179, and a
        # "sixteen trips by more than 150 euro" whose widest gap is now 140 --
        # a sentence naming a bracket the data has left entirely.
        "__PADI_ONLY__": figure(padi_only),
        "__FEE_BOTH__": figure(gap.get("fee_both")),
        "__FEE_DISAGREE__": figure(gap.get("fee_disagree")),
        "__FEE_WIDEST__": figure(gap.get("fee_widest")),
        "__FARE_GAP__": figure(gap.get("fare_gap")),

        # The Places section: "the 24 sailings where they disagree" was 10.
        "__FULL_DISAGREE__": figure(berths.get("full_disagree")),
        "__ZERO_BOOKABLE__": figure(berths.get("zero_bookable")),
        "__ZERO_DEARER_LOW__": figure(berths.get("dearer_low")),
        "__ZERO_DEARER_HIGH__": figure(berths.get("dearer_high")),

        # Per dive: "the ten vessels that do publish a figure ... 15 to 21" is
        # 69 vessels and 6 to 24 since PADI's counts became the last resort.
        "__DIVES_VESSELS__": figure(dives.get("vessels")),
        "__DIVES_LOW__": figure(dives.get("low")),
        "__DIVES_HIGH__": figure(dives.get("high")),
        "__DIVES_SPREAD__": figure(dives.get("spread")),

        # Rental gear: 54 vessels against 65, and the extremes had changed boat.
        "__GEAR_VESSELS__": figure(gear.get("vessels")),
        "__GEAR_WEEK__": figure(gear.get("week")),
        "__GEAR_LOW_THIRD__": figure(gear.get("low_third")),
        "__GEAR_TOP_THIRD__": figure(gear.get("top_third")),
        "__GEAR_DEAREST__": figure(gear.get("dearest")),
        # A vessel name is the one figure here that is text, so it is escaped:
        # the fleet holds ampersands, and a name is the operator's own spelling
        # rather than something this file gets to tidy.
        "__GEAR_DEAREST_BOAT__": _escape(figure(gear.get("dearest_boat"))),
        "__GEAR_CHEAPEST__": figure(gear.get("cheapest")),
        "__GEAR_CHEAPEST_BOAT__": _escape(figure(gear.get("cheapest_boat"))),
    }


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


DOWNLOAD_LABELS: dict[str, tuple[str, str]] = {
    "egypt-2027.csv": ("Every trip as a spreadsheet",
                       "one row per departure, with the advertised price, what "
                       "it is down from where a seller has marked it down, the "
                       "fees and the total"),
    "egypt-2027.json": ("The dataset", "exactly what the page is built from"),
    "CHANGES.md": ("What changed, refresh by refresh",
                   "boats and trips added, fares moved, berths sold out"),
    # The two sources, unmerged. The dataset above is what this project made of
    # them; these are what each site said, so a reader can check the working
    # rather than take the merge on trust -- which is the whole argument this
    # page makes about operators and their fees.
    "itineraries.json": ("What liveaboard.com says per trip",
                         "reefs, dive count, group size and entry bar, from the "
                         "operator's own itinerary panel"),
    "padi_departures.json": ("What PADI Travel charges per sailing",
                            "its own berth price for the same boat on the same "
                            "date, for 601 of the departures here"),
    "padi.json": ("What PADI Travel says per trip",
                  "the certification it states and the dives it counts, for the "
                  "38 boats both sites sell"),
    # Every day of it, not just today's. The panel on the page shows one
    # reading and one diff; the book behind it holds a month, which is what
    # answers whether a boat's "Early Bird" has been running since spring or
    # appeared this morning.
    "deals.json": ("What PADI Travel is discounting, day by day",
                   "the deals listing read daily, so a price move is a diff "
                   "between two readings rather than a claim"),
}


def _downloads_html(available: list[str]) -> str:
    """Links to the files the build actually wrote, and nothing more.

    Relative, so they resolve wherever the site is served from and add no
    external host: the no-CDN invariant is about what the page reaches for,
    and these sit beside it.
    """
    if not available:
        return ""
    items = []
    for name in DOWNLOAD_LABELS:
        if name not in available:
            continue
        label, blurb = DOWNLOAD_LABELS[name]
        items.append(
            f'<li><a href="data/{name}" download>{label}</a> '
            f'<span class="dl-note">&mdash; {blurb}</span></li>'
        )
    return "<ul class=\"downloads\">" + "".join(items) + "</ul>"


HISTORY_DAYS = 7


def _pretty_day(iso: str) -> str:
    """``2026-08-30`` as ``30 August 2026``, or unchanged if it is not a date.

    Built rather than formatted: ``%-d`` is a glibc extension and would print a
    leading zero on one platform and not another, which is a diff in the built
    page depending on where it was built.
    """
    try:
        day = date.fromisoformat(iso)
    except ValueError:
        return iso
    return f"{day.day} {day.strftime('%B')} {day.year}"


def _recent_reports(book: list[dict], days: int) -> list[dict]:
    """The reports within ``days`` of the newest one recorded.

    Measured against the book, never against today, for the reason
    `export.recent_entries` is: a clock inside `render` makes the same
    committed inputs render a different page tomorrow, and the test that
    compares the committed page against a fresh render would turn `main` red
    with nobody having changed anything.
    """
    def when(entry: dict) -> date | None:
        try:
            return date.fromisoformat(str(entry.get("day", "")))
        except ValueError:
            return None

    newest = next((d for d in map(when, book) if d), None)
    if newest is None:
        return list(book)
    floor = newest - timedelta(days=days - 1)
    return [e for e in book if (when(e) or newest) >= floor]


def _changes_html(entries: list[tuple[str, str]], linked: bool,
                  structured: bool) -> str:
    """The last week of refreshes, newest first.

    Three states, and the middle one is why this still exists.

    With the **structured book** present the page renders the reports itself,
    from `payload.changes`, as rows that can be clicked through to the sailing
    they are about. This returns the container it draws into and nothing else:
    a second copy of the same reports as prose would be the same facts twice,
    at several kilobytes a day.

    With **only the Markdown** -- a checkout whose last refresh predates the
    book -- the prose is what there is, and it is served as it always was
    rather than showing an empty history. It converges: the next refresh writes
    a structured entry, and within a week the fallback is unused.

    With **neither**, the view says so. That is a fact about this build, not
    about the fleet: no refresh has been *recorded* here, which is not the
    claim that nothing moved.

    Three things the view must not imply, all one rule. A **day with no entry**
    is a day the refresh did not run, so the lead counts the refreshes it has
    and names the span they cover, and says nothing about the days between. An
    **empty log** is about this checkout. And a **repeated date is two
    refreshes**, printed separately under one heading for the day.
    """
    if structured:
        # Filled by app.js. Empty in the markup on purpose: a fresh checkout
        # has never run the refresh, and a container that rendered its own
        # absence as "nothing has changed" would be the site claiming a fact
        # about the fleet from a file that does not exist.
        return '<div id="changeLog"></div>'

    if not entries:
        return ('<p class="history-lead">No refresh is recorded in this build, '
                "so there is nothing to compare against. The log is written by "
                "<code>liveaboard.cli changes</code> on each refresh.</p>")

    days: list[tuple[str, list[str]]] = []
    for day, body in entries:
        if days and days[-1][0] == day:
            days[-1][1].append(body)
        else:
            days.append((day, [body]))

    n = len(entries)
    when = (f"from {_pretty_day(entries[-1][0])} to {_pretty_day(entries[0][0])}"
            if days[0][0] != days[-1][0] else f"on {_pretty_day(entries[0][0])}")
    lead = (
        f'<p class="history-lead">'
        f'{n} refresh{"" if n == 1 else "es"} recorded {when}. '
        f"A day with no entry is a day the refresh did not run, which is not "
        f"the same as a day nothing moved.</p>"
    )

    blocks = []
    for day, bodies in days:
        label = _pretty_day(day)
        if len(bodies) > 1:
            label += f" &middot; {len(bodies)} refreshes"
        blocks.append(f'<h3 class="history-day">{label}</h3>')
        blocks.extend(f'<pre class="changelog">{_escape(b)}</pre>' for b in bodies)

    more = ('<p><a href="data/CHANGES.md">Every refresh before these</a>.</p>'
            if linked else "")
    return lead + "".join(blocks) + more


def write_downloads(dataset: Dataset, out: Path, data_dir: Path | None) -> list[str]:
    """Put the numbers next to the page, and say which ones arrived.

    Published, not committed: ``site/data/`` is gitignored. A copy of a 2.4 MB
    dataset committed beside the original on every refresh would double what
    the repository grows by, daily, to hold two identical files. The Pages
    workflow uploads the whole ``site/`` directory, so generating them at build
    time puts them online without putting them in the history twice.

    Returns the names that exist, so the page only ever links to a file that
    is actually there.
    """
    folder = out / "data"
    folder.mkdir(parents=True, exist_ok=True)
    written = []

    (folder / "egypt-2027.csv").write_text(to_csv(dataset), encoding="utf-8")
    written.append("egypt-2027.csv")

    for name in ("egypt-2027.json", "CHANGES.md", "itineraries.json", "padi.json",
                 "padi_departures.json", "deals.json"):
        source = (data_dir / name) if data_dir else None
        if source and source.exists():
            (folder / name).write_text(source.read_text(encoding="utf-8"),
                                       encoding="utf-8")
            written.append(name)
    return written


# Characters a data: URI can carry unescaped. Everything else -- the "#" of
# every colour above all, which would otherwise start a fragment and truncate
# the icon -- is percent-encoded.
ICON_SAFE = "/:;=,()'"


def icon_data_uri(svg: str) -> str:
    """Inline ``templates/icon.svg`` into an attribute.

    A favicon is normally a second request, and this page makes none: the mark
    ships in the same file as everything else. Comments and the whitespace
    between tags go, because the file is read once by a human and served on
    every page load.

    The SVG carries its own ``prefers-color-scheme`` block, so one file covers
    both themes -- there is no second export to keep in step with the first.
    """
    svg = re.sub(r"<!--.*?-->", "", svg, flags=re.S)
    svg = re.sub(r"\s+", " ", svg).strip()
    svg = re.sub(r">\s+<", "><", svg)
    return "data:image/svg+xml," + quote(svg, safe=ICON_SAFE)


def render(
    dataset: Dataset,
    out_dir: Path | str,
    template_dir: Path | None = None,
    data_dir: Path | None = None,
) -> Path:
    """Write ``index.html`` and return its path."""
    templates = template_dir or TEMPLATE_DIR
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    payload = build_payload(dataset)

    # The structured change reports, for the page to render as rows.
    #
    # `data/CHANGES.md` is the durable human record and stays exactly what it
    # is; this is the same reports as data, written beside it by the same
    # command. The page used to read that Markdown back, escape it into a
    # `<pre>`, and serve the visitor a terminal transcript -- boat names cut
    # mid-word to fit eighty columns, on a page that has a table renderer, and
    # not one line of it clickable through to the sailing it was about (#143).
    #
    # Trimmed to the same window the prose view shows, and only the days the
    # book actually covers: a checkout whose last refresh predates the book
    # falls back to the Markdown rather than showing an empty history.
    book = (data_dir / "changes.json") if data_dir else None
    if book and book.exists():
        try:
            reports = json.loads(book.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            reports = []
        if isinstance(reports, list) and reports:
            payload["changes"] = _recent_reports(reports, HISTORY_DAYS)
    html = (templates / "index.html").read_text(encoding="utf-8")
    css = (templates / "style.css").read_text(encoding="utf-8")
    js = (templates / "app.js").read_text(encoding="utf-8")
    icon = (templates / "icon.svg").read_text(encoding="utf-8")

    # json.dumps escapes nothing that matters here except "</script>", which
    # would close the tag early. Escaping the slash keeps the JSON valid.
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")

    available = write_downloads(dataset, out, data_dir)

    # The last week of refreshes, inline; the whole history stays a file the
    # page links to. The log grows by several entries a day and this site ships
    # as one download with nothing fetched lazily, so an unbounded history
    # inside it would be paid for by every visitor forever. A week is the
    # window the ordinary question needs -- and it is measured against the
    # newest entry in the log rather than against today, so the same committed
    # inputs render the same page tomorrow.
    history = (data_dir / "CHANGES.md") if data_dir else None
    entries: list[tuple[str, str]] = []
    if history and history.exists():
        entries = recent_entries(history.read_text(encoding="utf-8"), HISTORY_DAYS)

    html = html.replace("/*STYLE*/", css)
    html = html.replace("/*APP*/", js)
    html = html.replace("__ICON__", icon_data_uri(icon))
    html = html.replace('"__DATA__"', data)
    html = html.replace("__GENERATED__", payload["meta"]["generated"])
    html = html.replace("__DOWNLOADS__", _downloads_html(available))
    html = html.replace("__CHANGES__",
                        _changes_html(entries, "CHANGES.md" in available,
                                      bool(payload.get("changes"))))
    # Figures the footer states in prose, substituted rather than typed. Two
    # data jobs were refused by `TestTheFooterCountsMatchTheData` on
    # 2026-08-31 -- each after a full crawl, because the counts only move when
    # a fetch runs and so the mismatch cannot appear on anybody's machine --
    # and the entry-bar sentence two sections down had gone stale unwatched,
    # claiming 19 and 41 where the data said 30 and 30. A number the page
    # derives cannot do either.
    for token, value in _stated_figures(dataset, payload).items():
        html = html.replace(token, value)

    target = out / "index.html"
    target.write_text(html, encoding="utf-8")
    return target
