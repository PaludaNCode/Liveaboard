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

**`/boatsearch` is refused by the file and permitted by the parser** — the same
shape as liveaboard.com's blank-line bug, and the same rule applies: that yes
is a parser artefact and not permission. Nothing here fetches `/boatsearch`.
Note what is refused: `/destinations/`, `/countries/` and `/aquatories/` are
listing paths, and a crawl may not use them.

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

## Not yet asked

- Which of the 516 hulls are Egyptian. `/egypt-daz3881` is the country page and
  has not been read; `king-snefro-5-haz10` and `snefro-target-haz1` are Egyptian
  hulls found in the sitemap, so the family is not region-scoped.
- What one `Event`/`Offer` actually holds — field names, currency, whether a
  berth count or a list price is in there. That is the next probe, and the one
  that decides whether stage 0 is a third seller or a third opinion.
- Whether any fee disclosure exists at all. Nothing read so far carries one.
