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

**→ [paludancode.github.io/Liveaboard](https://paludancode.github.io/Liveaboard/)**
— goes live once this branch reaches `main` and **Settings → Pages → Source** is
set to **GitHub Actions**. Locally: `build`, then open `site/index.html`.

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

**Comparison, not a scoreboard**: the lists sort by true cost, cost per night,
advertised price, and how much lands after the headline figure. There is no
per-operator rating — two boats reaching the same true cost by different routes
show that in their breakdowns, which is the part a diver can act on.

**Unstated is not zero**: a vessel listing only optional extras gets no true
cost at all. Every Egyptian liveaboard pays park and port fees, so silence
means either bundled or collected at the dock, and the listing does not say
which.

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
tests/            stdlib unittest, no dependencies
```

### What is kept

| file | holds | committed |
|---|---|---|
| `data/egypt-2027.json` | the published dataset | yes |
| `data/candidate.json` | the raw scrape, before promotion | yes |
| `data/fees.json` | fee book **and** the disclosure text each parse was made from | yes |
| `data/archive.json` | every JSON-LD node each page published, parsed or not | yes |
| `data/snapshots/` | raw pages | no — gitignored, CI artifact for 14 days |

`archive.json` exists because current prices can always be re-scraped and past
ones cannot. It carries ratings, cabin counts, occupancy, amenities and
remaining capacity — none of which the site uses today — so a question asked
next month can still be put to this month's data. Every run is a commit, so
`git log -p data/` is the history.

## Next

1. Allowlist the hosts, run `scrape --source liveaboard.com --limit 1`.
2. Read the snapshot, finish `parse()` against real markup.
3. Replace the seed data; the banner clears itself.
4. Let the daily workflow accumulate history — every run is a commit, so
   `git log -p data/` becomes a price record.
