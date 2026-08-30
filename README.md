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

Live on real data: **1,122 departures, 402 itineraries, 77 boats, 47
operators**, every price `scraped`.

**Both sources are in use.** This said "padi.com is not wired up" until
2026-08-30, by which point PADI Travel was supplying a berth price on 654
sailings — 53 of which it is the only seller of — a berth count on 833, the
entry bar and stated dive count on 441 trips, and the only fee book the 22
vessels liveaboard.com sells no berths on have. Neither seller is ever allowed
to speak for the other: a row states a discount only from the seller whose fare
it prints, and a row PADI alone lists carries no second price at all.

Prices and availability come from a nightly crawl, and PADI's from a second one
half an hour later (`padi.yml`). Fees, rental-gear prices and the vessel
specification table need a browser — the site renders them client-side — so
they come from a weekly Playwright run and are keyed by vessel, because they do
not change with the month.

Both sources are reachable locally since the allowlist landed (#1), and from
GitHub's runners. Either way, anything about what a source actually returns is
settled by running a `tools/probe_*.py` against it and reading the answer, never
by guessing at markup.

Two paths this crawl uses are **disallowed by liveaboard.com's robots.txt**, and
only a formatting bug in that file lets `can_fetch()` say otherwise. Carrying on
is a call the owner took on 2026-08-30 rather than an oversight, and it is
conditional on the crawl staying small — 2 seconds a request, once a day, from
one runner. The reasoning, what reversing it would cost, and the two options
weighed and not taken are in
[`docs/sources/liveaboard.com.md`](docs/sources/liveaboard.com.md)
under *robots.txt, and the blank line* (#121).

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

The suite holds two kinds of test and the command above runs both. Some assert
against **committed data** — that the advertised price really is the bottom of
every shipped cabin ladder, that the footer's vessel counts are the dataset's.
Those are a *publication* gate: they are reached through `tests/published.py`,
and the jobs that fetch skip them on their pre-flight run
(`LIVEABOARD_TESTS=code`) and run them again before committing. Put in front of
a fetch they deadlock it — a stale cabin book failed the suite in front of the
only job able to refresh that book — and a run that fetches and then refuses to
publish is recoverable where one that refuses to fetch is not.

CI's list lives in `.github/actions/checks` rather than in `ci.yml`, because
every job that commits data runs it too, and the commit-and-push tail lives in
`.github/actions/publish` for the same reason. They push with the default
`GITHUB_TOKEN`, and GitHub does not trigger workflows on those pushes, so that
step is the only CI a scheduled commit will ever get.

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
Sites come from the operator's own description of the trip, then its region
list, then the trip title, then — last, and only where all three are silent —
the second seller's account of the same week. **399 of 402** itineraries name
reefs, one names only a direction and says so, and two name neither and stay
blank rather than being guessed at (#52, #113). The ordering is the point: a
source is never merged into one above it, because PADI's blurb says Elphinstone
and Brothers "are quite distant from one another" on a week that visits neither
together, and unioning that in is how a St John's week once got badged BDE.

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
templates/        index.html + style.css + app.js + icon.svg, inlined at build time
tools/            make_seed, fetch_fx, fetch_itineraries, fetch_deals,
                  fetch_cabins, derive_sales, fetch_padi,
                  scrape_fees, reparse_candidate, probe_*
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
| `data/padi.json` | what PADI states per trip: the entry bar, the dive count, its own fee book | yes |
| `data/padi_departures.json` | the same sailings as PADI sells them: one price and berth count per boat and day | yes |
| `data/padi_raw.json` | every field each PADI response published, parsed or not | no — gitignored, cached on the runner, CI artifact for 14 days |
| `data/deals.json` | what PADI Travel is discounting, one entry per day it was read | yes |
| `data/sales.json` | what liveaboard.com's booking pages were advertising, one entry per day they were read | yes |
| `data/CHANGES.md` | what moved on each refresh, newest first | yes |
| `data/snapshots/` | raw pages | no — gitignored, CI artifact for 14 days |

The dataset is **regenerated, not edited**. `promote` is pure — candidate, fees,
fees, facts, the per-trip book and FX in, dataset out, no network — so `data/egypt-2027.json` must always
be what the committed code produces from the committed inputs. CI checks it
(`promote --check`), and a merge touching `src/liveaboard/` re-promotes on main,
so the published page is never more than one merge behind the parser. Without
that, a parser fix passes CI and changes nothing until an unrelated crawl runs
(#53).

`deals.json` is committed for the same reason and a sharper one: the deals panel
carries a **change log**, and a change log is a diff between two committed days.
Re-reading the listing recovers today's offers and never yesterday's, so a log
computed from a build artifact would go quietly silent the moment the artifact
aged out — reporting "no changes" rather than "nothing to compare against".

`sales.json` is that rule applied to the other seller. liveaboard.com publishes
no deals listing at all — it strikes the list price through beside every
discounted cabin — and `cabins.json` is rewritten whole each run, so the larger
of the two signals could say what was on sale and not what had moved. It is a
projection of the cabin book onto the three fields a diff needs (advertised
price, the list price beside it, the currency), written by
`tools/derive_sales.py` and filed under the day each booking page was read. The
day the Red Sea Aggressors' 33% sale ended, PADI's half reported three offers
withdrawn; this one reports the 36 sailings that actually moved.

`archive.json` exists because current prices can always be re-scraped and past
ones cannot. It carries ratings, cabin counts, occupancy, amenities and
remaining capacity — none of which the site uses today — so a question asked
next month can still be put to this month's data. Every run is a commit, so
`git log -p data/` is the history.

### When a page cannot be read

A vessel page is fetched once per season month, so one response with no
structured data empties that boat's month while the other three come back
fine — and it looks exactly like a boat that sells nothing in May. On
2026-08-28 fourteen pages did this and the site deleted 49 real, bookable
sailings, DUNE Longara's whole May among them.

The source itself distinguishes the two, and now so does the scrape: a
`Product` node with no `Event` nodes is a boat selling nothing that month, and
its absence is the answer; no structured data at all answers nothing.

A probe re-read all fourteen and **thirteen answered in full on the first
retry** — the fourteenth was a genuinely empty month. So the failure is the
response and not the page: the crawl now asks a second time, which recovers
them. A markup parser is ruled out; the JSON-LD is there, it just occasionally
is not served.

The barren skip list turned out to lose trips the same way, with nothing going
wrong at all: it holds a vessel back for a week to save four requests, and
while it does, that vessel's departures were dropped and reported as
withdrawn. AVO's and Blue's three sailings went that way, and a probe found
all three still on sale. A skipped page is now recorded exactly like a failed
one, because the consequence is identical: the run did not look.

Where a page goes unread — skipped, or answering nothing twice — `scrape`
carries the previous run's departures forward for up to a fortnight, keeping each row's original `retrieved` date so the page still says
when every price was last read, and noting each carried page in the candidate's
warnings. After that they drop out — a page unread for two weeks is one we can
no longer claim to see. The same rule the fee book already followed: a run that
could not look at something knows nothing about it.

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

And a run that moved nothing writes no line at all. The page carries the minute
it was built, so `site/index.html` differs on every run whether or not any data
did; seven scheduled jobs a day therefore committed seven times a day
regardless, each one a log entry that moved no price. The publish action now
compares the page with that stamp normalised and commits nothing when the stamp
is the only difference, which is what lets the log be read as a price history
rather than skimmed for the entries that mean something.

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

