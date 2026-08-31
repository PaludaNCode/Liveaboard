"""Turn a candidate scrape into a dataset the site can render.

Promotion is deliberately a separate step from scraping. A source site changing
its markup should degrade into "yesterday's prices plus a warning", never into
a published page full of blanks, and that only holds if something has to
actively decide the new data is good enough.

What the scrape gives us is boats, dates and prices. What it does not give us
is fees — and that gap is the whole reason this module is careful. An itinerary
with no fee lines is not an itinerary with no fees; it is one we have not
looked at yet. Those two must never render the same way, or a site built to
expose hidden costs would be hiding them itself.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date
from typing import Any, Mapping, Sequence

from .classify import normalise
from .taxonomy import DIVER_LEVEL_LABELS, DIVER_LEVEL_ORDER, DiverLevel, FeeBasis, FeeCode, FeeTier, SourceKind

UNKNOWN_OPERATOR = {
    "id": "unknown-operator",
    "name": "Operator not captured",
}
"""For a departure whose source published no organizer.

This used to be every departure in the dataset -- 317 of 317 -- on the belief
that an agency listing names the vessel and not who runs it. It names both:
every ``Event`` node carries ``organizer.name``. The field was parsed and
dropped, so the fallback became the only answer.

It stays, because a source that says nothing must not be given an invented
company. On current evidence it is never reached.
"""

OPERATOR_ALIASES: dict[str, str] = {
    # A missing space, in the operator's own listing. Both halves are real
    # fleets under one company, and "Aggressor Fleet& Dancer Fleet" is 50
    # departures filed under a typo.
    "aggressor fleet& dancer fleet": "Aggressor Fleet & Dancer Fleet",
}
"""Operator names that are one company under more than one spelling.

Folded the way :data:`PORT_ALIASES` folds harbours: deliberately, in a table,
with a comment saying why. Not by fuzzy matching -- "Red Sea Explorers" and
"Red Sea Relax" are two companies four characters apart, and a similarity
threshold that merges the Aggressor typo also merges those.

Names are otherwise kept **verbatim**. "XPLORER AQUARIUS Safari" is shouted and
"MV Legends II" is a vessel name doing duty as a company, but both are what the
operator publishes, and tidying someone's capitalisation is a short step from
deciding what they are called.

Keys are matched lowercased with whitespace already collapsed by the scraper.
"""


MIN_NIGHTS = 1
MAX_NIGHTS = 30
"""Sanity bounds. A departure outside these is a parsing error, not a cruise."""


def slugify(value: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", value.lower())).strip("-")


def _collapsed(value: str | None) -> str | None:
    """Runs of whitespace folded to one space.

    Not a spelling change, which is why it is safe where re-casing is not: the
    first source's scraper already collapses whitespace on everything it reads,
    so this only brings the second source to the same footing. PADI ships
    "Explorer Ventures -  Grand Sea Explorer" with two spaces in the middle.
    """
    return " ".join(value.split()) if value else None


def operator_record(name: str) -> dict[str, str]:
    """One operator, as the dataset stores it.

    The id is a slug of the folded name, so two spellings of one company reach
    one record and one page grouping.
    """
    folded = OPERATOR_ALIASES.get(name.lower(), name)
    return {"id": slugify(folded) or UNKNOWN_OPERATOR["id"], "name": folded}


PROMOTION = re.compile(r"^\s*(\d{1,2}\s*%\s*off)\s*:\s*", re.I)
"""A discount banner the operator glues to the front of a trip title.

Every scraped title carrying one has a twin without it — "20% Off: North &
Tiran (Hurghada - Hurghada)" and "North & Tiran (Hurghada - Hurghada)" are the
same week on the same boat, differing only in which departures were on offer.
Grouping on the raw title split them into two cards, so a vessel appeared to
sell routes it does not and the discounted departures sat under a separate
heading from the rest.

The discount is real price information, so it moves to the departures it
applies to rather than being dropped. The route name stays a route name:
carrying an operator's marketing into our own headline is the opposite of what
this site is for.
"""

PORTS = re.compile(r"\(([^()]{2,60}?)\s+[-–]\s+([^()]{2,60}?)\)\s*$")
"""The departure and return ports, which the title states and the data does not.

liveaboard.com's Event location is the country — every itinerary promoted as
"Egypt → Egypt", which tells a visitor nothing. The title ends with the real
pair: "(Port Ghalib - Safaga/Soma Bay)". That distinction decides which
airport someone flies into and whether they need two of them, so it is worth
reading off the only place the source puts it.
"""

PORTS_TIGHT = re.compile(r"\(([^()]{2,60}?)\s*[-–]\s*([^()]{2,60}?)\)\s*$")
"""The same pair, written without the spaces around the dash.

Thirteen titles space the hyphen on one side or on neither -- "(Hurghada-
Hurghada)", "(Port Ghalib -Port Ghalib)" -- because ``TIDY`` evens out the
spacing around ``&`` and around an en dash but not around a hyphen. They read
as a port pair to anybody and as nothing at all to :data:`PORTS`, so 32 real
sailings printed "Unknown" in the column whose whole job is which airport to
fly into, and kept the bracket in their trip name where their 389 neighbours
had it cut.

Tried only after :data:`PORTS` fails, never in place of it. A spaced dash,
where a title has one, is the separator: "(Sharm el-Sheikh - Hurghada)" is two
harbours under that rule and "Sharm el" plus "Sheikh - Hurghada" under this
one. No name in the fleet is written that way today -- of 872 names across
every source in the repository, none has two dashes inside the trailing
bracket -- which is why the order is settled now rather than the first time
one is.
"""


def _ports_match(name: str) -> "re.Match[str] | None":
    """The port pair at the end of a title, a spaced dash preferred."""
    return PORTS.search(name) or PORTS_TIGHT.search(name)


PORT_ALIASES: dict[str, str] = {
    # Port Ghalib's marina is written three ways across the fleet. "Marsa" is
    # Arabic for the harbour itself, and Ras Galep is the headland it sits on.
    "marsa ghalib": "Port Ghalib",
    "ras galep | port ghalib": "Port Ghalib",
    # A hotel pickup point, not a port.
    "hurghada, marriott": "Hurghada",
    # Soma Bay is a resort bay ten kilometres up the coast from Safaga, and the
    # operator names both because it uses whichever berth it is given.
    "safaga/soma bay": "Safaga",
    # One hull abbreviates both its harbours to airport codes. What folds them
    # is not the letters but the same source saying the harbour outright:
    # every Seawolf Steel trip titled "(HRG - PRG)" carries PADI's
    # `harbourDepartureTitle` "Hurghada" and `harbourArrivalTitle` "Port
    # Ghalib". (HRG is indeed Hurghada's IATA code. PRG is Prague's, and no
    # Egyptian harbour's, which is the reason to read the field instead.)
    "hrg": "Hurghada",
    "prg": "Port Ghalib",
    # A letter short of the 130 itineraries that spell it out, on a Fury Shoals
    # week whose stated harbour is "Port Ghalib".
    "port galib": "Port Ghalib",
    # The stated harbour is more granular than the title: it names berths where
    # a title names towns. Three of PADI's eight harbour names are a marina
    # inside a town this page already lists, and left unfolded each would open
    # a fourth and fifth harbour chip for a port the filter already has.
    # "hurghada, marriott" above is the same berth reached from the other
    # source's own spelling of it.
    "hurghada marina": "Hurghada",
    "hurghada - marriott marina": "Hurghada",
    "new marina sharm el sheikh (el wataneya)": "Sharm El Sheikh",
    # One title prints the same harbour two wrong ways inside one bracket:
    # "(Sharm El sheikh - Sharm El Sheik)". The stated harbour is "Sharm El
    # Sheikh" both ends, so the case row settles the capital with the spelling.
    "sharm el sheik": "Sharm El Sheikh",
    "sharm el sheikh": "Sharm El Sheikh",
}
"""Ports that are one place under several spellings.

Left unmerged they made ten filter chips out of six real harbours, and split
the departures leaving from one marina across three of them -- which is worse
than cosmetic on a filter whose whole job is "which airport do I fly into".
The second seller put the count back up to eleven: PADI's titles carry the
abbreviations and the misspellings this table's first half was written for,
and 19 departures sat under four chips that name no place.

Deliberately narrow. Marsa Alam is sixty kilometres south of Port Ghalib and
stays its own port, however similar the names look. Each row here folds a
spelling onto one the *same fleet* already uses of the *same harbour* --
never a guess at which harbour an unfamiliar name might mean.

Folds the column, not the name. Ids are built from the trip name and the port
pair is inside them for a PADI-minted trip, so rewriting a name to settle a
chip moves the id under every departure hanging off it -- and Seawolf Steel
carries two trips differing only by their ports.

The bottom four rows are checked against the second source rather than argued
for: PADI publishes the harbour as a field next to the title it abbreviates,
and on all 212 itineraries that carry both, the stated harbour and the parsed
one are the same place -- no contradictions, only the spellings folded here.
That field is collected into ``data/padi.json`` and read by nothing yet; when
it is, it should beat a port parsed out of the same source's title, and this
half of the table becomes a net rather than the answer. See
``docs/sources/padi.com.md``.
"""


def _port(name: str | None) -> str:
    """Fold an operator's spelling of a harbour onto one name."""
    if not name:
        return "Unknown"
    cleaned = " ".join(name.split())
    return PORT_ALIASES.get(cleaned.lower(), cleaned)


TIDY = (
    # Tabs and runs of spaces. Four titles carry a tab, which renders as a
    # ragged gap mid-name and breaks the column's alignment.
    (re.compile(r"\s+"), " "),
    # A space wedged before the closing bracket: "(Safaga - Safaga )".
    (re.compile(r"\s+\)"), ")"),
    (re.compile(r"\(\s+"), "("),
    # One dash. Three titles use an en dash where 314 use a hyphen, so the
    # same route reads as two different names down the column.
    (re.compile(r"\s*[–—]\s*"), " - "),
    # Spacing around the separators operators are inconsistent about.
    (re.compile(r"\s*&\s*"), " & "),
    (re.compile(r"\s*,\s*"), ", "),
    (re.compile(r"\s*:\s*"), ": "),
)
"""Whitespace and punctuation fixes, applied before anything reads the title.

Purely presentational: none of these change a word. They exist because the
titles arrive from dozens of operators typing freely, and a column of trip
names only reads as a column when the separators are the same shape.
"""


def _tidy(name: str) -> str:
    """Even out an operator's spacing without touching their wording."""
    for pattern, replacement in TIDY:
        name = pattern.sub(replacement, name)
    return name.strip(" -,:")


def _split_title(name: str) -> tuple[str, str | None, tuple[str, str] | None]:
    """Split a scraped trip title into route, promotion and ports."""
    name = _tidy(name)
    promotion = None
    match = PROMOTION.match(name)
    if match:
        promotion = " ".join(match.group(1).split())
        name = name[match.end():].strip()

    ports = None
    match = _ports_match(name)
    if match:
        first, second = (" ".join(p.split()) for p in match.groups())
        # A parenthetical is only a port pair when it reads like one. "(Brothers
        # - Daedalus)" is a route, and filing Daedalus as a harbour would put a
        # reef on the page as somewhere to fly into.
        #
        # Some names are both. Safaga and Dahab are harbours you sail from and
        # stretches of reef you dive, so a plain "does this contain a dive
        # site" test rejected "(Port Ghalib - Safaga/Soma Bay)" as a route and
        # threw away a real port pair. Names that are ports in their own right
        # are set aside before the question is asked.
        if not _sites_from_name(_without_ports(f"{first} {second}")):
            ports = (first, second)
    return name.strip(), promotion, ports


def itinerary_key(slug: str, name: str) -> str:
    """One itinerary, identified as ``promote`` groups them: vessel + trip name.

    Departures are grouped by ``(slug, name)`` below, so anything read per
    *trip* rather than per sailing -- the itinerary book that
    ``tools/fetch_itineraries.py`` writes -- has to key on the same pair or it
    finds nothing.

    Put through :func:`_split_title` rather than compared raw, because the two
    sides do not spell a trip the same way. The book is built from archived
    ``Event`` names, which carry the operator's discount banner -- "20% Off:
    Ultimate Red Sea (Port Ghalib - Hurghada)" -- and the promoted name has
    already had it removed, because a week on sale is the same week. Keyed on
    the raw string, 71 of 314 itineraries matched nothing and the fetcher spent
    97 requests re-reading trips under their banner spellings.

    Exported so the fetcher imports it rather than reimplementing it. Two
    copies of this rule drifting apart is a book that silently matches nothing,
    and every field it fills has a fallback, so nothing would fail loudly.
    """
    trip, _, _ = _split_title(name)
    return f"{slug}::{trip}"


BDE = re.compile(
    r"""^\s*
        brother(?:s)?(?:\s+islands?)?   # Brother / Brothers / Brother Islands
        \s*(?:,|&|and|-|–|—|\+|\s)\s*
        daedalus
        \s*(?:,|&|and|-|–|—|\+|\s)\s*
        elphinstone
        \s*$""",
    re.I | re.X,
)
"""The one route the fleet writes seven different ways.

    Brother - Daedalus - Elphinstone
    Brother Islands - Daedalus - Elphinstone
    Brother Islands, Daedalus & Elphinstone
    Brothers - Daedalus - Elphinstone
    Brothers, Daedalus & Elphinstone
    Brothers, Daedalus and Elphinstone
    Brothers, Daedalus, Elphinstone

Seven spellings of one week, sitting next to each other in the widest column
on the page, and a visitor comparing them has to notice that they are the same
trip before they can compare anything else.

Deliberately only this route. Twelve other groups differ the same way -- North
& Brothers against North - Brothers, and so on -- and are left exactly as their
operators wrote them. A rule that rewrote every title would be a house style
imposed on somebody else's words, and it would eventually merge two trips that
only look alike. This matches one route, in one order, and rewrites nothing
else: the pattern is anchored at both ends, so "Brothers, Daedalus &
Elphinstone + Safaga" does not match and is left alone.

Never touches ``Itinerary.name``. The id is built from the name and
``data/itineraries.json`` keys on it, so the operator's own wording stays the
identity and this is presentation only.
"""

BDE_TITLE = "Brothers, Daedalus & Elphinstone"
"""House style: commas, then an ampersand before the last."""


TITLE_FIXES = (
    # Zero-width spaces, pasted in from wherever the title was written. Two
    # titles carry them -- "Red Sea Charm[]:" and "Sataya[][] (Fury Shoals)" --
    # invisible on the page and not invisible to anything else: they defeat a
    # search for the words either side and sort as though they were there.
    # Removed rather than spaced, because they are not spaces.
    (re.compile(r"[​‌‍﻿]"), ""),
    # One apostrophe. The fleet writes St John's with a typewriter quote, a
    # curly quote and an acute accent -- three characters for one reef, so the
    # same saint sorts in three places and matches in one.
    (re.compile(r"[‘’´]"), "'"),
    # Daedalus, twice misspelled: "Daedulus" and "Deadalus". Listed rather
    # than matched loosely, the same way PORT_ALIASES folds harbours -- a
    # near-miss rule that catches these also catches a reef that only looks
    # like another. Both trips already carry `daedalus` in their dive sites,
    # read from the operator's own description, so this corrects the spelling
    # of something the dataset has independently confirmed.
    (re.compile(r"\b(?:daedulus|deadalus)\b", re.I), "Daedalus"),
    # Zabargad, once, with two letters swapped: "Zarbagad". Listed for the
    # same reason, and confirmed the same way -- that trip carries `zabargad`
    # in its dive sites, read from the operator's own description, and the
    # fleet writes the reef 26 other times without ever writing it this way.
    (re.compile(r"\bzarbagad\b", re.I), "Zabargad"),
    # Gubal, once written "Gobal". The only correction here whose evidence is
    # the fleet's rather than the trip's own: that title names Thistlegorm, Abu
    # Nuhas and "Small Gobal", and its parsed sites name neither spelling, so
    # nothing confirms it from inside the trip. What decides it is that the
    # fleet writes "gubal" 46 times in parsed dive sites and "gobal" not once,
    # against a single title -- one operator's typing against everybody else's.
    (re.compile(r"\bgobal\b", re.I), "Gubal"),
    # A separator with a space on one side only: "St. John's- Elphinstone".
    # Both sides or neither. A hyphen with no space at all is spacing the
    # operator chose -- "Thistlegorm-Abu Nuhas" -- and is left exactly as it
    # is; a hyphen with one space is a space they dropped.
    (re.compile(r"(?<=\S)-(?=\s)"), " -"),
    (re.compile(r"(?<=\s)-(?=\S)"), "- "),
)
"""Errors in a title, as opposed to a style we happen not to share.

The distinction is the whole reason this is a short list. Separators and word
order are the operators' own and are left alone -- see the note on ``BDE``.
These are things nobody intended: an invisible control character, three
characters doing one apostrophe's job, reefs with their letters swapped, and a
dash with a space on one side.

A misspelling is corrected where the trip's own dive sites, read from the
operator's description, already name the reef correctly -- the dataset
confirming the reef independently of its title is what separates a correction
from a guess. ``Gobal`` is the one exception and is marked as such: its trip
names no Gubal at all, so the evidence is the fleet's instead -- 46 parsed
"gubal" against a single "gobal" in one title. A weaker warrant, taken
deliberately and only where the count is that lopsided.
The three reefs the fleet spells several ways are folded separately, in
:data:`REEF_ALIASES`, because they are differences rather than mistakes.

Applied to the display title only. ``Itinerary.name`` keeps the operator's
text verbatim because the id is built from it and ``itinerary_key`` matches
the per-trip book on it, so correcting a name would silently re-key the trips
it corrected -- and what the operator published is, after all, the identity.
"""


def _fix_title_errors(title: str) -> str:
    """Correct what is wrong in a title, never what is merely different."""
    for pattern, replacement in TITLE_FIXES:
        title = pattern.sub(replacement, title)
    return title


REEF_ALIASES = (
    # St John's, seven ways: "St. John's" (18), "St. Johns" (15), "St Johns"
    # (8), "St John's" (5), "St. John" (3), "St.Johns" (3), "Saint John's" (3).
    # The saint sorts in seven places and matches in one. Folded onto the
    # plurality spelling, which is also the one carrying both the stop and the
    # apostrophe -- and TITLE_FIXES has already settled the apostrophe's
    # character, so this sees only "'".
    #
    # The space is optional because PADI writes the reef closed up: "St.Johns"
    # and "Rocky-Zabargad-St.Johns" arrived with the sailings PADI sells and
    # liveaboard.com does not, and a required space left them printing a
    # seventh spelling in the same column as the other six. `\b` before the
    # abbreviation is what keeps `\s*` safe -- it can only start a word.
    (re.compile(r"\b(?:St\.?|Saint)\s*John(?:'s|s)?\b", re.I), "St. John's"),
    # Brothers, five ways: "Brothers" (109), "Brother Islands" (5), "Brothers
    # Islands" (5), "Brother" (1), "Brothers Island" (1). The optional plural
    # comes before the optional "Islands" rather than beside it -- written as
    # an alternation, "s" wins on "Brothers Islands" and leaves the second word
    # stranded. Same shape as BDE's own brother clause, for the same reason.
    # Guarded against "Big Brother" and "Little Brother", which name the two
    # islands separately: neither appears in the fleet today, and folding one
    # of them into the pair would delete which island the trip dives.
    (
        re.compile(r"\b(?<!Big )(?<!Little )Brother(?:s)?(?:\s+Islands?)?\b", re.I),
        "Brothers",
    ),
    # Fury Shoals, two ways: "Fury Shoals" (17), "Fury Shoal" (12) -- the
    # narrowest split in the fleet and the one least likely to mean anything.
    (re.compile(r"\bFury\s+Shoals?\b", re.I), "Fury Shoals"),
    # Ras Mohammed, three ways: "Ras Mohamed" (15), "Ras Mohammed" (6) and
    # "Ras Muhammad" (2). All three are real transliterations of the Arabic,
    # so none is a misspelling and this belongs here rather than in
    # TITLE_FIXES.
    #
    # What settles it is that SITE_HINTS already calls the reef "ras mohammed"
    # and folds the rest onto it, so every filter chip says that -- and a title
    # column printing "Ras Mohamed" beside a chip reading "ras mohammed" is one
    # page disagreeing with itself. Not, as an earlier version of this comment
    # claimed, that the parsed sites "say ras mohammed 101 times out of 101":
    # they do, but only because the alias table put it there. That count is
    # this project's own choice reflected back, not evidence from the
    # operators. The title plurality (15 to 6) points the other way and is
    # overruled by the need for the two to agree.
    (re.compile(r"\bRas\s+M[ou]h?a?mm?[ae]d\b", re.I), "Ras Mohammed"),
)
"""One spelling for the three reefs the fleet writes several ways.

Not mistakes, unlike :data:`TITLE_FIXES` -- "Fury Shoal" and "Brother Islands"
are what those operators call those reefs. They are folded anyway, for the
reason ``BDE`` folds one route: a visitor comparing two rows has to work out
that the reefs are the same reef before they can compare the prices beside
them, and the titles sit in the widest column on the page.

Each replacement is **a spelling an operator actually used** -- the plurality
of them -- never one invented to be consistent. Chosen by count rather than by
taste, so the table can be re-derived from the data rather than argued about.

Three reefs, listed, and nothing generalised. The same restraint as the
``BDE`` note: separators and word order stay the operators' own, and no rule
here rewrites a title it was not written for. The ``Brother`` guard is the
shape of the risk -- a reef name that is a prefix of a different reef name.
Display title only, for the reason given on :data:`TITLE_FIXES`.
"""


def _fold_reef_names(title: str) -> str:
    """One spelling per reef, chosen from the spellings the fleet used."""
    for pattern, replacement in REEF_ALIASES:
        title = pattern.sub(replacement, title)
    return title


LIST_SEPARATOR = re.compile(r"\s*(?:,|&|\+|\||-|\band\b)\s*", re.I)
"""What operators put between the stops of a route: , & + | - and."""

LIST_WORDS = ("north", "south", "deep south", "wrecks", "wreck", "reefs",
              "reef", "north reefs", "north reef", "north wrecks")
"""Words that stand in for a place in a route list without being a reef.

"North & Brothers" is a list of two stops, and the first is a direction. They
are listed here rather than added to ``SITE_HINTS`` because they are not dive
sites and must not become filter chips -- this table decides only whether a
title is a list, never what the trip dives.
"""


def _is_place_list(title: str) -> bool:
    """Whether a title is nothing but stops and the punctuation between them.

    The boundary for house separators, and the reason they are safe to apply.
    "Daedalus - Rocky - Zabargad" is a list whose dashes are separators;
    "Dancing with Dolphins - Dolphin Liveaboard Safari" is a sentence whose
    dash is not, and "Best of Dahab and Tiran" is English rather than two
    stops joined by "and". Rewriting either would be editing prose.

    Every part has to be something the dataset already recognises, so the test
    is the site vocabulary the rest of promote reads titles with rather than a
    second list that could drift from it.
    """
    parts = [p.strip() for p in LIST_SEPARATOR.split(title) if p.strip()]
    if len(parts) < 2:
        return False
    known = {normalise(h) for h in SITE_HINTS}
    known |= {normalise(a) for a in SITE_ALIASES}
    known |= {normalise(w) for w in LIST_WORDS}
    return all(normalise(p) in known for p in parts)


def _house_separators(title: str) -> str:
    """Commas, then an ampersand before the last -- on route lists only.

    Order is left exactly as the operator wrote it. Two titles naming the same
    reefs in a different sequence stay two titles: nothing here can verify the
    order means something, and nothing here may assume it means nothing.

    So this folds "North - Brothers", "North and Brothers" and "North &
    Brothers" onto one, and leaves "Daedalus & St. John's" and "St. John's &
    Daedalus" as the two different sentences their operators wrote.
    """
    if not _is_place_list(title):
        return title
    parts = [p.strip() for p in LIST_SEPARATOR.split(title) if p.strip()]
    return f"{', '.join(parts[:-1])} & {parts[-1]}"


def _settle_title_case(itineraries: list[dict[str, Any]]) -> None:
    """One spelling per title, where the only difference is capitalisation.

    Emperor Divers sells "Simply The Best" and "Simply the Best" -- two real
    trips with different ports, written two ways, and the page prints both a
    row apart as though the difference meant something.

    The chosen spelling is always **one the operator actually used**: the most
    common of them, alphabetical on a tie so the dataset is reproducible.
    Nothing is title-cased and no word is changed, which is what keeps this
    safe on names full of things a casing rule would ruin -- "MY Odyssey",
    "St. John's", "SS Turkia".

    Case only. A title differing by a word is a different title and is left
    alone, the same way the route rewriting is confined to one route.
    """
    spellings: dict[str, Counter[str]] = defaultdict(Counter)
    for itinerary in itineraries:
        spellings[itinerary["title"].casefold()][itinerary["title"]] += 1
    settled = {
        folded: min(counts.most_common(), key=lambda kv: (-kv[1], kv[0]))[0]
        for folded, counts in spellings.items()
    }
    for itinerary in itineraries:
        itinerary["title"] = settled[itinerary["title"].casefold()]


def _display_title(name: str) -> str:
    """The trip name with its port pair removed, for the column that shows it.

    Titles end with the ports -- "North & Tiran (Hurghada - Hurghada)" -- and
    From and To are columns of their own, so printing the brackets is the same
    fact twice in the widest column on the page.

    Cut here rather than in the browser. The page used to strip the suffix by
    checking the bracket text against ``port_from``, which held while the two
    were the same string and would have broken the moment ``PORT_ALIASES``
    started folding "Ras Galep | Port Ghalib" down to "Port Ghalib": the
    comparison fails, and seven titles quietly get their ports back. Deciding
    it beside the alias table, where the raw pair is still in hand, is the
    difference between a rule and a coincidence.

    Only a real port pair goes. "(Brothers - Daedalus)" is a route, and cutting
    it would delete what the trip actually is.
    """
    stripped, _, ports = _split_title(name)
    match = _ports_match(stripped) if ports is not None else None
    if match is not None:
        stripped = stripped[: match.start()].strip(" -,:") or stripped
    stripped = _fold_reef_names(_fix_title_errors(stripped))
    if BDE.match(stripped):
        return BDE_TITLE
    return _house_separators(stripped)


GUESTS = (
    re.compile(r"\bfor\s+(\d{1,3})\s+guests?\b", re.I),
    re.compile(r"\b(\d{1,3})\s+guests?\b", re.I),
    re.compile(r"\b(\d{1,3})\s+passengers?\b", re.I),
    re.compile(r"\baccommodat\w*\s+(?:up\s+to\s+)?(\d{1,3})\b", re.I),
)
"""How a vessel description states how many people it carries.

Berth price is per person, so the guest count is what tells a diver whether
they are buying into a boat of twelve or of thirty-four -- a difference in
group size, dive-deck crowding and how the same reef feels.

Read from the vessel description, which is the only place the scrape currently
has it. That covers about half the fleet: the number also sits in the page's
specification table, which nothing parses yet, so a vessel without one here has
not been asked rather than declined to say.
"""

MAX_GUESTS = 60
"""Above this the match is not a guest count.

Liveaboards in this fleet run from eight to thirty-four berths. A larger number
in the same sentence is a length in feet, a year, or a price.
"""


def _guests(summary: str | None) -> int | None:
    """Pull a guest count out of a vessel description, or admit there is none."""
    if not summary:
        return None
    for pattern in GUESTS:
        match = pattern.search(summary)
        if match:
            value = int(match.group(1))
            if 0 < value <= MAX_GUESTS:
                return value
    return None


def _dives(
    stated: int | None = None,
    *,
    nights: int | None = None,
    for_nights: int | None = None,
) -> int:
    """The operator's own dive count, or ``0`` when it does not publish one.

    This used to work the count out from nights at three dives a full day, and
    the arithmetic was defensible -- eighteen for a seven-night week is what the
    vessels that do state a figure publish. The problem was never accuracy.

    Price per dive is ``total / dives``, and at a fixed rate per night that is
    ``total / (3 * (nights - 1))`` -- a constant multiple of price per night.
    292 of 317 itineraries run seven nights, so across almost the whole table
    the column ranked trips in exactly the same order as the column beside it,
    while looking like an independent measurement. A number whose denominator
    was invented cannot say anything its numerator did not already say.

    Checking the formula against the counts operators do publish settled it.
    For a seven-night week they state anything from 15 to 21 while the formula
    says 18 for all of them -- a six-dive spread, a third of the figure, and
    the whole of what price per dive is meant to distinguish.

    Ten vessels publish a count, and a probe established the rest is not on the
    page at all. So the honest answer for the others is that nobody has said --
    the same answer this project gives for a dive site it cannot name and a fee
    nobody has read.

    ``for_nights`` guards the second half of the problem. A published count is
    the figure for that vessel's standard week, and applying it to every trip
    the boat sells put seventeen dives on a three-night mini-safari. A count is
    used only for the trip length it was quoted for.
    """
    if not stated or stated <= 0:
        return 0
    if for_nights is not None and nights is not None and nights != for_nights:
        return 0
    return stated


ABOVE_OPEN_WATER = re.compile(
    r"\b(?:advanced|experienced|technical|tec|tech)\b", re.I
)
"""Words that put an experience line above the entry level.

The line is free text -- "Advanced Open Water - 50 minimum logged dives
required." on the one trip a probe has read, and the spread across the fleet is
unknown -- so it is matched rather than mapped from a fixed vocabulary, and
anything unrecognised stays Open Water. Nothing is lost either way: the
operator's sentence is kept verbatim beside the level.

Deliberately no keyword reaches ``EXPERIENCED_100``. That level's label is
"Advanced + 100 dives", so reading it out of the word "experienced" would put a
hundred-dive bar on a trip whose operator named no number at all. The count
comes from the count.
"""


def _strictest(
    ours: dict[str, Any] | None, theirs: dict[str, Any] | None
) -> dict[str, Any] | None:
    """The higher of two stated entry bars, with both claims kept.

    liveaboard.com and PADI disagree about the bar on 60 of the 127 trips both
    describe, and in both directions: PADI is stricter on 19 and softer on 41.
    Neither source is the authority -- both are quoting the same operator -- so
    preferring one wholesale would publish a bar softer than somebody stated
    roughly half the time.

    Between them they cover the season completely: of the 402 itineraries, 190
    are described by liveaboard.com alone, 85 by PADI alone, 127 by both, and
    none by neither. That is why the Entry bar column never prints "not
    stated" -- not because the page declines to, but because nothing here is
    unread.

    Taking the higher level never softens either claim. It does state a bar
    stricter than one of them, which is the trade made deliberately: a diver
    turned away at the dock has been misled, and a diver who over-prepared has
    not.

    The winner's bar is taken whole rather than combined field by field. Mixing
    one source's level with the other's dive count produces a requirement
    neither operator stated, which is the same fabrication this module refuses
    everywhere else. It also matters that PADI's ``minimalNumberOfDives`` is a
    field whose semantics are recorded as unverified in
    `docs/sources/padi.com.md`: taking a maximum across it would raise a stated
    15-dive bar to 50 on the strength of a number nobody has confirmed is a
    requirement at all.

    Where they disagree the note says so and names both, because a visitor
    comparing two boats deserves to know the two sources do not agree rather
    than to see one number presented as settled.
    """
    if not ours or not theirs:
        return ours or theirs

    order = {level.value: n for n, level in enumerate(DIVER_LEVEL_ORDER)}
    ours_level = order.get(str(ours.get("min_level")), 0)
    theirs_level = order.get(str(theirs.get("min_level")), 0)
    winner = ours if ours_level >= theirs_level else theirs

    bar = dict(winner)

    if ours.get("min_level") != theirs.get("min_level"):
        labels = {level.value: text for level, text in DIVER_LEVEL_LABELS.items()}
        bar["notes"] = " ".join(
            part for part in (
                winner.get("notes"),
                f"Sources disagree: liveaboard.com states "
                f"{labels.get(str(ours.get('min_level')), ours.get('min_level'))}, "
                f"PADI Travel states "
                f"{labels.get(str(theirs.get('min_level')), theirs.get('min_level'))}. "
                f"The stricter is shown.",
            ) if part
        )
    elif theirs.get("notes") and theirs["notes"] not in (winner.get("notes") or ""):
        bar["notes"] = " ".join(p for p in (winner.get("notes"), theirs["notes"]) if p)
    return bar


def padi_key(slug: str, name: str) -> str:
    """`itinerary_key`, but tolerant of a second source's spelling.

    The fee and itinerary books key on the raw string and are right to: they come
    from liveaboard.com, so both sides spell a trip identically. PADI does not.
    It writes "&" for "and", en-dashes for hyphens, "Port Ghalib" where our
    titles say "Marsa Ghalib", and "BDE" for a name we give in full. Keyed
    exactly, 65 of its trips reached us; keyed on letters and digits with the
    harbour names folded, 104 do.

    Deliberately a second function rather than a change to `itinerary_key`.
    That key identifies an itinerary *within* this dataset, and loosening it
    would start merging two of our own sailings that differ by punctuation. This
    one only ever looks a foreign record up.
    """
    from .scrape.padi_com import PadiComAdapter

    trip, _, _ = _split_title(name)
    return f"{slug}::{PadiComAdapter.compare_key(PadiComAdapter.fold_ports(trip))}"


def _padi_requirements(record: dict[str, Any]) -> dict[str, Any] | None:
    """PADI's coded entry bar, in this dataset's shape.

    PADI states two things that are not the same claim, and this keeps them
    apart. ``requiredCertification`` is a requirement and becomes the level.
    ``experienceRequiredDives`` is worded *recommended* on every one of its
    labels, so it goes into ``notes`` as a sentence rather than into
    ``min_logged_dives`` -- hardening somebody's advice into a gate is the same
    error as softening their gate into advice.

    ``minimalNumberOfDives`` is a plain integer and is not the recommendation
    restated: Blue Melody states 30, which the coded field cannot produce. So it
    is the operator's own number and it is what fills ``min_logged_dives``.
    """
    bar = record.get("requirements")
    if not isinstance(bar, dict) or not bar:
        return None

    level = bar.get("min_level")
    logged = int(bar.get("min_logged_dives") or 0)
    recommended = int(bar.get("recommended_logged_dives") or 0)
    if not level and not logged:
        return None

    notes = None
    if recommended:
        notes = f"PADI Travel: {recommended}+ logged dives recommended."

    return {
        "min_level": level or DiverLevel.OPEN_WATER.value,
        "min_logged_dives": logged,
        "max_depth_m": None,
        "nitrox_recommended": False,
        "strong_current": False,
        "notes": notes,
    }


PADI_FEE_PROVENANCE: dict[str, Any] = {
    "kind": "scraped",
    "source_id": "padi.com",
}
"""Where a PADI fee line came from.

Every price and fee on this page carries one, and a second seller's fees are no
exception -- a line whose origin is not recorded is indistinguishable from one
this project made up, which is the whole distinction the site exists to police.
The retrieval date is stamped on by the caller, which is the only part of this
that varies per run.
"""


def _padi_fees(record: dict[str, Any]) -> dict[str, Any] | None:
    """PADI's mandatory charges in this dataset's fee shape.

    The parser has already done the reading -- classified each title against
    the same table liveaboard.com's wording goes through, mapped PADI's
    charging unit onto a `FeeBasis`, and decided whether what is left adds up.
    All this does is unwrap the money into the two keys `FeeItem.from_dict`
    expects, and pass the verdict through untouched.

    Returns ``None`` for a trip PADI has not been read for, which is not the
    same as a trip PADI says has no required extras: the first writes no key
    and claims nothing, the second writes an empty list and a complete bill,
    and only the second is a disclosure. Fifty of PADI's 307 itineraries are
    the second kind.
    """
    fees = record.get("fees")
    if not isinstance(fees, dict) or "lines" not in fees:
        return None
    return {
        "lines": [dict(line, provenance=dict(PADI_FEE_PROVENANCE))
                  for line in fees["lines"]],
        "complete": bool(fees.get("complete")),
    }


def _requirements(trip: dict[str, Any]) -> dict[str, Any] | None:
    """The entry bar a trip states, or ``None`` when none was read.

    ``None`` means nobody has looked, not that the operator asks for nothing --
    the same distinction this module makes about fees. It is the safer way
    round for a safety requirement: an unread trip shows no bar rather than a
    bar somebody invented.

    The operator's sentence is kept verbatim in ``notes``. The level and the
    logged-dive count are only ever read *out* of it, never added to it: a line
    naming a certification and no number gets ``min_logged_dives`` of zero,
    because filling in a plausible one would soften a stated requirement.
    """
    experience = (trip.get("experience") or "").strip()
    logged = int(trip.get("min_logged_dives") or 0)
    if not experience and not logged:
        return None

    if logged >= 100:
        level = DiverLevel.EXPERIENCED_100
    elif logged >= 50:
        level = DiverLevel.ADVANCED_50
    elif ABOVE_OPEN_WATER.search(experience):
        level = DiverLevel.ADVANCED
    else:
        level = DiverLevel.OPEN_WATER

    return {
        "min_level": level.value,
        "min_logged_dives": logged,
        "max_depth_m": None,
        "nitrox_recommended": False,
        "strong_current": False,
        "notes": experience or None,
    }


AVAILABILITY = {
    "soldout": "sold_out",
    "outofstock": "sold_out",
    "limitedavailability": "limited",
    "instock": "available",
    "onlineonly": "available",
    "instoreonly": "available",
    "preorder": "available",
}
"""schema.org availability, folded to what a diver needs to know.

The scrape has carried this from the start and promote threw it away, so 127
sold-out departures sat on the page priced exactly like the 746 bookable ones.
On a page whose whole job is comparison that is worse than a missing column:
sorting by cheapest could put a trip nobody can buy at the top of the list.

Unknown stays unknown. A source that says nothing about availability has not
said the trip is full.
"""


def _availability(raw: str | None) -> str | None:
    """Read schema.org's availability URL, or admit the source was silent."""
    if not raw:
        return None
    token = str(raw).rstrip("/").rsplit("/", 1)[-1].replace("_", "").lower()
    return AVAILABILITY.get(token)


def berth_key(slug: str, start: str) -> str:
    """Vessel and day, which is what one sailing is across every seller.

    The same key the PADI departure join uses, and for the same reason: a date
    has no spelling, so this is exact where the itinerary join has to be
    forgiving. Unique across all 864 records in the cabin book.
    """
    return f"{slug}::{start}"


# One rung of a cabin ladder, positional to keep 2,982 of them off the wire as
# repeated keys: name index into the pooled list, price in the display
# currency, places left, and the single-occupancy surcharge as a percentage.
#
# Terse because the page is one file: anything written per departure ships on
# every visit whether it is read or not, and named keys here cost more than the
# numbers they label. Measured — the first, fully named shape cost the page
# 136 KB against 76 KB for this one. The layout is stated in the dataset's own
# notes and named in `app.js`, so nothing has to count fields to read it.
CABIN_FIELDS = 4

SELLERS = ["liveaboard.com", "padi.com"]
"""Who is selling, pooled and indexed by every berth block.

Both names are here though only the first fills a block today: PADI sells 601
of these same sailings and publishes an availability figure of its own, so the
list that holds two answers is the list to write now rather than after a second
seller arrives ([#92]). An index costs two bytes against a repeated string's
twenty-two, on a field written once per departure.
"""


def _berth_blocks(
    record: dict[str, Any] | None,
    sailing: dict[str, Any] | None,
    names: dict[str, int],
    fx_table: Any,
) -> list[dict[str, Any]]:
    """What each seller says is left on this sailing, and at what price.

    A **list**, one block per seller, and both sellers fill one now ([#92]).
    They are answering two different questions and the block has a slot for
    each, because merging them would be a number neither of them published:

    * **at the advertised price** -- summed across every room selling at it.
      Only a ladder can say this, so only liveaboard.com does.
    * **on the sailing** -- every berth still for sale at any price. Both say
      it: liveaboard.com by adding its ladder up, PADI Travel as the single
      figure it publishes instead of a ladder.

    Which of the two PADI states was measured rather than assumed, and the
    answer is the second. Across the 584 sailings where both speak, PADI's
    figure equals liveaboard's whole-sailing total on 77% exactly and 88%
    within two berths -- a day's drift between the two crawls -- against 22%
    and a mean error of 7 berths for the count at the advertised price. Putting
    it in the first slot would have relabelled "22 aboard" as "22 at this
    price" on 258 rows that have no ladder to contradict it.

    They disagree outright on 24 sailings: 21 where PADI still sells berths
    liveaboard.com calls full, 3 the other way. That is not a reason to prefer
    one -- it is the thing this site exists to show, and both are printed under
    the name of whoever said it.

    A seller that states a count but no ladder simply has no ``cabins``. That
    is not a gap to fill with one invented rung: "24 places" and "24 places at
    £1,748" are different claims, and only the second is a ladder.
    """
    blocks: list[list[Any]] = []

    # PADI's, built first and appended last so liveaboard.com stays the block
    # the page reads for the advertised price. Its `availability` is a plain
    # integer on all 3,521 sailings and needs no currency, no ladder and no
    # conversion -- which is why it can be published from data already
    # committed, with no request to anybody.
    padi_left = (sailing or {}).get("availability")
    padi_block = (
        [SELLERS.index("padi.com"), None, None, int(padi_left)]
        if isinstance(padi_left, int) and padi_left >= 0
        else None
    )

    if not record or not record.get("cabins"):
        return [padi_block] if padi_block else []

    rungs: list[list[Any]] = []
    for cabin in record["cabins"]:
        price = cabin.get("price")
        if price is None:
            # A cabin with no price is a rung with no height. It is dropped
            # rather than drawn at zero, which would read as free.
            continue
        name = cabin.get("name") or "Cabin"
        if name not in names:
            names[name] = len(names)
        # Normalisation happens in Python only: the browser sums lines that are
        # switched on and converts nothing, so the ladder arrives in the
        # display currency like every other figure on the page.
        display = _to_display(price, record.get("currency") or "USD", fx_table)
        rungs.append([
            names[name],
            display,
            0 if cabin.get("sold_out") else cabin.get("berths"),
            cabin.get("single_supplement_pct"),
        ])

    if not rungs:
        return [padi_block] if padi_block else []

    cheapest = min(rung[1] for rung in rungs)
    at_cheapest = [rung for rung in rungs if rung[1] == cheapest]
    # Across every room selling at that price, because a boat can split them:
    # 233 of 864 sailings list more than one cabin at their cheapest. One
    # unstated count makes the whole total unknown rather than a partial sum.
    #
    # Kept here rather than left to the browser because it is a *rule* — which
    # rooms count, and when the answer is unknown — and this project keeps its
    # rules in one tested place. The cheapest rung still on sale is not a rule
    # but a minimum over numbers already normalised, so the page takes that one
    # itself rather than paying to ship it 864 times.
    spots: int | None = None
    if all(rung[2] is not None for rung in at_cheapest):
        spots = sum(rung[2] for rung in at_cheapest)

    # The whole sailing, by the same rule one rung up: one unstated count makes
    # the total unknown rather than a partial sum. This is what PADI's single
    # figure is comparable to, and publishing it is what lets the two be set
    # beside each other instead of taken on trust.
    aboard: int | None = None
    if all(rung[2] is not None for rung in rungs):
        aboard = sum(rung[2] for rung in rungs)

    # Positional, and the seller is an index into the dataset's pool: a block
    # is written once per departure, so a repeated "liveaboard.com" string is
    # 22 KB of one word. [seller, spots at the advertised price, cabins,
    # berths left on the sailing].
    return [[SELLERS.index("liveaboard.com"), spots, rungs, aboard]] + (
        [padi_block] if padi_block else []
    )


def _sale_for(
    cabins: dict[str, Any] | None,
    sailing: dict[str, Any] | None,
    own: str,
    fx_table: Any,
) -> dict[str, Any] | None:
    """Whether this sailing is discounted, and by whom.

    **A sale is a whole-ladder fact, measured rather than assumed.** On all 263
    discounted sailings read, every cabin is marked down by the same
    percentage; not one has a partial ladder, and not one discounts a dearer
    room while leaving the cheapest at list. So the cheapest rung -- which is
    the advertised price, checked on 864 of 864 -- carries the whole answer,
    and comparing it against its own ``<del>`` is like for like. Taking the
    cheapest price against the *dearest* room's list price is the obvious
    mistake here and reports a 33% sale as 40%.

    Both sellers are asked and **neither is allowed to speak for the other**.
    Where both publish a discount they agree exactly -- 158 of 158 sailings, to
    the percentage point -- but agreement measured today is not a rule, so
    ``pct`` and ``was`` are read only from the seller whose price this row
    actually prints. A row where PADI discounts and liveaboard.com does not
    (two sailings of Red Sea Aggressor IV) is *on sale*, and says so, without
    marking down a price nobody marked down.

    Silence and absence are different, as everywhere else here. A booking page
    that could not be read contributes no opinion rather than a "no"; three of
    the five sailings where only PADI reports a discount have no ladder at all.
    """
    # (price, list price, the currency both are in). The currency may be
    # missing and that is survivable *here* and nowhere else in this file: a
    # percentage is a ratio of two figures in the same unit, whatever that unit
    # is, so it needs no currency at all. Only the cash "was" does, and it is
    # withheld rather than converted at an assumed rate -- the rule the sailing
    # book already applies to PADI's prices, where guessing euro would have put
    # every Aggressor out by the EUR/USD rate.
    figures: dict[str, tuple[float, float, str | None]] = {}

    priced = [c for c in ((cabins or {}).get("cabins") or []) if c.get("price") is not None]
    if priced:
        cheapest = min(priced, key=lambda c: c["price"])
        listed = cheapest.get("list_price")
        if listed and listed > cheapest["price"] > 0:
            figures["liveaboard.com"] = (
                float(cheapest["price"]), float(listed), (cabins or {}).get("currency"),
            )

    if sailing:
        price, was = sailing.get("price"), sailing.get("was")
        if isinstance(price, (int, float)) and isinstance(was, (int, float)) and was > price > 0:
            figures["padi.com"] = (float(price), float(was), sailing.get("currency"))

    if not figures:
        return None

    sale: dict[str, Any] = {
        "sellers": sorted(SELLERS.index(name) for name in figures),
    }
    mine = figures.get(own)
    if mine:
        price, was, currency = mine
        # A markdown too small to state is not a stated markdown. Under half a
        # percent rounds to zero, and "0% off" beside a price is worse than the
        # nothing it is trying to say; the row stays on sale on the strength of
        # `sellers`, which is the fact that does not round away.
        cut = int(round(100 * (1 - price / was)))
        if cut:
            sale["pct"] = cut
            if currency:
                sale["was"] = _to_display(was, currency, fx_table)
    return sale


def _on_sale_summary(
    departures: list[dict[str, Any]],
    boat_of: Mapping[str, str],
    boats: Mapping[str, Mapping[str, Any]],
    read: str,
) -> dict[str, Any] | None:
    """What is discounted right now, per boat, over the whole season.

    Built from the very departures the page's filter selects, so the panel and
    the chip can never report different fleets -- the failure mode of any
    summary computed down a second path.

    The **window** is the point, and it is what PADI's own deals listing cannot
    say. PADI publishes one exemplar sailing per vessel; this states that Red
    Sea Aggressor II is 33% off every week from 1 May to 24 July and full price
    from 31 July, which is the difference between knowing a boat is on sale and
    knowing which week to book.
    """
    on_sale = [d for d in departures if d.get("sale")]
    if not on_sale:
        return None

    total: Counter = Counter(boat_of[d["itinerary_id"]] for d in departures)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in on_sale:
        grouped[boat_of[row["itinerary_id"]]].append(row)

    rows: list[dict[str, Any]] = []
    # By the name the panel prints, not by the id it does not. Sorted on the id
    # this read "Ocean Lovers, Oceanix, MY Odyssey Liveaboard" -- alphabetical
    # in a column nobody can see. The id breaks ties so promotion stays pure.
    for boat_id, group in sorted(
        grouped.items(), key=lambda kv: (str(boats[kv[0]]["name"]).lower(), kv[0])
    ):
        cuts = sorted({d["sale"]["pct"] for d in group if d["sale"].get("pct")})
        starts = sorted(d["start"] for d in group)
        row: dict[str, Any] = {
            "boat": boat_id,
            "boat_name": str(boats[boat_id]["name"]),
            "sailings": len(group),
            "of": total[boat_id],
            "first": starts[0],
            "last": starts[-1],
            "sellers": sorted({s for d in group for s in d["sale"]["sellers"]}),
        }
        # A range only where the boat really runs more than one, which is rare:
        # an operator discounts a season, not a sailing. Printing "10–10%"
        # everywhere to accommodate the exception is noise on every other row.
        if cuts:
            row["pct"] = cuts[0]
            if cuts[-1] != cuts[0]:
                row["pct_max"] = cuts[-1]
        rows.append(row)

    return {
        # The day the ladders were read. The most perishable thing on the page
        # after a berth count, and stated for the same reason: a sale is what a
        # seller claimed when it was looked at, and it can end overnight.
        "read": read,
        "sailings": len(on_sale),
        "boats": rows,
    }


def _cut(price: float, was: float) -> int:
    """A markdown as a percentage, the way ``_sale_for`` states one.

    One function rather than two so the change log and the row it explains can
    never round differently -- "36 sailings no longer 33% off" beside a table
    that had said 32% is the panel disagreeing with itself.
    """
    return int(round(100 * (1 - price / was)))


def _sales_block(
    sales: dict[str, Any] | None,
    boats: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any] | None:
    """What moved on liveaboard.com's booking pages between the last two readings.

    The half of the change log that could not previously speak. PADI publishes
    a deals *listing* and `data/deals.json` keeps a day per reading of it, so
    `_deals_block` can diff two committed days; liveaboard.com publishes no
    listing at all and `data/cabins.json` keeps only the last reading, so there
    was no second day here to diff against. `tools/derive_sales.py` writes one
    -- three fields per sailing, filed under the day the booking page was read.

    It is the bigger of the two signals: 263 discounted sailings on 22 boats
    against PADI's 13, and nine of those boats appear in no deals listing
    anywhere. The day the Red Sea Aggressors' 33% sale ended, PADI's half
    reported *three offers withdrawn* -- one exemplar sailing per vessel -- for
    an event that moved **36 sailings**.

    **Only over the sailings both readings covered.** A key is in a day's
    census exactly when that booking page was read, so a sailing missing from
    either side has not come off sale: nobody looked at it. The count of those
    is reported rather than dropped, which is the same rule `_deals_block`
    applies through `partial` and the crawl applies through `not_looked_at`.

    Grouped by boat because an operator discounts a season rather than a
    sailing, and because the ungrouped list is the failure being fixed: 36
    identical lines say less than one line saying 36. Bounded by the fleet --
    at most three moves per boat -- so nothing is capped and nothing is
    silently dropped.
    """
    days = (sales or {}).get("days") or {}
    if not days:
        return None
    order = sorted(days)
    today = order[-1]
    now = (days[today] or {}).get("sailings") or {}
    if not now:
        return None

    block: dict[str, Any] = {"read": today, "sailings": len(now)}
    if len(order) < 2:
        # A first reading has nothing to be a change from, and saying so is not
        # the same as saying nothing changed.
        block["first_reading"] = True
        return block

    previous = order[-2]
    before = (days[previous] or {}).get("sailings") or {}
    block["previous"] = previous

    both = set(now) & set(before)
    listed = {key for key in both if key.split("::")[0] in boats}
    block["compared"] = len(listed)
    # Stated every time, with a count, because a change report that quietly
    # narrows its own scope reads as "that was everything" -- the failure this
    # project exists to correct in other people.
    block["not_compared"] = len(set(now) ^ set(before))
    if len(both) != len(listed):
        block["unlisted"] = len(both) - len(listed)

    started: dict[str, list[tuple[str, int | None]]] = defaultdict(list)
    ended: dict[str, list[tuple[str, int | None]]] = defaultdict(list)
    changed: dict[str, list[tuple[str, int, int]]] = defaultdict(list)

    for key in sorted(listed):
        boat, _, start = key.partition("::")
        (was_now, was_before) = (now[key][1], before[key][1])
        if was_now and not was_before:
            started[boat].append((start, _cut(now[key][0], was_now)))
        elif was_before and not was_now:
            ended[boat].append((start, _cut(before[key][0], was_before)))
        elif was_now and was_before:
            after, first = _cut(now[key][0], was_now), _cut(before[key][0], was_before)
            if after != first:
                changed[boat].append((start, first, after))

    moves: list[dict[str, Any]] = []

    def move(boat: str, kind: str, sailings: list[tuple[str, Any]]) -> dict[str, Any]:
        cuts = sorted({c for _, c in sailings if c})
        row: dict[str, Any] = {
            "boat": boat,
            "boat_name": str(boats[boat]["name"]),
            "kind": kind,
            "sailings": len(sailings),
            "first": min(s for s, _ in sailings),
            "last": max(s for s, _ in sailings),
        }
        # A range only where the boat really runs more than one, as the fleet
        # table does: an operator discounts a season at one rate, and printing
        # "33–33%" everywhere to accommodate the exception is noise on every
        # other row.
        if cuts:
            row["pct"] = cuts[0]
            if cuts[-1] != cuts[0]:
                row["pct_max"] = cuts[-1]
        return row

    for boat, rows in started.items():
        moves.append(move(boat, "started", rows))
    for boat, rows in ended.items():
        moves.append(move(boat, "ended", rows))
    for boat, rows in changed.items():
        row = move(boat, "changed", [(s, after) for s, _, after in rows])
        was = sorted({first for _, first, _ in rows})
        row["was_pct"] = was[0]
        if was[-1] != was[0]:
            row["was_pct_max"] = was[-1]
        moves.append(row)

    # By the name the panel prints, then by what happened, so the order does
    # not depend on the order of a dict. Promotion is pure and CI compares its
    # output byte for byte.
    block["moves"] = sorted(moves, key=lambda m: (m["boat_name"].lower(), m["kind"], m["boat"]))
    return block


STALE_LADDER = 0.03
"""How far a ladder's bottom rung may sit from the row's advertised price.

The two are read by different passes on different days, so a little drift is
ordinary: with `cabins.yml` running an hour behind the refresh, all 864 ladders
sat within 0.6% of their own row. Three percent is loose enough that a night's
repricing is not a problem and tight enough to catch a ladder that is no longer
this sailing's, since the rungs across this fleet run from €500 to €2,900.
"""


def _drop_stale_ladder(
    blocks: list[list[Any]], advertised: int | None
) -> tuple[list[list[Any]], int | None]:
    """Refuse a ladder that contradicts the price it is supposed to explain.

    The advertised price *is* the bottom rung -- checked on 864 of 864 -- so a
    bottom rung far below it is not a cheaper berth, it is last week's prices
    still on the shelf. It happened the day the Red Sea Aggressors' 33% sale
    ended: the refresh re-priced 36 sailings to their list price while the
    booking pages behind them had been read two days earlier, and the page was
    left offering a €1,588 berth on a €2,371 sailing. A price nobody can buy,
    published by the site that exists to catch exactly that.

    Dropping the ladder rather than the row, in both directions and whichever
    of the two is the stale one, because it is the conservative loss: a sailing
    with no ladder falls back to what its sellers say is left, and the panel
    that would have opened is not worth a figure that is wrong.

    Returns the surviving blocks and, when one went, the rung that disagreed --
    so the caller can name it rather than drop it in silence.
    """
    if not advertised:
        return blocks, None
    kept: list[list[Any]] = []
    dropped: int | None = None
    for block in blocks:
        rungs = block[2] if len(block) > 2 else None
        if rungs:
            cheapest = min(rung[1] for rung in rungs)
            if abs(cheapest - advertised) / advertised > STALE_LADDER:
                dropped = cheapest
                continue
        kept.append(block)
    return kept, dropped


def _fx_table(fx: dict[str, Any] | None) -> Any:
    """The rate table promote converts cabin prices with, or ``None``.

    Built from the same payload the dataset publishes, so a ladder and the
    berth price above it are converted at one rate. A payload that will not
    parse yields ``None`` and the ladder stays in its quoted currency rather
    than failing the promotion: an unconverted number is wrong by an FX move,
    a missing dataset is wrong by everything.
    """
    from .money import FxTable

    try:
        return FxTable.from_dict(fx or _default_fx())
    except (KeyError, ValueError):
        return None


def _to_display(amount: float, currency: str, fx_table: Any) -> int:
    """One cabin price in the display currency, rounded as the page prints it.

    An ``int``, because the page prints whole euros and ``1501.0`` is two
    characters of nothing 2,982 times over.
    """
    from .money import Money

    money = Money(_dec_money(amount), currency)
    if fx_table is None:
        return int(round(money.amount))
    converted, _ = fx_table.to_display(money)
    return int(round(converted.amount))


def _dec_money(amount: float):
    from decimal import Decimal

    return Decimal(str(amount))


PORTS_THAT_ARE_ALSO_SITES = (
    "safaga", "dahab", "soma bay", "hurghada", "marsa alam", "port ghalib",
    "sharm el sheikh", "hamata", "marsa ghalib",
)
"""Names that are a harbour and a dive area at once.

Egypt sails from the same places it dives. Treating these as evidence that a
bracketed pair is a route rather than a port pair loses the port pair, which is
the more useful reading: a diver books flights off it.
"""


def _without_ports(text: str) -> str:
    """Drop names that are harbours before asking whether text names a reef.

    Substituted on word boundaries rather than by replacing " name ": adjacent
    repeats share the space between them, so "Safaga - Safaga" lost only the
    first and the leftover looked like a reef.
    """
    lowered = normalise(text)
    for port in PORTS_THAT_ARE_ALSO_SITES:
        lowered = re.sub(rf"\b{re.escape(normalise(port))}\b", " ", lowered)
    return lowered


def _deal_row(
    deal: Mapping[str, Any],
    boat_id: str,
    boat_name: str,
    fx_table: Any,
) -> dict[str, Any]:
    """One offer as the page needs it: converted here, never in the browser.

    Both figures survive the conversion. The euro is what the rest of the page
    is denominated in and is what makes two deals comparable; the quoted amount
    and its currency are what PADI actually published, and a converted number
    presented as the seller's own is the small dishonesty this project spends
    its whole codebase not committing.
    """
    nights = _nights(str(deal.get("start") or ""), str(deal.get("end") or ""))
    price, was = float(deal["price"]), float(deal["was"])
    currency = str(deal["currency"])
    row: dict[str, Any] = {
        "boat": boat_id,
        "boat_name": boat_name,
        "title": deal.get("title"),
        "kind": deal.get("kind_label"),
        "value": deal.get("value"),
        "price": _to_display(price, currency, fx_table),
        "was": _to_display(was, currency, fx_table),
        "quoted": price,
        "quoted_was": was,
        "currency": currency,
        "start": deal.get("start"),
        "end": deal.get("end"),
        "url": deal.get("url"),
    }
    if nights:
        row["nights"] = nights
    return row


def _deal_change(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[str]:
    """What moved between two readings of one vessel's offer, in words.

    Empty when nothing did, which is what makes it the test as well as the
    report -- there is no separate predicate to keep in step with this list.
    """
    moved: list[str] = []
    if before.get("price") != after.get("price"):
        moved.append("price")
    if before.get("was") != after.get("was"):
        moved.append("undiscounted price")
    if before.get("title") != after.get("title"):
        moved.append("offer")
    if (before.get("kind_label"), before.get("value")) != (
            after.get("kind_label"), after.get("value")):
        moved.append("discount")
    if (before.get("start"), before.get("end")) != (after.get("start"), after.get("end")):
        moved.append("sailing")
    return moved


def _deals_block(
    deals: dict[str, Any] | None,
    padi_vessels: Mapping[str, Mapping[str, Any]],
    boats: Mapping[str, Mapping[str, Any]],
    fx_table: Any,
) -> dict[str, Any] | None:
    """Today's deals, and what moved since the last day in the book.

    **The join places the deal, never PADI's country field.** That field says
    United States of America for all three Red Sea Aggressors, which is why the
    query has to ask for the USA as well as Egypt -- and asking for it also
    returns Bahamas, Belize, Cayman and Roatan, which sail an ocean away. Five
    of eighteen, so the label is wrong about where a boat is more than a quarter
    of the time. A vessel that joins to a boat of ours is Egyptian because our
    own fleet is; one that does not is **reported rather than dropped**, because
    an Egyptian boat filed under the USA and unmatched here is precisely the
    case worth catching, and deleting it silently would reproduce the bug.

    Pure, like the rest of promotion: the book is committed, so the diff is a
    diff between two committed days and `promote --check` proves the panel on
    the page is this code's reading of them. A change log computed from a
    gitignored snapshot would quietly become "no changes" once the artifact
    aged out.
    """
    days = (deals or {}).get("days") or {}
    if not days:
        return None
    order = sorted(days)
    today, entry = order[-1], days[order[-1]] or {}
    offers = entry.get("offers") or {}
    if not offers:
        return None

    by_slug = {
        str(record.get("slug")): boat
        for boat, record in padi_vessels.items()
        if record.get("slug")
    }

    def joined(book: Mapping[str, Any]) -> dict[str, tuple[str, Mapping[str, Any]]]:
        """The offers in one day's reading that land on a boat this site holds."""
        out: dict[str, tuple[str, Mapping[str, Any]]] = {}
        for slug, deal in book.items():
            boat_id = by_slug.get(slug)
            if boat_id and boat_id in boats:
                out[boat_id] = (slug, deal)
        return out

    here = joined(offers)
    rows = sorted(
        (_deal_row(deal, boat_id, str(boats[boat_id]["name"]), fx_table)
         for boat_id, (_, deal) in here.items()),
        # By when you would sail, not by how much comes off. Ordering deals by
        # the size of the discount is a best-value ranking wearing a sort order,
        # and this site does not grade what it lists.
        key=lambda row: (row["start"] or "", row["boat_name"], row["boat"]),
    )

    elsewhere = sorted(
        {
            (str(deal.get("shop") or slug), str(deal.get("url") or ""))
            for slug, deal in offers.items()
            if slug not in {s for s, _ in here.values()}
        }
    )

    block: dict[str, Any] = {
        "read": today,
        "source": "padi.com",
        "url": entry.get("url"),
        "offers": rows,
        # Named, not counted. A number would say five vessels did not match;
        # the names are what let a reader notice that one of them is Egyptian.
        "unmatched": [{"name": name, "url": url} for name, url in elsewhere],
    }

    previous = order[-2] if len(order) > 1 else None
    if previous is None:
        # A first reading has nothing to be a change from, and saying so is not
        # the same as saying nothing changed.
        block["first_reading"] = True
        return block

    before_entry = days[previous] or {}
    before = joined(before_entry.get("offers") or {})
    block["previous"] = previous

    # A day either reading could not finish is a day neither of them knows what
    # was on. An offer absent from a truncated reading has not been withdrawn;
    # it has not been looked at -- the same rule that stops an unreadable vessel
    # page emptying a boat's month, arriving through a different door.
    partial = bool(entry.get("truncated")) or bool(before_entry.get("truncated"))

    changed: list[dict[str, Any]] = []
    for boat_id in sorted(set(before) & set(here)):
        moved = _deal_change(before[boat_id][1], here[boat_id][1])
        if not moved:
            continue
        was_row = _deal_row(before[boat_id][1], boat_id,
                            str(boats[boat_id]["name"]), fx_table)
        now_row = _deal_row(here[boat_id][1], boat_id,
                            str(boats[boat_id]["name"]), fx_table)
        changed.append({"moved": moved, "before": was_row, "after": now_row})

    block["changes"] = {
        "new": [] if partial else sorted(set(here) - set(before)),
        "withdrawn": [] if partial else sorted(set(before) - set(here)),
        "changed": changed,
        # The names, so a withdrawal reads as a boat rather than as an id.
        "names": {
            boat_id: str(boats[boat_id]["name"])
            for boat_id in sorted(set(before) | set(here))
            if boat_id in boats
        },
    }
    if partial:
        block["changes"]["partial"] = True
    return block


def _nights(start: str, end: str) -> int | None:
    try:
        delta = (date.fromisoformat(end) - date.fromisoformat(start)).days
    except ValueError:
        return None
    return delta if MIN_NIGHTS <= delta <= MAX_NIGHTS else None


def _padi_only_departures(
    sailing_book: dict[str, dict[str, Any]],
    known: set[tuple[str, str]],
    named: dict[str, str],
    *,
    season: tuple[date, date] | None,
    retrieved: str,
    not_asked: frozenset[str] = frozenset(),
) -> tuple[list[dict[str, Any]], list[str]]:
    """The sailings PADI sells on dates the first source does not list.

    Shaped as candidate departures so they join `grouped` and go through the
    rest of promotion unchanged -- the same itinerary build, the same vessel fee
    book, the same title tidying. A second code path that assembled its own rows
    would be a second set of rules for what a row means, and the two would
    drift.

    Three things make a sailing eligible and each one can fail on its own:

    * the date is inside the published season,
    * `(boat, start)` is not already a row -- an exact key, because a date has
      no spelling, and the whole point is not to publish the same sailing twice,
    * PADI's title parses into a trip name and a night count.

    The title is what the itinerary is *named*, so an unparsable one is
    reported rather than filled in: a row under "Unnamed itinerary" would be a
    trip whose identity this code invented, and identity is what the id is
    built from.

    The price is PADI's and is recorded as PADI's. These rows carry no
    `padi_price`: a second seller's figure beside its own is not two sellers
    agreeing, and the Sellers column would read "both, same" on a sailing only
    one site offers.

    `named` maps `padi_key` to the trip name the first source already uses for
    that boat, and a hit means the new sailing joins that itinerary rather than
    founding one. PADI sells dates on trips we already carry -- Blue Seas
    writes *Daedalus & Fury Shoal (Port Ghalib- Port Ghalib)* where our source
    writes the same name with the missing space restored -- and two itineraries
    that are one trip would split its dates, its fees and its dive count in
    two. Worse, it can be silent: those two names slugify to one id, and a
    dataset keyed by id keeps whichever was built last.

    Deliberately the same `padi_key` the fee book and the trip book join on,
    not a new comparison. It exists because PADI does not spell our titles, it
    has been read against the whole fleet, and a second rule for the same
    question is a second rule to keep in step. Where it does not match, PADI's
    name stands as written: an unrecognised trip is a trip we do not have, and
    guessing it onto the nearest one we do is how a St John's week gets badged
    with a reef 600 km away.
    """
    from .scrape.padi_com import PadiComAdapter

    made: list[dict[str, Any]] = []
    unparsed: list[str] = []
    for key, sailing in sorted(sailing_book.items()):
        slug, start = sailing.get("boat"), sailing.get("start")
        if not slug or not start or not sailing.get("end"):
            continue
        if (slug, start) in known:
            continue
        if season and not (season[0] <= date.fromisoformat(start) <= season[1]):
            continue
        if not sailing.get("price") or not sailing.get("currency"):
            continue
        title = sailing.get("itinerary") or ""
        split = PadiComAdapter.split_title(title)
        if not split:
            unparsed.append(f"{key}: PADI title does not parse ({title!r})")
            continue
        url = (f"https://travel.padi.com/liveaboard/"
               f"{sailing.get('country', 'egypt')}/{sailing.get('slug', slug)}/")
        name = named.get(padi_key(slug, split[0]), split[0])
        made.append({
            "id": f"{slug}-{start}-padi",
            "boat_slug": slug,
            "start": start,
            "end": sailing["end"],
            "name": name,
            "price": {"amount": sailing["price"], "currency": sailing["currency"]},
            "booking_url": url,
            # PADI states berths left rather than a schema.org token. Zero is
            # sold out and a positive count is in stock -- that is what the
            # field means, not an inference from it -- and where PADI omits it
            # entirely the row says nothing, as a silent source should.
            "availability": (None if sailing.get("availability") is None
                             else "SoldOut" if sailing["availability"] == 0
                             else "InStock"),
            "padi_only": True,
            # ...but "the other seller does not list this" is a claim about a
            # page somebody read, and on a vessel the barren list held back
            # nobody read one. `data/barren.json` skips a boat found selling
            # nothing for a week at a time to save the requests, and PADI sells
            # 87 season sailings on four of those -- Bella 2, Bella 3, Eriny
            # and Blue Pearl -- every one of which the page was calling
            # "liveaboard.com does not list this sailing" on the strength of a
            # run that chose not to look.
            #
            # That is the precise distinction the barren list was built to
            # preserve, arriving one layer further down: `discover` keeps it
            # through `not_looked_at`, and `promote` lost it because a boat
            # with no candidate departures is indistinguishable from a boat
            # nobody asked about. It costs one key, and it is the same shape as
            # `fees_known` -- no fee lines means nobody looked, not that there
            # are none.
            **({"not_asked": True} if slug in not_asked else {}),
            "provenance": {
                "kind": SourceKind.SCRAPED.value,
                "source_id": "padi.com",
                "retrieved": retrieved,
                "url": url,
            },
        })
    return made, unparsed


def promote(
    candidate: dict[str, Any],
    *,
    season: tuple[date, date] | None = None,
    fx: dict[str, Any] | None = None,
    notes: str | None = None,
    fees: dict[str, Any] | None = None,
    facts: dict[str, Any] | None = None,
    trips: dict[str, Any] | None = None,
    padi: dict[str, Any] | None = None,
    padi_departures: dict[str, Any] | None = None,
    cabins: dict[str, Any] | None = None,
    deals: dict[str, Any] | None = None,
    sales: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a dataset payload from a scrape candidate.

    Departures are grouped into itineraries by (vessel, trip name): the same
    boat sells several routes through a season, and lumping them together would
    average away the difference between a wreck week and a shark week.
    """
    scraped_boats = {i.get("id"): i for i in candidate.get("itineraries", []) if i.get("id")}

    # Fees come from a separate, slower browser-driven run because the source
    # renders them client-side. They are keyed by vessel and reused across
    # every itinerary that vessel sells, which is what the operator does too:
    # the extras do not change with the month.
    fee_book: dict[str, list[dict[str, Any]]] = {}
    # The same run reads the vessel's specification table and diving amenities,
    # which are structured and were previously being worked around: the guest
    # count was mined out of marketing prose and missed half the fleet, and
    # nitrox inclusion was not read at all.
    spec_book: dict[str, dict[str, Any]] = {}
    # And the company the page names as the vessel's brand, which is the only
    # statement of it for a boat with no departures of its own. See where it is
    # consumed, below.
    page_operator: dict[str, str] = {}
    if fees:
        for slug, entry in (fees.get("vessels") or {}).items():
            if entry.get("fees"):
                fee_book[slug] = entry["fees"]
            if entry.get("specs"):
                spec_book[slug] = entry["specs"]
            if entry.get("operator"):
                page_operator[slug] = entry["operator"]

    # What the operator says about *one trip*: the reefs it visits, how many
    # dives it fits in, how many people are aboard and who it will take. Read
    # from the itinerary fragment by ``tools/fetch_itineraries.py`` and merged
    # here the way the fee book is, because it is a separate crawl with its own
    # cadence -- a trip's reefs do not change from night to night.
    #
    # Keyed on vessel plus trip name, which is what an itinerary is here.
    trip_book: dict[str, dict[str, Any]] = {}
    for trip in ((trips or {}).get("trips") or {}).values():
        if trip.get("boat") and trip.get("name"):
            trip_book[itinerary_key(trip["boat"], trip["name"])] = trip

    # What PADI states about the same trip: a coded certification requirement
    # and a dive count the operator wrote down. Merged the way the fee book and
    # the itinerary book are, on the same key, and only ever as a fallback --
    # liveaboard.com is where the prices come from, so where it states a bar or
    # a count that is the answer, and PADI fills the silences.
    padi_book: dict[str, dict[str, Any]] = {}
    for record in ((padi or {}).get("trips") or {}).values():
        if record.get("boat") and record.get("name"):
            padi_book[padi_key(record["boat"], record["name"])] = record

    # What PADI says about a vessel rather than about one of its trips: its
    # name and the fleet it belongs to.
    #
    # Read for every boat and used for almost none. A vessel liveaboard.com
    # also sells takes its name and operator from there, because those are the
    # strings the rest of the dataset is keyed and sorted on, and having one
    # source of them is the point. It is the boats PADI sells alone that have
    # no other name to take.
    padi_vessels: dict[str, dict[str, Any]] = {
        boat: record
        for boat, record in ((padi or {}).get("vessels") or {}).items()
    }

    # The same sailing, as PADI sells it. Keyed on vessel and day -- an exact
    # key, unlike the itinerary join, because a date has no spelling.
    #
    # It fills a field on a departure that already exists, and -- since
    # `_padi_only_departures` -- creates a row where the second seller sells a
    # date the first does not. That reverses this comment's own earlier rule
    # that the row count was the candidate's, and it was reversed on evidence:
    # 601 of the 654 PADI sailings inside the season land on a row we already
    # had, and the other 53 are real, bookable trips the page was silent about.
    # Blue Storm and Blue Seas are near-complete weekly seasons on PADI that
    # liveaboard.com does not sell at all -- 29 of the 53 between them -- so
    # "one row per sailing" was quietly meaning "one row per sailing
    # liveaboard.com happens to list". A trip nobody looked at is not a trip
    # that does not exist; that rule is why `carry_unread` and the barren skip
    # list exist, and it applies to a second seller exactly as it applies to a
    # page that failed to load.
    sailing_book: dict[str, dict[str, Any]] = {
        key: record
        for key, record in ((padi_departures or {}).get("departures") or {}).items()
    }

    # What each sailing costs cabin by cabin, and how many berths are left at
    # each rung. Read from the booking page by ``tools/fetch_cabins.py``, which
    # is its own nightly pass because the counts are the most perishable thing
    # in the dataset -- true when read, stale by morning.
    #
    # Keyed on vessel and day like the PADI join above rather than on the tour
    # id it was fetched with: a departure knows its boat and its date, and
    # putting the tour id on the departure to join with would be a second key
    # doing the first one's job. Unique across all 864 records.
    cabin_book: dict[str, dict[str, Any]] = {}
    cabin_read = (cabins or {}).get("collected") or ""
    # The second seller's berth counts are read by their own crawl on their own
    # day. Kept apart from `cabin_read` because they are, and because a count's
    # date is the whole of what makes it a claim rather than a fact.
    padi_read = (padi_departures or {}).get("collected") or ""
    for record in ((cabins or {}).get("departures") or {}).values():
        if record.get("boat") and record.get("start"):
            cabin_book[berth_key(record["boat"], record["start"])] = record

    # Cabin names pooled across the whole dataset rather than repeated per
    # sailing: 2,982 cabins share 157 names, and a boat's rooms are called the
    # same thing on every week it sells. Halves what the ladder costs the page.
    cabin_names: dict[str, int] = {}
    fx_table = _fx_table(fx)

    # A vessel's guest count, as its own trips state it. Fallback only: the
    # specification table's "Max guests" is the hull's number and this is one
    # sailing's, so it is used where the table is missing the row. Taken as the
    # most common answer across that boat's trips rather than whichever trip
    # sorts first, so the figure does not depend on the order of the file.
    trip_guests: dict[str, int] = {}
    stated_guests: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for trip in trip_book.values():
        if trip.get("guests"):
            stated_guests[trip["boat"]][int(trip["guests"])] += 1
    for boat_slug, counts in stated_guests.items():
        trip_guests[boat_slug] = max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0]

    # Figures read off a vessel page by hand, for the boats whose extras block
    # names a charge without a number. They win per fee code: a page that says
    # "Rental Gear EUR 135" is a better answer than the same page's summary
    # line saying "Rental Gear" and stopping, and both are the same source.
    #
    # Merged per code rather than wholesale, so a vessel covered here keeps
    # every scraped fee this file does not mention.
    hand: dict[str, dict[str, Any]] = (facts or {}).get("vessels") or {}
    hand_read_on = (facts or {}).get("collected") or ""
    superseded: list[str] = []
    for slug, entry in hand.items():
        if not entry.get("fees"):
            continue
        merged = {f["code"]: f for f in fee_book.get(slug, [])}
        # The scrape's own date, per vessel, falling back to the book's.
        scraped_on = (
            (fees or {}).get("vessels", {}).get(slug, {}).get("collected")
            or (fees or {}).get("scraped_at")
            or ""
        )
        for fee in entry["fees"]:
            existing = merged.get(fee["code"])
            # A hand-read figure fills a gap; it does not outlive the scrape
            # that replaced it. Once the browser run reads a real price for a
            # code, a typed-in number collected earlier is last month's answer
            # winning over this week's, and nothing would ever say so.
            if (
                existing is not None
                and existing.get("amount") is not None
                and scraped_on
                and hand_read_on
                and scraped_on > hand_read_on
            ):
                superseded.append(f"{slug}/{fee['code']}")
                continue
            merged[fee["code"]] = fee
        fee_book[slug] = list(merged.values())

    # "Free Nitrox" in the vessel's diving amenities is the operator saying it
    # does not charge for fills. Note what it does *not* do: overwrite a stated
    # nitrox price. Where a vessel both ticks the box and quotes a figure, the
    # figure wins -- turning a stated cost into "free" is the one direction of
    # error this site must never make, and a marketing tick is weaker evidence
    # than a number the operator typed.
    #
    # It no longer overwrites the *disclosure* either. The `Included:` block now
    # reaches the fee book and says the same thing in the same words the prices
    # are quoted in, on 49 vessels, so re-labelling those lines "Vessel lists
    # nitrox as free" would replace a price disclosure with an amenity tick --
    # the weaker of the two claims, and the note a reader would then see.
    for slug, spec in spec_book.items():
        if not spec.get("nitrox_free"):
            continue
        existing = {f["code"]: f for f in fee_book.get(slug, [])}
        nitrox = existing.get(FeeCode.NITROX.value)
        if nitrox is not None and (
            nitrox.get("amount") is not None or nitrox.get("included")
        ):
            continue
        existing[FeeCode.NITROX.value] = {
            "code": FeeCode.NITROX.value,
            "tier": FeeTier.CONDITIONAL.value,
            "included": True,
            "amount": None,
            "basis": FeeBasis.PER_TRIP.value,
            "note": "Vessel lists nitrox as free",
            "provenance": {
                "kind": SourceKind.SCRAPED.value,
                "source_id": "liveaboard.com",
                "retrieved": (fees or {}).get("scraped_at") or date.today().isoformat(),
            },
        }
        fee_book[slug] = list(existing.values())

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    skipped: list[str] = []

    for departure in candidate.get("departures", []):
        slug = departure.get("boat_slug")
        if not slug:
            skipped.append(f"{departure.get('id')}: no boat")
            continue
        nights = _nights(departure["start"], departure["end"])
        if nights is None:
            skipped.append(f"{departure.get('id')}: implausible dates")
            continue
        if season and not (season[0] <= date.fromisoformat(departure["start"]) <= season[1]):
            continue
        name, promotion, _ = _split_title(departure.get("name") or "Unnamed itinerary")
        grouped[(slug, name or "Unnamed itinerary")].append(
            {**departure, "nights": nights, "promotion": promotion}
        )

    # The second seller's own sailings, on dates the first does not list.
    #
    # Injected here rather than merged later so they are grouped into
    # itineraries by the same rule as everything else, and injected *after* the
    # candidate so `known` is the full set of first-source rows: a PADI sailing
    # is only ever added where nothing already stands on that (boat, date).
    known = {
        (d["boat_slug"], d["start"])
        for group in grouped.values() for d in group if d.get("boat_slug")
    }
    # Only where the key names exactly one of our trips. Two of a boat's
    # itineraries can share it -- Blue Horizon sells *Rocky, Zabargad & St.
    # Johns* from two harbours, and `fold_ports` is what makes those one key --
    # and a dict would silently keep whichever was built last, filing PADI's
    # sailing under a port it may not sail from. Two sailings differing only by
    # port are two trips here; where the lookup cannot say which, PADI's own
    # name stands and the trip lands on its own row rather than the wrong one.
    by_key: dict[str, set[str]] = defaultdict(set)
    for slug, name in grouped:
        by_key[padi_key(slug, name)].add(name)

    padi_only, unparsed = _padi_only_departures(
        sailing_book, known,
        {key: next(iter(names)) for key, names in by_key.items() if len(names) == 1},
        season=season,
        retrieved=(padi_departures or {}).get("collected") or "",
        not_asked=frozenset(candidate.get("not_asked") or ()),
    )
    skipped.extend(unparsed)
    for departure in padi_only:
        nights = _nights(departure["start"], departure["end"])
        if nights is None:
            skipped.append(f"{departure['id']}: implausible dates")
            continue
        name, promotion, _ = _split_title(departure["name"])
        grouped[(departure["boat_slug"], name or departure["name"])].append(
            {**departure, "nights": nights, "promotion": promotion}
        )

    # Who runs each boat, from the organizer its own departures name.
    #
    # Resolved per vessel rather than per departure because that is how the
    # source states it: across 878 archived events no boat's departures ever
    # disagreed about their operator, and one boat under two companies would be
    # a fact worth noticing rather than averaging. A disagreement therefore
    # warns and takes the most common answer.
    operators: dict[str, dict[str, str]] = {}
    boat_operator: dict[str, str] = {}
    for (slug, _), group in grouped.items():
        stated = [d["operator"] for d in group if d.get("operator")]
        if not stated:
            continue
        counts: dict[str, int] = defaultdict(int)
        for value in stated:
            counts[value] += 1
        if len(counts) > 1:
            listed = ", ".join(f"{n}x {v!r}" for v, n in sorted(counts.items()))
            skipped.append(f"{slug}: departures name more than one operator ({listed})")
        winner = max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0]
        record = operator_record(winner)
        operators.setdefault(record["id"], record)
        boat_operator[slug] = record["id"]

    # The fleet PADI files a vessel under, for the boats whose departures name
    # no operator because the first source has none of them. Second, never a
    # tie-breaker: where our own departures state a company that is the answer.
    #
    # Taken verbatim, and deliberately not folded onto a company we already
    # hold. The temptation was MY Blue Pearl: PADI files it in "BLUE PLANET
    # Fleet" and so files MY Blue, which is our Blue, whose own departures say
    # "Blue Planet Liveaboards" -- so an OPERATOR_ALIASES entry made the two one
    # operator and tidied a duplicate off the page. It also asserted, on nothing
    # but a fleet label, that a boat we know almost nothing about is run by a
    # company our own source never connected it to. A fleet on a booking site is
    # not established to be the operating company, and these are two different
    # hulls -- 24 guests at 43 m against 20 at 36 m. Two operator rows that may
    # be one company is a cosmetic cost; naming the wrong company is the kind of
    # claim this site exists to catch other people making.
    # First, though: the vessel page's own `Product.brand.name`, which the fee
    # run reads for every boat it visits. It beats PADI's fleet label outright
    # and is not a judgement call -- it is the *same source* every other
    # operator on this page comes from, naming the company for a hull whose
    # lack of departures is the only reason it had no `Event.organizer`.
    #
    # It is what settles MY Blue Pearl. PADI shelves it and MY Blue under one
    # "BLUE PLANET Fleet", and folding the two on that alone asserted a company
    # for a hull our own source connected to nobody -- so an OPERATOR_ALIASES
    # entry was written and then removed, correctly. Blue Pearl's own page
    # says `"brand": {"name": "Blue Planet Liveaboards"}`, which is Blue's
    # operator stated outright rather than inferred from a shelf. The two rows
    # become one because the evidence arrived, not because the duplicate was
    # untidy.
    #
    # It also ends the shouting without anybody deciding how a company spells
    # itself: `BELLA LIVEABOARDS` is PADI's rendering, `Bella Liveaboard` is
    # this field's, and preferring the second is taking a different source's
    # own words rather than editing the first's.
    for slug, stated in sorted(page_operator.items()):
        if slug in boat_operator or not stated:
            continue
        record = operator_record(_collapsed(stated) or "")
        operators.setdefault(record["id"], record)
        boat_operator[slug] = record["id"]

    for slug, vessel in sorted(padi_vessels.items()):
        if slug in boat_operator or not vessel.get("operator"):
            continue
        record = operator_record(_collapsed(vessel["operator"]) or "")
        operators.setdefault(record["id"], record)
        boat_operator[slug] = record["id"]

    # Our boat id -> PADI's slug, for the provenance URL. The alias map is the
    # only place that pairing is recorded, and it is recorded by hand.
    padi_slug_for: dict[str, str] = {
        boat: (vessel.get("slug") or "") for boat, vessel in padi_vessels.items()
    }
    padi_slug_for.update({
        record["boat"]: record.get("slug", "")
        for record in sailing_book.values() if record.get("boat")
    })

    boats: dict[str, dict[str, Any]] = {}
    itineraries: list[dict[str, Any]] = []
    departures: list[dict[str, Any]] = []
    # Ladders refused for contradicting the row above them. Reported, never
    # silent: each one is a booking page this pipeline read and then declined
    # to publish, and the fix is a fresh `cabins.yml` rather than anything here.
    stale_ladders: list[str] = []

    for (slug, name), group in sorted(grouped.items()):
        source = scraped_boats.get(slug, {})
        # PADI's name only where the first source has none, which is the 22
        # vessels it does not sell. Falling through to a title-cased slug is
        # the last resort it has always been, and it now means something worse
        # than it used to: a boat published under a name this code invented
        # rather than one anybody wrote.
        boat_name = (source.get("boat") or source.get("name")
                     or _collapsed((padi_vessels.get(slug) or {}).get("name"))
                     or slug.replace("-", " ").title())
        # Guests belong to the vessel, not the sailing: the same boat carries
        # the same number of people whichever week you book.
        boats.setdefault(
            slug,
            {
                "id": slug,
                "name": boat_name,
                "operator_id": boat_operator.get(slug, UNKNOWN_OPERATOR["id"]),
                # The specification table first: it is the operator stating a
                # number in a field labelled "Max guests", which beats both a
                # hand-typed figure and a regex over the marketing copy. The
                # prose match stays as the fallback for vessels whose table is
                # missing the row.
                "guests": (spec_book.get(slug, {}).get("guests")
                           or hand.get(slug, {}).get("guests")
                           or trip_guests.get(slug)
                           or _guests(source.get("summary"))),
                "cabins": spec_book.get(slug, {}).get("cabins"),
            },
        )

        itinerary_id = f"{slug}--{slugify(name)}"[:96]
        nights = _most_common(d["nights"] for d in group)
        trip = trip_book.get(itinerary_key(slug, name), {})
        padi_trip = padi_book.get(padi_key(slug, name), {})

        # The operator's own list of reefs for this trip, when it has been
        # read. It beats the title outright rather than being merged with it:
        # a title is branding on 23 itineraries and actively wrong on some --
        # a St John's week matched two of BDE's three reefs and was badged
        # accordingly -- so unioning the two would reimport exactly the error
        # this source exists to remove. The title stays as the fallback, so a
        # trip the fetcher has not reached keeps the sites it already had.
        #
        # Folded here, from the operator's own words, rather than read out of
        # the book's `dive_sites`. That field is the same fold done by the
        # fetcher at crawl time, which made it a cache of whatever SITE_HINTS
        # knew that day: teaching the parser four Sinai reefs recovered 86
        # mentions in the parser and changed nothing in the dataset, because
        # promote was reading yesterday's answer. Re-crawling 315 trips to
        # re-read words already in the repository is the same slow and rude
        # thing `reparse_candidate.py` exists to avoid. `dive_sites` stays as
        # the fallback for a book written before regions were kept.
        # One region at a time, for the reason `_sites_from_prose` explains:
        # `normalise` eats any separator, so matching the joined string lets a
        # site be assembled across two entries that neither one names.
        # The description, then the operator's region list, then the title.
        # The regions are last-resort only, never merged: they are what this
        # ordering exists to stop trusting, but a trip whose description names
        # no reef at all -- "Famous Five", "Get Wrecked" -- would otherwise
        # publish an empty cell where the operator did say something.
        # And, last of all, what the *second* seller says about the same trip.
        # Last because it is the least structured thing here: PADI publishes a
        # blurb and a day plan where liveaboard.com publishes headed sections,
        # and against the 180 trips both describe, PADI's words add 173 reef
        # mentions ours does not -- among them Elphinstone on a Brothers and
        # Safaga week, off a sentence saying the two "are quite distant from
        # one another". Merged in, that is the BDE-badging failure this project
        # already removed once.
        #
        # It only ever answers where everything else is silent, and there it is
        # the difference between a row the filter can reach and one it cannot:
        # 19 itineraries and 47 rows have no site at all, and PADI describes 16
        # of them. The other three name no reef in any field -- "Best Of
        # Hurghada", "Specialty Photography Safari" -- and stay blank, which is
        # right. A trip whose sites nobody states has none to show.
        sites = (_sites_from_description(trip)
                 or _sites_from_regions(trip.get("regions") or [])
                 or _sites_from_name(name)
                 or list(padi_trip.get("dive_sites") or []))

        # The title's port pair beats the Event location, which is the country.
        _, _, titled_ports = _split_title(name)
        located = [d.get("location") for d in group if d.get("location")]
        port_from, port_to = titled_ports or (
            (located[0], located[0]) if located else ("Unknown", "Unknown")
        )
        port_from, port_to = _port(port_from), _port(port_to)

        # And PADI states its harbours outright, in two fields, on 447 of 447
        # itineraries -- where every port on this page is otherwise parsed out
        # of a trip title. A statement beats a parse of the same source's own
        # title, which is not a judgement call; and a statement beats nothing
        # at all, wherever the title named no harbour this code could read.
        #
        # Nothing else. liveaboard.com's title stays authoritative for a
        # liveaboard.com trip, the way our fee book beats PADI's where both
        # exist -- the second source is a check here, not a replacement, and
        # two independent readings agreeing is worth more than one reading
        # nobody can check. They do agree: **207 of 207** trips where both
        # speak, with no contradiction anywhere.
        #
        # So this changes no port today, deliberately. What it buys is that the
        # next abbreviation answers itself instead of waiting for somebody to
        # notice a filter chip that is not a place -- which is how "(HRG - PRG)"
        # was found, by hand, after it had shipped.
        stated_from = _port(padi_trip.get("port_from"))
        stated_to = _port(padi_trip.get("port_to"))
        if stated_from != "Unknown" and stated_to != "Unknown" and (
            all(d.get("padi_only") for d in group)
            or "Unknown" in (port_from, port_to)
        ):
            port_from, port_to = stated_from, stated_to

        # The second seller's own required extras, beside ours and never mixed
        # into them. Written only where PADI states at least one charge or
        # states a complete bill of none, so a trip PADI has not been read for
        # carries no key rather than an empty list that reads as "no fees".
        #
        # Resolved before the itinerary is built rather than bolted on after,
        # because for a vessel liveaboard.com does not sell this *is* the
        # itinerary's fee book -- see "fees" below.
        padi_fees = _padi_fees(padi_trip)
        if padi_fees is not None:
            retrieved = (padi or {}).get("collected") or ""
            for line in padi_fees["lines"]:
                line["provenance"]["retrieved"] = retrieved
                line["provenance"]["url"] = (
                    f"https://travel.padi.com/liveaboard/egypt/"
                    f"{padi_slug_for.get(slug, slug)}/"
                )

        itineraries.append(
            {
                "id": itinerary_id,
                "name": name,
                # The name minus its port suffix. The full name stays: two
                # trips differing only by port are different trips, and the
                # itinerary id is built from it.
                "title": _display_title(name),
                "operator_id": boat_operator.get(slug, UNKNOWN_OPERATOR["id"]),
                "boat_id": slug,
                "nights": nights,
                # Zero means the operator does not publish one, and the page
                # says so rather than dividing by a number nobody stated.
                #
                # The itinerary fragment states a count for *this trip*, which
                # is what the column has always wanted: the vessel-level
                # figures behind ``_dives`` are a standard week's, and had to
                # be withheld from every other trip length that boat sells to
                # stay honest. A per-trip figure needs no such guard, so it is
                # taken as stated and the vessel figure remains the fallback.
                # PADI's count comes last and only where nothing of ours
                # answers. It cannot outrank what we hold: it reads as a
                # per-trip figure and on some boats is not one -- every All Star
                # Ghani itinerary says 16 where ours say 17, 19, 20 and 21 --
                # and a number less differentiated than ours cannot improve a
                # column ours already fills. Of the 142 trips where both speak,
                # 113 disagree and PADI is the lower one on 90 of those.
                #
                # But the alternative to it is nothing. 69 published
                # itineraries state no dive count from any source of ours and
                # PADI states one for every one of them, and 43 are on the
                # vessels PADI alone sells berths on -- boats liveaboard.com
                # lists no departure for, so `fetch_itineraries.py` has no tour
                # id to ask about and never will. Bella 2's mini-safari is the
                # case: PADI says 9 dives over its three nights and the column
                # said "not stated". Its low end, as everywhere here, so price
                # per dive stays a ceiling.
                "dives": trip.get("dives") or _dives(
                    hand.get(slug, {}).get("dives") or source.get("dives"),
                    nights=nights,
                    for_nights=hand.get(slug, {}).get("dives_for_nights"),
                ) or int(padi_trip.get("dives") or 0),
                "port_from": port_from,
                "port_to": port_to,
                # Where the trip goes: the operator's own "Key regions" list
                # when the fragment has been read, otherwise the trip title,
                # which usually names the reefs. This is what the page filters
                # on -- there is no route label over the top of it any more,
                # because a name for a set of sites could be wrong about a
                # trip in a way the sites themselves cannot.
                "dive_sites": sites,
                # Only when nothing names a reef at all, so the column says
                # something true rather than sitting empty.
                "region": None if sites else _region_from_name(name),
                "summary": source.get("summary"),
                "source_url": source.get("source_url"),
                # Empty when the fee run has not covered this vessel. The
                # renderer shows that as unknown, never as zero.
                #
                # PADI's own book last, and only for the 22 vessels the fee run
                # can never cover because liveaboard.com does not sell them.
                # This is a fallback where ours is absent, not a merge: the two
                # sources disclose at different resolutions -- one figure per
                # vessel against one per itinerary -- and adding a line from
                # each would build a bill neither seller quotes. Where our book
                # exists it wins outright, and PADI's stays beside it under its
                # own name as `padi_fees`, which is what the page compares.
                #
                # Without this the ten PADI-only boats with sailings in the
                # season would publish 166 berth prices and not one total, on a
                # site whose whole subject is the difference between the two.
                "fees": (fee_book.get(slug)
                         or source.get("fees")
                         or (padi_fees or {}).get("lines")
                         or []),
            }
        )

        # Written only when the operator has actually stated one, rather than
        # as 314 nulls. A key appearing in a dataset diff then means somebody
        # read a safety requirement, which is the only reason to look at it.
        # An absent key loads as the default bar, which asks for nothing --
        # and that is the safe way round: an unread trip must not carry a
        # requirement nobody stated.
        # The stricter of the two, never the first to answer. Both are operators'
        # claims about a safety gate, and where they disagree, showing the lower
        # one publishes a bar softer than somebody stated -- the one direction
        # this project does not go. See _strictest.
        bar = _strictest(_requirements(trip), _padi_requirements(padi_trip))
        if bar:
            itineraries[-1]["requirements"] = bar

        if padi_fees is not None:
            itineraries[-1]["padi_fees"] = padi_fees["lines"]
            itineraries[-1]["padi_fees_complete"] = padi_fees["complete"]

        # Whether this trip's own fee rows came from PADI rather than from the
        # vessel panel every other itinerary uses. Written only where true, so
        # it marks the 22 boats liveaboard.com does not sell rather than
        # shipping a false on 341 itineraries. The page needs it because the
        # sentence it prints under the table names a source, and naming the
        # wrong one is the failure this project reports in other people.
        if (itineraries[-1]["fees"]
                and padi_fees is not None
                and itineraries[-1]["fees"] is padi_fees["lines"]):
            itineraries[-1]["padi_sourced_fees"] = True

        for item in group:
            entry = {
                "id": item["id"],
                "itinerary_id": itinerary_id,
                "start": item["start"],
                "end": item["end"],
                "price": item["price"],
                "booking_url": item.get("booking_url"),
                "availability": _availability(item.get("availability")),
                "provenance": item["provenance"],
            }
            # Which sellers list this sailing at all. Written only where the
            # answer is "not the one every other row comes from", so the field
            # ships on 53 departures rather than 945: a key written per
            # departure is a key written once per departure, and this page is
            # one file.
            if item.get("padi_only"):
                entry["padi_only"] = True
                # A weaker claim than the one beside it, and the honest one:
                # PADI is the only seller *this run asked*. See
                # `_padi_only_departures`.
                if item.get("not_asked"):
                    entry["not_asked"] = True
            # Carried on the departure, not the itinerary: the operator
            # discounts specific dates, and the price scraped is already the
            # discounted one.
            #
            # Recorded, not rendered, and the reason has changed. It used to be
            # that "20% Off" was a claim about a list price we had never seen,
            # so repeating it would put an uncheckable number on a site whose
            # whole argument is that prices should be checked. We have seen it
            # since: the booking page strikes the list price through beside
            # every cabin, and `entry["sale"]` below is built from that. The
            # banner turns out to be exactly right — 241 of 241 sailings that
            # carry one state the percentage the ladder works out to.
            #
            # It stays unrendered anyway, now on the opposite ground: it is the
            # weaker of two agreeing sources. The ladder carries the money as
            # well as the percentage and catches 22 discounted sailings that
            # carry no banner at all, so the banner would add a second, coarser
            # answer to a question already answered. It remains in the dataset
            # as the corroboration, and to explain why two departures of one
            # trip cost different amounts.
            if item.get("promotion"):
                entry["promotion"] = item["promotion"]

            # PADI's price for this exact sailing, in the currency its vessel
            # page states. Left absent rather than zeroed where PADI does not
            # sell the date -- a berth nobody offered has no price, and a zero
            # would read as free.
            #
            # Never on a row PADI is the only seller of: its price is already
            # this row's price, and repeating it in the second seller's field
            # would print a comparison of PADI against itself -- "both sellers,
            # same price" on a sailing one of them does not offer.
            sailing = (None if item.get("padi_only")
                       else sailing_book.get(f"{slug}::{item['start']}"))
            if sailing and sailing.get("price") and sailing.get("currency"):
                entry["padi_price"] = {
                    "amount": sailing["price"],
                    "currency": sailing["currency"],
                }
                entry["padi_provenance"] = {
                    "kind": "scraped",
                    "source_id": "padi.com",
                    "retrieved": (padi_departures or {}).get("collected") or "",
                    "url": f"https://travel.padi.com/liveaboard/"
                           f"{sailing.get('country', 'egypt')}/"
                           f"{padi_slug_for.get(slug, slug)}/",
                }

            # What is left on this sailing and at what price, per seller.
            # Absent where the booking page could not be read -- 25 of 889 --
            # rather than written as a sailing with no cabins, which is the
            # same distinction the crawl draws between an empty page and an
            # unread one.
            ladder = cabin_book.get(berth_key(slug, item["start"]))
            berths = _berth_blocks(
                ladder, sailing_book.get(f"{slug}::{item['start']}"), cabin_names, fx_table
            )
            # A ladder whose bottom rung is nowhere near the price above it is
            # not this sailing's any more. Dropped and named rather than
            # published; see `_drop_stale_ladder`.
            advertised = _to_display(
                float(item["price"]["amount"]), item["price"]["currency"], fx_table
            )
            berths, outdated = _drop_stale_ladder(berths, advertised)
            if outdated is not None:
                stale_ladders.append(
                    f"{slug} {item['start']}: row {advertised}, ladder starts at {outdated}"
                )
            if berths:
                entry["berths"] = berths

            # Whether this berth is marked down, and by whom. Read from the
            # struck-through list price beside each cabin and from PADI's
            # `compareAtPrice`, never from the "20% Off:" the operator writes
            # into the trip name -- that banner is stripped before grouping and
            # is a claim, where these are the two figures the same sellers
            # publish beside the price. They corroborate it exactly, on 241 of
            # 241 sailings that carry one, and go further: 22 more sailings are
            # discounted with no banner at all.
            #
            # `sailing_book` rather than `sailing`, deliberately. That variable
            # is blanked on a PADI-only row to keep PADI's price out of the
            # second seller's field, and here PADI *is* the row's own seller.
            sale = _sale_for(
                ladder,
                sailing_book.get(f"{slug}::{item['start']}"),
                "padi.com" if item.get("padi_only") else "liveaboard.com",
                fx_table,
            )
            if sale:
                entry["sale"] = sale
            departures.append(entry)

    _settle_title_case(itineraries)

    # Two itineraries under one id is silent data loss, not a warning.
    # `Dataset.from_dict` keys itineraries by id, so the second simply replaces
    # the first and every departure of the loser is served the winner's reefs,
    # fees and dive count -- a page that is confidently wrong rather than
    # visibly broken. It has happened: *Daedalus & Fury Shoal (Port Ghalib -
    # Port Ghalib)* and PADI's spelling of it without the space slugify to the
    # same string, and the row count stayed right while two trips became one.
    # Promotion is pure and CI compares its output byte for byte, so raising
    # here turns that into a red build at the moment it is introduced.
    clashes = sorted(
        key for key, count in Counter(i["id"] for i in itineraries).items() if count > 1
    )
    if clashes:
        raise ValueError(
            "two itineraries share an id, which would silently discard one: "
            + ", ".join(clashes)
        )

    payload: dict[str, Any] = {
        "schema_version": 1,
        "generated": candidate.get("scraped_at") or date.today().isoformat(),
        "default_currency": "EUR",
        "notes": notes or _notes_for(itineraries, departures),
        "fx": fx or _default_fx(),
        # Only the operators actually named. UNKNOWN_OPERATOR joins the list
        # only when something is filed under it -- carrying an unused "Operator
        # not captured" row would put a company on the page that does not exist.
        "operators": _operators_for(operators, itineraries),
        "boats": sorted(boats.values(), key=lambda b: b["name"]),
        "itineraries": itineraries,
        "departures": departures,
    }
    # Any berth block at all, not just a ladder. PADI publishes a count and no
    # cabins, so a run with its book and no booking pages would otherwise ship
    # blocks whose seller index points into a `sellers` list that was never
    # written -- a number on the page attributed to nobody.
    if cabin_names or any(d.get("berths") for d in departures):
        # The pool the ladder's first field indexes into, in the order names
        # were first seen -- which is promotion order, and promotion is pure,
        # so the same inputs give the same list byte for byte.
        payload["cabin_names"] = [
            name for name, _ in sorted(cabin_names.items(), key=lambda kv: kv[1])
        ]
        payload["sellers"] = list(SELLERS)
        # One date for the whole book, so it is stated once rather than on 864
        # departures. It is the most load-bearing caveat here: a berth count is
        # what the seller claimed when it was read, and stale by morning.
        payload["berths_read"] = cabin_read
        # PADI's counts come from its own crawl on its own day, and the two are
        # not the same day. One date printed over both would date half the
        # panel wrong, on the figure whose whole caveat is when it was true.
        if padi_read:
            payload["padi_berths_read"] = padi_read
        payload["berths_note"] = (
            "departures[].berths is one block per seller: "
            "[seller index into sellers, places left at the advertised price, "
            "[cabins], berths left on the sailing]. Each cabin is [name index "
            "into cabin_names, price per person in the display currency, "
            "places left (0 = full, null = not stated), single-occupancy "
            "surcharge %]. Both counts are totals -- the first across every "
            "room selling at the advertised price, the second across every "
            "room at any price -- and either is null where any room it covers "
            "does not state a count, because one unstated figure makes the sum "
            "unknown rather than partial. The two are different claims and are "
            "never merged: PADI Travel publishes only the second, and it was "
            "measured against liveaboard.com's rather than assumed (77% exact, "
            "88% within two berths, against 22% for the count at the "
            "advertised price). A seller that publishes a count but no ladder "
            "has a null cabin list: 24 places and 24 places at a stated price "
            "are different claims, and only the second is a ladder."
        )
    # What PADI is discounting today, and what moved since the last day the
    # book holds. Its own committed input, diffed here rather than in the
    # browser, so `promote --check` proves the panel on the page is this code's
    # reading of the deals book -- the same relationship every other number on
    # the page has with the file it came from.
    deals_block = _deals_block(deals, padi_vessels, boats, fx_table) or {}

    # And what the *first* seller is discounting, which is a different question
    # answered by a different file. PADI publishes a deals listing: one
    # exemplar sailing per vessel, 13 of them on this fleet. liveaboard.com
    # publishes no such listing at all -- `/liveaboard-deals` is SEO prose --
    # but marks every discounted cabin on every booking page, which the nightly
    # cabin pass already reads. That is 263 sailings on 22 boats, and 9 of
    # those boats appear in no deals listing anywhere.
    #
    # It has a change log of its own now, and by the same rule that gives PADI
    # one: `data/sales.json` keeps a day per reading of the booking pages, so
    # there is a second committed day to diff against. The cabin book itself
    # still keeps only the last reading -- 70,000 lines of cabin names and
    # amenities that never change -- so the sale book is a projection of it
    # onto the three fields a diff needs. Promotion stays pure either way: it
    # reads two committed days and never goes to git for one.
    on_sale = _on_sale_summary(
        departures,
        {i["id"]: i["boat_id"] for i in itineraries},
        boats,
        cabin_read,
    )
    if on_sale:
        deals_block["on_sale"] = on_sale
    # Beside the summary rather than inside it, because the day every sale on
    # the fleet ends is the day there is no summary to hang it off -- and it is
    # also the day the change log is the only thing left worth printing.
    moved = _sales_block(sales, boats)
    if moved:
        deals_block["on_sale_changes"] = moved
    if deals_block:
        payload["deals"] = deals_block

    if stale_ladders:
        payload["stale_ladders"] = sorted(stale_ladders)
    if skipped:
        payload["promotion_skipped"] = skipped
    if superseded:
        # Reported rather than silent: every entry here is a hand-typed figure
        # the scrape has since read for itself, and the honest response is to
        # delete it from data/operator_facts.json rather than leave it as a
        # redundant override that will one day be wrong.
        payload["facts_superseded"] = sorted(superseded)
    return payload


def _operators_for(
    named: dict[str, dict[str, str]], itineraries: list[dict[str, Any]]
) -> list[dict[str, str]]:
    """The operator rows the dataset needs, and no others.

    Every ``operator_id`` an itinerary references must resolve -- ``dataset``
    validates that and refuses to load otherwise -- so the fallback row is
    included exactly when something is filed under it. Carrying it
    unconditionally would put "Operator not captured" on a page where every
    trip has a real company behind it.
    """
    used = {i["operator_id"] for i in itineraries}
    rows = [record for key, record in sorted(named.items()) if key in used]
    if UNKNOWN_OPERATOR["id"] in used:
        rows.append(dict(UNKNOWN_OPERATOR))
    return rows or [dict(UNKNOWN_OPERATOR)]


def _notes_for(
    itineraries: list[dict[str, Any]],
    departures: list[dict[str, Any]] | None = None,
) -> str:
    """Describe what this run actually captured.

    The note used to be a constant reading "Fees are not yet captured, so true
    cost is shown as unknown". It outlived the fee run by a week: the page
    carried a full breakdown for every trip while telling its visitors it had
    none. A site that exists to catch operators describing their prices
    inaccurately cannot describe its own data inaccurately.

    The same rule is why the second source is named here. This sentence is the
    page's own statement of where its numbers come from, and for eleven
    refreshes it named liveaboard.com alone while a "vs PADI" column sat in the
    table and PADI's entry bar decided what 95 trips print. A source a page
    reads and does not admit to is the reverse of the failure this project
    reports in operators, and no less a failure for being an omission.
    """
    total = len(itineraries)
    with_fees = sum(1 for i in itineraries if i["fees"])
    if not total or not with_fees:
        return (
            "Prices scraped from liveaboard.com. Fees are not yet captured, so "
            "true cost is shown as unknown rather than as the advertised price."
        ) + _padi_note(itineraries, departures)
    if with_fees == total:
        return (
            "Prices and fee disclosures scraped from liveaboard.com. True cost "
            "adds every fee the operator lists, including the ones it states "
            "without a price."
        ) + _padi_note(itineraries, departures)
    return (
        f"Prices scraped from liveaboard.com. Fee disclosures captured for "
        f"{with_fees} of {total} itineraries; the rest show true cost as "
        f"unknown rather than as the advertised price."
    ) + _padi_note(itineraries, departures)


def _padi_note(
    itineraries: list[dict[str, Any]],
    departures: list[dict[str, Any]] | None,
) -> str:
    """What the second source contributed, counted rather than claimed.

    Three numbers, because PADI contributes three different things and their
    coverage is not the same: a berth price on the sailings whose date it also
    sells, an entry bar on the trips it also describes, and -- on 53 rows --
    the sailing itself. Naming the source without the counts would let a run
    that read nothing from PADI go on saying it did, which is the failure the
    fee sentence above was written to stop.

    The third count is the one the sentence before it would otherwise
    contradict outright. "Prices scraped from liveaboard.com" stopped being
    true of every row the day promotion began creating them, and a page that
    names the wrong seller for a berth is doing the thing this project exists
    to report.
    """
    priced = sum(1 for d in (departures or []) if d.get("padi_price") is not None)
    only = sum(1 for d in (departures or []) if d.get("padi_only"))
    barred = sum(
        1 for i in itineraries
        if "PADI" in ((i.get("requirements") or {}).get("notes") or "")
    )
    if not priced and not barred and not only:
        return ""
    parts = []
    if priced:
        parts.append(
            f"its own berth price on {priced} of {len(departures or [])} sailings"
        )
    if barred:
        parts.append(f"the entry bar on {barred} of {len(itineraries)} trips")
    note = ""
    if parts:
        note = (
            " PADI Travel is read as a second source for " + " and ".join(parts) +
            "; where the two disagree about the bar the stricter is shown."
        )
    if only:
        note += (
            f" On {only} sailings it is the only seller and the berth price is "
            f"PADI's."
        )
        # Which fee book those rows use is not one answer, and the sentence
        # above would be false for the second kind if it claimed it was. Most
        # are boats liveaboard.com sells on other dates, so the vessel's own
        # panel applies; the rest are boats it publishes no departures for at
        # all, and PADI's per-itinerary book is the only one there is.
        sourced = sum(1 for i in itineraries if i.get("padi_sourced_fees"))
        if sourced:
            note += (
                f" The fees are the vessel's own panel, except on {sourced} "
                f"trips whose boats liveaboard.com does not sell, where they "
                f"are PADI's too."
            )
        else:
            note += " The fees are the vessel's own."
    return note


def _most_common(values) -> int:
    counts: dict[int, int] = defaultdict(int)
    for value in values:
        counts[value] += 1
    return max(counts.items(), key=lambda kv: (kv[1], -kv[0]))[0]


SITE_HINTS = (
    "brothers", "daedalus", "elphinstone", "thistlegorm", "abu nuhas",
    "rocky island", "zabargad", "st johns", "st john's", "fury shoals",
    "sataya", "ras mohammed", "tiran", "salem express", "rosalie moller",
    "gubal", "abu dabab", "samadai", "habili ali", "dangerous reef",
    "gota kebir",
    # Named on titles that previously yielded nothing at all.
    "dahab", "safaga", "elba reef", "turkia", "sataya reef",
    "marsa shouna", "gota abu ramada", "panorama reef", "middle reef",
    "small giftun", "shaab sheer", "umm gamar", "ras disha", "tobia arbaa",
    # Named in the operators' own prose. "Sha'ab el Erg" is where the resident
    # pod is, and is asked for as Dolphin House -- which is an alias below,
    # because the two are one reef.
    "sha'ab el erg",
)
"""Dive-site names operators actually write, in a title or in their own prose.

liveaboard.com does not publish a per-trip site list, but operators name their
routes after the sites — "Brothers, Daedalus & Elphinstone" says exactly where
it goes. Pulling the names back out of the title lets the existing classifier
work unchanged instead of leaving every scraped trip unclassified.

**A hint is a destination; a dive on one is an alias.** Thistlegorm and the
Salem Express are here because a week is sold to reach them and nothing else
on this list contains them. The Giannis D is not, because it is one of four
wrecks on Abu Nuhas, which is here — and a diver who wants it filters on Abu
Nuhas. Reading the prose is what forced the distinction: it names the
individual dives, so without the fold a Brothers week grew five chips
(Brothers, Big Brother, Little Brother, Numidia, Aida) for one place.

"sha'ab" and "shaab" were hints and are gone. It is Arabic for *reef*, so it
matched inside Sha'ab Sheer, Sha'ab Abu Nuhas and Sha'ab el Erg alike and put
a chip reading "reef" on **113 of 315** trips. No title's site list depended on
it.
"""

SITE_ALIASES: dict[str, str] = {
    "deadalus": "daedalus",
    "rocky": "rocky island",
    "st john": "st johns",
    "saint johns": "st johns",
    # Plurals. The match is on whole words, so one spelling misses a hint
    # written as the other. The plural is canonical because it is what the
    # operators mostly write -- 17 names against 12 -- and because the title
    # column and the filter chip have to agree: printing "Fury Shoals" beside
    # a chip reading "fury shoal" is the mismatch this folding exists to end.
    "fury shoal": "fury shoals",
    "brother islands": "brothers",
    "brother island": "brothers",
    # The individual reefs in the Straits of Tiran, and the signature dive at
    # Ras Mohammed. Operators name these on their own per-trip region lists,
    # where nothing here knew the words at all: 86 mentions across 61 trips
    # went into the parser and came out empty, and Topaz's dolphin safari --
    # whose only region is Gordon Reef -- lost its site list entirely.
    #
    # Folded to the area rather than carried as their own chips. A filter is
    # only useful if a diver recognises what is in it, and "Tiran" and "Ras
    # Mohammed" are how these dives are asked for; four more chips naming
    # reefs inside them is precision nobody was looking for. 55 of the 61
    # trips name the area as well, so almost all of this is a fold onto a
    # chip that was already there.
    #
    # `normalise` turns "&" into "and", so one spelling covers "Shark &
    # Yolanda" too.
    "jackson reef": "tiran",
    "gordon reef": "tiran",
    "woodhouse reef": "tiran",
    # Thomas Reef completes the four; no trip names it yet, and the set is
    # only meaningful whole.
    "thomas reef": "tiran",
    "shark and yolanda": "ras mohammed",
    "the brothers": "brothers",
    # One m or two, both spellings are on the listing.
    "ras mohamed": "ras mohammed",
    "ras muhammad": "ras mohammed",
    "ras mohamad": "ras mohammed",
    "shaab abu nuhas": "abu nuhas",
    "ss thistlegorm": "thistlegorm",
    "elba borders": "elba reef",
    # Same fold, forced by the operators' own prose rather than by titles. A
    # day plan names the dive, not the destination -- "Dive 2: Giannis D" --
    # so on 267 trips the prose offered sites the region list did not, and
    # most of them were parts of a place already on the row. Left unfolded, an
    # Abu Nuhas week showed five chips for one reef and a Brothers week five
    # for one island pair, which is the precision nobody asked for that
    # Tiran's reefs were folded away for.
    #
    # The four wrecks on Abu Nuhas.
    "giannis d": "abu nuhas",
    "carnatic": "abu nuhas",
    "chrisoula k": "abu nuhas",
    "kimon m": "abu nuhas",
    # The two islands, and the two wrecks down Big Brother's wall.
    "big brother": "brothers",
    "little brother": "brothers",
    "numidia": "brothers",
    "aida": "brothers",
    # Ulysses lies on Gubal Seghir.
    "ulysses": "gubal",
    # Ras Mohammed's own dives. "Shark & Yolanda" is one dive over two reefs
    # and operators name either half; `normalise` turns "&" into "and", so the
    # spelling above covers the pair.
    "yolanda": "ras mohammed",
    "yolanda reef": "ras mohammed",
    "shark reef": "ras mohammed",
    # Beacon Rock, inside the national park.
    "dunraven": "ras mohammed",
    # Elphinstone is on the list under its bare name; the prose adds "Reef".
    "elphinstone reef": "elphinstone",
    # ---- Read out of the operators' own descriptions, not their titles ----
    #
    # A description names the dive; a key-regions list names the destination.
    # Until these were here, comparing the two measured our ignorance as much
    # as the operator's mistakes: "Daedalus & Fury Shoal" appeared to visit no
    # Fury Shoal because its week is spent at Shaab Claudio, Abu Galawa and
    # Shilineat, and nothing here knew those are Fury Shoal.
    #
    # One spelling variant, and the single most expensive one: the descriptions
    # write Abu Dabbab with two b's and the region lists write Abu Dabab with
    # one, so the same reef read as two different places on 16 trips.
    "abu dabbab": "abu dabab",
    # A typo in the operator's own day plan, on three trips.
    "gota abu ramad": "gota abu ramada",
    # The Fury Shoals, reef by reef.
    "shaab maksur": "fury shoals",
    "shaab claudio": "fury shoals",
    "shaab claudia": "fury shoals",
    "abu galawa": "fury shoals",
    "gotat abu galawa": "fury shoals",
    "shaab hamam": "fury shoals",
    "el malahi": "fury shoals",
    "malahi": "fury shoals",
    "shilineat": "fury shoals",
    "abu fendera": "fury shoals",
    "abu fandira": "fury shoals",
    # St John's, reef by reef.
    "umm aruk": "st johns",
    "cave reef": "st johns",
    "small gota": "st johns",
    "habili gafaar": "st johns",
    # The Straits of Gubal. Shag Rock carries the Kingston, and the Barge and
    # Bluff Point are Small Gubal's two best-known dives.
    "shag rock": "gubal",
    "kingston": "gubal",
    "bluff point": "gubal",
    "small gubal": "gubal",
    "big gubal": "gubal",
    # Ras Mohammed's park, which reaches past the headland: Shaab Mahmoud and
    # the Alternatives are dived on the same day as Shark and Yolanda, and
    # Jolanda is how half the fleet spells Yolanda.
    "shaab mahmoud": "ras mohammed",
    "the alternatives": "ras mohammed",
    "jolanda": "ras mohammed",
    "beacon rock": "ras mohammed",
    # Safaga's own house reef, which its region lists name and its
    # descriptions do not.
    "ras abu soma": "safaga",
    # The Abu Nuhas wrecks again, as the day plans abbreviate them: "Dive 4 at
    # Abu Nuhas - Giannis D" is matched by the full name above, but the prose
    # elsewhere drops the letter.
    "giannis": "abu nuhas",
    "chrisoula": "abu nuhas",
    "kimon": "abu nuhas",
}
# "Dolphin House" is deliberately absent. It is two reefs 400 km apart --
# Sha'ab el Erg off Hurghada and Sha'ab Samadai off Marsa Alam -- and both are
# sold under the name. A southern trip's own prose lists it beside Sataya and
# Fury Shoal, so folding it to the northern reef would have moved nine deep
# south itineraries to Hurghada. A nickname that needs the region to
# disambiguate cannot be resolved by a table that has no region.
"""How operators actually spell a site in a trip title.

Titles are marketing copy, not a gazetteer. Two of four vessels sell
"Deadalus, St. John´s & Elphinstone" — one transposition and one acute accent
— and a third abbreviates Rocky Island to "Rocky". Matching only the correct
spelling dropped Daedalus and St John's from those routes, and since
classification derives from dive sites, it filed a southern shark itinerary as
a single-site trip.

Keys are matched after :func:`~liveaboard.classify.normalise`, so accents and
apostrophes are already folded; only genuine misspellings and short forms
belong here.
"""


REGIONS = (
    (re.compile(r"\bdeep south\b|\bsouthern\b|\bsouth\b", re.I), "southern route"),
    (re.compile(r"\bsinai\b|\btiran\b", re.I), "Sinai and Tiran"),
    (re.compile(r"\bnorthern\b|\bnorth\b", re.I), "northern route"),
    (re.compile(r"\bwreck\w*\b", re.I), "wreck route"),
)
"""What a title says about where it goes when it names no reef.

Fifty-one trips are sold as "North", "Deep South" or "Get Wrecked" and never
name a site. This transcribes the operator's own word rather than classifying
anything -- a title saying "North" is evidence it goes north, and nothing more
is claimed.

The vessel description was the tempting alternative and is unusable: it is the
boat's brochure, listing everywhere it sails all year. Aphrodite's names
St John's, so its "North Wrecks" week would have been tagged with a site
six hundred kilometres from where it goes.
"""


def _region_from_name(name: str) -> str | None:
    """Transcribe the region a site-less title states, or admit it states none."""
    for pattern, label in REGIONS:
        if pattern.search(name):
            return label
    return None


def _sites_from_regions(regions: Sequence[str]) -> list[str]:
    """The operator's curated place list, one entry at a time.

    Exported as its own function so the fetcher matches it exactly rather than
    keeping a second copy of the rule -- the same reason `itinerary_key` is
    shared.
    """
    sites: list[str] = []
    for region in regions:
        sites = _also(sites, _sites_from_name(region))
    return sites


def _sites_from_description(trip: Mapping[str, Any]) -> list[str]:
    """Every reef the operator names in its own description of the trip.

    The whole description -- its lead paragraph, its section headings and its
    day plan -- and **not** the curated "Key regions" list beside it. That list
    is a summary somebody typed once and it is demonstrably wrong: All Star Red
    Sea sells a "North & Brothers" week whose regions name Daedalus, 180 km
    from anywhere its own day plan goes, and an "Ultimate Red Sea" fortnight
    whose regions name St John's while its description lists nine sites and
    omits it. Across 293 trips the regions claim a site the description never
    mentions on 42 of them.

    The description wins because it is what the buyer reads. A diver books on
    the sentences, not on the sidebar, so those sentences are the operator's
    actual claim -- which is the only thing this site ever reports.

    The fragment's fourth heading, "Route", is not read and cannot be: it is a
    single ``<figure>`` holding a map image, with zero characters of text on
    every vessel probed. Recorded in ``docs/sources/liveaboard.com.md``.

    Read one section at a time rather than from the lot joined together.
    Joining and matching once looks equivalent and is not: :func:`normalise`
    reduces every punctuation mark to a space, so a separator disappears into
    the text and a section headed "Ras" followed by one headed "Mohammed"
    would invent a reef neither of them names.
    """
    sites = _sites_from_name(trip.get("intro") or "")
    for section in trip.get("sections") or ():
        sites = _also(sites, _sites_from_name(section.get("heading") or ""))
        # A day says what you dive; a heading names a place; the body of a
        # place section is an essay about it and will mention anywhere.
        # All Star Red Sea describes Daedalus as "Much like the Brothers
        # Islands, Daedalus also sits in open water" -- a comparison, on a
        # trip that goes nowhere near the Brothers. Reading it put the
        # Brothers on that row and on two others.
        if section.get("is_day"):
            sites = _also(sites, _sites_from_name(section.get("text") or ""))
    return sites


def _also(sites: list[str], extra: list[str]) -> list[str]:
    """``sites`` with anything genuinely new appended, order kept.

    Deduplicated on the folded key rather than the string, so "Elphinstone"
    from a region list and "Elphinstone Reef" from a heading stay one chip.
    """
    if not extra:
        return sites
    out = list(sites)
    seen = {normalise(site) for site in out}
    for site in extra:
        key = normalise(site)
        if key not in seen:
            seen.add(key)
            out.append(site)
    return out


def _sites_from_name(name: str) -> list[str]:
    """Recover dive-site names from an itinerary title.

    Both sides are folded through the classifier's own :func:`normalise`, so
    "St. John´s" in a title reaches the same key as "St Johns" in the hints
    rather than missing it over punctuation.
    """
    folded = f" {normalise(name)} "
    found: list[str] = []
    # Keyed on the folded form: SITE_HINTS lists "st johns" and "st john's"
    # separately for readability, and they are one site.
    seen: set[str] = set()

    def add(site: str) -> None:
        key = normalise(site)
        if key not in seen:
            seen.add(key)
            found.append(site)

    for hint in SITE_HINTS:
        if f" {normalise(hint)} " in folded:
            add(hint)
    for alias, canonical in SITE_ALIASES.items():
        if f" {normalise(alias)} " in folded:
            add(canonical)
    return found


def _default_fx() -> dict[str, Any]:
    return {
        "display_currency": "EUR",
        "as_of": date.today().isoformat(),
        # Named a placeholder on purpose: money.PLACEHOLDER_SOURCE matches it,
        # so the page warns instead of passing this off as a looked-up rate.
        # tools/fetch_fx.py replaces the whole table with ECB reference rates;
        # this only applies when that has never run.
        "source": "placeholder — replace with a real rate source",
        "rates": {"USD": 0.92, "GBP": 1.17},
    }
