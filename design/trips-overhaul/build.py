#!/usr/bin/env python3
"""Assemble the .dc.html artboards from the hand-authored templates.

The data is real: 240 departures read off the built page with Playwright,
each carrying all four Include-switch readings so the two switches in the
prototype move the same numbers the live page moves. Nothing here computes a
price.

    python3 design/trips-overhaul/build.py
"""

import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROWS = HERE / "rows.json"


VARIANTS = ("n1g1", "n0g1", "n0g0", "n1g0")


def normalise(rows):
    """Season-relative dates and the four toggle readings as one ordered list.

    The season is one year, so the four leading characters are the same on
    every row and the JS only ever needs month and day.
    """
    for r in rows:
        r["start"] = r["start"][5:]
        r["end"] = r["end"][5:]
        r["v"] = [[r["v"][t]["feeLo"], r["v"][t]["feeHi"],
                   r["v"][t]["totLo"], r["v"][t]["totHi"]] for t in VARIANTS]
    return rows


def facets(rows):
    """The filter banks, counted over the sample the prototype ships.

    The live page counts over all 1,122 departures; these count over the 240
    the prototype carries, so a chip's number always matches what pressing it
    actually leaves on screen.
    """
    from collections import Counter
    port, site, boat, bar, sell = (Counter() for _ in range(5))
    for r in rows:
        port[r["from"]] += 1
        boat[r["boat"]] += 1
        bar[r["entry"]] += 1
        for s in r["sites"]:
            site[s] += 1
        sell["Both" if len(r["sellers"]) == 2 else
             ("PADI only" if r["sellers"][0] == "padi.com"
              else "liveaboard.com only")] += 1
    order = ["OW", "OW + 5", "OW + 10", "OW + 15", "OW + 20", "OW + 25",
             "OW + 30", "OW + 40", "ADV", "ADV + 10", "ADV + 15", "ADV + 20",
             "ADV + 25", "ADV + 30", "ADV + 35", "ADV + 40", "ADV + 50"]
    return {
        "ports": port.most_common(),
        "sites": site.most_common(),
        "boats": boat.most_common(),
        # Least demanding first: a ladder, not a list of popular options.
        "bars": [(b, bar[b]) for b in order if b in bar],
        "sellers": [(k, sell[k]) for k in
                    ("Both", "liveaboard.com only", "PADI only") if k in sell],
    }


def months(rows):
    from collections import Counter
    short = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
             "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    c = Counter(short[int(r["start"][:2]) - 1] for r in rows)
    return dict(c)


def emit(tpl_name, out_name, rows, *, width, height, drawer,
         logic="_logic.js", bank="ports"):
    tpl = (HERE / tpl_name).read_text()
    shared = (HERE / "_shared.js").read_text()
    logic = (HERE / logic).read_text()
    j = lambda o: json.dumps(o, ensure_ascii=False, separators=(",", ":"))
    data = "\n".join([
        "var W = %d, H = %d;" % (width, height),
        "var ROWS = %s;" % j(rows),
        "var FACETS = %s;" % j(facets(rows)),
        "var MONTH_COUNTS = %s;" % j(months(rows)),
        "var N_SALE = %d;" % sum(1 for r in rows if r["sale"]),
    ])
    out = (tpl
           .replace("__SHARED__", shared + "\n" + data)
           .replace("__LOGIC__", logic)
           .replace("__DRAWER__", "true" if drawer else "false")
           .replace("__BANK__", bank)
           .replace("__W__", str(width))
           .replace("__H__", str(height)))
    for token in ("__SHARED__", "__LOGIC__", "__DRAWER__", "__BANK__",
                  "__W__", "__H__"):
        if token in out:
            sys.exit("unreplaced %s in %s" % (token, out_name))
    (HERE / out_name).write_text(out)
    print("%-22s %6.0f KB  %d rows" % (out_name, len(out) / 1024, len(rows)))


def main():
    rows = normalise(json.loads(ROWS.read_text()))
    emit("main.tpl.html", "Main.dc.html", rows,
         width=1440, height=900, drawer=False)
    # The drawer open, and a lighter row set behind it: the point of this
    # artboard is every filter option at once, not a second copy of the table.
    emit("main.tpl.html", "Filters.dc.html", rows[:60],
         width=1440, height=900, drawer=True, bank="sites")
    emit("phone.tpl.html", "Phone.dc.html", rows[:40],
         width=390, height=844, drawer=False, logic="_logic_phone.js")


if __name__ == "__main__":
    main()
