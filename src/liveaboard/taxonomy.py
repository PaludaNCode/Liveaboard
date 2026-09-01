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
    PER_WEEK = "per_week"
    """Hire priced by the week, which is how the gear dialog quotes everything.

    Trips run from three nights to fourteen, so this needs a rule and the page
    states none. It rounds up: a diver keeps the kit for the whole trip, so
    nine nights is two weeks' hire, and rounding down would undercharge the
    one number this site exists to get right.
    """


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
    ENVIRONMENT_TAX = "environment_tax"
    SERVICE_CHARGE = "service_charge"
    COMBINED_FEES = "combined_fees"
    # Five charges PADI's fee book names and liveaboard.com's does not, each
    # mandatory and each blocking a trip's total for want of a code to put it
    # under. One per wording rather than one bucket, because the parser keeps
    # one entry per code: Andromeda bills a Navy fee *and* an Environmental/
    # Government Fee on the same trip, so a shared code would have silently
    # dropped one of them and shown the boat cheaper by exactly what it left
    # out. Coast guard and navy are two authorities and stay two codes on the
    # same reasoning, even though no boat today bills both.
    LOCAL_FEES = "local_fees"
    HOSPITALITY_FEE = "hospitality_fee"
    ROUTE_SUPPLEMENT = "route_supplement"
    COAST_GUARD = "coast_guard"
    NAVY_FEE = "navy_fee"
    HYPERBARIC_LEVY = "hyperbaric_levy"
    """A per-diver contribution to the recompression chamber.

    Its own code rather than a share of the park or service fee: it is a
    charge to a named third party -- the chamber -- and the two trips that bill
    it also bill park fees and a service charge separately. Filing it under
    either would have been this parser deciding two lines are one.
    """

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
    LAUNDRY = "laundry"
    SNORKEL_GEAR = "snorkel_gear"
    EXTRA_DIVES = "extra_dives"
    LAND_EXCURSION = "land_excursion"
    NATURALIST_GUIDE = "naturalist_guide"
    NITROX_COURSE = "nitrox_course"


TOGGLEABLE: dict[FeeCode, str] = {
    FeeCode.NITROX: "nitrox",
    FeeCode.GEAR_RENTAL: "gear",
}
"""Fee codes the visitor can switch on and off, mapped to the site's toggle id.

Two, because the page is for comparing trips and every extra control is one
more way two rows can differ for a reason that is not the boat.

Insurance and transfers left: they are not part of what an operator charges
for the week, and carrying them made the headline number answer a different
question per visitor. Gratuities left for the opposite reason -- tips are
customary rather than optional, so they now always count where an operator
states an amount, and the total says "+ tips" where one does not.
"""


FEE_LABELS: dict[FeeCode, str] = {
    FeeCode.BASE_FARE: "Berth (advertised price)",
    FeeCode.MARINE_PARK: "Marine park fees",
    FeeCode.PORT_FEES: "Port & harbour dues",
    FeeCode.FUEL_SURCHARGE: "Fuel surcharge",
    FeeCode.VISA: "Egypt visa on arrival",
    FeeCode.TAX_VAT: "VAT / local tax",
    FeeCode.ENVIRONMENT_TAX: "Environment tax",
    FeeCode.SERVICE_CHARGE: "Mandatory service charge",
    FeeCode.COMBINED_FEES: "Park, port & fuel fees (billed together)",
    # The operators' own words, tidied for case and nothing else. "Local fees"
    # says less than a reader would like and is exactly what the boat wrote;
    # naming it "Port & harbour dues" would be this code deciding what the
    # charge covers. Where the wording differs from these at all it survives
    # as the line's note, so the page always carries what was actually said.
    FeeCode.LOCAL_FEES: "Local fees",
    FeeCode.HOSPITALITY_FEE: "Hospitality fee",
    FeeCode.HYPERBARIC_LEVY: "Hyperbaric chamber levy",
    FeeCode.ROUTE_SUPPLEMENT: "Route supplement",
    FeeCode.COAST_GUARD: "Coast guard fee",
    FeeCode.NAVY_FEE: "Navy fee",
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
    FeeCode.LAUNDRY: "Laundry & pressing",
    FeeCode.SNORKEL_GEAR: "Snorkel gear",
    FeeCode.EXTRA_DIVES: "Extra dives",
    FeeCode.LAND_EXCURSION: "Excursions",
    FeeCode.NATURALIST_GUIDE: "Naturalist / snorkelling guide",
    FeeCode.NITROX_COURSE: "Nitrox course",
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


DIVER_LEVEL_BARS: list[tuple[DiverLevel, str, int]] = [
    (DiverLevel.OPEN_WATER, "Open Water", 0),
    (DiverLevel.ADVANCED, "Advanced", 0),
    (DiverLevel.ADVANCED_50, "Advanced", 50),
    (DiverLevel.EXPERIENCED_100, "Advanced", 100),
]
"""The entry bar as the table prints it: a certification and a dive count.

`DIVER_LEVEL_LABELS` names each level on its own, which is what a sentence
needs -- "PADI Travel states Advanced Open Water". A column cannot use those:
two of the four fold the dive count into the label, so printing the level
beside `min_logged_dives` gave "Advanced + 50 dives, 50 logged dives" on the
commonest bar in the fleet, and the page carried a regex to spot it.

So the two facts are separated here instead. The middle field is the
certification alone -- *Advanced*, the shorthand every operator writes, not
the card's full name -- and the last is the dive count the level itself
implies, which is 50 for `ADVANCED_50` by definition and 0 where the level
says nothing about dives. The printed count is the greater of that and the
trip's own `min_logged_dives`, so a level's implied bar can never be softened
by a trip that states a smaller number, and a trip stating a larger one is
shown as it stated it.

Ordered least to most demanding, like `DIVER_LEVEL_ORDER`, because the page
sorts and ranks on the position: shipped as a list rather than a mapping so
the order travels with the labels instead of being a second copy in the
browser. `TestTheEntryBarVocabularyIsOrdered` holds the two in step.
"""






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
