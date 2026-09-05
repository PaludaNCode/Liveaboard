# What is missing

Every hole in the published dataset, counted against `data/egypt-2027.json` as
built on 2026-08-30 (77 boats, 46 operators, 402 itineraries, 1,122 sailings).

**Three of these are closed** — see `docs/plan-missing.md` for what each one
actually bought, including the two joins §2 needed and the one it refused. The
counts below are as first measured; where a section is done it says so. §2 and
both of §3's identity holes closed on 2026-09-05 and say so in place, against a
dataset that has since grown to 416 itineraries and 1,145 sailings.

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
| ~~`Boat.length_m`~~ | — | **Closed.** 75 / 77 now; see §2. |
| `Operator.website` | 0 / 46 | Parsed from neither source. `Event.organizer` carries a name and no url. |
| ~~`Departure.spaces_left`~~ | — | **Gone.** Superseded by `berths`. Its always-empty CSV column became two filled ones, `places_at_price` and `berths_aboard`, on 830 and 1,113 rows. |
| `Requirements.max_depth_m` | 0 / 402 | Both sources state the entry bar as a level and a logged-dive count; neither states a depth. Kept: `make_seed.py` sets it. |
| `Requirements.nitrox_recommended` | 0 / 402 | Kept, same reason. |
| `Requirements.strong_current` | 0 / 402 | **Kept.** Not dead: `PadiComAdapter.extract_requirements` writes it from page text on a live path; this season's boats simply do not trigger it. |

`spaces_left` was the one to decide about rather than fill, and it is gone. The
other three were called modelled-ahead-of-the-data and that was wrong: two are
set by the seed generator and `strong_current` has a live writer that this
season's pages do not trigger. Deleting a working parser's output because the
current fleet does not exercise it is not a cleanup.

## 2. Read and not published — closed

`tools/scrape_fees.py` reads the specification table on every vessel page and
writes it to `data/fees.json` under `specs`. `promote` read that book for
exactly one key, `nitrox_free`, and dropped the rest on the floor:

| `specs` key | Present | Reaches the dataset |
|---|---|---|
| `cabins` | 79 / 79 | yes |
| `guests` | 78 / 79 | yes |
| `nitrox_free`, `nitrox_available` | 79 / 79 | `nitrox_free` only |
| `length_m` | 71 / 79 | **yes** — was no |
| `year_built` | 67 / 79 | **yes** — was no, and there was no field to put it in |

Both are promoted now, and PADI's own specification strip fills behind the
panel for the hulls liveaboard.com does not sell. What is left is what neither
source states: **`length_m` on 2** (Golden Dolphin, Sea Serpent Contessa) and
**`year_built` on 11** (Blue Storm, DUNE Longara, Grand Sea Explorer, MY Heaven
Saphir, MY Independence II, the three Red Sea Aggressors, Red Sea Blue Force 2,
Tala, Vita Xplorer). Those are §3-shaped holes — read, and the source said
nothing — not fields waiting on a fetch.

Whether either belongs on the *page* is still a separate question and still
open: `render` carries only what it draws, so both sit in the dataset and in
the CSV and neither is a column. This section recorded that closing them cost
no request, and it did not.

## 3. Vessel identity

- **Operator unknown on 3 boats: closed, as unanswerable.** MY Anemone, MY
  Heaven Saphir and MY Independence II sit under the placeholder operator
  `unknown-operator` ("Operator not captured"), carrying **44 in-season
  sailings** between them. All three are `padi_only` with no liveaboard.com
  vessel panel, and PADI states `fleetTitle: ""` for them — re-read 2026-09-05,
  so that is the source's answer rather than one crawl's — so neither of the two
  routes to a company name (`Product.brand.name`, then `window.shop.fleetTitle`)
  answers. The one loose end is closed too: Independence II's description prose
  names a *"Blue Water fleet"*, and a fleet on a booking site is not established
  to be the operating company — prose is a weaker warrant than the `fleetTitle`
  field the Blue Pearl fold was already refused on, not a stronger one. There is
  nothing further to read, so this is a hole to leave rather than a fetch to
  write. See `docs/sources/padi.com.md`.
- **Guests: closed. Cabins missing on 6.** Seven boats stated no guest count —
  Grand Sea Explorer, MY Anemone, MY Heaven Saphir, MY Independence II, MY
  Seawolf Dominator, Seawolf Steel and Vita Xplorer. PADI's vessel description
  answered four on 2026-09-05 (28, 20, 20 and 33); the other three name no
  count in any source this pipeline reads and were read from the sellers'
  pages by hand into `data/operator_facts.json` — Anemone 32, Seawolf
  Dominator 24, Vita Xplorer 24 — with Seawolf Steel's 33 confirming itself
  (one cabin sleeps three). All 77 hulls state a count now, which is what
  retires the *Hide unstated* chip until a new hull arrives without one.
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

**71 of 443 PADI books do not add up** — PADI named a charge and stated no
figure, so no total may be claimed on its behalf. It was **105**, and what
closed the other 34 was the classifier rather than a fetch: three titles it
declined were re-read (`docs/sources/padi.com.md`), the unreadable count went
43 → 0 and complete books 179 → 199, with **no mandatory total moving**.

Re-measured 2026-09-05, and this is the whole of what is left. Five titles, on
seven boats:

    Fuel surcharges 45 — DUNE Silky 15, DUNE Titan 15, Dune Longara 13,
                          Amelie Safari 2
    Visa fees       18 — MY Independence II 16, Red Sea Blue Force 2 2
    National park fees 8 · 10% (of the trip cost) VAT 8 — All Star Scuba Scene
    Service fees     2 — Amelie Safari

Four of the five are the honest kind: the seller named the charge and published
no number, and there is nothing to read. **The fifth looks actionable and is
not.** "10% (of the trip cost) VAT" is a rule rather than a figure, and this
project has no percentage `FeeBasis` — adding one is a change with its own
plan, because a basis has to be mirrored in `pricing._is_counted` and
`lineCounts` and it is the first one whose amount depends on the fare beside
it. It would also buy **nothing**: all 8 of those trips carry *National park
fees* unpriced in the same book, so pricing the VAT completes none of them.
Measured before it was proposed, which is what stops it being written.

A further **202 itineraries have no PADI fee rows at all**, which is a
different thing: PADI has not been read for that trip, not PADI stating the
fare covers everything.

**Unpriced lines in our own book**, by code and by how many vessels carry at
least one:

    alcohol 52 · snorkel_gear 31 · gratuities 30 · private_guide 17 ·
    extra_dives 15 · gear_rental 12 · course 10 · nitrox_course 10 ·
    naturalist_guide 6 · laundry 6 · land_excursion 5 · airport_transfer 3 ·
    visa 1

`gratuities` was the one that cost something — *customary*, counted by default,
so an unpriced line put a known cost of unknown size inside 30 vessels' totals.
It no longer is: all 55 vessels that state gratuities file them under Optional
and the tier follows the seller's block now, so the line is visible, labelled
optional, and outside the arithmetic. See `docs/plan-missing.md` §5.

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
- ~~**A guest count on PADI's vessel page.** Searched the whole body for every
  numeric form: none.~~ **Wrong, and struck rather than deleted.** The search
  was of the specification strip; the page states the count in its description
  prose, and a reader found it on MY Independence II — *"a 40-meter vessel
  designed for just 20 guests"* — beside our own page reading *guests not
  stated*. Read since 2026-09-05, last in the chain, and it fills four of the
  seven hulls that had no count. Vita Xplorer really does have no second
  source: it is the one boat with no PADI mapping at all. The lesson is the
  one this section exists to serve: a negative recorded from the wrong part of
  a page reads exactly like a negative recorded from the whole of it.
- **`Requirements` depth, current and nitrox advice.** No candidate field in
  either source.
- **The single gear items beside "Full scuba set".** Priced, deliberately
  unread — a basket assembled from parts is a price nobody quoted.
- ~~**63 PADI fee entries the classifier declines** ("14% GST (on onboard
  purchases)", "Supervision fees for Level 1 divers…"). Each makes its trip's
  bill incomplete, which is the safe direction.~~ **Read since 2026-09-05, and
  neither is a fee on the trip.** A tax on onboard purchases is charged on what
  a diver chooses to buy aboard, so it is not a charge on the sailing at all
  (`billed_on_purchases`, 68 entries) and it is carried under `on_purchases`
  rather than as a line; supervision of Level 1 and 2 divers is a service some
  divers buy (`FeeCode.GUIDED_DIVING`, filed Optional). Neither is *declined*
  now and neither makes a book incomplete — which is why the count fell without
  any total moving. What is left is §5's five unpriced titles.
- **"Tips for the crew", 376 PADI entries, never priced.** One pattern away from
  classifying as `gratuities` — and because gratuities are customary, that
  would move an unpriced line into every affected trip's counted total. Worth
  doing deliberately, which is why it has not happened as a side effect.
- **A route label, a theme, a dive-site region for "Dolphin House".** All
  removed or refused on purpose.
