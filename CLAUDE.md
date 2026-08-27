# CLAUDE.md

Price-transparency site for Egyptian liveaboards. Takes an advertised berth
price and reassembles the real bill. See README.md for the domain.

## Commands

```bash
PYTHONPATH=src python3 -m unittest discover -s tests   # 60 tests, no deps
PYTHONPATH=src python3 -m liveaboard.cli check         # validate + summarise
PYTHONPATH=src python3 -m liveaboard.cli build         # -> site/index.html
python3 tools/make_seed.py                             # regenerate seed data
```

## Invariants

Break these and the site starts lying quietly rather than failing loudly.

- **Never invent a price.** Every price and fee needs a `Provenance`. A parser
  that cannot find a number returns `None`; it does not guess. `seed_estimate`
  triggers the "not real quotes" banner — do not suppress it.
- **`DEFAULT_ON_TIERS` is mirrored in `templates/app.js`.** Change the tier
  rules in `taxonomy.py` and you must change the JS to match.
- **Normalisation happens in Python only.** Fee basis (per night / day / dive)
  and FX conversion resolve in `pricing.py`; the browser only sums lines that
  are switched on. Do not add pricing logic to the JS.
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
- **Zero runtime dependencies**, stdlib only, and the site stays one
  self-contained HTML file with no CDN. Tests use `unittest`, not pytest.
- **The committed seed must match `tools/make_seed.py`** — CI enforces it, so
  edit the generator, not the JSON.

## Sources

`padi.com` and `liveaboard.com` are the only permitted sources. Both are
blocked by the environment's network policy (see README), so the adapters in
`src/liveaboard/scrape/` are structural. **Do not write markup parsers for
pages nobody has fetched** — run a scrape, read the snapshot, then parse.

`data/snapshots/` is gitignored; CI keeps it as a build artifact for 14 days.
`data/archive.json` is committed and holds every JSON-LD node each page
published, parsed or not — re-scraping recovers today's prices, never
yesterday's. Add fields to the parser freely; do not trim the archive to match
what the parser happens to read.
