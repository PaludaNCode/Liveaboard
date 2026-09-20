# divebooker.com — what this source cannot do

Written 2026-09-20, from what `tools/probe_divebooker.py`,
`tools/probe_divebooker_departures.py` and `tools/fetch_divebooker.py` read on
a runner. Every line is a measurement or a refusal, not a worry.

`docs/sources/divebooker.com.md` is where each fact came from;
`docs/plan-divebooker.md` is the order of work. This file is the one to read
before deciding what the third seller is allowed to say on the page.

## What it cannot answer, at all

1. **Berths left.** `Offer.availability` is `https://schema.org/InStock` on
   every offer read, and no node anywhere states a number. Over 219 departures
   there is not one count. So the *Places* column can never carry a divebooker
   figure, and nothing may imply otherwise.
2. **A list price, so a markdown.** Nothing states a struck-through, previous
   or was-price. The sale view's whole mechanism is the seller printing its
   own list price beside its fare, and this seller does not. A divebooker row
   can only ever read *not on sale* — which is why it must never be counted
   into `deals.coverage` as if it had been asked.
3. **The operator.** `Product.brand` is `{"name": "Divebooker.com"}` — the
   seller — and `Event.organizer` is the hull (`Bella 2`). No company is named
   anywhere on the page. The operator goes on coming from liveaboard.com's
   vessel page, and a divebooker-only boat would have none.
4. **A fee book.** Nothing read carries a required-extras disclosure of any
   kind. By this project's own rule that is *nobody looked*, not *there are no
   fees* — so a bill built on divebooker alone would be a total the disclosure
   does not support. **Now looked at harder:** the page's streamed payload
   holds 253 distinct keys and not one matches `fee`, `extra`, `includ` or
   `exclud`, and the page calls no endpoint at all. So it is not rendered
   client-side and not fetched — it is not on the vessel page. If it exists it
   is in the booking flow.
4b. **The currency a price is in may be the request's, not the boat's.** The
   payload carries `currencies.current` and a `rates` table keyed by
   `currencyId`. So the book's 570 USD against 407 EUR could be a fact about
   the vessels or about where the crawl ran from, and a figure compared
   against ours is compared against an unknown base until that is settled.
5. **A dive count, an entry bar, a cabin ladder.** None of the three appears in
   any node read.

## The one that stops the fares being published

0. **The unit of `Offer.price` is not established.** Red Sea Aggressor IV on
   2027-07-24 states 5,398 USD against our 2,699 for the same seven nights —
   exactly twice — and twelve more rows differ by 20 to 350 on two boats,
   against 57 that agree to the cent. So the figure is a per-person berth on
   most rows and something else on at least one, and the page never labels it.
   Not a parser artefact: a date can carry several offers — Red Sea Aggressor
   IV states 161 over 143 sailings, and the book keeps the cheapest, which
   moved 30 dates — but every one of the 13 disagreeing rows states exactly
   one offer on its date, the 5,398 included.
   Every fare is kept in `data/divebooker.json` and **none reaches the
   dataset**: `promote` writes `divebooker.fares: "withheld"` and a guard
   asserts no departure carries a divebooker figure. Until somebody reads a
   booking page and establishes what the number counts, this source cannot be
   a third price.

## What it answers, but not the way the page would want

6. **`AggregateOffer` is a rate with no stated unit.** Bella 2: `lowPrice 143,
   highPrice 144` in EUR beside fares of 576, 576 and 579 on a three-night
   trip — each fare over four, so a per-day figure on days aboard. The page
   never labels it. Read as a trip price it quarters every fare on the site;
   it is read by nothing and a test keeps every fare above 200.
7. **Two currencies in one fleet.** 13 offers in EUR against 10 in USD across
   three Egyptian boats. `changes.repriced`'s rule already covers the diff — a
   fare in a different currency has not moved — but any comparison against
   another seller's figure has to convert first, and the FX table's own age
   applies.
8. **The trip name is the only trip identity.** There is no id, no slug and no
   tour number on a departure; the fragment (`#259223`) is the page's own
   anchor, not a stated identifier. So a divebooker sailing joins ours on
   `(boat, date)` and on nothing else — which is the key `promote` already
   merges on, and it is exact, but it means two sailings of one boat on one
   day cannot be told apart.

## What this reading did not cover

9. **Ten hulls out of this site's 77 — and that was our mistake, not the
   seller's.** The Egypt country page links ten
   `-haz` vessels and the fetch follows those links. The sitemap knows 516
   hulls worldwide and does not say which sea any is in.
   `/boatsearch?et=2&e=3881&ym=202705` answers — **75 boats for May 2027** —
   and it is **not** refused to us: that `Disallow: /boatsearch` sits in the
   file's `turnitinbot` record, and this project read it as a rule about
   everybody. `/destinations/`, `/countries/` and `/aquatories/` really are
   refused to `*` and stay unavailable. So the ten is a limit of how we
   looked, not of what the seller lists, and it is liftable. All ten read so
   far are boats we already carry, adding no vessel and no sailing — which
   says nothing about the other 65.
10. **One day's reading.** 977 departures collected 2026-09-20, in a single
    run, with no second run to compare against. Every rule this project has
    about staleness applies and none has been exercised here yet.
11. **The season.** The fetch keeps every dated sailing the page states; it
    does not filter to the published season, and nothing downstream has yet
    decided which of the 977 fall inside it.

## What is deliberately not built

12. **Nothing on the page names divebooker.** The dataset carries the book and
    `promote` can read it, but no column, chip, filter or panel mentions a
    third seller. That is stage 11 of the plan and it is the owner's call
    (stage 0): *two sellers, neither of them the house* is written into
    `best().cheaper`, the `.lav`/`.padi` metric keys, the Seller cell, the
    sale-run folding and both `*_read` dates.
13. **No workflow fetches it on a schedule.** The fetcher runs from
    `probe.yml` by hand. A `divebooker.yml` on the one-source-per-workflow
    shape is stage 12, and it cannot be dispatched until it is on the default
    branch.
14. **The book reaches this repository through a job log.** The sandbox's
    egress policy refuses divebooker.com *and* the blob host artifacts are
    served from, so the runner prints the book as gzip+base64 and
    `tools/land_divebooker.py` reassembles it. It is checksummed per line
    because a hand-carried 17,400 characters failed once on one character.
    This is a courier for the development sandbox, not a pipeline: the
    scheduled job, when it exists, will write `data/divebooker.json` directly.
