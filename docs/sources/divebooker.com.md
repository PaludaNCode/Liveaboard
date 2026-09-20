# divebooker.com — source interface

Where each fact this site publishes comes from: the URL, the node or selector,
and whether reading it needs a browser.

Read on **2026-09-20** by `tools/probe_divebooker.py`, from a GitHub runner —
the development sandbox's egress allowlist refuses divebooker.com outright
(403 on the CONNECT, apex and www alike), so `.github/workflows/probe.yml`
with `only: divebooker` is where these answers come from. Runs
[35479407276](https://github.com/PaludaNCode/Liveaboard/actions/runs/35479407276)
and [35479624336](https://github.com/PaludaNCode/Liveaboard/actions/runs/35479624336),
11 requests apiece.

Same two rules as the other two source maps:

1. **Negatives carry equal weight.** They are the last section and not an
   appendix.
2. **A probe that discovers something updates this file in the same commit.**

**Status: read, not parsed.** Nothing in `src/` fetches this host. Whether it
becomes a third seller at all is the open question in
`docs/plan-divebooker.md`, stage 0.

---

## robots.txt, and the anonymous request

`https://divebooker.com/robots.txt` and the `www` both answer **200, 480
bytes**, `Server: cloudflare`, to this project's own user agent.

**They answer 403 to `urllib.robotparser`.** `RobotFileParser.read()` sends no
`User-Agent` header at all, so what it gets is the site's answer to
`Python-urllib/3.11` — and a 403 on robots.txt sets `disallow_all`, which
reaches `can_fetch()` as a confident refusal of every URL on the host. The
first runner pass reported exactly that: *robots.txt reachable*, then
`BLOCKED … robots.txt disallows` on every page including robots.txt itself.
It is not the site's position. It is the crawler never having said who it is.

**`scrape/base.py` has this hole.** `PoliteFetcher._robots_for` calls `read()`,
so a Cloudflare-fronted source would be refused wholesale, silently and with
the refusal attributed to the site. It does not affect the two live sources —
both serve robots.txt to anonymous requests — and it is **not fixed here**: it
changes how two working crawls fetch, and it needs its own guard.

What the file states, once read as ourselves:

| | |
|---|---|
| `Disallow:` paths | **10** |
| of those, `can_fetch()` permits | **1** — `/boatsearch` |
| `Crawl-delay` | not stated |

The eight the probe prints: `/rc/`, `/destinations./`, `/destinations/`,
`/aquatories/`, `/countries/`, `/owner/`, `/profile/`, `/admin/`.

**That sentence was wrong, and it is kept because it cost a week's worth of
wrong conclusions.** It read: *"`/boatsearch` is refused by the file and
permitted by the parser — the same shape as liveaboard.com's blank-line bug,
and the same rule applies: that yes is a parser artefact and not permission.
Nothing here fetches `/boatsearch`."* It is not. The file, read verbatim,
ends its `*` record and then opens another:

    Disallow: /profile

    User-agent: turnitinbot
    Disallow: /boatsearch

`/boatsearch` is refused to **turnitinbot** and to nobody else. `can_fetch()`
was right all along; the probe was reading every `Disallow:` line in the file
without asking whose record it sat in, and then announcing a mismatch against
its own miscount. `stated_disallows` groups by `User-agent` now. **A rule in
another agent's record is not a rule about us** — and the path this cost us is
the one that answers how many boats this seller lists.

What the `*` record really refuses: `/rc/`, `/destinations./`,
`/destinations/`, `/aquatories/`, `/countries/`, `/owner/`, `/profile/`,
`/profile`, `/admin/`. Those are listing and account paths and a crawl may not
use them. `/boatsearch` is not among them.

No `Crawl-delay` means the pace is ours to choose. The host is **not** in
`CHECKED_HOSTS`, so it gets the five-second default for a host nobody has read,
and it stays there until somebody writes down a reason for a number.

## Entry points

| Purpose | URL | Notes |
|---|---|---|
| Inventory | `/sitemap.xml` | **5,715 URLs**, one flat list, no index. Declared in robots.txt on the apex while the site links the `www` |
| Blog | `/blog/post-sitemap.xml`, `/blog/page-sitemap.xml` | 116 and 5 URLs. Nothing priced |
| Deals | `/specials?et=3&e=1` | The sitemap's own spelling of it. 322 KB, **130 priced trips in JSON-LD** |
| Vessel | `/{slug}-haz{id}` | 516 hulls worldwide. Both shapes below |
| Country | `/{country}-daz{id}` | 57. Egypt is `/egypt-daz3881` |
| Region | `/{region}-laz{id}` | 49. e.g. `/egypt-fury-shoals-laz10` |
| Port | `/{country}-{port}-eaz{id}` | 1,232 across three id widths |
| Dive site | `/{site}-baz{id}` | 3,716 — the largest family by far |
| Operator | `/{name}-jaz{id}` | 89. e.g. `/aqua-cat-cruises-jaz105` |
| Campaign | `/{name}-iaz{id}` | 16. e.g. `/black-friday-liveaboard-diving-deals-iaz103` |

**The namespace is flat and typed by a suffix.** Not `/diving/egypt/{slug}`:
everything sits at the root and the two letters before the id say what it is.
The counts above are that suffix, bucketed over all 5,715 sitemap URLs, and
`tools/probe_divebooker.py`'s `FLAT_ID` is what reads it — the first pass
grouped by path depth, put all 5,715 in buckets of one, and printed twelve
buckets of one as if it were a census.

## Fact to location

Everything below is **server-rendered JSON-LD over plain HTTP**. No browser.

| Fact | Where | Evidence |
|---|---|---|
| Departures for a vessel | `Event` nodes on the vessel page | `pearl-of-papua-haz102`: `Event×42` beside `Offer×42` |
| Price per departure | the `Offer` on each `Event` | 54 nodes carrying a price on that one page |
| Trip | `TouristTrip×32` on the same page | fewer than the Events, so a trip is sold on several dates — the shape this project already merges on |
| Where a trip goes | `Place×32`, and the page's links to `/*-baz#####` | the dive-site family, stated as links rather than prose |
| Port | the page's `/*-eaz#####` links | `pearl-of-papua-haz102` links `/indonesia-port-of-sorong-eaz18153` ten times |
| Vessel with no departures | `Product` + one `Offer`, no `Event` | `southern-cross-haz100`: `Product×1, Offer×1, Brand×1, Organization×1` |
| Operator | `Brand` on that `Product` | the same node PADI states it in, and the rule *a fleet is not an operator* already covers |
| Discounted trips | `/specials?et=3&e=1` | `TouristTrip×130`, `Offer×130`, `UnitPriceSpecification×130` |
| A trip's rate basis | `UnitPriceSpecification` | present wherever an `Offer` is; unread, and the unit is exactly what this project refuses to assume |

**A vessel page is one request for a whole boat's season** — 547 KB for 42
departures on Pearl of Papua. That is liveaboard.com's vessel-month page
without the month selector, and it is the cheapest shape any of the three
sources has offered.

### What a departure states

Read 2026-09-20 by `tools/probe_divebooker_departures.py` over three Egyptian
hulls the Egypt country page links —
[run 35479952153](https://github.com/PaludaNCode/Liveaboard/actions/runs/35479952153).
**219 `Event` nodes.** Every one of them carries:

| Key | Value |
|---|---|
| `name` | *North Wrecks, Ras Mohamed, Tiran, Brothers & Safaga, Egypt* |
| `startDate` / `endDate` | `2026-10-10` / `2026-10-17`, plain ISO dates |
| `location` | `Egypt` — the country, not the port |

And every `Offer` carries `price`, `priceCurrency` and `availability`. So this
source states a fare, a currency and a date to key on, which is the whole of
what a third seller needs.

**Two nestings state the same sailing, and only one of them is priced.** Of
those 219 Events, **23 carry `offers`** — and that is not 23 sailings out of
219. The page publishes each trip twice over:

- `Offer` → `itemOffered` → `TouristTrip` → `subjectOf` → `Event`. Discovery II
  has 49 of these, Bella 2 has 3. The `Offer` here carries `name`,
  `itemOffered` and `validThrough`.
- a top-level `Event` carrying `offers`, plus `url`, `id`, `duration`,
  `organizer`, `eventStatus`, `description` and `image`. **Ten on each of the
  two larger boats and three on Bella 2**, which has three sailings in total.

A parser collecting `@type == Event` gets each sailing twice, one copy without
a price — the shape `jsonld.walk` flattens and a census is what makes visible.
The working reading is that the `TouristTrip` chain is the **whole** departure
list and the top-level Events are a capped ten with a booking URL attached;
that ten appearing on two unrelated boats is what suggests a cap rather than a
meaning. **Settled since, by reading the whole Egyptian fleet**: every boat selling
more than ten sailings states exactly **ten** of the second kind — Alsuraya 10
of 54, Blue Horizon 10 of 142, Red Sea Aggressor V 10 of 143 — and Bella 2,
which sells three, states three. So the top-level Events are a capped ten with
a booking url and the `TouristTrip` chain is the whole list. The fold takes the
chain and lets the ten add a url.

**Both currencies, in one fleet.** 13 offers in EUR and 10 in USD across three
Egyptian boats — Discovery II quotes EUR 1,254 and the boat above it USD 2,760.
`changes.repriced`'s rule already covers what that means for a diff: a fare in
a different currency has not moved.

**Three of the three hulls read are already in this fleet** — `bella-2-haz432`
and `discovery-ii-haz395` among them — so the Egypt country page is a working
entry point and the boats join ours by name. What an id is keyed on, and
whether a slug is stable, is unasked.

## The fleet is about 75, and the country page shows a carousel

Read 2026-09-20 by `tools/probe_divebooker_search.py`
([run 35519334453](https://github.com/PaludaNCode/Liveaboard/actions/runs/35519334453)),
over `/boatsearch?et=2&e=3881&ym=YYYYMM` — the site's own search URL: entity
type 2, the Egypt id the country slug already carries (`egypt-daz3881`), and a
year-month.

| | |
|---|---|
| Count the page states | **75** |
| Hull links on the page | **20** — so it is paged |
| Across 2027-05, -06 and -07 | 22 distinct hulls, **16 of them new to the book** |
| Boats in the streamed payload | with `minPrice`, `minPriceDay`, `currencyId` |

The sixteen are boats this site already carries — Blue, Blue Melody, Blue
Storm, DUNE Longara, Emperor Asmaa, Ghazala Adventure, Iceberg, Ocean Lovers,
Odyssey, Sea Serpent, Serenity, Sinai Star, Titan, Topaz, Yachtiano. So the
Egypt country page is a landing page with a carousel on it, and
`fetch_divebooker.py` reading its links as an inventory is the same error as
reading liveaboard.com's featured strip as a fleet. **Discovery has moved to
this search.**

### It pages on `p=`, and nine other spellings do nothing

Read 2026-09-20 by `tools/probe_divebooker_search.py --try-params`
([run 35520903255](https://github.com/PaludaNCode/Liveaboard/actions/runs/35520903255)),
against the answer page one already gives:

| Parameter | Hulls | New |
|---|---|---|
| **`p=2`** | 20 | **20** — amelie-adventures, aml-hayaty, aphrodite, blue-pearl, blue-seas, destiny, … |
| `page=2`, `pg=2` | 20 | 0 |
| `offset=20`, `start=20`, `skip=20` | 20 | 0 |
| `limit=100`, `perPage=100`, `size=100`, `take=100` | 20 | 0 |

Nine wrong spellings return the first twenty again — no error, no hint, no
empty page — so **a paging parameter here cannot be assumed, only measured**:
a run that typed `page=` would have walked the same twenty boats four times
and reported a complete fleet. The search renders no numbered links,
`/api/`, `/graphql/` and `/_next/data` literals are absent from the page, and
it carries **no server action id**, so there was nothing to follow and the
experiment was the only route. It is safe in a way guessing a hull id is not:
a wrong parameter here is visible in one request, where a wrong hull id
silently reads another boat.

`divebooker_com.PAGE_PARAM` carries that measurement beside it, and
`fetch_divebooker.py` walks each season month until a page adds no hull the
month has already shown — **the repeat is the stop, never a page number**,
because the failure mode above is exactly a paginator that keeps answering.

### The fleet, read whole

Read 2026-09-20
([run 35522066901](https://github.com/PaludaNCode/Liveaboard/actions/runs/35522066901)),
four months walked, 117 pages fetched at the five-second pace:

| | |
|---|---|
| Hulls the search links | **92** — the stated 75 is one month; four months are more |
| Season sailings read | **888** (2027-05-01 to 2027-08-31) |
| Hulls stating no sailing at all | 17, every one of them carried as read rather than as empty |
| Against the country page | 10 hulls, 148 season sailings |

**59 of the 92 map to a boat this site carries**, and the 33 that do not are
boats neither of the other two sellers lists — Aml Hayaty, Argo, Ashrafi,
Bismarck, C Echo 2, Freedom III and IV, Galaxy 720, Golden Dolphin I,
Hammerhead I, Icon, Independence III, Omneia Spirit, Sea Treasure, South
Moon 1, VipOne and seventeen more. That is the answer to *what does a third
seller add*: a sixth of the Egyptian fleet, by hull.

## The payload the page streams to itself

Read 2026-09-20 by `tools/probe_divebooker_flight.py` over
`/red-sea-aggressor-ii-haz285`
([run 35518848545](https://github.com/PaludaNCode/Liveaboard/actions/runs/35518848545)).

**It is not fetched. It is in the HTML.** 51 `self.__next_f.push` chunks
decode to **206 KB, a third of the 628 KB page**, and there are **zero**
`/api/`, `/graphql/` or `/_next/data` literals in the HTML or in the first six
script chunks the page loads. So a price a reader sees that this project does
not hold is not behind an endpoint; it is in bytes we already fetch.

What the payload adds over the JSON-LD:

| | |
|---|---|
| **Cabins** | one node per cabin type — `occupancy` (*2 Guests*), `numberCabins` (*8 Cabins*), `bathroom`, `aircon`, bedding, images, `inseanqCabinTypeId`. **No per-cabin price in anything read** |
| **A boat's own minimum** | `minPrice` and `minPriceDay` on each boat card — 179 and 156 for one hull, beside `currencyId` |
| **A currency table** | `currencies: {"current": "USD", …}` and `rates: {"1":"1.0000","2":"0.8708","4":"33.3167",…}`, keyed by the same numeric `currencyId` |

**The currency table is the thing to be careful about.** The page states the
currency it rendered *in*, and carries the rates to convert. So a `price` read
from this source is denominated in whatever currency the response chose — and
the book's 570 USD against 407 EUR may be a fact about the boats or a fact
about the requests. Until that is settled, comparing a divebooker figure with
ours is comparing against an unknown base, which is a second reason the fares
stay withheld.

**And the fee book is not hiding client-side.** 253 distinct keys in the
payload, and the money-shaped ones are the seventeen above: **not one**
matches `fee`, `extra`, `includ` or `exclud`. Whatever this seller discloses
about required extras, it is not on the vessel page in any form — rendered or
streamed.

## What is ruled out

- **No browser.** Seven pages read over plain `urllib`, every one of them
  server-rendered with its JSON-LD intact. Nothing here needs Playwright, and
  a weekly browser run would be a cost with no finding behind it.
- **No JSON endpoint to find.** Zero `/api/`, `/graphql/` or `/_next/data/`
  literals in the served bytes of any of the seven pages. PADI's deals listing
  needed that hunt because its shell had no prices in it; this one has them.
- **No `__NEXT_DATA__` anywhere.** It is Next.js App Router, whose payload
  streams through `self.__next_f.push` — present on all seven pages. Reading
  it is not necessary while the JSON-LD answers, and it is where to look if
  one day it does not.
- **No price in the markup.** Price-shaped strings in the HTML: **0**, on every
  page including the ones carrying 54 priced JSON-LD nodes. A selector-based
  parser would find nothing at all here.
- **`/boatsearch`, `/destinations/`, `/countries/`, `/aquatories/` are refused**
  by the file. The parser permits the first; that is not permission.
- **A port page can be empty.** `/philippines-dalaguete-eaz17470` answers 200
  with `TouristDestination×1, ItemList×1` and no priced node — a listing with
  nothing in it, which is the source's own way of saying so, and not the same
  as a page that failed. The distinction `carry_unread` exists for.
- **No berth count, and no list price.** `Offer.availability` is
  `https://schema.org/InStock` — a state, not a number — on every offer read,
  and nothing in 219 departures states a struck-through or previous price.
  Whatever this source becomes, *places left* and *on sale* are not questions
  it can answer. `AggregateOffer` sits once per vessel page and is unread; it
  is the only remaining candidate for a low/high figure.

## The fleet, read whole

`tools/fetch_divebooker.py` read every hull the Egypt country page links, on
2026-09-20: **10 vessels, 977 departures, no warnings**. All ten are boats this
site already carries, matched by exact equality of the name the page states
(`Event.organizer`) against ours — nine slugs equal their boat id and `silky`
is `dune-silky`. `data/divebooker_aliases.json` holds the map, hand-maintained
like PADI's.

| | |
|---|---|
| Departures | 977, spanning 2026-09-23 to **2029-12-08** |
| Priced | 977 of 977 |
| Currencies | 570 USD, 407 EUR |
| Lengths | 947 of seven nights; also 3, 9, 10, 11 and 14 |
| Inside the published season | 148 |
| Of those, joining one of ours on `(boat, date)` | **148** |

Not one in-season sailing this source lists is a sailing this site does not
already carry, so nothing here creates a row.

## The fare, and why none of it is published

The block `promote` writes states `fares: withheld`, and this is the
measurement behind it. On the 70 in-season rows where both sellers quote the
same currency, **57 agree to the cent**. Thirteen do not, on two boats:

| Sailing | Ours | Theirs |
|---|---|---|
| Red Sea Aggressor IV, 2027-07-24 | 2,699 | **5,398** |
| Red Sea Aggressor II, 2027-07-31 | 2,760 | 3,060 |
| Blue Horizon, 2027-05-08 | 1,394 | 1,743 |
| …ten more, 20–350 apart | | |

**5,398 is exactly twice 2,699, on the same seven nights** — and the obvious
explanation is wrong. A date can carry more than one offer here: Red Sea
Aggressor IV states **161 offers over 143 sailings**, and `departures()` now
keeps the cheapest of them, which changed 30 dates across the fleet. It did
not change this one. 2027-07-24 states **one** offer, at 5,398, while the
weeks either side of it state one each at 2,799 and 2,899, and every one of
the 13 disagreeing rows has a single offer on its date.

So the doubling is not a parser artefact and not a second cabin class. It is
the seller stating a number this site cannot account for, and `Offer.price` is
therefore a per-person berth on most rows and something unestablished on at
least one. That is the same shape as liveaboard.com's unitless gear figure and
it gets the same answer: the figure is kept in `data/divebooker.json` where a
person can read it, and nothing totals it, compares it or prints it. The other
78 in-season rows quote EUR against our USD and cannot be compared without
converting first.

**The next probe is a booking page**, if one can be reached without a path
robots.txt refuses — that is where a per-person figure would say so.

## Not yet asked

- **Which nesting is the departure list.** The working reading above, held
  against the dates: do the ten top-level Events appear among the forty-nine?
  Everything a parser does here depends on that answer.
- **How many Egyptian hulls there are.** Three were read; the country page's
  own link count is in the run log and the fleet has not been enumerated.
- **What `AggregateOffer` holds.** One per vessel page, unread, and the last
  place a low/high figure could be.
- **Whether any fee disclosure exists at all.** Nothing read so far carries
  one, and *no fee lines means nobody looked* — so this is a question, not a
  finding that the source charges nothing.
