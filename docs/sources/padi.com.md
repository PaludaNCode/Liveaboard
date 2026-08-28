# padi.com — source interface

**Egypt only, and not wired up yet.** Nothing on the site reads from padi.com
today. The host became reachable from this environment on 2026-08-28 and the
Egyptian vessel pages have now been read, so this file has stopped being a list
of guesses; it is a map. Nothing is promoted from it.

The scope stays what [#3](https://github.com/PaludaNCode/Liveaboard/issues/3)
set: **requirements and accreditation, not prices.** PADI Travel is weak on
departure-level pricing and strong on the certification bar, which is what the
price comparison needs in order to be fair — comparing a week that requires 50
logged dives against one that takes beginners is not comparing like with like.
The probe confirmed both halves of that: the entry bar is a first-class coded
field on their side, and the prices are the next four departures at best.

## Entry points

Everything below answers 200 over plain HTTP, no browser.

| URL | What it gives |
|---|---|
| `sitemap-travel-dive-operators-page_1.xml` | 269 liveaboards, 58 of them `/liveaboard/egypt/`. A starting set, **not** the whole inventory — see below |
| `/liveaboard/egypt/{vessel-slug}/` | Server-rendered. Vessel, fleet, JSON-LD `Product`, every itinerary title, PADI courses taught, spec table |
| `/liveaboard/egypt/{vessel}/{itinerary-slug}/` | `<title>` and `<meta description>` per trip, server-rendered — and **nothing else**: the body is filled by XHR |
| `/liveaboard-diving/egypt/` | Country landing page. Four featured vessels; the sitemap is the real inventory |

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

## Matching a PADI vessel to one of ours

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
cabin count printed against ours for a person to accept or reject.

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

### The fields that matter

| Field | Example | Note |
|---|---|---|
| `requiredCertification` | `30` | `ITINERARY_CERTIFICATION_CHOICES`. A requirement |
| `experienceRequiredDives` | `20` | The enum, every label of which reads *recommended* |
| `minimalNumberOfDives` | `50` | A plain integer, **not** the enum restated — see below |
| `totalNumberOfDives` / `…Max` | `17` / `18` | The dive count per trip, with a low end |
| `length` | `7` | Nights, stated |
| `harbourDepartureTitle` / `…Arrival…` | `Marsa Alam` | Ports as fields, not parsed out of a title |
| `days`, `descriptions`, `highlightsDescription`, `goodToKnow` | | Day-by-day and prose |
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

### Still to do

Nothing in the pipeline fetches this yet. `requirements_from_payload()` and
`itinerary_from_payload()` read a response, with tests pinned to a real one, but
no tool walks the 58 vessels and no promotion consumes the result. The join is
already measured (below) and unchanged: PADI's title minus its night suffix is
our `Itinerary.name`.

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

`CERTIFICATION_CHOICES` and `EXPERIENCE_DIVES` in `padi_com.py` map both onto
`DiverLevel`, with tests. Nitrox rides along with the certification in PADI's
vocabulary (10 vs 20, 30 vs 40) but is a gas rather than an entry bar, so each
pair lands on one level.

## The join, measured

Against `data/egypt-2027.json` for Hammerhead II — 14 itineraries ours, 22
theirs, our May–Aug 2027 window:

> **PADI's title minus its night suffix is our `Itinerary.name`.** Ports
> included. That is the join, and it needs no new key on either side.

10 of our 14 matched on it. The four that did not are not join failures:

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
the port variants, which is worse. Twelve of their 22 have no counterpart of
ours, mostly 3- and 4-night short trips and extra port permutations.

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
