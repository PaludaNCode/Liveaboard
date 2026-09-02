# CLAUDE.md

Price-transparency site for Egyptian liveaboards. Takes an advertised berth
price and reassembles the real bill. See README.md for the domain.

## Commands

```bash
python3 tools/ship.py                    # the whole gate, in parallel — 24s
python3 tools/ship.py --fast             # the inner loop, no browser — 4s
python3 tools/ship.py --push -m "..."    # gate, then commit and push
```

**`tools/ship.py` is the gate, and `.github/actions/checks` runs that same
command** — so the bar a person clears before pushing is the bar the five
workflows run, rather than a second list that drifts from it. It builds the
page, then runs the suite sharded by module alongside `check`, `promote
--check` and the seed check: 24 seconds against about 60 serially, and 4 with
`--fast`, which drops `test_promote_check` and `test_layout` and says so.
`TestOneGate` refuses an `action.yml` that grows its own steps back.

CI runs the same gate, so a green run here is a green run there. **Do not sit
and poll it** — the only thing worth waiting for is a red one, and the way to
find out is to be told.

The pieces, when one of them is what you want on its own:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests   # everything, no deps
PYTHONPATH=src python3 -m liveaboard.cli check         # validate + summarise
PYTHONPATH=src python3 -m liveaboard.cli build         # -> site/index.html
PYTHONPATH=src python3 -m liveaboard.cli changes       # what moved since HEAD~1
python3 tools/make_seed.py                             # regenerate seed data
```

Re-promoting is offline and takes seconds — do it after any change to
`src/liveaboard/`, rather than waiting for a crawl. **CI enforces this**
(`promote --check`), so skipping it is a red build rather than a site that is
quietly a parser behind:

```bash
PYTHONPATH=src python3 -m liveaboard.cli promote --check   # did I forget?
PYTHONPATH=src python3 -m liveaboard.cli promote           # rebuild the dataset
PYTHONPATH=src python3 -m liveaboard.cli build             # and the page
```

Take the defaults. There is now exactly **one** place a job promotes —
`.github/workflows/publish.yml`, the serialised tail every pushing workflow
delegates to — plus the rebase path in `.github/actions/publish` and
`promote --check` in `.github/actions/checks`. Nine callers spelling out their
own promote became three, so "one canonical set of inputs, in `cli.py`" is
structural rather than a convention nine files have to keep.

**A test over committed data gates the commit, never the fetch.** The suite
holds both kinds and the default command runs both. Seven workflows run the
code-only suite up front — six of them before fetching — and every one of them
runs the whole suite afterwards through `.github/actions/checks`, so an
assertion about committed data is reached only once there is a commit to gate.
`fees.yml` drives a browser and runs no up-front suite at all, which is the
same rule taken to its end rather than an exception to it:

```bash
LIVEABOARD_TESTS=code PYTHONPATH=src python3 -m unittest discover -s tests
```

**And a job that pushes runs CI's own list before it does.** GitHub does not
trigger workflows on pushes made with the default `GITHUB_TOKEN`, so not one
scheduled data commit has ever had a CI run against it — which is how a refresh
published 36 sailings advertising a berth nobody could buy and left `main` red
for seven hours, found by a person opening an unrelated PR. `.github/actions/
checks` holds the list once and `ci.yml` uses it too, so the two cannot drift;
`TestEveryPushingWorkflowChecksItself` refuses a workflow that pushes without
it. Add a check there rather than to `ci.yml`.

This is not a convenience. `cabins.yml`, `refresh.yml` and `itineraries.yml`
ran the whole suite as their *first* step, so on 2026-08-30 a stale cabin book
contradicting the rows the refresh had just written failed the suite in front
of the only job that could refresh that book — the guard gating the fetch that
would clear the condition the guard was testing for. A run that fetches and
then refuses to publish is recoverable; one that refuses to fetch is not.
Committed data is reached through `tests/published.py`, which is the gate, and
`TestThePublicationGateIsComplete` refuses a test that opens `data/` for
itself. Hand-maintained inputs — `padi_aliases.json`, `operator_facts.json` —
are outside it: no crawl touches them, so nothing asserted about them can ever
be cleared by fetching.

**One source per workflow, and the derive-and-push tail is a second job.** Each
source fetches independently — that is what the split bought — and everything
from `promote` onwards lives in `.github/workflows/publish.yml`, which
re-checks out the **branch tip** and derives there.

**Serialising that tail was tried and it lost data.** A shared `concurrency`
group is the obvious way to stop two sources colliding, and GitHub's
concurrency is not a queue: a group holds one running job and *one* pending,
and a third arrival cancels the pending one. On three simultaneous dispatches
`deals.yml` read PADI's deals, uploaded them, and had its publish cancelled
four seconds later without running — a day's figures fetched and discarded, the
run reading *cancelled* rather than failed, which nothing alerts on. Worse than
the race it was meant to prevent. `TestThePublishTailDoesNotQueueOnConcurrency`
fails if a group comes back.

The reason is a bug this shape makes unreachable rather than repairable. Two
jobs in flight both derive `data/egypt-2027.json` and `site/index.html` from a
checkout, and `-X theirs` on the rebase then favours the *stale* copy: on
2026-08-31 a `deals.yml` run put back a page saying `berths_read: 2026-08-28`
beside a `data/cabins.json` a capped `cabins.yml` had just collected on 08-31.
The site dated its own berth counts three days wrong, `promote --check` was
green throughout because the dataset really did match its inputs, and the next
run healed it before anybody could look.

So **a fetching job hands over its inputs and never its output.** What it read
goes up as an artifact; the dataset and the page are rebuilt in the publish job
from the branch tip plus those inputs. A derived file never travels, so it
cannot arrive stale. `TestThePageIsWhatItsDataBuilds` is the net under that,
and the re-derivation in `.github/actions/publish` is the belt: the rebase path
still exists for a collision that beats the queue.

**Two shared actions, and one shared workflow.** `refresh.yml` bundled the
ECB rates, the liveaboard.com crawl, the itinerary fragments and PADI's deals,
so proving a two-request change to `fetch_deals.py` meant a run that first
fetched 320 vessel pages from a site with nothing to do with it. Each source is
its own dispatchable job now — `fx.yml`, `refresh.yml` (the crawl alone),
`deals.yml`, `itineraries.yml`, `padi.yml`, `cabins.yml`, `fees.yml` — and each
ends the same way, because the shape is identical and six copies of it drifted:

- `.github/actions/checks` — everything CI asserts. `ci.yml` uses it too, which
  is what makes "the same bar" true rather than intended.
- `.github/actions/publish` — stage, commit, push with rebase-and-retry. Its
  `subject` takes `{today}` and `{sha}`; a `headline` carrying scraped text is
  its own input and never reaches a shell as code.
  **A rebuild is not news.** `cli build` stamps the page with the minute it
  ran, so `site/index.html` differs on every run whether or not any data did
  and the "nothing to commit" exit could never fire: seven jobs a day committed
  seven times a day regardless. Each was a line in `git log --oneline data/` —
  which this file calls the price history — that moved no price, and a deploy
  that published nothing. So when nothing but the page is staged and the page
  differs only by that stamp, the action says so and commits nothing. Compared
  with the stamp normalised on both sides, never by reading the shape of a
  diff: the payload is one enormous line, so a real change and the stamp land
  on it together and no line-wise filter can tell them apart.
  **It also means a quiet job never reaches the push at all**, which is not
  obvious until you want a collision. Two attempts to force one for #127 hit
  this instead: a full `cabins.yml` had already covered every departure that
  morning, so a capped re-read produced a byte-identical book and the publish
  exited before it could be rejected. A job with nothing to say cannot lose a
  race, so the rebase path is reachable only on a day something moved.

Every one of those files is a **promote input**, so a job commits its data
*and* the dataset built from it. Committing an input alone leaves
`promote --check` red until something unrelated heals it.

Five guards police this contract — `TestThePublicationGateIsComplete`,
`TestEveryPushingWorkflowChecksItself`, `TestEveryDataCommitReachesThePage`,
`TestThePageAnnouncesTheNewsInTheCommitThatMakesIt` and `TestARebuildIsNotNews`
— and two of them have already been blinded once, by the refactor that moved
`git add` and `git push` into the action: they now assert they can still see
what they are checking, because a check that stops checking is green for the
wrong reason. Add a guard here rather than trusting the next refactor to leave
the shape alone.

**Read the published page without checking anything out.** After a merge, to
see what actually shipped:

```bash
git show origin/main:site/index.html > /tmp/prod.html   # no checkout, no reset
```

Resetting the working branch onto `main` to look at it works and then leaves
the branch one merge commit "ahead" of its own remote, which reads as unpushed
work every time. `git show` answers the same question and disturbs nothing.

## Workflow

Every ask gets a branch, and comes back through a merge. No exceptions, and no
asking whether this one counts.

```bash
python3 tools/ship.py --fast             # while working
python3 tools/ship.py --push -m "..."    # gate, branch, push
python3 tools/ship.py --merge            # gate, merge to main, push
```

`--push` refuses to commit on `main` and branches from the commit subject, so
this holds whether or not anybody remembered it. **"Merge to prod" means run
`--merge`** — if it ever comes back "nothing to merge", the work was put on the
trunk directly and that was the mistake, not the request.

Eleven changes went onto `main` with no branch before this was written down.
The concern was raised three times in that session and the pushing continued
anyway, which is worse than never raising it: a flagged worry followed by the
original behaviour reads as permission that was never given.

Deleting the merged branch 403s here — the token pushes branches and cannot
remove them. The merge lands; the branch is left stale. Say so, do not retry.

## Answering

Short. The finding, not the derivation. This is a codebase whose owner knows it
better than you do, so a table of evidence for a conclusion he can reach in one
glance is noise, not rigour.

- Lead with the answer. Detail only if asked.
- One decision per message when a decision is wanted.
- Skip restating what was just said, and skip the recap section.
- Findings that matter go in `docs/` or a commit message, not into chat twice.
- **The commit message is the record; chat is not a second copy of it.** Do not
  narrate what was done, what was measured, what was decided and why, in a
  reply, when all of it is in the commit that just landed. Two or three lines
  and a link to look at.
- **No status tables, no per-size measurement grids, no bullet list of every
  judgement call**, unless they were asked for. A reader who wants the numbers
  will ask; one who does not has to scroll past them to find the answer.
- A reply that needs a heading is too long.

## Invariants

Break these and the site starts lying quietly rather than failing loudly.

- **Two sellers, neither of them the house.** `padi.com` and `liveaboard.com`
  are both sources this site reads. liveaboard.com was read first and PADI
  second, and that is a fact about this project rather than about either
  seller, so it may not appear as *ours* and *theirs*, as a named seller beside
  an unnamed default, or as a reason in a comment that explains a price. The
  metric keys are `.lav` and `.padi`; `best().cheaper` says `"liveaboard"` or
  `"padi"`; a link in the Seller column always names the seller it opens —
  "listing" was liveaboard.com's, printed on no PADI row ever, and handed a
  visitor to a site the page never named (#139). The asymmetries that *are*
  real are all statements about what a source publishes and each says so where
  it is written: the fee panel is the vessel's own and beats a seller's account
  of it; a row states `pct` only from the seller whose fare it prints;
  `berths_read` and `padi_berths_read` are two crawls on two days; PADI's
  `availability` fills the whole-sailing slot and not the at-price one, because
  that was measured.

- **Never invent a price.** Every price and fee needs a `Provenance`. A parser
  that cannot find a number returns `None`; it does not guess. `seed_estimate`
  triggers the "not real quotes" banner — do not suppress it.
- **`pricing._is_counted` is mirrored by `lineCounts` in `templates/app.js`** —
  both `DEFAULT_ON_TIERS` and the order of its checks. The toggle is asked
  before the tier: nitrox and gear are filed under the source's *Optional*
  extras, and testing the tier first made both switches on the page add nothing
  to any total.
- **Normalisation happens in Python only.** Fee basis (per night / day / dive /
  week) and FX conversion resolve in `pricing.py`; the browser only sums lines
  that are switched on. Do not add pricing logic to the JS. Operators quote the
  same gear set per day, per trip *and* per week, so comparing raw amounts
  across vessels is meaningless — normalise first.
- **Included fees stay in the breakdown at zero.** Removing them hides the
  difference between a bundled operator and one that bills at the dock. Both
  sellers are read for it now, and each of them had to be found separately:
  PADI states it in `whatsIncludedNew`, on 447 of 447 itineraries, and
  liveaboard.com prints an **`Included:` block** above the Required and Optional
  ones `fees.BLOCK` was reading — on all 79 vessel pages, and read by nothing
  until Bella 2's missing nitrox line found it. Reading only what a seller
  charges *on top* left two bills in one expanded row disclosing at different
  depths. The list is prose rather
  than labels, so a **parenthetical is a qualifier and never the name** —
  *Airport Meet & Greet (VISA assistance)* classified as the visa fee and would
  have told eight itineraries' readers that the €25 they still pay at the
  airport was covered. And an amenity nobody can classify — Water, Coffee, Free
  WiFi — is not a hole in a fee book: inclusions never reach `unreadable`,
  which would have taken the book from 259 complete trips to none.
  **A stated amount beats an inclusion, and an inclusion beats a line with no
  amount.** One code covers two services often enough to matter — Topaz includes
  the airport transfer and charges €25 for the hotel one — so printing
  "included" there would call a published charge free; and the other way round,
  Dune Longara states a transfer as included *and* lists one with no price,
  where "listed with no price" is the parser missing an answer on the page.
  Neither seller's optional lines can make a bill `complete` or not: that
  verdict is about the charges a diver cannot decline.
- **Read both halves of the optional disclosure too.** PADI's
  `optionalOnBoard`, `optionalInAdvance` and `optionalBookableAdvancePaidOnBoard`
  hold nitrox and gear hire — the two extras this site puts a *toggle* on — and
  were read by nothing: Bella 2's €50 nitrox and its €40-per-diving-day scuba
  set were absent from a vessel whose PADI book is the only fee book there is.
  Two traps in that list, both measured: *PADI Enriched Air Diver (Nitrox)* is a
  313-entry **certification** that matched the nitrox pattern and would have
  priced a course as the gas on the toggle that counts; and *Full scuba set* is
  the bundle row, 417 entries carrying `fullSetDescription`, which is the only
  honest gear price because adding up singles invents a basket nobody sold.
- **A price PADI states as a string is still a price.** `price` is null on 236
  of its 872 mandatory entries and `extraValue` carries the figure on 133 of
  them — Bella 2's *Coast Guard Fee* is `"5 EUR"` and its *Service fees*
  `"10 EUR"`, two of the three charges on every trip that boat sells. Reading it
  takes the book from 259 trips whose bill adds up to 332. `price` still wins
  wherever it is a number: the two disagree on 27 entries and every one is a
  repricing `extraValue` did not follow — Blue Horizon's fuel surcharge is 56 a
  trip against a stale `"8"` a night. Whole string or nothing, so *"14% GST (on
  onboard purchases)"* never becomes 14 of anything.
- **A figure with no unit is not a per-trip figure.** liveaboard.com's gear
  dialog states one on 65 of the 70 vessels that quote a bundle — 25 per trip,
  25 per week, 15 per day — and the five that leave it out span every one of
  those answers, so there is no fallback that is right. Read as per trip, the
  cheapest of the three, Bella 2's €40 set was a third of a three-night trip's
  hire and a seventh of a week's. `FeeBasis` is `None` there, the figure goes in
  the note, and no total claims it.
- **No score grading operators.** The site compares what trips cost; it does
  not rank who sells them. A per-operator "honesty" percentage was removed:
  it read as a league table and contradicted the total beside it.
- **Never claim a total the disclosure does not support.** No fee lines means
  nobody looked; only optional ones means the operator did not state its
  required extras. Neither is a clean bill.
- **A charge priced for last year is not this year's charge, and silence is not
  expiry.** PADI's payload states `validFrom`/`validTo` on a fee and keeps both
  sides of a repricing: Grand Sea Explorer lists *Route supplement* twice on
  every trip — 300 valid to 2026-12-31, 400 from 2027-01-01 — and DUNE Longara
  lists *Environmental taxes* at 100 and 200. Nothing read the dates, so the
  bill got whichever entry the parser happened to keep. A comment used to
  reason the opposite way, that two entries under one title are two charges the
  operator bills; the dates sat in the same payload and refute it. All **69
  such pairs resolve to exactly one entry valid in the published season**, not
  one has two, and they sit on the largest mandatory lines there are. The other
  half of the rule is the usual one: 750 of 896 entries state no window at all
  and every one is kept. Only an entry stating a window the season cannot reach
  is dropped, which is the source saying this price stopped applying before the
  trip sails.
- **No route, theme or level labels.** They were inferred from the dive sites,
  read by nothing on the page, and removable without loss: if you want a BDE
  week you filter on Brothers, Daedalus and Elphinstone. A name for a set of
  sites is a layer that can be wrong — a St John's week was badged BDE — and
  answers nothing the site filter does not. `Itinerary.requirements` stays: a
  stated safety requirement is the operator's claim, and is never softened.
- **A vessel summary is the boat's year-round brochure, not the trip's.** Never
  a source for where one trip goes: Aphrodite's names St John's, so its *North
  Wrecks* week would be tagged with a reef 600 km away.
- **Never derive a dive count.** Ten vessels publish one and they state 15 to
  21 for the same seven-night week, so a fixed three-a-day is wrong by up to a
  third — and at €60–100 a dive that is the whole difference between two boats.
  The spread has a cause: a week that crosses further, or sits longer in the
  parks where night dives are not permitted, fits fewer in. Operators quote a
  range and the dataset keeps the **low end**, so price per dive is a ceiling.
  A vessel-level count is for a standard week (`dives_for_nights`) and is
  withheld from every other trip length that boat sells; the itinerary fragment
  states one per *trip*, which needs no such guard and wins. **PADI's stated
  count is the last resort and only where nothing of ours answers** — it cannot
  outrank ours, because every All Star Ghani itinerary says 16 where ours say
  17, 19, 20 and 21, and of the 142 trips where both speak, 113 disagree with
  PADI the lower one on 90. But on 69 published itineraries we hold nothing at
  all and PADI answers every one; 43 are on the vessels PADI alone sells berths
  on, where `fetch_itineraries.py` has no tour id to ask about and never will.
  Unknown stays 0 and the column says "not stated".
- **Four sources for a trip's reefs, in order, never merged.** The operator's
  own description, then its region list, then the trip title, then — last —
  what the *second* seller says about the same week (`padi.json`'s
  `dive_sites`, folded by `fetch_padi._padi_sites` from the day plan and then
  the blurb). PADI is last because it is the least structured: against the 180
  trips both sellers describe, its words add 173 reef mentions liveaboard.com
  does not,
  among them Elphinstone on a Brothers and Safaga week off a sentence saying
  the two "are quite distant from one another". Merged in, that is the
  BDE-badging failure removed once already; used only where the three above are
  silent, it is the difference between a row the site filter can reach and one
  it cannot — 47 rows on 19 itineraries, down to 4 on 3. The three that stay
  blank name no reef in any field, and blank is right for them.
- **The per-trip book beats the trip title, and never joins it.** `promote`
  merges `data/itineraries.json` — the operator's own reefs, dive count, group
  size and entry bar for one trip — the way it merges the fee book. Where it is
  silent the title parser still answers, so a fetch that has not reached a trip
  never blanks it. Unioning the two would reimport the titles' errors, which is
  the whole reason the fragment is fetched. Both sides key on
  `promote.itinerary_key`; every field has a fallback, so a key that stops
  matching fails silently.
- **`Itinerary.name` is identity; `Itinerary.title` is presentation.** The id is
  built from `name`, and two sailings differing only by port are two trips.
  Only `title` is ever tidied, and it is tidied in exactly five ways.
  **One route, not a house style.** `BDE`/`BDE_TITLE` fold the seven spellings
  of Brothers/Daedalus/Elphinstone onto one; the pattern is anchored at both
  ends, so *Marine Park North: Brothers - Daedalus & Elphinstone* and
  *Brothers, Daedalus, Elphinstone & Safaga* are untouched. Twelve other groups
  differ the same way and are deliberately left as their operators wrote them —
  do not generalise this into a rule that rewrites every title, and never
  reorder words: nothing here can verify the order means something, and nothing
  may assume it means nothing — *St. John's & Daedalus* and *Daedalus & St.
  John's* stay two titles. **House separators, on route lists only.**
  `_house_separators` prints a list of stops as commas then `&` before the
  last, so *North - Brothers*, *North and Brothers* and *North & Brothers*
  print once. It fires only where `_is_place_list` holds — every part between
  the separators is something `SITE_HINTS`/`SITE_ALIASES` already recognises —
  because *Dancing with Dolphins - Dolphin Liveaboard Safari* is a sentence
  whose dash is not a separator and *Best of Dahab and Tiran* is English.
  Reuse that vocabulary rather than writing a second one: a copy drifts, and
  the day it drifts is the day the site starts repunctuating prose.
  **Case only, otherwise.**
  `_settle_title_case` picks one spelling where titles differ *only* by
  capitalisation, and always one the operator actually used — never title-cased
  into a spelling nobody wrote, because the fleet is full of names a casing
  rule would ruin (*MY Odyssey*, *St. John's*, *SS Turkia*). Ties break
  alphabetically: `promote` is pure and CI compares its output byte for byte.
  `TITLE_FIXES` corrects what is *wrong* rather than what is merely different —
  zero-width spaces, three characters doing one apostrophe's job, a dash with
  a space on one side only, the two misspellings of Daedalus and the one each
  of Zabargad and Gubal, listed the way
  `PORT_ALIASES` lists harbours because a near-miss rule that catches those
  also catches a reef that only looks like another. A misspelling goes in the
  table only where the trip's own `dive_sites`, parsed from the operator's
  description, already name the reef correctly — the dataset confirming the
  reef independently of its title is what separates a correction from a guess.
  *Gobal* is the single exception and is commented as one: its trip names no
  Gubal at all, so the warrant is the fleet's instead — 46 parsed *gubal*
  against one *gobal* — a weaker warrant taken deliberately, and only at that
  margin. **Four reefs, not every reef.** `REEF_ALIASES` folds the
  five spellings of *St. John's*, the three of *Brothers*, the two of *Fury
  Shoals* and the three of *Ras Mohammed* onto one each — differences rather than mistakes, folded for the
  reason `BDE` folds one route: the reader should not have to work out that the
  reef is the same reef before comparing the prices beside it. Each replacement
  is the *plurality* of what the fleet wrote, never a spelling invented to be
  consistent, so the table re-derives from the data instead of from taste. The
  guard on `Brother` is the shape of the risk — *Big Brother* and *Little
  Brother* name the two islands separately, and folding either into the pair
  would delete which island the trip dives. *Ras Mohamed*/*Ras Mohammed*/*Ras
  Muhammad* are all real transliterations and none is wrong, so this is a fold
  and not a correction. **What picks a spelling is `SITE_HINTS`, not a count of
  the data.** The parsed `dive_sites` agree unanimously on any reef only
  because the alias table folds them there — quoting that agreement as evidence
  is this project's own choice reflected back at itself, and an earlier version
  of this note made exactly that mistake. The real reason to fold a title is
  that the trip-name column and the filter chip beside it must not disagree,
  which is also why `SITE_HINTS` and the title tables have to move together:
  *Fury Shoals* was changed in both when the two drifted apart. Do not lengthen this
  table without counting the spellings first, and count in the *names* rather
  than the titles: counting folded titles hid *Ras Muhammad* entirely.
- **One file, three views, three questions.** Trips, on sale and history are
  panes `showView` swaps in `app.js`, addressed by the URL hash — never three
  documents, because the payload is inlined and a second file would ship those
  megabytes again. **A filter is not a view.** The sale view was built as the
  trips table with the markdown filter held down, and that was wrong twice
  over: it made the rail's middle entry a second way to press the On sale
  chip, and it left what the view is actually for — the discount overview —
  folded into a `details` above the table, which is exactly where nobody
  looking for it would go. `saleOnly()`
  reads the chip and nothing else. Which departures are discounted is a table
  question; what the sales *are* is a page.
  **And that page is two sections: the sales, then the trips on sale (#145).**
  It was one boat-keyed table joining the two sellers side by side, and the
  join was the mistake — what they publish are not two halves of one record.
  liveaboard.com strikes a list price through on a booking page, so its
  evidence is a *run* of that boat's discounted sailings; PADI publishes a
  named offer against one sailing, and states no validity window for it, so
  that row's dates are one sailing and the cell says so. A row per sale states
  each as what it is, sorted by boat so a boat both sellers discount shows two
  rows rather than one asserting a join nobody made — and the union is
  structural, because a table keyed on either book drops the other's. The
  second section is the 229 discounted sailings themselves, deepest first,
  which were reachable only by holding the On sale chip down over the trips
  table. **Three paragraphs of reasoning came off the top of it**, and the
  facts inside them did not: the coverage counts are a muted line with a hover
  per count, and the vessels PADI advertises that no boat here joins to are
  still *named* — a count cannot tell a Caribbean boat from an unpaired
  Egyptian one, which is the whole reason the query asks for the USA.
  A `read` list is **parallel to its `sellers` list and keeps its holes**:
  `None` for a seller with no reading date rather than a shorter list, because
  the two are read in lockstep and dropping an entry shifts every date after it
  onto the wrong seller's name. A new
  section is a
  pane and a rail item — and `tablePane` is not the trips pane: two views draw
  there and the deals panel inside it belongs to the other one. Which pane is
  on screen is the `hidden` attribute, and `[hidden] { display:none !important }`
  is what makes that beat the panes' own `display:flex` — without it every view
  draws at once. **The hash is the address, so it may not name a view that is
  not on screen**: `showView` rewrites a name it will not honour — an unknown
  one, or `#sale` where no markdown was read — and `location.replace` corrects
  the address with it, or what a visitor bookmarks is a view they were never
  shown. Each view sets `document.title` and focuses its pane, because a
  bookmark, a history entry and a screen reader each have only a name to go on
  and all three views once had the same one.
- **The index never outgrows what it indexes.** The deals panel and the table
  divide the room evenly (`flex:1 1 0` on both), and 34vh is what the panel may
  not exceed when there is plenty rather than what it claims when there is not.
  A cap alone does the opposite of what it looks like: `max-height` does not
  make a flex item shrink, it *freezes* it — an item whose content exceeds its
  clamp contributes nothing to absorbing a shortfall — so the table was handed
  the remainder after the panel had taken its cap, and at 768×600 the remainder
  was **0px**. A `min-height` floor under the table fixes neither half: a floor
  that can exceed the room left is a pane painting over the footer.
- **One panel mechanism, not one per column.** Three cells open a panel —
  Places for the cabin ladder, Mandatory fees for the bill, Entry bar for the
  stated requirement — and all three go through `hoverPanel`, which takes an
  `opts.hoverOpens` (default true) governing the pointer half only; click and
  keyboard focus always open every panel it drives. Places and Mandatory fees
  keep hovering, because a diver comparing cabin ladders or fee books wants
  them without a click each time. **Entry bar turned it off (#151)**: sitting
  in the money block, running the pointer down that column to compare prices
  opened a dialog on every row it crossed. Keep the click half regardless of
  `hoverOpens`: hover does not exist on a touch screen and this page is built
  to work on a phone in a dive shop. Each trigger is a `<button>` with
  `aria-haspopup="dialog"`, opening one closes the others, and each host is
  **one div filled on demand** — nothing is lazily fetched here, so anything
  written per row ships 1,122 times. `rowFor` rebuilds the row rather than
  caching it against the trigger, because the bill depends on the toggles.
  The fee bill was a per-row `+` column and a full-width detail row (#149): it
  pushed every row below it down to answer a question about one row and spent
  26px of pinned width on all 1,122. **The whole breakdown moved, not a
  summary** — the fee table with its included lines at zero, the caveat that
  applies, and the second seller's bill with its three-state wording — because
  a panel holding only the line items would claim a total on part of a
  disclosure. What gives is height: it caps at 70vh and scrolls inside itself,
  and each fee table sits in its own `.fee-scroll` because the table's 460px
  `min-width` beats the panel's width and would otherwise drag the panel's
  header sideways. **The entry bar is not a fee** and opens from the Entry bar
  column instead; it led that dropdown because whether a diver may board is
  prior to what boarding costs, and that reason does not put it under
  *Mandatory fees*. And **the row mark moved with the column**: it is a bar on
  the pinned *first* cell, which was the expander and is now `.stick1`, so
  reclaiming the width did not quietly delete the mark.
- **Twelve columns, in four named bands, and the bill is tinted.** Sixteen
  columns of one weight gave the eye nothing to land on, so the money — the
  thing this site exists to publish — was exactly as findable as the return
  port. The header is two tiers: a band naming what the columns under it are
  about (`groups()`), then the sortable row. The band is built from
  **contiguous runs of each column's `zone`**, never written out per order, so
  `COMPACT_ORDER` moving the price block in front of the descriptive columns
  relabels the bands instead of mislabelling them — and a zone split into two
  runs would print its label twice, which is what
  `test_every_zone_is_contiguous_in_both_orders` refuses. Four of the sixteen
  columns are gone and **not one fact with them**: Return, Guests, From and To
  are each a second fact about a column that was already there, so each is a
  second line inside it (`.sub`). What is lost is a sort on those four, which
  is the price; the port is what the Departs from bank filters on, and that is
  the question a reader actually asks of it.
- **A wide window stretches the spacer, never the money.** `table
  { width:max-content; min-width:100% }` means anything wider than the table
  stretches it, and auto layout hands the surplus to whatever is not pinned to
  a width. The five descriptive columns are pinned — deliberately, so they
  cannot crowd the money out — which left the money as the only thing that
  could grow: at 2560px the **Total was 478px wide for a 60px figure**,
  Advertised 312, and every row's numbers drifted apart from the fees they are
  the sum of. The one thing this table exists to line up, unlined-up, on the
  biggest screens. One empty column at the end asks for `width:100%` and takes
  the lot, so every real column holds its content width at every size and the
  Total's right edge stops moving. **It is a body column too**, at about 22KB
  of payload: leaving the rows one cell short is legal HTML and hover paints
  across the gap because it is on the `tr`, but the *row rule* is on the
  cells — so the header's rule ran to the right-hand edge and every row's
  stopped 700px short of it, and the table appeared to end in one place and be
  underlined in another. `max-width` on a prose column is not the alternative:
  in auto layout it becomes the column's *preferred* width, so `.trip` took
  440px at every size and made the table wider at 1300 than it was before.
  **And above 1700px the table takes some of that room rather than leaving all
  of it beside itself** — 13px of horizontal padding against 8, 9px vertical
  against 5, rows at 55px against 47, and the two columns truncated at every
  width (the trip name and the reefs) 76px and 38px wider. The density was set
  by the narrowest window this has to work in and then applied to the widest.
  1700 is a number derived from the table's own content width, which is the
  #150 mistake, so it is guarded rather than trusted: the roomier table is
  about 1,499px against a 1,552px shell, and the suite asserts at 1700 and up
  that it still fits. The block sits at the *end* of the table's rules,
  because a media query adds no specificity and a `.trip` width written after
  it wins at every size — which is how it shipped doing nothing, once.

- **The money is on screen at rest, and on a phone the rows are cards.**
  `MONEY_FOLD` took columns off the front of the row until the Total fit, and
  it was answering the wrong question: the money only stayed on screen by
  hiding the boat behind it, and the widths deciding which columns went are set
  by whichever rows are on screen — so the fold moved when a filter changed and
  the reader lost a column for reasons they could not see. Below 760px the rows
  are `renderCards`, which have no columns to fold: the Total sits in its own
  corner at every width and nothing has to be measured to keep it there. **Both
  hosts are always filled**, so a rotation crosses the breakpoint with no redraw
  and `Ctrl+F` finds either — and `appendPage` appends to both, or a scrolled
  table would meet a card list holding the first 120 rows. Every card cell
  reads the same column's renderer, so the two cannot drift and the three panel
  triggers come across working — `cardCell` falls through to `show` unless the
  column declares a `card`, and **exactly one does**. Price per dive was a bare
  `€95` on the meta line, one gap from `+€400 → 500` and in the same weight:
  two euro figures, neither named, told apart only by a column heading a phone
  does not draw — and its `↓ 17+` carried its meaning in a `title`, on the one
  device that cannot open one. It sits under the total now, inside the same
  tinted box, and says *a dive* in words. The different words go on the column
  rather than into `renderCards`, so a second reading of the data has nowhere
  to appear; and both silences survive the move with a subject in front of
  them, because *not stated* under a total reads as a fact about the money. This replaced a typed
  breakpoint at 385px that was wrong on most phones (#150): 385 was measured
  against a Total column 155px wide and the column was 211px, sized by its
  worst-case row. **A typed breakpoint here is a number derived from the data,
  in a file that cannot see the data**, so it went stale exactly as the footer
  prose did in #144, and the page lost the number it exists to publish on 360,
  390, 393, 402 and 414 — silently, because the row still renders. The guard
  measures the card's own money block and **asserts which layout it measured**,
  because a hidden table's rect is all zeros and clears every bound without the
  number being anywhere.
- **The sort is a control of its own, because on a phone the header is not
  one.** The table header *is* the sort control, and below 760px there is no
  table: `.shell > table` is `display:none` and the rows are cards, so 1,122
  departures came in departure order and stayed there. Same shape as the money
  fold (#150) — a control that exists only in the layout it was written in. A
  native `<select>` and a direction button on the toolbar, not in the drawer:
  the drawer holds what picks rows and this picks none. Native because on a
  phone it opens the platform's own picker, which is thumb-sized and
  full-height and arrives with keyboard support and a screen-reader contract
  already written — this page has three popovers in `hoverPanel` without
  needing a fourth. **`paintSort` writes both renderings**, called from `draw`,
  so a heading click moves the dropdown and a pick moves the heading's arrow;
  two controls stating two orders is a reader with no way to tell which to
  believe. The direction is said in the column's own words — *Cheapest first*,
  *Earliest first*, *Fewest places*, *A–Z* — because "▲" says what the table
  did and not what the reader asked for; `SORT_WORDS` keys the columns a
  generic pair would be wrong about and everything `num` falls to the money
  pair. The options come off `ORDER`, once, grouped by `zone` under the
  header band's own words: `COLS` is re-sorted at the compact breakpoint, and
  a menu that rearranges itself when the window does is a menu nobody can
  learn. **`color-scheme` is declared for this control's sake** — the popup is
  the platform's and the platform paints it from that property, so without it
  the one thing on the page that is not ours is a white list over a dark page.
  **On a phone the labels give way, never a control and never a row.** Four
  controls plus their words are 477px against the 340 a 360px screen has, and
  this container wraps, so no shrink rule can save it — a wrapping flex
  container breaks the line before it shrinks anything. So SORT and INCLUDE
  go, *Rental gear* becomes the switch's own `short`, and the sort menu takes
  each column's own `short` where it has one: `Mandatory fees` is 30px of the
  difference and the header already resorted to that abbreviation for the same
  column. Both spellings are written and the stylesheet shows one, like the
  table and the cards. Nothing invents a new word and nothing types a width —
  a `select` sized to the longest column name, in a file that cannot see the
  column names, is the #150 mistake again. What replaces the missing labels is
  the accessible name: the switches read *Include nitrox in every total*,
  because a lit chip saying "Nitrox" beside a row of filter chips is a chip
  that looks like one. 360px up is one 42px row, exactly what the toolbar was
  before the sort existed; 344 and below wrap, which is the graceful half.
  **And `.tgroup.meta-right` is hidden whole, not emptied** — a zero-width
  flex item still carries `margin-left:auto` and still takes a line, which is
  how 360px reported one row of controls in a toolbar 48px tall.
  The toolbar's nowrap breakpoint also moved from 901px to 761px: 901 was the
  width the *meta line* stopped fitting at when the toolbar held two fewer
  controls, and a wrapping flex container breaks the line before it shrinks
  anything, so the sort put 761–900 on two rows until the rule was tied to
  `narrow` instead of to a number somebody measured once.

- **Every filter is in the drawer, and what is on is on screen.** Five chip
  banks stood permanently open above the table: 270px before the first row on a
  1440×900 window, a quarter of it spent on filters nobody had chosen. They
  fold at *every* width now rather than under 1000px — the argument was never
  about small screens. What stays on the toolbar is what changes the table's
  numbers rather than its rows (the two Include switches), the season, and the
  two chips a reader reaches for. Chrome above the rows went from 373px to 96.
  Two things stop a closed drawer hiding an active filter: the button carries
  the count, and `#activePills` names each one and drops it on a press — so
  undoing one no longer means opening the drawer to hunt for the chip that set
  it. **Both are about the controls the drawer hides, and that is what bounds
  them.** The two Include switches were in the count and in the bar, on the
  reasoning that the number should cover every control not as the page opened —
  which gave a badge reading "2" over a drawer holding nothing that put it
  there, and an *EXCLUDING nitrox* pill under a heading reading *Filtering on*
  about a switch that filters no rows. They are never hidden: they sit on the
  toolbar at every width, lit, an inch from the badge, which is the whole
  reason they are out there. So `Clear all` leaves them where the visitor put
  them too — it clears what the bar lists, and resetting a control the bar does
  not name is an unnamed side effect that moves every total on the page.
  **A bank ordered by a count is ordered by the count it is showing.** The
  `tally` banks — Departs from, Dive sites, Boat — re-rank on every recount,
  chosen chips leading. Dive sites is where it bites, because that bank is
  ANDed: pick Brothers and every other reef's number becomes *trips that visit
  both*, which reshuffles the list — and the chips held their boot order, so
  the reefs that combine with Brothers sat behind *+34 more* while ones that
  barely do led the bank. Only where the order *was* a count: months are
  chronological, the entry bar is ranked by how strict it is, and the two
  sellers are listed in neither's favour, so sorting any of those by
  popularity would replace a meaning with a ranking. Nothing moves under a
  finger — picking inside an OR bank does not change that bank's own counts.
  Inside, **one bank at a time**, picked from a rail that carries a count
  per bank: stacked, the panel was as tall as the sum of the longest of each,
  which is the wall of filters the fold exists to prevent. The toolbar does not
  wrap above 900px and the meta line shrinks instead — a wrapping flex
  container breaks the line *before* it shrinks anything, so one item 7px too
  wide put the whole toolbar on two rows and cost the rows 25px on every
  screen.
- **The shell is the *visible* viewport, not the tallest one.** `html, body
  { height:100% }` resolves against the initial containing block, and on iOS
  that block is the viewport with the URL bar **hidden** — so with the bar
  showing, the shell stood about 120px taller than the area it was being
  looked at through, and those 120px were slack the page could be panned
  through. Panned, the masthead and the rail went off the top and the footer
  floated over bare canvas at the bottom: the two things an app shell exists
  to pin, both unpinned, on the one device where the window is the whole
  screen. `100dvh` follows the bar as it comes and goes and leaves nothing to
  pan; `100%` stays under it for anything that does not know the unit, and
  every `vh` cap on a panel is doubled the same way for the same reason.
  `overscroll-behavior` is the other half — `none` on the document and
  `contain` on `.shell`, or a flick that reaches the end of the table hands
  itself to a page that should not have a scroll. **And nothing may go looking
  for slack either**, because `dvh` only covers iOS 15.4 up: `focus()` on a
  pane or a panel trigger takes `{ preventScroll: true }` — focusing asks the
  browser to bring an element into view and the only box it can move here is
  the shell — and `history.scrollRestoration` is `"manual"`, so a reload at
  `#trips`, `#sale` or `#history` cannot have a position put back into a
  window that does not scroll. **And the height is measured, not only
  declared.** Backgrounding the app and coming back fixed it, which says the
  layout was not wrong but *stale* — one forced reflow and it snapped right —
  so `fitShell` sets `body` from `window.innerHeight` on load and again on
  `resize`, `orientationchange` and `pageshow`, twice each because iOS reports
  the old height during a rotation and animates the bar away after a resize.
  `innerHeight` and not `visualViewport.height`: the two agree about the URL
  bar and disagree about the keyboard, and a shell that resized itself
  whenever somebody tapped the nights field is a worse bug than this one. It went unnoticed until
  `color-scheme` landed: before that the panned-into strip was the UA's white,
  and after it is black, which is what made a long-standing gap look like a
  new one.

- **A layout claim is measured, never grepped.** `tests/test_layout.py` drives
  Chromium over the three views at six windows, and over eleven phone widths; everything else about the split
  is asserted as template text, which is right for wiring and worthless for
  geometry. Eight source-string assertions passed over that 0px table,
  including the one named for the panel that caused it. The file skips without
  Playwright so `unittest discover` still needs nothing — the `layout` job in
  `ci.yml` is what makes it run.
- **Zero runtime dependencies**, stdlib only, and the site stays one
  self-contained HTML file. Tests use `unittest`, not pytest. One file makes
  page weight load-bearing: nothing is lazily fetched, so anything written per
  departure ships 878 times. Fees belong to the itinerary.
  **No CDN — nothing external at all**, and `ALLOWED_EXTERNAL` in
  `tests/test_dataset.py` is empty to keep it that way. The page used to pull a
  webfont stylesheet from Google; because a `<link>` in `<head>` is
  render-blocking, that cost **13 seconds to first row** whenever the host was
  slow or unreachable, against 0.6 seconds without it (#59). Fonts are the
  visitor's own now. Adding a host back is adding a way for the page to be
  blank on somebody else's network.
- **A page nobody read is not an empty one**, however it went unread. Two
  mechanisms have now deleted real, bookable trips this way and both were
  reported as withdrawals. A vessel-month page that answers nothing is asked
  again (`PARSE_ATTEMPTS`) and carried forward if it fails twice. A vessel the
  **barren skip list** holds back is carried too: nothing goes wrong there at
  all, the run simply chooses not to look, and AVO's and Blue's three sailings
  were dropped by a run that never asked — a probe found all three still on
  sale. `discover()` records what it skips via `not_looked_at`, so the skip and
  the failure travel down the same channel. `CARRY_MAX_DAYS` outlasts
  `BARREN_RECHECK_DAYS` by design.
- **And a vessel that sells nothing is not one that prices nothing.**
  `_departure_book` drops a sailing with no date, with no price, and outside
  the window, all three the same way: silently. So twelve mapped PADI vessels
  produced no row and the run could not say which of those it was.
  `fetch_padi._sailing_counts` records the lot per vessel and `why_empty`
  names it in the run log: *"22 dated sailing(s), none in the season
  (2026-09-05 to 2027-01-30)"* is a calendar that has not reached us, where
  *"17 … — and none of them priced"* is VIP One, which lists dated berths at no
  stated price. **Unpriced is counted over every dated sailing, not only the
  in-season ones** — VIP One's window stops in December, so counting inside it
  would have hidden the fact entirely. Recorded, never acted on: `promote` does
  not read it, and a boat with no price still gets no row.
- **A seller nobody asked is the same blindness.** The two sources merge on
  `(boat, date)` — exact, because a date has no spelling — and for eleven
  refreshes that merge could only ever fill a field, on the rule that the row
  count was the candidate's. It made "one row per sailing" quietly mean "one
  row per sailing liveaboard.com happens to list": 601 of PADI's 654 in-season
  sailings landed on a row we had and the other **53 were dropped**, among them
  Blue Storm's and Blue Seas' near-complete weekly seasons, on boats the page
  already carried. `promote` creates those rows now. The berth price is PADI's
  and says so; the fees are the vessel's own book, which the boat charges on
  board whoever sold the berth. Such a row carries **no `padi_price`** — one
  seller's figure repeated into the second seller's field prints as two sellers
  agreeing about a sailing one of them does not offer — and the page marks it
  `PADI only`, which is a fact about who was asked and not about the trip.
  **And on a vessel the barren list held back, even that is too strong.** The
  crawl keeps the distinction — `discover` records a skip through
  `not_looked_at` — and `promote` lost it, publishing *"liveaboard.com does not
  list this sailing"* on 87 sailings across Bella 2, Bella 3, Eriny and Blue
  Pearl: a result for a page nobody opened. All four have a vessel page the fee
  scraper read in full. `candidate.not_asked` names what the run declined to
  visit, recorded by the crawl rather than re-derived — the skip rule is
  date-dependent and promotion is pure — and the row says *not asked* instead.
  Same rule as `fees_known`: no fee lines means nobody looked, not that there
  are none.
- **A boat only one of the two sellers lists is still a boat.** 22 Egyptian
  liveaboards on PADI mapped to nothing on liveaboard.com; ids for them are minted in
  `data/padi_aliases.json` under `padi_only` and 10 carry season sailings, so
  the fleet is 77 rather than 67. `padi_only` means PADI is the only source of
  *sailings*: ten of the 22 have a liveaboard.com fee panel and simply no
  departures there. Such a vessel has no name and no operator from the first
  source, so both come from `window.shop` — `title`, and `fleetTitle` minus
  PADI's trailing "Fleet" — kept **verbatim**, shouting included, because
  `OPERATOR_ALIASES` already rules that tidying a company's capitalisation is a
  short step from deciding what it is called. **But the vessel page states the
  company, and it beats the fleet label outright** — `Product.brand.name` in
  the page's own JSON-LD, read by the fee run because that is the only pass
  visiting a boat with no departures. It is the *same source* every other
  operator here comes from, so preferring it is not a judgement call; 79 of 79
  vessels state one. It ends the shouting without anybody deciding how a
  company spells itself (`Bella Liveaboard` is that source's own words, not a
  tidy-up of PADI's `BELLA LIVEABOARDS`), and it settles Blue Pearl. **A fleet
  is not an operator**:
  PADI shelves MY Blue and MY Blue Pearl under one "BLUE PLANET Fleet", and
  folding that onto Blue's liveaboard.com-stated "Blue Planet Liveaboards"
  removed a duplicate row by asserting a company for a hull the other source
  connects to nobody. Two operator rows that may be one company is cosmetic; the assertion
  is not. **The two are one row now, and the rule is why that is allowed** —
  not folded on the fleet label, which still folds nothing, but on Blue Pearl's
  own page saying `"brand": {"name": "Blue Planet Liveaboards"}`. The fold is a
  fact rather than a tidy-up, which is the whole difference. Where the vessel's
  own fee panel is absent PADI's per-itinerary book becomes the itinerary's own
  (`padi_sourced_fees`); where the panel exists it wins outright — because a
  panel the boat publishes about itself outranks a seller's account of it, which
  is a statement about the two disclosures and not about the two sellers. Never a merge of the two: one figure per
  vessel against one per itinerary is not a difference you can add up, and a
  line from each is a bill neither seller quotes.
- **A sale is the list price a seller prints beside its own, never a banner.**
  liveaboard.com publishes no deals listing at all — `/liveaboard-deals` is SEO
  prose and the seasonal campaign pages carry only *"Up to 30% OFF"* over a
  region — but strikes the list price through beside every discounted cabin, and
  `fetch_cabins.py` has read that nightly since #79. It is a **whole-ladder**
  fact: of 263 discounted sailings, none is discounted in part and none leaves
  its cheapest cabin at list, so the advertised price against its own `<del>` is
  the sailing's discount. Setting the cheapest price against the *dearest*
  room's list price is the mistake here and reports a 33% sale as 40%.
  `Event.name`'s `33% Off:` prefix agrees on 241 of 241 and is still not the
  input: the ladder carries the money as well as the rate and finds 22 sales
  that carry no banner. **Neither seller marks down the other's price.** They
  agree on the percentage on 158 of 158 sailings where both speak, but a row
  states `pct` only from the seller whose fare it prints — two Red Sea Aggressor
  IV sailings are cut on PADI and at list here, and are *on sale* with no
  percentage rather than showing PADI's 33% off a fare nobody cut. An unread
  booking page states nothing, which is not "no": 3 of the 5 PADI-only
  discounts are exactly that.
  **Each markdown is dated to the day its own seller was read**, like the berth
  counts and for the same reason: `berths_read` and `padi_berths_read` are two
  crawls two days apart, and the sale marks stamped the first over both — on
  124 rows whose evidence is partly PADI's and 2 where it is all of it. The
  summary carries a date per seller and its heading takes the oldest of them,
  because a panel is only as fresh as its stalest half.
  **And the panel states what it could not read.** Three absences print
  identically to "not on sale" — a ladder rejected as stale, a sailing neither
  seller published a list price for (9), and a trip-name banner the seller read
  for it contradicts (2) — so `promote` counts each into `deals.coverage` and
  the panel says so. `promotion` stays unrendered and is now worth keeping for
  that count: a corroborating field nobody hears from when it stops
  corroborating is a field to delete.
- **A deal is placed by its vessel, never by the country beside it.** PADI's
  deals listing has to be asked for the USA as well as Egypt, because all three
  Red Sea Aggressors are filed under the USA and asking Egypt alone drops them.
  The same breadth returns Bahamas, Belize, Cayman and Roatan: 5 of 18 offers in
  the published season sail another ocean, so the field is wrong about where a
  boat is more than a quarter of the time and cannot place anything. `promote`
  joins the deal's vessel to a boat in the fleet and lets that decide. A vessel that
  joins to nothing is **named rather than dropped**: an Egyptian boat under a USA
  label that nothing has paired is exactly what the breadth is for, and only a
  name a person reads tells it apart from a Caribbean one. **Named in the build
  log, and to nobody else** — `promote` keeps `deals.unmatched` and `cli` prints
  a `::warning::` per vessel. It was on the sale view as well and came off:
  the reader there is shopping the sales, and a list of boats the page does not
  carry is the pipeline talking to its maintainer over the visitor's shoulder.
  The name is what may not be lost; the page was the wrong place to keep it. The change log obeys the unread-page rule too: absences in a
  reading the fetcher could not finish are not withdrawals, and it says so
  instead of reporting them.
- **Two itineraries must never share an id.** `Dataset.from_dict` keys them by
  id, so a collision keeps one and serves every departure of the loser the
  winner's reefs, fees and dive count: the row count stays right and the page
  is confidently wrong. Ids are truncated to 96 characters and the ports sit at
  the end, so two long names can collide without anybody typing a wrong
  character — which would break "two sailings differing only by port are two
  trips" silently. `promote` raises instead. A PADI trip name is folded onto a
  liveaboard.com one through `padi_key` and only where that key names exactly
  one of them; where two of a boat's own itineraries share it the fold is refused,
  because nothing can say which harbour the other source meant.
- **A joined string is not a record, and no parser makes it one again.** PADI
  states a trip's two harbours in two fields; `itinerary_from_payload` stored
  them joined into one `ports`, and nothing read it because nothing could:
  **two of the eight harbour names contain the separator.** *Hurghada -
  Marriott Marina - Hurghada - Marriott Marina* is either `("A", "B - C")` or
  `("A - B", "C")` and the string does not say. 436 of 447 split cleanly and
  the other 11 cannot be split without guessing — and a closed-vocabulary parse
  over today's eight names is exactly the rule that breaks silently the first
  time PADI names a ninth marina. The fix is the record: `port_from` and
  `port_to`, with `ports` dropped rather than left beside them for something to
  read by accident. Where a source hands you two facts, keep two fields;
  re-deriving them later is a guess wearing a parser's clothes.
- **An unreadable page is not an empty one.** A vessel page is fetched once per
  season month, so one response with no JSON-LD empties that boat's month while
  the other three come back fine — and it looks exactly like a boat that sells
  nothing in May. Fourteen pages did this on 2026-08-28 and the site deleted 49
  real, bookable sailings, DUNE Longara's whole May among them. `scrape` now
  carries those departures forward from the last run for up to `CARRY_MAX_DAYS`,
  keeping each row's original `retrieved` date, and says so. The distinction is
  the source's own: a *Product* node with no *Event* nodes is a boat selling
  nothing that month and its absence is the answer; no structured data at all
  answers nothing. Probed rather than guessed: 13 of the 14 answered in full
  on the first retry, so `PARSE_ATTEMPTS = 2` asks again and `carry_unread` is
  the net under a page that fails twice. A markup parser for them is ruled
  out — the JSON-LD is there, it just occasionally is not served. Same rule as
  the fee book: a run that could not look at something knows nothing about it.
- **Places left is a total across every room at that price, and it is a claim.**
  The advertised price is the bottom of a cabin ladder — on 864 of 864 sailings
  read, with no exceptions — so "how many spots at that price" is summed over
  every room selling at it: **233 of 864 sailings list more than one**, and
  taking the first in document order reported the price as gone on thirteen
  Yachtiano sailings while 8 berths were on sale at exactly it. One unstated
  count makes the whole total unknown rather than a partial sum. The rule lives
  in `promote._berth_blocks` and nowhere else; `app.js` displays it and takes
  only a plain minimum of its own, for the cheapest rung still on sale.
  A count is **the seller's claim on its booking page**, not a verified
  number — true when read and stale by morning — so the page prints the date
  beside it and `cabins.yml` runs an hour after the refresh. Ordering is
  load-bearing: read a day apart, all 864 ladders sat up to 0.6% above their
  own row, which is the panel disagreeing with the number that opened it.
  `berths` is a **list of seller blocks** because a sailing has more than one
  seller, and both fill one ([#92]). A seller that states a count but no ladder
  gets no cabin list — *24 places* and *24 places at a stated price* are
  different claims, and only the second is a ladder.
- **Two sellers, two counts, and never one number.** A block carries both
  *at the advertised price* and *on the sailing*, because they are different
  questions and only a ladder answers the first. **Which one PADI's
  `availability` answers was measured, not assumed**: against liveaboard.com's
  whole-sailing total it is exact on 77% of the 584 sailings where both speak
  and within two berths on 88% — a day between the crawls — against 22% and a
  mean error of seven berths for the count at the advertised price. So it fills
  the second slot only. Putting it in the first would have relabelled *22
  aboard* as *22 at this price* on the 249 rows with no ladder to contradict
  it, which is why the page prints **aboard** and **places** as different words.
  They disagree outright on 24 sailings — 21 where PADI still sells berths
  liveaboard.com calls full — and both are printed under the name of whoever
  said it and the day they said it. The two crawls run on different days, so
  `berths_read` and `padi_berths_read` are separate: one date over two sellers
  dates half of them wrong.
- **A ladder that contradicts its row is not that row's ladder.** The advertised
  price *is* the bottom rung, on 864 of 864, so a rung far below it is not a
  cheaper berth on offer — it is last week's prices still on the shelf. The day
  the Red Sea Aggressors' 33% sale ended, the daily refresh re-priced 36
  sailings to list while the booking pages behind them had been read two days
  earlier, and the page offered a €1,588 berth on a €2,371 sailing: a price
  nobody can buy, published by the site that exists to catch that. `promote`
  drops such a ladder past `STALE_LADDER` (3%, which leaves room for the 0.6%
  the whole fleet drifts overnight) and **names every one it dropped** — the
  fix is a fresh `cabins.yml`, and nothing in the dataset can put it back. The
  other seller's count survives the drop: it is not a ladder and has nothing to
  contradict. **CI never saw this**, because the scheduled data commits push
  with `GITHUB_TOKEN` and GitHub does not run workflows on those — so a refresh
  can turn `main` red and the next human PR is what finds out.
  **And a reading thrown away stays thrown away, in every field it touches.**
  The drop landed and the sale beside it did not: `_sale_for` was handed the
  same rejected book eight lines later, so all 36 dropped rows kept its
  discount — *“−33%, down from €2,371”* on a berth advertised at €2,371, the
  dataset quoting a source it had just ruled out, on the largest markdowns on
  the page. `_drop_stale_ladder` returns **which seller** it dropped rather
  than a count, `_list_prices` ignores that seller for the sailing, and
  `TestAStaleLadderCannotSpeak` asserts at both ends: the ladder never reaches
  the sale, and no shipped row states a list price equal to its own fare. Then
  the loss is stated — `deals.coverage.dropped` names the boats, because after
  the fix those rows read *not on sale*, which is one seller's discarded
  reading wearing its answer.
- **The history view is a week of refreshes, anchored to the log.** Not one
  entry: the refresh runs several times a day, and a single one is the noisiest
  possible window — a run that read nothing rendered a view saying nothing
  moved with days of real movement one link away. `recent_entries` measures the
  window from the **newest entry in the log, never from today**, because
  `render` is pure: a clock in it would make the same committed inputs render a
  different page tomorrow and turn `main` red with nobody having changed
  anything (`TestThePageIsWhatItsDataBuilds` normalises only the build stamp).
  A stale log shows as the dates the view prints, which a reader can check. A
  day with no entry is a day the refresh did not run, and the lead says so —
  never that nothing moved. A repeated date is two refreshes, printed
  separately under one heading for the day.
- **The refresh news is in the history, and the sale panel says what is on
  sale.** The two discount-move blocks were drawn inside the sale panel, so one
  page reported refresh news twice (#146). They sit under *What changed on the
  last refresh* now, as **their own blocks under their own headings**, never
  folded into `changes.compare`'s report: that is a diff between two committed
  datasets and each of these is a diff between the last two readings of *one
  seller*, crawled days apart. Neither is the commit boundary and neither is
  the other's, so each carries its own seller and its own *since* date — the
  same rule `berths_read` and the sale marks each learned once. The one-line
  count of those moves went with them: a signpost that restates both headings
  verbatim, dates included, is the split rather than a cure for it. So a day
  with **no discount anywhere has no sale view** — the moves used to keep it
  alive on their own, and "what is on sale" with nothing on sale is not a page.
- **The history view shows what happened, and does not interpret it.** Badges
  were proposed and declined: *cheapest reading this week*, *raised twice this
  week*, a sparkline per row. `sales.json` holds seven days and could support
  them, and that is not the question — this site reports what the sellers did,
  and a row that grades a price into a buying signal is the shape of the sales
  page it exists to correct. So `KEEP_DAYS` stays at a week, nothing is
  computed across readings, and the view lists refreshes. It is for visitors as
  well as for us, which is why it is a view rather than a link, and why it is
  the plain list rather than the clever one.
- **A change report is rendered, never transcribed.** `changes.compare` builds
  a report of dataclasses; `changes.render` flattens it to text for the log and
  `CHANGES.md`, and `changes.as_dict` emits the same comparison as data into
  `data/changes.json`, which is committed and which the page renders as rows.
  Neither shape is derived from the other. The page used to read that Markdown
  back and escape it into a `<pre>` — a terminal transcript served to a
  browser, boat names cut mid-word to fit eighty columns, and not one line
  clickable through to the sailing it was about. `BOOK_LIMIT` caps the book at
  120 rows per kind because one refresh landed 644 fare moves — 136 KB of the
  200 the week came to — and what is cut is **counted**, never silent. A
  checkout whose last refresh predates the book still gets the prose, and
  converges within a week.
- **A change report never drops a row silently.** `changes` caps its blocks and
  suppresses sub-unit price moves as source rounding — and says so, with a
  count, every time. A truncated list that does not admit it reads as "that was
  everything", which is exactly the failure this project exists to correct in
  other people. The same rule kills four false positives it used to report: an
  FX move is not a reprice, a vessel losing *every* departure is a failed fetch
  and not a cancelled season, a newly parsed field has not changed, and a
  currency switch is not a price move.
- **The committed seed must match `tools/make_seed.py`** — CI enforces it, so
  edit the generator, not the JSON.
- **The committed dataset must match what `promote` produces from the committed
  inputs** — CI enforces it too (`promote --check`). Promotion is pure, so the
  two agreeing is the statement "the published page is this code's output". A
  parser fix that never reaches `data/` is a green build and a site that is
  still slightly wrong, which is the failure this project exists to correct in
  other people.

## Sources

`padi.com` and `liveaboard.com` are the only permitted sources. Both are
reachable locally since the allowlist landed (#1), and from GitHub's runners.

**`/BookingStep1` and the `?m=` selector are disallowed by liveaboard.com's
robots.txt, and we fetch them anyway** — a blank line after `User-agent: *`
orphans all 31 rules, so `urllib.robotparser` discards them and `can_fetch()`
says yes. That is a deliberate call taken 2026-08-30, not an oversight, and the
reasoning and the price of reversing it are in
`docs/sources/liveaboard.com.md` under *robots.txt, and the blank line*. Do not
re-derive it as a fresh discovery; do read it before quoting `can_fetch()` as
permission.
**Do not write markup parsers for pages nobody has fetched** — run a probe on a
runner, read what came back, then parse. `tools/probe_*.py` write nothing and
exist for exactly this.

**`docs/sources/{host}.md` says where every fact lives** — URL, JSON-LD path or
selector, browser or not — and, with equal weight, what has already been ruled
out. Read it before opening a parser and before writing a probe. **A probe that
discovers something updates that file in the same commit**, negative results
included: a lead ruled out and not written down gets followed again, and a
stale map is worse than none.

`tools/scrape_fees.py` drives a browser weekly and reads the fee disclosure,
the gear prices and the specification table from one page load. A capped run
(`--limit N`) merges into the existing fee book rather than replacing it: it
knows nothing about the vessels it did not visit.

**Three of those four panels are not client-rendered, though** — probed
2026-08-31 over plain `urllib` on all 79 vessel pages, and the extras and the
gear dialog parse from the served bytes exactly as they do through Playwright.
The specification table and the diving amenities are in those bytes too and fail
for a different reason: the page ships `<dl><dt>Year built <dd>2017</dl>` with
the tags unclosed, and `SPEC_ROW`/`TICK` are getting closing tags only from the
browser's normalised DOM. So what those two want is an HTML parser, not a
browser. Not acted on — dropping the weekly Playwright run is a change with its
own probe, not a side effect of a fee fix — and written down in
`docs/sources/liveaboard.com.md` so it is not re-derived as a negative.

What one *trip* says about itself needs no browser and no crawl to find:
`tools/fetch_itineraries.py` builds every URL from ids already in
`data/archive.json` and fetches `/itinerary/getpopupv2` over plain HTTP, in the
daily refresh. Incremental — a trip already in `data/itineraries.json` is not
re-fetched — so the first run is ~314 requests and every run after it is a
handful. Everything else in the pipeline describes the boat's year.
`itineraries.yml` runs it alone, capped (`--limit N`), which is how a change to
the parser gets proved against three real trips before it is pointed at three
hundred of somebody else's pages. A capped run merges into the book, like
`scrape_fees.py --limit`.

**What PADI states has a cadence too, and it is `padi.yml`, daily.**
`tools/fetch_padi.py` ran in no workflow at all until then, and by that point
five published facts rested on it — the entry bar, the stated dive count, the
only fee book the 22 PADI-only vessels have, PADI's berth price on 654 sailings
and its berth count on 833. Everything else here reports its own failures; this
one's failure mode was **"nobody ran it"**, which nothing reports. `data/
padi_raw.json` stays gitignored and is *cached* on the runner instead, so an
ordinary run re-fetches only the itinerary listings and the sailings — ~80
requests against ~530 from cold — and every run uploads it as an artifact,
because a re-parse that needs the raw store should download it rather than
re-crawl 530 pages. **The book is rebuilt whole from that store**, so a cold
runner would rebuild it with zero trips and write it: green job, valid file,
five facts gone. `MIN_BOOK_RATIO` refuses that, the way `fetch_cabins.py`
refuses to rewrite its file after reading nothing.

**A deal is a promotion, and PADI publishes them without a browser.**
`/liveaboard-deals/` is an AngularJS shell — 272 KB, no prices, and a `page=`
that page 99 answers with page 1 — but `/api/v2/travel/promotions/` takes that
page's own `country` and `date` parameters over plain HTTP and pages honestly.
`tools/fetch_deals.py` reads it in the daily refresh and appends one entry per
day to `data/deals.json`, which is **committed**: a change log is a diff between
two committed days, and one computed from an artifact silently becomes "no
changes" once the artifact ages out. A re-run on a day already in the book makes
no request at all. Paging stops when a page adds no offer identity already seen
— never on a page number, because the page beside it proves a page number here
carries no information.

**The other seller's sales are kept the same way, as a projection.**
liveaboard.com publishes no listing, so its markdowns are read off the booking
pages by `fetch_cabins.py` — and `data/cabins.json` is rewritten whole each run,
which left the *bigger* signal (263 sailings on 22 boats against PADI's 13) able
to say what was on sale and not what had moved. `tools/derive_sales.py` reads
the committed cabin book and writes `data/sales.json`: one day per reading,
three fields per sailing, no request of its own. Two things are load-bearing. It
files each sailing under **that record's own `collected`**, never the book's
header, because a capped run merges and leaves most of the file older than its
header says. And a day is a **census, not a list of sales** — every sailing read
that day is in it, discounted or not, because the keys are the only thing
separating *not on sale* from *not looked at*; `promote` diffs over the sailings
both readings covered and prints the count of those it could not. Thirty days of
it would be 2 MB, so `KEEP_DAYS` is a week here and a month there.

`data/snapshots/` is gitignored; CI keeps it as a build artifact for 14 days.
`data/archive.json` is committed and holds every JSON-LD node each page
published, parsed or not — re-scraping recovers today's prices, never
yesterday's. Add fields to the parser freely; do not trim the archive to match
what the parser happens to read.

When the parser learns to read a field the archive already has,
`tools/reparse_candidate.py` fills it onto the committed candidate offline —
re-crawling 320 pages to re-read data already in the repository is both slow and
rude. It only ever fills, never overwrites, so it is a no-op after a fresh
crawl.
