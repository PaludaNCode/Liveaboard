"""Controlled vocabularies for the Red Sea liveaboard dataset.

Everything the site can filter, group or classify on is defined here, once.
Free-form strings are deliberately avoided: a fee code that nobody recognises
is a fee that silently vanishes from the true-cost total, which is the exact
failure this project exists to prevent.
"""

from __future__ import annotations

from enum import Enum


class FeeTier(str, Enum):
    """How unavoidable a cost is.

    The tier decides whether a line item lands in the true-cost total by
    default, which is the single most consequential judgement in the dataset.
    """

    BASE = "base"
    """The advertised headline price."""

    MANDATORY = "mandatory"
    """Payable by every guest, no exceptions, no opt-out. Park fees, port dues."""

    CONDITIONAL = "conditional"
    """Real for most guests but genuinely avoidable. Driven by a user toggle."""

    CUSTOMARY = "customary"
    """Not contractually owed, universally expected. Crew gratuities."""

    OPTIONAL = "optional"
    """Excluded unless explicitly chosen. Single supplement, courses, alcohol."""


DEFAULT_ON_TIERS = frozenset({FeeTier.BASE, FeeTier.MANDATORY, FeeTier.CUSTOMARY})
"""Tiers counted in the true cost without the user asking for them."""


class FeeBasis(str, Enum):
    """The unit a fee is quoted in, before normalisation to per-trip."""

    PER_TRIP = "per_trip"
    PER_NIGHT = "per_night"
    PER_DAY = "per_day"
    PER_DIVE = "per_dive"
    PER_PERSON_PER_DAY = "per_person_per_day"


class FeeCode(str, Enum):
    """Stable identifiers for the cost lines we track.

    Grouped by tier for readability; the authoritative tier for a given fee
    lives on the :class:`~liveaboard.models.FeeItem` itself, because operators
    genuinely disagree about which of these are mandatory.
    """

    BASE_FARE = "base_fare"

    MARINE_PARK = "marine_park"
    PORT_FEES = "port_fees"
    FUEL_SURCHARGE = "fuel_surcharge"
    VISA = "visa"
    TAX_VAT = "tax_vat"

    NITROX = "nitrox"
    GEAR_RENTAL = "gear_rental"
    DIVE_INSURANCE = "dive_insurance"
    AIRPORT_TRANSFER = "airport_transfer"
    GUIDED_DIVING = "guided_diving"

    GRATUITIES = "gratuities"

    SINGLE_SUPPLEMENT = "single_supplement"
    PRIVATE_GUIDE = "private_guide"
    COURSE = "course"
    TANK_15L = "tank_15l"
    ALCOHOL = "alcohol"


TOGGLEABLE: dict[FeeCode, str] = {
    FeeCode.NITROX: "nitrox",
    FeeCode.GEAR_RENTAL: "gear",
    FeeCode.DIVE_INSURANCE: "insurance",
    FeeCode.AIRPORT_TRANSFER: "transfers",
    FeeCode.GRATUITIES: "gratuities",
}
"""Fee codes the visitor can switch on and off, mapped to the site's toggle id."""


FEE_LABELS: dict[FeeCode, str] = {
    FeeCode.BASE_FARE: "Berth (advertised price)",
    FeeCode.MARINE_PARK: "Marine park fees",
    FeeCode.PORT_FEES: "Port & harbour dues",
    FeeCode.FUEL_SURCHARGE: "Fuel surcharge",
    FeeCode.VISA: "Egypt visa on arrival",
    FeeCode.TAX_VAT: "VAT / local tax",
    FeeCode.NITROX: "Nitrox",
    FeeCode.GEAR_RENTAL: "Equipment rental",
    FeeCode.DIVE_INSURANCE: "Dive insurance",
    FeeCode.AIRPORT_TRANSFER: "Airport transfers",
    FeeCode.GUIDED_DIVING: "Guided diving surcharge",
    FeeCode.GRATUITIES: "Crew gratuities",
    FeeCode.SINGLE_SUPPLEMENT: "Single cabin supplement",
    FeeCode.PRIVATE_GUIDE: "Private dive guide",
    FeeCode.COURSE: "Course",
    FeeCode.TANK_15L: "15 L tank",
    FeeCode.ALCOHOL: "Alcoholic drinks",
}


class Route(str, Enum):
    """The itinerary families Egyptian liveaboards actually sell."""

    NORTH_WRECKS_REEFS = "north_wrecks_reefs"
    RAS_MOHAMMED_TIRAN = "ras_mohammed_tiran"
    BDE = "bde"
    DEEP_SOUTH = "deep_south"
    FURY_SHOAL = "fury_shoal"
    ST_JOHNS = "st_johns"
    COMBINATION = "combination"


ROUTE_LABELS: dict[Route, str] = {
    Route.NORTH_WRECKS_REEFS: "North — Wrecks & Reefs",
    Route.RAS_MOHAMMED_TIRAN: "Ras Mohammed & Tiran",
    Route.BDE: "Brothers, Daedalus & Elphinstone",
    Route.DEEP_SOUTH: "Deep South",
    Route.FURY_SHOAL: "Fury Shoal & Sataya",
    Route.ST_JOHNS: "St John's",
    Route.COMBINATION: "Combination / Ultimate",
}


class DiverLevel(str, Enum):
    """Minimum realistic entry bar, ordered from least to most demanding."""

    OPEN_WATER = "open_water"
    ADVANCED = "advanced"
    ADVANCED_50 = "advanced_50"
    EXPERIENCED_100 = "experienced_100"


DIVER_LEVEL_LABELS: dict[DiverLevel, str] = {
    DiverLevel.OPEN_WATER: "Open Water",
    DiverLevel.ADVANCED: "Advanced Open Water",
    DiverLevel.ADVANCED_50: "Advanced + 50 dives",
    DiverLevel.EXPERIENCED_100: "Advanced + 100 dives",
}

DIVER_LEVEL_ORDER: list[DiverLevel] = [
    DiverLevel.OPEN_WATER,
    DiverLevel.ADVANCED,
    DiverLevel.ADVANCED_50,
    DiverLevel.EXPERIENCED_100,
]


class Theme(str, Enum):
    """What a trip is actually for, beyond the route name."""

    SHARKS_PELAGIC = "sharks_pelagic"
    HAMMERHEADS = "hammerheads"
    OCEANIC_WHITETIP = "oceanic_whitetip"
    WRECKS = "wrecks"
    REEF = "reef"
    DOLPHINS = "dolphins"
    MACRO = "macro"
    PHOTOGRAPHY = "photography"
    TECH = "tech"
    CURRENT = "current"


THEME_LABELS: dict[Theme, str] = {
    Theme.SHARKS_PELAGIC: "Sharks & pelagics",
    Theme.HAMMERHEADS: "Hammerheads",
    Theme.OCEANIC_WHITETIP: "Oceanic whitetips",
    Theme.WRECKS: "Wrecks",
    Theme.REEF: "Coral reef",
    Theme.DOLPHINS: "Dolphins",
    Theme.MACRO: "Macro",
    Theme.PHOTOGRAPHY: "Photography",
    Theme.TECH: "Technical diving",
    Theme.CURRENT: "Strong current",
}


class SourceKind(str, Enum):
    """Where a value came from. Rendered on the site next to the number."""

    SCRAPED = "scraped"
    """Read from a source site by an adapter, on the recorded date."""

    OPERATOR_STATED = "operator_stated"
    """Taken from an operator's own published terms."""

    SEED_ESTIMATE = "seed_estimate"
    """Researched placeholder. Plausible, not authoritative, never bookable."""

    DERIVED = "derived"
    """Computed by this project from other fields."""


UNVERIFIED_SOURCES = frozenset({SourceKind.SEED_ESTIMATE})
"""Source kinds the site must visibly flag as not-a-real-quote."""
