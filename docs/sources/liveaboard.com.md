# liveaboard.com — source interface

Where each fact this site publishes comes from: the URL, the node or selector,
and whether reading it needs a browser.

**This is a map, not a tutorial.** If something here does not help answer
"where does X come from?", it does not belong. Two rules keep it worth reading:

1. **Negatives carry equal weight.** The dead ends are what stop the same lead
   being followed twice, and they are exactly what reading the parsers cannot
   recover. They are the last two sections and they are not an appendix.
2. **A probe that discovers something updates this file in the same commit.**
   A stale map is worse than none — see [#53](https://github.com/PaludaNCode/Liveaboard/issues/53)
   for what stale-but-plausible does to this project.

Verified against the parsers and `data/archive.json` on 2026-08-27.

---

## Entry points

| Purpose | URL | Notes |
|---|---|---|
| Fleet for a month | `/diving/search/egypt/{month}/{year}` | Returns **every** result at once. `?page=2` came back byte-identical with `pageCount:0` — the Next button pages the rendered view, not the server. One fetch per month is the whole month. Built by `liveaboard_com.search_paths()`. |
| Vessel page | `/diving/egypt/{slug}?m={M}/{YYYY}` | The `?m=` selector means **that month and no other**. Without it the page returns a window starting from today: one run scraped 746 departures spanning 2026-09 to 2027-10 and kept 14. Covering May–Aug is four fetches per vessel (`SEASON_QUERIES`). |
| Destination listing | `/diving/egypt`, `/diving/egypt/red-sea` | Confirmed live, but neither yields a priced offer — overview pages. Boat-link fallback only (`DESTINATION_PATHS`). |
| **Booking step 1** | `/BookingStep1?tourid={tour}&boatid={boat}` | The cabin ladder and the only stated berth count anywhere on the site. Plain GET, no browser, ~190 KB. Both ids come from `Event.@id`, so the URL is built rather than crawled. One request per **departure**. See below. |

The month listings are a **global template**: they link every destination the
site sells. Scoping the boat-link pattern to `/diving/egypt/` is load-bearing —
an earlier version accepted any two-segment `/diving/` path and the crawler
walked off into Indonesia and the Rhine. About twenty `/diving/egypt/` links
are dive sites and regions rather than vessels; `NON_BOAT_SLUGS` skips them.

### The booking page: the cabin ladder, and the berth count (#79)

Established by `tools/probe_booking.py` on runs 33217225920, 33217903323 and
33218390727, reading three deliberately different sailings in full.

The vessel page advertises one price per departure. The booking page shows what
that figure is the bottom of — and **the advertised price is the cheapest
cabin's per-person price**, checked on all three: Iceberg advertises $619 and
its cheapest cabin is $619, Alia Soul $1,923 against $1,923, Red Sea Aggressor
II $1,320 against $1,320. `fetch_cabins.py` re-checks it on every sailing and
prints any that disagree, rather than trusting three.

| Fact | Where | Notes |
|---|---|---|
| Cabin listing | a `<fieldset>` per cabin, opened by `<button aria-controls=help-content-cabin-details-{id} title="...">` | The **title** is what separates a listing from a dialog: the close button inside each cabin's modal carries the same `aria-controls` and no title. |
| Cabin name | that button's `title` | Quoted only when it must be — `title=Suite` beside `title="Cabin 1 &amp; 2"`. |
| Sleeps / beds / amenities | the `<ol>` under the name | Item 1 is occupancy, item 2 is the sleeping arrangement, the rest are amenities. The bed line is the one whose `<span>` carries a `title`, on 8 of 8 cabins — *not* a word match: Red Sea Aggressor II's "1 Double or Twin (convertible)" contains no "bed". |
| Price now | `<em>$</em><span translate=no>619</span>` | `translate=no` is on the numbers and nothing else, which beats the Tailwind classes around them. |
| List price | `<del translate=no>$ 688</del>` | Absent entirely when the cabin is not discounted — the absence is the answer, not a list price equal to the price. **This is the sale**; see below. |
| **Berths left** | `data-allocation` on `<select name=input-cabin-guests-{id}>` | See below. |
| Sleeps, shareable, privacy | `data-cabin-occupancy`, `data-shareable`, `data-privacy-optional` on the same select | `data-privacy-optional=undefined` occurs (Iceberg's Suite) — a JavaScript value reaching the markup, and not an answer. |
| Single-occupancy surcharge | `<div id=private-cabin-help-text-{id}>` or `<div id=privacy-optional-help-text-{id}>` | Two phrasings, one number, **keyed by cabin id** — the div sits *after* that cabin's select and before the next cabin, so anything positional gives each cabin the number belonging to the one above it. 60% on Iceberg, 50% on Alia Soul, 65% on Red Sea Aggressor II. |
| Sold out | `<span class="... text-red-600">FULL</span>` where the select would be | The cabin is still listed and priced in full. |

**The berth count is `data-allocation`, not the red banner.** Three things on
the page state the number and they agree: the attribute, the `only N spaces
left!` banner, and the select's own options, which run `1..allocation`. But the
banner only appears at **four or fewer** — Alia Soul's twelve-berth cabin has
none — so a parser reading the banner reports the roomiest cabins as unknown.
`parse_cabins` reads the attribute and reports a disagreement rather than
choosing. On a `FULL` cabin the count is zero, stated.

It is still the **operator's claim**, not verified inventory, and it is the most
perishable thing the crawl reads: true at fetch, stale by morning. Every record
carries the day it was read and anything rendering it must say so.

**A sold-out page is not an unreadable one.** Red Sea Aggressor II's sailing
lists both cabins, prices both, states the surcharge, and prints `FULL` in
place of each select. So the ladder is fully readable and only the berths are
zero — where a page returning no cabin markup at all knows nothing, and writing
that as zero would publish a sold-out sign for a page that merely failed.

**Reading, not booking.** Step one of a booking flow is a page with a form on
it; a GET renders it and submits nothing. The polite fetcher asks `robots.txt`
first and it answers yes — but read *"robots.txt, and the blank line"* below
before taking that at face value, because the yes is an accident.

**The cost is one request per departure**, ~890 a night, because a berth count
changes the moment somebody books. That is why `cabins.yml` is manual and
capped by default rather than folded into the daily refresh.

### The operator, on a vessel page with no departures

`Product.brand.name`, in the page's own JSON-LD:

```json
"brand": {"@type": "Brand", "name": "Blue Planet Liveaboards"}
```

Every operator this site publishes otherwise comes from an `Event.organizer`.
A vessel liveaboard.com sells **no berths** on has no `Event`, so 22 hulls fell
back to PADI's `fleetTitle` — a shelf on a booking site rather than a company,
and shouted. The brand is on the page either way, which is the point.

Read 2026-08-30 over all 79 vessels in the fee book: **79 of 79 state one**,
50 distinct companies, no nulls. On the 10 PADI-only vessels that have a page
here at all, every one is the operating company rather than a fleet label:

| vessel | `brand.name` | PADI's `fleetTitle` |
|---|---|---|
| blue-pearl | Blue Planet Liveaboards | `BLUE PLANET` |
| bella-2, bella-3, eriny | Bella Liveaboard | `BELLA LIVEABOARDS` |
| ashrafi | Crystal Reef Adventures | — |
| freedom-iii, freedom-iv | Sharks Bay Umbi | — |
| lady-m | Blue Ocean Diving Centers & Resorts | — |
| reef-voyager | Reef Oasis Fleet | — |
| south-moon-1 | Sea Queen Fleet | — |

Read by `scrape_fees.py`, because the weekly fee run is the only pass that
visits a vessel with no departures. Plain JSON-LD, so no browser is needed for
the field itself — it is read there only because that pass is already open on
the page.

**It is what settled MY Blue Pearl** ([#115](https://github.com/PaludaNCode/Liveaboard/issues/115)).
PADI shelves it and MY Blue under one "BLUE PLANET Fleet", and folding the two
on that alone asserts a company for a hull our own source connects to nobody.
This is that source connecting it.

### What is on sale, and where it is not

**There is no deals listing on this site.** `/liveaboard-deals` exists — the
same path PADI uses — and is SEO prose: shoulder-season advice and a newsletter
signup, zero offers, zero prices, one JSON-LD block carrying none. Beside it in
the sitemap sit `spring-sale`, `spring-sale-cruises`, `deep-blue-friday`,
`singles-day`, `summer-of-scuba`, `wave-season` and `mediterranean-cruise-deals`;
all are campaign landing pages whose only discount text is a destination badge —
*"Up to 30% OFF"* over a region card, with no vessel, date or price behind it.
`/spring-sale` still said **"Spring Sale 2026"** when read on 2026-08-30, so a
campaign URL is a stale input as well as an empty one. Checked 2026-08-30; do
not go looking again.

**The sale is on the booking page instead, and it is better than a listing.**
The `<del>` list price beside each cabin is the whole answer, and
`tools/fetch_cabins.py` has been reading it nightly since #79 without anything
downstream using it. Measured on `data/cabins.json` of 2026-08-28:

| | |
|---|---|
| departures with a discounted cabin | **263 of 864**, on 22 boats |
| discounted cabins | 901 of 2,982 |
| ladders discounted in part | **0** — a sale marks down every cabin, or none |
| cheapest cabin left at list while a dearer one is cut | **0** |

Those last two rows are what make a per-sailing answer possible at all: the
advertised price is the cheapest rung, so that rung against its own `<del>` is
the sailing's discount. Setting the cheapest *price* against the dearest room's
*list price* is the mistake this invites, and it reports Red Sea Aggressor II's
33% sale as 40%.

**The trip name carries the same claim, and is not used.** `Event.name` begins
`33% Off: ` on 241 of 864 departures, which `promote.PROMOTION` has always
stripped before grouping. It is exactly right — the banner's percentage equals
the ladder's on **241 of 241** — and it is still the weaker source: the ladder
carries the money as well as the rate, and finds **22 discounted sailings that
carry no banner at all**. Corroboration, not input.

**Both sellers agree about the discount, and disagree about its extent.**
Against PADI's `compareAtPrice` over the published season:

| | |
|---|---|
| both sellers say on sale | 158 — and they agree on the percentage on **158 of 158** |
| liveaboard.com only | 105 |
| PADI only | 5 — of which 3 have no ladder read at all, so this site said nothing rather than "no" |

The three Red Sea Aggressors settle it: on the sailing PADI advertises for each
hull, every cabin is 33.0% off on both sites, at the same dollar figures. What
differs is coverage. PADI publishes one exemplar sailing per vessel; this
publishes the whole window, and the window has a cliff — Red Sea Aggressor II is
33% off every week from 1 May to 24 July and full price from 31 July.

### Saying what moved, without keeping the ladders

`data/cabins.json` is rewritten whole every run and carries one `collected`
date, so for eleven readings this source could say what was on sale today and
nothing at all about what had changed. That is the same failure `data/deals.json`
was shaped to avoid — *a change log is a diff between two committed days* — and
the cabin book predates the rule.

It showed on 2026-08-30, when the Red Sea Aggressors' 33% sale ended. The page
reported it **from PADI**, which publishes one exemplar sailing per vessel, so
it said *three offers withdrawn* for an event that moved **36 sailings**. The
bigger of the two signals was the one that could not speak, and nine of the 22
boats discounted here appear in no deals listing anywhere.

Thirty days of `cabins.json` is not a file this repository should carry — 70,000
lines, mostly cabin names and amenities that never change. So the book kept is a
**projection**: `tools/derive_sales.py` reads the committed cabin book and
writes `data/sales.json`, one entry per day, three fields per sailing.

```
"red-sea-aggressor-ii::2027-05-01": [1849.0, 2760.0, "USD"]
                                     price   list     currency
```

Two things about its shape are load-bearing:

* **Filed by each record's own `collected`, never the book's header.** A capped
  `fetch_cabins.py --limit N` run merges, so most of the file is older than the
  header says; taking the whole file would report a week-old price as this
  morning's.
* **A census, not a list of sales.** Every sailing read that day is in it,
  discounted or not, because the keys are the only thing separating *not on
  sale* from *not looked at*. `promote` compares the two days over the sailings
  both readings covered and prints the count of those it could not.

Measured: a day is 864 sailings and ~66 KB, so seven days is ~460 KB against
`cabins.json`'s 1.9 MB beside it. Thirty days — the deals book's own figure —
would be 2 MB, which is why `KEEP_DAYS` differs between the two books.

### The per-trip itinerary fragment

```
GET /itinerary/getpopupv2?boatID={boat}&tourID={tour}&languageID=1&curr=USD&showPrices=false
→ 200 text/html, ~11–19 KB, an HTML fragment (a `#help-content-travel-itinerary-modal-{tour}` dialog)
```

**No browser needed.** A probe fetched six tours over plain `urllib` and through
Playwright: byte-identical every time. This belongs in the nightly crawl with
the polite fetcher, not the weekly browser run.

**Both ids come from the repository, not from a crawl.** `Event.@id` is
`LA-{x}-{boatID}-{tourID}` on all 878 archived events, and the boatID is
constant per vessel across all 67.

**Cost: one request per itinerary, not per departure.** There are 878 distinct
tour ids — one per sailing — but dive sites, dive count and the entry bar are
properties of the trip, so every departure of one itinerary returns the same
answer. 314 requests covers everything; fetching all 878 would spend 564
re-reading what was already in hand.

`showPrices=false` is passed because prices come from the `Event` offers. A
price-bearing variant presumably exists and has not been looked at.

**Group by the promoted trip name, not the raw one.** `Event.name` carries the
operator's discount banner — `20% Off: Ultimate Red Sea (Port Ghalib -
Hurghada)` — and `promote` strips it before grouping, because a week on sale is
the same week. Keyed on the raw string, 71 of 314 itineraries matched nothing
and the fetcher asked for 97 trips it already had under their banner spellings.
`promote.itinerary_key` is that rule, exported so there is one copy of it.

`tools/fetch_itineraries.py` writes the answers to `data/itineraries.json` and
`promote` merges them the way it merges the fee book. It is **incremental**: a
trip's reefs do not change from night to night, so only genuinely new trips are
fetched, which is what makes the daily refresh affordable.

#### "What to expect": the operator's own prose

The fragment's fourth heading. Everything else on it is a field; this is the
only place the operator writes in sentences, and it names reefs the "Key
regions" list does not — `Dive 1: Elerok  Dive 2: Gota Abu Ramada` against a
region list saying "Hurghada".

```
<h4>What to expect</h4>
  <figure> … itinerary map, magnify button, two inline SVGs … </figure>
  <div class="prose …">
     intro paragraph
     <strong>Day 2</strong> … <strong>Day 3</strong> …
  </div>
```

**The `<figure>` is the thing to know.** A pattern requiring the heading and
the prose div to be adjacent matched on **0 of 67** vessels while the regions
on the same fragments parsed fine, so the failure read as "the operators write
nothing" rather than "the pattern is wrong". `EXPECT_BLOCK` allows the gap.

**Bold runs are the structure; days are not.** Measured on one trip from each
of the 67 vessels, headings split as:

| | vessels |
|---|---|
| mixed days and places | 48 |
| every heading a day | 12 |
| every heading a place — no "Day" anywhere | 7 |

So a section is a heading and its text, and `is_day` is a question asked of a
heading rather than a claim built into the parser. Day markers themselves are
219 `<strong>`, 10 `<p>`, 5 bare, 2 `<li>` and 1 `<br>` — splitting on
paragraphs reaches neither the bulleted vessels nor the place-headed ones.

Three shapes, one fixture each in `tests/fixtures/`:

| Shape | Example vessel | Fixture |
|---|---|---|
| `<strong>Day 2</strong>` + paragraph | alia-soul | `itinerary_fragment.html` |
| `<strong>Day 1:</strong>` + `<ul><li>` | all-star-red-sea | `itinerary_days_bulleted.html` |
| `<strong>Brothers Islands</strong>` + description | all-star-ghani | `itinerary_places_not_days.html` |

Tags become a **space** when stripped, not nothing, or the bullets under a
"Day 1:" heading close up into `5:00 pmThe crew will`.

**Read it as a sketch, never as a schedule.** The days are not contiguous — 2,
3, 5, 7 on one trip — and some vessels say so outright: Miss Nouran's own
"Sample Itinerary" section reads *"We do not announce a day-by-day plan"*, and
Serenity's is conditional on the marine parks. Anything rendering it says so.

## Facts, and where each one is

Everything in the "no" column comes from JSON-LD in the served HTML
(`scrape/jsonld.py`); everything in the "yes" column is rendered client-side
and needs Playwright.

| Fact | Location | Browser |
|---|---|---|
| Departure dates, price, currency, availability, booking url | `Event` → `offers` (`Offer`) | no |
| Vessel name, description | `Product.name`, `Product.description` | no |
| **Operator** | `Event.organizer.name` — **878/878 events**, 42 companies. Also `Product.brand.name`, which agrees on all 878. Parsed and discarded ([#35](https://github.com/PaludaNCode/Liveaboard/issues/35)) | no |
| Trip title (ports, and usually the reefs) | `Event.name`, e.g. `Simply The Best (Hurghada - Marsa Ghalib)` | no |
| Vessel id / tour id | `Product.sku` = `LA-1538-6565`; `Event.@id` = that plus `-{tourid}`, on all 878 | no |
| Required / Optional Extras | rendered; read from `document.body.innerText` (`scrape/fees.py`) | **yes** |
| Rental gear prices | `#modal-gear` → `<h5>` per section, then `<li><strong>ITEM</strong><span>PRICE / week</span></li>` (`scrape/gear.py`) | **yes** (already in the DOM; the URL hash alone opens it) |
| Guests, cabins, length, year built | `#help-content-boat-amenities-specifications` → `<dl><dt>Max guests</dt><dd>20</dd></dl>` (`scrape/vessel.py`) | **yes** |
| Nitrox inclusion | `#help-content-boat-amenities-diving` → `<li>Free Nitrox</li>` | **yes** |
| **Per-trip dive sites** | `/itinerary/getpopupv2` → `<h4>What to expect</h4>`: the section **headings** and the **day** text. **Not** `<h4>Key regions</h4>`, which is wrong on 42 of 293 trips and is now a last resort only | no |
| **Per-trip dive count** | same fragment → `<dt>Dives <dd>Approximately 18 dives in total` | no |
| **Per-trip guests** | same fragment → `<dt>Group Size <dd>Up to 20 guests` | no |
| **Stated entry bar** | same fragment → `<strong>Experience</strong><span>Advanced Open Water - 50 minimum logged dives required.</span>` | no |
| **The trip's own prose** | same fragment → `<h4>What to expect</h4>`, a `<figure>`, then `<div class="prose">` split on `<strong>` runs. **67/67 vessels.** See above | no |

`Product.offers` is an `AggregateOffer` on all 318 archived pages —
`lowPrice`/`highPrice` for the vessel, i.e. a "from" price. Useless for what a
specific sailing costs; the per-departure `Offer` under each `Event` is the one
to read.

The three browser-only panels and the gear dialog are **all in the document at
page load**. `tools/scrape_fees.py` therefore reads fees, gear and
specifications from one page load per vessel, not four.

## Traps

Each of these cost a cycle at least once.

- **The specification table is many one-row `<dl>` elements**, each a single
  `<dt>`/`<dd>` pair — not one table with many rows. A parser expecting the
  latter finds one row and stops.
- **`Free Nitrox` and `Nitrox available` both appear, and only the first means
  included.** One says the boat fills tanks, the other that it does not charge
  for them. Reading "available" as included would mark half the fleet's paid
  nitrox free.
- **`Nitrox tank: Included` inside the gear dialog is a third thing again.** It
  sits among hire charges, so it means the tank costs nothing on top of the
  gear — not that fills are free. Recorded, deliberately not promoted into the
  nitrox fee.
- **Operators quote the same gear set per day, per trip *and* per week.**
  Comparing raw amounts across vessels is meaningless; normalise first, in
  Python.
- **Amounts are ranges as often as not.** `€35-100` for park fees is a 65-euro
  spread on a supposedly fixed cost. Both ends survive to the page.
- **Some extras carry no price at all** — `Rental Gear` listed and left blank.
  A third state, distinct from zero and from absent.
- **`Event.description` is boilerplate.** It restates the name, vessel and
  dates and names no dive site:
  > 'Simply The Best (Hurghada - Marsa Ghalib)' with the Emperor Asmaa in Egypt for 8 days and 7 nights, departing 01 May 2027. No more spaces available.
- **The vessel summary is the boat's year-round brochure, never the trip's.**
  Safy Mar's names St John's, Fury Shoal, the Brothers and Tiran for every trip
  it sells; Aphrodite's names St John's, 600 km from where its *North Wrecks*
  week sails.
- **`Event.location` is the country.** Every itinerary is "Egypt → Egypt". The
  real port pair is in the title's trailing bracket.
- **The extras block is split across elements.** `Environment Tax (€45)` puts
  the label in an anchor and the amount in a span, so a leaf-element probe
  reports the block absent. Flatten to text first.
- **A vessel-month page sometimes returns no JSON-LD at all**, and it is not
  the same as a month with no trips. Two shapes come back and they mean
  opposite things:
  - a `Product` node with no `Event` nodes — the page loaded, that boat sells
    nothing that month. The absence *is* the answer. 42 pages on 2026-08-28.
  - no `Product` and no `Event` — the page answered nothing. 14 pages on
    2026-08-28, spread across all four months and 11 vessels, so it is a flaky
    response rather than a bad month or a broken vessel. Re-fetching the same
    URL usually succeeds.

  Treating the second as the first deleted 49 real, bookable sailings from the
  site — DUNE Longara's entire May, still on sale at the source — and the
  change report called them withdrawn.

  **Probed, and it is transient.** `tools/probe_unread.py` re-read all
  fourteen (run 33206151057): **thirteen answered in full on the very first
  retry** — DUNE Longara's May came back with its 5 Events, Blue Horizon's
  with 5, Yachtiano's three months with 5/4/5 — and the fourteenth,
  `bismarck?m=5/2027`, returned a `Product` with no Events, which is a real
  empty month. Bodies were normal size (0.9–1.1 MB) on both the failing and
  the succeeding fetch, so it is not a truncation or a bot wall.

  So the answer is **a retry, not a markup parser**: `PARSE_ATTEMPTS = 2` in
  `scrape/base.py`, with `PoliteFetcher.forget()` to make the second attempt a
  real request. `carry_unread` stays as the net under a page that fails twice.
  A markup parser for these pages is ruled out and should not be revisited —
  the JSON-LD is there, it just occasionally is not served.
- **`?m=` is zero-padded in the data and unpadded in our crawl.**
  `Offer.url` says `?m=05/2027`; `SEASON_QUERIES` builds `?m=5/2027`. Both
  work — worth knowing before treating one as canonical.

## Negatives — checked, ruled out, do not re-check

- **No remaining-berth count anywhere on a vessel page** (#79, probe run
  33216105137). Availability is binary and that is all the source gives:
  - the `Offer` on all 889 archived `Event` nodes carries exactly seven keys —
    url, availabilityEnds, availability, price, priceCurrency, validFrom,
    @type — and none is a count;
  - the only inventory wording in any `Event.description` is *"No more spaces
    available"*, 128 times, with no numeric form of it anywhere;
  - a browser probe of four vessel-months chosen because they hold a
    **limited** departure read 43 rendered elements carrying a date and a
    price, and none states a count. The single grep hit was the vessel
    brochure — *"welcomes up to 30 guests … 4 cabin"* — which is the boat's
    capacity, not what is left on a sailing;
  - no XHR the page makes carries an inventory-shaped key (avail, space,
    berth, seat, capacity, remaining, slot, quantity, stock).

  So `spaces_left` cannot be filled from the vessel page, and a "spots left"
  column sourced from it would be empty on every row.

  **This is now a negative about the vessel page only.** The booking flow was
  the one lead left, and it answers: `/BookingStep1` states `data-allocation`
  on every cabin. See *The booking page* above. The vessel-page negative still
  stands and is still worth not re-checking — but "liveaboard.com does not
  publish a berth count" would be wrong, and #79 is answerable.

  Two earlier passes of this probe were wrong in ways worth remembering. The
  first guessed at CSS selectors, matched zero elements on all four pages, and
  printed a confident "nothing states a count" — a finding about the selectors.
  The second found rows by content and matched the embedded JSON-LD `<script>`
  and the month `<select>`, both of which carry every date and price on the
  page, then reported their prose as a berth count. A probe that has not read
  the thing cannot produce a negative about it.

| Question | Answer | Established by |
|---|---|---|
| Does `#tourid=NNN` open the trip detail, the way `#modal-gear` opens the gear dialog? | **No.** The fragment opens nothing; the trip detail is not in the document at load. ~50 dialogs enumerated, not one named a dive site. The id is real and useful — it is the `tourID` the fragment endpoint takes — but the hash alone does not fetch it. | `tools/probe_itinerary.py`, a CI run |
| Does the itinerary fragment need a browser? | **No.** Six tours over plain `urllib` and through Playwright returned byte-identical bodies. | `tools/probe_itinerary_endpoint.py` |
| Are the month listings filtered by month? | **No.** All four return the same 80 vessels, so a vessel's absence from May's listing proves nothing. Kills the planned ~30% request saving — skipping on that basis would drop real departures. | `tools/probe_crawl.py` |
| Does `robots.txt` state a `Crawl-delay`? | **No.** The pace is ours to choose. | `tools/probe_crawl.py` |
| Does `?page=2` return a second page of results? | **No.** Byte-identical to page 1, `pageCount:0`, all results already present. | a live probe; noted in `liveaboard_com.py` |
| Are charter-only vessels reachable? | **No.** The search pages never link them, so they are invisible to the crawl however many requests it makes. Heaven Saphir and MY Anemone are the known examples — which means "67 boats" is the fleet bookable **by the berth**, not the Egyptian fleet ([#55](https://github.com/PaludaNCode/Liveaboard/issues/55)). | `tools/probe_crawl.py` |
| Does a destination listing carry a priced offer? | **No.** `/diving/egypt` and `/diving/egypt/red-sea` are overviews. Boat links only. | a live run |
| Does the fee disclosure text name any dive site? | **No.** Checked for Emperor Asmaa and Emperor Elite — no reef mentioned. | `data/fees.json` disclosure text |
| Is there a per-trip dive count on the page? | **Partly.** 61 of 317 itineraries state one; the rest is derived from nights and carries no new information ([#50](https://github.com/PaludaNCode/Liveaboard/issues/50)). | `tools/probe_dives.py` |
| Is the "Route" heading readable? | **No.** It is a single `<figure>` holding a map image and nothing else — zero characters of text on 6 of 6 vessels probed. The image filename occasionally names the route (`Brothers Daedalus Elphinstone.jpg`) and is generic on half (`HRG.jpg`, `egypt-general--2.jpg`), and a filename is not what a buyer reads. Not a source. | `tools/probe_itinerary_route.py` |
| Are the "Key regions" trustworthy? | **No, and they are no longer read.** On 42 of 293 trips they name a reef the operator's own description never mentions: All Star Red Sea's *North & Brothers* week lists Daedalus, 180 km from anywhere its day plan goes, and its *Ultimate Red Sea* lists St John's while the description enumerates nine sites without it. Kept only as a last resort for a trip whose description names no reef at all. | `tools/probe_prose_sites.py` |
| Can a description section's *body* be read for sites? | **No.** It is an essay about the reef and will mention anywhere: All Star Red Sea introduces Daedalus as "Much like the Brothers Islands, Daedalus also sits in open water", which put the Brothers on three trips that go nowhere near them. Headings and day text only. | same |
| Can a day's text be read for dive sites? | **Yes, and it is** — this reversed. It was excluded because Aphrodite's "North - Straits of Tiran" week has a day plan for a deep-south expedition, evidently pasted from another trip, and reading it claims eight reefs across the whole sea. That is one trip against the 42 the key regions get wrong, and it is the operator's own text either way. Aphrodite's row is now wrong instead of its regions being wrong; recorded rather than hidden. | `tools/probe_prose_sites.py`, 308 trips |
| Is "Dolphin House" a dive site we can resolve? | **No.** Two reefs 400 km apart are sold under it — Sha'ab el Erg off Hurghada, Sha'ab Samadai off Marsa Alam. A deep-south trip's own prose lists it beside Sataya and Fury Shoal, so folding it north would have moved nine southern itineraries to Hurghada. A nickname needing the region to disambiguate cannot be resolved by a table with no region. | same |
| Is "Sha'ab" a dive site? | **No.** It is Arabic for *reef*. As a hint it matched inside Sha'ab Sheer, Sha'ab Abu Nuhas and Sha'ab el Erg alike and would have put a chip reading "reef" on 113 of 315 trips. Removed; no title's site list depended on it. | same |
| Why do operators quote a fee as a range? | **Because the fee is published per boat, not per trip**, and most boats sell several routes. 34 of 67 vessels quote at least one mandatory fee as a range, and the ranged fees are the park ones — `marine_park`, `environment_tax`, "Park and Port Fees". 20 of the 34 sell several routes at **one** trip length, so the route is the only thing the spread can be (Blue Horizon: €90–200 across five itineraries); 8 also vary by length, where the range is often exactly proportional (Dune Silky: three fees at ×2.00 for 7 vs 14 nights). 6 are explained by neither — the three King Snefro boats each sell one route and all quote the identical €65–130, i.e. a fleet-level figure. **Narrowing the range per trip would mean publishing a price the operator did not state**; the range is kept. | `data/fees.json` + `data/egypt-2027.json`, all 67 vessels |
| Do the operators' own region lists ever name the wrong sea? | **Yes, and it is theirs, not ours.** Topaz sells "North Wrecks Reefs, Tiran and Dahab" with St Johns and Zabargad in its curated regions; Odyssey's "Golden Loop: North" carries St Johns. Both are live. Worth knowing before blaming a parser for a southern reef on a northern row. | same |
| Is "What to expect" a day-by-day itinerary? | **Not reliably.** Only 12 of 67 vessels head every section with a day; 7 head none with one, and 48 mix days with places. Parse it as headings, not as days — and the days that do appear are a sketch, non-contiguous, and disclaimed outright by some operators. | `tools/probe_itinerary_prose.py`, all 67 vessels |
| Does every vessel publish that prose? | **Yes, 67/67** — and every one of the four headings (`Overview`, `Route`, `What to expect`, `Key regions`) appears on all 67, so a fragment missing one is a parse failure rather than a quiet operator. | same run |
| Does a vessel page carry all four season months at once, so one fetch could replace four? | **No, and it is not close.** A bare fetch returns **the next ten sailings from today** — a count cap, not a date window: five vessels, exactly 10 `Event` nodes each, and 0 in season on four of them. The season is nine months out. Marselia Star is the apparent exception and proves the rule — it reaches 2027-05 with 2 of its 7 season sailings only because it sails so rarely that ten sailings carry it that far. This also explains the "746 departures spanning 2026-09 to 2027-10" recorded against an early bare-fetch run: ten per vessel across ~75 vessels, a range produced by the sparse boats, never a wide window. **The 320-fetch saving does not exist.** | `tools/probe_season_months.py`, 5 vessels × 5 fetches |
| Do multi-month selector forms work — `?m=5/2027&m=6/2027`, or `?m=5-8/2027`? | **No, and they fail dangerously.** Both are guesses (neither appears anywhere on the site) and both were *accepted* rather than rejected: each returned 4 `Event` nodes, all in 2026-09 — **fewer than the bare page's 10**, with no error and no empty response. A selector the server does not understand degrades to a silent subset, which is the one failure mode this crawl must never build on. Do not try these again, and do not invent a third form without probing it. | same run |
| Is a vessel on the barren skip list actually empty, or is the crawl cementing a parse failure? | **Actually empty.** The four the second seller contradicts — Bella 2, Bella 3, Eriny, Blue Pearl, on which PADI sells 87 season sailings — answered all sixteen vessel-months with **1 `Product` node and 0 `Event` nodes**: the source stating this boat sells nothing that month, which is an answer, and never the no-structured-data state `carry_unread` exists for. So `barren.json` is holding back boats that really do sell nothing here, and the second seller is simply the only one selling them. This does **not** make `padi_only` the right label for those rows ([#110](https://github.com/PaludaNCode/Liveaboard/issues/110)): `promote` knows what the run recorded, and the run recorded a skip, so `not_asked` stays the honest output. Re-probe only if a boat's PADI season grows while its skip persists. | `tools/probe_barren.py`, 16 pages |

## Still open

- **Where the trip detail actually loads from.** Presumably an XHR fired by
  clicking a departure row, not by the hash. `tools/probe_network.py` records
  every XHR and fetch on a page and is the tool for asking. A JSON endpoint
  would be a better source than any HTML parse, and would replace
  title-matching for all 317 itineraries
  ([#52](https://github.com/PaludaNCode/Liveaboard/issues/52),
  [#34](https://github.com/PaludaNCode/Liveaboard/issues/34),
  [#36](https://github.com/PaludaNCode/Liveaboard/issues/36)).
- **Whether a `?m=` month page can truncate.** The bare page caps at ten
  sailings (below), and nothing says the month pages do not share that cap. It
  has never been reached: the busiest vessel-month this site has read is
  Snefro Pearl's May 2027 at **9**, and no vessel-month in the dataset reaches
  10. So the cap is untestable from the season and unprovable either way —
  what is written down instead is the trigger. **A vessel-month that reads
  exactly 10 is a truncation suspect, not a busy boat**, and should be checked
  against the vessel's own cadence before it is believed. Probed as far as it
  goes: near-term months on the two busiest boats returned 4, 8, 9, 8 and 8,
  every one consistent with a boat sailing every three days rather than with a
  ceiling.

## robots.txt, and the blank line

**Read this before relying on `can_fetch()`.** `https://www.liveaboard.com/robots.txt`,
read 2026-08-30, disallows 31 paths for `User-agent: *`. Two of them are ours:

```
Disallow: /BookingStep1     <- tools/fetch_cabins.py, ~890 pages nightly
Disallow: /*?*m=*           <- the ?m={M}/{YYYY} selector the whole crawl uses
```

`PoliteFetcher` obeys `robots.txt`, and that part works. It is not refusing
these because **the file is malformed**:

```
User-agent: *
                              <- a blank line, which ends the record
    Disallow: /BookingStep1
    …
```

A blank line terminates a group, so all 31 rules belong to no user-agent and
Python's `urllib.robotparser` discards them: `can_fetch()` returns `True` for
every path in the file. Delete that one line locally and `/BookingStep1` flips
to `False`. Both readings were checked. (The indentation is a red herring —
stripping it changes nothing. It is the blank line.)

**The decision — the owner's, taken 2026-08-30 with the alternatives and their
prices in front of him: carry on, and write it down.** That is what this
section is. The letter of the file permits it; the intent plainly does not, and
leaning on somebody else's typo is a poor position for a project whose whole
argument is the difference between what a site technically discloses and what
it means to. So it is recorded here, as a call somebody made, rather than left
for the next reader to rediscover as a curiosity — and it stays reversible: the
three options below were all live and none was ruled out, only outweighed.

Carrying on is conditional on the crawl staying the small thing it is. The
pace is 2 seconds a request against a stated `Crawl-delay` of none, taken once
a day, from one runner, identifying itself truthfully — slower than a person
clicking the same pages. If any of that changes the decision needs taking
again, because it was taken about *this* crawl.

What honouring it would cost, stated so the choice can be revisited with the
price in view: the cabin ladder on 828 sailings and the "advertised price is
the bottom rung" check with it; berths left at the advertised price, which has
no other source; the sale detection and the change log built on the same pages;
and the `?m=` selector, without which a vessel-page fetch returns a rolling
window from today and the season has to be filtered out of 746 departures to
keep 14. There is no second route to any of it — `/liveaboard-deals` is prose
and the JSON-LD carries no list price, both checked and recorded above.

Asking them remains the better answer than any reading of the file, and is not
foreclosed by this: they publish an affiliate programme and a partners page,
and a reply would settle it in a way no parser can. Cutting the ~890 nightly
`/BookingStep1` fetches to weekly was also on the table and was not taken —
the ladder is what the `STALE_LADDER` guard reads to catch a row offering a
price nobody can buy, and a week-old ladder is exactly the input that guard
exists to reject. See
[#121](https://github.com/PaludaNCode/Liveaboard/issues/121).

## Access

Both source hosts were denied by this environment's egress policy until
2026-08-30, when the allowlist landed:

```
$ curl -o /dev/null -w "%{http_code}" https://www.liveaboard.com/robots.txt
200
```

Probes can now be run locally as well as on a runner. Everything scheduled
still runs on GitHub Actions. See
[#1](https://github.com/PaludaNCode/Liveaboard/issues/1), closed.
