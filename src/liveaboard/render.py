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
from datetime import date
from pathlib import Path
from typing import Any

from .dataset import Dataset
from .export import latest_entry, to_csv
from .money import DISPLAY_CURRENCY
from .pricing import (
    DEFAULT_TOGGLES,
    base_line,
    compute,
    itinerary_lines,
    mandatory_known,
    resolve_fees,
)
from .taxonomy import DIVER_LEVEL_LABELS, FEE_LABELS

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
        operator = dataset.operator_for(itinerary)
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
            "boat": boat.name,
            "operator": operator.name,
            "nights": itinerary.nights,
            # Zero where the operator publishes no count. The page prints
            # nothing rather than dividing by an assumption.
            "dives": itinerary.dives,
            "port_from": itinerary.port_from,
            "port_to": itinerary.port_to,
            "one_way": itinerary.port_from != itinerary.port_to,
            "dive_sites": itinerary.dive_sites,
            "region": itinerary.region,
            "guests": boat.guests,
            "summary": itinerary.summary,
            "source_url": itinerary.source_url,
            # What the operator states about the entry bar. Kept because it
            # is their claim; the route, theme and level the site used to
            # infer beside it were ours, unread by the page, and gone.
            "requirements": itinerary.requirements.as_dict(),
        }

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

        entry: dict[str, Any] = {
            "id": departure.id,
            "fees_known": fees_known,
            "mandatory_known": mandatory,
            "itinerary_id": itinerary.id,
            "boat_id": itinerary.boat_id,
            "start": departure.start.isoformat(),
            "end": departure.end.isoformat(),
            "month": departure.start.month,
            "nights": itinerary.nights,
            "spaces_left": departure.spaces_left,
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
        # Compared against ``base``, never against the total. Both are berth
        # prices; the total adds fees PADI does not publish, so measuring one
        # against the other would show PADI cheaper by exactly the fees it never
        # disclosed. Two numbers only, and only where PADI sells the date: a
        # field written per departure ships 892 times.
        if departure.padi_price is not None:
            padi_display, _ = dataset.fx.to_display(departure.padi_price)
            entry["padi"] = float(padi_display.rounded)
            entry["padi_delta"] = round(float(padi_display.rounded) - entry["base"], 2)

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
            "counts": {
                "departures": len(departures),
                "itineraries": len(itineraries),
                "boats": len(dataset.boats),
                "operators": len(dataset.operators),
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
                {"id": key, "label": TOGGLE_LABELS[key], "default": DEFAULT_TOGGLES[key]}
                for key in TOGGLE_LABELS
            ],
        },
        # The pools every cabin ladder indexes into. Shipped once for the same
        # reason the fee labels are: one vocabulary, defined in one place.
        "cabin_names": dataset.cabin_names,
        "sellers": dataset.sellers,
        "fee_labels": {code.value: label for code, label in FEE_LABELS.items()},
        # Shipped for the same reason the fee labels are: one vocabulary,
        # defined once. Nothing on the page prints these today -- the Entry
        # column that did was removed as noise -- but the bar is in every
        # itinerary record and in the published downloads, so a reader who
        # wants it has the vocabulary to read it with.
        "level_labels": {
            level.value: label for level, label in DIVER_LEVEL_LABELS.items()
        },
        "itineraries": itineraries,
        "departures": departures,
    }


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


DOWNLOAD_LABELS: dict[str, tuple[str, str]] = {
    "egypt-2027.csv": ("Every trip as a spreadsheet",
                       "one row per departure, with the advertised price, the "
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


def _changes_html(entry: str, linked: bool) -> str:
    """The last refresh's report, verbatim, in a block that scrolls."""
    if not entry:
        return ""
    more = ('<p><a href="data/CHANGES.md">Every refresh before this one</a>.</p>'
            if linked else "")
    return (f'<pre class="changelog">{_escape(entry)}</pre>{more}')


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
                 "padi_departures.json"):
        source = (data_dir / name) if data_dir else None
        if source and source.exists():
            (folder / name).write_text(source.read_text(encoding="utf-8"),
                                       encoding="utf-8")
            written.append(name)
    return written


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
    html = (templates / "index.html").read_text(encoding="utf-8")
    css = (templates / "style.css").read_text(encoding="utf-8")
    js = (templates / "app.js").read_text(encoding="utf-8")

    # json.dumps escapes nothing that matters here except "</script>", which
    # would close the tag early. Escaping the slash keeps the JSON valid.
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")

    available = write_downloads(dataset, out, data_dir)

    # What moved on the last refresh, inline. The whole history is a file the
    # page links to: it grows by an entry a day, and this site ships as one
    # download with nothing fetched lazily, so an unbounded log inside it would
    # be paid for by every visitor forever.
    history = (data_dir / "CHANGES.md") if data_dir else None
    entry = ""
    if history and history.exists():
        entry = latest_entry(history.read_text(encoding="utf-8"))

    html = html.replace("/*STYLE*/", css)
    html = html.replace("/*APP*/", js)
    html = html.replace('"__DATA__"', data)
    html = html.replace("__GENERATED__", payload["meta"]["generated"])
    html = html.replace("__DOWNLOADS__", _downloads_html(available))
    html = html.replace("__CHANGES__", _changes_html(entry, "CHANGES.md" in available))

    target = out / "index.html"
    target.write_text(html, encoding="utf-8")
    return target
