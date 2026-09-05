# How a change gets made here

`docs/plan-missing.md` and `docs/plan-stable-ids.md` are two worked examples of
the same method. This is the method itself, so the next change does not have to
rediscover it — and so that "it works" means something a person can check
rather than something the person who wrote it believes.

Nothing below is new. Every step is here because the project has a scar where
it was skipped, and the scar is named.

## 1. Say what is wrong in one sentence, with the case that shows it

Not "the history looks noisy" but *"twelve of Blue's weeks are listed as new
and withdrawn at once"*. A defect you cannot state in one sentence with one
example is a defect you have not found yet, and the fix will be aimed at the
symptom.

Four fixes went out against one iOS scrolling report — hover timers, the
listener host, `overscroll-behavior`, a close button — every one a true finding
about a real bug and none of them the reported one. What ended it was a second
dead scroller in the same report, which named the one declaration they shared.

## 2. Record the gate green before touching anything

    python3 tools/ship.py

A red gate after a change means nothing if it was red before it, and ten
minutes of reading a failure you did not cause is ten minutes gone.

## 3. Measure before changing, from the committed data

Never from a guess, and never from the shape of the code. The measurement is
usually five lines of Python over `data/`, and it is usually the thing that
decides the design:

- 955 departures fall into **955** distinct `(boat, day)` pairs — so the id
  suffix that renumbers everything had never separated anything.
- PADI's guest count agrees with ours on 34 vessels, disagrees on 5, and fills
  4 that had none — so it goes **last** in the chain, where it can fill a blank
  and never move a stated figure.
- `content-visibility:auto` takes an append's layout from 147ms to 7ms and
  turns 44 late frames into 131 — so it was rejected.

A number that came from the data can be put in the commit message. A number
that came from a hunch is how `MONEY_FOLD` shipped a breakpoint measured
against a column 56px narrower than the real one, and how the footer prose in
#144 went stale the day after it was typed.

## 4. Write the guard first, and watch it fail

Point the new test at the **old** code and run it. A guard that passes either
way guards nothing, and this repo has shipped two of those: the fixture that
put rental gear on one seller's book alone passed all the way through the bug
it existed to catch, and eight source-string assertions passed over a table
that was 0px wide.

    $ (old rule restored in a scratch copy)
    Ran 4 tests — FAILED (failures=4)

Assert the **outcome**, not the property that delivers it. "The list must not
move under an open panel" survives a rewrite of how the panel is built;
`position: fixed` does not. And a layout claim is measured in a browser, never
grepped.

## 5. Make the change where the question can be answered

One function, one vocabulary, one place. `_departure_from` sees one node and
cannot know what shares its date, so the numbering moved to the caller.
`perDayOf` is one function because a filter and a column disagreeing about a
row is a reader with no way to tell which to believe. A second copy of a
pattern, a table or a rule drifts — and the day it drifts is the day the site
starts lying quietly.

## 6. Migrate the committed data in the same commit, through the same code

A parser fix that never reaches `data/` is a green build and a site that is
still wrong. Where the fix changes what the committed inputs *should* say,
rewrite them here — offline, through the function that will write them from now
on, so the migration cannot differ from the next crawl. Then:

    PYTHONPATH=src python3 -m liveaboard.cli promote --check
    PYTHONPATH=src python3 -m liveaboard.cli build

Re-crawling to re-read data already in the repository is both slow and rude;
`reparse_candidate.py` and the archive exist for exactly this.

## 7. Prove the churn survives everything downstream

A change to the data is a change to what the change log says about the data.
Renumbering 717 ids came out of `changes.compare` as 717 arrivals and 717
withdrawals; the fix for *that* made it 717 re-listed rows each saying `X → X`;
the fix for *that* counted them in one line. None of those three was visible
from the diff — each was found by running the comparison and reading it.

Ask what the page prints, what the log prints, and what the next refresh will
commit. Not what the function returns.

## 8. Run the gate again, and record both numbers

Before and after, in the commit message or the plan document. "Tests pass" is
not evidence; `24 jobs in 132.7s / gate passed` beside the same line from
before the change is.

## 9. Write down what you measured, negatives included

In the commit message first — it is the record, and chat is not a second copy
of it. In `docs/sources/{host}.md` when a probe touched a source, **in the same
commit**: a lead ruled out and not written down gets followed again.

And correct a negative that turns out to be wrong by **quoting it**, not by
deleting it. *"The strip has no such row and neither does the rest of the
page — searched in full, zero hits"* was written about a search of the
specification strip and read as a search of the page; the guest count was in
the description all along, and a reader found it. The wrong sentence stays in
`docs/sources/padi.com.md` because it is the reason nobody looked again.

## 10. Branch, merge, and say what is left

Every ask gets a branch and comes back through a merge — `--push`, then
`--merge`. Deleting the merged branch 403s here; say so rather than retrying.

If part of the work is deferred, it is a task with the reason in it, not a
sentence in a reply. The id fix waited for the re-listing fix on purpose, so
its 717-row churn had somewhere honest to land.

## What this does not buy

None of it replaces looking at the thing. The phone bill's missing padding was
found by a screenshot after the assertion measuring it had been passing for a
week, because the assertion compared the amounts with the panel's edge and the
panel ended exactly there. Measure, then look.
