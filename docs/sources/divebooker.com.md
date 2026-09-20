# divebooker.com — source interface

Where each fact this site would read from divebooker.com comes from: the URL,
the node or selector, and whether reading it needs a browser.

**Nothing below is verified yet, and the file says so line by line.** It is the
map drawn before the territory has been walked — the questions in the order
they have to be answered, and the probe that answers each. `tools/
probe_divebooker.py` is that probe; it has never run against this host from
here, because the development sandbox's egress allowlist refuses
divebooker.com outright (403 on the CONNECT, measured 2026-09-20). The
`divebooker` job in `.github/workflows/probe.yml` is where it can run.

Same two rules as the other two source maps:

1. **Negatives carry equal weight.** A lead ruled out and not written down gets
   followed again.
2. **A probe that discovers something updates this file in the same commit.**
   Every `unverified` below is a claim nobody has checked; replace it with what
   came back, including when what came back is "no".

**Status: no fact on this page has been read from the source.**
`docs/plan-divebooker.md` is the order of work.

---

## Before any of it: this is a third seller

`CLAUDE.md` states two permitted sources, `padi.com` and `liveaboard.com`, and
that sentence is load-bearing rather than a list that grew by habit — the whole
invariant *two sellers, neither of them the house* is written in terms of two.
Adding a third is the owner's call and it changes vocabulary the page, the
dataset and five guards are written in:

- `best().cheaper` returns `"liveaboard"` or `"padi"`, a two-way answer.
- The metric keys are `.lav` and `.padi`.
- `berths_read` and `padi_berths_read` are two dates because there are two
  crawls; a third is a third field, not a third value in one.
- `promote` merges two books on `(boat, date)`; a third book is a third merge
  with its own "which seller does this row's `pct` come from" answer.

None of that is a reason not to do it. It is the reason the parser is the
*late* step and not the first one — see `docs/plan-divebooker.md`.

## Entry points

| Purpose | URL | Status |
|---|---|---|
| robots.txt | `/robots.txt` | **unverified** — what it states, and whether `urllib.robotparser` repeats it. The probe reads both and compares; on liveaboard.com those two answers differ on all 31 refused paths |
| Inventory | declared sitemaps | **unverified** — the sitemap is the only inventory that is not a guess. The probe buckets every `<loc>` by path shape, which is what names the vessel and trip patterns |
| Fleet / country listing | unknown | **unverified.** Do not type one here. liveaboard.com's boat-link pattern had to be scoped to `/diving/egypt/` after an earlier version walked off into Indonesia and the Rhine |
| Vessel page | unknown | **unverified** |
| Departure / trip page | unknown | **unverified** |

## Fact to location

Every row is a fact this site publishes about a sailing, and every one of them
is a question here. Ordered by what the page would lose without it.

| Fact | Where | Status |
|---|---|---|
| Vessel name and operator | — | **unverified.** Two sources already disagree about operator names; a third needs `Product.brand.name`'s equivalent, not a fleet label |
| Departure date and length | — | **unverified.** The merge key is `(boat, date)`, so a date that is not exact is a row that will not join |
| Berth price and currency | — | **unverified.** Is it in the served bytes (JSON-LD `Offer`, like liveaboard.com) or fetched after the page (like PADI's itinerary endpoint)? |
| Required extras / fee book | — | **unverified.** `Never claim a total the disclosure does not support` applies from the first row: no fee lines means nobody looked |
| Optional extras (nitrox, gear) | — | **unverified.** These are the vessel's charge and appear once, from the vessel's book — a third seller's copy of them is a duplicate, not a second fact |
| Cabin ladder / berths left | — | **unverified** |
| List price beside a discount | — | **unverified.** A markdown is a struck-through list price the seller prints beside its own, never a banner |
| Dive count, entry bar, reefs | — | **unverified.** All three have precedence rules already; a third source joins the *end* of each chain where it fills a blank, and may not outrank a stated figure |

## What is ruled out

Nothing yet, and that is not the same as nothing to rule out. This section is
the one that pays for itself later: every negative the probe returns belongs
here, quoted, in the commit that found it.

## Cadence, if it ever has one

Unknown, and not to be guessed. `CHECKED_HOSTS` in `scrape/base.py` holds the
pace chosen for each host whose robots.txt somebody has actually read;
divebooker.com is not in it, so the polite fetcher gives it the five-second
default for a host nobody has checked. Leave it there until the file has been
read and the number written down beside the reason.
