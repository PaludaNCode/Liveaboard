"""Loading, validating and querying the trip dataset."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterator

from .models import Boat, Departure, Itinerary, Operator
from .money import DISPLAY_CURRENCY, FxTable
from .taxonomy import SourceKind


class DatasetError(ValueError):
    """Raised when the dataset is internally inconsistent.

    Loud by design: a departure pointing at a missing itinerary would otherwise
    become a trip that silently never appears on the site.
    """


@dataclass(slots=True)
class Dataset:
    """Everything the site renders, plus the metadata to justify it."""

    operators: dict[str, Operator] = field(default_factory=dict)
    boats: dict[str, Boat] = field(default_factory=dict)
    itineraries: dict[str, Itinerary] = field(default_factory=dict)
    departures: list[Departure] = field(default_factory=list)
    fx: FxTable | None = None
    generated: date | None = None
    notes: str | None = None
    cabin_names: list[str] = field(default_factory=list)
    """Cabin names, pooled once and indexed by every ladder on every departure.

    2,982 cabins share 157 names — a boat calls its rooms the same thing on
    every week it sells — so the page ships the names once and the rungs ship
    an index. Halves what the ladder costs a one-file site.
    """
    sellers: list[str] = field(default_factory=list)
    """Who sells these sailings, pooled and indexed by every berth block."""
    deals: dict[str, Any] = field(default_factory=dict)
    """What PADI Travel is discounting, and what moved since the day before.

    Not a price the site quotes anywhere: an offer on a berth, dated, with the
    vessel page it was read from beside it. It is carried on the dataset rather
    than fetched by the page because everything here is — the site is one file
    with nothing lazily loaded, and a panel that reached out for its own numbers
    would be the first thing on it that could arrive blank.

    Empty until ``tools/fetch_deals.py`` has run, which is the ordinary state of
    a fresh checkout and renders as no panel rather than as no deals.
    """

    berths_read: str | None = None
    """The day the berth counts were read.

    One date for the whole book, and the most load-bearing caveat attached to
    it: a count is what the seller claimed when it was read, not a verified
    number, and it is stale by morning. Stated once rather than on each of 864
    departures.
    """

    # -- construction ----------------------------------------------------

    @classmethod
    def load(cls, path: Path | str) -> Dataset:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(payload)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Dataset:
        currency = payload.get("default_currency", DISPLAY_CURRENCY)
        generated = payload.get("generated")

        dataset = cls(
            operators={o["id"]: Operator.from_dict(o) for o in payload.get("operators", [])},
            boats={b["id"]: Boat.from_dict(b) for b in payload.get("boats", [])},
            itineraries={
                i["id"]: Itinerary.from_dict(i, currency)
                for i in payload.get("itineraries", [])
            },
            departures=[
                Departure.from_dict(d, currency) for d in payload.get("departures", [])
            ],
            fx=FxTable.from_dict(payload["fx"]) if "fx" in payload else None,
            generated=date.fromisoformat(generated) if generated else None,
            notes=payload.get("notes"),
            cabin_names=list(payload.get("cabin_names") or []),
            sellers=list(payload.get("sellers") or []),
            berths_read=payload.get("berths_read") or None,
            deals=dict(payload.get("deals") or {}),
        )
        dataset.validate()
        return dataset

    # -- integrity -------------------------------------------------------

    def validate(self) -> None:
        """Check referential integrity and date sanity."""
        problems: list[str] = []

        for itinerary in self.itineraries.values():
            if itinerary.operator_id not in self.operators:
                problems.append(f"itinerary {itinerary.id}: unknown operator {itinerary.operator_id}")
            if itinerary.boat_id not in self.boats:
                problems.append(f"itinerary {itinerary.id}: unknown boat {itinerary.boat_id}")
            if itinerary.nights <= 0:
                problems.append(f"itinerary {itinerary.id}: nights must be positive")

        seen: set[str] = set()
        for departure in self.departures:
            if departure.id in seen:
                problems.append(f"duplicate departure id {departure.id}")
            seen.add(departure.id)
            if departure.itinerary_id not in self.itineraries:
                problems.append(
                    f"departure {departure.id}: unknown itinerary {departure.itinerary_id}"
                )
            if departure.end < departure.start:
                problems.append(f"departure {departure.id}: ends before it starts")

        if self.fx is None:
            problems.append("dataset has no fx table")

        if problems:
            raise DatasetError("; ".join(problems))

    # -- queries ---------------------------------------------------------

    def itinerary_for(self, departure: Departure) -> Itinerary:
        return self.itineraries[departure.itinerary_id]

    def boat_for(self, itinerary: Itinerary) -> Boat:
        return self.boats[itinerary.boat_id]

    def operator_for(self, itinerary: Itinerary) -> Operator:
        return self.operators[itinerary.operator_id]

    def in_window(self, start: date, end: date) -> Iterator[Departure]:
        """Departures whose first day falls inside the window, chronologically."""
        for departure in sorted(self.departures, key=lambda d: (d.start, d.id)):
            if start <= departure.start <= end:
                yield departure


    @property
    def is_fully_verified(self) -> bool:
        """True when no displayed price is a researched placeholder."""
        return all(
            d.price_provenance.kind is not SourceKind.SEED_ESTIMATE
            for d in self.departures
        )

    @property
    def source_kinds(self) -> set[SourceKind]:
        return {d.price_provenance.kind for d in self.departures}
