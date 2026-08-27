# padi.com — source interface

**Not wired up.** Nothing on the site reads from padi.com today, and no page of
it has ever been fetched.

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
| `TRAVEL_PATHS`, `TRIP_LINK` | **Unverified.** Written from the site's URL shape, never run against a response |
| Whether PADI Travel states requirements in prose at all | **Unknown.** This is the question a first fetch answers |
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

Blocked by this environment's egress policy, same as liveaboard.com:

```
connect_rejected — gateway answered 403 to CONNECT   www.padi.com:443
```

Runners are not. See [#1](https://github.com/PaludaNCode/Liveaboard/issues/1).
