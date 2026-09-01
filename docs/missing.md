# What is missing

Every hole in the published dataset, counted against `data/egypt-2027.json` as
built on 2026-08-30 (77 boats, 46 operators, 402 itineraries, 1,122 sailings).

**Three of these are closed** — see `docs/plan-missing.md` for what each one
actually bought, including the two joins §2 needed and the one it refused. The
counts below are as first measured; where a section is done it says so.

Grouped by **why** it is empty, because that is what decides whether there is
anything to do about it:

1. modelled and never filled — a field the page cannot draw at all;
2. read and not published — the figure is already in the repository;
3. read, and the source said nothing — the honest holes;
4. nobody looked — a fetch would close it.

A gap is only worth writing down where the site would say something more if it
were closed. Absences the sources have already been probed for are in
*Ruled out* at the foot, so they are not opened again.

## 1. Modelled, never filled

Five fields exist on the dataclasses, load from `from_dict`, and are `None` on
every record. Nothing reads them, so nothing is visibly wrong — which is why
they have survived.

| Field | Filled on | Note |
|---|---|---|
| `Boat.length_m` | 63 / 77 | `data/fees.json` already holds it for **71** vessels. See §2. |
| `Operator.website` | 0 / 46 | Parsed from neither source. `Event.organizer` carries a name and no url. |
| `Departure.spaces_left` | 0 / 1,122 | **Dead.** Superseded by `berths`, which answers the same question per seller. `render.py:162` says so already. It still ships as a column in `export.py`, empty on every row. |
| `Requirements.max_depth_m` | 0 / 402 | Both sources state the entry bar as a level and a logged-dive count; neither states a depth. |
| `Requirements.nitrox_recommended` | 0 / 402 | |
| `Requirements.strong_current` | 0 / 402 | |

`spaces_left` is the one to decide about rather than fill: it is a field whose
job was taken. The other five are unfilled because nobody has read them, and
three of those (`max_depth_m`, `nitrox_recommended`, `strong_current`) have no
candidate source behind them — they were modelled ahead of the data.

## 2. Read and not published

`tools/scrape_fees.py` reads the specification table on every vessel page and
writes it to `data/fees.json` under `specs`. `promote` reads that book for
exactly one key, `nitrox_free`, and drops the rest on the floor:

| `specs` key | Present | Reaches the dataset |
|---|---|---|
| `cabins` | 79 / 79 | yes |
| `guests` | 78 / 79 | yes |
| `nitrox_free`, `nitrox_available` | 79 / 79 | `nitrox_free` only |
| **`length_m`** | 71 / 79 | **no** |
| **`year_built`** | 67 / 79 | **no** — and there is no field on `Boat` to put it in |

Missing `length_m` in the book: Blue, DUNE Longara, DUNE Titan, Golden Dolphin,
Sea Serpent Contessa, Snefro Love, Snefro Pearl, Snefro Target. Missing
`year_built`: those two DUNEs plus Blue Storm, Discovery II, Freedom III, the
three Red Sea Aggressors, Red Sea Blue Force 2, Royal Evolution, Tala, Vita
Xplorer.

Whether either belongs on the page is a separate question — this file only
records that closing them costs no request.

## 3. Vessel identity

- **Operator unknown on 3 boats.** MY Anemone, MY Heaven Saphir and MY
  Independence II sit under the placeholder operator `unknown-operator`
  ("Operator not captured"), carrying **44 in-season sailings** between them.
  All three are `padi_only` with no liveaboard.com vessel panel, and PADI
  states no `fleetTitle` for them either — so neither of the two routes to a
  company name (`Product.brand.name`, then `window.shop.fleetTitle`) answers.
- **Guests missing on 7 boats, cabins on 6.** Grand Sea Explorer, MY Anemone,
  MY Heaven Saphir, MY Independence II, MY Seawolf Dominator, Seawolf Steel,
  and — for guests only — Vita Xplorer. Six of the seven are the same
  panel-less PADI-only vessels; Vita Xplorer has a panel whose table states no
  guest count.
- **Zabargad has no fee panel.** The only vessel in `fees.json`'s `missing`
  list, and on the barren skip list since 2026-08-28. It publishes no
  departures, so nothing of it is on the page — but it is the one hull the fee
  run has never read.

## 4. Per-trip facts

**The dive count.** One itinerary in the season states none from any of the
four sources — **Aphrodite, *North Dolphins*** (7 nights, Hurghada return, 2
sailings) — and **it is not a hole.** Probed 2026-09-01: liveaboard.com prints
`Dives  -` for that trip, the only dash in 317, and its day plan is snorkelling
only ("2–3 guided snorkeling sessions daily", *No Certificate needed*). PADI
sells eleven Aphrodite itineraries and not this one. There is no dive count
because there are no dives. The column reads *not stated*, which claims nobody
answered when the seller did — see `docs/plan-missing.md` §0. Where the number
does exist, it is worth knowing which source answered:

| Source | Itineraries (was, before the discovery pass) |
|---|---|
| the trip's own fragment (`data/itineraries.json`) | **327** (316) |
| PADI, last resort | **61** (69) |
| a vessel-level count read by hand, for that trip length only | **13** (16) |
| nothing | **1** (1) |

**74 itineraries have no per-trip fragment** — 144 sailings; it was 85 and 210
until the discovery pass landed. Everything the fragment carries — dive count,
group size, the stated entry bar, and the operator's own reef list — falls back
to the trip title or to PADI on those rows. What is left splits in two, and
only one half is a gap:

| | Itineraries | |
|---|---|---|
| boats liveaboard.com does not list at all | **41** on 6 | Seawolf Steel 13, MY Heaven Saphir 10, MY Seawolf Dominator 6, MY Independence II 5, MY Anemone 4, Grand Sea Explorer 3. No vessel page exists, so no fetch reaches them: PADI is the only source these trips will ever have. |
| trips the two sellers name differently | **33** on 13 | Blue Seas 10, MY Blue Pearl 6, Blue 5, Blue Storm 4, and nine more with one or two. Eriny's *Sinai Classic* against liveaboard.com's *Sinai Classic One Week*; PADI routes that site lists nowhere. |

**3 itineraries name no reef at all** — no `dive_sites` from any of the four
ordered sources. Amelie Adventures, Marselia Star, Seawolf Steel (one trip
each, 4 sailings). Seawolf Steel's carries a `region` off its title; the other
two carry nothing, and the site filter cannot reach any of them.

**55 itineraries have no summary** (166 sailings) — all on the ten PADI-only
vessels, which have no `Product.description` because they have no
liveaboard.com product.

**16 itineraries state no entry bar** from either seller (25 sailings), so the
column shows the default Open Water rather than an operator's claim: MY Heaven
Saphir 9, Red Sea Aggressor IV 2, and one each on Amelie Adventures, Emperor
Asmaa, Emperor Elite, Grand Sea Explorer, Seawolf Steel.

## 5. Fees

**33 itineraries carry PADI fee rows that do not add up** — PADI named a charge
and stated no figure, so no total may be claimed on its behalf. These are the
actionable ones, because the entries exist and are unread rather than absent:

    Blue Horizon 9 · Blue Melody 8 · All Star Scuba Scene 6 ·
    MY Independence II 5 · DUNE Longara 3 · Red Sea Aggressor IV 2

A further **190 itineraries have no PADI fee rows at all**, which is a
different thing: PADI has not been read for that trip, not PADI stating the
fare covers everything. 179 itineraries have a complete PADI bill.

**Unpriced lines in our own book**, by code and by how many vessels carry at
least one:

    alcohol 52 · snorkel_gear 31 · gratuities 30 · private_guide 17 ·
    extra_dives 15 · gear_rental 12 · course 10 · nitrox_course 10 ·
    naturalist_guide 6 · laundry 6 · land_excursion 5 · airport_transfer 3 ·
    visa 1

`gratuities` is the one that costs something: it is *customary*, so it is
counted by default, and an unpriced line means 30 vessels' counted totals carry
a known cost of unknown size. The rest are *optional* and sit below the line.

**New Sambo is the one vessel neither seller names nitrox for** — not free,
not priced, unknown. The nitrox column has a branch for it.

**`single_supplement` is used by no vessel (0 / 77).** What a cabin to yourself
costs comes from the ladder's per-rung percentage instead, and **66 of 830
ladders state none**: Yachtiano 17, Destiny 16, Queen Sherry 16, Unity 13,
Oceanix 4. The fee code is modelled and no parser writes it.

## 6. Berths, and what is on sale

- **9 sailings have no berth block from any seller** — the booking page
  answered nothing and PADI does not sell them. One each on Amelie Adventures,
  Blue, Golden Dolphin IV, Ocean Lovers, Queen Sherry, Sea Friend, Sunshine,
  Vita Xplorer, Yachtiano.
- **292 sailings state no count at the advertised price.** 225 are `padi_only`,
  where PADI publishes a whole-sailing count and no ladder — that is the source
  answering a different question, not a gap. The other **67 are ours to close**,
  and 35 of them are the three Red Sea Aggressors whose ladders `promote`
  dropped as stale. The fix there is a fresh `cabins.yml`, and nothing in the
  dataset can put them back.
- **Every one of 2,903 cabin rungs states a places-left figure.** No hole here.
- **Sale coverage**: 9 sailings whose ladder was unreadable, 2 whose trip-name
  banner the ladder contradicts, 34 dropped stale. All 45 print as *not on
  sale*, and `deals.coverage` says so on the page.
- **3 unmatched PADI deals** — Belize Aggressor III and IV, Cayman Aggressor IV.
  Correctly unmatched: they sail another ocean. Named rather than dropped, which
  is the rule working.

## 7. Ruled out — not gaps

Established by probe and written up in `docs/sources/`. Listed here so a
coverage count does not read as a to-do.

- **A berth count on a vessel page.** Does not exist; the booking flow's
  `data-allocation` is the only source and is already read.
- **A dive count in the vessel page's Highlights block.** Free marketing prose:
  present on 8 of 12 vessels probed, a count on 2, and both are upper bounds
  ("Up to 20 dives + night dives"). The dataset keeps the low end so price per
  dive stays a ceiling, so an upper bound is the one figure it cannot use.
- **A guest count on PADI's vessel page.** Searched the whole body for every
  numeric form: none. So Vita Xplorer's missing count has no second source.
- **`Requirements` depth, current and nitrox advice.** No candidate field in
  either source.
- **The single gear items beside "Full scuba set".** Priced, deliberately
  unread — a basket assembled from parts is a price nobody quoted.
- **63 PADI fee entries the classifier declines** ("14% GST (on onboard
  purchases)", "Supervision fees for Level 1 divers…"). Each makes its trip's
  bill incomplete, which is the safe direction.
- **"Tips for the crew", 376 PADI entries, never priced.** One pattern away from
  classifying as `gratuities` — and because gratuities are customary, that
  would move an unpriced line into every affected trip's counted total. Worth
  doing deliberately, which is why it has not happened as a side effect.
- **A route label, a theme, a dive-site region for "Dolphin House".** All
  removed or refused on purpose.
