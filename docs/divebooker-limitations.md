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

0. **Two of the three reasons turned out to be ours, and the third is one
   row.** This section led with *"the unit of `Offer.price` is not
   established"*, on thirteen rows that disagreed with the other two sellers.
   Twelve of them are now accounted for:

   * **The currency label was wrong, and we believed it.** `Offer.priceCurrency`
     is static per vessel and does not describe `Offer.price`: three of four
     hulls probed label every offer EUR on a page whose own payload says it
     rendered in USD. `page_currency` reads the payload now.
   * **The comparison converted the wrong way.** `money.FxTable` multiplies
     into euros and this project's own comparison tool divided, inflating
     every dollar figure by 31% against itself.

   With both fixed, 645 of 777 joined sailings carry the same number as a
   figure liveaboard.com or PADI states, to the cent — which is what "prices
   should generally be the same" looks like when the arithmetic is right.

   **What is left is Red Sea Aggressor IV on 2027-07-24: 5,398 against 2,699
   for the same seven nights, exactly twice, on a page that states USD and
   means it.** Its offer node was printed verbatim and carries no occupancy, no
   cabin class and no second traveller — it is the same shape as the 2,799 and
   2,899 either side of it. Not a parser artefact either: a date can carry
   several offers, Red Sea Aggressor IV states 161 over 143 sailings and the
   book keeps the cheapest, but that date states exactly one. Around 50 more
   rows differ by real amounts — Unity at 1.42x on thirteen sailings, Ghazala
   Explorer at 1.65x, Blue Pearl at 1.45x — and those are two sellers pricing
   one berth differently, which is what this site exists to show.
   Every fare is kept in `data/divebooker.json` and **none reaches the
   dataset**: `promote` writes `divebooker.fares: "withheld"` and a guard
   asserts no departure carries a divebooker figure. Until somebody reads a
   booking page and establishes what the number counts, this source cannot be
   a third price.

   **This file said three sellers were pricing one berth apart, and that was
   our own arithmetic.** It read: *"on Alsuraya, Discovery I and II and Grand
   Discovery, divebooker sits 13% under liveaboard.com and about 15% over PADI
   on every sailing, which is three sellers pricing one berth"*. It is not.
   `money.FxTable` converts **into** euros by multiplying (USD 0.8726), and the
   first version of `tools/compare_divebooker_fares.py` divided — which
   inflates every dollar figure by 31% against itself and manufactures a spread
   out of the conversion. The sentence is kept because a tidy story that came
   out of a wrong division is exactly the kind this project has to be able to
   recognise later.

   **Read correctly, the two comparisons disagree with each other, and that is
   the finding.** Over the 777 joined rows:

   | Distance from the nearer seller | as labelled | as digits |
   |---|---|---|
   | exact (<0.2%) | 106 | **645** |
   | within 1% | 2 | 15 |
   | within 3% | 4 | 2 |
   | within 10% | 29 | 18 |
   | within 20% | **595** | 76 |
   | over 20% | 41 | 21 |

   *As labelled* puts 595 rows in a 10–20% band, which is the euro-dollar gap
   wearing a costume. *As digits* — both currency labels ignored — puts **645
   of 777 on the same number to the cent**. Broken down by what each seller
   says: where divebooker says EUR and liveaboard.com says USD, 554 of 618
   carry the same number; where liveaboard.com itself says EUR, divebooker's
   figure is **1.148× it, which is 1/0.8726**. So the number is the dollar
   figure and `Offer.priceCurrency` is not describing it — or this seller
   charges a 14.6% premium that lands exactly on another seller's dollar price
   across a dozen operators. `tools/probe_divebooker_currency.py` asks the page
   which, rather than taking the agreement as proof.

   **And the residue has a shape too.** Reading the number as dollars leaves
   ~50 rows that really differ, of which the loud ones are Unity (13 sailings
   at 1.42×), Ghazala Explorer 2027-07-12 at 1.65×, Blue Pearl 2027-07-29 at
   1.45× — and Red Sea Aggressor IV 2027-07-24 at exactly **2.000×**, still the
   only one whose offer node is identical in shape to the sailings either side
   of it.

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

## What the whole fleet turned out to be

9a. **Three numbers, and they are different facts.** Of the 888 season
   sailings, **793** match a sailing this site already carries on
   `(boat, date)`; **85** sit on hulls the alias map does not know, which is a
   fact about our map rather than about the seller; and **10** are on a boat
   this site carries and a date it does not — a third seller listing a
   departure the other two do not. None of the three is published: the last is
   the interesting one and creating rows from it is the owner's call, the way
   PADI-only rows were.

   **And four of those ten are not sailings on sale.** Read them by name and
   the list says so itself: three are *Route on Request (Available for groups
   and…)* with no price at all — Independence II 2027-07-08, Vita Xplorer
   2027-07-17 and 07-24 — and Iceberg 2027-08-09 is a four-night *Full Charter
   Request*. A charter enquiry with no fare is not a berth the other two
   sellers failed to list. What is left is **six**: Blue on 2027-07-01 and
   07-29, Heaven Saphir on 05-07, 07-10 and 07-12, and Independence II's
   twelve-night 05-29 — each with a stated fare and a named route. Six is the
   honest size of *what a third seller would add in rows*, against ten as
   counted.

9b. **33 hulls this site does not carry at all.** Aml Hayaty, Argo, Ashrafi,
   Bismarck, C Echo 2, Freedom III and IV, Galaxy 720, Golden Dolphin I,
   Hammerhead I, Icon, Independence III, Omneia Spirit, Sea Treasure, South
   Moon 1, VipOne and eighteen others. They are named in the build log, one
   `::warning::` each, and nowhere on the page — the `deals.unmatched` rule:
   the name is what may not be lost, and the page is the wrong place to keep
   it.

9c. **The fare picture did not change shape, it got bigger.** 777 joined rows,
   compared in euros through the committed ECB table: 106 exact, 8 within 3%,
   59 within 10%, 585 within 20% and **19 over**, of which the largest by far
   is still Red Sea Aggressor IV on 2027-07-24 at exactly 2.000×; the next is
   44.6%. Three sellers pricing one berth looks like the 585; one row looks
   like nothing else on the page.

## What this reading did not cover

9. **Ten hulls out of this site's 75 — and that was our mistake, not the
   seller's. It is fixed, and the fix is a measurement.** The Egypt country
   page links ten `-haz` vessels and the fetch followed those links; it is a
   landing page with a carousel on it. The sitemap knows 516 hulls worldwide
   and does not say which sea any is in.
   `/boatsearch?et=2&e=3881&ym=202705` answers — **75 boats for May 2027** —
   and it is **not** refused to us: that `Disallow: /boatsearch` sits in the
   file's `turnitinbot` record, and this project read it as a rule about
   everybody. `/destinations/`, `/countries/` and `/aquatories/` really are
   refused to `*` and stay unavailable. The search links 20 a page and pages
   on **`p=`**, which was found by trying it against a known answer: nine
   other spellings (`page`, `pg`, `offset`, `start`, `skip`, `limit`,
   `perPage`, `size`, `take`) each return the first twenty again, silently.
   Discovery now walks every season month on `p=` until a page repeats, and
   **the full read has landed**: 92 hulls, 888 season sailings, against the
   country page's 10 and 148. So the coverage numbers in `data/egypt-2027.json`
   are about what divebooker sells rather than about how we looked.
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
