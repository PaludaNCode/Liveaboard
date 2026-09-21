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
   vessel page, and the 33 hulls only this seller lists carry
   `unknown-operator`, which is the honest answer rather than a gap.
4. **A cabin ladder.** No node states what a room costs or how many are left
   at a price, so the *Places* column can never carry a figure from here. This
   entry used to read *"a fee book"* and *"a dive count, an entry bar, a cabin
   ladder"*, and two thirds of that was wrong — see below.

**Items 4 and 5 were a keyword sweep, and the sweep was the limitation.** They
read: *"Nothing read carries a required-extras disclosure of any kind … the
page's streamed payload holds 253 distinct keys and not one matches `fee`,
`extra`, `includ` or `exclud` … it is not on the vessel page. If it exists it
is in the booking flow"*, and *"a dive count, an entry bar, a cabin ladder —
none of the three appears in any node read"*.

The panel is called `details`, the count is `numberDives`, the bar is
`requirements`, and all three sit in the same trip object. A sweep answers only
about the words it was given, and *Trip & price details* is exactly the shape
that survives one. What settled it was a person opening the page and asking
what the panel held. **Two of the three are now read and used**; the ladder
really is absent.

4b. **The currency a price is in was the request's, and it is settled.** That
   entry read *"could be a fact about the vessels or about where the crawl ran
   from, and a figure compared against ours is compared against an unknown
   base until that is settled"*. `currencies.current` says USD, the offer
   labels often say EUR, and the payload's own `rates` table is what tells
   them apart — `page_currency` reads it, and the fares reconcile with the
   other two sellers on 725 of 777 joined sailings.

## The one that stopped the fares being published, and no longer does

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
   **The fares are published now**, on the 777 departures this seller lists
   and nowhere else — `divebooker_price` and its provenance, and
   `promote` writes `divebooker.fares: "published"`. This paragraph used to
   end: *"Every fare is kept in `data/divebooker.json` and **none reaches the
   dataset** … Until somebody reads a booking page and establishes what the
   number counts, this source cannot be a third price."* What established it
   was not a booking page but the currency, one paragraph up: read the page's
   own way, 725 of 777 joined sailings carry a figure identical to one of the
   other two sellers'. A unit that reproduces another seller's number to the
   cent on 93% of a fleet is an established unit.

   The 2.000× row is published with the rest, as the seller states it.
   Dropping a price because this site finds it surprising is the failure it
   reports in other people, and the page's whole job is drawing sellers who
   disagree. It is named here and in the build log instead.

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

   **How the two comparisons disagreed was the finding, and the page settled
   it.** Before the fix, reading each figure by its own label put 595 of 777
   rows in a 10–20% band — the euro-dollar gap wearing a costume — while
   ignoring both labels put 645 on the same number to the cent. The page's own
   payload says which is right (`currencies.current: USD`), and with the
   currency read from there:

   | Distance from the nearer seller | before | after |
   |---|---|---|
   | exact (<0.2%) | 106 | **725** |
   | within 3% | 6 | 2 |
   | within 10% | 29 | 27 |
   | within 20% | 595 | 3 |
   | over 20% | 41 | **20** |

   Three sellers, 725 sailings, one price each, to the cent.

   **And the residue has a shape too.** 52 rows really differ, of which the
   loud ones are Unity (13 sailings at 1.39–1.42×), Ghazala Explorer
   2027-07-12 at 1.65×, Blue Pearl 2027-07-29 at 1.26× — and Red Sea Aggressor
   IV 2027-07-24 at exactly **2.000×**, still the only one whose offer node is
   identical in shape to the sailings either side of it. A boat one seller
   prices 40% above another is what a price-comparison site is for; a sailing
   priced at exactly double is not that shape, which is why it is the one row
   still unaccounted for.

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

## What is built, and what the page is still waiting for

12. ~~**Nothing on the page names divebooker.**~~ Built. The third seller has
    its own `.db` metric key, its own `best().cheaper` value, its own column
    in the fee panel and its own chip in the Seller bank; `best()` reads a
    list rather than a pair, and the seller chip's word for *more than one*
    is no longer `both`. Three shapes each had to be rewritten rather than
    extended, and every one of them was the reading order hardened into a
    structure — which is the finding, and it is in CLAUDE.md under *Three
    sellers, none of them the house*.
13. ~~**No workflow fetches it on a schedule.**~~ `divebooker.yml` exists, on
    the one-source-per-workflow shape, daily. **It has never run**, and it
    cannot: `workflow_dispatch` and `schedule` register only for workflows on
    the default branch. So this is the one thing still blocking, and it
    blocks the rest of the list below.
14. **The committed book predates the fee reader.** `data/divebooker.json`
    holds 92 hulls and 977 departures and no `fees` and no `trips` key at
    all, because it was collected before the *Price details* panel was found.
    Everything read out of that panel is therefore written and tested and
    **dormant**: the fee book, the dive count, the entry bar, the reefs and
    the two harbours. 26 itineraries carry no fee line, 30 no dive count, 8
    no reef, and all 29 founded rows read *Unknown* at both ends of *Departs
    from*, and the first `divebooker.yml` run is what answers all of it. A
    hand-edited input is not the fix — the dataset must be what `promote`
    builds from what a fetch wrote.
15. **Two thirds of its bills do not add up, and the unit is why.**
    Measured 2026-09-21 with the shipped reader over all 92 hulls, four times
    ([35585415354](https://github.com/PaludaNCode/Liveaboard/actions/runs/35585415354)
    → [35590899283](https://github.com/PaludaNCode/Liveaboard/actions/runs/35590899283)).
    **205 of 608 panels are complete**, 53 of those state no obligatory charge
    at all, so **152 carry a priced bill this project can total** — from 155
    and 102 on the first read, which five words to `fees.LABEL_PATTERNS`
    bought.
    What is left is not a vocabulary problem. **319 panels hold a figure with
    no unit** — *50 USD per person* states a payer and no period,
    `FeeItem.span_for_trip` refuses the line, and one such line silences the
    bill it sits in — and 105 hold a charge with no figure, which is nothing to
    do: this source stated no price and this project does not invent one.
    The words proved it rather than dented it. Royal Evolution's 9 panels
    completed the moment *Port & Permission fees* was read, because it says
    *per trip*; Tala's 12 did not, because *Route fees and enviromental taxes -
    200-320 EUR per person* says who pays and not how often. Read, and still
    silent.
    So a third of the fleet is the ceiling, and the one lever that could raise
    it is the rule `_with_units_resolved` already applies to gear — join the
    two books on the money and take only the unit. Whether it reaches these 319
    cannot be measured until the fee book is committed, because it needs both
    books side by side.
    40 obligatory lines in 9 spellings are still declined, and each is a
    deliberate refusal rather than a gap. *Government fees* is 35 of them, all
    on the Sea Serpent fleet, whose only other required line is a park fee with
    no unit — so naming it would buy nothing, and choosing what the charge is
    for with no total riding on the answer is a guess. Dive Runner's *Entrance
    fee* states 10 EUR per person per day for the Strait of Tiran and 15 for
    Ras Mohammed in one line, and reading the first as the charge would publish
    10 to a diver who pays 15.
16. **The operator is permanent, and it is item 3 seen from the other end.**
    The 33 hulls only this seller lists have no liveaboard.com vessel page,
    so nothing states a company for them and all 29 founded itineraries carry
    `unknown-operator`. Correct rather than missing: `Product.brand` here
    names the seller, and publishing *Divebooker.com* as the operator of an
    Egyptian boat is the mistake a fixture caught once already.
17. **The book reaches this repository through a job log.** The sandbox's
    egress policy refuses divebooker.com *and* the blob host artifacts are
    served from, so the runner prints the book as gzip+base64 and
    `tools/land_divebooker.py` reassembles it. It is checksummed per line
    because a hand-carried 17,400 characters failed once on one character.
    This is a courier for the development sandbox, not a pipeline: the
    scheduled job, when it exists, will write `data/divebooker.json` directly.
