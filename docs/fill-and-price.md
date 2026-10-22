# Does a fare rise as a sailing fills?

Measured 2026-10-22 by `tools/fill_and_price.py`, over 54 days of
`data/egypt-2027.json` (2026-08-27 to 2026-10-22, the last commit of each
day). It reads nothing but the git log and writes nothing. #6.

**No, not on this log.** On all three sellers a fare is no likelier to rise
after the places on a sailing fall than after they *grow*. A seller that
touches a sailing moves its count and its price together, in either direction.
A rise after a fill is a few times likelier than one where the count held,
and that is the comparison that would have read as a yes.

## The numbers

A step is two consecutive readings of one sailing by one seller. The window
is that step and the next one, so a reprice the morning after a booking
counts. A *fill* is a step where that seller's own count fell, including to
nothing. A fare *rose* when it moved by `MIN_MOVE` or more beyond what the
rest of its boat moved across the same two readings.

| seller | after a fill | after places grew | where places held |
|---|---|---|---|
| liveaboard.com | 6 of 314 (1.9%) | 5 of 138 (3.6%) | 149 of 28,297 (0.53%) |
| PADI | 3 of 249 (1.2%) | 7 of 152 (4.6%) | 16 of 41,481 (0.04%) |
| divebooker | 0 of 33 | 0 of 14 | 3 of 5,021 (0.06%) |

A fare is as likely to fall after a fill as to rise: on liveaboard.com 10
falls against 6 rises, on PADI 2 against 3. Fills themselves are rare this
far out. The first sailing in the season is seven months away, and 436 of
liveaboard.com's 46,632 steps saw its count fall.

## What was set aside, and why

Each of these is a mistake the change log made once over the same data:

- **Boat-wide moves:** on liveaboard.com, 5,106 steps where places held and
  77 of its fills; on divebooker, 2,329 steps where places held and 31 of its
  65 fills. These two sellers
  quote in dollars fares the operator set in euro, so a boat's whole season
  moves with the rate. Every sailing moving by one ratio is the boat's move,
  never a fill.
- **Currency switches:** on liveaboard.com, 1,829 steps where places held
  and 41 fills. It answered in euro in the 2026-09-04 and 2026-09-13 readings,
  and a step across that is not a price move.
- **Markdowns:** on liveaboard.com, 97 steps where places held and 4 fills;
  on PADI, 157 and 2. These are steps where this seller's sale started, ended
  or changed rate.
- **Too few siblings:** 39 liveaboard.com steps where places held; on
  divebooker, 22 and 1 fill. Fewer than three of the boat's sailings were re-read across the step, so the
  boat's move cannot be told from the sailing's.
- **Places not re-read:** 9,821 liveaboard.com steps, 1,434 PADI and 13,686
  divebooker. Its ladders were read from 2026-09-22 only, and since #154 a
  quiet liveaboard.com ladder is read every third day.
- **Sailings keyed by seller, boat and day, never by id:** in no snapshot did
  a seller start two rows on one boat on one day, so none was set aside for
  that.

## The nine rises after a fill

liveaboard.com:

- AVO, 2027-06-05: 24 → 12 places, 1,701 → 1,810 USD. Its struck-through
  list price rose with it, so a 5% sale stayed a 5% sale over a dearer fare.
- Dune Titan, 2027-06-05: 30 → 28 places, 1,232 → 1,288 USD.
- Emperor Asmaa, 2027-06-19: 18 → 0 places, 1,450 → 1,456 USD, just over
  the floor.
- Oceanix, 2027-07-24: 24 → 0 places, 1,147 → 1,168 USD.
- Sunlight, 2027-06-15: 20 → 18 places, 1,739 → 1,791 USD.
- Vita Xplorer, 2027-06-19: 24 → 14 places, 1,233 → 1,577 USD, 28%, with no
  markdown marked on either side.

PADI:

- Discovery I, 2027-07-31: 3 → 0 places, then 871.25 → 1,045.50 EUR on the
  next reading. That is a sixth off ending, and **the rule could not see it**:
  the row's sale marker did not change. It names both sellers at one rate,
  liveaboard.com's 15%.
- DUNE Longara, 2027-06-12: 30 → 15 places, 1,600 → 1,700 EUR.
  liveaboard.com's count of the same sailing stayed at 30.
- New Sambo, 2027-05-16: 20 → 10 places, 470 → 570 EUR.

Four of the nine halve the count in one reading and reprice in the same one.
None is a sailing selling berth by berth while its fare climbs.

## The ladder does not climb either

When a fill raises an advertised fare mechanically, it is because its cheapest
rooms sold out and the next rung became the price. On liveaboard.com, whose
advertised fare is the bottom rung, that happened 4 times while a dearer room
was still on sale. The fare held twice, moved with its boat once and changed
currency once, so it never rose. The advertised fare stays on the sold-out
rung, which is what *0 places at this price* says on the page. The other 106
times its cheapest rooms sold out, the whole sailing went with them.

## What this cannot see

- **A markdown the dataset does not mark** reads as a rise, as Discovery I
  shows.
- **A seller repricing a whole season as it fills.** The boat-wide rule takes
  that with the exchange rate, and nothing in a day's readings can separate
  the two.
- **A selection that favours finding a pattern, not hiding one.** Since #154
  a liveaboard.com ladder is read the day its fare moves and every third day
  otherwise, so a fill and a fare move are likelier to land in one step than
  a fill alone.

Re-run it as the log grows. It needs the whole history, so on a fresh clone
run `git fetch --unshallow` first:

    python3 tools/fill_and_price.py
