# Closing the gaps in `docs/missing.md`

**§0, §1, §2, §3 and half of §6 are done** — see *What it actually bought*, at
the foot, which records the measurements rather than the intentions. §5 is
still open and deliberately so; the rest of §6 turned out to be wrong and the
correction is down there too.

Ordered by what each item costs against what it fills. Every source claim below
was probed live on 2026-09-01 rather than reasoned about; the probe results are
written into `docs/sources/` in the same commit, negatives included.

**The headline is not the one asked for.** Aphrodite's dive count cannot be
filled from its description page — the page states an upper bound for the
vessel, and the trip in question is a snorkelling cruise whose count
liveaboard.com prints as a dash. §0 has what it does say. The item that
actually moves the most is §2, and it turned out to be a change of where one
fetcher looks rather than a new source.

Each section is its own branch and its own merge.

## 0. Aphrodite — what the pages say

The single itinerary with no dive count is **Aphrodite, *North Dolphins*
(Hurghada – Hurghada)**, 7 nights, 2 sailings, tour 395645.

Fetched the fragment again today. liveaboard.com answers, and its answer is a
dash:

    Duration    8 Days / 7 Nights
    Trip type   Diving cruise
    Group Size  Up to 23 guests
    Dives       -
    Experience  No Certificate needed - No minimum logged dives required.

It is the **only one of 317 trips in the book where that field is a dash**, and
the parser is right to return `None` for it. The day plan says why:

> Enjoy 2–3 guided **snorkeling** sessions daily … Early morning visits to
> Sha'ab El Erg provide excellent opportunities to encounter resident spinner
> dolphins … suitable for snorkelers and swimmers of all experience levels.

Guests "prepare their **snorkeling** equipment" on day 1. PADI sells eleven
Aphrodite itineraries and does not list this one. So there is no dive count
because there are no dives.

**The vessel page does state a figure, and it is not usable.** In the
Highlights bullets:

> Tech Diving / Trimix support · ENOS system on board · Diving courses
> available · **Up to 20 dives + night dives**

That is an *upper* bound for the boat's year, and this project keeps the low
end of a stated range so price per dive stays a ceiling — taking 20 would
flatter every Aphrodite row. It is also a vessel-level bullet, which
`_dives`' `for_nights` guard exists to keep off the trips it was not quoted
for, and putting 20 dives on a snorkelling cruise is the worst case that guard
is for.

Probed across twelve vessels: the Highlights block exists on 8, and a dive
count appears in it on **2** — Aphrodite's *"Up to 20"* and Emperor Asmaa's
*"Deep South trips that give 21 dives in a week"*, which is qualified by route
and by length. Free marketing prose, two hits, both upper bounds. **Ruled out
as a source**, and written into `docs/sources/liveaboard.com.md` so it is not
re-derived.

**What to do instead.** The row currently prints *not stated*, which claims
nobody answered when liveaboard.com did. Two states are collapsed into one:

| Truth | Rows | Prints today |
|---|---|---|
| the source printed a dash | 1 | *not stated* |
| we never fetched a fragment | 85 itineraries | *not stated* |

Splitting them is a small change in `render.py` and one field on the trip
record — a dash read is *asked and answered*, and price per dive is not a
ceiling there, it is meaningless. Worth doing after §2, which shrinks the
second row to almost nothing and makes the distinction visible instead of
academic.

## 1. `length_m` and `year_built` — no request at all

`tools/scrape_fees.py` already reads the specification table and writes
`specs` into `data/fees.json`. `promote` reads that book for `nitrox_free` and
nothing else, so **`length_m` (71 of 79 vessels) and `year_built` (67 of 79)
are in the repository and never reach the dataset**.

- add `year_built` to `Boat` beside `length_m`, which already exists and is
  null on all 77;
- read both from `spec_book` in `promote`, next to `guests` and `cabins`;
- `promote` then `build`, both offline, both seconds.

**The guard matters more than the change.** This was missed because nothing
notices a `specs` key that goes nowhere. Add a test asserting every key the
fee book publishes either reaches the dataset or is named in an explicit
ignore list — so the next key `scrape_fees.py` learns to read cannot be
dropped in silence.

For the 8 vessels whose table states no length, **PADI answers for 5** of them
(DUNE Longara, DUNE Titan, Snefro Love, Snefro Pearl, Snefro Target). See §3.

## 2. The 85 unfragmented itineraries — one request per vessel

This is the largest hole on the list: **85 itineraries, 210 sailings, a fifth
of the season**, with no per-trip fragment. On those rows the dive count, the
group size, the stated entry bar and the operator's own reef list all fall back
to the trip title or to PADI.

`tools/fetch_itineraries.py` builds its URLs from tour ids in
`data/archive.json`. The archive holds ids from `Event` nodes the departure
crawl parsed — so a boat with no in-season departure on liveaboard.com never
contributes an id, and every `padi_only` sailing's itinerary is unreachable by
construction. That is the whole cause. It is not a missing source.

**The ids are on the vessel page.** Probed live on three barren-listed,
`padi_only` boats:

| Vessel | Tour ids on the page |
|---|---|
| MY Blue Pearl | **12** |
| Bella 2 | 4 |
| Eriny | 1 |

And the fragments answer in full. Blue Pearl's first three, fetched:

    382612  North & Brothers (Hurghada - Hurghada)  Approximately 18 dives in total  Up to 20 guests
    382613  North & Tiran (Hurghada - Hurghada)     Approximately 18 dives in total  Up to 20 guests
    382614  North & Wrecks (Hurghada - Hurghada)    Approximately 18 dives in total  Up to 20 guests

Nine MY Blue Pearl itineraries publish today with no fragment at all.

**It fills `Boat.guests` for free.** `promote`'s guest chain is already
`spec_book → hand → trip_guests → summary`, so a fragment for a panel-less
boat fills the count without a line of new code — which covers 6 of the 7
boats with no `guests`.

Cost: one vessel-page fetch per boat with unfragmented trips (21 boats), then
one fragment per new tour (~85). Incremental after that, like every other run:
a trip already in `data/itineraries.json` is not re-fetched.

**The risk is the join, and it is a real one.** Both sides key on
`promote.itinerary_key`, which keeps the port pair in the string, and the two
sources punctuate ports differently. Ours come from PADI for these rows:

| Ours (PADI-derived) | The fragment | Joins |
|---|---|---|
| `North & Brothers (Hurghada - Hurghada)` | `North & Brothers (Hurghada - Hurghada)` | yes |
| `North & Tiran (Hurghada-Hurghada)` | `North & Tiran (Hurghada - Hurghada)` | **no** |
| `North & Wrecks (Hurghada- Hurghada)` | `North & Wrecks (Hurghada - Hurghada)` | **no** |

Two of three fail on whitespace alone. So the key has to normalise the port
parenthetical — or better, join on the trip name plus `port_from` and
`port_to`, which the dataset already keeps as two fields precisely because a
joined string is not a record.

**And it needs the failure reported.** Every field the book fills has a
fallback, so a key that matches nothing fails silently — which has happened
here once already, 71 of 314 itineraries matching nothing under their banner
spellings. `fetch_itineraries.py` must print how many harvested ids matched no
itinerary and how many itineraries remain unfragmented, and a guard should
assert the second number does not grow.

## 3. PADI's vessel page states cabins, length and year

Server-rendered, plain `urllib`, no browser, no bundle. Read off
`/liveaboard/egypt/my-anemone/` today:

    Cabins 16 · Length / Width 45 m / 8 m · Year built / renovated 2022 / 2025 ·
    Rental equip. YES ($) · Internet FREE · Nitrox FREE

`tools/fetch_padi.py` already fetches this exact page for every vessel, to read
`window.shop` for the country, currency, name and fleet. The strip is in the
same response and costs nothing extra. It closes:

- **cabins** for the 6 boats with none;
- **length** and **year** for the 5 length-less vessels PADI carries (§1);
- a second reading of `nitrox_free`, against which the fee panel's can be
  checked rather than trusted.

Precedence follows the existing rule: the vessel's own panel wins where it
exists, PADI fills where it does not. Never a merge.

**Negative, and worth having written down: the PADI vessel page states no
guest count anywhere.** Searched the whole rendered body for every numeric
form — 0 hits. So `guests` for **Vita Xplorer** has no second source: it is the
one boat with a liveaboard.com panel whose table leaves the field blank, and
its answer is either a parser fix on that `<dl>` or nothing. §2 covers the
other six.

## 4. The operator on three boats — leave it

MY Anemone, MY Heaven Saphir and MY Independence II carry 44 in-season sailings
under *Operator not captured*. Both routes to a company name are exhausted, and
this was verified live rather than assumed:

- no liveaboard.com vessel page, so no `Product.brand.name`;
- `window.shop` on all three states **`fleetTitle: ""`** — PADI publishes no
  fleet for them.

The description prose names no company either. **Recommendation: do nothing.**
"Operator not captured" is true, and the alternative is naming a company from a
hull's own marketing copy — the assertion `padi_aliases.json` already refuses
to make for Blue Pearl on much better evidence.

## 5. Fees

**33 itineraries carry PADI fee rows PADI did not price**, so no total may be
claimed on its behalf: Blue Horizon 9, Blue Melody 8, All Star Scuba Scene 6,
MY Independence II 5, DUNE Longara 3, Red Sea Aggressor IV 2. The entries exist
and are unread rather than absent — start from the 63 the classifier declines,
which `docs/sources/padi.com.md` lists, and check which of them are these 33.
Some will be right to keep declining ("14% GST (on onboard purchases)" is a
percentage of an unrelated purchase); each one that is not is a trip whose bill
starts adding up.

**"Tips for the crew", 376 PADI entries, never priced.** One pattern away from
classifying as `gratuities`, and gratuities are *customary*, so the effect of
adding that pattern is to move an unpriced line into every affected trip's
**counted** total. Do the pattern and the unpriced-customary display state in
one change, or neither. The same shape as the 30 vessels whose own `gratuities`
line carries no figure — a known cost of unknown size, already in the counted
tier, and currently invisible.

**`single_supplement` is not a gap.** The code is used by no vessel because the
figure lives on the cabin ladder as a per-rung percentage, which the page
already prints. 66 of 830 ladders state none (Yachtiano 17, Destiny 16, Queen
Sherry 16, Unity 13, Oceanix 4) and the ladder says so. No action; the unused
fee code is worth deleting.

## 6. Three fields to delete rather than fill

- **`Departure.spaces_left`** — null on all 1,122 rows, its job taken by
  `berths`, and still shipping as an always-empty column in `export.py`.
- **`Requirements.max_depth_m`, `nitrox_recommended`, `strong_current`** — null
  on all 402, and neither source has a field behind any of them. Modelled ahead
  of the data; no probe will change that.

A field that cannot be filled is a column the page has to explain, and this
project's own rule is that an empty state must mean something. These four mean
nothing.

## Sequence

1. **§1** — an afternoon, no requests, and the guard stops it recurring.
2. **§2** — the one that moves the season. Do the join on `port_from`/`port_to`
   before fetching anything, and prove it against Blue Pearl's twelve ids on a
   capped run (`--limit`) before pointing it at 85.
3. **§3** — folds into `fetch_padi.py`'s existing fetch.
4. **§0's split** — once §2 has shrunk *not stated* to the rows that earn it.
5. **§5** — deliberate, and the display state ships with the pattern.
6. **§6** — a deletion, whenever.


## What it actually bought

Written after doing it, because two of the estimates above were wrong and the
wrong ones are the useful part.

**§1 — as expected.** `length_m` 0 → 63 of 77 boats, `year_built` 0 → 60,
no requests. `TestEverySpecTheFeeBookHoldsIsPublished` now fails on a `specs`
key that reaches nothing, which is the thing that let this sit unnoticed.

**§3 — landed, and empty until tomorrow.** `PadiComAdapter.specs_from_page`
reads the strip and `fetch_padi.py` writes it, but `data/padi.json` carries no
`specs` yet: the book is rebuilt whole from a raw store that a local run cannot
refresh (`MIN_BOOK_RATIO` refuses a cold rebuild, correctly). The daily
`padi.yml` fills it, and the 14 boats still without a length become 9.

**§2 — the mechanism works and the ceiling is lower than the count suggested.**
The discovery pass found **162 tour ids on 15 vessel pages** and the book went
from 317 trips to 352. Published itineraries with no fragment: **85 → 74**, and
the dive count now comes from the operator's own per-trip figure on 327 rather
than 316, with PADI's last-resort count down from 69 to 61.

Not the 85 the plan implied, and the residue divides cleanly:

| Still unfragmented | Itineraries | Why |
|---|---|---|
| boats liveaboard.com does not list at all | **41** on 6 | no vessel page exists to harvest — PADI is the only source these trips will ever have |
| trips the two sellers name differently | **33** on 13 | Eriny's *Sinai Classic* against *Sinai Classic One Week*; Blue Seas' PADI routes that site lists nowhere |

The 41 are not a gap this or any fetch can close, and the pass now says so
rather than spending six requests learning it each run: the fee book is the
record of which hulls liveaboard.com carries, and boats outside it are named
and skipped.

**Two joins were needed, not one, and a third was refused.**

1. *The port pair's spacing*, as predicted. `itinerary_key` re-renders it, and
   MY Blue Pearl went 9 short to 6 on the strength of it.
2. *The trip's wording*, which the plan missed. A vessel liveaboard.com sells
   no berth on takes its **names** from PADI, so the fragment spells the same
   week differently — *St. Johns* against *St. John's*. `padi_key` already
   exists for looking a foreign record up and is now the fallback, worth 5
   more itineraries. Emperor Asmaa's two trips that fold onto one key are
   refused rather than guessed between, which is the standing rule.
3. *The harbour names.* Folding them through `PORT_ALIASES` looked obvious —
   *Marsa Ghalib* and *Port Ghalib* are the same harbour and the table says so.
   Measured: **one extra match, and two collisions**, one of them Blue
   Horizon's own two itineraries onto a single key. A key that merges two of
   our own trips serves one's dive count and reefs for the other, which is
   worse than the miss. Not done, and `test_two_harbours_stay_two_trips` holds
   it that way.

**And the pass now reports what it did not reach**, every run, fetch or none.
Every field this book fills has a fallback in `promote`, so a key that matches
nothing fails silently — which is how 85 itineraries stayed unread without
anything going red.


## §0 and §6, after doing them

**§0 — the two silences are now different cells.** `dives_read` is written on
an itinerary whose own fragment was read, and the page prints **none stated**
where the seller answered and left it blank against **not stated** where no
source was read at all. One row is in the first state, which is the point:
Aphrodite's *North Dolphins* is not a hole in the data, and the cell no longer
says nobody published a count when liveaboard.com did.

**§6 was half wrong, and the wrong half is the interesting one.**

`Departure.spaces_left` was as dead as claimed — but *deleting* its always-empty
CSV column was the wrong move. `spots_at_advertised` already answers the
question, so the column is now **two** columns under the page's own two names,
`places_at_price` and `berths_aboard`, filled on 830 and 1,113 of 1,122 rows
where one empty column stood before. A published spreadsheet gaining two real
numbers beats it losing one blank.

Doing that found a third thing nobody was looking for: **`spots_at_advertised`
read `block.get("spots")`**, against blocks that have been lists since they
gained a second seller. It could only ever have raised. Nothing noticed because
nothing called it — a dead accessor written against a shape the data left
behind, which is worse than no accessor, because it reads as the answer to a
question that has one. Both counts now share one `_stated` helper and the
positions are named once, mirroring `app.js`.

**The `Requirements` fields stay.** The plan called `max_depth_m`,
`nitrox_recommended` and `strong_current` modelled ahead of the data and said
to delete them. They are null across the season, but that is not the same as
dead: `PadiComAdapter.extract_requirements` writes `strong_current` from page
text on a live path, and `tools/make_seed.py` sets two of the three. Deleting
them would remove a working parser's output because this season's boats happen
not to trigger it. Left alone, and the reason recorded so the next reader does
not have to re-derive it.
