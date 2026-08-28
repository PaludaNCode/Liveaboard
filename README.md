# Liveaboard

**What an Egyptian liveaboard actually costs.**

Red Sea liveaboards advertise a berth price. That price is not the bill. Park
fees, port dues, fuel, visa, nitrox, gear and the expected crew tip arrive
afterwards and routinely add **30–60%**. This mines trip data from padi.com and
liveaboard.com and reprices it the way you actually pay.

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
  Total                                       €1,783      +34%
```

Scope: Egypt, May–August 2027. Euro only.

**→ [paludancode.github.io/Liveaboard](https://paludancode.github.io/Liveaboard/)**
Locally: `build`, then open `site/index.html`.

## Status

Live on real data: **878 departures, 314 itineraries, 67 boats, 42 operators**,
every price `scraped`. padi.com is not wired up; liveaboard.com is the only
source in use.

Prices and availability come from a nightly crawl. Fees, rental-gear prices and
the vessel specification table need a browser — the site renders them
client-side — so they come from a weekly Playwright run and are keyed by vessel,
because they do not change with the month.

A sandbox cannot reach liveaboard.com (network policy, see #1); GitHub's runners
can. So anything about what the source actually returns is settled by running a
`tools/probe_*.py` on a runner, never by guessing at markup.

**Where each fact comes from is written down**, per source, in
[`docs/sources/`](docs/sources/) — the URL, the JSON-LD path or selector, and
whether reading it needs a browser. Read that before opening a parser, and
before probing for something: it also records what has already been ruled out,
which is the half a code-reading exercise cannot recover.

## Usage

Python 3.11+, no dependencies.

```bash
PYTHONPATH=src python3 -m liveaboard.cli check    # validate and summarise
PYTHONPATH=src python3 -m liveaboard.cli build    # write site/index.html
PYTHONPATH=src python3 -m liveaboard.cli scrape   # refresh from the sources
PYTHONPATH=src python3 -m unittest discover -s tests
```

`build` emits one self-contained HTML file — CSS, JS and data all inlined, no
CDN, nothing fetched at runtime. Type is the visitor's own system font: a
webfont link in `<head>` is render-blocking, which cost 13 seconds to first row
whenever Google was slow to answer and 0.6 seconds when it was not there at all
(#59). That makes page weight a first-class concern: there is no lazy second request to hide
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

**Comparison, not a scoreboard**: the table sorts by total, price per dive,
advertised price, and how much lands after the headline figure. There is no
per-operator rating — two boats reaching the same total by different routes
show that in their breakdowns, which is the part a diver can act on. The
operator is named and filterable, because one company's boats may all bundle
nitrox while another's all bill for it; that is a fact about prices. Scoring
them against each other is the thing that was tried and removed.

**Unstated is not zero**: a vessel listing only optional extras gets no true
cost at all. Every Egyptian liveaboard pays park and port fees, so silence
means either bundled or collected at the dock, and the listing does not say
which.

**Dive sites, not route labels.** Filtering is on the sites themselves — a BDE
week is one naming Brothers, Daedalus and Elphinstone — because a name for a
set of sites is a layer that can be wrong and answers nothing the sites do not.
Sites are read from the trip title, which is all the source publishes: 251 of
314 name reefs, 40 name only a direction and say so, and 23 name neither and
stay blank rather than being guessed at (#52).

**Provenance**: every price and fee records where it came from and when
(`scraped`, `operator_stated`, `seed_estimate`, `derived`).

The scrape takes **facts** — dates, ports, sites, prices, prerequisites. All
descriptions are written here; no marketing copy or photography is reproduced.
The fetcher obeys `robots.txt` and `Crawl-delay`, taking the larger of that and
its own floor. liveaboard.com states none, so the crawl runs at 2s.

## Layout

```
src/liveaboard/   taxonomy, money, models, pricing, changes, promote,
                  dataset, render, cli
        scrape/   polite fetcher, JSON-LD, liveaboard_com, padi_com,
                  itinerary, fees, gear, vessel   (the last three need a browser)
templates/        index.html + style.css + app.js, inlined at build time
tools/            make_seed, fetch_fx, fetch_itineraries, scrape_fees,
                  reparse_candidate, probe_*
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
| `data/itineraries.json` | what each *trip* says about itself: reefs, dive count, group size, entry bar | yes |
| `data/CHANGES.md` | what moved on each refresh, newest first | yes |
| `data/snapshots/` | raw pages | no — gitignored, CI artifact for 14 days |

The dataset is **regenerated, not edited**. `promote` is pure — candidate, fees,
fees, facts, the per-trip book and FX in, dataset out, no network — so `data/egypt-2027.json` must always
be what the committed code produces from the committed inputs. CI checks it
(`promote --check`), and a merge touching `src/liveaboard/` re-promotes on main,
so the published page is never more than one merge behind the parser. Without
that, a parser fix passes CI and changes nothing until an unrelated crawl runs
(#53).

`archive.json` exists because current prices can always be re-scraped and past
ones cannot. It carries ratings, cabin counts, occupancy, amenities and
remaining capacity — none of which the site uses today — so a question asked
next month can still be put to this month's data. Every run is a commit, so
`git log -p data/` is the history.

### Noticing that something moved

The history was always there and nobody could read it: five new departures
show up as `886` where yesterday said `881`, and a two-hundred-dollar rise is
a two-megabyte JSON diff. `changes` turns two datasets into a few lines —
which boats and trips appeared, which departures were added or withdrawn,
which fares moved and by how much, which fees changed, what sold out.

```bash
PYTHONPATH=src python3 -m liveaboard.cli changes                    # vs HEAD~1
PYTHONPATH=src python3 -m liveaboard.cli changes --revision HEAD~7  # a week
PYTHONPATH=src python3 -m liveaboard.cli changes --headline         # one line
```

The daily refresh writes it to three places, because a workflow run summary
disappears when the run ages out: the run summary, `data/CHANGES.md`
(committed, newest first), and the subject of the data commit itself — so
`git log --oneline data/` reads as the changelog rather than 23 identical
lines saying `data: daily refresh`.

Four distinctions decide whether it is worth reading, and all four are
false positives it used to report: a euro figure moving because the ECB moved
is not an operator repricing; a fare moving by one dollar is the source
re-rounding (174 of them in one run); a vessel that lost *every* departure at
once is a failed fetch, not a cancelled season; and a field a parser has just
learned to read has not changed — the first run after `availability` was
parsed announced 126 sailings as newly sold out, and nobody had looked before.

## Next

Tracked in [issues](https://github.com/PaludaNCode/Liveaboard/issues). The ones
that would change what the page can say:

- **#52** 23 trips name no dive site and no direction, so the column a diver
  filters on is blank for them. The trip detail is loaded by something other
  than the `#tourid=` hash; finding that endpoint would give a per-trip site
  list for all 314, not just the 23.
- **#47** the gear spread is €40–333 a trip and unexplained. Whether the boats
  with the cheapest berths charge most for kit is answerable from data already
  committed, and would be a real finding for a price-transparency site.
- **#6** price history from the git log. Every run is a commit, so *"this trip
  was €200 cheaper in March"* costs only the reading — and #48 built the diff.

