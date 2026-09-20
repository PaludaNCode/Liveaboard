# Reading a third seller: divebooker.com

The site reads two sellers. This is what it takes to read a third, in the order
the steps have to happen, with the thing that decides each one.

It is written the way `docs/implementing.md` says a change gets made here:
measure before designing, point the guard at the old code, write the negatives
down, record the gate either side. What is different about a new source is that
the first six stages produce **no code at all** — they produce facts, and a
parser written before them is a parser written against a guess. This project
has the scars for that: a fee parser proved against one hand-trimmed fixture
matched nothing on six real trips, and a crawler with an unscoped link pattern
walked off into Indonesia and the Rhine.

## What is already done

- `tools/probe_divebooker.py` — the probe. Writes nothing, capped at
  `--max-requests`, paced at the five-second default this project gives a host
  nobody has read the robots.txt of. Its machinery is proved: run against
  `--host www.liveaboard.com` it reproduces this repository's own known finding
  — 31 refused paths in the file, `can_fetch()` saying yes to all 31.
- The `divebooker` job in `.github/workflows/probe.yml` — the only place it can
  run, see stage 1.
- `docs/sources/divebooker.com.md` — the map, currently a list of questions
  with `unverified` against every row. Stage 6 is where it stops being that.

## Stage 0 — the decision that is not technical

`CLAUDE.md` names two permitted sources, and the invariant above them is *two
sellers, neither of them the house*. A third seller is the owner's call, and it
is worth making before any of the rest, because it costs more than a parser:

| What assumes two | Where |
|---|---|
| `best().cheaper` returns `"liveaboard"` or `"padi"` | `pricing.py`, `app.js` |
| Metric keys `.lav` and `.padi` | `render.py`, `templates/app.js` |
| `berths_read` / `padi_berths_read` — two crawls, two dates | dataset, page |
| A row states `pct` from the seller whose fare it prints | `promote._sale_for` |
| The Seller column names one of two | `templates/app.js` |
| `_name_the_runs` folds one book's offers onto the other's runs | `promote.py` |

None of those is hard. All of them are places where a third value silently
becomes a fourth state nobody wrote a word for, and the failure mode of this
project is the site lying quietly rather than falling over.

**The cheap version of this stage:** decide up front whether divebooker is a
third *seller* (its own fare, its own column, its own crawl date) or a third
*opinion* (fills blanks in fields nothing else answers — dive counts, entry
bars, reefs — and never prints a price). The second is a fraction of the work
and answers a different question. Write down which, before stage 1.

## Stage 1 — reach it

The sandbox cannot. Measured 2026-09-20: `CONNECT divebooker.com:443` comes
back `403 Forbidden` from the egress proxy, for both the apex and `www`. So
there are two ways forward and one of them is not "try again":

1. **A runner.** `.github/workflows/probe.yml` → *Run workflow* → the
   `divebooker` job. This needs no permission from anybody and is where every
   fact below should be established first.
2. **The environment's allowlist**, if the same work is wanted locally: add
   `divebooker.com` and `*.divebooker.com` to the Custom network allowlist and
   start a new session. `SourceAdapter.preflight` already prints exactly this
   sentence, for exactly this reason.

**Pass condition:** the probe prints `robots.txt reachable`. Anything else and
every stage below is unanswerable — do not proceed on a page fetched from a
search index, a cache, or a memory of what such sites usually look like.

## Stage 2 — read robots.txt with both eyes

The probe prints the file's own refusals and what `urllib.robotparser` makes of
them, side by side, because those two can disagree. On liveaboard.com they
disagree completely — a blank line inside the record orphans all 31 rules and
`can_fetch()` answers yes to every one of them. That site's entry in
`docs/sources/liveaboard.com.md` under *robots.txt, and the blank line* is what
a deliberate decision about it looks like, taken 2026-08-30 and written down
with the price of reversing it.

**What to record:** the stated `Crawl-delay` (or that there is none), every
`Disallow:` path that touches something worth reading, and whether the parser
repeats them. If the probe prints `MISMATCH`, `can_fetch()` is not permission
here and nobody may later quote it as though it were.

**What to change if there is a stated delay:** nothing. `crawl_delay` already
takes the larger of the site's number and ours. `CHECKED_HOSTS` is for the
opposite case — a host whose file somebody has read, where a pace *faster* than
the five-second default is a choice with a reason beside it.

## Stage 3 — the inventory

The sitemap is the only list of what a site sells that is not a guess. The
probe buckets every `<loc>` by its first two path segments and prints the
shapes with a sample, which is how `/diving/egypt/*` announced itself as
liveaboard.com's vessel pattern.

**What to record:** the shape that looks like a vessel, the shape that looks
like a trip or a departure, and their counts. **What to resist:** typing a URL
pattern into the parser because it looks right. Scope the pattern to the
country, the way `NON_BOAT_SLUGS` and the `/diving/egypt/` scoping had to be,
and expect a fraction of the matches to be dive sites, regions or landing
pages rather than boats.

If no sitemap is served, or it comes back gzipped, the probe says so rather
than guessing — and the next step is a listing page and its link shapes, not a
pattern somebody invented.

## Stage 4 — what a page actually returns

`scrape.diagnose.describe` is this project's one vocabulary for that question
and the probe uses it: status, size, `<title>`, the `@type` census of every
JSON-LD block, how many nodes carry a price, and the link shapes.

Three outcomes, and they lead to three different projects:

- **Server-rendered with JSON-LD.** The best case and liveaboard.com's: a plain
  `urllib` GET is the whole crawl.
- **Server-rendered markup, no structured data.** An HTML parser, not a
  browser — the same finding that says liveaboard.com's specification table
  needs one (unclosed `<dt>`/`<dd>`, which only a normalising DOM closes).
- **A shell.** Then stage 5 is the whole of it.

## Stage 5 — where the money is

If the price is not in the served bytes, the page fetched it. The probe lists
inline `/api/`, `/graphql/` and `/_next/data/` literals and names any framework
state blob it finds, which is how PADI's itinerary endpoint was found by
`tools/probe_padi_bundle.py` — and PADI's own deals listing is the worked
example of the payoff: a 272 KB AngularJS shell with no prices in it, beside
`/api/v2/travel/promotions/` which answers over plain HTTP and pages honestly.

**A browser is the last resort, not the first.** Three of liveaboard.com's four
client-rendered panels turned out to parse from the served bytes; the weekly
Playwright run persists for the two that do not. Establish which half this site
is before committing to a browser in a workflow.

**Pass condition:** one sailing's price, currency, date and vessel read from a
response somebody has in front of them.

## Stage 6 — write the map before the parser

`docs/sources/divebooker.com.md`, replacing every `unverified` with what came
back — **in the same commit as the probe run that found it**, negatives
included. A lead ruled out and not written down gets followed again, and a
stale map is worse than none.

This is the stage that is skipped when things are going well, and it is the one
that pays for the next person. `docs/sources/padi.com.md` still carries a
sentence that turned out to be wrong, quoted rather than deleted, because it is
the reason nobody looked again for a year.

## Stage 7 — fixtures, from the bytes

Save the real responses as fixtures under `tests/fixtures/`. Not trimmed to
what the parser wants: a fee parser proved against one hand-trimmed fixture
matched nothing on six real trips, and the gear fixture that put a charge on
one seller's book alone passed all the way through the bug it existed to catch.

Three shapes minimum, deliberately different — the probe's `booking_tours`
default is the template: the ordinary case, the sold-out or empty case, and
the awkward one. A parser proved against a single page is a parser proved
against one page's coincidences.

## Stage 8 — the parser, and where it lives

`src/liveaboard/scrape/divebooker_com.py`, a `SourceAdapter` subclass with
`source_id`, `host`, `discover()` and `parse()`. What the base class already
gives, which is most of it: robots, pacing, snapshots, the two-attempt re-read
for a page that comes back without structured data, and `not_looked_at` for a
page the run chose not to open.

Three rules that are not negotiable here, because each is a scar:

- **A page nobody read is not an empty one.** Unreadable, skipped and
  "genuinely sells nothing" are three different answers and two of them have
  already deleted real, bookable trips from this site.
- **Never invent a price.** A parser that cannot find a number returns `None`.
  A figure with no unit is not a per-trip figure.
- **Keep two fields where the source states two facts.** PADI's two harbours
  were stored joined and could not be split back out on 11 of 447 trips,
  because two of the eight harbour names contain the separator.

## Stage 9 — identity, and the join

The merge key is `(boat, date)` — exact, because a date has no spelling — so
everything here is about the boat half.

`data/padi_aliases.json` is the precedent and it is **hand-maintained**,
because nothing automatic survives this data: "Destiny" scores 0.67 against
"eriny", and dive centres, dive sites, fleets and boats all borrow each other's
names. Expect the same file for this source, minted the same way, and expect
`tools/probe_padi_slugs.py`'s lesson to apply — gather evidence for a human
review, and verify the record is a *boat* rather than trusting a name match.

Then the two questions that follow from it, both already answered once:

- **A boat only this seller lists is still a boat** — PADI-only vessels get
  minted ids and the fleet went from 67 to 77. The equivalent here is a
  `divebooker_only` list, and such a row carries no other seller's price.
- **A seller nobody asked is blindness, not absence.** `promote` creates rows
  for sailings only the second seller lists; 53 real sailings were dropped for
  eleven refreshes before it did.

## Stage 10 — promote, and what a third book may say

Promotion is pure and CI compares its output byte for byte, so this stage is
where the third source becomes a published fact. The rules it inherits:

- **The vessel's own fee panel beats a seller's account of it.** That is a
  statement about two disclosures, not about two sellers, and it decides where
  a third book sits without anybody ranking sellers.
- **Nitrox and gear are the vessel's charge and appear once.** A third seller's
  copy of them is a duplicate. Serenity's PADI bill carried €35 of nitrox twice
  and the page printed the fabricated total as a *disagreement between the
  sellers*.
- **A charge stated twice is counted once**, and the row says what covers it.
- **Never claim a total the disclosure does not support.** No fee lines means
  nobody looked.
- **Each fact is dated to the day its own seller was read.** A third crawl is a
  third date field, not a third value in one.

## Stage 11 — the page

Only now, and this is where stage 0's decision is spent. A third column in the
money block, a third entry in the Seller cell, a third `pct` source, a third
reading date in every panel that prints one — or none of it, if stage 0 said
*third opinion*. The three-cells-read-as-one-value rule and the thirteen-column
budget both bite here: row height is paid on 1,122 rows and column width once.

## Stage 12 — cadence

**One source per workflow.** `divebooker.yml`, dispatchable, capped on
dispatch and uncapped on the schedule (`TestNoScheduleInheritsADispatchCap`
enforces that the cap is resolved against `github.event_name`), running the
code-only suite up front, handing its *inputs* up as an artifact and
delegating everything from `promote` onwards to `publish.yml`. A fetching job
hands over its inputs and never its output — a derived file that travels
arrives stale, and did, on 2026-08-31.

And it commits its data *and* the dataset built from it, or `promote --check`
is red until something unrelated heals it.

## Stage 13 — guards

Point each one at the old code and watch it fail. Assert the outcome, not the
property that delivers it.

- No shipped bill names one fee code twice, on a fixture where **both** books
  carry the charge.
- A row states a list price only from the seller whose fare it prints.
- A sailing only this seller lists carries no other seller's price.
- Ids: two itineraries must never share one — `promote` raises.
- Whatever the page learns to say, `TestThePageIsWhatItsDataBuilds` and
  `TestTheRenderedPageHasNoClockInIt` still have to pass.

## Stage 14 — ship it

`python3 tools/ship.py` before and after, both numbers in the commit message.
Every ask gets a branch and comes back through a merge.

## Stop conditions

Places where the honest answer is to stop, and each is a real outcome:

1. **robots.txt refuses what would have to be read.** Then the question is
   whether the refusal is genuine or a parser artefact, and either way the
   answer goes in `docs/sources/divebooker.com.md` as a decision with a date
   on it — not as a lead somebody re-follows in six months.
2. **The price needs a browser per departure.** liveaboard.com's booking pages
   already cost ~890 requests a night over plain HTTP. The same shape in a
   browser is a different project, and it needs its own measurement before its
   own workflow.
3. **The boats cannot be joined.** A third book that cannot be keyed to the
   fleet is a third set of rows nobody can compare, which is the opposite of
   what this site is for.
4. **Stage 0 said third opinion.** Then stages 10 and 11 shrink to "fills a
   blank, never outranks a stated figure, last in the chain" — and that is a
   finished piece of work, not a half-done one.
