# CLAUDE.md

Price-transparency site for Egyptian liveaboards. Takes an advertised berth
price and reassembles the real bill. See README.md for the domain.

## Commands

```bash
PYTHONPATH=src python3 -m unittest discover -s tests   # 348 tests, no deps
PYTHONPATH=src python3 -m liveaboard.cli check         # validate + summarise
PYTHONPATH=src python3 -m liveaboard.cli build         # -> site/index.html
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

Take the defaults. `refresh.yml`, `fees.yml`, `promote.yml` and the CI check all
promote on them, so one canonical set of inputs lives in `cli.py` and cannot
drift apart across four workflows.

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
