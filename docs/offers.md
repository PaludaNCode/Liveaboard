# Two sellers, one offer — how a markdown reaches the page

Both sellers publish a markdown and both are read. liveaboard.com strikes the
list price through beside every cabin, which `tools/fetch_cabins.py` reads
nightly off the booking pages; PADI states `compareAtPrice` on a sailing and
runs a deals listing on top, which `tools/fetch_padi.py` and
`tools/fetch_deals.py` read daily. Neither is ever allowed to speak for the
other, and neither is allowed to speak about a reading this pipeline threw
away.

This file is what the panel means, and what has gone wrong in it before.

## The join is not the problem, and never has been

Worth stating first, because every failure below sits downstream of it. Against
the published season, every one of PADI's 10 in-season offers lands on a boat
of ours, on the exact sailing it names, at the percentage that boat's ladder
works out to; the two sellers' berth prices agree to the euro on 281 of the 609
sailings both price and to within 2% on 546. `promote` joins a deal to a boat
and lets the boat place it — never PADI's country field, which is wrong about
where a boat is more than a quarter of the time.

## What each field is

| field | seller | what it is |
| --- | --- | --- |
| `departures[].sale.sellers` | both | who published a list price above what they charge |
| `departures[].sale.pct` / `.was` | one | read **only** from the seller whose fare this row prints |
| `departures[].promotion` | the operator | the “20% Off” glued to a trip name. Kept, never rendered |
| `deals.on_sale` | both | every discounted sailing, per boat, with its window and a read date per seller |
| `deals.offers` | PADI | one exemplar sailing per boat, with money |
| `deals.coverage` | — | what the panel could **not** read |
| `stale_ladders` | — | every reading dropped for contradicting its own row |

`pct` is absent whenever the discounting seller is not the one who set this
row's price. Such a row is still on sale and still filters as one; printing
another seller's markdown against a fare nobody cut would invent a saving.

## Three failures, and the rule each one broke

**A rejected reading spoke anyway.** `_drop_stale_ladder` throws away a cabin
ladder whose bottom rung sits more than `STALE_LADDER` (3%) from the price above
it, because such a ladder is last week's prices on this week's shelf. The sale
beside it was then read off the same book, eight lines later. So all 36 rows
whose ladder was discarded kept its discount, and on all 36 the “down from”
figure was the price printed beside it — *“−33%, down from €2,371”* on a berth
advertised at €2,371, while the panel below printed PADI withdrawing the same
three offers. The drop now names **which seller** it dropped and `_list_prices`
ignores that seller for that sailing. `TestAStaleLadderCannotSpeak` asserts it
at both ends: the ladder never reaches the sale, and no shipped row states a
list price equal to its own fare.

**One date stood for two books.** `berths_read` and `padi_berths_read` are two
crawls two days apart. The sale marks stamped the first over both — on 124 rows
whose evidence is partly PADI's and 2 where it is all of it — eleven lines from
the code that already did this correctly for berth counts. `namedReadings()`
now dates each seller separately, in the row's tooltip and in the panel's own
*Marked down by* column, and the summary's headline takes the **oldest** of the
three books it draws on rather than the freshest.

**The absences read as answers.** Three different things print identically to
*not on sale*: a ladder rejected as stale (36 sailings on 3 boats), a sailing
neither seller published a list price for (9), and a trip-name banner the
seller read for it contradicts (2). None can be recovered from the rows the
panel is drawn from — that is what makes them absences — so `promote` counts
each into `deals.coverage` and the panel states them, in the shape the change
log's `not_compared` note already used.

## One table, because it is one question

The panel was two tables: 22 boats with a window and no money, sorted by name;
10 boats with money and no window, sorted by sail date; ten boats in both and
no way to read across. They were defended as two different claims, and the
claims are different — a ladder says which weeks are cut, a listing names one
exemplar sailing and what it costs — but the difference is which half of a row
each seller can fill, not which table it belongs in.

One row per boat now, with a column group naming whose half is whose, and the
**union** of the two: a PADI offer for a boat no ladder has caught keeps its
row with an empty left half rather than vanishing out of a panel headed *what
is discounted*. Today that case is empty — the 10 boats PADI advertises are
exactly the 10 whose ladders also show a `padi.com` markdown — and it is
guarded anyway, because a merge keyed on either seller's list alone passes
every test until the day it silently drops the other's.

The `As quoted` column went into the `Now` cell's title: it was a dash on eight
of ten rows, and PADI's own name for the offer stays verbatim beside it,
percentage and shouting included.

## What the row itself says

A single figure in the *Advertised* column meant three different things and
printed identically: both sellers quote it (155 rows), PADI quotes the sailing
and does not disclose enough of its own bill for the row to price it — so its
berth price exists and no column shows it (454 rows) — or nobody else was asked
(513 rows). The middle case now carries the same `2 sellers` mark the Total
uses, with PADI's figure in the tooltip and both bills one click away in the
expanded row.

A berth-to-berth *column* stays refused, and the reason is unchanged: it
compares the half the two sellers agree about — under €5 apart on 89% of
matched sailings — and hides the half they do not, where 43 of 74 comparable
trips disagree and 16 by more than €150.
