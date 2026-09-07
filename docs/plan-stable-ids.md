# Making a departure id stop moving

A departure id is identity: `changes.compare` keys on it, `Dataset.from_dict`
keys on it, and the page writes it into every row's `data-id`. It is built by
`liveaboard_com._departure_from` as `f"{slug}-{start}-{index}"`, where `index`
is `enumerate(events)` — the Event node's **position on the vessel-month page**.

So a sailing inserted or removed earlier in that list renumbers every later one,
and a renumbered sailing reads to the diff as one row withdrawn and another
arrived. Two of Blue's weeks did exactly that on 2026-09-04:
`blue-2027-08-12-0` became `blue-2027-08-12-1` with the same boat, the same
week, the same trip and the same seller.

That half is now *reported* honestly — `changes.Relisted` pairs the two rows —
but the report is a net under a defect, not the fix. This is the fix.

## What the suffix is for, and what it is doing

It exists to separate two sailings of one boat that start on the same day. In
the committed candidate it separates nothing at all:

| | |
|---|---|
| departures | 955 |
| distinct `(boat, start)` pairs | **955** |
| pairs carrying more than one sailing | **0** |
| suffixes in use | 0–8 |

Every one of those suffixes above `0` is therefore pure position. It is not
dead code — two sailings on one day are possible and an id collision would be
the silent kind this project raises on — but nothing in this season needs it,
and everything in this season is renumbered by it.

## The rule

Number **within `(slug, start)`**, and where a date really does carry more than
one sailing, order them by the trip's own name rather than by where the seller
happened to print them. Then no sibling elsewhere on the page can move an id,
and the residual tie is broken by content instead of by position.

## The steps

1. **Record the gate green before touching anything.** `python3 tools/ship.py`.
   A red gate after the change means nothing if it was red before it.
2. **Measure the churn this causes**, from the committed candidate rather than
   from a guess: how many ids change, and how many `(boat, start)` pairs hold
   more than one sailing. Both numbers go in this file (above).
3. **Move the numbering out of `_departure_from` and into the caller**, which
   is the only place that can see the whole page: build the rows first, group
   them by `start`, sort a group by `(name, end)`, then write the ids. The
   parser keeps returning `None` for a row it cannot price or date, so the
   grouping is over rows that survived rather than over raw nodes.
4. **Migrate the committed candidate offline.** `data/candidate.json` is a
   crawl artifact and the dataset's ids come from it, so leaving it alone would
   defer the churn to the next crawl in CI — where nobody is watching and the
   report lands as 717 arrivals and withdrawals unless the `Relisted` fix
   catches them. Rewrite it here, under the same rule, and prove the rewrite
   agrees with what a fresh parse would produce.
5. **Re-promote and rebuild.** `promote --check` is what proves the dataset is
   this code's output; the page must be rebuilt for the same reason.
6. **Test the property, not the output**: a sailing inserted earlier on a page
   must not renumber the ones after it, and two sailings on one day must still
   get two ids.
7. **Prove the churn reads as one thing.** Compare the dataset before this
   change with the dataset after it through `changes.compare`: the answer must
   not be 717 added and 717 withdrawn. It came back as 717 `Relisted` rows, and
   that was still wrong — every one of them said `X -> X` about the seller and
   the fare, because nothing but the id had moved. So a re-listing where
   nothing moved is **counted** rather than listed (`Report.renumbered`), the
   way `price_rounding` already is, and the quiet-run line names the count.
8. **Run the gate again**, and record it.

## What is not in scope

`promote` mints `f"{slug}-{start}-padi"` for a sailing PADI alone lists. That
id is already stable — it carries no index — and the day liveaboard.com starts
listing such a sailing the row changes id by design, which is the *other* half
of the Blue report and is what `Relisted` exists to say.

## Evidence

### Before

    $ python3 tools/ship.py
    24 jobs in 140.1s across 6 workers
    gate passed

    candidate departures: 955
    boat+date groups: 955 | groups with >1 sailing: 0
    ids that change under the new rule: 717 of 955

### After

The four new tests in `tests/test_scrape.py::TestAnIdNoSiblingCanMove` were run
against the **old** rule before the new one landed — a guard that passes either
way guards nothing:

    $ (old positional rule restored in a scratch copy)
    $ python3 -m unittest test_scrape.TestAnIdNoSiblingCanMove
    Ran 4 tests — FAILED (failures=4)

    $ python3 -m unittest test_scrape.TestAnIdNoSiblingCanMove
    Ran 4 tests — OK

The migration, through `_number` itself rather than a second implementation of
the rule, so the rewrite cannot differ from what the next crawl writes:

    rewritten: 717 of 955          # and 955 unique ids after, asserted
    $ git diff --stat data/candidate.json
    717 insertions(+), 717 deletions(-)   # every one of them an "id" line

    $ PYTHONPATH=src python3 -m liveaboard.cli promote --check
    data/egypt-2027.json matches what promote produces from the committed inputs

And the churn, put through the report that has to survive it:

    added 0 | withdrawn 0 | relisted 0 | renumbered 717 | quiet True

    changes: the id before -> the id after
    ======================================

    nothing moved, beyond 717 sailing(s) that kept everything but their id.

That last line is the second half of this change. A re-listing where **nothing
but the id moved** is not news, so `changes` counts it rather than listing it —
the rule `price_rounding` already follows — and the quiet-run sentence names
the count, because "nothing moved" over 717 moved ids is the silent truncation
this project exists to refuse.

    $ python3 tools/ship.py
    24 jobs in 132.7s across 6 workers
    gate passed
