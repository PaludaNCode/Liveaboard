# Liveaboard

**What an Egyptian liveaboard actually costs.**

Red Sea liveaboards advertise a berth price. That price is not the bill. Marine
park fees, port dues, a fuel surcharge, the visa, nitrox, gear, insurance and
the expected crew tip arrive afterwards, and they routinely add **30–60%** to
the number that sold you the trip.

This project mines trip data, reclassifies it, and republishes it priced the way
you actually pay for it.

```
Brothers, Daedalus & Elphinstone · 1–8 May 2027

  Berth (advertised price)                    €1,335
  Marine park fees            mandatory         €140
  Port & harbour dues         mandatory          €45
  Fuel surcharge              mandatory          €60
  Egypt visa on arrival       mandatory          €23
  Airport transfers           conditional        €40
  Crew gratuities             customary         €140
  ───────────────────────────────────────────────────
  True cost                                   €1,783      +34%
```

## Status

| Piece | State |
| --- | --- |
| True-cost engine | Working, 60 tests passing |
| Route / theme / level classification | Working, derived from dive-site lists |
| Static site, two views, live toggles | Working |
| Daily refresh workflow | Written, untested against live sources |
| `liveaboard.com` adapter | **Structural only** — never run against the live site |
| `padi.com` adapter | **Structural only** — never run against the live site |
| Dataset | **Seed estimates**, not real quotes |

The two source sites are blocked by the development environment's network
policy, so no scrape has ever run. Everything downstream of the scrape is
finished and tested; the parsers are scaffolding with a defined contract.

### Unblocking the sources

Open the cloud environment for editing, set **Network access** to **Custom**,
and add:

```text
liveaboard.com
*.liveaboard.com
padi.com
*.padi.com
```

Tick **"Also include default list of common package managers"** — without it the
allowlist becomes only those four lines and package installs break. Then start a
**new session**: the policy is applied when the container boots, so an existing
session stays blocked.

The scheduled workflow runs on GitHub's runners, which are not subject to that
policy, so the daily refresh can work even while a sandbox cannot reach the
sources.

## Usage

No dependencies. Python 3.11+.

```bash
PYTHONPATH=src python3 -m liveaboard.cli check    # validate and summarise
PYTHONPATH=src python3 -m liveaboard.cli build    # write site/index.html
PYTHONPATH=src python3 -m liveaboard.cli scrape   # refresh from the sources
PYTHONPATH=src python3 -m unittest discover -s tests
```

`build` emits a single self-contained HTML file — CSS and JavaScript inlined, no
CDN, no build step. Open it from disk or drop it on any host.

## How it works

```
padi.com ─┐
          ├─► adapters ─► candidate.json ─► dataset ─► pricing ─► site/index.html
liveaboard.com ─┘            │                  │          │
                             │                  │          └─ true cost, honesty score
                             │                  └─ classification from dive sites
                             └─ raw page snapshots (audit trail)
```

### The pricing model

Every cost line carries a **tier** that decides whether it counts:

| Tier | Meaning | Counted by default |
| --- | --- | --- |
| `base` | The advertised price | yes |
| `mandatory` | No opt-out — park fees, port dues, fuel, visa | yes |
| `conditional` | Real for most, genuinely avoidable — nitrox, gear | follows a toggle |
| `customary` | Not owed, universally expected — crew tips | yes |
| `optional` | Single supplement, courses, alcohol | no |

A fee an operator **includes** still appears in the breakdown, at zero. Deleting
the line would hide exactly the difference this site exists to show, and an
operator that bundles its park fees deserves the visible credit.

### The honesty score

What share of the full bill the advertised price discloses. It is measured
against a **fixed** basket of every conditional cost, not against the visitor's
toggles — a score that moved when someone clicked "nitrox" would rank operators
by the visitor's mood rather than by their pricing.

Two boats can reach the same true cost and score very differently. That is the
point.

### Classification

Routes, themes and the experience bar are **derived from each trip's dive-site
list**, not copied from operator marketing. "Simply the Best", "Ultimate Red
Sea" and "BDE" all describe the same water; the site labels them the same way.

Site names are folded before matching, so `Sha'ab` and `Shaab`, `St John's` and
`St Johns` agree. Routes belong to cruising-ground families, so a southern week
touching both St John's and Rocky Island is *Deep South*, not a combination —
while a genuine three-region crossing is.

Seasonal themes are cross-referenced against the departure month: a May trip is
not sold on August's hammerheads.

### Provenance

Every price and every fee carries where it came from and when:

- `scraped` — read from a source site on the recorded date
- `operator_stated` — from an operator's published terms
- `seed_estimate` — a researched placeholder, never a quote
- `derived` — computed here

The site renders a prominent banner while any displayed price is unverified. A
transparency site that cannot account for its own numbers has no standing to
complain about anyone else's.

### Currency

Everything displays in euro. Amounts quoted in another currency are marked as
converted, with the rate and its date, because a conversion is a weaker claim
than a quote.

## Data and copyright

The scrape extracts **facts** — dates, routes, dive sites, prices,
prerequisites, inclusions. Descriptions and classifications are written here.
No marketing copy or photography is reproduced. The fetcher reads `robots.txt`
and obeys it, honours `Crawl-delay`, identifies itself, and defaults to a five
second gap between requests.

Seed operator and boat names are explicit placeholders. Attaching an invented
price to a real business on a public page would be a small lie of exactly the
kind this project exists to expose.

## Layout

```
src/liveaboard/
  taxonomy.py     controlled vocabularies — fee codes, tiers, routes, themes
  money.py        Money, currency, FX with attribution
  models.py       Operator, Boat, Itinerary, Departure, FeeItem, Provenance
  pricing.py      the true-cost engine and the honesty score
  classify.py     route / theme / level derivation from dive sites
  dataset.py      loading, referential integrity, queries
  render.py       dataset -> self-contained static site
  cli.py          build / check / scrape
  scrape/
    base.py       polite fetcher, snapshot trail, adapter contract
    jsonld.py     schema.org extraction
    liveaboard_com.py, padi_com.py
templates/        index.html, style.css, app.js — inlined at build time
tools/make_seed.py
data/seed/        the seed dataset
tests/            60 tests, stdlib unittest, no dependencies
```

## Next

1. Allowlist the two hosts and run `scrape --source liveaboard.com --limit 1`.
2. Read the snapshot it writes, then finish `parse()` against real markup.
3. Replace the seed dataset; the banner disappears on its own once every price
   is `scraped`.
4. Let the daily workflow accumulate history — every run is a commit, so
   `git log -p data/` becomes a price record, and "this trip was €200 cheaper in
   March" falls out for free.
