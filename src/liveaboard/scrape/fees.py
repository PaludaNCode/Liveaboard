"""Parse liveaboard.com's "Required Extras" and "Optional Extras" blocks.

The disclosure looks like this, verbatim from a vessel page:

    Required Extras: Environment Tax (€45), Fuel Surcharge (€60-70 / trip),
    National Park Fees (€35-100 / trip), Port Fees (€35).

    Optional Extras: Gratuities (€80), Nitrox (€30 / trip),
    Nitrox Course (€250 / item), Private Dive Guide (€500 / trip),
    Rental Gear, Scuba Diving Courses (€300-350),
    Laundry / Pressing Services (€5 / item).

Three details drive the whole design here:

* **Amounts are ranges as often as not.** "€35-100" for park fees is a 65 euro
  spread on a supposedly fixed cost. Collapsing it to the low end would
  understate the bill, which is the exact failure this project exists to
  correct, so both ends are kept and the total is reported as a range.

* **Some extras carry no price at all** — "Rental Gear" is listed and left
  blank. That is a third state, distinct from zero and from absent, and it has
  to survive to the page.

* **The site's own Required/Optional split is authoritative**, for whether a
  cost is escapable *and* for whether it is counted. It used to decide only the
  first: gratuities were promoted into the counted total from the Optional
  block because tips are paid by nearly everyone. See `_tier_for` for why that
  was overruling the seller rather than reading it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..taxonomy import FEE_LABELS, FeeBasis, FeeCode, FeeTier

BLOCK = re.compile(
    r"\b(Included|Required|Optional)(?:\s+Extras)?\s*:\s*(.+?)"
    r"(?=\b(?:Included|Required|Optional)(?:\s+Extras)?\s*:|$)",
    re.I | re.S,
)
"""The three blocks the disclosure is written in, and it was read as two.

**Every one of the 79 vessel pages carries an `Included:` block** and nothing
opened it. 63 pages state all three; 6 state Included and Optional with no
Required block at all. It sits immediately above the two that were read, in the
same paragraph, in the same comma-separated prose:

    Included: VAT, Drinking Water, Soft drinks, Tea & Coffee, Welcome
    Cocktails, Full-Board Meal Plan (All meals), Snacks, Diving Package, Night
    Dives, Nitrox, Snorkeling Guide, Beach Towels, Cabin Towels, Complimentary
    Toiletries, Deck Towels, WiFi internet.
    Required Extras: Mandatory Service Charge (€10 / day), National Park Fees
    (€10 / day), Port Fees (€5 / trip).
    Optional Extras: Airport Transfer, Hotel Transfer, ...

That is Bella 2's, and the missing line it cost is **nitrox**: the other seller
charges 50 EUR for it on that boat and this one says it is in the fare, and the
page showed neither. **Included fees stay in the breakdown at zero** is an
invariant here, and it was being kept on PADI's side alone -- `whatsIncludedNew`
has been read since the same rule was noticed there. Two bills in one expanded
row were disclosing at different depths, which is the exact wording of the
finding that fixed the other seller.

Read across the fleet it is 273 lines on 79 vessels: VAT on 79, a transfer on
59, nitrox on 49, a fuel surcharge on 16, port fees on 10, environmental tax on
9, park fees on 8.

`Extras` is optional after the heading word so that `drift` can rebuild this
text from a stored excerpt, where the key is the bare word. `Required` and
`Optional` never appear followed by a colon anywhere else in a vessel page's
text, and `Included` appears once -- checked, because this regex is handed
`document.body.innerText` and every loosening of it is a chance to mine the
page for a charge nobody made.
"""

ENTRY = re.compile(
    r"""
    (?P<label>[^,()]+?)                      # "National Park Fees"
    \s*
    (?:\(                                    # optional bracketed amount
        \s*(?P<currency>[€$£])?\s*
        (?P<low>\d[\d.,]*)
        (?:\s*[-–]\s*(?P<high>\d[\d.,]*))?   # "60-70"
        \s*(?:/\s*(?P<basis>trip|item|day|night|dive|person))?
        [^)]*
    \))?
    \s*(?P<end>[,.]|$)
    """,
    re.I | re.X,
)

CURRENCIES = {"€": "EUR", "$": "USD", "£": "GBP"}

BASES = {
    "trip": FeeBasis.PER_TRIP,
    "item": FeeBasis.PER_TRIP,  # an "item" is one purchase on one trip
    "day": FeeBasis.PER_DAY,
    "night": FeeBasis.PER_NIGHT,
    "dive": FeeBasis.PER_DIVE,
    "person": FeeBasis.PER_TRIP,
}

MAX_LABEL_CHARS = 60
"""Longest plausible label for one extra.

Anything longer is the page running on past the end of the list, and it is
where the entry list stops. A live run without this bound swallowed the vessel
specifications, a global destination menu and raw CSS.
"""

MAX_LABEL_WORDS = 6
"""Most words a fee label ever has.

The length cap alone was not enough: a published dataset still carried an
airport transfer charged from "Pay by bank transfer or online with…", nitrox
from "Diving Nitrox available Free Nitrox Shaded…", VAT from "Show prices
Drawings & Vessel Layouts Cabin…" and a fuel surcharge from "meters Top speed
11 Knots Cruising speed…".

Each of those is a sentence or a specification row. Real entries are short noun
phrases — "Environment Tax", "Rental Gear", "Laundry / Pressing Services" — and
none reaches seven words, while every fabrication above exceeds it.
"""

# Ordered longest-first so "Nitrox Course" never resolves as "Nitrox", and
# anchored on word boundaries throughout.
#
# Substring matching produced fees out of thin air on a live run: "vat" inside
# "renovated", "visa" inside "Visayas", "tip" inside a boat named Tip Top II,
# "transfer" inside "pay by bank transfer". Every needle below must therefore
# match whole words, and the vaguest ones ("fuel", "course", "transfer") carry
# enough context to mean only the fee.
LABEL_PATTERNS: tuple[tuple[str, FeeCode], ...] = (
    # Required on three of twelve vessels and previously nameless, so it was
    # dropped from the true cost of every one of them.
    # Every one of these takes a plural. They were written singular against
    # liveaboard.com's wording and read as complete, and the gap only showed
    # when the same table was pointed at a second source that happens to
    # pluralise: "Fuel surcharges" is PADI's commonest mandatory charge, 116
    # entries of it, and not one matched "fuel surcharge". The words are the
    # operators' either way -- the fleet is shared -- so the plurals are a fix
    # to both readers and not an accommodation of one.
    (r"\b(?:mandatory\s+)?service\s+(?:charges?|fees?)\b", FeeCode.SERVICE_CHARGE),
    (r"\bnational park\b|\bmarine park\b|\bpark fees?\b", FeeCode.MARINE_PARK),
    # Conservation and reef tax sit here rather than under the park fee. Both
    # are the Red Sea's environmental levy under the name the operator gives
    # it -- "Governamental Reef Tax", misspelling and all, is what one boat
    # calls the charge another bills as "Environmental tax" -- and the park fee
    # is a separate line that several of them bill alongside it. Folding the
    # two would merge two charges a diver pays both of.
    # `Environmental/Government Fee` joins them rather than getting a code of
    # its own: this is the same Red Sea levy under a name that also credits the
    # government, which is precisely the case the paragraph above describes --
    # one boat's "Governamental Reef Tax" is another's "Environmental tax". It
    # is spelled out rather than generalised to "environmental anything",
    # because the fleet also writes `Environmental and Route Fees`, which names
    # two charges and is left declined rather than filed under half of itself.
    (r"\benvironment(?:al)?\s+tax(?:es)?\b|\beco\s+tax\b"
     r"|\bconservation\s+(?:fees?|charges?)\b|\breef\s+tax(?:es)?\b"
     r"|\benvironmental\s*/\s*government\s+fees?\b",
     FeeCode.ENVIRONMENT_TAX),
    (r"\bfuel\s+(?:surcharges?|fees?|supplements?)\b", FeeCode.FUEL_SURCHARGE),
    (r"\bport\s+fees?\b|\bharbou?r\s+(?:fees?|dues)\b", FeeCode.PORT_FEES),
    # Six wordings PADI's fee book uses and liveaboard.com's does not. Each is
    # `isMandatory` on the source's own say-so, each is priced, and between
    # them they were the *only* thing keeping 41 trips from claiming a total --
    # a berth price on the page with no bill beside it, which is the state this
    # site exists to correct in other people.
    #
    # The same fix that paid last time. Pointing this table at a second source
    # showed every needle was singular and that "Fuel surcharges", PADI's
    # commonest mandatory line at 116 entries, matched none of them.
    #
    # Listed as the operators write them rather than generalised into "any
    # authority charge", on the rule PORT_ALIASES and TITLE_FIXES already keep:
    # a near-miss rule that catches these also catches something that only
    # looks like them. `Cost Gard Fee` is a misspelling on the operator's side
    # and is in the table for the same reason the two misspellings of Daedalus
    # are -- the trip's own sibling entries name the charge correctly.
    (r"\blocal\s+fees?\b", FeeCode.LOCAL_FEES),
    (r"\bhospitality\s+(?:fees?|charges?)\b", FeeCode.HOSPITALITY_FEE),
    (r"\broute\s+supplements?\b", FeeCode.ROUTE_SUPPLEMENT),
    (r"\bcoast\s*guard\b|\bcost\s+gard\b", FeeCode.COAST_GUARD),
    (r"\bnavy\s+(?:fees?|charges?)\b", FeeCode.NAVY_FEE),
    # A contribution to the recompression chamber, billed per diver on two of
    # PADI's trips and declined until now. Narrow on the same rule as the five
    # above: the word "chamber" alone would catch a cabin description.
    (r"\bhyperbaric\b|\brecompression\s+chamber\b", FeeCode.HYPERBARIC_LEVY),
    # Split from the general course line for the same reason as snorkel gear:
    # vessels list "Nitrox Course (€99)" and "Scuba Diving Courses (€79-110)"
    # as separate priced entries, and one entry per code drops the second.
    #
    # **A qualification is not a gas fill**, and PADI names the qualification
    # the way its own courses are named rather than with the word "course":
    # "PADI Enriched Air Diver (Nitrox)" appears on 313 of its optional-extra
    # entries and matched `\benriched\s+air\b` below, which would have priced
    # a 100 EUR certification as the nitrox a diver breathes on the trip -- and
    # on the toggle this site counts. The two are separate lines on the same
    # operator's list, which is exactly the case this code was split out for.
    (r"\bnitrox\s+course\b|\benriched\s+air\s+course\b"
     r"|\benriched\s+air\s+diver\b|\bnitrox\s+diver\b", FeeCode.NITROX_COURSE),
    # A tank size is the tank, not the gas, and it has to be read before the
    # word beside it. The operator fills 12-litre tanks free and charges for
    # 15-litre ones, so "Nitrox 15 liter tanks", "15 LITER tank nitrox (only 12
    # liter is free of chanrge)" and "15 liters Nitrox" are one charge with
    # three spellings -- and `\bnitrox\b` claimed all 17 of them, pricing an
    # upgrade as the gas on the toggle this site counts, on 15 itineraries and
    # 48 sailings, against a vessel panel stating nitrox included.
    #
    # liveaboard.com settles that they are one charge rather than two sellers
    # disagreeing: it files the same thing under *gear* ("15L tanks 35-65/week")
    # on 79 of 79 vessels and never touches its own nitrox line.
    #
    # Narrow on purpose. The 14 remaining clashes are one vessel's bare
    # "Nitrox" at 50 with no size in the title, and there the stated amount
    # wins: turning a stated cost into free is the error this must never make.
    (r"\b1[0-9]\s*(?:l\b|lt\b|ltrs?\b|liters?\b|litres?\b)", FeeCode.TANK_15L),
    # Supervision, at 9 a dive, for divers of a stated certification level.
    # Priced and real, and owed by some divers and not others -- so it is a
    # charge like a guide's rather than one every berth carries, which is what
    # `_tier_for` and the mandatory loop in `padi_com` do with it.
    (r"\bsupervision\b", FeeCode.GUIDED_DIVING),
    (r"\bdiving\s+courses?\b|\bscuba\s+courses?\b|\bcourses?\b", FeeCode.COURSE),
    (r"\bnitrox\b|\benriched\s+air\b", FeeCode.NITROX),
    (r"\b(?:private\s+)?dive\s+guide\b|\bprivate\s+guide\b", FeeCode.PRIVATE_GUIDE),
    # Ahead of the general gear line so "Snorkel Gear" keeps its own code.
    # They are separate lines on the operator's own list, and one entry per
    # code means folding them together would drop whichever came second.
    (r"\bsnorkell?(?:ing)?\s+(?:gear|equipment|set)\b", FeeCode.SNORKEL_GEAR),
    # "Full scuba set" is PADI's name for the bundle, on 417 entries and with no
    # second wording. It is the same thing liveaboard.com heads "Full equipment
    # rent", and **the bundle is the only honest gear price** -- see
    # `scrape/gear.py`: a diver renting gear rents a set of it, and adding up
    # singles invents a basket the operator never sold. Every one of those
    # entries carries `fullSetDescription` naming what is in it, so the note can
    # say so in the seller's words.
    (r"\b(?:rental|hire)\s+(?:gear|equipment)\b|\b(?:gear|equipment)\s+(?:rental|hire)\b"
     r"|\bfull\s+scuba\s+set\b",
     FeeCode.GEAR_RENTAL),
    (r"\bnaturalist\s+guide\b|\bsnorkell?(?:ing)?\s+guide\b", FeeCode.NATURALIST_GUIDE),
    (r"\bextra\s+dives?\b|\badditional\s+dives?\b", FeeCode.EXTRA_DIVES),
    # Not only land: a full run turned up "Glass Bottom Boat Excursion"
    # alongside "Land Excursions", and calling the first one a land
    # excursion renames a charge into something it is not.
    (r"\bexcursions?\b", FeeCode.LAND_EXCURSION),
    # Narrow on purpose: the boat-features list that follows the disclosure
    # carries "Beer available" and "Wine Available" as amenities, not charges.
    (r"\balcoholic\s+(?:beverages?|drinks?)\b|\balcohol\b", FeeCode.ALCOHOL),
    # Both word orders. PADI writes "Tips for the crew" -- 23 of 23 sampled
    # itineraries carry it -- and `crew\s+tips?` only matched the other way
    # round, so the one charge every operator on that seller states was the one
    # charge nothing read.
    (r"\bgratuit\w*\b|\bcrew\s+tips?\b|\btips?\s+for\s+the\s+crew\b|\btipping\b",
     FeeCode.GRATUITIES),
    (r"\blaundry\b|\bpressing\s+services?\b", FeeCode.LAUNDRY),
    (r"\bvisas?\s*(?:fees?|on\s+arrival)?\b(?!\w)", FeeCode.VISA),
    (r"\b(?:dive|diving|travel)\s+insurance\b|\binsurance\b", FeeCode.DIVE_INSURANCE),
    (r"\b(?:airport|hotel)\s+transfers?\b|\btransfers?\b(?!\s*(?:or|and)\b)",
     FeeCode.AIRPORT_TRANSFER),
    (r"\bsingle\s+(?:cabin\s+)?supplement\b", FeeCode.SINGLE_SUPPLEMENT),
    (r"\bvat\b|\bsales\s+tax\b|\blocal\s+tax\b", FeeCode.TAX_VAT),
)

COMPILED_LABELS: tuple[tuple[re.Pattern[str], FeeCode], ...] = tuple(
    (re.compile(pattern, re.I), code) for pattern, code in LABEL_PATTERNS
)

# Codes this site treats as chosen rather than owed. Read by
# `tier_for_inclusion` only: an operator stating tips as covered has covered a
# cost its guests choose the size of, so the line belongs beside the other
# optional ones at zero rather than among the charges nobody can decline.
#
# It no longer reaches `_tier_for`, which now lets the seller's own block
# decide a billed charge's tier outright. See there.
CUSTOMARY_CODES = frozenset({FeeCode.GRATUITIES})
TOGGLED_CODES = frozenset(
    {
        FeeCode.NITROX,
        FeeCode.GEAR_RENTAL,
        FeeCode.DIVE_INSURANCE,
        FeeCode.AIRPORT_TRANSFER,
    }
)


def tier_for_inclusion(code: FeeCode) -> FeeTier:
    """Which column an included charge belongs in.

    A charge is worth stating as covered exactly because you would otherwise
    have to pay it, so an inclusion is **mandatory unless this site already
    treats that charge as something you choose** -- nitrox and gear follow a
    toggle, tips are customary. No new table: the two sets above are the ones
    `_tier_for` consults for a charge that is billed.

    Both sellers' inclusions come through here, which is the point of it living
    beside them: one seller's "Harbour fees" and the other's must not mean
    different things.
    """
    return _tier_for(code, required=code not in TOGGLED_CODES | CUSTOMARY_CODES)


NOISE = re.compile(r"^\s*(and|or|etc|extras?|none|n/?a)\s*$", re.I)


@dataclass(frozen=True, slots=True)
class ParsedFee:
    """One extra as the page states it, before it becomes a :class:`FeeItem`."""

    code: FeeCode
    label: str
    tier: FeeTier
    low: float | None
    high: float | None
    currency: str
    basis: FeeBasis
    included: bool = False
    """Stated as covered by the fare rather than charged on top.

    A third answer beside a price and a blank, and the one the `Included:` block
    gives: the charge exists, the operator bills nothing for it, and the line
    stays in the breakdown at zero so a bundled boat can be told apart from one
    that bills at the dock.
    """

    @property
    def is_range(self) -> bool:
        return self.high is not None and self.high != self.low

    @property
    def has_price(self) -> bool:
        return self.low is not None


NOT_A_LABEL = re.compile(r'[\[\]{}<>"|\\]|:\s*\S')
"""Characters that never appear in a fee label but do in leaked markup.

A live run mined ``] [&>*]:mx-3 -mx-3"> Nitrox available`` — a fragment of
Tailwind CSS — and charged a nitrox fee for it. A label carrying brackets,
braces or angle brackets is page furniture, not a price.
"""


COMBINED_PARTS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.I)
    for p in (
        r"\bparks?\b",
        r"\bports?\b|\bharbou?rs?\b",
        r"\bfuel\b",
        r"\benvironment(?:al)?\b|\beco\b",
        # The fifth part, and the reason it is here: PADI bills "Environmental
        # and Route Fees", which names two charges and matched exactly one of
        # the four above, so it declined and blocked its trip's bill. A route
        # supplement on its own still reaches `ROUTE_SUPPLEMENT` -- `route`
        # alone is one part, and `COMBINED_TAIL` does not match "Route
        # supplement" either way.
        r"\broutes?\b",
    )
)
COMBINED_TAIL = re.compile(r"\b(?:fees?|charges?|taxes?|dues)\b", re.I)


def _combined_fee(label: str) -> bool:
    """Is this one charge covering several of the mandatory fees at once?

    Operators bill "Park, Port and Fuel Fees (€200-450 / trip)" and "Park and
    Port Fees (€130 / trip)" as a single line. Matching those against the
    individual patterns either failed outright — the words are not adjacent, so
    one vessel lost its whole required block, €280 to €530 of mandatory cost —
    or filed the lot under whichever component happened to sit last, calling a
    combined charge "port dues".

    It stays one line carrying the whole amount. Splitting €200-450 across
    three codes would mean inventing three prices the operator never quoted,
    which is the one thing this parser must never do.
    """
    if not COMBINED_TAIL.search(label):
        return False
    return sum(1 for part in COMBINED_PARTS if part.search(label)) >= 2


ON_PURCHASES = re.compile(r"\bon\s*-?\s*board\s+purchases\b|\bonboard\s+purchases\b",
                          re.I)
"""A charge whose base is what the diver buys on board, not what they booked.

Three titles, 34 entries, and `price` and `extraValue` are null on every one:
*14% GST (on onboard purchases)*, *15% Local GST (on onboard purchases)*,
*14% Value Added Tax (VAT) (onboard purchases)*. There is no figure because
there is no base -- a diver who buys nothing at the bar pays nothing -- so this
is not a percentage this project could apply even if it were willing to.

PADI files them under its **mandatory** extras, which is what made them block
a bill: a charge in that list with no readable answer keeps the trip's book
`complete: false`, and `complete` is a verdict about the charges a diver cannot
decline. This one they can, by not buying anything.

Deliberately about the stated **base** rather than the word *tax*: *"10% (of
the trip cost) VAT Mandatory Fee"* is the same shape, is charged on the fare,
and stays mandatory and unpriced -- its trips keep an incomplete book until
this project can carry a percentage of a fare, which is the honest state.
"""


def billed_on_purchases(label: str) -> bool:
    """Whether this charge falls on the diver's own onboard spend."""
    return bool(ON_PURCHASES.search(label))


def classify_label(label: str, *, prose: bool = True) -> FeeCode | None:
    """Resolve one entry's label, or ``None`` when it is not a fee we know.

    Returns ``None`` freely. An unrecognised extra costs a line of data; a
    misrecognised one puts an invented charge on the page.

    ``prose=False`` drops the length and punctuation guards, and is for a label
    that arrived as its own field rather than as a line cut out of a page. The
    guards are not a view about what a fee is called; they are a defence
    against `parse_extras` reading a sentence off a vessel page and calling it
    a charge -- VAT out of "renovated", a fuel surcharge out of a paragraph
    about cruising speed. A JSON field named ``title`` in an entry already
    flagged ``isMandatory`` cannot fail that way, and applying the guards there
    silently dropped real, priced charges for being long: "Environmental tax,
    Park fees, Harbour fees and Fuel surcharge" is nine words and one of the
    largest mandatory lines in the book. Where there is no sentence to mistake
    for a label, a word count is not evidence of anything.
    """
    if prose and (
        len(label) > MAX_LABEL_CHARS
        or len(label.split()) > MAX_LABEL_WORDS
        or NOT_A_LABEL.search(label)
    ):
        return None
    # Ahead of the individual patterns, which would otherwise claim one
    # component of a combined charge and drop the rest of its meaning.
    if _combined_fee(label):
        return FeeCode.COMBINED_FEES
    for pattern, code in COMPILED_LABELS:
        if pattern.search(label):
            return code
    return None


def _tier_for(code: FeeCode, required: bool) -> FeeTier:
    """Which tier a billed charge lands in. **The seller's block decides.**

    Gratuities used to be promoted to :attr:`FeeTier.CUSTOMARY` from here
    whatever block they were listed in, on the reasoning that tips are paid by
    nearly everyone. That reasoning is sound about divers and wrong about this
    dataset: a mandatory $50 tip and a tip you choose the size of are different
    charges, and the only party that can say which one an operator bills is the
    operator. Both sellers do say. All 55 vessels that state gratuities file
    them under **Optional**, and PADI puts "Tips for the crew" in
    `optionalOnBoard` on every trip that names it -- so the promotion was this
    project overruling both sellers at once, on 255 fee lines, and adding a
    mean of EUR 74 to 278 sailings' counted totals on its own authority.

    A tip an operator does list as required still counts, through `required`
    and without a special case: a charge in the Required block is mandatory
    whatever it is for.

    Nothing emits `FeeTier.CUSTOMARY` now. The tier stays in the vocabulary and
    in `DEFAULT_ON_TIERS` -- it is mirrored by `lineCounts` in `templates/app.js`
    and the two must move together -- but no parser writes it, and a seller that
    files a tip as owed writes `MANDATORY` instead.
    """
    if required:
        return FeeTier.MANDATORY
    if code in TOGGLED_CODES:
        return FeeTier.CONDITIONAL
    return FeeTier.OPTIONAL


def _number(raw: str | None) -> float | None:
    if raw is None:
        return None
    cleaned = raw.replace(",", "").rstrip(".")
    try:
        return float(cleaned)
    except ValueError:
        return None


CONTINUATION = re.compile(r"^\s*[(\-–—/]")
"""A line that continues the one above rather than starting a new entry."""


def normalise_disclosure(text: str) -> str:
    """Turn a browser's ``innerText`` into comma-separated entries.

    Rendered text puts each extra on its own line and often its amount on the
    next one again::

        Environment Tax
        (€45)
        Fuel Surcharge
        (€60-70 / trip)

    Naively swapping every newline for a comma separates each label from its
    own price and silently reports seven priced extras as unpriced — worse than
    the run-together text it was meant to fix. So a line opening with a bracket
    or a dash is rejoined to the line above, and only the remaining breaks
    become separators.

    Idempotent on text that is already comma-separated, so both the raw-HTML
    and rendered-text paths can call it.
    """
    joined: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if joined and CONTINUATION.match(line):
            joined[-1] = f"{joined[-1]} {line}"
        else:
            joined.append(line)
    return ", ".join(joined)


def parse_extras(text: str, default_currency: str = "EUR") -> list[ParsedFee]:
    """Extract every stated extra from a vessel page's disclosure text.

    Charges first and inclusions last, whatever order the page prints them in --
    the `Included:` block comes above the other two there, and read in that
    order it would have won every collision by arriving first. **A stated amount
    is the stronger claim**, which is the rule PADI's side of this already
    keeps. It decides four vessels today, and all four are one code covering two
    services: Topaz includes the airport transfer and charges €25 for the hotel
    one, Oceanix includes a naturalist guide and charges €300 for a snorkelling
    one. Printing "included" for those would tell a reader a charge the operator
    published is free.

    The other half of the same rule: **an inclusion beats a charge that names no
    figure.** Dune Longara lists a transfer with no price and states the
    transfer as included, and "listed with no price" there is this parser
    failing to notice the answer was on the page.
    """
    found: dict[FeeCode, ParsedFee] = {}

    if "\n" in text:
        text = normalise_disclosure(text)

    blocks = sorted(BLOCK.findall(text), key=lambda b: b[0].lower() == "included")
    for heading, body in blocks:
        required = heading.lower() == "required"
        inclusion = heading.lower() == "included"
        for match in ENTRY.finditer(body):
            label = " ".join(match.group("label").split())

            # The operator ends the list with a full stop, and everything after
            # it is the rest of the page. Stored disclosures from six vessels
            # show the real list running 55 to 273 characters inside a block
            # that runs to 1500, closing "Snorkel Gear." or "Land Excursions."
            # before the booking copy and the boat's feature list begin.
            #
            # Read before the entry is judged, not after, because the last
            # genuine extra is often one we do not model: stopping only on
            # recognised entries would let the whole page through behind an
            # unrecognised "Snorkel Gear."
            #
            # This is what the label bounds below were standing in for. They
            # stay as a second line of defence -- a page that never closes its
            # list still must not mine prose -- but this one matches how the
            # source is actually written.
            last = match.group("end") == "."

            if not label or NOISE.match(label):
                if last:
                    break
                continue

            # The block regex runs to the next heading or the end of the page,
            # so on a flattened page it keeps going long after the extras stop.
            # A segment too long to be a label is where the list ended: stop
            # rather than skip, or the vessel's spec sheet and the site's
            # destination menu get mined for fees that were never charged.
            #
            # A priced segment is spared that test. Truncating on length alone
            # would silently drop every remaining extra the moment one genuine
            # entry ran long, and losing real mandatory fees is the same lie as
            # inventing them, told the other way round.
            if len(label) > MAX_LABEL_CHARS and match.group("low") is None:
                break

            code = classify_label(label)
            if code is None or not _is_new(found.get(code), inclusion):
                if last:
                    break
                continue

            low = _number(match.group("low"))
            high = _number(match.group("high"))
            symbol = match.group("currency")
            basis = BASES.get((match.group("basis") or "").lower(), FeeBasis.PER_TRIP)

            # An inclusion states no figure and must not be read as one: the
            # `Included:` block is a list of names, so a number inside it
            # belongs to a qualifier -- "Full-Board Meal Plan (All meals)".
            found[code] = ParsedFee(
                code=code,
                label=label,
                tier=tier_for_inclusion(code) if inclusion else _tier_for(code, required),
                low=None if inclusion else low,
                high=None if inclusion else (high if high is not None else low),
                currency=CURRENCIES.get(symbol or "", default_currency),
                basis=basis,
                included=inclusion,
            )

            if last:
                break
    # Insertion-ordered, so an inclusion that replaced an unpriced charge keeps
    # that charge's place in the breakdown rather than moving to the end.
    return list(found.values())


def _is_new(existing: ParsedFee | None, inclusion: bool) -> bool:
    """Whether this entry should be kept over one already read for its code.

    One line per code, first past the post -- except that an inclusion may
    displace a charge that names no figure. See :func:`parse_extras`.
    """
    if existing is None:
        return True
    return (
        inclusion
        and not existing.included
        and not existing.has_price
        and existing.tier is not FeeTier.MANDATORY
    )


EXCERPT_CHARS = 1500
"""How much of each Required/Optional block is kept as evidence.

The reference disclosure is under 300 characters. This is generous enough to
hold a long one whole, and small enough that keeping one per vessel does not
turn the fee book into a page dump.
"""


def extras_excerpt(text: str, limit: int = EXCERPT_CHARS) -> dict[str, str]:
    """Return the disclosure text :func:`parse_extras` reads, for the record.

    All three blocks, keyed by the bare heading word -- ``included``,
    ``required``, ``optional``. `drift` rebuilds the text from these keys, which
    is why `BLOCK` accepts a heading with the word "Extras" missing.

    The fee book is the one input this project cannot rebuild without a
    browser, and it stored only the parsed result. So when the parser was found
    to be inventing charges, there was no way to check the fix against what the
    page had actually said — the published fabrications had to be re-derived
    from their own ``note`` fields, and confirming the fix meant driving a
    browser at the live site again.

    Keeping the text the parse was made from turns the next parser fix into a
    replay instead of another live run, and makes "did the operator change
    this, or did we?" answerable from the repository alone.
    """
    blocks: dict[str, str] = {}
    for heading, body in BLOCK.findall(normalise_disclosure(text)):
        excerpt = " ".join(body.split())[:limit]
        if excerpt:
            blocks[heading.lower()] = excerpt
    return blocks


def to_fee_dicts(fees: list[ParsedFee], provenance: dict) -> list[dict]:
    """Render parsed extras into the dataset's fee shape."""
    out = []
    for fee in fees:
        entry: dict = {
            "code": fee.code.value,
            "tier": fee.tier.value,
            "basis": fee.basis.value,
            "included": fee.included,
            "provenance": provenance,
        }
        if fee.included:
            # Drawn at zero rather than counted, and never "listed with no
            # price": the operator did state the price, and it is nothing.
            entry["amount"] = None
            entry["note"] = f"{fee.label}: stated as included"
        elif fee.has_price:
            entry["amount"] = {"amount": fee.low, "currency": fee.currency}
            if fee.is_range:
                entry["amount_max"] = {"amount": fee.high, "currency": fee.currency}
                entry["note"] = f'Operator quotes "{fee.label}" as a range'
            elif fee.label.lower() != FEE_LABELS.get(fee.code, "").lower():
                # Keep the operator's own wording only when it says something
                # our label does not; echoing it back is noise.
                entry["note"] = fee.label
        else:
            # Listed with no figure. Never treated as free.
            entry["amount"] = None
            entry["note"] = f"{fee.label}: listed with no price"
        out.append(entry)
    return out


NOT_FROM_THE_DISCLOSURE = frozenset({FeeCode.GEAR_RENTAL})
"""Codes the extras text names but does not price.

The disclosure lists "Rental Gear" and stops; the figures are in the
``#modal-gear`` dialog, and the fee scrape overwrites the unpriced line with
the priced one. So re-reading the text alone can never reproduce what is
stored, and comparing them reported all seventy-nine vessels as drifted on the
first run of :func:`drift`. Excluded rather than special-cased, because the
question this check asks is only about what the text parser owns.
"""


def _comparable(fee: dict) -> tuple:
    """The parts of a fee line a parser change would move.

    Provenance and note are excluded: the first records when a thing was
    fetched and the second is wording, and neither says what a diver pays.
    """
    amount = fee.get("amount") or {}
    high = fee.get("amount_max") or {}
    return (
        fee.get("code"), fee.get("tier"), fee.get("basis"), bool(fee.get("included")),
        amount.get("amount"), amount.get("currency"), high.get("amount"),
    )


def drift(book: dict) -> dict[str, tuple[list[str], list[str]]]:
    """Vessels whose stored fees are not what today's parser reads.

    ``promote`` prefers the committed fee book over the daily run's own parse,
    and that preference is right -- a browser sees extras the raw HTML never
    will. The consequence is that a fee-parser fix reaches nothing until the
    weekly browser run happens to go again, so a refresh can run entirely green
    and change nothing while the page keeps charges the fix was written to
    remove. That happened, and every step reported success.

    This closes the gap without inverting the preference, because the book also
    stores the disclosure text each parse was made from. Re-reading that text
    with the current parser and comparing says whether the book is what this
    code would produce -- offline, in milliseconds, with no fetch.

    Returns ``{slug: (gained, lost)}`` describing what today's parser would add
    and drop. An empty result means the book is current.

    What it cannot see is a block the stored text never held. Every excerpt in
    the committed book predates `BLOCK` reading the `Included:` paragraph, so
    re-parsing them produces no inclusion and reports no drift -- and the book
    is a parser behind all the same. Same rule as everywhere else here: a run
    that could not look at something knows nothing about it. The excerpt keeps
    all three blocks now, so the next book can answer.
    """
    out: dict[str, tuple[list[str], list[str]]] = {}
    for slug, entry in (book.get("vessels") or {}).items():
        disclosure = entry.get("disclosure")
        if not disclosure:
            # Collected before the text was kept. Not drift, just unanswerable.
            continue
        text = "\n".join(
            f"{heading.title()} Extras: {body}" for heading, body in disclosure.items()
        )
        fresh = to_fee_dicts(parse_extras(text), {})
        skip = {c.value for c in NOT_FROM_THE_DISCLOSURE}
        was = {_comparable(f) for f in entry.get("fees") or [] if f.get("code") not in skip}
        now = {_comparable(f) for f in fresh if f.get("code") not in skip}
        if was == now:
            continue
        gained = sorted(f"{c[0]}={c[4]}" for c in now - was)
        lost = sorted(f"{c[0]}={c[4]}" for c in was - now)
        out[slug] = (gained, lost)
    return out
