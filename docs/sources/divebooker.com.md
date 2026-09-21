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

**Status: a third seller.** `src/liveaboard/scrape/divebooker_com.py` reads it,
`tools/fetch_divebooker.py` fetches it and `.github/workflows/divebooker.yml`
runs that daily. Its fares are on 777 departures, it founds seven of its own,
and its *Price details* panel is the third fee book on the page. That sentence
read *"read, not parsed. Nothing in `src/` fetches this host"* while stage 0 of
`docs/plan-divebooker.md` was open; the owner has since answered it.

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

### The fee book the sweep missed

`details` in the streamed payload, titled **Price details**, three columns on
every one of them. Counted 2026-09-20 by
`tools/probe_divebooker_fee_shape.py` over all 92 hulls
([run 35533600353](https://github.com/PaludaNCode/Liveaboard/actions/runs/35533600353)):

| | |
|---|---|
| Hulls carrying the block | **75 of 92** — the 17 without are the 17 that state no departure either |
| Blocks per hull | 1 to 23, **606 in all** — it is per *trip*, not per vessel |
| On hulls with several | **42 state the same surcharges on every trip, 25 differ** |
| Columns | `included` / `notincluded` ("Obligatory surcharges") / `extra` ("Extra cost"), on all 606 |

And what the lines hold:

| Column | Lines | Priced |
|---|---|---|
| `included` | 5,244 | **0** — an inclusion list, which is what it is for |
| `notincluded` | 986 | **792 (80%)** |
| `extra` | 4,276 | 303 |

* **Currency is stated in the prose**: EUR on 796 priced lines, USD on 299 —
  and 796 of them disagree with the currency the *page* rendered in, which is
  the right way round. The fare takes its currency from the payload because
  the offer's label is a static per-vessel thing; a fee line says its own.
* **Unit is stated on 85%**: per person 507, per trip 366, per day 42, per
  person per day 5, per tank 5 — and **170 state none**, which is the
  `unit_unstated` case this project already has a rule for.
* **Where it is paid is stated too**: on board 626, in advance 161, at the
  airport 17, cash 51.
* **The existing vocabulary already reads it.** `scrape.fees.classify_label`
  resolves 4,311 label instances — dive insurance, visa, gratuities, gear
  rental, airport transfer, 15l tanks, combined fees, fuel surcharge, marine
  park, environment tax, nitrox — against 951 it does not, of which the
  largest are things that are not this site's fees at all (*international
  flights* 297+158, *hotel accommodation*, *massage service*) plus a short
  list of real near-misses: *government fees*, *fuel charge*, *route
  supplement*, *port & permission fees*, *crew gratitude*, *14% GST*.

Three shapes the parser has to survive, all of them ones the other two
sellers already forced:

    Marine Park, Port Fees and Permissions - 125-250 EUR per person (to be paid on board)
    Fuel Surcharge: 10EUR per day, to be paid on board
    Marine Park fees, harbour fees and fuel surcharge - 165-240 EUR per person per trip (to be paid on board)

a **range** rather than a figure, a **combined charge** naming three fees at
once, and a separator that is a colon on one boat and a dash on the next with
no space between the number and its currency.

#### Whose fees these are: the trip's, on 603 of 605

A block sits inside a trip object whose `title` reads *Northern Red Sea - Best
Wreck Diving (7 nights) (Hurghada-Hurghada)*, and the departures on the same
page are named *Northern Red Sea, Ras Mohamed, Straits of Tiran* — two
vocabularies for one boat's trips, and a key that stops matching fails
silently. Counted over the whole fleet 2026-09-21 by
`tools/probe_divebooker_fee_join.py`
([run 35545965933](https://github.com/PaludaNCode/Liveaboard/actions/runs/35545965933)):

| | |
|---|---|
| Fee blocks / trips | 605 / 472 |
| Blocks whose title is a trip name **exactly**, suffix off | **603** |
| Loosely (case and punctuation folded) | 603 — the fold buys nothing |
| Hulls whose blocks all agree | 42; **25 differ** |

So the book **keys on the trip**. The tempting fallback — fold the lot onto
the vessel where its blocks agree — is wrong on 25 of the 67 hulls that have
blocks, whose trips state different surcharges, so it would publish one week's
bill on another's row. The two blocks matching nothing stay unattached and are
named in the run log: guessing which trip they belong to is the failure
`promote.itinerary_key` already cost this project once.

Measured over the whole fleet rather than over the first eight names, because
an alphabetical sample of eight came back 36 of 36 and excluded Red Sea
Aggressor II, the boat whose two names raised the question. A sample that
leaves out the case that prompted it is not evidence, and 100% is exactly the
number that gets believed.

**Ask for the owner at the key, not at the value, and read `name`.** Two
findings one line apart, and between them they cost two whole-fleet reads:

* `"details"` names an object whose own smallest enclosing object *is that
  object*, so a scan asked for the owner at the `{` hands back the panel. The
  index that finds the trip is the one on the key's opening quote, which sits
  inside the parent and before the panel begins.
* the trip states **`name`**. `title` is the panel's own heading and reads
  *Price details* on every hull, so reading it gave every block one name — and
  once the index was right it gave nothing, because the trip object has no
  `title` at all.

The whole-fleet join probe read `name` at `start()` and got 603 of 605, which
is why the keying looked settled while the parser was attaching nothing.
`tests/fixtures/divebooker-price-details.json` is what stops a third.

#### How many of those bills add up: 155 of 608

Asked of the shipped reader rather than of a copy of it, over all 92 hulls
2026-09-21 by `tools/probe_divebooker_fee_verdict.py`
([run 35585415354](https://github.com/PaludaNCode/Liveaboard/actions/runs/35585415354)).
`pricing.divebooker_lines` returns `None` unless the book names, prices *and*
scales every charge a diver cannot decline, so `complete` is exactly how many
trips can reach the Total with a third column.

| | | first read | after five words |
|---|---|---|---|
| Panels | 608 on 92 pages | | |
| The bill adds up | | 155 (25%) | **205 (34%)** |
| …with a priced bill | | 102 | **152** |

Five words added to `fees.LABEL_PATTERNS` took it from a quarter of the fleet
to a third, and the panels carrying an actual priced bill by half again. Why
the remaining 403 do not, counting a panel once per reason, on the first read:

| | |
|---|---|
| a figure with no unit | 319 |
| a label nothing could name | 132 |
| a charge with no figure | 105 |

**And the unit survived every one of those words**, which is the confirmation
rather than the disappointment: Royal Evolution's 9 panels completed the moment
*Port & Permission fees* was read, because it states *per trip*; Tala's 12 did
not, because *Route fees and enviromental taxes - 200-320 EUR per person* says
who pays and not how often. The line is read now and the bill is still silent.

**The unit is the whole of it.** *Port fees - 50 USD per person* states a payer
and no period, `FeeItem.span_for_trip` refuses the line, and one such line
silences the bill it is in. There is a precedent for resolving it and it is
not a guess: `promote._with_units_resolved` already joins liveaboard.com's
unit-less gear figure to PADI's stated unit **on the money**, taking only the
unit and only where the figures match exactly. Whether that reaches these 319
is a measurement nobody can make until the fee book is committed, because it
needs both books side by side.

**A label is a word, and three of them were added on this census.** *Fuel
Charge* (24 lines), *Crew Gratitude* (32) and *Route suplement* (13) are each
the operator's own spelling of a charge `fees.LABEL_PATTERNS` already holds,
and none reached it: `gratuit\w*` stems on *gratuit* and cannot see
*gratitude* at all. What is **left declined on purpose** is every title naming
two charges — *Port & Permission fees*, *Route fees and enviromental taxes*,
*Permissions & jetty fees* — which is the rule `Environmental and Route Fees`
already set: filing a line under half of itself is worse than leaving it read
and unnamed.

**Two more were a bundle rather than an unknown**, found by printing the whole
obligatory column beside each declined line
([run 35588197625](https://github.com/PaludaNCode/Liveaboard/actions/runs/35588197625)).
Tala's *Route fees and enviromental taxes - 200-320 EUR per person* is its
entire required bill on 12 panels and declined on a missing `n`; Royal
Evolution's *Port & Permission fees: 150.00EUR per trip* is the same shape on
9. Both are `COMBINED_FEES` — one line carrying the whole amount, because
splitting 150 between a port and a permit invents two prices nobody quoted —
so `enviro(?:n)?ment` and `permissions?|permits?` join `COMBINED_PARTS`.

*Government fees* is the largest spelling left at 35 lines and stays declined,
and the panel is why rather than the taste. All 35 are the **Sea Serpent
fleet's six hulls**, whose obligatory column is exactly two lines:

```
sea-serpent
  mandatory  marine_park  200.0-300.0 EUR (no unit)
    DECLINED  Government fees - 100 EUR per person (for trips from January, 2027)
```

So it is not a duplicate of an environmental tax the boat bills separately —
there is no such line. What settles it instead is that **naming it would buy
nothing**: the park fee beside it states no unit, so the bill is silent either
way, and the choice between the environment tax and `LOCAL_FEES` would be this
project deciding what a charge is for with no total riding on the answer.
Recorded, not guessed.

Dive Runner's *Entrance fee - 10 EUR per person per day for the Strait of
Tiran, and 15 EUR per person per day for dives in Ras Mohammed National Park*
is the other deliberate refusal, and a sharper one: it states **two rates for
two places** in one line, and reading the first as the charge would publish
10 where a diver visiting Ras Mohammed pays 15.

#### What else that object states, for nothing

The trip holding the panel also holds `nights`, `numberDives`,
`requirements.expirience` / `.sertification`, `divesites`, `departurePort` and
`arrivalPort` — every fact this site takes from the other two sellers, in the
object the fee panel is already found in. All of them are read, and every one
is the **last** answer any chain takes: it is a seller's account of what the
operator publishes, and it may not outrank the operator's own.

`departurePort` and `arrivalPort` are `{"name": …, "url": …}` and are kept as
**two fields**, never joined. PADI's `ports` was one joined string and could
not be split back — two of that source's eight harbour names contain the
separator — so nothing ever read it. The `url` beside each name is this site's
own port page and is dropped: the built page ships nothing external.

They matter most where nothing else speaks. A row founded by this seller has
no liveaboard.com trip title to parse a harbour out of and no PADI trip to
ask, so all 69 of them read *Unknown* at both ends until these were read —
on a page whose *Departs from* bank is what a reader filters the fleet with.

#### Read end to end against the other seller's own panel

Dry-run 2026-09-21: the two verbatim panels from the fixture injected into the
committed book, promoted, built, and the page read in a browser. Amelie's
sailing of 2027-05-01 came out

| | Advertised | Mandatory | Total |
|---|---|---|---|
| liveaboard.com | €435.43 | fuel 40, park 60, port 25 | €688 |
| divebooker.com | €435.43 | fuel 40, park 60, port 25 | €688 |

**Identical to the cent, through two parsers that share nothing.** Ours reads a
`Required Extras:` sentence off a browser-rendered vessel page; this one reads
`Fuel Surcharge: 10EUR per day, to be paid on board` out of a streamed JSON
panel, resolves the period itself and scales it. They agree on all three
charges and on the berth. `best()` collapses the span, the Seller column names
all three sellers, and the fee panel shows both tables — which is what a row
looks like when three sites are telling the truth about one boat.

Red Sea Aggressor II, in the same run, ships its berth price and no third
total: its three obligatory lines state a payer and no period, so the bill
names every charge and scales none. Both outcomes are what they should be.

#### What the reader does with a line

`divebooker_com._read_fee_line` feeds `fees.ParsedFee` — the same dataclass,
the same `classify_label`, the same range handling — because a second fee
vocabulary drifts from the first. Only the *line* shape is this seller's.

* The label is whatever sits in front of the **first amount**, with a trailing
  separator off. The dash must be **spaced** or `Check-dive` loses its head;
  the colon need not be.
* The currency token must **touch** the figure, so *14% GST applicable to all
  onboard payments* stays an unpriced line rather than becoming 14 of
  something — `padi_com`'s whole-string rule, in this seller's grammar.
* A line that is only an amount (`$44`, `$43`, `$46` are whole lines on this
  fleet) is not a charge.
* **`per person` is the payer, not the period**, and it comes out before the
  unit is looked for. The census reported 507 lines under *per person* only
  because its own pattern matched the payer first and never reached the
  *per trip* beside it — a number about the probe rather than about the fleet.
  What is left with no unit gets `unit_unstated`: the figure is kept, the note
  prints it, and no total claims it.
* **`per tank` is a unit this project cannot scale.** One fill per dive is the
  diving world's ordinary assumption and it is still a derivation, and
  deriving a dive count is the arithmetic this dataset refuses outright. Same
  answer: keep the figure, claim nothing.
* **The seller's own column decides the tier** — `notincluded` is mandatory,
  `extra` is optional, `included` is an inclusion at zero — which is the rule
  `fees._tier_for` was rewritten around.
* Where one code appears twice, **a stated amount beats an inclusion and an
  inclusion beats a line with no amount**, and the columns are read in the
  page's own order so a tie goes to the obligatory line.
* A **priced** line nothing can name is carried back verbatim in
  `unnamed_fees`. Counted is not enough: what an unread charge needs is the
  word, and a count cannot say which word.

## The fleet, read whole

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

**The currency table is the thing to be careful about, and it turned out to be
the answer.** Read again 2026-09-20 over four hulls
([run 35524478377](https://github.com/PaludaNCode/Liveaboard/actions/runs/35524478377),
confirmed against the shipped parser in
[35524672758](https://github.com/PaludaNCode/Liveaboard/actions/runs/35524672758)):

| Hull | `Offer.priceCurrency` | `currencies.current` |
|---|---|---|
| Seawolf Steel | EUR | **USD** |
| Unity | EUR | **USD** |
| Iceberg | EUR | **USD** |
| Red Sea Aggressor IV | USD | **USD** |

USD is the only currency code anywhere in those bytes, and the rates beside it
are keyed by currency id — `"1":"1.0000"`, `"2":"0.8708"` — so the page is
priced in id 1 and knows what a euro costs. `?currency=EUR`, `?currency=USD`
and `?cur=USD` change nothing: same numbers, same labels, same `current`.

So **`Offer.priceCurrency` is a static per-vessel label that does not describe
`Offer.price`**, and `divebooker_com.page_currency` reads the payload instead.
The other two sellers agree, which is how it was noticed rather than how it is
decided: 645 of 777 joined sailings carry the same number as a figure
liveaboard.com or PADI states in **dollars**, and where liveaboard.com itself
quotes euros the divebooker figure is 1.148x it — 1/0.8708, the rate this
payload publishes.

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
- **`/destinations/`, `/countries/` and `/aquatories/` are refused** by the
  file, to `*`, and stay unavailable. `/boatsearch` is **not** among them —
  that `Disallow` sits in the `turnitinbot` record, as the robots section
  above sets out — and it is the entry point the fleet is discovered from.
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
- **"No fee book on the vessel page" was wrong, and the sweep is why.** It
  rested on 253 payload keys, none matching `fee`, `extra`, `includ` or
  `exclud` — and the panel is under a key called `details`. See *The fee book
  the sweep missed* below.

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

**And the owner has since answered it: that sailing is sold out on
divebooker.** So the doubled figure is what the page prints once there is no
single berth left to sell, and it is the last row on which `Offer.price` means
what it means everywhere else. One row in 888.

**And the reason we could not see it was ours.** `Offer.availability` came out
`InStock` on 888 of 888 departures, which looked like a field the source does
not fill. It fills it: two nodes state it, the trip's offer says `InStock` for
every sailing that trip sells and the **Event** is one sailing. Bella 2's three
Events say `LimitedAvailability`, `OnlineOnly` and `OnlineOnly`. The event pass
only filled the field when it was still empty and the trip pass runs first, so
the sailing's own word was read by nothing. Fixed, with the guard that asserted
`InStock` on every row re-aimed — it had the bug written into it.

Which probably also explains the doubled figure, and the vessel page says how:
a sold-out row there reads **"For full charters and groups only"**. 5,398 is
exactly twice 2,699. So the number is likely a whole-cabin or charter price
standing where a berth price normally is, on a sailing whose own node says
sold out — a prediction the next fetch checks rather than a finding. Once that
node is read, `AVAILABILITY` folds `SoldOut` to `sold_out`, `bookable` goes
false, and the row is marked gone instead of advertising a berth at 5,398.

So the doubling is not a parser artefact and not a second cabin class: it is
what the page prints for a week it can no longer sell a single berth on. The
other 78 in-season rows quote EUR against our USD and cannot be compared
without converting first.

**The next probe is still a booking page**, and the reason has outlived the
row that prompted it. Every fee this source gives us is read from the *Price
details* panel on the **vessel** page — a panel filed under the trip, not
under the sailing — and nothing has ever checked it against what a diver is
shown at checkout. The corroboration it does have is one sailing deep: Amelie
2027-05-01 read €435.43 plus fuel 40, park 60 and port 25 on this source and
on liveaboard.com, identical to the cent through two parsers sharing only
`classify_label`. That is real evidence and it is one row of 1,251. If the
panel is the boat's standing terms and a sailing can carry its own, this
reading would not know.

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
