# CLAUDE.md

Price-transparency site for Egyptian liveaboards. Takes an advertised berth
price and reassembles the real bill. See README.md for the domain.

## Commands

```bash
PYTHONPATH=src python3 -m unittest discover -s tests   # everything, no deps
PYTHONPATH=src python3 -m liveaboard.cli check         # validate + summarise
PYTHONPATH=src python3 -m liveaboard.cli build         # -> site/index.html
PYTHONPATH=src python3 -m liveaboard.cli changes       # what moved since HEAD~1
python3 tools/make_seed.py                             # regenerate seed data
```

Re-promoting is offline and takes seconds — do it after any change to
`src/liveaboard/`, rather than waiting for a crawl. **CI enforces this**
(`promote --check`), so skipping it is a red build rather than a site that is
quietly a parser behind:

```bash
PYTHONPATH=src python3 -m liveaboard.cli promote --check   # did I forget?
PYTHONPATH=src python3 -m liveaboard.cli promote           # rebuild the dataset
PYTHONPATH=src python3 -m liveaboard.cli build             # and the page
```

Take the defaults. `refresh.yml`, `fees.yml`, `promote.yml`, `itineraries.yml`
and the CI check all promote on them, so one canonical set of inputs lives in
`cli.py` and cannot drift apart across five workflows.

**A test over committed data gates the commit, never the fetch.** The suite
holds both kinds and the default command runs both; only the three jobs that
fetch run it twice, and the first of those runs opts out:

```bash
LIVEABOARD_TESTS=code PYTHONPATH=src python3 -m unittest discover -s tests
```

**And a job that pushes runs CI's own list before it does.** GitHub does not
trigger workflows on pushes made with the default `GITHUB_TOKEN`, so not one
scheduled data commit has ever had a CI run against it — which is how a refresh
published 36 sailings advertising a berth nobody could buy and left `main` red
for seven hours, found by a person opening an unrelated PR. `.github/actions/
checks` holds the list once and `ci.yml` uses it too, so the two cannot drift;
`TestEveryPushingWorkflowChecksItself` refuses a workflow that pushes without
it. Add a check there rather than to `ci.yml`.

This is not a convenience. `cabins.yml`, `refresh.yml` and `itineraries.yml`
ran the whole suite as their *first* step, so on 2026-08-30 a stale cabin book
contradicting the rows the refresh had just written failed the suite in front
of the only job that could refresh that book — the guard gating the fetch that
would clear the condition the guard was testing for. A run that fetches and
then refuses to publish is recoverable; one that refuses to fetch is not.
Committed data is reached through `tests/published.py`, which is the gate, and
`TestThePublicationGateIsComplete` refuses a test that opens `data/` for
itself. Hand-maintained inputs — `padi_aliases.json`, `operator_facts.json` —
are outside it: no crawl touches them, so nothing asserted about them can ever
be cleared by fetching.

**One source per workflow, and two shared actions.** `refresh.yml` bundled the
ECB rates, the liveaboard.com crawl, the itinerary fragments and PADI's deals,
so proving a two-request change to `fetch_deals.py` meant a run that first
fetched 320 vessel pages from a site with nothing to do with it. Each source is
its own dispatchable job now — `fx.yml`, `refresh.yml` (the crawl alone),
`deals.yml`, `itineraries.yml`, `padi.yml`, `cabins.yml`, `fees.yml` — and each
ends the same way, because the shape is identical and six copies of it drifted:

- `.github/actions/checks` — everything CI asserts. `ci.yml` uses it too, which
  is what makes "the same bar" true rather than intended.
- `.github/actions/publish` — stage, commit, push with rebase-and-retry. Its
  `subject` takes `{today}` and `{sha}`; a `headline` carrying scraped text is
  its own input and never reaches a shell as code.

Every one of those files is a **promote input**, so a job commits its data
*and* the dataset built from it. Committing an input alone leaves
`promote --check` red until something unrelated heals it.

Three guards police this and two of them have already been blinded once, by the
refactor that moved `git add` and `git push` into the action: they now assert
they can still see what they are checking, because a check that stops checking
is green for the wrong reason.

**Read the published page without checking anything out.** After a merge, to
see what actually shipped:

```bash
git show origin/main:site/index.html > /tmp/prod.html   # no checkout, no reset
```

Resetting the working branch onto `main` to look at it works and then leaves
the branch one merge commit "ahead" of its own remote, which reads as unpushed
work every time. `git show` answers the same question and disturbs nothing.

## Answering

Short. The finding, not the derivation. This is a codebase whose owner knows it
better than you do, so a table of evidence for a conclusion he can reach in one
glance is noise, not rigour.

- Lead with the answer. Detail only if asked.
- One decision per message when a decision is wanted.
- Skip restating what was just said, and skip the recap section.
- Findings that matter go in `docs/` or a commit message, not into chat twice.

## Invariants

Break these and the site starts lying quietly rather than failing loudly.

- **Never invent a price.** Every price and fee needs a `Provenance`. A parser
  that cannot find a number returns `None`; it does not guess. `seed_estimate`
  triggers the "not real quotes" banner — do not suppress it.
- **`pricing._is_counted` is mirrored by `lineCounts` in `templates/app.js`** —
  both `DEFAULT_ON_TIERS` and the order of its checks. The toggle is asked
  before the tier: nitrox and gear are filed under the source's *Optional*
  extras, and testing the tier first made both switches on the page add nothing
  to any total.
- **Normalisation happens in Python only.** Fee basis (per night / day / dive /
  week) and FX conversion resolve in `pricing.py`; the browser only sums lines
  that are switched on. Do not add pricing logic to the JS. Operators quote the
  same gear set per day, per trip *and* per week, so comparing raw amounts
  across vessels is meaningless — normalise first.
- **Included fees stay in the breakdown at zero.** Removing them hides the
  difference between a bundled operator and one that bills at the dock. Both
  sellers are read for it now: PADI states it in `whatsIncludedNew`, on 447 of
  447 itineraries, and reading only what it charges *on top* left two bills in
  one expanded row disclosing at different depths. The list is prose rather
  than labels, so a **parenthetical is a qualifier and never the name** —
  *Airport Meet & Greet (VISA assistance)* classified as the visa fee and would
  have told eight itineraries' readers that the €25 they still pay at the
  airport was covered. And an amenity nobody can classify — Water, Coffee, Free
  WiFi — is not a hole in a fee book: inclusions never reach `unreadable`,
  which would have taken the book from 259 complete trips to none.
- **No score grading operators.** The site compares what trips cost; it does
  not rank who sells them. A per-operator "honesty" percentage was removed:
  it read as a league table and contradicted the total beside it.
- **Never claim a total the disclosure does not support.** No fee lines means
  nobody looked; only optional ones means the operator did not state its
  required extras. Neither is a clean bill.
- **No route, theme or level labels.** They were inferred from the dive sites,
  read by nothing on the page, and removable without loss: if you want a BDE
  week you filter on Brothers, Daedalus and Elphinstone. A name for a set of
  sites is a layer that can be wrong — a St John's week was badged BDE — and
  answers nothing the site filter does not. `Itinerary.requirements` stays: a
  stated safety requirement is the operator's claim, and is never softened.
- **A vessel summary is the boat's year-round brochure, not the trip's.** Never
  a source for where one trip goes: Aphrodite's names St John's, so its *North
  Wrecks* week would be tagged with a reef 600 km away.
- **Never derive a dive count.** Ten vessels publish one and they state 15 to
  21 for the same seven-night week, so a fixed three-a-day is wrong by up to a
  third — and at €60–100 a dive that is the whole difference between two boats.
  The spread has a cause: a week that crosses further, or sits longer in the
  parks where night dives are not permitted, fits fewer in. Operators quote a
  range and the dataset keeps the **low end**, so price per dive is a ceiling.
  A vessel-level count is for a standard week (`dives_for_nights`) and is
  withheld from every other trip length that boat sells; the itinerary fragment
  states one per *trip*, which needs no such guard and wins. Unknown stays 0 and
  the column says "not stated".
- **Four sources for a trip's reefs, in order, never merged.** The operator's
  own description, then its region list, then the trip title, then — last —
  what the *second* seller says about the same week (`padi.json`'s
  `dive_sites`, folded by `fetch_padi._padi_sites` from the day plan and then
  the blurb). PADI is last because it is the least structured: against the 180
  trips both sellers describe, its words add 173 reef mentions ours does not,
  among them Elphinstone on a Brothers and Safaga week off a sentence saying
  the two "are quite distant from one another". Merged in, that is the
  BDE-badging failure removed once already; used only where the three above are
  silent, it is the difference between a row the site filter can reach and one
  it cannot — 47 rows on 19 itineraries, down to 4 on 3. The three that stay
  blank name no reef in any field, and blank is right for them.
- **The per-trip book beats the trip title, and never joins it.** `promote`
  merges `data/itineraries.json` — the operator's own reefs, dive count, group
  size and entry bar for one trip — the way it merges the fee book. Where it is
  silent the title parser still answers, so a fetch that has not reached a trip
  never blanks it. Unioning the two would reimport the titles' errors, which is
  the whole reason the fragment is fetched. Both sides key on
  `promote.itinerary_key`; every field has a fallback, so a key that stops
  matching fails silently.
- **`Itinerary.name` is identity; `Itinerary.title` is presentation.** The id is
  built from `name`, and two sailings differing only by port are two trips.
  Only `title` is ever tidied, and it is tidied in exactly five ways.
  **One route, not a house style.** `BDE`/`BDE_TITLE` fold the seven spellings
  of Brothers/Daedalus/Elphinstone onto one; the pattern is anchored at both
  ends, so *Marine Park North: Brothers - Daedalus & Elphinstone* and
  *Brothers, Daedalus, Elphinstone & Safaga* are untouched. Twelve other groups
  differ the same way and are deliberately left as their operators wrote them —
  do not generalise this into a rule that rewrites every title, and never
  reorder words: nothing here can verify the order means something, and nothing
  may assume it means nothing — *St. John's & Daedalus* and *Daedalus & St.
  John's* stay two titles. **House separators, on route lists only.**
  `_house_separators` prints a list of stops as commas then `&` before the
  last, so *North - Brothers*, *North and Brothers* and *North & Brothers*
  print once. It fires only where `_is_place_list` holds — every part between
  the separators is something `SITE_HINTS`/`SITE_ALIASES` already recognises —
  because *Dancing with Dolphins - Dolphin Liveaboard Safari* is a sentence
  whose dash is not a separator and *Best of Dahab and Tiran* is English.
  Reuse that vocabulary rather than writing a second one: a copy drifts, and
  the day it drifts is the day the site starts repunctuating prose.
  **Case only, otherwise.**
  `_settle_title_case` picks one spelling where titles differ *only* by
  capitalisation, and always one the operator actually used — never title-cased
  into a spelling nobody wrote, because the fleet is full of names a casing
  rule would ruin (*MY Odyssey*, *St. John's*, *SS Turkia*). Ties break
  alphabetically: `promote` is pure and CI compares its output byte for byte.
  `TITLE_FIXES` corrects what is *wrong* rather than what is merely different —
  zero-width spaces, three characters doing one apostrophe's job, a dash with
  a space on one side only, the two misspellings of Daedalus and the one each
  of Zabargad and Gubal, listed the way
  `PORT_ALIASES` lists harbours because a near-miss rule that catches those
  also catches a reef that only looks like another. A misspelling goes in the
  table only where the trip's own `dive_sites`, parsed from the operator's
  description, already name the reef correctly — the dataset confirming the
  reef independently of its title is what separates a correction from a guess.
  *Gobal* is the single exception and is commented as one: its trip names no
  Gubal at all, so the warrant is the fleet's instead — 46 parsed *gubal*
  against one *gobal* — a weaker warrant taken deliberately, and only at that
  margin. **Four reefs, not every reef.** `REEF_ALIASES` folds the
  five spellings of *St. John's*, the three of *Brothers*, the two of *Fury
  Shoals* and the three of *Ras Mohammed* onto one each — differences rather than mistakes, folded for the
  reason `BDE` folds one route: the reader should not have to work out that the
  reef is the same reef before comparing the prices beside it. Each replacement
  is the *plurality* of what the fleet wrote, never a spelling invented to be
  consistent, so the table re-derives from the data instead of from taste. The
  guard on `Brother` is the shape of the risk — *Big Brother* and *Little
  Brother* name the two islands separately, and folding either into the pair
  would delete which island the trip dives. *Ras Mohamed*/*Ras Mohammed*/*Ras
  Muhammad* are all real transliterations and none is wrong, so this is a fold
  and not a correction. **What picks a spelling is `SITE_HINTS`, not a count of
  the data.** The parsed `dive_sites` agree unanimously on any reef only
  because the alias table folds them there — quoting that agreement as evidence
  is this project's own choice reflected back at itself, and an earlier version
  of this note made exactly that mistake. The real reason to fold a title is
  that the trip-name column and the filter chip beside it must not disagree,
  which is also why `SITE_HINTS` and the title tables have to move together:
  *Fury Shoals* was changed in both when the two drifted apart. Do not lengthen this
  table without counting the spellings first, and count in the *names* rather
  than the titles: counting folded titles hid *Ras Muhammad* entirely.
- **Zero runtime dependencies**, stdlib only, and the site stays one
  self-contained HTML file. Tests use `unittest`, not pytest. One file makes
  page weight load-bearing: nothing is lazily fetched, so anything written per
  departure ships 878 times. Fees belong to the itinerary.
  **No CDN — nothing external at all**, and `ALLOWED_EXTERNAL` in
  `tests/test_dataset.py` is empty to keep it that way. The page used to pull a
  webfont stylesheet from Google; because a `<link>` in `<head>` is
  render-blocking, that cost **13 seconds to first row** whenever the host was
  slow or unreachable, against 0.6 seconds without it (#59). Fonts are the
  visitor's own now. Adding a host back is adding a way for the page to be
  blank on somebody else's network.
- **A page nobody read is not an empty one**, however it went unread. Two
  mechanisms have now deleted real, bookable trips this way and both were
  reported as withdrawals. A vessel-month page that answers nothing is asked
  again (`PARSE_ATTEMPTS`) and carried forward if it fails twice. A vessel the
  **barren skip list** holds back is carried too: nothing goes wrong there at
  all, the run simply chooses not to look, and AVO's and Blue's three sailings
  were dropped by a run that never asked — a probe found all three still on
  sale. `discover()` records what it skips via `not_looked_at`, so the skip and
  the failure travel down the same channel. `CARRY_MAX_DAYS` outlasts
  `BARREN_RECHECK_DAYS` by design.
- **And a vessel that sells nothing is not one that prices nothing.**
  `_departure_book` drops a sailing with no date, with no price, and outside
  the window, all three the same way: silently. So twelve mapped PADI vessels
  produced no row and the run could not say which of those it was.
  `fetch_padi._sailing_counts` records the lot per vessel and `why_empty`
  names it in the run log: *"22 dated sailing(s), none in the season
  (2026-09-05 to 2027-01-30)"* is a calendar that has not reached us, where
  *"17 … — and none of them priced"* is VIP One, which lists dated berths at no
  stated price. **Unpriced is counted over every dated sailing, not only the
  in-season ones** — VIP One's window stops in December, so counting inside it
  would have hidden the fact entirely. Recorded, never acted on: `promote` does
  not read it, and a boat with no price still gets no row.
- **A seller nobody asked is the same blindness.** The two sources merge on
  `(boat, date)` — exact, because a date has no spelling — and for eleven
  refreshes that merge could only ever fill a field, on the rule that the row
  count was the candidate's. It made "one row per sailing" quietly mean "one
  row per sailing liveaboard.com happens to list": 601 of PADI's 654 in-season
  sailings landed on a row we had and the other **53 were dropped**, among them
  Blue Storm's and Blue Seas' near-complete weekly seasons, on boats the page
  already carried. `promote` creates those rows now. The berth price is PADI's
  and says so; the fees are the vessel's own book, which the boat charges on
  board whoever sold the berth. Such a row carries **no `padi_price`** — one
  seller's figure repeated into the second seller's field prints as two sellers
  agreeing about a sailing one of them does not offer — and the page marks it
  `PADI only`, which is a fact about who was asked and not about the trip.
  **And on a vessel the barren list held back, even that is too strong.** The
  crawl keeps the distinction — `discover` records a skip through
  `not_looked_at` — and `promote` lost it, publishing *"liveaboard.com does not
  list this sailing"* on 87 sailings across Bella 2, Bella 3, Eriny and Blue
  Pearl: a result for a page nobody opened. All four have a vessel page the fee
  scraper read in full. `candidate.not_asked` names what the run declined to
  visit, recorded by the crawl rather than re-derived — the skip rule is
  date-dependent and promotion is pure — and the row says *not asked* instead.
  Same rule as `fees_known`: no fee lines means nobody looked, not that there
  are none.
- **A boat only the second seller lists is still a boat.** 22 Egyptian
  liveaboards on PADI mapped to nothing of ours; ids for them are minted in
  `data/padi_aliases.json` under `padi_only` and 10 carry season sailings, so
  the fleet is 77 rather than 67. `padi_only` means PADI is the only source of
  *sailings*: ten of the 22 have a liveaboard.com fee panel and simply no
  departures there. Such a vessel has no name and no operator from the first
  source, so both come from `window.shop` — `title`, and `fleetTitle` minus
  PADI's trailing "Fleet" — kept **verbatim**, shouting included, because
  `OPERATOR_ALIASES` already rules that tidying a company's capitalisation is a
  short step from deciding what it is called. **But the vessel page states the
  company, and it beats the fleet label outright** — `Product.brand.name` in
  the page's own JSON-LD, read by the fee run because that is the only pass
  visiting a boat with no departures. It is the *same source* every other
  operator here comes from, so preferring it is not a judgement call; 79 of 79
  vessels state one. It ends the shouting without anybody deciding how a
  company spells itself (`Bella Liveaboard` is that source's own words, not a
  tidy-up of PADI's `BELLA LIVEABOARDS`), and it settles Blue Pearl. **A fleet
  is not an operator**:
  PADI shelves MY Blue and MY Blue Pearl under one "BLUE PLANET Fleet", and
  folding that onto our Blue's stated "Blue Planet Liveaboards" removed a
  duplicate row by asserting a company for a hull our own source connects to
  nobody. Two operator rows that may be one company is cosmetic; the assertion
  is not. **The two are one row now, and the rule is why that is allowed** —
  not folded on the fleet label, which still folds nothing, but on Blue Pearl's
  own page saying `"brand": {"name": "Blue Planet Liveaboards"}`. The fold is a
  fact rather than a tidy-up, which is the whole difference. Where our fee book is absent
  PADI's per-itinerary one becomes the itinerary's own (`padi_sourced_fees`);
  where ours exists it wins outright. Never a merge of the two: one figure per
  vessel against one per itinerary is not a difference you can add up, and a
  line from each is a bill neither seller quotes.
- **A sale is the list price a seller prints beside its own, never a banner.**
  liveaboard.com publishes no deals listing at all — `/liveaboard-deals` is SEO
  prose and the seasonal campaign pages carry only *"Up to 30% OFF"* over a
  region — but strikes the list price through beside every discounted cabin, and
  `fetch_cabins.py` has read that nightly since #79. It is a **whole-ladder**
  fact: of 263 discounted sailings, none is discounted in part and none leaves
  its cheapest cabin at list, so the advertised price against its own `<del>` is
  the sailing's discount. Setting the cheapest price against the *dearest*
  room's list price is the mistake here and reports a 33% sale as 40%.
  `Event.name`'s `33% Off:` prefix agrees on 241 of 241 and is still not the
  input: the ladder carries the money as well as the rate and finds 22 sales
  that carry no banner. **Neither seller marks down the other's price.** They
  agree on the percentage on 158 of 158 sailings where both speak, but a row
  states `pct` only from the seller whose fare it prints — two Red Sea Aggressor
  IV sailings are cut on PADI and at list here, and are *on sale* with no
  percentage rather than showing PADI's 33% off a fare nobody cut. An unread
  booking page states nothing, which is not "no": 3 of the 5 PADI-only
  discounts are exactly that.
- **A deal is placed by its vessel, never by the country beside it.** PADI's
  deals listing has to be asked for the USA as well as Egypt, because all three
  Red Sea Aggressors are filed under the USA and asking Egypt alone drops them.
  The same breadth returns Bahamas, Belize, Cayman and Roatan: 5 of 18 offers in
  the published season sail another ocean, so the field is wrong about where a
  boat is more than a quarter of the time and cannot place anything. `promote`
  joins the deal's vessel to a boat of ours and lets that decide. A vessel that
  joins to nothing is **named** — in the build log and on the page — rather than
  dropped: an Egyptian boat under a USA label that nothing has paired is exactly
  what the breadth is for, and only a name a person reads tells it apart from a
  Caribbean one. The change log obeys the unread-page rule too: absences in a
  reading the fetcher could not finish are not withdrawals, and it says so
  instead of reporting them.
- **Two itineraries must never share an id.** `Dataset.from_dict` keys them by
  id, so a collision keeps one and serves every departure of the loser the
  winner's reefs, fees and dive count: the row count stays right and the page
  is confidently wrong. Ids are truncated to 96 characters and the ports sit at
  the end, so two long names can collide without anybody typing a wrong
  character — which would break "two sailings differing only by port are two
  trips" silently. `promote` raises instead. A foreign trip name is folded onto
  ours through `padi_key` and only where that key names exactly one of our
  trips; where two of a boat's own itineraries share it the fold is refused,
  because nothing can say which harbour the other source meant.
- **An unreadable page is not an empty one.** A vessel page is fetched once per
  season month, so one response with no JSON-LD empties that boat's month while
  the other three come back fine — and it looks exactly like a boat that sells
  nothing in May. Fourteen pages did this on 2026-08-28 and the site deleted 49
  real, bookable sailings, DUNE Longara's whole May among them. `scrape` now
  carries those departures forward from the last run for up to `CARRY_MAX_DAYS`,
  keeping each row's original `retrieved` date, and says so. The distinction is
  the source's own: a *Product* node with no *Event* nodes is a boat selling
  nothing that month and its absence is the answer; no structured data at all
  answers nothing. Probed rather than guessed: 13 of the 14 answered in full
  on the first retry, so `PARSE_ATTEMPTS = 2` asks again and `carry_unread` is
  the net under a page that fails twice. A markup parser for them is ruled
  out — the JSON-LD is there, it just occasionally is not served. Same rule as
  the fee book: a run that could not look at something knows nothing about it.
- **Places left is a total across every room at that price, and it is a claim.**
  The advertised price is the bottom of a cabin ladder — on 864 of 864 sailings
  read, with no exceptions — so "how many spots at that price" is summed over
  every room selling at it: **233 of 864 sailings list more than one**, and
  taking the first in document order reported the price as gone on thirteen
  Yachtiano sailings while 8 berths were on sale at exactly it. One unstated
  count makes the whole total unknown rather than a partial sum. The rule lives
  in `promote._berth_blocks` and nowhere else; `app.js` displays it and takes
  only a plain minimum of its own, for the cheapest rung still on sale.
  A count is **the seller's claim on its booking page**, not a verified
  number — true when read and stale by morning — so the page prints the date
  beside it and `cabins.yml` runs an hour after the refresh. Ordering is
  load-bearing: read a day apart, all 864 ladders sat up to 0.6% above their
  own row, which is the panel disagreeing with the number that opened it.
  `berths` is a **list of seller blocks** because a sailing has more than one
  seller, and both fill one ([#92]). A seller that states a count but no ladder
  gets no cabin list — *24 places* and *24 places at a stated price* are
  different claims, and only the second is a ladder.
- **Two sellers, two counts, and never one number.** A block carries both
  *at the advertised price* and *on the sailing*, because they are different
  questions and only a ladder answers the first. **Which one PADI's
  `availability` answers was measured, not assumed**: against liveaboard.com's
  whole-sailing total it is exact on 77% of the 584 sailings where both speak
  and within two berths on 88% — a day between the crawls — against 22% and a
  mean error of seven berths for the count at the advertised price. So it fills
  the second slot only. Putting it in the first would have relabelled *22
  aboard* as *22 at this price* on the 249 rows with no ladder to contradict
  it, which is why the page prints **aboard** and **places** as different words.
  They disagree outright on 24 sailings — 21 where PADI still sells berths
  liveaboard.com calls full — and both are printed under the name of whoever
  said it and the day they said it. The two crawls run on different days, so
  `berths_read` and `padi_berths_read` are separate: one date over two sellers
  dates half of them wrong.
- **A ladder that contradicts its row is not that row's ladder.** The advertised
  price *is* the bottom rung, on 864 of 864, so a rung far below it is not a
  cheaper berth on offer — it is last week's prices still on the shelf. The day
  the Red Sea Aggressors' 33% sale ended, the daily refresh re-priced 36
  sailings to list while the booking pages behind them had been read two days
  earlier, and the page offered a €1,588 berth on a €2,371 sailing: a price
  nobody can buy, published by the site that exists to catch that. `promote`
  drops such a ladder past `STALE_LADDER` (3%, which leaves room for the 0.6%
  the whole fleet drifts overnight) and **names every one it dropped** — the
  fix is a fresh `cabins.yml`, and nothing in the dataset can put it back. The
  other seller's count survives the drop: it is not a ladder and has nothing to
  contradict. **CI never saw this**, because the scheduled data commits push
  with `GITHUB_TOKEN` and GitHub does not run workflows on those — so a refresh
  can turn `main` red and the next human PR is what finds out.
- **A change report never drops a row silently.** `changes` caps its blocks and
  suppresses sub-unit price moves as source rounding — and says so, with a
  count, every time. A truncated list that does not admit it reads as "that was
  everything", which is exactly the failure this project exists to correct in
  other people. The same rule kills four false positives it used to report: an
  FX move is not a reprice, a vessel losing *every* departure is a failed fetch
  and not a cancelled season, a newly parsed field has not changed, and a
  currency switch is not a price move.
- **The committed seed must match `tools/make_seed.py`** — CI enforces it, so
  edit the generator, not the JSON.
- **The committed dataset must match what `promote` produces from the committed
  inputs** — CI enforces it too (`promote --check`). Promotion is pure, so the
  two agreeing is the statement "the published page is this code's output". A
  parser fix that never reaches `data/` is a green build and a site that is
  still slightly wrong, which is the failure this project exists to correct in
  other people.

## Sources

`padi.com` and `liveaboard.com` are the only permitted sources. Both are blocked
by the environment's network policy (see README); GitHub's runners are not.
**Do not write markup parsers for pages nobody has fetched** — run a probe on a
runner, read what came back, then parse. `tools/probe_*.py` write nothing and
exist for exactly this.

**`docs/sources/{host}.md` says where every fact lives** — URL, JSON-LD path or
selector, browser or not — and, with equal weight, what has already been ruled
out. Read it before opening a parser and before writing a probe. **A probe that
discovers something updates that file in the same commit**, negative results
included: a lead ruled out and not written down gets followed again, and a
stale map is worse than none.

Fees, gear prices and the specification table are rendered client-side, so
`tools/scrape_fees.py` drives a browser weekly and reads all three panels from
one page load. A capped run (`--limit N`) merges into the existing fee book
rather than replacing it: it knows nothing about the vessels it did not visit.

What one *trip* says about itself needs no browser and no crawl to find:
`tools/fetch_itineraries.py` builds every URL from ids already in
`data/archive.json` and fetches `/itinerary/getpopupv2` over plain HTTP, in the
daily refresh. Incremental — a trip already in `data/itineraries.json` is not
re-fetched — so the first run is ~314 requests and every run after it is a
handful. Everything else in the pipeline describes the boat's year.
`itineraries.yml` runs it alone, capped (`--limit N`), which is how a change to
the parser gets proved against three real trips before it is pointed at three
hundred of somebody else's pages. A capped run merges into the book, like
`scrape_fees.py --limit`.

**What PADI states has a cadence too, and it is `padi.yml`, daily.**
`tools/fetch_padi.py` ran in no workflow at all until then, and by that point
five published facts rested on it — the entry bar, the stated dive count, the
only fee book the 22 PADI-only vessels have, PADI's berth price on 654 sailings
and its berth count on 833. Everything else here reports its own failures; this
one's failure mode was **"nobody ran it"**, which nothing reports. `data/
padi_raw.json` stays gitignored and is *cached* on the runner instead, so an
ordinary run re-fetches only the itinerary listings and the sailings — ~80
requests against ~530 from cold — and every run uploads it as an artifact,
because a re-parse that needs the raw store should download it rather than
re-crawl 530 pages. **The book is rebuilt whole from that store**, so a cold
runner would rebuild it with zero trips and write it: green job, valid file,
five facts gone. `MIN_BOOK_RATIO` refuses that, the way `fetch_cabins.py`
refuses to rewrite its file after reading nothing.

**A deal is a promotion, and PADI publishes them without a browser.**
`/liveaboard-deals/` is an AngularJS shell — 272 KB, no prices, and a `page=`
that page 99 answers with page 1 — but `/api/v2/travel/promotions/` takes that
page's own `country` and `date` parameters over plain HTTP and pages honestly.
`tools/fetch_deals.py` reads it in the daily refresh and appends one entry per
day to `data/deals.json`, which is **committed**: a change log is a diff between
two committed days, and one computed from an artifact silently becomes "no
changes" once the artifact ages out. A re-run on a day already in the book makes
no request at all. Paging stops when a page adds no offer identity already seen
— never on a page number, because the page beside it proves a page number here
carries no information.

**The other seller's sales are kept the same way, as a projection.**
liveaboard.com publishes no listing, so its markdowns are read off the booking
pages by `fetch_cabins.py` — and `data/cabins.json` is rewritten whole each run,
which left the *bigger* signal (263 sailings on 22 boats against PADI's 13) able
to say what was on sale and not what had moved. `tools/derive_sales.py` reads
the committed cabin book and writes `data/sales.json`: one day per reading,
three fields per sailing, no request of its own. Two things are load-bearing. It
files each sailing under **that record's own `collected`**, never the book's
header, because a capped run merges and leaves most of the file older than its
header says. And a day is a **census, not a list of sales** — every sailing read
that day is in it, discounted or not, because the keys are the only thing
separating *not on sale* from *not looked at*; `promote` diffs over the sailings
both readings covered and prints the count of those it could not. Thirty days of
it would be 2 MB, so `KEEP_DAYS` is a week here and a month there.

`data/snapshots/` is gitignored; CI keeps it as a build artifact for 14 days.
`data/archive.json` is committed and holds every JSON-LD node each page
published, parsed or not — re-scraping recovers today's prices, never
yesterday's. Add fields to the parser freely; do not trim the archive to match
what the parser happens to read.

When the parser learns to read a field the archive already has,
`tools/reparse_candidate.py` fills it onto the committed candidate offline —
re-crawling 320 pages to re-read data already in the repository is both slow and
rude. It only ever fills, never overwrites, so it is a no-op after a fresh
crawl.
