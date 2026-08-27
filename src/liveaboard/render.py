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

from .classify import classify, themes_in_season
from .dataset import Dataset
from .money import DISPLAY_CURRENCY
from .pricing import DEFAULT_TOGGLES, compute, mandatory_known, resolve_fees
from .taxonomy import (
    DIVER_LEVEL_LABELS,
    DIVER_LEVEL_ORDER,
    FEE_LABELS,
    ROUTE_LABELS,
    THEME_LABELS,
    FeeCode,
    SourceKind,
)

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
    for key, itinerary in dataset.itineraries.items():
        classification = classifications[key]
        boat = dataset.boat_for(itinerary)
        operator = dataset.operator_for(itinerary)
        itineraries[key] = {
            "id": key,
            "name": itinerary.name,
            "boat_id": boat.id,
            "boat": boat.name,
            "operator": operator.name,
            "nights": itinerary.nights,
            "dives": itinerary.dives,
            "dives_estimated": itinerary.dives_estimated,
            "port_from": itinerary.port_from,
            "port_to": itinerary.port_to,
            "one_way": itinerary.port_from != itinerary.port_to,
            "dive_sites": itinerary.dive_sites,
            "guests": boat.guests,
            "summary": itinerary.summary,
            "source_url": itinerary.source_url,
            "requirements": itinerary.requirements.as_dict(),
            **classification.as_dict(),
        }

    departures: list[dict[str, Any]] = []
    for departure in sorted(dataset.departures, key=lambda d: (d.start, d.id)):
        itinerary = dataset.itinerary_for(departure)
        breakdown = compute(itinerary, departure, dataset.fx)
        themes = classifications[itinerary.id].themes

        # An itinerary with no fee lines is not one with no fees; it is one
        # nobody has looked at yet. Reporting a true cost equal to the
        # advertised price, and a perfect honesty score, would make this site
        # commit exactly the omission it exists to expose.
        fees_known = bool(resolve_fees(itinerary, departure))
        # Listing only optional extras is not the same as having no required
        # ones, and scoring it as such put the least forthcoming operators at
        # the top of the honesty ranking. See pricing.mandatory_known.
        mandatory = mandatory_known(itinerary, departure)

        departures.append(
            {
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
                "base": float(breakdown.base.rounded),
                "lines": [line.as_dict() for line in breakdown.lines],
                "peak_themes": [t.value for t in themes_in_season(themes, departure.start.month)],
                "verified": departure.price_provenance.is_verified,
            }
        )

    months = sorted({d["month"] for d in departures})
    used_routes = [i["route"] for i in itineraries.values() if i["route"]]
    used_themes = {t for i in itineraries.values() for t in i["themes"]}
    used_levels = {i["level"] for i in itineraries.values()}

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
        "facets": {
            "routes": [
                {"id": r, "label": ROUTE_LABELS[_route_enum(r)]}
                for r in _ordered_unique(used_routes)
            ],
            "levels": [
                {"id": lvl.value, "label": DIVER_LEVEL_LABELS[lvl]}
                for lvl in DIVER_LEVEL_ORDER
                if lvl.value in used_levels
            ],
            "themes": [
                {"id": t, "label": THEME_LABELS[_theme_enum(t)]}
                for t in sorted(used_themes)
            ],
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


def _route_enum(value: str):
    from .taxonomy import Route

    return Route(value)


def _theme_enum(value: str):
    from .taxonomy import Theme

    return Theme(value)


def _ordered_unique(values: list[str]) -> list[str]:
    """Preserve the taxonomy's route order rather than first-seen order."""
    from .taxonomy import Route

    order = [r.value for r in Route]
    present = set(values)
    return [v for v in order if v in present]


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
