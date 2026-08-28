# padi.com — source interface

**Not wired up.** Nothing on the site reads from padi.com today. The host is
reachable from this environment as of 2026-08-28 and has now been probed — see
[Access](#access) and [First fetch](#first-fetch-2026-08-28) — but nothing has
been parsed from it.

That is the honest content of this file, and it stays that way until
[#3](https://github.com/PaludaNCode/Liveaboard/issues/3) is picked up. The
scope, when it is: **requirements and accreditation, not prices.** PADI Travel
is weak on departure-level pricing and strong on the certification bar, which
is what the price comparison needs to be fair — comparing a week that requires
50 logged dives against one that takes beginners is not comparing like with
like.

## What exists

| Piece | State |
|---|---|
| `scrape/padi_com.py` `extract_requirements()` | Written, unit-tested against the industry's stock phrasings ("minimum of 50 logged dives", "Advanced Open Water", drift/current wording) |
| `TRAVEL_PATHS` | **Wrong.** Both paths 404 against the live site, and they are on the wrong host: PADI Travel is `travel.padi.com`, not `www.padi.com/travel` |
| `TRIP_LINK` | **Unusable as written.** No trip or vessel URL appears in any server response probed — the listing is client-rendered, so a regex over the HTML finds nothing to follow |
| Whether PADI Travel states requirements in prose at all | **Still unknown.** No trip page has been read, because no trip URL can be discovered without running the page's JS |
| The step that joins a PADI record to an itinerary | **Does not exist.** Boat name is the likely key and needs `classify.normalise()`, which already folds `Sha'ab`/`Shaab` and `St John's`/`St Johns` |

## The rule that applies here

Fetch first, then parse. Do not write markup parsers for pages nobody has
fetched — run `python3 -m liveaboard.cli scrape --source padi.com --limit 1` on
a runner, read the snapshot, then extend `CERT_PATTERNS` / `DIVES_PATTERN` only
for phrasings actually seen.

When the first real response arrives, this file gets the same treatment as
`liveaboard.com.md`: entry points, a fact-to-location table, traps, and the
negatives written down with equal weight.

## One property to preserve

A stated requirement is a safety gate. `extract_requirements()` returns `None`
when the page states nothing, and `classify.infer_level` never softens a stated
requirement. Both hold today; a matching step must not break either, and an
unmatched PADI record should warn rather than vanish.

## Access

**Open from this environment as of 2026-08-28**, unlike liveaboard.com. The
403-on-CONNECT this file used to record no longer happens:

```
GET https://www.padi.com/          200  Drupal 10, 166 KB, real homepage
GET https://travel.padi.com/       200
```

So a first fetch no longer needs a runner. That is a property of the current
egress policy, not a promise — re-probe rather than assume, and
[#1](https://github.com/PaludaNCode/Liveaboard/issues/1) still covers the
runner path for when it closes again.

Both hosts allow crawling. `www.padi.com/robots.txt` names `ClaudeBot` with
`Allow: /` and `Crawl-delay: 2`, and says the delay is kept in sync with a
Cloudflare AI-bot limit of **30 requests/min/IP** — so pace a crawl at one
request every two seconds or expect to be rate-limited by the edge rather than
by robots. `travel.padi.com/robots.txt` allows the same agents with no delay
for them. Nothing under `/travel` or `/diving-in/` is disallowed.

## First fetch (2026-08-28)

The probe answered where PADI Travel lives and ruled out reading it over plain
HTTP. Both results are load-bearing for whoever picks up
[#3](https://github.com/PaludaNCode/Liveaboard/issues/3).

**PADI Travel is a separate host.** `www.padi.com/travel` 302s to
`https://travel.padi.com/`, and the `www` sitemap
(`https://www.padi.com/default/sitemap.xml`, 3304 URLs) contains **no** travel,
liveaboard or Egypt URL at all — it is the certification and dive-centre site
only. Entry points that answer 200:

| URL | What it is |
|---|---|
| `travel.padi.com/s/liveaboards/all/` | The liveaboard search. `/liveaboards/` redirects here. `<title>` is server-rendered and currently reads *269 Trips in the World* |
| `travel.padi.com/diving-in/egypt/` | Egypt destination page. `/d/egypt/` redirects here |

**Ruled out: reading either page without a browser.** `travel.padi.com` is a
Next.js app (`travel-next`, chunks served from CloudFront) and the trip cards
are fetched client-side:

- The 240 KB liveaboard listing contains **zero** `application/ld+json` blocks
  and not one link to a vessel or trip — the only internal hrefs are
  `/s/liveaboards/all/` and `/liveaboard-deals/`.
- Its single `self.__next_f` RSC payload carries destination slugs
  (`australia`, `bahamas`, …) and page metadata, no trips.
- The Egypt page's 50-odd internal links are all `/diving-in/<place>/` siblings
  plus two `/dive-center/egypt/…`. No trip URL, no liveaboard URL.

So the shape `scrape/padi_com.py` assumes — fetch a listing, regex trip links
out of it, fetch each trip — does not work against this site as it stands. A
first trip page still needs either the browser path `tools/scrape_fees.py`
already uses, or the JSON endpoint the listing's chunks call, which has not been
looked for yet.

**Not yet probed:** whether a trip page, once its URL is known, states
requirements in server HTML (it may well — only the *listing* is proven
client-rendered), and whether an unauthenticated JSON API backs the search.
