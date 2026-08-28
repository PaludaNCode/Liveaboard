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

The month listings are a **global template**: they link every destination the
site sells. Scoping the boat-link pattern to `/diving/egypt/` is load-bearing —
an earlier version accepted any two-segment `/diving/` path and the crawler
walked off into Indonesia and the Rhine. About twenty `/diving/egypt/` links
are dive sites and regions rather than vessels; `NON_BOAT_SLUGS` skips them.

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
  column would be empty on every row. **Not** ruled out: the booking flow
  itself, which nobody has entered — starting a booking to read an inventory
  number is a different kind of request and needs a deliberate decision.

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

## Still open

- **Where the trip detail actually loads from.** Presumably an XHR fired by
  clicking a departure row, not by the hash. `tools/probe_network.py` records
  every XHR and fetch on a page and is the tool for asking. A JSON endpoint
  would be a better source than any HTML parse, and would replace
  title-matching for all 317 itineraries
  ([#52](https://github.com/PaludaNCode/Liveaboard/issues/52),
  [#34](https://github.com/PaludaNCode/Liveaboard/issues/34),
  [#36](https://github.com/PaludaNCode/Liveaboard/issues/36)).
- **Whether the vessel page carries all four season months at once.** If it
  does, that is four fetches per vessel down to one. Unprobed — do not assume
  either way.

## Access

Both source hosts are denied by this environment's egress policy:

```
connect_rejected — gateway answered 403 to CONNECT   www.liveaboard.com:443
```

GitHub Actions runners are not behind it, which is why every probe here runs on
a runner. See [#1](https://github.com/PaludaNCode/Liveaboard/issues/1).
