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
| `sitemap-travel-dive-operators-page_1.xml` | **All 269 liveaboards, 58 of them `/liveaboard/egypt/`.** The whole inventory in one 3 MB file |
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
  Chromium loads the page, then cannot load the app. Runners can reach the CDN.

## The one thing still missing

Per-itinerary requirement **values**. PADI stores the entry bar as two coded
fields, and a vessel page ships their vocabulary but never a value —
`"certification"`, `"experience_required"` and `"min_certification"` appear zero
times in 395 KB of HTML. The vocabulary, verbatim from `window.info.shop`:

```
ITINERARY_CERTIFICATION_CHOICES = [[10, "Open Water"], [20, "Open Water + Nitrox"],
                                   [30, "Advanced Open Water"],
                                   [40, "Advanced Open Water + Nitrox"], [50, "Tec Diver"]]
EXPERIENCE_REQUIRED_DIVES       = [[0, "No min. logged dives required"],
                                   [10, "20+ dives recommended"], [20, "50+ dives recommended"],
                                   [30, "100+ dives recommended"]]
```

`CERTIFICATION_CHOICES` and `EXPERIENCE_DIVES` in `padi_com.py` map both onto
`DiverLevel`, with tests. What is *not* written is the plumbing that finds a
value, because no value has been seen — the fetch-first rule applies to the
payload shape exactly as it applied to the URL shape, and guessing the latter is
what produced the 404s above.

Two ways to get one, both needing a runner:

1. **Read the bundle.** `static/travel-app/dist/itinerary.*.js` on the CDN
   builds the URL the popup calls. Cheaper, and the answer is a plain URL that
   this environment can then fetch directly.
2. **Drive the browser.** `tools/probe_network.py --url <a vessel page>` already
   records XHR and reports JSON shape; the popup opens via an Angular click, so
   this needs a click step the tool does not have yet.

Take (1) first. If the endpoint turns out to need a session or a signed
parameter, (2) is the fallback and the fee scrape's browser is already there.

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
