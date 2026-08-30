/* Interactive layer.
 *
 * Every euro amount on a fee line arrives pre-resolved from Python: basis
 * normalised to a per-trip figure and any source currency already converted.
 * All this file does is decide which lines count under the visitor's toggles
 * and add them up, which keeps one authoritative implementation of the pricing
 * rules rather than two that quietly disagree.
 */
(function () {
  "use strict";

  var D = JSON.parse(document.getElementById("payload").textContent);

  /* Tiers that count without the visitor asking. Mirrors DEFAULT_ON_TIERS in
     liveaboard/taxonomy.py — change one and you must change the other. */
  var DEFAULT_ON_TIERS = { base: true, mandatory: true, customary: true };

  /* Below this, the two sellers are quoting the same price and the column says
     so rather than printing a difference.

     Five euro, and the data picked the number rather than taste. Under one
     euro, 317 of the 601 matched sailings read "same"; under two, 526 do. The
     209 rows in that gap are almost all the same figure -- €1.02 -- which is
     not a price difference at all but the rounding on a dollar price converted
     to euro, and printing it as "−€1" invited a reader to compare two boats on
     the arithmetic of an exchange rate. Widening to five adds ten more rows on
     top of that and no further cluster: 536 of 601 at €5, and still 536 at €10.
     So the threshold sits past the noise and well short of anything a diver
     would choose a boat over, and it is one number here rather than two that
     can drift -- the column and the sentence in the expanded row read it. */
  var PADI_SAME = 5;

  var euro = new Intl.NumberFormat("en-IE", {
    style: "currency", currency: D.meta.currency,
    minimumFractionDigits: 0, maximumFractionDigits: 0
  });

  var state = {
    sort: "start", dir: 1,
    months: new Set(), ports: new Set(), sites: new Set(), boats: new Set(),
    /* Who sells the sailing. Empty means every seller, like every other chip
       bank here -- a filter nobody has touched must never be a filter. */
    sellers: new Set(),
    nightsMin: null, nightsMax: null, hideSoldOut: false,
    /* Only sailings a seller has marked down. Off by default, like every
       other filter here: a page that opened showing 268 of 1,122 rows would
       be answering a question nobody asked. */
    onSaleOnly: false,
    toggles: {}, open: null,
    /* Rows the visitor has marked to keep their place while scrolling
       sideways. Keyed on the departure id rather than the row index, so a
       mark survives sorting, filtering and the table being redrawn under it --
       an index would follow the position and mark whatever moved into it. */
    marked: new Set()
  };
  D.facets.toggles.forEach(function (t) { state.toggles[t.id] = t.default; });

  /* ---------- pricing ---------- */

  /* Whether a line counts at all, independent of what it costs. A line with no
     stated price still counts — it just cannot be added up.

     The toggle is asked before the tier. Nitrox and gear are filed under the
     site's Optional Extras, so testing the tier first returned false before
     the toggle was ever read and both switches on the page added nothing to
     any total. Mirrors pricing._is_counted — keep the two in step. */
  function lineCounts(line) {
    if (line.included) return false;
    if (line.toggle) return !!state.toggles[line.toggle];
    if (line.tier === "optional") return false;
    return !!DEFAULT_ON_TIERS[line.tier];
  }

  /* Every row of one departure's cost table: its own fare, then the fee lines.

     The fees hang off the itinerary because that is what they are a property
     of -- the vessel's disclosure, which does not change with the month. They
     used to be written onto each departure, which stored 314 distinct answers
     878 times and made the page 5.6 MB. A departure carries its own copy only
     when it genuinely prices a fee differently; Python decides that and writes
     dep.lines only in that case. */
  function linesFor(dep) {
    var itin = D.itineraries[dep.itinerary_id];
    return [dep.base_line].concat(dep.lines || itin.lines);
  }

  function metricsFor(dep) {
    return metricsOf(linesFor(dep), dep.base);
  }

  /* The same trip as the other seller bills it, or null.
   *
     Two things have to be true and they fail independently. PADI must sell
     that date -- 601 of 892 -- and its fee book for the trip must be complete,
     every charge it names classified and priced. 169 rows clear both. Where
     only the first holds there is a second price and no second total, and the
     page says exactly that rather than comparing a bill against half of one.

     Deliberately the same `metricsOf` as our own side. Two adders would drift,
     and the one thing this column must never do is show a difference that is
     an artefact of how the two were summed. */
  function padiMetricsFor(dep) {
    var itin = D.itineraries[dep.itinerary_id];
    if (!dep.padi_base_line || !itin.padi_lines) return null;
    return metricsOf([dep.padi_base_line].concat(itin.padi_lines), dep.padi);
  }

  function metricsOf(lines, base) {
    var low = 0, high = 0, unpriced = [], required = 0;
    var nitrox = null, tips = null;
    lines.forEach(function (line) {
      if (line.code === "nitrox") {
        nitrox = line.included ? { included: true }
               : line.has_price ? { price: line.display.amount }
               : { listed: true };
      }
      /* Tips are customary rather than optional, so a stated amount is in the
         total. An operator who lists them without a figure leaves a real cost
         outside the arithmetic, and the total has to say so. */
      if (line.code === "gratuities") {
        tips = line.included ? "included" : line.has_price ? "counted" : "unpriced";
      }
      if (lineCounts(line)) {
        if (line.has_price) {
          low += line.display ? line.display.amount : 0;
          high += line.display_max ? line.display_max.amount
                                   : (line.display ? line.display.amount : 0);
        } else {
          unpriced.push(line.label);
        }
      }
      if (line.tier === "mandatory" && line.has_price && line.display) {
        required += line.display.amount;
      }
    });
    return {
      /* The berth this bill starts from, carried so a row can print one
         seller's whole bill rather than one seller's total beside another
         seller's advertised price. Advertised plus Mandatory fees is the
         Total on every row, and that has to keep holding when the Total is
         the second seller's. */
      base: base,
      total: low, totalMax: high, isRange: high > low + 0.5,
      unpriced: unpriced, required: required, nitrox: nitrox, tips: tips,
      later: low - base
    };
  }

  /* What this sailing costs, across everyone selling it.
   *
     `.m` is the bill from the seller whose fee book this site was built on;
     `.p` is the second seller's, present only where its own disclosure is
     complete.

     Where both exist the page prints the **span**, not one of them. Picking the
     lower was the obvious thing and it was wrong in a way that took a fleet
     owner to see: the two sellers do not disclose at the same resolution.
     liveaboard.com publishes one fee figure per vessel; PADI publishes one per
     itinerary, and its numbers move with the trip -- Tala's northern week is
     €100 against its deep-south week at €280, where ours is €200 for both.
     Taking the cheaper therefore takes our flat figure exactly where it
     understates and theirs where ours overstates, and pulls the published
     number toward the low side at both ends. On a site whose argument is that
     advertised prices are too low, that is the house error.

     So neither is "the" price. The columns print both sellers' figures, and
     **each end is one seller's whole bill** -- the cheaper seller's Advertised
     and Mandatory fees make the low end, the dearer seller's make the high
     end. Not the minimum of each part: our berth is sometimes the cheaper
     while their fee book is, and min(base) + min(fees) is then a bill neither
     seller quoted. Measured before it was believed -- taking the parts
     independently broke the sum on 74 of the 108 rows where both bills add
     up. That ordering is why Advertised and Mandatory fees print through
     `sellerPair` and not `sellerSpan`: they follow the Total's seller order
     rather than running low to high, and on 27 rows that order is backwards.

     `cheapest` still says who is lower, for the Sellers column. Under
     `PADI_SAME` they are one price and the span collapses. */
  function best(row) {
    var ours = row.d.mandatory_known ? row.m : null;
    var theirs = row.p;
    if (!ours && !theirs) return null;
    if (!ours || !theirs) {
      var only = ours || theirs;
      return {
        m: only, cheapest: ours ? "ours" : "padi", varies: 0, both: false,
        lo: only.total, hi: only.totalMax,
        baseLo: only.base, baseHi: only.base,
        /* The berth is one figure and the ranges sit on the fees, so a ranged
           total is a ranged fee bill. Printing the midpoint here beside a
           ranged Total would put the difference nowhere. */
        laterLo: only.later, laterHi: only.totalMax - only.base
      };
    }
    var gap = row.m.total - row.p.total;
    var same = Math.abs(gap) < PADI_SAME;
    var cheap = gap <= 0 ? row.m : row.p;
    var dear = gap <= 0 ? row.p : row.m;
    var top = cheap.totalMax > dear.totalMax ? cheap : dear;
    return {
      /* The bill the expanded row leads with, and the one the proportion bar
         is drawn from: the cheaper, because a bar and a fee table have to come
         from one coherent bill even when the headline is a span. */
      m: cheap,
      cheapest: same ? "same" : gap < 0 ? "ours" : "padi",
      varies: Math.abs(gap),
      both: true,
      /* One seller per end. The high end is `top`'s ceiling rather than the
         dearer seller's midpoint, because an operator's own quoted range can
         reach past the other seller entirely and hiding it behind a seller
         comparison would be this site's own suppressed cost. `top` is a whole
         bill either way, so Advertised + Mandatory fees still equals Total at
         both ends -- the ranges sit on the fee lines, never on the berth, so
         the ceiling is carried by Mandatory fees. */
      lo: cheap.total,
      hi: top.totalMax,
      baseLo: cheap.base, baseHi: top.base,
      laterLo: cheap.later, laterHi: top.totalMax - top.base
    };
  }

  /* "€1,757" when the two agree, "€1,757–2,057" when they do not.
     Rounded before comparing, so a pair that prints identically never prints
     as a range: "€1,757–1,757" would read as a spread that is not there.

     For the Total only, where the low end is always the low end. */
  function sellerSpan(lo, hi) {
    var a = Math.round(lo), b = Math.round(hi);
    if (a === b) return "€" + a.toLocaleString("en-IE");
    return "€" + a.toLocaleString("en-IE") + "–" + b.toLocaleString("en-IE");
  }

  /* The same two sellers, for a *part* of the bill: the low-total seller's
     figure then the high-total seller's, in that order and never sorted.

     The order is load-bearing -- Advertised + Mandatory fees has to equal
     Total at each end, so sorting either column would break the row's
     arithmetic. But the parts do not have to run the same way as the whole,
     and on 27 of the 108 rows they do not: the seller with the cheaper total
     advertises the *dearer* berth, which is this site's entire argument
     happening inside one row. Printed with an en dash that reads as
     "€2,152–2,150", a broken range. An arrow reads as an order rather than a
     span, so a pair that runs backwards is legible instead of wrong. */
  function sellerPair(lo, hi) {
    var a = Math.round(lo), b = Math.round(hi);
    if (a === b) return "€" + a.toLocaleString("en-IE");
    return "€" + a.toLocaleString("en-IE") + " \u2192 " + b.toLocaleString("en-IE");
  }

  /* ---------- what is marked down ---------- */

  /* A sale is one seller's list price beside the price it charges — read off
     the struck-through figure on each cabin and off PADI's compareAtPrice,
     both of which the sellers publish themselves. Never off the "20% Off:" an
     operator writes into a trip name: that is a claim about a number, and this
     is the number. They agree on all 241 sailings carrying such a banner, and
     22 more are discounted without one.

     `pct` describes *this row's* advertised price, so it is absent whenever
     the seller who set that price is not the one discounting. Such a row is
     still on sale and still filters as one — it just gets no percentage,
     because the alternative is printing another seller's markdown against a
     fare nobody marked down. */
  function saleTag(d) {
    if (!d.sale) return "";
    var who = (d.sale.sellers || []).map(function (s) { return SELLER_NAMES[s] || "a seller"; });
    var read = D.meta.berths_read ? ", read " + D.meta.berths_read : "";
    /* No percentage has two causes — the discounting seller is not the one
       whose fare this row prints, or the markdown rounds to nothing — and the
       row does not carry which. So the tooltip states what is true of both
       rather than picking one and being wrong on the other. */
    if (!d.sale.pct) {
      return '<span class="sale-mark" title="' +
        esc("On sale through " + who.join(" and ") +
            ", with no percentage stated against the fare on this row" + read) +
        '">on sale</span>';
    }
    /* "Down from" only where there is a figure to be down from. `was` is
       withheld when the seller stated no currency, and a converted amount
       built from a missing one printed "€NaN" into the tooltip. */
    var title = (d.sale.was ? "Down from " + eur(d.sale.was) + ", per " : "Marked down by ") +
      who.join(" and ") + read;
    return '<span class="sale-mark" title="' + esc(title) + '">−' + d.sale.pct + "%</span>";
  }

  /* "€1,757" when fixed, "€1,757–1,832" when the operator quoted a range.
     Collapsing a range to one number would be the site's own hidden cost. */
  function span(m) {
    var lo = Math.round(m.total).toLocaleString("en-IE");
    if (!m.isRange) return "€" + lo;
    return "€" + lo + "–" + Math.round(m.totalMax).toLocaleString("en-IE");
  }
  function eur(n) { return euro.format(n); }

  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  /* "2027-05-01" printed as "01 May". Formatted from the string rather than
     through Date, which would read the ISO date as UTC midnight and print the
     day before for anyone west of Greenwich. */
  function shortDate(iso) {
    var p = String(iso).split("-");
    if (p.length !== 3) return esc(iso);
    return p[2] + " " + MONTHS[+p[1] - 1];
  }

  /* The dearest true cost among the rows on screen, which is what the anchor
     bars are drawn against. Recomputed on every draw so the bars always
     compare the trips being looked at, not a fleet maximum that filtering has
     already excluded. */
  var barMax = 0;

  /* The bar's full length, in pixels, for the dearest trip on screen. Fixed so
     the bar is measured against the column it is printed in. */
  var BAR_TRACK = 68;

  /* Where the total is a range, the bar is drawn from its low end -- the same
     figure Mandatory fees is worked out from. Said out loud, because a graphic
     that answers a narrower question than the number above it should not do so
     silently. */
  var BAR_TITLE = "Advertised, then the fees on top of it. Scaled against the " +
    "dearest trip shown; drawn from the low end where the total is a range.";
  function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  /* ---------- the cabin ladder ---------- */

  /* What a berth actually costs, rung by rung, and how many are left on each.
     The advertised price is the bottom of this ladder rather than a figure of
     its own: on all 864 sailings read, the two agree.

     Positional in the payload -- a block is written once per departure and a
     rung 2,982 times, so named keys there cost more than the numbers they
     label. Named here instead, so nothing in this file counts array offsets.

     Every price arrives already converted: normalisation is Python's job, and
     the only arithmetic below is picking a minimum out of numbers it settled. */
  var CABIN_NAMES = D.cabin_names || [];
  /* SELLER_NAMES, not SELLERS. The Sold by chips further down declare their
     own `var SELLERS` in this same function scope — an array of {id,label,n}
     objects — and `var` has no block scope, so that second declaration wins
     for the whole file including the lines above it. The ladder's own seller
     heading was already reading it and would have printed "[object Object]"
     the day a second seller filled a block (#92); it is guarded by
     `blocks.length > 1`, which is why nothing has shown it yet. Two different
     lists cannot share one name. */
  var SELLER_NAMES = D.sellers || [];
  var BLOCK_SELLER = 0, BLOCK_SPOTS = 1, BLOCK_CABINS = 2, BLOCK_ABOARD = 3;
  var RUNG_NAME = 0, RUNG_PRICE = 1, RUNG_LEFT = 2, RUNG_SUPP = 3;

  /* Berths left at the advertised price, across every room selling at it.
     `null` is "nobody stated a count", which is not the same as none left --
     a sailing nobody could read has no answer, and 0 is an answer.

     Reads slot 1 and only slot 1. PADI Travel publishes a count too and it is
     a different quantity -- every berth aboard rather than every berth at this
     price -- so it lives in its own slot and is read by `aboardLeft`. Letting
     it answer here would relabel "22 aboard" as "22 at this price" on the 249
     rows that have no ladder to contradict it. */
  function spotsLeft(d) {
    var blocks = d.berths || [];
    for (var n = 0; n < blocks.length; n++) {
      if (blocks[n][BLOCK_SPOTS] != null) return blocks[n][BLOCK_SPOTS];
    }
    return null;
  }

  /* Berths left on the sailing at any price, and who says so. The weaker
     claim, and on 249 rows the only one anybody makes. */
  function aboardLeft(d) {
    var blocks = d.berths || [];
    for (var n = 0; n < blocks.length; n++) {
      if (blocks[n][BLOCK_ABOARD] != null) {
        return { n: blocks[n][BLOCK_ABOARD], who: SELLER_NAMES[blocks[n][BLOCK_SELLER]] || "" };
      }
    }
    return null;
  }

  /* The day a seller's counts were read. Two crawls, two days, and the date is
     the whole of what makes a count a claim rather than a fact. */
  function readOn(block) {
    return block[BLOCK_SELLER] === 1 ? D.meta.padi_berths_read : D.meta.berths_read;
  }

  /* The cheapest rung anyone can still book. A minimum over prices Python
     already converted -- not a second opinion about what a trip costs. */
  function cheapestOnSale(block) {
    var open = (block[BLOCK_CABINS] || []).filter(function (c) { return c[RUNG_LEFT]; });
    if (!open.length) return null;
    return Math.min.apply(null, open.map(function (c) { return c[RUNG_PRICE]; }));
  }

  /* ---------- derived facets ---------- */

  /* Trip titles end with their ports — "North & Tiran (Hurghada - Hurghada)" —
     which From and To already say. Python cuts the suffix, next to the alias
     table that decides what is a port; this used to compare the bracket text
     against port_from here, which only worked while the two were spelled the
     same and broke as soon as an alias folded them apart. */
  function tripName(itin) {
    return itin.title || itin.name;
  }

  function tally(pick) {
    var n = {};
    D.departures.forEach(function (dep) {
      pick(D.itineraries[dep.itinerary_id]).forEach(function (v) {
        if (v) n[v] = (n[v] || 0) + 1;
      });
    });
    return Object.keys(n).sort(function (a, b) {
      return n[b] - n[a] || a.localeCompare(b);
    }).map(function (v) { return { id: v, n: n[v] }; });
  }

  /* Which airport you fly into is decided by the departure port, so this
     filters on From alone: a one-way run returning elsewhere still leaves
     from the port you picked. */
  var PORTS = tally(function (i) { return [i.port_from]; });

  /* A route label is our classification of a trip; a dive site is the thing a
     diver is choosing between, and it is what the operator names in the title. */
  var SITES = tally(function (i) { return i.dive_sites || []; });

  /* The vessel, which is what a diver is actually choosing.
     Every boat here sells several trips across the season, so this is the
     filter that answers "show me everything this boat runs" -- the question
     the operator bank could not answer, because a company with six boats
     returned six boats' worth of rows and no way to tell them apart.

     The operator is not lost -- it stays on every itinerary in the dataset and
     in the expanded row -- but it is no longer *filterable*. It was reachable
     through the search box, and that box has gone: a second way to ask what
     the chips ask, redrawing the table on every keystroke, and the only
     question it answered alone was the company. 42 buttons above the prices
     is the wrong weight for a question asked far less often than "which
     boat", and so, it turns out, is a permanent input. */
  var BOATS = tally(function (i) { return [i.boat]; });

  /* Which sites sell this sailing. Three states and they are three different
     facts, so they are three chips rather than one "PADI" switch:

       both              both sites list the date. The money columns print a
                         span across the two, and this is where a reader who
                         wants only the comparable rows finds them.
       liveaboard only   liveaboard.com lists it and PADI does not. Its
                         calendar runs to a different depth on every boat, so
                         this is a fact about who was asked and not about the
                         trip.
       PADI only         PADI is the only seller this site has a price from.
                         On most of those rows liveaboard.com does not list
                         the date, and on 22 boats does not sell berths at
                         all -- but on 87 it was simply never asked, because
                         the barren list held its vessel back for the week.
                         The chip is a fact about who was asked, which is why
                         it can hold all three; the *sentence* in the expanded
                         row is the one that must not overstate, and it does
                         not.

     Both sellers are named, and named the way the Seller column names them.
     The middle chip read "Here only", which asks the reader to know which of
     the two sites "here" is -- and this page is neither of them: it is a
     third thing that reads both. A filter that says who sells a berth must
     say who, and the row it filters to links "liveaboard ↗" in the Seller
     column, so the chip says liveaboard too. One name per seller, in every
     place the page prints one.

     Read off `padi_only` and `padi` rather than recomputed, because those are
     the same two keys the Seller column branches on and the row's own bill is
     built from. A second derivation here would be a second answer to "who
     sells this", and the two would drift. */
  function sellerOf(dep) {
    return dep.padi_only ? "padi" : dep.padi != null ? "both" : "liveaboard";
  }

  var SELLER_LABELS = {
    both: "Both", liveaboard: "liveaboard only", padi: "PADI only"
  };
  var SELLERS = ["both", "liveaboard", "padi"].map(function (id) {
    return {
      id: id, label: SELLER_LABELS[id],
      n: D.departures.filter(function (d) { return sellerOf(d) === id; }).length
    };
  }).filter(function (it) { return it.n; });

  /* ---------- columns ---------- */

  function disclosure(dep) {
    if (!dep.fees_known) return ["none", "not looked at"];
    if (!dep.mandatory_known) return ["partial", "optional only"];
    return ["full", "required stated"];
  }

  var COLS = [
    /* Sorted on the ISO string and printed short. Every departure here is in
       one season, so the year is the same four characters on 882 rows and
       repeating it crowds out the day and month, which is the part being
       compared. The heading still names the season. */
    { k: "start", t: "Depart", v: function (d) { return d.start; },
      show: function (d) { return shortDate(d.start); } },
    { k: "end", t: "Return", v: function (d) { return d.end; },
      show: function (d) { return shortDate(d.end); } },
    { k: "boat", t: "Boat", cls: "boat", v: function (d, i) { return i.boat; } },
    /* Berth price is per person, so this says whether you are buying into a
       boat of twelve or of thirty-four. Null where the description does not
       state it — about half the fleet, which is a gap in the scrape rather
       than an operator declining to say. */
    { k: "guests", t: "Guests", short: "Pax", cls: "guests", num: true,
      v: function (d, i) { return i.guests == null ? -1 : i.guests; },
      show: function (d, i) {
        return i.guests == null ? '<span class="dim">—</span>' : i.guests;
      } },
    { k: "trip", t: "Trip", cls: "trip", v: function (d, i) { return tripName(i); } },
    { k: "from", t: "From", v: function (d, i) { return i.port_from; } },
    { k: "to", t: "To", v: function (d, i) { return i.port_to; } },
    { k: "sites", t: "Dive sites", cls: "sites",
      v: function (d, i) {
        return (i.dive_sites || []).join(", ") || i.region || "—";
      },
      show: function (d, i) {
        if (i.dive_sites && i.dive_sites.length) return esc(i.dive_sites.join(", "));
        /* The operator named no reef. Their own word for the region, marked as
           the weaker statement it is. */
        if (i.region) return '<span class="region">' + esc(i.region) + ", sites not named</span>";
        return '<span class="dim">—</span>';
      } },
    /* The berth price of whichever seller's bill this row is printing. It read
       ours unconditionally, which on a row won by the second seller put two
       sellers' numbers in one arithmetic: Advertised plus Mandatory fees no
       longer made the Total, and a reader checking the sum would find the page
       wrong rather than find two sellers. One row, one bill. */
    { k: "base", t: "Advertised", num: true, cls: "money",
      /* Sorted on the cheaper of the two, which is the smaller of the pair
         rather than its first member: the pair runs in the Total's seller
         order and that order is not the price order on 27 rows. A sort key is
         not a printed figure, so taking the minimum here splices nothing. */
      v: function (d, i, m, row) {
        var b = best(row);
        return b ? Math.min(b.baseLo, b.baseHi) : d.base;
      },
      show: function (d, i, m, row) {
        var b = best(row);
        if (!b) return eur(d.base) + saleTag(d);
        return sellerPair(b.baseLo, b.baseHi) + saleTag(d);
      } },
    /* The cheapest bill anyone quotes for this sailing, not this site's own.
     *
       Two sites sell the same berth on the same boat on the same day and they
       do not agree -- 43 of the 74 trips where both fee books can be added up
       differ, and 16 of those by more than €150. Printing one seller's number
       as "Total" was answering "what does liveaboard.com charge" on a page
       whose question is "what does this trip cost". Where the second seller's
       disclosure is complete and cheaper, its bill is the one printed, marked
       so nobody mistakes which. */
    { k: "total", t: "Total", num: true, cls: "cost",
      /* Sorted on the low end, so "cheapest first" still means what it says. */
      v: function (d, i, m, row) { var b = best(row); return b ? b.lo : Infinity; },
      show: function (d, i, m, row) {
        var b = best(row);
        if (!b) return '<span class="dim">—</span>';
        m = b.m;
        /* The two numbers already in this row, drawn to scale: how much of the
           bill was advertised, and how much of it was not. Scaled against the
           dearest trip currently in view, so bar length compares down the
           column and the split compares within one row.

           Only where the required extras are stated. A bar is a claim about
           proportion, and a trip nobody has read the fees for has no
           proportion to claim -- it gets no bar rather than a full one. */
        var bar = "";
        if (barMax > 0 && m.total > 0) {
          var advertised = Math.max(0, Math.min(100, (d.base / m.total) * 100));
          /* Sized in pixels against a fixed track rather than as a percentage
             of the cell. A percentage width on an absolutely positioned box
             resolves against the containing block's padding box, but this box
             is inset from it by `right`, so a full-length bar came out wider
             than the space it sits in and hung across the one vertical rule in
             the table -- 14 of 50 rows once a filter brought more totals near
             the maximum. A track in px cannot do that, and it also means every
             bar is drawn on the axis it is read against. */
          bar = '<span class="anchor" title="' + BAR_TITLE + '" style="width:' +
            ((m.total / barMax) * BAR_TRACK).toFixed(1) + 'px">' +
            '<i class="was" style="width:' + advertised.toFixed(1) + '%"></i>' +
            '<i class="add" style="width:' + (100 - advertised).toFixed(1) + '%"></i></span>';
        }
        /* The span is the answer, so the marker only has to name its cause.
           It sits on the number rather than in a column of its own because it
           qualifies this number: €1,757–2,057 is not an operator quoting a
           range, it is two sellers who do not agree, and those are different
           facts that would otherwise print identically. */
        var varies = b.both && b.cheapest !== "same"
          ? '<span class="varies" title="Two sellers price this sailing and ' +
            'they differ by €' + Math.round(b.varies).toLocaleString("en-IE") +
            '. Both are shown; the Sellers column says which end is whose. ' +
            'They disclose at different resolutions -- liveaboard.com states ' +
            'one fee figure per boat, PADI Travel one per itinerary -- so ' +
            'neither end is the price.">2 sellers</span>'
          : "";
        return "<b>" + sellerSpan(b.lo, b.hi) + "</b>" +
          (m.tips === "unpriced" ? '<span class="plus"> + tips</span>' : "") +
          varies + bar;
      } },
    /* What divers actually compare on, and the reason price per night is not
       here: two denominators over the same total, and only one of them is the
       thing being bought.

       Shown only where the operator publishes a count. The rest used to carry
       three dives per full day, and checking that against the ten vessels that
       do publish one settled it: they state 15 to 21 for the same seven-night
       week. A third of the figure, and the whole of what this column exists to
       tell apart. An empty cell is what this page already says for a dive site
       nobody named. */
    { k: "perdive", t: "Per dive", num: true,
      /* Divides the total the row prints -- the cheaper seller's -- so the two
         money columns cannot disagree about what a dive costs on one row. */
      v: function (d, i, m, row) {
        var b = best(row);
        return i.dives > 0 && b ? b.m.total / i.dives : -1;
      },
      show: function (d, i, m, row) {
        var b = best(row);
        if (b) m = b.m;
        if (!i.dives) {
          return '<span class="dim" title="This operator does not publish a ' +
                 'dive count. Assuming one would divide the bill by a number ' +
                 'nobody stated.">not stated</span>';
        }
        if (!b) return '<span class="dim">—</span>';
        /* The operator quotes a range and this is the fewest, so the figure is
           a ceiling: a week with less steaming fits more dives in and costs
           less each. Erring this way on purpose — the other direction would
           flatter every trip. */
        return '<b>' + eur(m.total / i.dives) + "</b> " +
               '<span class="dim" title="' + i.dives + '+ dives — the fewest ' +
               'this operator states for the week. Boats that cross further, ' +
               'or spend longer in the parks where night dives are not ' +
               'allowed, fit fewer in.">↓ ' + i.dives + "+</span>";
      } },
    /* Included or extra, said plainly. Two thirds of this fleet bundles nitrox
       and a third bills for it -- 44 vessels against 21 -- and on a page for
       comparing trips that difference has to be readable without opening a row.

       Four answers, of which three occur. "not listed" is two vessels whose
       extras block never mentions nitrox: not free, not priced, unknown. The
       fourth -- a nitrox line that is neither included nor priced, the way
       "Rental Gear" is routinely named with no figure -- currently matches
       nothing. It stays because without it the function returns undefined and
       the cell would print that word rather than an answer, and because the
       gear case proves an operator can list an extra and leave the number
       blank. A branch nothing reaches yet is cheaper than a cell reading
       "undefined" on the day one does. */
    { k: "nitrox", t: "Nitrox", num: true,
      v: function (d, i, m, row) {
        var b = best(row); if (b) m = b.m;
        return !m.nitrox ? 9e9 : m.nitrox.included ? -1
             : m.nitrox.price != null ? m.nitrox.price : 9e8;
      },
      show: function (d, i, m, row) {
        var b = best(row); if (b) m = b.m;
        if (!m.nitrox) return '<span class="dim">not listed</span>';
        if (m.nitrox.included) return '<span class="inc">included</span>';
        if (m.nitrox.price != null) return eur(m.nitrox.price);
        return '<span class="dim">extra, no price</span>';
      } },
    /* Everything on top of the advertised price. Under the page's defaults
       that is the fees a diver cannot refuse -- marine park, port dues, fuel,
       the visa, and crew tips where an operator states a figure -- which is
       what the heading says. Switching on nitrox or rental gear above adds
       those to it too, because they are then part of what you will pay; the
       footer says so, and the Nitrox column beside it shows one of them. */
    /* The column the two sellers actually disagree in. Their berth prices are
       within €5 on 89% of matched sailings; the fee books differ on 43 of the
       74 trips where both add up, and by more than €150 on sixteen. Printing
       one figure here hid the whole finding. */
    { k: "later", t: "Mandatory fees", num: true,
      v: function (d, i, m, row) {
        var b = best(row);
        return b ? Math.min(b.laterLo, b.laterHi) : m.later;
      },
      show: function (d, i, m, row) {
        var b = best(row);
        if (!b) return '<span class="dim">—</span>';
        return '<span class="later">+' +
          sellerPair(b.laterLo, b.laterHi) + "</span>";
      } },
    /* 127 of 886 departures are sold out. Priced alongside bookable ones with
       no way to tell them apart, a cheapest-first sort could put a trip nobody
       can buy at the top of the list. */
    /* How many berths are left at the price on the row, and -- on hover or
       click -- the whole ladder that price is the bottom of.

       This column used to print the operator's adjective: available, few left,
       sold out. The booking page states a number, so the number is what it
       says now. The adjective survives only where there is no number to print.

       Sorted on the count rather than on the old three-way code. A column of
       figures whose header sorted them into "limited, available, sold out"
       would be a trap laid for the first person to click it. Unknown sorts
       last: nobody looked is not the same as none left, and it must not
       collide with zero. */
    { k: "availability", t: "Places", cls: "places",
      v: function (d) {
        var spots = spotsLeft(d);
        if (spots != null) return spots;
        /* The whole-sailing count where there is no per-price one. Sorting the
           two together is sorting on "how many can I still get", which is the
           question the column is for; the cell says which of the two it is. */
        var aboard = aboardLeft(d);
        if (aboard) return aboard.n;
        if (d.availability === "sold_out") return 0;
        return Infinity;
      },
      show: function (d) {
        var spots = spotsLeft(d), label, tone;
        if (spots == null) {
          /* No ladder for this sailing. The second seller's whole-sailing
             count is the next best answer and is printed under its own word:
             "aboard", never "places", because it is berths left at any price
             and the column's usual figure is berths left at this one. Same
             number of characters, different claim, and the label is the only
             thing that can carry the difference. */
          var aboard = aboardLeft(d);
          if (aboard) {
            tone = aboard.n === 0 ? "none" : aboard.n <= 3 ? "low" : "many";
            return '<button class="berths" type="button" data-berths="' + esc(d.id) +
              '" aria-expanded="false" aria-haspopup="dialog" title="' +
              esc("Berths left on this sailing at any price, per " + aboard.who) + '">' +
              '<span class="n ' + tone + '">' + aboard.n + "</span>" +
              '<span class="lbl">aboard</span>' +
              '<span class="caret" aria-hidden="true">▾</span></button>';
          }
          /* Nobody counted. The operator's own word is all there is, and it is
             printed as the weaker statement it is. */
          if (d.availability === "sold_out") return '<span class="pill gone">sold out</span>';
          if (d.availability === "limited") return '<span class="pill few">few left</span>';
          if (d.availability === "available") return '<span class="pill open">available</span>';
          return '<span class="dim">—</span>';
        }
        tone = spots === 0 ? "none" : spots <= 3 ? "low" : "many";
        label = spots === 0 ? "at this price" : spots === 1 ? "place" : "places";
        return '<button class="berths" type="button" data-berths="' + esc(d.id) +
          '" aria-expanded="false" aria-haspopup="dialog">' +
          '<span class="n ' + tone + '">' + spots + "</span>" +
          '<span class="lbl">' + label + "</span>" +
          '<span class="caret" aria-hidden="true">▾</span></button>';
      } },
    /* Who else sells this sailing, and whether they agree about the price.
     *
       This replaced a column that measured PADI's berth price against ours and
       called the gap "vs PADI". It was comparing the wrong halves. Both
       sellers publish a fee book -- PADI's is on its itinerary endpoint rather
       than beside its price, which is why it was written off as absent -- and
       the books disagree far more than the berths do: 43 of the 74 comparable
       trips, 16 of them by over €150, against a berth gap that is under five
       euro on 89% of the rows the old column could measure. So the column was
       showing the half the sellers agree about and hiding the half they do
       not, on a site whose entire argument is that the extras are where the
       money is.

       Five states, and they are five different facts:

         both, differ  who holds the low end of the span, and by how much.
                       What the column is for.
         both, same    one price sold twice.
         berth only    PADI sells the date and does not disclose a full bill,
                       so there is a second price and no second total -- 432
                       rows. Printing a berth gap here would be the old
                       column's mistake with a new heading.
         PADI only     PADI is the only seller listing this sailing -- 53 rows,
                       on 14 boats the page already carried. Read as a dash it
                       would say the opposite of the truth: not "PADI does not
                       sell this date" but "PADI is why this row is here".
                       There is one bill, and it is PADI's berth against the
                       vessel's own fee book, so there is nothing to compare
                       and that is the whole statement.
         nothing       PADI does not sell that date. Evidence of nothing: its
                       calendar runs to a different depth on every boat.

       Sorted by the size of the disagreement so the widest come to the top,
       which is the one thing this column is for. */
    { k: "disclosure", t: "Disclosure", v: function (d) { return disclosure(d)[1]; },
      show: function (d) {
        var s = disclosure(d);
        return '<span class="pill ' + s[0] + '">' + s[1] + "</span>";
      } },
    /* The listing for *this departure*, not for its boat.
     *
     * The itinerary's `source_url` is the vessel page at whichever month the
     * crawl happened to be reading when it found the boat -- 310 of 315 point
     * at August. Every row used it, so the link under a May departure opened
     * the August listing: 881 of 881 departures pointed at the wrong month,
     * and a visitor checking a May price landed on a page showing a different
     * number.
     *
     * The departure carries its own url, with its own month, because that is
     * what the source published beside that sailing's price. That is the one
     * to follow: somebody clicking through from a row is checking that row.
     * The vessel page stays as the fallback for a departure that has none. */
    /* Both sellers, where both sell the sailing.
     *
     * The money columns already price this row against whichever of them is
     * cheaper, so a reader who wants to check the figure needs the page it
     * came from -- and on 601 rows that is two pages, not one. Named rather
     * than numbered: "listing" alone was fine while there was one, and would
     * now be the more important half of an ambiguity.
     *
     * PADI's link is per boat and printed only where PADI prices *this date*.
     * A vessel having a PADI page says nothing about whether a given sailing
     * is on its calendar, and a link landing on a calendar without the trip on
     * it is worse than no link. */
    { k: "source", t: "Seller",
      v: function (d, i) { return d.booking_url || i.source_url || ""; },
      show: function (d, i) {
        var links = [];
        var url = d.booking_url || i.source_url;
        var padi = d.padi != null ? (D.padi_urls || {})[i.boat_id] : null;
        if (url) {
          /* Named, not "listing", on the rows where the one link is not the
             usual source. 230 sailings are sold only by PADI -- liveaboard.com
             does not list the date, and on 22 boats does not sell berths at
             all -- so their single url points at travel.padi.com. Calling that
             "listing" is the name a reader would give the *other* site, which
             is the one thing this column must not get wrong now that it is
             where both sellers are reached from. */
          links.push('<a href="' + esc(url) + '" target="_blank" rel="noopener">' +
            (d.padi_only ? "PADI" : padi ? "liveaboard" : "listing") + " ↗</a>");
        }
        if (padi) {
          links.push('<a href="' + esc(padi) + '" target="_blank" rel="noopener">' +
            "PADI ↗</a>");
        }
        return links.length ? links.join(" ") : '<span class="dim">—</span>';
      } }
  ];

  /* The order the columns are printed in, which is not the order they are
     defined in above: definitions are grouped by what they mean, and this is
     grouped by what a visitor reads first.

     It exists because True cost sat in column twelve of a table 2522px wide
     inside a 1440px window -- the one number this site is for was off the
     right-hand edge at every screen size anyone actually has, and on a phone
     the whole table was. Identity first, then the money, then everything the
     money is for, then the provenance a visitor only wants once they care.

     Anything missing here is appended rather than dropped, and says so, because
     a column that silently vanished would be a fact the page stopped
     publishing. */
  var ORDER = [
    "start", "end", "boat", "guests",
    "from", "to", "trip", "sites",
    "base", "nitrox", "later", "total", "perdive",
    "availability", "disclosure", "source"
  ];
  /* The same columns on a phone, and as much of the same order as 390px holds.
     Three of the four rules above survive here unchanged: no Operator or
     Nights column, Guests grouped with the Boat, and Dive sites straight after
     Trip. The fourth -- the price block reading Advertised, Nitrox, Mandatory
     fees, Total -- cannot, and the note on the block below says what it costs.

     What does not fit in front of the money, measured at 390px: Return is
     62px, From and To are 120px each, and the pinned pair plus the expander
     already spend 186. Return therefore follows the money rather than leading
     it, as it does nowhere else. Of the two identifiers, the boat's name is
     what a row is compared by; the return date you read once you have found
     the row. Nothing is dropped -- the order is what changes. */
  var PHONE_ORDER = [
    /* Guests sits with the boat here, as it does at every other width: how
       many people share the dive deck is a fact about the vessel, not about
       the route. It is the one descriptive column that fits in front of the
       money -- 45px, against 62 for Return and 120 for From. */
    "start", "boat", "guests",
    /* The one place the bill order gives way, and only in its first term.
       Everywhere else the prices read Advertised, Nitrox, Mandatory fees,
       Total, which is the order a bill is read in and the order the footer
       explains. A phone cannot have it: measured at 390px, the three parts
       ahead of the Total put the Total's left edge at x=1247 on the wide
       order and x=477 even with nothing but the pinned columns before them.
       The number this page exists to publish would be off the edge on the
       device most people open it on.
       So the Total leads and its parts follow it, in the bill's own order --
       Advertised, Nitrox, Mandatory fees -- rather than in a third order
       invented for this width. Scrolling right reads the bill; not scrolling
       still shows the answer. */
    "total", "base", "nitrox", "later", "perdive",
    "end", "from", "to", "trip", "sites",
    "availability", "disclosure", "source"
  ];

  /* The same again on a screen too small to afford Guests in front of the
     money. Guests is 45px and the Total is 155 behind 186px of expander and
     pinned columns, so the Total's right edge lands at 386: fine on a 390px
     phone and 26px past the edge of a 360px one, which is a Galaxy S-series
     and most older Androids. Measured, not assumed -- putting Guests ahead of
     the price took the Total from 186..345 to 231..386.

     So below that width Guests goes back behind the price block, at the head
     of the descriptive columns rather than buried among them. The money is
     the one thing that cannot move: a Total off the right-hand edge is the
     exact failure this whole ordering exists to prevent, and it would be
     silent -- the row still renders, it just does not answer the question. */
  var TINY_ORDER = [
    "start", "boat",
    "total", "base", "nitrox", "later", "perdive",
    "guests", "end", "from", "to", "trip", "sites",
    "availability", "disclosure", "source"
  ];

  /* The same columns, and the same price order within them, wherever there is
     not room for the reading order above. Identity, then the money, then
     everything the money is for.

     The wide order reads as a bill and puts the Total last, which is right on
     paper and expensive on screen: with Guests, From, To, Trip and Dive sites
     ahead of the price block, and the Total at the end of it, the Total needs
     about 1500px of window to be visible at all. It fell off a 1200px and a
     1440px laptop, which is most of them. Below that the price block moves in
     front of the descriptive columns -- Advertised, Nitrox, Mandatory fees,
     Total, in that order still. */
  var COMPACT_ORDER = [
    "start", "end", "boat",
    "base", "nitrox", "later", "total", "perdive",
    "guests", "from", "to", "trip", "sites",
    "availability", "disclosure", "source"
  ];

  /* Two questions, two breakpoints. `compact` is about how much room there is
     before the money column; `narrow` is about how much room there is at all,
     and drives the pinned-column widths and the folded filter banks. */
  var compact = window.matchMedia("(max-width: 1700px)");
  var narrow = window.matchMedia("(max-width: 760px)");
  /* A third question, and the narrowest one: is there room for a descriptive
     column in front of the price at all? 385 is where the measurement falls,
     not a round number chosen first -- Guests ahead of the money puts the
     Total's right edge at 386. */
  var tiny = window.matchMedia("(max-width: 385px)");

  /* How many of the leading columns are pinned. By position, never by name:
     two pinned columns with a third between them overlap exactly as badly as
     two with a wrong offset, and naming them let that happen the moment the
     order changed. Three fit a laptop; Return scrolls there.

     Four on a wide screen, because Guests is a fact about the vessel -- how
     many people you share a dive deck with -- and the pinned group's closing
     rule is what says where the identity columns end. Left at three, that rule
     fell between Boat and Guests and filed the guest count as the first of the
     route columns. It is on the boat's side of it now.

     One on a phone, and the date is the one. Pinning Depart, Boat and Guests
     froze 231px of a 390px screen: three fifths of the display held still
     while the visitor dragged the sixteen columns the page exists to compare
     through the 159px that were left. Every column pinned is a column the
     money has to be read next to, and on a phone there is only room to read
     the money next to one thing. The date is that thing -- it is what a row
     is looked up by, it is the sort the table opens on, and at 66px it is the
     cheapest of the three to hold. The boat and the guest count keep their
     widths and their place in front of the money (see PHONE_ORDER); they
     simply scroll away with everything else once you go looking down the
     bill, and scrolling back is one gesture. */
  function pinned() {
    return narrow.matches ? 1 : compact.matches ? 3 : 4;
  }

  function orderColumns() {
    /* The rule that closes the pinned group goes on whichever column is last
       in it, and that changes with the breakpoint. */
    var n = pinned();
    document.body.classList.toggle("pins-1", n === 1);
    document.body.classList.toggle("pins-2", n === 2);
    document.body.classList.toggle("pins-3", n === 3);
    document.body.classList.toggle("pins-4", n === 4);
    var order = tiny.matches ? TINY_ORDER
              : narrow.matches ? PHONE_ORDER
              : compact.matches ? COMPACT_ORDER : ORDER;
    COLS.sort(function (a, b) {
      var x = order.indexOf(a.k), y = order.indexOf(b.k);
      return (x < 0 ? order.length : x) - (y < 0 ? order.length : y);
    });
  }
  /* Appended rather than dropped, and said out loud: a column missing from one
     of these lists is a fact the page stops publishing at that width only,
     which is the hardest kind of gap to notice -- the laptop everyone develops
     on would look right. So every list is checked, not just the widest. */
  COLS.forEach(function (c) {
    [["ORDER", ORDER], ["COMPACT_ORDER", COMPACT_ORDER],
     ["PHONE_ORDER", PHONE_ORDER], ["TINY_ORDER", TINY_ORDER]
    ].forEach(function (pair) {
      if (pair[1].indexOf(c.k) < 0 && window.console) {
        console.warn("column " + c.k + " is not in " + pair[0] + "; printed last");
      }
    });
  });
  orderColumns();

  /* ---------- filtering and sorting ---------- */

  /* One predicate, so the table and the filter counts can never disagree
     about what a filter means. `skip` names a facet to ignore, which is what
     makes a chip's number the answer to "what if I picked this too?" rather
     than "what did I already pick". */
  function passes(dep, itin, skip) {
    if (skip !== "months" && state.months.size && !state.months.has(dep.month)) return false;
    if (state.hideSoldOut && !dep.bookable) return false;
    if (state.onSaleOnly && !dep.sale) return false;
    if (state.nightsMin !== null && dep.nights < state.nightsMin) return false;
    if (state.nightsMax !== null && dep.nights > state.nightsMax) return false;
    if (skip !== "ports" && state.ports.size && !state.ports.has(itin.port_from)) return false;
    if (skip !== "boats" && state.boats.size && !state.boats.has(itin.boat)) return false;
    if (state.sites.size) {
      /* All, not any: picking Brothers and Daedalus means a week that visits
         both. It was "either" once, on the reasoning that people shop rather
         than tick boxes -- but "either" is what one chip already does, so a
         second chip that only ever widens the result cannot narrow a search
         down to the trip you want.
         This is the one filter where it is a real question. A departure has
         one month, one departure port and one boat, so requiring two of any
         of those returns nothing; a trip visits several reefs. */
      var sites = itin.dive_sites || [], all = true;
      state.sites.forEach(function (s) { if (sites.indexOf(s) < 0) all = false; });
      if (!all) return false;
    }
    /* Any-of, like months and ports and unlike dive sites: a sailing has
       exactly one answer here, so requiring two would return nothing. */
    if (skip !== "sellers" && state.sellers.size && !state.sellers.has(sellerOf(dep))) {
      return false;
    }
    return true;
  }

  function visible() {
    var out = [];
    D.departures.forEach(function (dep) {
      var itin = D.itineraries[dep.itinerary_id];
      if (passes(dep, itin, null)) {
        out.push({ d: dep, i: itin, m: metricsFor(dep), p: padiMetricsFor(dep) });
      }
    });

    var col = COLS.filter(function (c) { return c.k === state.sort; })[0] || COLS[0];
    out.sort(function (a, b) {
      var x = col.v(a.d, a.i, a.m, a), y = col.v(b.d, b.i, b.m, b);
      if (typeof x === "number" && typeof y === "number") return (x - y) * state.dir;
      return String(x).localeCompare(String(y)) * state.dir;
    });
    return out;
  }

  /* ---------- rendering ---------- */

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  /* The entry bar this trip states, in words rather than a code.
   *
   * It had a column of its own and lost it: sixteen columns already competed
   * for the width, the bar is the same three words on most rows, and its
   * "sources disagree" marker was styled like the disclosure pill beside it,
   * so two unrelated warnings looked identical in adjacent columns. All three
   * were arguments against the column and none was an argument against the
   * fact -- but removing the column removed the fact, because nothing else on
   * the page printed it. `level_labels` and every itinerary's `requirements`
   * went on shipping in the payload with no code reading either: 892
   * departures carrying a stated safety requirement that a visitor could only
   * reach by downloading the JSON.
   *
   * So it lives here instead, under the bill, where there is room for the part
   * that actually needs saying: liveaboard.com and PADI Travel disagree about
   * the bar on 49 of these trips, the stricter one is what is shown, and a
   * diver deserves to know the two sources do not agree rather than to see one
   * number presented as settled.
   *
   * The dim line under it is the sources' own wording, printed for the same
   * reason the fee rows print the operator's: a normalised label is this
   * project's phrasing, and the sentence it was normalised from is the
   * evidence for it. */
  function entryBar(itin) {
    var req = itin.requirements;
    if (!req || !req.min_level) return "";
    var level = (D.level_labels && D.level_labels[req.min_level]) || req.min_level;
    /* The dive count beside the certification, unless the certification's own
       label already states it: two of the four levels are written "Advanced +
       50 dives", and appending the number again printed "Advanced + 50 dives,
       50 logged dives" on the single commonest bar in the fleet. Matched on a
       word boundary rather than as a substring, so a ten-dive bar is not
       swallowed by a label that happens to read "+ 100 dives". */
    var stated = req.min_logged_dives &&
      new RegExp("\\b" + req.min_logged_dives + "\\b").test(level);
    var dives = req.min_logged_dives && !stated
      ? ", " + req.min_logged_dives + " logged dives" : "";
    /* The note, on the trips where it says more than the line above it.
     *
       Every trip has one and 222 of the 317 are the winning source restating
       the bar in its own words -- true, attributable, and on its own a dim
       line repeating the black one directly above. What the note is *for* is
       the other 95: the ones where a second source was read, so the reader can
       see that the number shown was chosen between two claims rather than
       simply reported. Where a source is named the whole note prints, first
       sentence included, because there the restatement is no longer a
       repetition -- it is which of the two bars was taken. */
    var note = req.notes && /PADI|liveaboard\.com/.test(req.notes) ? req.notes : "";
    return '<p class="entry"><b>Entry bar</b> <span>' + esc(level + dives) +
      "</span>" +
      (note ? '<span class="dim">' + esc(note) + "</span>" : "") +
      "</p>";
  }

  /* One seller's bill, as rows. Factored out because there are two of them
     now, and they must be built by the same code for the same reason both
     totals are added by the same code: the whole claim the panel makes is that
     these are two disclosures of one trip, and a reader comparing them must be
     comparing what each seller said rather than how each table was made. */
  function feeRows(lines) {
    return lines.map(function (line) {
      var on = lineCounts(line) || line.tier === "base";
      var amount = line.has_price && line.display
        ? "€" + Math.round(line.display.amount).toLocaleString("en-IE") +
          (line.is_range && line.display_max
            ? "–" + Math.round(line.display_max.amount).toLocaleString("en-IE") : "")
        : "unstated";
      /* A converted number is a weaker claim than a quoted one, and the page
         says which is which rather than presenting both as the same fact. */
      var prov = [];
      if (line.converted && line.fx && line.quoted) {
        prov.push("converted from " + line.quoted.amount + " " + line.quoted.currency +
                  " at " + line.fx.rate +
                  (D.meta.fx && D.meta.fx.sourced === false
                    ? " (approximate rate, not a sourced one)"
                    : " (" + line.fx.as_of + ")"));
      }
      if (line.note) prov.push(line.note);
      return '<tr class="' + (on ? "" : "off") + '"><td>' + (on ? "▪" : "▫") +
        "</td><td>" + esc(line.label) + '</td><td class="num">' + amount +
        "</td><td>" + esc(line.tier) + '</td><td class="prov">' +
        esc(prov.join(" · ")) + "</td></tr>";
    }).join("");
  }

  function feeTable(row) {
    var body = feeRows(linesFor(row.d));

    var caveat = "";
    if (!row.d.fees_known) {
      caveat = "Marine park fees, port dues, fuel, nitrox and gratuities are not " +
        "included above because they have not been captured for this trip yet. " +
        "On comparable trips they add 30–60%.";
    } else if (!row.d.mandatory_known) {
      caveat = "This operator publishes no required extras. Every Egyptian " +
        "liveaboard pays marine park and port fees, so either they are already " +
        "inside the advertised price or they are collected at the dock — the " +
        "listing does not say which, so no total is claimed here.";
    } else if (row.m.unpriced.length) {
      caveat = "Plus " + row.m.unpriced.join(", ") + ": listed by the operator " +
        "with no price, so it cannot be added up here. It is not free.";
    }
    /* The same sailing, as the other seller bills it.
     *
     * Three states, and the one in the middle is the one worth being careful
     * about: a second price with no second total. Saying "PADI charges €1,215"
     * beside a total is an invitation to compare them, and they are not the
     * same kind of number -- which is the mistake the old column made in the
     * table and would be no better here. */
    var padi = "";
    if (row.p) {
      var gap = row.m.total - row.p.total;
      padi = "PADI Travel sells this same sailing and publishes its own "
        + "required extras. Its bill comes to €"
        + Math.round(row.p.total).toLocaleString("en-IE") + " against €"
        + Math.round(row.m.total).toLocaleString("en-IE") + " here"
        + (Math.abs(gap) < PADI_SAME
            ? " — the same price, give or take a few euro."
            : ", a difference of €" +
              Math.round(Math.abs(gap)).toLocaleString("en-IE") + ".")
        + " Both include the same nitrox and rental gear, because those are "
        + "the vessel's charge on board whoever sold the berth; where the two "
        + "sellers differ is the berth price and the fees each one discloses.";
    } else if (row.d.padi != null) {
      padi = "PADI Travel advertises this berth at €"
        + Math.round(row.d.padi).toLocaleString("en-IE")
        + ", and does not publish a complete set of required extras for this "
        + "trip. So there is a second price here and no second total, and the "
        + "two are not comparable: what that figure leaves out is exactly what "
        + "the table above is for.";
    } else if (row.d.padi_only) {
      /* Two sentences, because there are two cases and the difference is
         which source the fee rows came from.

         On a boat both sites sell, the table is a mixture and the reader is
         owed the join: the berth is PADI's, the fees are the vessel's own
         panel on liveaboard.com. That is not a spliced bill -- the marine
         park, the port and the fuel are charged on board by the boat whoever
         sold the berth, which is the same reason both sellers' totals carry
         the same nitrox and gear.

         On a boat only PADI sells there is no such panel and no mixture: one
         seller published the whole bill. Saying "the vessel's own fees" there
         would name a source the row does not have. */
      padi = row.i.padi_sourced_fees
        ? "liveaboard.com does not sell this boat at all, so PADI Travel is "
          + "the only seller and everything above is PADI’s: the berth price "
          + "and the required extras its itinerary publishes. There is no "
          + "second bill to set against it."
        : row.d.not_asked
        /* The third case, and the reason it exists: `data/barren.json` holds
           a vessel back for a week after a crawl found it selling nothing,
           and while it does, nobody reads that boat's pages. Saying
           "liveaboard.com does not list this sailing" about it states a
           result for a page this site did not open — on 87 sailings across
           four boats. Not asked is weaker and it is what the data supports.
           Same rule as `fees_known`: no fee lines means nobody looked, not
           that there are none. */
        ? "liveaboard.com was not asked about this sailing — a recent crawl "
          + "found this boat selling nothing and it is re-checked weekly, so "
          + "its pages were not read this run. PADI Travel is the seller this "
          + "site has a price from; whether the other one lists the date is "
          + "not something this page knows. The fees under the berth price "
          + "are the vessel’s own, which it charges on board whoever sold it."
        : "liveaboard.com does not list this sailing, so PADI Travel is the "
          + "only seller and there is one bill rather than two. The berth "
          + "price above is PADI’s; the fees under it are the vessel’s own, "
          + "which it charges on board whoever sold the berth.";
    }

    /* The bar leads the panel rather than following the bill. Whether a diver
       may board this trip at all is prior to what boarding it costs, and a
       reader who opens a row to check a 50-dive requirement should not have to
       read a fee table first to find it. */
    /* Both bills, each under the name of the seller who published it, and
       never merged. They are two claims about one trip and the reader is here
       to see where they part company -- fold them together and the page would
       be publishing a bill neither site quotes, which is the thing it accuses
       operators of. Ours first because its fee book is the one every row on
       the page is built from; PADI's beneath it, only where its own disclosure
       is complete enough to add up. */
    var second = row.p
      ? '<p class="whose">PADI Travel\u2019s bill for the same trip</p>' +
        '<table class="fees"><tbody>' +
        feeRows([row.d.padi_base_line].concat(row.i.padi_lines)) +
        "</tbody></table>"
      : "";
    return entryBar(row.i) +
      (second ? '<p class="whose">This site\u2019s source, liveaboard.com</p>' : "") +
      '<table class="fees"><tbody>' + body + "</tbody></table>" +
      second +
      (caveat ? '<p class="caveat">' + esc(caveat) + "</p>" : "") +
      (padi ? '<p class="caveat padi">' + esc(padi) + "</p>" : "");
  }

  /* How many rows reach the DOM before the visitor scrolls.
     All 838 used to, which put 25,062 nodes and 14,246 table cells on the
     page and cost 37ms of `innerHTML` parsing on every redraw -- paid again
     on every keystroke in the search box. Nothing else in a redraw is
     measurable beside it: walking all 8,745 fee lines is 1.3ms and building
     the HTML string is 0.2ms.
     Rows are appended as the table is scrolled, so nothing is hidden and no
     count changes -- "838 rows shown" still says 838. Only the moment of
     construction moves. */
  var PAGE_ROWS = 120;
  var drawn = 0;
  var lastRows = [];
  /* Module scope, not inside draw(): appending on scroll builds rows through
     the same renderRows() and has to give them the same pinned classes, or
     rows added below would lose their sticky columns. */
  var pins = pinned();
  function pin(index) { return index < pins ? "stick" + (index + 1) : ""; }

  /* `keep` holds the table where the visitor left it.
     Every redraw used to rebuild the first 120 rows and no more, whatever had
     been drawn before -- so marking a row 700 deep, after scrolling or after
     Ctrl+F had filled the table, redrew 120 rows and the row just clicked
     vanished. The mark survived in state, which made it worse: the page had
     done what was asked and looked like it had not.
     Sorting, marking, expanding a bill and rotating the device all keep what
     is drawn. Changing a filter does not: the list is a different list, the
     visitor is back at the top of it, and starting from 120 again is what
     makes filtering fast. */
  function draw(keep) {
    var rows = visible();
    var target = keep ? Math.max(PAGE_ROWS, drawn) : PAGE_ROWS;
    drawn = 0;
    lastRows = rows;

    /* Before any cell is rendered: the anchor bars scale against the dearest
       trip on screen, so filtering to three boats redraws the bars against
       those three rather than against a fleet maximum that is no longer
       visible. Only priced rows count -- an unpriced one has no total. */
    barMax = rows.reduce(function (top, r) {
      /* Against the total the column prints, which is now the cheaper of the
         two sellers'. Measured against ours, a row whose PADI bill undercut it
         drew a bar longer than the number beside it. */
      var b = best(r);
      return b && b.m.total > top ? b.m.total : top;
    }, 0);

    /* `stick1`..`stickN` on the leading columns, so the CSS offsets line up
       with the order actually being rendered. */
    pins = pinned();

    document.getElementById("head").innerHTML = '<tr><th class="expander"></th>' +
      COLS.map(function (c, n) {
      var dir = c.k === state.sort
        ? '<span class="dir">' + (state.dir > 0 ? "▲" : "▼") + "</span>" : "";
      /* The short label where one is set and the screen is narrow. A pinned
         column is a fixed width, and "GUESTS" wants 65px of the 45 it has
         there -- so the choice is a header reading "GUES…" or a shorter word
         that is whole. The same call the date column already made when it
         took a smaller font rather than print "DEPAR…": a truncated value can
         be read as truncated, a truncated column name cannot. */
      var label = (narrow.matches && c.short) ? c.short : c.t;
      /* The tooltip only where the word was shortened, so it names the column
         rather than repeating it -- or, on a column that needs a sentence
         rather than a name, whatever `hint` says. A heading a visitor cannot
         interpret is worse than no heading: "vs PADI" printed a dash on 291
         rows and read as missing data rather than as a second seller that does
         not sell that date. */
      var full = label !== c.t ? ' title="' + esc(c.t) + '"'
               : c.hint ? ' title="' + esc(c.hint) + '"' : "";
      return '<th tabindex="0" class="' + (c.num ? "num " : "") + pin(n) +
        '" data-k="' + c.k + '"' + full + ">" + label + " " + dir + "</th>";
    }).join("") + "</tr>";

    document.getElementById("body").innerHTML = rows.length
      ? renderRows(rows, 0, target)
      : '<tr><td class="empty" colspan="' + (COLS.length + 1) +
        '">Nothing matches those filters.</td></tr>';
    drawn = Math.min(rows.length, target);
    afterDraw(rows);
  }

  /* Kept as a separate function so appending on scroll and drawing from
     scratch build a row exactly the same way. */
  function renderRows(rows, from, count) {
    return rows.slice(from, from + count).map(function (row, offset) {
          var n = from + offset;
          var tds = COLS.map(function (c, col) {
            var v = c.show ? c.show(row.d, row.i, row.m, row)
                           : esc(c.v(row.d, row.i, row.m, row));
            return '<td class="' + (c.num ? "num " : "") + (c.cls || "") +
              " " + pin(col) + '">' + v + "</td>";
          }).join("");
          var open = state.open === row.d.id;
          /* Banding is written here from the row's own position, not left to
             `:nth-of-type(even)`. That selector counts every `tr` in the
             tbody, and an expanded row injects one -- so opening any row
             inverted the stripes of every row below it. */
          var marked = state.marked.has(row.d.id);
          return '<tr class="row' + (n % 2 ? " alt" : "") +
            (row.d.bookable ? "" : " gone") + (marked ? " marked" : "") +
            '" aria-selected="' + marked + '" data-id="' + esc(row.d.id) + '">' +
            '<td class="expander"><button class="expand" data-n="' + n +
            '" aria-expanded="' + open + '">' + (open ? "−" : "+") + "</button></td>" +
            tds + "</tr>" +
            (open ? '<tr class="detail"><td colspan="' + (COLS.length + 1) + '">' +
              feeTable(row) + "</td></tr>" : "");
        }).join("");
  }

  function afterDraw(rows) {
    /* Every bank re-counted against the filters now in force. Cheap enough to
       do unconditionally: one pass over 838 departures per bank, and the whole
       redraw it sits inside measures 16ms. */
    BANKS.forEach(function (bank) { bank.recount(); });
    document.getElementById("shown").textContent = rows.length.toLocaleString("en-IE");
    var boats = {}, itins = {};
    rows.forEach(function (r) { boats[r.i.boat_id] = 1; itins[r.i.id] = 1; });
    document.getElementById("nboats").textContent = Object.keys(boats).length;
    document.getElementById("nitin").textContent = Object.keys(itins).length;
  }

  /* How many chips a bank shows before the rest go behind "more".
     67 boats, 17 dive sites and 6 ports would all print at once, which
     put 66 buttons above the table: the first row of data began 596px down a
     1440x900 window and 1708px down a phone, where nothing was visible at all
     without scrolling past two screens of filters. A filter you have not
     chosen yet should not outrank the prices you came to read.

     Six on a phone rather than eight, because the cost of a chip is a row of
     screen and a phone's rows are narrower: eight boat chips wrap to four
     lines at 390px where they take two on a laptop, and four banks doing that
     is the same wall of filters this limit exists to prevent. The tail is not
     lost -- "+ n more" still opens it -- and the chips that survive the cut
     are the ones with the most departures behind them, which are the ones a
     filter is most likely to be reached for. `narrow` is the breakpoint the
     rest of the phone layout already turns on, so the banks fold at the same
     width as the columns they sit above. */
  function chipLimit() { return narrow.matches ? 6 : 8; }

  /* What each chip in a bank would give you, under the filters in force now.
     The numbers were counted once at load and never moved, so a bank could
     read "Hurghada 435" while the table showed 18 rows -- a filter offering a
     number that was true for a page you are no longer looking at.

     An OR bank skips its own selection: picking Hurghada must not zero every
     other port, because picking Safaga as well would *add* those rows, and 0
     would say the opposite. The sites bank does not skip itself, because
     there the chips are ANDed: its number is what you would narrow to. Each
     bank's arithmetic therefore matches its own operator. */
  function countsFor(skip, pick) {
    var n = {};
    D.departures.forEach(function (dep) {
      var itin = D.itineraries[dep.itinerary_id];
      if (!passes(dep, itin, skip)) return;
      pick(itin, dep).forEach(function (v) { if (v) n[v] = (n[v] || 0) + 1; });
    });
    return n;
  }

  var BANKS = [];

  function chips(host, items, picked, numeric, skip, pick) {
    var node = document.getElementById(host);
    var expanded = false;
    var counts = null;
    BANKS.push({
      recount: function () {
        counts = skip === false ? null : countsFor(skip, pick);
        paint();
      }
    });

    function paint() {
      /* A chosen filter is always shown, wherever it sits in the list: a
         chip hidden behind "more" while switched on is a filter that appears
         to have been ignored. */
      /* A value no longer reachable is dropped rather than shown as 0: the
         bank is a list of what you can still do. A chosen one always stays,
         even at zero, or turning it off would mean hunting for a chip that
         had vanished. */
      var live = counts === null ? items : items.filter(function (it) {
        var v = numeric ? +it.id : it.id;
        return counts[it.id] || picked.has(v);
      });
      var limit = chipLimit();
      var shown = expanded ? live : live.filter(function (it, n) {
        var v = numeric ? +it.id : it.id;
        return n < limit || picked.has(v);
      });
      var hidden = live.length - shown.length;
      node.innerHTML = shown.map(function (it) {
        var v = numeric ? +it.id : it.id;
        var n = counts === null ? it.n : (counts[it.id] || 0);
        return '<button class="chip" data-v="' +
          esc(it.id).replace(/"/g, "&quot;") + '" aria-pressed="' + picked.has(v) + '">' +
          esc(it.label || it.id) + ' <span class="dim">' + n + "</span></button>";
      }).join("") +
        (hidden > 0 || expanded
          ? '<button class="chip more" data-more="1" aria-expanded="' + expanded + '">' +
            (expanded ? "− fewer" : "+ " + hidden + " more") + "</button>"
          : "");
    }

    node.addEventListener("click", function (event) {
      var button = event.target.closest("button");
      if (!button) return;
      if (button.dataset.more) { expanded = !expanded; paint(); return; }
      var v = numeric ? +button.dataset.v : button.dataset.v;
      if (picked.has(v)) picked.delete(v); else picked.add(v);
      button.setAttribute("aria-pressed", picked.has(v));
      labelFilters();
      draw();
    });

    /* Reset clears the picked set directly, so the bank has to be repainted
       from it rather than left showing chips it is no longer holding. */
    node.repaint = function () { expanded = false; paint(); };
    paint();
  }

  function drawNotice() {
    var host = document.getElementById("dataNotice");
    host.textContent = "";
    if (!D.meta.verified) {
      var seed = el("div", "notice");
      seed.appendChild(el("strong", null, "Seed data — these are not real quotes"));
      seed.appendChild(document.createTextNode(
        D.meta.notes || "Prices are researched placeholders pending a live scrape."));
      host.appendChild(seed);
    }
    var fx = D.meta.fx;
    if (fx && fx.sourced === false) {
      var rate = el("div", "notice");
      rate.appendChild(el("strong", null, "Euro figures use an approximate rate"));
      rate.appendChild(document.createTextNode(
        "Most operators quote in dollars. Those prices are converted at a " +
        "stand-in rate, not one taken from a rate source, so euro totals here " +
        "may differ by a few percent from what your card is charged."));
      host.appendChild(rate);
    } else if (fx && fx.stale) {
      /* Sourced but no longer moving. The build keeps the last good rate when
         a fetch fails, which is right — a real rate from last week beats an
         invented one today — but it must not pass as current. */
      var old = el("div", "notice");
      old.appendChild(el("strong", null, "The exchange rate is out of date"));
      old.appendChild(document.createTextNode(
        "Dollar prices are converted at the rate published on " + fx.as_of +
        ", " + fx.age_days + " days ago. It is a real rate, but currencies have " +
        "moved since, so euro totals may be off."));
      host.appendChild(old);
    }
  }

  /* ---------- what the other seller is discounting ---------- */

  /* PADI Travel's deals listing, read daily and diffed against the last day
     the book holds. Everything here was settled in Python: the join that
     decided which of these boats is one of ours, the conversion into euro, and
     the comparison with yesterday. This draws it.

     Two things it must not do, both of them versions of the same rule. It must
     not present a first reading as "nothing changed" -- there is a flag for
     that, because the two are different claims. And it must not present a
     truncated reading's absences as withdrawals: a listing the fetcher could
     not finish knows nothing about the offers it did not reach, exactly as a
     vessel page that fails to load knows nothing about the month behind it. */
  /* The overview, in one line, and it leads with the number that answers the
     question. Both sellers are counted, and the sailing count wins over the
     offer count: 268 discounted sailings is what a reader can act on, where
     PADI's 13 is thirteen boats' worth of "there is a sale on".

     Both halves now say what moved, and each says it under its own seller's
     name and its own reading date. They are different books read on different
     days — `data/deals.json` from PADI's listing, `data/sales.json` derived
     from the booking pages — and one date over two sellers dates half of them
     wrong. The liveaboard half leads because it is the larger signal: the day
     the Red Sea Aggressors' sale ended, PADI's listing moved three offers and
     the booking pages moved 36 sailings. */
  function dealsSummary(deals, changed) {
    var sale = deals.on_sale, offers = deals.offers || [];
    var n = sale ? sale.sailings : offers.length;
    var line = sale || offers.length
      ? n + (n === 1 ? " discounted sailing" : " discounted sailings")
      : "nothing discounted";
    if (sale) line += " on " + sale.boats.length + " boats";

    var shifted = deals.on_sale_changes;
    if (shifted && !shifted.first_reading) {
      var moved = 0;
      (shifted.moves || []).forEach(function (m) { moved += m.sailings; });
      line += " · " + (moved
        ? moved + " moved on liveaboard.com"
        : "nothing moved on liveaboard.com") + " since " + shortDate(shifted.previous);
    }

    if (!offers.length) return "On sale · " + line;
    if (deals.first_reading) line += " · PADI's listing, first reading";
    else if (changed) line += " · " + changed + " moved on PADI since " + shortDate(deals.previous);
    else if (deals.previous) line += " · nothing moved on PADI since " + shortDate(deals.previous);
    return "On sale · " + line + " · read " + shortDate(deals.read);
  }

  /* "20% off" where PADI states a percentage, and the money either way.

     Its own word, not a percentage worked out from the two prices: a "Free
     night(s)" offer takes nothing off the nightly rate and dividing one price
     by the other would print it as a discount PADI never claimed. Where the
     kind is a percentage the value is that percentage and is printed; where it
     is not, the saving is the whole of what can be said. */
  function offerOff(row) {
    var saved = row.was - row.price;
    if (row.kind === "Discount %" && row.value) {
      return row.value + "% off · " + eur(saved);
    }
    return saved > 0 ? eur(saved) + " off" : (row.kind || "offer");
  }

  function dealRow(row) {
    var tr = el("tr", null);
    tr.appendChild(el("td", "d-when", shortDate(row.start) +
      (row.nights ? " · " + row.nights + "n" : "")));

    var boat = el("td", "d-boat");
    if (row.url) {
      var a = document.createElement("a");
      a.href = row.url;
      a.rel = "noopener";
      a.target = "_blank";
      a.textContent = row.boat_name;
      /* The page the offer was read from, per row. A price whose source
         cannot be opened is a price this site is asking to be taken on
         trust, which is the thing it exists to object to. */
      a.title = "The PADI Travel page this offer was read from";
      boat.appendChild(a);
    } else {
      boat.textContent = row.boat_name;
    }
    tr.appendChild(boat);

    /* Money before the offer's name, because the name is the longest column
       and a phone reads this table left to right through a 390px window: with
       the name third, every price on it sat off the right-hand edge behind a
       horizontal scroll. What it costs is the column somebody came for. */
    tr.appendChild(el("td", "d-now", eur(row.price)));
    /* Struck through only where it is genuinely a different number. A "was"
       equal to the price is PADI stating an offer that takes nothing off this
       figure, and printing it crossed out would invent a saving. */
    tr.appendChild(el("td", "d-was", row.was > row.price ? eur(row.was) : "—"));
    tr.appendChild(el("td", "d-off", offerOff(row)));
    tr.appendChild(el("td", "d-offer", row.title || "—"));

    /* What PADI actually published, beside what this page converted it to.
       Every other price here is converted the same way and says so once in the
       footer; a deal is a number somebody is being asked to act on today, so it
       says so on its own row. A dash where the two currencies already agree:
       repeating "EUR 720" beside "€720" is a column of noise on eight of
       thirteen rows. */
    function quoted(amount) {
      return row.currency + " " + Math.round(amount).toLocaleString("en-IE");
    }
    var native = el("td", "d-native",
      row.currency === D.meta.currency ? "—" : quoted(row.quoted));
    native.title = row.was > row.price
      ? "As PADI quotes it, before conversion: " + quoted(row.quoted) +
        ", against " + quoted(row.quoted_was)
      : "As PADI quotes it, before conversion";
    tr.appendChild(native);
    return tr;
  }

  function dealsTable(rows) {
    var table = el("table", "deals-table");
    var head = document.createElement("thead");
    var hr = el("tr", null);
    ["Sails", "Boat", "Now", "Was", "Saving", "Offer", "As quoted"].forEach(function (h) {
      hr.appendChild(el("th", null, h));
    });
    head.appendChild(hr);
    table.appendChild(head);
    var body = document.createElement("tbody");
    rows.forEach(function (row) { body.appendChild(dealRow(row)); });
    table.appendChild(body);
    return table;
  }

  function dealsChanges(deals) {
    var moved = deals.changes || {};
    var names = moved.names || {};
    var box = el("div", "deals-moved");
    /* Named, now that both sellers have a change log. Two adjacent sections
       headed "What moved since …" over two different books read on two
       different days is exactly the splice this page refuses everywhere else. */
    box.appendChild(el("h4", null, "What moved on padi.com since " +
      shortDate(deals.previous)));

    if (moved.partial) {
      /* The distinction the whole pipeline turns on: a reading nobody could
         finish is not a day on which nothing was on sale. */
      box.appendChild(el("p", "deals-note",
        "One of the two readings did not finish, so new and withdrawn offers " +
        "are not reported for it. An offer missing from a listing that could " +
        "not be read has not been withdrawn — it has not been looked at."));
    }

    var list = el("ul", "deals-changes");
    (moved.new || []).forEach(function (id) {
      list.appendChild(el("li", "d-new", "New — " + (names[id] || id)));
    });
    (moved.withdrawn || []).forEach(function (id) {
      list.appendChild(el("li", "d-gone", "Withdrawn — " + (names[id] || id)));
    });
    /* One clause per thing that actually moved, and none for anything that did
       not. An earlier version always printed a "before → after" for the offer
       name, so a sailing that had merely shifted a week read "15% Early Bird →
       15% Early Bird", which is a change log reporting a change to a field that
       is identical on both sides. */
    (moved.changed || []).forEach(function (change) {
      var before = change.before, after = change.after, said = [];
      if (before.price !== after.price) said.push(eur(before.price) + " → " + eur(after.price));
      if (before.title !== after.title) {
        said.push("“" + (before.title || "no name") + "” → “" + (after.title || "no name") + "”");
      }
      if (before.start !== after.start || before.end !== after.end) {
        said.push("sailing " + shortDate(before.start) + " → " + shortDate(after.start));
      }
      if (!said.length) said.push(change.moved.join(", ") + " changed");
      list.appendChild(el("li", "d-moved", after.boat_name + " — " + said.join("; ")));
    });

    if (!list.childNodes.length) {
      box.appendChild(el("p", "deals-note", "No offer was added, withdrawn or repriced."));
    } else {
      box.appendChild(list);
    }
    return box;
  }

  /* What the first seller is discounting, per boat and across the season.

     A different shape from PADI's list because it is a different claim. PADI
     publishes a deals *listing*: one exemplar sailing per vessel, which says a
     boat is on sale and nothing about when. liveaboard.com publishes no such
     listing at all — its /liveaboard-deals is prose — but strikes out the list
     price on every discounted cabin of every booking page, so the answer here
     is per sailing, and what it supports is a **window**: Red Sea Aggressor II
     is 33% off every week from 1 May to 24 July and full price from 31 July.
     That is the difference between knowing a boat is on sale and knowing which
     week to book, and it is worth a table of its own rather than 263 rows
     spliced into a list of 13. */
  function fleetTable(rows) {
    var table = el("table", "deals-table");
    var head = document.createElement("thead");
    var hr = el("tr", null);
    ["Boat", "On sale", "Off", "From", "To", "Per"].forEach(function (h) {
      hr.appendChild(el("th", null, h));
    });
    head.appendChild(hr);
    table.appendChild(head);
    var body = document.createElement("tbody");
    rows.forEach(function (r) {
      var tr = el("tr", null);
      tr.appendChild(el("td", "d-boat", r.boat_name));
      /* "of" as well as the count, because 18 of 18 and 3 of 16 are different
         situations and the bare number cannot tell them apart. */
      tr.appendChild(el("td", "d-when", r.sailings + " of " + r.of));
      tr.appendChild(el("td", "d-off", r.pct
        ? (r.pct + (r.pct_max ? "–" + r.pct_max : "") + "% off")
        : "—"));
      tr.appendChild(el("td", "d-when", shortDate(r.first)));
      tr.appendChild(el("td", "d-when", shortDate(r.last)));
      /* Who publishes the markdown, named rather than assumed. Most of this
         table is liveaboard.com's booking pages, but not all of it — and a
         section headed with one seller's name over a count that includes the
         other's would be exactly the splice this page refuses everywhere
         else. */
      tr.appendChild(el("td", "d-native",
        (r.sellers || []).map(function (s) { return SELLER_NAMES[s] || "?"; }).join(", ")));
      body.appendChild(tr);
    });
    table.appendChild(body);
    return table;
  }

  /* What moved on the booking pages between the last two readings of them.

     The other seller's half of "what changed", and the bigger one: 263
     discounted sailings on 22 boats against PADI's 13, with nine of those
     boats appearing in no deals listing anywhere. It could not be drawn until
     there was a second committed day to diff — `data/cabins.json` is rewritten
     whole each run — which is what `data/sales.json` is for.

     Grouped by boat, per move, because that is the unit an operator discounts
     in and because the ungrouped list is the failure being fixed: "36 sailings
     no longer 33% off" is one line that says more than 36 identical ones. */
  function salesChanges(shifted) {
    var box = el("div", "deals-moved");
    box.appendChild(el("h4", null, "What moved on liveaboard.com since " +
      shortDate(shifted.previous)));

    var list = el("ul", "deals-changes");
    (shifted.moves || []).forEach(function (m) {
      var n = m.sailings + (m.sailings === 1 ? " sailing" : " sailings");
      var when = " (" + shortDate(m.first) +
        (m.last !== m.first ? " – " + shortDate(m.last) : "") + ")";
      var off = m.pct ? m.pct + (m.pct_max ? "–" + m.pct_max : "") + "% off" : "discounted";
      var said, cls;
      if (m.kind === "started") {
        cls = "d-new";
        said = n + " newly " + off;
      } else if (m.kind === "ended") {
        cls = "d-gone";
        /* The rate it *was*, not a rate it is: a sale that ended is described
           by what came off the price while it ran. */
        said = n + " no longer " + off;
      } else {
        cls = "d-moved";
        said = n + " " + (m.was_pct + (m.was_pct_max ? "–" + m.was_pct_max : "")) +
          "% → " + off;
      }
      list.appendChild(el("li", cls, m.boat_name + " — " + said + when));
    });

    if (!list.childNodes.length) {
      box.appendChild(el("p", "deals-note",
        "No sailing started, ended or changed its discount."));
    } else {
      box.appendChild(list);
    }

    /* Said out loud, with a count, every time. A sailing missing from either
       reading has not come off sale — its booking page was not read — and a
       change list that quietly narrows its own scope reads as "that was
       everything", which is the thing this site exists to object to. */
    var one = shifted.compared === 1;
    var note = shifted.compared + (one ? " sailing was" : " sailings were") +
      " read on both days and compared.";
    if (shifted.not_compared) {
      note += " " + shifted.not_compared + " more " +
        (shifted.not_compared === 1 ? "was" : "were") + " read on only one of " +
        "them and " + (shifted.not_compared === 1 ? "is" : "are") +
        " not compared: a sailing missing from a reading has not come off sale, " +
        "it has not been looked at.";
    }
    box.appendChild(el("p", "deals-note", note));
    return box;
  }

  function drawDeals() {
    var deals = D.deals;
    var host = document.getElementById("deals");
    if (!host || !deals) return;
    var offers = deals.offers || [], fleet = (deals.on_sale || {}).boats || [];
    /* The change log counts as content of its own. The day every sale on the
       fleet ends there is no table to draw and "36 sailings are no longer 33%
       off" is the whole of what the panel has to say — which is precisely the
       thing it could not say before. */
    var shifted = deals.on_sale_changes;
    var moved = shifted && !shifted.first_reading;
    if (!offers.length && !fleet.length && !moved) return;

    var changed = ((deals.changes || {}).new || []).length +
      ((deals.changes || {}).withdrawn || []).length +
      ((deals.changes || {}).changed || []).length;
    document.getElementById("dealsLine").textContent = dealsSummary(deals, changed);

    var body = document.getElementById("dealsBody");
    body.textContent = "";

    if (fleet.length) {
      var read = deals.on_sale.read;
      body.appendChild(el("h4", null, "Every discounted sailing, by boat"));
      body.appendChild(el("p", "deals-note",
        deals.on_sale.sailings + " sailings on " + fleet.length +
        " boats are marked down, read " + shortDate(read) + ". Taken from the " +
        "list price each seller prints beside its own — struck through against " +
        "every cabin on a booking page, and stated outright by PADI — never " +
        "from the “20% Off” an operator writes into a trip name. The banner " +
        "and the list price agree on every sailing carrying one, and the list " +
        "price finds 22 more that carry none. The On sale filter below shows " +
        "these same sailings. A sale is what a seller claimed when it was " +
        "read, and can end overnight."));
      body.appendChild(fleetTable(fleet));
    }
    if (moved) body.appendChild(salesChanges(shifted));

    if (!offers.length) { host.hidden = false; return; }

    body.appendChild(el("h4", null, "Advertised on padi.com"));
    body.appendChild(el("p", "deals-note",
      "What PADI Travel advertised as discounted on " + shortDate(deals.read) +
      ", for sailings in this season. One offer per boat: PADI names an " +
      "exemplar sailing rather than every discounted one, which is why the " +
      "table above is the longer answer. It is a berth price and an offer on " +
      "one, not a total: the fees in the table below still land on the bill. " +
      "Every boat here links to the page its offer was read from."));
    body.appendChild(dealsTable(offers));

    if (deals.previous) body.appendChild(dealsChanges(deals));

    var strangers = deals.unmatched || [];
    if (strangers.length) {
      /* Named rather than counted, and on the page rather than only in a build
         log. The query asks PADI for the USA as well as Egypt because three
         Egyptian boats are filed there; the same breadth returns Caribbean
         ones. So an unmatched vessel is usually a boat from another sea and
         occasionally an Egyptian one nothing here has paired yet — and only a
         name somebody reads tells those apart. */
      body.appendChild(el("p", "deals-note",
        "PADI also advertises deals on " + strangers.length + " vessel" +
        (strangers.length === 1 ? "" : "s") + " this site does not carry: " +
        strangers.map(function (v) { return v.name; }).join(", ") +
        ". They are here because the query asks PADI for the USA as well as " +
        "Egypt — which is how the three Red Sea Aggressors, filed under the " +
        "USA, reach us at all."));
    }

    host.hidden = false;
  }

  /* ---------- wiring ---------- */

  document.getElementById("head").addEventListener("click", function (event) {
    var th = event.target.closest("th[data-k]");
    if (!th) return;
    if (th.dataset.k === state.sort) state.dir = -state.dir;
    else { state.sort = th.dataset.k; state.dir = 1; }
    draw(true);
  });
  document.getElementById("head").addEventListener("keydown", function (event) {
    if (event.key !== "Enter" && event.key !== " ") return;
    var th = event.target.closest("th[data-k]");
    if (!th) return;
    event.preventDefault();
    th.click();
  });

  document.getElementById("body").addEventListener("click", function (event) {
    var button = event.target.closest(".expand");
    if (button) {
      var row = visible()[+button.dataset.n];
      state.open = state.open === row.d.id ? null : row.d.id;
      draw(true);
      return;
    }

    /* Anywhere else on a row marks it, so the visitor can keep their place
       while scrolling sixteen columns sideways.
     *
       Three things it must not do. It must not steal a click meant for a
       link -- the Source column opens the operator's own listing, and a
       marked row is no consolation for not going there. It must not fire
       inside an expanded bill, which is a panel the visitor opened and not
       part of the row's own surface. And it must not fire at the end of a
       drag: selecting a price to copy it ends in a mouseup over the row, and
       toggling a highlight underneath the text being selected reads as the
       page fighting back. `isCollapsed` is false exactly when text was
       dragged, which is the distinction wanted -- not whether a selection
       exists, since an old one elsewhere on the page would then block every
       mark. */
    if (event.target.closest("a, button")) return;
    var tr = event.target.closest("tr.row");
    if (!tr) return;
    var selection = window.getSelection && window.getSelection();
    if (selection && !selection.isCollapsed) return;

    var id = tr.dataset.id;
    if (state.marked.has(id)) state.marked.delete(id);
    else state.marked.add(id);
    draw(true);
  });

  /* ---------- the ladder, on hover ---------- */

  /* Hover to peek, click to pin, Escape to close, and nothing on the page
     moves. The expanding row this replaces pushed every row below it down,
     which is a poor trade for a panel most visitors open to read one number.

     Both gestures, not either. Hover alone does not exist on a phone; click
     alone makes a reader hold still for something they wanted to glance at. */
  var pop = document.getElementById("berths");
  var byId = {};
  D.departures.forEach(function (d) { byId[d.id] = d; });
  var held = null, peeked = null, dismissed = null, openTimer = 0, shutTimer = 0;

  function ladderRows(block) {
    var cheapest = Math.min.apply(null, block[BLOCK_CABINS].map(function (c) {
      return c[RUNG_PRICE];
    }));
    return block[BLOCK_CABINS].map(function (c) {
      var full = c[RUNG_LEFT] === 0;
      /* The rung the row's own price came from, marked so the ladder always
         says which one you were looking at -- including when it is the one
         that has sold out. */
      var here = c[RUNG_PRICE] === cheapest;
      var left = c[RUNG_LEFT] == null
        ? '<span class="dim">not stated</span>'
        : full ? '<span class="full">full</span>' : c[RUNG_LEFT];
      return '<tr class="' + (full ? "out" : "") + (here ? " here" : "") + '">' +
        "<td>" + esc(CABIN_NAMES[c[RUNG_NAME]] || "Cabin") + "</td>" +
        '<td class="num">' + eur(c[RUNG_PRICE]) + "</td>" +
        '<td class="num">' + left + "</td></tr>";
    }).join("");
  }

  function ladderBody(d, block) {
    var cabins = block[BLOCK_CABINS] || [];
    var spots = block[BLOCK_SPOTS];
    var sale = cheapestOnSale(block);
    var advertised = Math.min.apply(null, cabins.map(function (c) { return c[RUNG_PRICE]; }));

    var verdict;
    if (sale === null) {
      verdict = '<p class="verdict bad">Every cabin is full. Nothing on this ' +
        "sailing is for sale.</p>";
    } else if (sale > advertised) {
      /* The strongest thing the ladder catches: a price on the row that
         nobody can buy. Ten sailings do this, and burying it would waste the
         whole exercise. */
      verdict = '<p class="verdict bad">The ' + eur(advertised) +
        " berth is gone. The cheapest you can book is <b>" + eur(sale) + "</b> — " +
        Math.round((sale / advertised - 1) * 100) + "% more.</p>";
    } else {
      var at = cabins.filter(function (c) { return c[RUNG_PRICE] === advertised; });
      verdict = '<p class="verdict"><b>' + spots +
        (spots === 1 ? " place" : " places") + "</b> left at " + eur(advertised) +
        (at.length > 1 ? ", across " + at.length + " rooms sold at that price" : "") +
        ".</p>";
    }

    /* One cabin type is not a ladder. Drawing a one-row table would dress a
       flat price up as a range; 39 of 864 sailings sell exactly one rung. */
    var body;
    if (cabins.length === 1) {
      body = '<p class="single">This boat sells <b>one cabin type</b> — ' +
        esc(CABIN_NAMES[cabins[0][RUNG_NAME]] || "Cabin") + " at " +
        eur(cabins[0][RUNG_PRICE]) + " a head. There is no ladder to climb: " +
        (cabins[0][RUNG_LEFT] ? "every berth on board is this price."
                              : "and it is full.") + "</p>";
    } else {
      body = '<table class="cabins"><thead><tr><th>Cabin</th>' +
        '<th class="num">Per person</th><th class="num">Left</th></tr></thead>' +
        "<tbody>" + ladderRows(block) + "</tbody></table>";
    }

    /* Checked rather than assumed: on all 864 sailings every cabin quotes the
       same supplement, so it is one line rather than a column repeating one
       number down the ladder. */
    var supp = cabins[0][RUNG_SUPP];
    var solo = supp == null
      ? '<p class="single">This vessel states <b>no single-occupancy ' +
        "supplement</b>, so what a cabin to yourself costs is unknown.</p>"
      : '<p class="single">A cabin to yourself: <b>+' + supp + "%</b>" +
        (supp >= 100 ? " — double the price above." : ".") + "</p>";

    return verdict + body + solo;
  }

  /* A seller who counted the sailing and published no ladder. One sentence,
     because one figure is all there is: "24 places" and "24 places at £1,748"
     are different claims, and inventing a rung to carry the first would dress
     it up as the second. */
  function aboardOnly(block) {
    var n = block[BLOCK_ABOARD];
    return '<p class="verdict' + (n ? "" : " bad") + '"><b>' + n +
      (n === 1 ? " berth" : " berths") + "</b> left on this sailing, at any " +
      "price. No cabin prices are published here, so there is no ladder to " +
      "read and no way to say how many are left at the fare on the row.</p>";
  }

  function fill(d) {
    /* Every block, not only the ones with a ladder. Filtering on cabins was
       right while liveaboard.com was the only seller that counted; it now
       throws away the answer on the 249 sailings where PADI is the only one
       that did. */
    var blocks = (d.berths || []).filter(function (b) {
      return (b[BLOCK_CABINS] || []).length || b[BLOCK_ABOARD] != null;
    });
    /* The read date, and nothing else by way of explanation -- what a count is
       and how perishable it is belongs in the footer with the rest of the
       method, not in a panel opened to glance at a number. But the date itself
       is not commentary: it is the difference between "two left" and "two left
       last Tuesday", and it travels with the figure or not at all. */
    /* The read date moved off the header and onto each seller's own line: two
       crawls run on two days, and one date over both dates half of them wrong.
       It stays in the header only while a single seller is speaking. */
    /* Only where one seller speaks *and* it publishes a ladder, which is the
       case that shows no seller heading for the date to ride on. Everywhere
       else the date sits against the name whose claim it dates. */
    var lone = (blocks.length === 1 && (blocks[0][BLOCK_CABINS] || []).length)
      ? readOn(blocks[0]) : "";
    var head = '<p class="pwho">' + esc(boatOf(d)) + " &middot; " +
      shortDate(d.start) + " &middot; " + d.nights + " nights" +
      (lone ? '<span class="pread">read ' + shortDate(lone) + "</span>" : "") + "</p>";
    if (!blocks.length) { pop.innerHTML = head; return; }
    /* One section per seller, and both fill one now (#92). The seller is named
       whenever two are speaking, and also whenever the only one speaking has
       no ladder — a bare count with nobody's name on it is a number the page
       is asking to be taken on trust. */
    pop.innerHTML = head + blocks.map(function (block) {
      var ladder = (block[BLOCK_CABINS] || []).length;
      var read = readOn(block);
      var name = (blocks.length > 1 || !ladder)
        ? '<p class="pseller">' + esc(SELLER_NAMES[block[BLOCK_SELLER]] || "") +
          (read ? '<span class="pread">read ' + shortDate(read) + "</span>" : "") + "</p>"
        : "";
      return name + (ladder ? ladderBody(d, block) : aboardOnly(block));
    }).join("");
  }

  function boatOf(d) {
    var itin = D.itineraries[d.itinerary_id];
    return itin ? itin.boat : "";
  }

  /* Positioned against the button rather than nested under it, so the table's
     own horizontal scroll cannot clip the panel. Flips above where there is no
     room below, and is clamped to the viewport on both axes. */
  function place(trigger) {
    var box = trigger.getBoundingClientRect();
    pop.hidden = false;
    pop.style.visibility = "hidden";
    var w = pop.offsetWidth, h = pop.offsetHeight, pad = 8;
    var left = Math.min(Math.max(pad, box.left), window.innerWidth - w - pad);
    var below = window.innerHeight - box.bottom;
    var top = below > h + pad || below > box.top ? box.bottom + 4 : box.top - h - 4;
    pop.style.left = Math.round(left) + "px";
    pop.style.top = Math.round(Math.min(Math.max(pad, top), window.innerHeight - h - pad)) + "px";
    pop.style.visibility = "";
  }

  function openBerths(trigger) {
    var d = byId[trigger.dataset.berths];
    if (!d) return;
    fill(d);
    place(trigger);
    trigger.setAttribute("aria-expanded", "true");
  }

  function shutBerths() {
    pop.hidden = true;
    var open = document.querySelector('.berths[aria-expanded="true"]');
    if (open) open.setAttribute("aria-expanded", "false");
    held = null;
    peeked = null;
  }

  var body = document.getElementById("body");

  body.addEventListener("pointerover", function (event) {
    var trigger = event.target.closest(".berths");
    if (!trigger || held || trigger === peeked) return;
    clearTimeout(shutTimer);
    clearTimeout(openTimer);
    /* A beat before opening, so running the pointer down the column does not
       flash a panel open on every row it crosses. */
    openTimer = setTimeout(function () { peeked = trigger; openBerths(trigger); }, 120);
  });

  body.addEventListener("pointerout", function (event) {
    if (held || !event.target.closest(".berths")) return;
    clearTimeout(openTimer);
    shutTimer = setTimeout(shutBerths, 160);
  });

  /* Staying open while the pointer is inside it means a six-rung ladder can be
     read without pinning it first. */
  pop.addEventListener("pointerenter", function () { clearTimeout(shutTimer); });
  pop.addEventListener("pointerleave", function () {
    if (!held) shutTimer = setTimeout(shutBerths, 160);
  });

  body.addEventListener("click", function (event) {
    var trigger = event.target.closest(".berths");
    if (!trigger) return;
    clearTimeout(openTimer);
    clearTimeout(shutTimer);
    var again = held === trigger;
    shutBerths();
    if (again) return;
    held = trigger;
    openBerths(trigger);
  });

  /* Tabbing to the cell opens it too: the panel is the column's content, not
     a reward for owning a mouse. */
  body.addEventListener("focusin", function (event) {
    var trigger = event.target.closest(".berths");
    if (!trigger || held) return;
    /* Escape returns focus to the button it dismissed, which lands right back
       here -- so without this the panel reopened the instant it closed and
       Escape did nothing at all. Cleared as soon as focus reaches any other
       trigger, so dismissing one cell does not mute the next. */
    if (trigger === dismissed) return;
    dismissed = null;
    peeked = trigger;
    openBerths(trigger);
  });

  /* Leaving the cell forgets that it was dismissed, so tabbing away and back
     opens it again. Without this, one Escape muted that cell for good. */
  body.addEventListener("focusout", function (event) {
    if (dismissed && event.target === dismissed) dismissed = null;
  });

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape" || pop.hidden) return;
    var trigger = held || peeked;
    shutBerths();
    if (trigger && document.contains(trigger)) {
      dismissed = trigger;
      trigger.focus();
    }
  });

  document.addEventListener("click", function (event) {
    if (held && !event.target.closest(".berths") && !event.target.closest("#berths")) {
      shutBerths();
    }
  });

  /* Fixed positioning is relative to the viewport, so the panel has to be
     moved with whatever scrolled -- the page or the table. Closing on resize
     rather than chasing it: a reflow can move the button out from under it. */
  window.addEventListener("scroll", function () {
    var trigger = held || peeked;
    if (pop.hidden || !trigger) return;
    if (!document.contains(trigger)) { shutBerths(); return; }
    place(trigger);
  }, true);
  window.addEventListener("resize", shutBerths);

  document.getElementById("toggles").innerHTML = D.facets.toggles.map(function (t) {
    return '<button class="chip" data-t="' + t.id + '" aria-pressed="' + t.default + '">' +
      esc(t.label) + "</button>";
  }).join("");
  document.getElementById("toggles").addEventListener("click", function (event) {
    var button = event.target.closest("button");
    if (!button) return;
    state.toggles[button.dataset.t] = !state.toggles[button.dataset.t];
    button.setAttribute("aria-pressed", state.toggles[button.dataset.t]);
    draw();
  });

  chips("months", D.facets.months.map(function (m) {
    return { id: m.id, label: m.label,
             n: D.departures.filter(function (d) { return d.month === m.id; }).length };
  }), state.months, true, "months", function (i, dep) { return [dep.month]; });
  chips("ports", PORTS, state.ports, false, "ports",
        function (i) { return [i.port_from]; });
  /* Not skipped: these chips are ANDed, so the number is what you narrow to. */
  chips("sites", SITES, state.sites, false, null,
        function (i) { return i.dive_sites || []; });
  chips("boats", BOATS, state.boats, false, "boats",
        function (i) { return [i.boat]; });
  chips("sellers", SELLERS, state.sellers, false, "sellers",
        function (i, dep) { return [sellerOf(dep)]; });

  /* A range rather than chips: the fleet runs three to fourteen nights but
     sits overwhelmingly at seven, so a chip per length would be one useful
     control surrounded by near-empty ones. Blank means unbounded on that side,
     which is what an empty box should mean. */
  var NIGHTS = D.departures.map(function (d) { return d.nights; });
  var nmin = document.getElementById("nmin"), nmax = document.getElementById("nmax");
  nmin.min = nmax.min = Math.min.apply(null, NIGHTS);
  nmin.max = nmax.max = Math.max.apply(null, NIGHTS);
  nmin.placeholder = nmin.min;
  nmax.placeholder = nmax.max;

  function readNights() {
    var lo = nmin.value === "" ? null : +nmin.value;
    var hi = nmax.value === "" ? null : +nmax.value;
    /* Entering 7 then 3 should show seven-night trips, not nothing at all. */
    if (lo !== null && hi !== null && lo > hi) { var t = lo; lo = hi; hi = t; }
    state.nightsMin = lo;
    state.nightsMax = hi;
    draw();
  }
  nmin.addEventListener("input", readNights);
  nmax.addEventListener("input", readNights);

  /* The On sale chip. Its count is of the whole dataset rather than of what
     is on screen, deliberately: it says how much there is to find, not how
     much the filters have already left, and a count that fell to 0 under an
     unrelated month filter would read as "no sales" rather than "none in
     June". */
  var onSale = document.getElementById("onSale");
  var onSaleCount = D.departures.filter(function (d) { return !!d.sale; }).length;
  if (onSaleCount) {
    onSale.hidden = false;
    onSale.textContent = "On sale " + onSaleCount;
    onSale.addEventListener("click", function () {
      state.onSaleOnly = !state.onSaleOnly;
      onSale.setAttribute("aria-pressed", state.onSaleOnly);
      draw();
    });
  }

  var soldOut = document.getElementById("hideSold");
  soldOut.addEventListener("click", function () {
    state.hideSoldOut = !state.hideSoldOut;
    soldOut.setAttribute("aria-pressed", state.hideSoldOut);
    draw();
  });

  document.getElementById("reset").addEventListener("click", function () {
    state.months.clear(); state.ports.clear(); state.sites.clear();
    state.boats.clear();
    /* Marks go with the filters. Reset puts the table back to how it opened,
       and a highlight left behind on a row the visitor can no longer find is
       worse than no highlight at all. */
    state.marked.clear();
    state.sellers.clear();
    state.nightsMin = state.nightsMax = null;
    state.hideSoldOut = false;
    soldOut.setAttribute("aria-pressed", "false");
    state.onSaleOnly = false;
    onSale.setAttribute("aria-pressed", "false");
    nmin.value = ""; nmax.value = "";
    D.facets.toggles.forEach(function (t) { state.toggles[t.id] = t.default; });
    Array.prototype.forEach.call(document.querySelectorAll(".chip"), function (chip) {
      /* The "more" control is a disclosure, not a filter -- writing
         aria-pressed onto it would give it toggle semantics it does not have. */
      if (chip.dataset.more) return;
      chip.setAttribute("aria-pressed",
        chip.dataset.t ? String(!!state.toggles[chip.dataset.t]) : "false");
    });
    /* A bank that was expanded to reach a chip, or that is holding a chosen
       chip out of its hidden tail, has to be rebuilt from the cleared set --
       repainting is the only thing that puts those chips back where they
       belong. */
    ["months", "ports", "sites", "boats", "sellers"].forEach(function (id) {
      var node = document.getElementById(id);
      if (node && node.repaint) node.repaint();
    });
    labelFilters();
    draw();
  });

  /* Append the next page of rows as the table is scrolled. Guarded on
     `state.open` being unchanged is unnecessary -- an expanded row is drawn
     inside its own page and appending after it does not disturb it. */
  document.querySelector(".shell").addEventListener("scroll", function () {
    if (drawn >= lastRows.length) return;
    var shell = this;
    if (shell.scrollTop + shell.clientHeight < shell.scrollHeight - 600) return;
    document.getElementById("body").insertAdjacentHTML(
      "beforeend", renderRows(lastRows, drawn, PAGE_ROWS)
    );
    drawn = Math.min(lastRows.length, drawn + PAGE_ROWS);
  }, { passive: true });

  /* Draw the rest of the rows the moment the browser's own find is opened.
     Chunking means only the drawn rows are in the DOM, so Ctrl+F would search
     120 of 838 -- the one thing it costs. The keydown arrives before the find
     bar does, so the remaining rows can be in place by the time it is typed
     into. Cmd+F on a Mac, F3 on Windows, and "/" in Firefox's quick find. */
  function drawEverything() {
    if (drawn >= lastRows.length) return;
    document.getElementById("body").insertAdjacentHTML(
      "beforeend", renderRows(lastRows, drawn, lastRows.length - drawn)
    );
    drawn = lastRows.length;
  }
  window.addEventListener("keydown", function (event) {
    var find = (event.key === "f" || event.key === "F") && (event.ctrlKey || event.metaKey);
    if (find || event.key === "F3") drawEverything();
  });
  /* And on print, for the same reason: a printed page of 120 rows would be a
     silent truncation of one showing 838. */
  if (window.matchMedia) {
    var print = window.matchMedia("print");
    if (print.addEventListener) print.addEventListener("change", drawEverything);
  }
  window.addEventListener("beforeprint", drawEverything);

  /* Rotating the device changes which order the columns should be in, and a
     table left in the other one is the bug this exists to prevent. */
  var onWidthChange = function () { orderColumns(); draw(true); };
  [compact, narrow, tiny].forEach(function (mq) {
    if (mq.addEventListener) mq.addEventListener("change", onWidthChange);
    else if (mq.addListener) mq.addListener(onWidthChange);
  });

  /* The banks fold away from 1000px down, and a chosen filter folds away with
     them: at 900px you could pick one operator, collapse the panel, and the
     page would show 40 of 882 rows with nothing on screen saying why. The
     label carries the count so the fold never hides the fact that something
     is filtering. */
  var filtersToggle = document.getElementById("filtersToggle");
  function labelFilters() {
    var n = state.ports.size + state.sites.size + state.boats.size;
    filtersToggle.textContent = n
      ? n + (n === 1 ? " filter" : " filters") + " on — port, site or boat"
      : "Filter by port, site or boat";
    filtersToggle.classList.toggle("active", n > 0);
  }
  filtersToggle.addEventListener("click", function () {
    var open = document.body.classList.toggle("filters-open");
    filtersToggle.setAttribute("aria-expanded", String(open));
  });

  labelFilters();

  /* Closing the method panel puts the reader back at its heading.
   *
   * The panel scrolls inside itself, and its summary is pinned to the top of
   * that box, so it can be shut from wherever you got to. Two things are then
   * wrong if nothing is done. The box keeps the scroll position it had, so
   * reopening it drops you back into the middle of a paragraph you had
   * finished with. And the page may be scrolled to where the panel filled the
   * screen a moment ago, which after it collapses to one line is blank space
   * under the table.
   *
   * `nearest` rather than a jump to the top: when the heading is already on
   * screen -- which it is whenever the panel was opened without scrolling --
   * the right amount of movement is none. */
  var method = document.querySelector(".site-footer");
  if (method) {
    method.addEventListener("toggle", function () {
      if (method.open) return;
      method.scrollTop = 0;
      method.scrollIntoView({ block: "nearest" });
    });
  }

  document.getElementById("metaLine").textContent =
    D.meta.counts.departures.toLocaleString("en-IE") + " departures · " +
    /* "bookable by the berth", not "boats in Egypt" — charter-only vessels are
       never linked from the search pages, so the crawl cannot see them. */
    D.meta.counts.boats + " boats bookable by the berth · " +
    D.meta.counts.operators + " operators · all prices in " + D.meta.currency +
    /* The build, not the crawl. `generated` is the day the data was read and
       is what the colophon prints beside the sources; this line says when the
       page you are looking at was made, which is a different day whenever a
       template or parser change ships without a fresh crawl. To the minute,
       because several builds an hour is normal and a date cannot tell them
       apart. */
    " · built " + (D.meta.built || D.meta.generated);

  drawNotice();
  drawDeals();
  draw();
})();
