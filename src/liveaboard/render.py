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
from .money import DISPLAY_CURRENCY
from .pricing import (
    DEFAULT_TOGGLES,
    base_line,
    compute,
    itinerary_lines,
    mandatory_known,
    resolve_fees,
)
from .taxonomy import FEE_LABELS

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

    classifications = dataset.classifications()

    itineraries: dict[str, Any] = {}
    # The resolved fee rows, once per itinerary. Reused below to decide whether
    # a departure needs its own copy, which is why they are kept as dicts here
    # rather than re-serialised per departure.
    shared_lines: dict[str, list[dict[str, Any]]] = {}
    for key, itinerary in dataset.itineraries.items():
        classification = classifications[key]
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
            "requirements": itinerary.requirements.as_dict(),
            **classification.as_dict(),
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
        "fee_labels": {code.value: label for code, label in FEE_LABELS.items()},
        "itineraries": itineraries,
        "departures": departures,
    }


def render(dataset: Dataset, out_dir: Path | str, template_dir: Path | None = None) -> Path:
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

    html = html.replace("/*STYLE*/", css)
    html = html.replace("/*APP*/", js)
    html = html.replace('"__DATA__"', data)
    html = html.replace("__GENERATED__", payload["meta"]["generated"])

    target = out / "index.html"
    target.write_text(html, encoding="utf-8")
    return target
