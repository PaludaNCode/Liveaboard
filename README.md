# Liveaboard

**What an Egyptian liveaboard actually costs.**

Red Sea liveaboards advertise a berth price. That price is not the bill. Park
fees, port dues, fuel, visa, nitrox, gear and the expected crew tip arrive
afterwards and routinely add **30–60%**. This mines trip data from padi.com and
liveaboard.com, reclassifies it, and reprices it the way you actually pay.

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

Scope: Egypt, May–August 2027. Euro only.

## Status

Everything downstream of the scrape works and is tested. **The scrape itself is
a stub** — both source sites are blocked by the environment's network policy and
have never been reached, so the adapters have a polite fetcher, snapshot trail
and JSON-LD extraction but no markup parser. The dataset ships as researched
**seed estimates** with placeholder operator names, and the page says so in a
banner until every price is `scraped`.

### Unblocking the sources

Environment → **Network access** → **Custom**, then add:

```text
liveaboard.com    *.liveaboard.com    padi.com    *.padi.com
```

Tick **"Also include default list of common package managers"**, or package
installs break. Then start a **new session** — the policy applies at container
boot. GitHub's runners are not behind this policy, so the daily workflow can
scrape even when a sandbox cannot.

## Usage

Python 3.11+, no dependencies.

```bash
PYTHONPATH=src python3 -m liveaboard.cli check    # validate and summarise
PYTHONPATH=src python3 -m liveaboard.cli build    # write site/index.html
PYTHONPATH=src python3 -m liveaboard.cli scrape   # refresh from the sources
PYTHONPATH=src python3 -m unittest discover -s tests
```

`build` emits one self-contained HTML file — CSS and JS inlined, no CDN.

## Design

**Fee tiers** decide what counts toward the total:

| Tier | Example | Counted |
| --- | --- | --- |
| `base` | advertised price | yes |
| `mandatory` | park fees, port dues, fuel, visa | yes |
| `conditional` | nitrox, gear, insurance, transfers | follows a toggle |
| `customary` | crew tips | yes |
| `optional` | single supplement, courses, alcohol | no |

A fee an operator **includes** still appears in the breakdown, at zero —
deleting it would hide the exact difference this site exists to show.

**Honesty score**: what share of the bill the headline discloses, measured
against a fixed basket rather than the visitor's toggles, so it describes the
operator rather than the visitor. Two boats can reach the same true cost and
score 60% vs 83%.

**Classification** is derived from each trip's dive-site list, not from operator
marketing, so "Simply the Best", "Ultimate Red Sea" and "BDE" get one label.

**Provenance**: every price and fee records where it came from and when
(`scraped`, `operator_stated`, `seed_estimate`, `derived`).

The scrape takes **facts** — dates, routes, sites, prices, prerequisites. All
descriptions are written here; no marketing copy or photography is reproduced.
The fetcher obeys `robots.txt` and `Crawl-delay`, and defaults to 5s between
requests.

## Layout

```
src/liveaboard/   taxonomy, money, models, pricing, classify, dataset, render, cli
        scrape/   polite fetcher, JSON-LD, liveaboard_com, padi_com
templates/        index.html + style.css + app.js, inlined at build time
tools/make_seed.py
data/seed/        the seed dataset
tests/            60 tests, stdlib unittest
```

## Next

1. Allowlist the hosts, run `scrape --source liveaboard.com --limit 1`.
2. Read the snapshot, finish `parse()` against real markup.
3. Replace the seed data; the banner clears itself.
4. Let the daily workflow accumulate history — every run is a commit, so
   `git log -p data/` becomes a price record.
