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
| **Per-trip dive sites** | `/itinerary/getpopupv2` → `<h4>Key regions</h4>` then `<li title="The Brothers">` (`scrape/itinerary.py`) | no |
| **Per-trip dive count** | same fragment → `<dt>Dives <dd>Approximately 18 dives in total` | no |
| **Per-trip guests** | same fragment → `<dt>Group Size <dd>Up to 20 guests` | no |
| **Stated entry bar** | same fragment → `<strong>Experience</strong><span>Advanced Open Water - 50 minimum logged dives required.</span>` | no |

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
- **`?m=` is zero-padded in the data and unpadded in our crawl.**
  `Offer.url` says `?m=05/2027`; `SEASON_QUERIES` builds `?m=5/2027`. Both
  work — worth knowing before treating one as canonical.

## Negatives — checked, ruled out, do not re-check

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
