# padi.com — source interface

**Egypt only, and wired up.** The site reads four things from padi.com: the
entry bar, the price of a sailing, the fee book behind that price, and — since
2026-08-29 — what PADI is discounting. All four are promoted.

The scope [#3](https://github.com/PaludaNCode/Liveaboard/issues/3) set was
**requirements and accreditation, not prices**, on the reading that PADI Travel
is weak on departure-level pricing and strong on the certification bar. Half of
that held and half did not, and the half that did not is now the more useful:
the vessel page's `offers[]` really is four departures at best, but
`shop/{vessel}/trips/` prices every sailing on sale, and the itinerary endpoint
publishes the operator's **required extras** as structured, priced records. So
PADI is a second seller here and not only a second opinion.

**The claim "PADI publishes no fee book at all" was wrong, and it shaped the
design for a while.** It is true of the endpoint that states the price and
false of the one next to it. The cost of believing it was a column that set
PADI's berth price against liveaboard.com's — which compares the half the two sellers agree
about (under €5 apart on 89% of matched sailings) and hides the half they do
not: of the 74 trips where both books add up, **43 disagree and 16 by more than
€150**, one of them by €300 on a single week. The lesson is the file's own rule
turned on itself: a negative result is only as good as the probe behind it, and
this one had no probe behind it at all.

## Entry points

Everything below answers 200 over plain HTTP, no browser.

| URL | What it gives |
|---|---|
| `sitemap-travel-dive-operators-page_1.xml` | 269 liveaboards, 58 of them `/liveaboard/egypt/`. A starting set, **not** the whole inventory — see below |
| `/liveaboard/egypt/{vessel-slug}/` | Server-rendered. Vessel, fleet, JSON-LD `Product`, every itinerary title, PADI courses taught, spec table |
| `/liveaboard/egypt/{vessel}/{itinerary-slug}/` | `<title>` and `<meta description>` per trip, server-rendered — and **nothing else**: the body is filled by XHR |
| `/liveaboard-diving/egypt/` | Country landing page. Four featured vessels; the sitemap is the real inventory |
| `/liveaboard-deals/?country=…&date=…` | **Nothing.** An AngularJS shell; see *The deals listing* below |

`tools/probe_padi_vessel.py --slug hammerhead-ii --boat hammerhead-ii` reads one
vessel page and holds it against `data/egypt-2027.json`. `--cache DIR` makes
every run after the first offline, for the same reason
`reparse_candidate.py` exists.

## Fact to location

| Fact | Where | Notes |
|---|---|---|
| Vessel name | JSON-LD `Product.name`, `window.shop.title` | Agrees with `<title>` |
| Vessel id | `window.shop.id`, JSON-LD `SKU` | Hammerhead II is 94466 |
| Fleet | `window.shop.fleetTitle` | Empty for some vessels |
| Itinerary titles | The nav `<a>` text | "Name (Port - Port) N Nights" |
| Trip length | The night suffix on that title | Sometimes doubled; see traps |
| PADI courses taught | Visible text under "PADI® Courses Available" | The accreditation half of #3 |
| Requirement **vocabulary** | `window.info.shop` | Verbatim in `padi_com.py` |
| Requirement **values** | Not in the HTML | AngularJS XHR; see below |
| Next few departures | JSON-LD `offers[]` | See traps: not a departure feed |

## Matching a PADI vessel to a liveaboard.com one

`data/padi_aliases.json` maps our `boat_id` to a PADI slug. **Hand-maintained**,
because nothing automatic survives this data:

- Similarity ranks garbage first. "Destiny" scores 0.67 against PADI's "eriny";
  the real "New Sambo" pair scores 0.59. No threshold separates them.
- Containment is no better, and three wrong pairs were committed and removed
  proving it. Dive centres, boats, dive sites and fleets all borrow each other's
  names.
- **Two hulls can share a name, an operator *and* an itinerary.** `amelie-safari`
  (id 18156, 6 cabins) and `amelie-adventures` (id 93484, 8 cabins) are separate
  boats of one operator, both selling a 3-night *Best of Hurghada*. Only the
  cabin count tells them apart. Any matcher that would have merged those is
  wrong regardless of how well it scores elsewhere.

So a pairing needs evidence from the hull, and `tools/probe_padi_slugs.py`
gathers it: slug candidates (PADI prefixes many hulls `my-`, `mv-`, `ms-`), then
`window.shop.kind == 10`, then the ports its trips name, then trip lengths and
cabin count printed against liveaboard.com's for a person to accept or reject.

**`SHOP_KIND` is the record type**, and it matters: `0` Dive center, `10`
Liveaboard, `20` Dive resort. "Iceberg" exists twice — as
`deep-breath-diving-safari-the-iceberg` (kind 0, no itineraries) and as
`my-iceberg` (kind 10, the boat). A page's `<title>` is no guide at all: the
boat's reads *"My Iceberg | Hurghada City | PADI Dive Center"*.

**`countrySlug` is the operator's registered country, not the cruising ground.**
All three Red Sea Aggressors read `united-states-of-america-usa` while sailing
Hurghada, Port Ghalib and Hamata — Aggressor Fleet is American. Filtering on it
silently dropped three real Egyptian boats, so the probe tests the ports its
itineraries name instead.

State as of 2026-08-28: **36 boats paired** of 65, `odyssey` held pending a call,
29 unreviewed. `absent` records a boat looked for and not found, which
`is_reviewed()` distinguishes from one nobody has checked — both give no slug,
and a tail that passes as settled never gets finished. A test asserts every key
names a boat we actually hold, because a key that matches nothing fails silently.

Specs disagree by small amounts and that is expected, not disqualifying: 26 of 32
probe-verified pairs agree on cabins exactly and the other six are off by one or
two, which is two sources counting a crew berth differently. Amelie is the reason
a small delta still gets read rather than waved through — there, two real hulls
differed by exactly two cabins.

## Traps

**An itinerary slug is an opaque id, never a fact.** Fifteen of Hammerhead II's
twenty-two slugs contradict the page they serve. The page is authoritative and
the slug is a fossil of some earlier title:

| Slug | What that URL actually serves |
|---|---|
| `mini-wrecks-and-nature-hurghada-hurghada-5-nights` | *Brothers Light 3 (Marsa Alam - Marsa Alam) 3 Nights* |
| `exploring-north-abu-nuhas-ss-thistlegorm-ras-moham` | *Southern Charm: Daedalus - Zabargad - Rocky Island - Sataya - Fury Shoals (Marsa Alam - Marsa Alam) 7 Nights* |
| `brothers-light-marsa-alam-marsa-alam-4-nights` | *Sharks & Dolphins (Marsa Alam - Marsa Alam) 7 Nights* |

Verified by fetching those URLs and reading the server-rendered `<title>`, not
inferred. Nights, ports and reefs read out of a PADI URL are confident nonsense
— the same failure as a St John's week badged BDE, arriving through a different
door. Slugs are also truncated at ~50 characters and disambiguated with `-2`,
`-3`, so they are lossy even when honest.

**Their titles need validating too.** One live trip title appends the night
count twice — *"… (Marsa Alam - Hurghada) 7 Nights 7 Nights"* — and another
concatenates two trip names into one string. `split_title()` strips repeated
suffixes and returns `None` when two counts disagree, because a trip length is
the denominator under every per-night price and there is no way to tell which
one was meant.

**JSON-LD `offers[]` is not a departure feed.** On Hammerhead II it advertised
four trips, all within six weeks of the fetch, three with `offerCount: 0` and
one priced `0.00`. Useful as proof a trip sails; useless for a season. Our own
May–Aug 2027 window does not appear on the page at all.

**`window.shop` is a JS object literal, not JSON** — unquoted keys, `+"20"`.
`json.loads` will not read it. `window.info` next to it *is* valid JSON.

**`/diving-in/` and `/dive-center/` are `Disallow` for `User-agent: *`** on
`travel.padi.com`. `/liveaboard/`, `/liveaboard-diving/` and `/s/` are allowed.
Also disallowed: any URL carrying `trip_date=`, `departure_date=`, `date_from=`,
`dateStart=`, `dateTo=`, `activity_date=`, `date_after=` — which rules out the
per-departure URLs JSON-LD hands out. `?trip_id=` alone is not disallowed, and
returns the vessel page unchanged.

## Ruled out

Written down so nobody spends the afternoon again.

- **The liveaboard search cannot be read without a browser.** `/s/liveaboards/all/`
  and `/s/liveaboards/egypt/` are a Next.js app. 240 KB of HTML, zero JSON-LD,
  not one link to a vessel. Its RSC payload (`RSC: 1` header returns
  `text/x-component`) carries page metadata and destination slugs, no trips.
  Only the `<title>` count is server-rendered — *"61 Trips in Egypt"*. **The
  sitemap makes this moot**: there is no reason to page a search whose results
  the sitemap already lists.
- **The `www` sitemap has no travel URLs.** 3304 URLs of certification and
  dive-centre pages. `www.padi.com/travel` 302s to `travel.padi.com`.
- **`TRAVEL_PATHS` as originally guessed.** `/travel/liveaboards` and
  `/travel/destinations/egypt` both 404.
- **No JSON API found from here.** `/api/`, `/api/v1/`, `/graphql/`,
  `/liveaboard/api/`, `/shop/api/` and ten `/itinerary/...` shapes all 404.
  `?format=json` and `X-Requested-With: XMLHttpRequest` on an itinerary URL
  return the same HTML shell. `/graphql` 301s to `/graphql/`, which 404s — that
  is Django's `APPEND_SLASH`, not an endpoint.
- **`/liveaboard-deals/` cannot be parsed, at any effort.** An AngularJS shell
  with no prices and a `page=` that is decorative; see *The deals listing*.
  Its data is `/api/v2/travel/promotions/`, over plain HTTP.
- **Ten guessed paths for the deals endpoint, all 404.** Every shape built on
  the word *deal* under `/api/v2/travel/`. PADI's own word is **promotion**, and
  the vocabulary that says so is in `window.info` on the shell — which is
  cheaper to read than the CDN bundle and was sitting there the whole time.
- **The app's own JS is unreachable from this environment.** Bundles and Angular
  templates live on `d2p1cf6997m1ir.cloudfront.net`, which the egress policy
  blocks (403 to CONNECT). `travel.padi.com/static/...` 404s — the CDN is the
  only route. This also means **a browser-driven probe cannot work from here**:
  Chromium loads the page, then cannot load the app. Runners can reach the CDN,
  and reading the bundle there is what produced the endpoint above. The *data*
  needs no runner — only that one read did.
- **`api.padi.com` is not it.** It answers JSON and is an AWS API Gateway, so
  every unmatched route returns 403 `{"message":"Missing Authentication Token"}`
  — including `/travel/` itself. That 403 reads like a route that exists behind
  auth and is not one; do not take it as a lead. `api-ecomm.padi.com` does not
  resolve, and the `"api-ecomm."` string in the bundle is a test in the client's
  auth branch, not a host to point at.

## The JSON endpoint

**Found, called, unauthenticated.** The entry bar this file called missing is a
first-class field on an endpoint that needs no token, no CSRF cookie and no
headers at all — a plain GET answers 200 with JSON, and nothing under
`/api/v2/travel/` is disallowed by robots.

| Endpoint | Returns |
|---|---|
| `/api/v2/travel/shop/{vessel}/itineraries/?kind=10` | Paginated DRF list: `{"count": 22, "results": [{title, slug, id, totalNumberOfDives, totalNumberOfDivesMax}]}` |
| `/api/v2/travel/shop/{country}/{vessel}/itineraries/{slug}/` | One itinerary, **95 fields** |
| `/api/v2/travel/shop/{vessel}/trips/` | **Every sailing on sale**: `startDate`, `endDate`, `duration`, `price`, `compareAtPrice`, `availability`, `promotion`, and the itinerary it belongs to |
| `/api/v2/travel/promotions/?country=…&date=…` | **The deals listing.** One row per discounted vessel, with a currency |

One request per vessel for the list — 58 for Egypt — then one per itinerary.

### How it was found

Not by guessing. Eight bases were tried against the known path and all 404'd,
including `/api/`, `/api/v2/` and `/api/travel/v1/`, because the prefix is never
one literal. `tools/probe_padi_bundle.py` read `itinerary.*.js` on a runner and
printed the client's own resolver:

```js
getUrl() {
  return this.endpoint.includes("https://")        ? this.endpoint
       : (adventure | recipients | account paths)  ? `${origin}${this.endpoint}`
       : this.chinaApi                             ? `https://china-wechat-api.padi.com.cn${...}`
       : this.endpointAsUrl                        ? this.endpoint
       :                                             `${origin}/api/v2/travel/${this.endpoint}`;
}
```

`/api/v2/travel/` was the one combination not guessed. Two lessons worth the
space: the call sites are handed **relative** paths (`shop/egypt/…`), so a
pattern anchored on a leading slash finds none of them; and a runner log read
through the API truncates from the front, so a probe that prints its findings
after eighty candidate 404s hides the answer — hence `--only-base`.

### Sailings, and the two traps in their prices

`shop/{vessel}/trips/` is the only place PADI states a date or a price — the
itinerary endpoint carries neither. 2,797 sailings across the 38 mapped boats,
669 of them in our May–August 2027 window.

**The price has no currency beside it**, and the `Currency-code` header the app
sends does not convert: EUR, USD and GBP all answer `1473.0` for the same
sailing. The unit is the vessel's own `window.shop.currency` — Hammerhead II
prices in EUR, Red Sea Aggressor II in USD. Assuming one currency would have put
every Aggressor price out by the EUR/USD rate. A vessel whose page states no
currency has its prices dropped rather than guessed.

**It is a berth price, and stays one until PADI's own extras are added to it.**
Set against our *total* on its own it would show PADI cheaper by exactly the
fees that endpoint does not carry — the failure this site exists to expose,
committed by the site itself. The fee book below is what makes a total out of
it, and only where that book is complete: 169 of 892 departures. On the other
432 the page prints *berth only* and compares nothing.
`tests/test_padi_sailings.py` asserts both halves.

Match quality on (boat, date) is **601 of 892 departures**, and the key is exact:
a date has no spelling, where the itinerary-title join needed en-dash folding and
a harbour alias table to reach a third of that. PADI's calendar coverage varies
per boat — `my-iceberg` lists 22 sailings and none in our window — so a missing
PADI price is evidence of nothing.

### The deals listing

Read 2026-08-29. `/liveaboard-deals/` is the page; `promotions/` is the answer.

**The page cannot be read and never will be.** Probed 2026-08-28 on pages 1, 2,
3 and 99, paced two seconds apart:

| page | HTTP | bytes | deals in HTML |
|---|---|---|---|
| 1 | 200 | 272,103 | 0 |
| 2 | 200 | 272,103 | 0 |
| 3 | 200 | 272,103 | 0 |
| 99 | 200 | 272,110 | 0 |

Zero `application/ld+json`, zero vessel links, no prices. The only difference
between any two of them is the URL echoed back in `og:url` and the `hreflang`
alternates — 30 lines of navigation chrome. `page=` is reflected and never acted
on. `window.pageType = "la_deals"` and the bundle is
`static/travel-app/dist/special_deals.*.js` on the CDN, which the sandbox
refuses with a 403 to CONNECT. **A markup parser for this page is ruled out**,
as is a browser probe from here.

**The endpoint took eleven guesses and one vocabulary.** Ten shapes built on the
word "deal" — `special-deals/`, `special_deals/`, `deals/`, `liveaboard-deals/`,
`liveaboards/deals/`, `shop/deals/`, `trip/deals/`, `trips/deals/`, `deal/`,
`specialdeals/` — all 404 under `/api/v2/travel/`. What found it was reading
`window.info` on the shell itself: the vocabulary there is
`PROMOTION_KIND`/`PROMOTION_SELECTION_KIND`, not "deals", and
`/api/v2/travel/promotions/` answers 200 on the first try. **PADI's word for a
deal is a promotion**, and that is worth more than the endpoint: `promotion` is
also a field on every row of `shop/{vessel}/trips/`, which this file had been
reading for a fortnight without noticing.

It takes the deals page's own query verbatim — repeated `country=`, repeated
`date=` — and **pages honestly**, unlike the HTML: on a 24-row query `page=2`
returns the last four with `next: null` and `page=3` answers 404. The fetcher
still terminates on offer identity rather than on either signal, because a
listing whose HTML lies about paging has no standing to be trusted about it.

| Field | Note |
|---|---|
| `url` | The vessel page. **The only thing that places the deal** — see below |
| `shopTitle`, `shopId` | The vessel, as PADI names it |
| `price` / `compareAtPrice` | The offer and what it is against |
| `currency` | **Stated here**, unlike on `trips/`, where a bare number is in the vessel's own unit and the `Currency-code` header does not convert |
| `dateFrom` / `dateTo` | One exemplar sailing, not the offer's validity |
| `promotion` | `title`, `kind` (`PROMOTION_KIND`), `value`, `description` |
| `countryTitle` | Not read. See below |

**One row per vessel per query**, quoting that vessel's earliest promoted
sailing in the window. So the unit is a boat's offer, not a sailing's — which
is why `promote` keys the change log on the vessel and reports a moved exemplar
as a change rather than as a withdrawal and a new offer.

#### `country` is not where the boat sails, and this is where it costs most

The deals query has to ask for **110 (USA) as well as 120 (Egypt)**, because all
three Red Sea Aggressors are filed under the USA — Aggressor Fleet is American,
the same fact `countrySlug` records on the vessel page. Asking Egypt alone drops
them silently.

Asking for the USA as well is coarse, and the measurement is the argument.
Of **18 deals** in the May–August 2027 window:

| | |
|---|---|
| join to a boat this site carries | **13** |
| join to nothing | **5** — Bahamas Aggressor II, Belize Aggressor III and IV, Cayman Aggressor IV, Roatan Aggressor |

Every one of the five sails the Caribbean. So the country field is wrong about
where a boat is on **28% of what it returns here**, and it cannot place a deal.
The join does: a vessel that maps to a liveaboard.com one is Egyptian because that
fleet is, and one that does not is **named rather than dropped** — in the build
log and on the page — because an Egyptian boat filed under the USA and unmatched
is precisely the case the breadth exists to catch. Only a name a person reads
separates that from a boat in another ocean.

#### Access

`/liveaboard-deals/` is `Allow` for `User-agent: *`, nothing under
`/api/v2/travel/` is disallowed, and plain `date=` is not in the
disallowed-parameter list — unlike `trip_date=`, `departure_date=`,
`date_from=`, `dateStart=`, `dateTo=`, `date_after=` and `activity_date=`, which
are. Paced at one request every two seconds, per the Cloudflare AI-bot limit of
30/min/IP that `www.padi.com/robots.txt` documents. The whole daily read is one
or two requests.

### `availability` is berths left on the sailing, not at the price

Read 2026-08-30 off `data/padi_departures.json`, which has carried the field
since the sailings landed and used it for nothing. A plain integer on all 3,521
sailings, 833 of them in the published season, every one of which lands on a row
this site already publishes. **No request to anybody was needed to establish
any of this.**

Two hypotheses, both tested, one wrong:

- **The hull's capacity.** Ruled out. The value varies across the calendar on 58
  of 61 boats and sits at or under the vessel's stated guest count. It exceeds
  it on 18 sailings, all by one or two — spec tables disagreeing about a crew
  berth, the same margin the cabin counts disagree by.
- **Berths left at the advertised price**, which is what liveaboard.com's ladder
  states. **Ruled out, and this is the one that mattered.**

Against the 584 season sailings where both sellers state a count:

| PADI's figure vs | exact | within 2 | mean error |
|---|---|---|---|
| liveaboard.com's whole-sailing total | **451 (77%)** | 514 (88%) | 1.5 berths |
| liveaboard.com's count at the advertised price | 126 (22%) | 176 (30%) | 7.3 berths |

The two crawls run a day apart, which is what the 23% of near-misses are. So
PADI answers *how many berths are left on this sailing at any price* — the
weaker of the two claims the invariants already distinguish, and the only one
obtainable without a ladder. `promote` puts it in its own slot; letting it fill
the advertised-price slot would have relabelled "22 aboard" as "22 at this
price" on the 249 rows that have no ladder to contradict it.

**They disagree outright on 24 sailings** — 21 where PADI still sells berths
liveaboard.com calls full, 3 the other way. Both are printed under the name of
whoever said it and the day they said it, which are a day apart and stated
separately for that reason.

### The fields that matter

| Field | Example | Note |
|---|---|---|
| `requiredCertification` | `30` | `ITINERARY_CERTIFICATION_CHOICES`. A requirement |
| `experienceRequiredDives` | `20` | The enum, every label of which reads *recommended* |
| `minimalNumberOfDives` | `50` | A plain integer, **not** the enum restated — see below |
| `totalNumberOfDives` / `…Max` | `17` / `18` | The dive count per trip, with a low end |
| `length` | `7` | Nights, stated |
| `harbourDepartureTitle` / `…Arrival…` | `Marsa Alam` | Ports as fields, not parsed out of a title. Stored as `port_from` / `port_to` and **read** — see below |
| `days`, `highlightsDescription` | | Day-by-day and prose. Read for reefs, last of four sources — see #113 |
| `descriptions`, `goodToKnow` | | Logistics and passport boilerplate. **Not a reef source**: `descriptions` is autogenerated ("The trip begins in Hurghada"), `goodToKnow` is visas and insurance |
| `mandatoryOnBoard`, `optionalInAdvance`, `notIncludedInfo`, `whatsIncludedNew` | | The fee book, structured |
| `cancellationMilestones`, `paymentInformation` | | Deposit schedule and cancellation terms |

**`minimalNumberOfDives` is independent of the enum beside it.** Blue Melody
states 30, and the enum can only resolve to 0, 20, 50 or 100 — so it is the
operator's own number, not a rendering of the code. The two are reported
separately (`min_logged_dives` and `recommended_logged_dives`) for that reason.
Whether PADI shows a diver that integer as required or as advice has not been
checked; until it has, it is carried under its own name and folded into nothing.

### It varies per itinerary, which is the point

Sampled across five Egyptian vessels:

| Vessel | Entry bar |
|---|---|
| My Blue Melody | **Open Water**, 30 dives, 15 dives over 7 nights |
| Snefro Pearl | Advanced Open Water, 20 dives, 9 dives over 3 nights |
| My Aphrodite | Advanced Open Water — and **50 / 30 / 50 dives across its own three trips** |
| All Star Ghani | Advanced Open Water, 50 dives, 16 dives over 7 nights |
| DUNE Silky | Advanced Open Water, 50 dives, 14 dives over 7 nights |

Aphrodite settles it: the field is per *itinerary*, not a vessel default. That is
exactly the comparison [#3](https://github.com/PaludaNCode/Liveaboard/issues/3)
was opened for — a beginner week and a 50-dive week are not the same product, and
until now nothing in the dataset could say so.

The endpoint also answers the dive-count problem the invariants describe: it
states a per-trip range, so the low end is a stated figure rather than a derived
one. `itinerary_from_payload()` keeps `totalNumberOfDives`.

### The fee book, and where it hides

**`mandatoryOnBoard` and `mandatoryInAdvance` are the charges a diver cannot
decline**, and membership of those two lists is the whole of the claim: every
one of the 623 entries across 307 itineraries carries `isMandatory: true` and
`isIncluded: false`. 257 itineraries state at least one; 50 state none, which is
PADI saying the fare covers everything and is a disclosure rather than a gap.

**`section` and `kind` are fossils, exactly like the itinerary slugs.** All Star
Ghani's "Marine Park/Port Fees" — a €200 charge — is filed under `section: 10`
("Information") and `kind: 10` ("Full board, including"). Reading either would
have turned a park fee into a meal. 333 of the 623 are `kind: 600` ("Other
fees"), which says nothing. **The `title` is the only field that describes the
charge**, and it goes through the same `LABEL_PATTERNS` table liveaboard.com's
wording does — the fleet is shared, so the words are.

Pointing that table at a second source found gaps in it that had read as
complete: every pattern was written singular, and "Fuel surcharges" is PADI's
commonest mandatory line at 116 entries and matched none of them. Fixing the
plurals, and adding conservation fees and reef tax under the environmental
levy, took the classifier from 182 of 623 entries to 560. It improves both
readers, which is the argument for one table rather than two.

| Field | Note |
|---|---|
| `title` | The charge, and the only field that names it |
| `price` / `priceGross` | 481 of 623 have one. Where it is null, look at `extraValue` before calling the charge unpriced — see below |
| `extraValue` | A price typed as a string. **133 mandatory entries state their figure here and nowhere else** |
| `payedPer` | `LIVEABOARD_EXTRA_PAYED_PER`, verbatim below. Trip on 506, then Night/Person, Week, Dive, Day/Person |
| currency | **Not in the payload.** The vessel's own `window.shop.currency`, same trap as the prices |

`PAYED_PER` maps only the six members that normalise to one diver's bill for
one trip. The twelve it leaves out are transfers, courses, activities, an
"Offset", and two priced per *cabin* — a per-cabin charge needs an occupancy
nobody publishes. An unmapped basis does not drop the line; it makes the whole
bill incomplete, which is the same rule an unpriced line follows.

**A total is claimed only where the bill is complete** — every charge named,
classified and priced in a unit that normalises. 174 of 307 itineraries clear
that, 74 of them join to a liveaboard.com trip, and 169 of the 892 departures end up
comparable total-to-total. The rest keep PADI's berth price and print *berth
only*: a second price with no second total, and the two are not the same kind
of number. That is the whole discipline here — a bill assembled from part of a
disclosure would show PADI cheaper by exactly what it left out.

Two entries with one title are kept as two. DUNE Longara lists "Environmental
taxes" twice on six trips, €100 and €200 under separate `extraId`s; no pair in
the book is an exact duplicate, so folding on the title would halve a real bill.

### `extraValue`: a price where `price` is null, and never a second opinion

Probed 2026-08-31 by `tools/probe_padi_extras.py` over all 438 itineraries in
the store. Of the store's 872
mandatory entries `price` is null on 236, and **133 of those state the figure in
`extraValue`** as a string: Bella 2's Coast Guard Fee is `price: null, extraValue: "5 EUR"` and its
Service fees `"10 EUR"` — two of the three mandatory charges on every trip that
boat sells, read as unpriced, on a vessel whose PADI book is the only fee book
this site has for it. Reading the field takes the store from **259 trips whose
mandatory bill adds up to 332**.

**`price` wins wherever it is one**, because where the two disagree
`extraValue` is the stale half. Measured: they disagree on 27 entries and every
one is a repricing the string did not follow.

| Vessel | `price` | `extraValue` | What it is |
|---|---|---|---|
| Blue Horizon | 56.0 (per trip) | `"8"` | 8 a night over a seven-night trip. Still 56 on its ten-night sailings |
| Blue Melody | 56.0 | `"USD"` | No number in it at all |
| Andromeda | 50.0 (per week) | `"30 EUR"` | An older figure in an older unit |

So the fallback is anchored at both ends and takes the whole string or nothing:
`"14% GST (on onboard purchases)"` must not become 14 of anything, and 43
mandatory fuel surcharges reading `"10 - 20 USD To be confirmed 30 days before
the trip"` are an operator saying the figure is not settled — those bills stay
incomplete, which is the answer rather than a gap. A currency
the string names is the currency — Andromeda writes `"5 USD"` on a vessel PADI
prices in EUR — and the vessel's currency is assumed only where the string names
none. One vessel writes `"8 EU"`; that entry keeps no amount, because a currency
this parser cannot name is money it cannot add up.

### The optional half, and the gear set inside it

`optionalOnBoard`, `optionalInAdvance` and `optionalBookableAdvancePaidOnBoard`
(same probe) are the charges a diver *can* decline — `isMandatory: false` on every entry —
and they hold the two extras this site puts a toggle on. Bella 2 states 50 EUR
for nitrox and 40 EUR per diving day for the full scuba set; both were absent
from the page.

111 distinct titles across the store, of which 39 classify. The 72 that do not
are courses, amenities and single gear items — "PADI Deep Diver", "Espresso
coffee", "Wetsuit" — and they are dropped rather than recorded as unreadable: an
optional extra nobody can name says nothing about whether what a diver *must*
pay adds up, so it cannot make a bill incomplete. Neither can a real price in a
unit `PAYED_PER` will not map, which is where every course lands (`payedPer: 80`,
per course) and most transfers (`50`, "Return, per person").

Three traps in that list, each measured:

- **"PADI Enriched Air Diver (Nitrox)" is a certification, not a gas fill.** 144
  entries, and it matched the nitrox pattern, which would have priced a 100 EUR
  course as the nitrox on the trip — on the toggle this site counts.
  `NITROX_COURSE` now claims "enriched air diver" ahead of it.
- **"Full scuba set" is PADI's bundle row**, on 417 entries, 401 of them
  carrying `fullSetDescription` with its contents, and priced per week (249),
  per trip (76) or per diving day (76). It is the same thing liveaboard.com heads *Full
  equipment rent*, and the bundle is the only honest gear price — adding up
  singles invents a basket the operator never sold.
- **A parenthetical is a qualifier, as in the inclusions.** "Airport Meet &
  Greet (VISA assistance, eligible countries only)" is help with the paperwork,
  not the €25 a diver still pays at the airport.

Where a code appears twice, the first entry wins — the rule `parse_extras`
keeps on the other seller's Optional block. Checked across the store: no code
has an unpriced entry followed by a priced one, so the order costs no figure.

### The two dive counts

`totalNumberOfDives` and `totalNumberOfDivesMax` are a range and the low end is
kept, as everywhere in this project: a range reported as its ceiling flatters
the price per dive. `minimalNumberOfDives` is **not** a dive count at all — it
is the logged-dive bar, and it is read as one.

PADI's count cannot outrank ours: every All Star Ghani itinerary says 16 where
ours say 17, 19, 20 and 21, and of the 142 trips where both speak, 113 disagree
with PADI the lower on 90. It is the fallback where nothing of ours answers,
which is **69 published itineraries** — 43 of them on the vessels PADI alone
sells berths on, where `fetch_itineraries.py` has no tour id to ask about and
never will.

### Not read yet

- **61 entries the classifier still declines**, and it should: "14% GST (on
  onboard purchases)" carries `price: 14.0` and is a percentage of an unrelated
  purchase; "Supervision fees for Level 1 divers…" is conditional on the diver.
  Each of them makes its trip's bill incomplete, which is the safe direction.
- **The single gear items beside "Full scuba set"** — BCD, Regulator, Wetsuit,
  "15 liter tanks", "Flashlight (torch)", "Fins, mask, snorkel (ABC)". Priced,
  and deliberately unread: the set is what a diver renting gear rents, and a
  basket assembled from parts is a price nobody quoted.
- ~~**"Tips for the crew"**~~ — **read now**, and the reason it was worth doing
  deliberately turned out to be a different reason than this said. It is not 376
  scattered entries: **23 of 23** sampled itineraries carry it, `payedPer: 30`
  (per trip), and one states `extraValue: "15% suggested"` — a percentage of an
  unstated base, which the money reader declines like any other. `crew\s+tips?`
  only matched the other word order, so the one charge nearly every operator on
  this seller states was the one nothing read.

  It does **not** land in a counted total, and that is the change this prompted:
  `gratuities` used to be promoted to *customary* from whatever block it was
  listed in, and PADI files this under `optionalOnBoard` on every trip that
  names it — as do all 55 liveaboard.com vessels that state gratuities. The
  seller's block decides now. See `_tier_for` and `docs/plan-missing.md`.

## The vocabulary

Verbatim from `window.info.shop` on any vessel page:

```
ITINERARY_CERTIFICATION_CHOICES = [[10, "Open Water"], [20, "Open Water + Nitrox"],
                                   [30, "Advanced Open Water"],
                                   [40, "Advanced Open Water + Nitrox"], [50, "Tec Diver"]]
EXPERIENCE_REQUIRED_DIVES       = [[0, "No min. logged dives required"],
                                   [10, "20+ dives recommended"], [20, "50+ dives recommended"],
                                   [30, "100+ dives recommended"]]
```

`LIVEABOARD_EXTRA_PAYED_PER`, from the same object, is the fee book's charging
unit and is reproduced in `PAYED_PER`:

```
LIVEABOARD_EXTRA_PAYED_PER = [[0, "Day/Person"], [10, "Night/Person"], [20, "Dive"],
                              [30, "Trip"], [40, "Diving day"], [42, "From, per person"],
                              [44, "From, per vehicle"], [46, "To, per person"],
                              [48, "To, per vehicle"], [49, "Return, per person"],
                              [50, "Transfer"], [55, "Return, per vehicle"], [60, "Activity"],
                              [70, "Week"], [80, "Course"], [90, "Day/Cabin"],
                              [100, "Night/Cabin"], [110, "Offset"]]
LIVEABOARD_EXTRA_SECTION   = [[10, "Information"], [20, "Optional extras"],
                              [30, "Compulsive charges"]]
```

`LIVEABOARD_EXTRA_SECTION` is listed to be explicit that it is *not* used: only
43 of 307 itineraries file anything under "Compulsive charges", while 257 state
a mandatory charge. The section is not where the answer is.

`CERTIFICATION_CHOICES` and `EXPERIENCE_DIVES` in `padi_com.py` map both onto
`DiverLevel`, with tests. Nitrox rides along with the certification in PADI's
vocabulary (10 vs 20, 30 vs 40) but is a gas rather than an entry bar, so each
pair lands on one level.

### The stated harbour, and what reads it

`harbourDepartureTitle` / `harbourArrivalTitle` are the harbour as PADI's own
field. `itinerary_from_payload` stores them as **`port_from` and `port_to`,
two fields**, and `promote` reads them: a statement beats a parse of the same
source's own title, and a statement beats nothing at all wherever the title
named no harbour this code could read. liveaboard.com's title stays
authoritative for a liveaboard.com trip.

They were stored joined with `" - "` under `ports`, and nothing read them —
nor could have. **Two of the eight harbour names contain the separator:**

```
Hurghada - Marriott Marina - Hurghada - Marriott Marina    9 trips
Port Ghalib - Hurghada - Marriott Marina                   1 trip
```

`"A - B - C"` is either `("A", "B - C")` or `("A - B", "C")` and the string
does not say. 436 of 447 split cleanly; the other 11 cannot be split without
guessing, and a closed-vocabulary parse over today's eight names is the rule
that breaks silently the first time PADI names a ninth marina. **So the fix was
the record, not a parser**, and `ports` is gone rather than left beside its
lossless replacement.

The field is more granular than a title — it names berths where a title names
towns — so three of the eight fold in `PORT_ALIASES`, next to the entry the
other source's spelling of the same berth already had:

| stated | folds to |
|---|---|
| `Hurghada Marina` | Hurghada |
| `Hurghada - Marriott Marina` | Hurghada |
| `New Marina Sharm El Sheikh (El Wataneya)` | Sharm El Sheikh |

**Reading it changed no port**, which was expected and is the point. On the 207
itineraries where both a stated and a parsed harbour exist, the two are the
same place every time. What it buys is that the next abbreviation answers
itself, instead of waiting to be noticed by hand the way `(HRG - PRG)` was —
after it had shipped.

**Quality: it is the better source, and it does not cover the fleet.** All 441
trips carry both harbour titles — no nulls, no blanks — across eight distinct
harbour names. Checked against the parsed port on the 212 itineraries carrying
both, the stated harbour and the parsed one are the **same place every time**,
with no contradiction anywhere; and all 28 rows the port fix corrected have a
stated harbour, so the field alone would have answered every one of them. But
**190 of 402 itineraries, on 51 boats, have no PADI trip at all**, and no crawl
changes that — PADI does not sell them. The title parser is not replaceable by
this field; it is checkable against it, which is worth more.

What the field settles is the spellings a title parser has to guess at:

| The title writes | The field states | PADI trips |
|---|---|---|
| `HRG`, `PRG` | `Hurghada`, `Port Ghalib` | 19, all Seawolf Steel |
| `Port Galib` | `Port Ghalib` | 11 — MY Anemone and Blue Horizon |
| `Sharm El sheikh`, `Sharm El Sheik` | `Sharm El Sheikh` | 1, Bella 2 |

Most of Blue Horizon's reach the page under liveaboard.com's spelling, because its trips
match liveaboard.com's and take the name from there; the rest of the column is
PADI's spelling standing alone.

Those are folded in `promote.PORT_ALIASES` for now, on this evidence — which
also recovered five correct pairings, because `fold_ports()` runs the same
table over a title before the join key is taken. Blue Horizon went from four of
its nine trips matched to all nine, and those five now carry PADI's fee panel
and its recommended-dives note where they carried nothing. One of them needed
both halves of the fix at once: PADI titles it `(Port Galib -Port Galib)`. **PRG is
the reason to read the field rather than the letters**: HRG really is
Hurghada's IATA code, but PRG is Prague's and no Egyptian harbour's, so a rule
that resolved airport codes would have filed those seven sailings in the Czech
Republic. The abbreviation is the operator's, and only the field says what it
stands for.

Reading it properly is blocked on the record shape, not on a fetch. `ports` is
one string, and two of the eight harbours PADI names contain the separator
itself:

```
Hurghada - Marriott Marina - Hurghada - Marriott Marina    (9 trips)
Port Ghalib - Hurghada - Marriott Marina                   (1 trip)
```

so the join cannot be undone without guessing where the middle is. The fix is
to store the two harbours as two fields and let them beat a port parsed out of
the same source's title — which takes a crawl to populate, and three more
marina spellings folded (`Hurghada Marina`, `Hurghada - Marriott Marina`, `New
Marina Sharm El Sheikh (El Wataneya)`). Until then the field is collected and
unread, and that is a deliberate state rather than an oversight.

## Two things that lost matches on correct pairings

Both were found by reading a vessel's trips beside liveaboard.com's rather than by any test
failing, and both made a correct boat pairing look like a wrong one.

**PADI mixes dash characters.** It writes *"Name (Hurghada – Hurghada) – 7
nights"* with en-dashes as readily as hyphens. A night-suffix pattern anchored on
whitespace left the trailing dash behind, the ports pattern then failed on a
string not ending in `)`, and `split_title` returned `None` — so the whole title
became the join key. That cost **Unity all three of its matches** while PADI was
plainly selling all three: *North and Brothers*, *The Grand Tour*, *Brothers,
Daedalus, Elphinstone*.

**A harbour is spelled differently inside a trip name.** Our Emperor Asmaa trips
say *"Marsa Ghalib"* where PADI's say *"Port Ghalib"* — the same terminal, and
`promote.PORT_ALIASES` has folded that pair for the port columns all along.
Nothing was folding it inside a title, and it cost that boat **all seven**
matches. `fold_ports()` now applies the same table before the key is taken.

Together with the wider mapping these moved the join from 81 of 308 itineraries
to 104. The lesson is worth more than the nine matches: **a zero join is not
evidence against a pairing.** It was the signal that flagged Unity and Emperor
Asmaa as doubtful, and in both cases the pairing was right and the reader was
wrong.

## The join, measured

Against `data/egypt-2027.json` for Hammerhead II — 14 itineraries from liveaboard.com, 22
theirs, our May–Aug 2027 window:

> **PADI's title minus its night suffix is our `Itinerary.name`.** Ports
> included. That is the join, and it needs no new key on either side.

10 of the 14 matched on it. The four that did not are not join failures:

- *Brothers Light (Hurghada - Marsa Alam)* vs their *(Marsa Alam - Marsa Alam)*,
  and *Brothers Light 3 (Marsa Alam - Hurghada)* vs their *(Marsa Alam - Marsa
  Alam)* — different ports, so by this codebase's own rule different trips.
  Matching them would merge two sailings.
- *Northernmost: Abu Nuhas - SS Thistlegorm - Ras Mohammed - Tiran Island -
  Dahab* against their *Exploring North: Abu Nuhas - SS Thistlegorm - Ras
  Mohamed - Tiran* — the same water under two operator names.
- *Super Diversity: … Sataya (Fury Shoals)* against their *Super Diversity: …
  Sataya* — one qualifier apart.

The last two are the real residue: a name join gets ~70% and the rest need a
human or a reef-set comparison. Loosening the key to close them would also merge
the port variants, which is worse. Twelve of their 22 have no liveaboard.com
counterpart, mostly 3- and 4-night short trips and extra port permutations.

## Sailings PADI sells and liveaboard.com does not

Counted on 2026-08-29, over the published May–Aug 2027 season:

| | sailings | boats |
|---|---|---|
| PADI sailings fetched | 2,797 | 38 |
| …inside the season window | 654 | 37 |
| …landing on a row liveaboard.com also lists | 601 | |
| …on a date liveaboard.com does not list | **53** | 14 |

All 14 are boats the dataset already carries. Blue Storm (15) and Blue Seas
(14) are near-complete weekly seasons PADI sells that liveaboard.com does not
list at all; Ghazala Adventure (6) and Ghazala Explorer (5) follow, then nine
boats with one or two each.

`promote` creates a row for each. **The berth price is PADI's and its
provenance says so; the fees are the vessel's own fee book**, which the boat
charges on board whoever sold the berth — the same reason both sellers' totals
already carry the same nitrox and gear. Such a row carries no `padi_price`:
repeating one seller's figure into the second seller's field would print as two
sellers agreeing about a sailing one of them does not offer. The page marks
them `PADI only`.

The trip name is PADI's title minus its night count, folded onto liveaboard.com's where
`padi_key` matches — 19 of the 53 join a trip liveaboard.com also carries, and the rest
found 24 new ones. Where two of a boat's own itineraries share that key the
fold is refused rather than guessed: Blue Horizon sells *Rocky, Zabargad & St.
Johns* from two harbours and nothing can say which one PADI means.

Two things this uncovered:

- **`compare_key` kept the word "and" while stripping `&` and `,`**, so an
  operator writing one reef list three ways reached us as two trips. Folding
  the conjunction merges exactly two pairs across all 317 trips of the dataset
  it was measured on, both one trip typed twice, and nothing in PADI's own
  book. It also gained a PADI fee book on 13 itineraries that had none.
- **Itinerary ids are truncated to 96 characters**, so two long names that
  agree up to the cut collide — and the ports are at the end. `Dataset.from_dict`
  keys by id and would silently keep one. `promote` now raises.

## The 24 vessels that mapped to no liveaboard.com boat

Fetched 2026-08-29, all 24, no failures, 737 sailings. Two turned out not to be
PADI's side at all: **`my-avo` is our AVO and `my-blue` is our Blue**, and both
sat outside `aliases` and `absent` alike — the one state that file exists to
make impossible. Confirmed the way every other pair there was, on trips rather
than names: PADI's *Daedalus & Elphinstone (Port Ghalib - Port Ghalib)* is
byte-identical to AVO's only trip, and Blue matches on two independently.

The other 22 are now in `aliases` too, with ids minted in that file, and listed
under `padi_only`. **179 of their sailings fall inside the published season, on
12 boats** — far short of the ~424 a per-boat average predicts, because these
calendars are much shallower: twelve of the 24 have nothing in the window at
all, and VIP One prices all 18 of its sailings at zero, which `_departure_book`
drops rather than storing as free.

`padi_only` means PADI is the only source of **sailings**, not of the vessel.
Ten of the 22 have a liveaboard.com fee panel in `data/fees.json` — that site
carries the boat and publishes no departures for it. `promote` needs no flag for
the difference: a boat with our fee book uses it like any other, and one without
falls back to PADI's per-itinerary book, which is what `Itinerary.padi_sourced_fees`
records. Where both exist the vessel's own panel wins outright; the fallback is never a merge,
because the two disclose at different resolutions and a line from each builds a
bill neither seller quotes.

Two facts a PADI-only vessel has nowhere else to get:

- **Name** — `window.shop`'s `title`. A boat published under a title-cased slug
  is one this code named rather than one anybody wrote.
- **Operator** — `window.shop`'s `fleetTitle`, minus the trailing "Fleet" that
  is PADI's furniture. Kept **verbatim**, shouting included, per
  `OPERATOR_ALIASES`' standing rule, and deliberately never folded onto a
  company held from the other source.

  That fold was written and removed. PADI files MY Blue and MY Blue Pearl in one
  "BLUE PLANET Fleet"; MY Blue is our Blue, whose own departures say "Blue
  Planet Liveaboards", so a `BLUE PLANET` → `Blue Planet Liveaboards` alias
  tidied a duplicate off the operator list. It also asserted, on nothing but a
  fleet label, that a boat the other source says nothing about is run by that
  company. **A fleet on a booking site is not established to be the operating
  company**, and these are two different hulls: shop ids 19679 and 16676, 24
  guests at 43 m built 2016 against 20 at 36 m built 2003. Two operator rows
  that may be one company is a cosmetic cost; naming the wrong company is the
  claim this site exists to catch other people making.

Not every boat states a fleet; three of the ten land under "Operator not
captured", which is true rather than tidy. **Confirmed at the source rather
than inferred from a null**, 2026-09-01: `window.shop` on `my-anemone`,
`my-heaven-saphir` and `my-independence-ii` states `fleetTitle: ""` — an empty
string PADI publishes, not a fetch that failed or a regex that missed. Those
three carry 44 in-season sailings, have no liveaboard.com vessel page and so no
`Product.brand.name`, and their description prose names no company. There is
nothing left to read, and inventing one from marketing copy is the assertion
this file already refuses to make for Blue Pearl on better evidence.

### The vessel page also states cabins, length and year built

Server-rendered in the same response `window.shop` comes from — plain
`urllib`, no browser, no CDN bundle. `/liveaboard/egypt/my-anemone/`, read
2026-09-01:

    Cabins 16 · Length / Width 45 m / 8 m · Year built / renovated 2022 / 2025 ·
    Rental equip. YES ($) · Internet FREE · Nitrox FREE

`fetch_padi.py` already fetches this page per vessel for the country, currency,
name and fleet, so the strip costs no extra request. It answers `cabins` for
the 6 boats that have none, `length_m` and `year_built` for the 5 length-less
vessels PADI carries (DUNE Longara, DUNE Titan, Snefro Love, Snefro Pearl,
Snefro Target), and gives a second reading of nitrox inclusion to check the fee
panel's against. Precedence is the standing rule: the vessel's own panel wins
where it exists, PADI fills where it does not, never a merge.

**Negative, and the reason `guests` stays open: the page states no guest count
anywhere.** The whole rendered body searched for every numeric form of guests,
divers, passengers, people, pax — zero hits, and the strip has no such row.
Vita Xplorer is the boat this leaves stranded: it is the one vessel with a
liveaboard.com panel whose specification table leaves the field blank, so its
answer is a parser fix on that `<dl>` or nothing. The other six missing a guest
count are reachable through the itinerary fragment's *Group Size*, which
`promote`'s guest chain already reads.

## One property to preserve

A stated requirement is a safety gate, and PADI's two fields are not the same
kind of claim. The certification choice is a requirement; every
`EXPERIENCE_REQUIRED_DIVES` label says *recommended*. So they are reported
separately — `min_level` and `recommended_logged_dives` — and neither is folded
into the other. Hardening somebody's advice into a gate is the same class of
error as softening their gate into advice, and `classify.infer_level` never
softens a stated requirement.

`extract_requirements()` (prose) and `requirements_from_choices()` (codes) both
return `None` rather than a default when nothing is stated. An unmatched PADI
record should warn rather than vanish: `parse()` emits the itinerary titles and
warns about the absent entry bar, because a page naming 22 trips is not a failed
fetch.

## Access

**Open from this environment as of 2026-08-28**, unlike when this file was
written. The 403-on-CONNECT it used to record no longer happens:

```
GET https://www.padi.com/          200  Drupal 10
GET https://travel.padi.com/       200  Django + AngularJS, Next.js under /s/
```

The allowlist is narrow and is worth knowing precisely: `*.padi.com` and
`liveaboard.com` resolve, everything else — the CDN included — gets a 403 to
CONNECT. So a first fetch no longer needs a runner but a *browser* probe still
does, per **Ruled out** above.
[#1](https://github.com/PaludaNCode/Liveaboard/issues/1) still covers the runner
path for when the policy closes again.

Both hosts allow crawling. `www.padi.com/robots.txt` names `ClaudeBot` with
`Allow: /` and `Crawl-delay: 2`, and says the delay is kept in sync with a
Cloudflare AI-bot limit of **30 requests/min/IP** — so pace at one request every
two seconds or be rate-limited by the edge rather than by robots. The probe here
paced at 1.2–2 s and was never throttled.
