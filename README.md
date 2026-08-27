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
Locally: `build`, then open `site/index.html`.

## Status

Live on real data: **886 departures, 317 itineraries, 67 boats**, every price
`scraped`. padi.com is not wired up; liveaboard.com is the only source in use.

Prices and availability come from a nightly crawl. Fees, rental-gear prices and
the vessel specification table need a browser — the site renders them
client-side — so they come from a weekly Playwright run and are keyed by vessel,
because they do not change with the month.

A sandbox cannot reach liveaboard.com (network policy, see #1); GitHub's runners
can. So anything about what the source actually returns is settled by running a
`tools/probe_*.py` on a runner, never by guessing at markup.

## Usage

Python 3.11+, no dependencies.

```bash
PYTHONPATH=src python3 -m liveaboard.cli check    # validate and summarise
PYTHONPATH=src python3 -m liveaboard.cli build    # write site/index.html
PYTHONPATH=src python3 -m liveaboard.cli scrape   # refresh from the sources
PYTHONPATH=src python3 -m unittest discover -s tests
```

`build` emits one self-contained HTML file — CSS and JS inlined, no CDN. That
makes page weight a first-class concern: there is no lazy second request to hide
behind, so a visitor on a phone in a dive shop downloads all of it before seeing
a row. Fees are written once per itinerary rather than once per departure, which
is what they are a property of.

## Design

**Fee tiers** decide what counts toward the total:

| Tier | Example | Counted |
| --- | --- | --- |
| `base` | advertised price | yes |
| `mandatory` | park fees, port dues, fuel, visa | yes |
| `conditional` | nitrox, gear | follows a toggle |
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
Sites are read from the trip title, which is all the source publishes: 254 of
317 name reefs, 40 name only a direction and say so, and 23 name neither and
stay blank rather than being guessed at (#52).

**Provenance**: every price and fee records where it came from and when
(`scraped`, `operator_stated`, `seed_estimate`, `derived`).

The scrape takes **facts** — dates, routes, sites, prices, prerequisites. All
descriptions are written here; no marketing copy or photography is reproduced.
The fetcher obeys `robots.txt` and `Crawl-delay`, taking the larger of that and
its own floor. liveaboard.com states none, so the crawl runs at 2s.

## Layout

```
src/liveaboard/   taxonomy, money, models, pricing, classify, promote,
                  dataset, render, cli
        scrape/   polite fetcher, JSON-LD, liveaboard_com, padi_com,
                  fees, gear, vessel        (the last three need a browser)
templates/        index.html + style.css + app.js, inlined at build time
tools/            make_seed, fetch_fx, scrape_fees, probe_*
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

Tracked in [issues](https://github.com/PaludaNCode/Liveaboard/issues). The ones
that would change what the page can say:

- **#48** report what changed since the last run — new trips, gone trips, moved
  prices. The git history is already the record; nothing reads it back.
- **#35** name the operator. All 886 archived events carry `organizer.name`
  naming 42 companies, and the parser discards the field.
- **#50** price per dive is derived from nights on 256 of 317 trips, so it
  carries no information its own denominator did not invent.
