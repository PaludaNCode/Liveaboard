# Two sellers, one offer — what is misaligned and what to do about it

Both sellers now publish a markdown and the page reads both: liveaboard.com
strikes the list price through beside every cabin, PADI states `compareAtPrice`
on a sailing and runs a deals listing on top. The join between them is sound —
every one of PADI's 10 in-season offers lands on a boat of ours, on the exact
sailing it names, at the percentage the ladder works out to, and the berth
prices agree to the euro on 281 of the 609 sailings both sellers price and to
within 2% on 546 of them. What
is misaligned is everything downstream of the join: one panel publishes a
discount off a ladder the same run rejected, two dates are stamped as one, and
the two sellers' offers are drawn as two tables that share no column.

Measured on `data/egypt-2027.json` as of 2026-08-30 (1,122 departures, 265 of
them marked down).

## 1. A sale outlives the ladder that was dropped for contradicting it

`promote` drops a cabin ladder whose bottom rung sits more than 3% from the
price above it, because such a ladder is last week's prices still on the shelf
(`_drop_stale_ladder`, `promote.py:1329`). It then reads the discount off that
same rejected ladder: `berths, outdated = _drop_stale_ladder(...)` at
`promote.py:2397`, and eight lines later `_sale_for(ladder, ...)` at
`promote.py:2417` is handed the raw book rather than the surviving blocks.

So **all 36 rows whose ladder was thrown away still carry its sale, and on all
36 the "down from" figure is the price printed beside it.** Red Sea Aggressor
II, 1 May 2027: advertised €2,371, sale mark `−33%`, tooltip *"Down from
€2,371"*. The three Aggressors are 36 of the panel's 265 discounted sailings
and 3 of its 22 boats, and PADI withdrew all three offers on the same run — so
one screen prints *"Withdrawn — Red Sea Aggressor II"* directly under a table
saying *"12 of 18 sailings, 33% off"*.

This is the failure the stale-ladder rule was written for, one call site short
of being fixed. It is also the failure this site exists to report in operators:
a discount off a price nobody was charged.

**Fix.** Pass the surviving blocks, not `ladder`. A rejected ladder contributes
no opinion — the same rule as an unread page — so those rows fall back to what
PADI's book says, which is that the sale ended. The panel headline becomes
**229 sailings on 19 boats**. Guard it with a test that asserts no departure
carries a `sale` whose `was` equals its own advertised price, which is the
observable form of the bug and cannot be satisfied by a coincidence.

## 2. Dropping a ladder is a fact about a reading, and the page never says it

`stale_ladders` is written into the dataset (36 entries) and named in the
`promote` build log. Nothing renders it. After fix 1 those rows will read *not
on sale*, which is one seller's silence dressed as its answer — precisely the
distinction the rest of the pipeline is built on (`fees_known`, `not_asked`,
`carry_unread`, and `salesChanges`' own `not_compared` note).

**Fix.** One line in the sales panel, in the shape `salesChanges` already uses:
*"36 sailings on 3 boats had a ladder too far from their own price to be this
sailing's and are not reported here; a ladder that contradicts its row has not
said no, it has gone stale."* The count is already in the payload.

## 3. One read date stamped over two sellers' readings

`berths_read` is 28 Aug; `padi_berths_read` is 30 Aug. `saleTag`
(`app.js:272`) appends `berths_read` to every sale tooltip regardless of who
discounted, and the fleet table's paragraph says *"read 28 Aug"* over all 265
sailings. But **124 of the 265 rest partly on PADI's 30 Aug book and 2 rest on
it entirely**, and 10 of the 22 boats print `padi.com` in the panel's own *Per*
column. `CLAUDE.md` already states the rule for berth counts — *one date over
two sellers dates half of them wrong* — and `app.js:389` already implements it
correctly for the ladder popover, eleven lines from the code that gets it wrong.

**Fix.** `saleTag` reads the date per entry in `sale.sellers` (both dates where
both spoke); `_on_sale_summary` carries a read date per boat row rather than one
for the block, and the fleet table prints it in the column that already names
the seller.

## 4. The trip-title banner disagrees with both sellers on three sailings, silently

`promotion` — the *"20% Off"* an operator glues to a trip name — is kept in the
dataset as corroboration and deliberately not rendered, on the ground that the
ladder is the stronger and agreeing source. It agrees on 205 of 209. The other
**four are now the interesting ones**: ALSURAYA 28 Aug and AVO 24 Jul advertise
10% and 5% off in their own trip names while PADI, the only seller read for
them, states price equal to list; Ocean Lovers 7 Aug has no reading at all;
Royal Evolution 8 Jul carries a banner where only PADI discounts. A field kept
as corroboration is worth keeping only if the page says something when it stops
corroborating.

**Fix.** Count them in the panel — *"3 sailings advertise a discount in the trip
name that no seller's list price supports"* — or drop the field. Not both.

## 5. Nine rows have no evidence either way and print as "not on sale"

Nine departures have neither a cabin ladder nor a PADI sailing entry, so nothing
has ever looked at whether they are discounted, and the `On sale` filter
excludes them as though something had. Small, and the same rule as the four
above: absence is not a no. Worth an `unknown` state only if it is free; worth a
sentence in the panel either way.

## 6. The two sellers' offers are drawn as two tables that share no spine

| | *Every discounted sailing, by boat* | *Advertised on padi.com* |
| --- | --- | --- |
| unit | one boat, whole season | one exemplar sailing |
| columns | Boat, On sale, Off, From, To, Per | Sails, Boat, Now, Was, Saving, Offer, As quoted |
| money | none at all | the whole middle of the table |
| window | From–To | one date |
| sorted by | boat name | sail date |

Ten boats appear in both and a reader cannot read across: Discovery I is row 6
of one table without a price and row 8 of the other without a window, and
nothing on the page says they are the same offer. The split is defended as two
different claims, and the claims *are* different — but the difference is which
half of one row each seller can fill, not which table it belongs in.

**Fix.** One table, one row per boat, sorted once: `Boat | On sale (n of N) |
Off | From | To | Seller | PADI's exemplar`, with the last group filled on the
10 boats PADI advertises and empty on the other 12 — an empty cell being the
honest statement that PADI publishes no listing for that boat. The three
liveaboard-only facts and the exemplar's money then sit on one line, and the
"no listing" case reads as a gap rather than as an absence from a table nobody
knew to look in.

Two smaller things in the same panel. PADI's offer name repeats the discount a
third time — *"20% off · €295"* beside *"Monthly Special 20% Off + Free
Nitrox"* — so print the part of the title that is not the percentage. And the
panel lays out to 630px inside a 1500px viewport, with its prose at 570px: on a
desktop the section reads as a phone layout with a third of the page beside it
empty.

## 7. The Advertised column prints one number for three different situations

A single figure in that column means *both sellers, same price* (155 rows print
through the two-seller pair), or *only our price, because PADI's fee book is
incomplete* (454 rows where PADI's berth price is known and appears in no
column), or *only one seller lists this sailing at all* (513 rows). The Seller
column tells them apart and is column 17 of 17.

Not a bug — pairing berth against berth was removed on purpose, because it
compares the half the sellers agree about and hides the half they do not — but
it is the reason the offers "look" misaligned before any of the defects above
are reached. The cheapest repair is a mark on the pair rather than a new
column: the two-seller rows already say `2 SELLERS` in the Total, and the same
mark on Advertised would separate *one price sold twice* from *one price we
have*.

## Order of work

1. **Fix 1** — one argument at `promote.py:2417`, plus the guard. It publishes a
   false price today; everything else is a presentation debt.
2. **Fixes 2 and 3** — the two honesty notes, both small, both already have
   their numbers in the payload.
3. **Fix 6** — the merged table, which is where the "misaligned" reading
   actually comes from, and the panel's width with it.
4. **Fixes 4, 5 and 7** — decide-then-do: each is a sentence or a deletion, and
   choosing wrongly costs nothing that a later change cannot undo.

Fixes 1–3 change `data/egypt-2027.json`, so each needs `promote` then `build`
in the same commit.
