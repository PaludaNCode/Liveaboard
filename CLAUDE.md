# CLAUDE.md

Price-transparency site for Egyptian liveaboards. Takes an advertised berth
price and reassembles the real bill. See README.md for the domain.

## Commands

```bash
PYTHONPATH=src python3 -m unittest discover -s tests   # 307 tests, no deps
PYTHONPATH=src python3 -m liveaboard.cli check         # validate + summarise
PYTHONPATH=src python3 -m liveaboard.cli build         # -> site/index.html
python3 tools/make_seed.py                             # regenerate seed data
```

Re-promoting is offline and takes seconds — do it after any `promote.py` change
rather than waiting for a crawl (see #53):

```bash
PYTHONPATH=src python3 -m liveaboard.cli promote --candidate data/candidate.json \
  --fees data/fees.json --facts data/operator_facts.json --fx data/fx.json \
  --out data/egypt-2027.json
```

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
- **Classification derives from dive sites**, not from trip names. An explicit
  value in the dataset wins; a stated safety requirement is never softened.
- **A vessel summary is the boat's year-round brochure, not the trip's.** Never
  a source for where one trip goes: Aphrodite's names St John's, so its *North
  Wrecks* week would be tagged with a reef 600 km away.
- **`Itinerary.name` is identity; `Itinerary.title` is presentation.** The id is
  built from `name`, and two sailings differing only by port are two trips.
- **Zero runtime dependencies**, stdlib only, and the site stays one
  self-contained HTML file with no CDN. Tests use `unittest`, not pytest. One
  file makes page weight load-bearing: nothing is lazily fetched, so anything
  written per departure ships 878 times. Fees belong to the itinerary.
- **The committed seed must match `tools/make_seed.py`** — CI enforces it, so
  edit the generator, not the JSON.

## Sources

`padi.com` and `liveaboard.com` are the only permitted sources. Both are blocked
by the environment's network policy (see README); GitHub's runners are not.
**Do not write markup parsers for pages nobody has fetched** — run a probe on a
runner, read what came back, then parse. `tools/probe_*.py` write nothing and
exist for exactly this.

Fees, gear prices and the specification table are rendered client-side, so
`tools/scrape_fees.py` drives a browser weekly and reads all three panels from
one page load. A capped run (`--limit N`) merges into the existing fee book
rather than replacing it: it knows nothing about the vessels it did not visit.

`data/snapshots/` is gitignored; CI keeps it as a build artifact for 14 days.
`data/archive.json` is committed and holds every JSON-LD node each page
published, parsed or not — re-scraping recovers today's prices, never
yesterday's. Add fields to the parser freely; do not trim the archive to match
what the parser happens to read.
