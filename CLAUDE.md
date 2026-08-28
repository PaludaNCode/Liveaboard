# CLAUDE.md

Price-transparency site for Egyptian liveaboards. Takes an advertised berth
price and reassembles the real bill. See README.md for the domain.

## Commands

```bash
PYTHONPATH=src python3 -m unittest discover -s tests   # 482 tests, no deps
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

**Read the published page without checking anything out.** After a merge, to
see what actually shipped:

```bash
git show origin/main:site/index.html > /tmp/prod.html   # no checkout, no reset
```

Resetting the working branch onto `main` to look at it works and then leaves
the branch one merge commit "ahead" of its own remote, which reads as unpushed
work every time. `git show` answers the same question and disturbs nothing.

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
  difference between a bundled operator and one that bills at the dock.
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
  Only `title` is ever tidied, and it is tidied in exactly two ways.
  **One route, not a house style.** `BDE`/`BDE_TITLE` fold the seven spellings
  of Brothers/Daedalus/Elphinstone onto one; the pattern is anchored at both
  ends, so *Marine Park North: Brothers - Daedalus & Elphinstone* and
  *Brothers, Daedalus, Elphinstone & Safaga* are untouched. Twelve other groups
  differ the same way and are deliberately left as their operators wrote them —
  do not generalise this into a rule that rewrites every title, and never
  reorder words: nothing here can verify the order means something, and nothing
  may assume it means nothing. **Case only, otherwise.**
  `_settle_title_case` picks one spelling where titles differ *only* by
  capitalisation, and always one the operator actually used — never title-cased
  into a spelling nobody wrote, because the fleet is full of names a casing
  rule would ruin (*MY Odyssey*, *St. John's*, *SS Turkia*). Ties break
  alphabetically: `promote` is pure and CI compares its output byte for byte.
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
