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
    sort: "start", dir: 1, q: "",
    months: new Set(), ports: new Set(), sites: new Set(), boats: new Set(),
    nightsMin: null, nightsMax: null, hideSoldOut: false,
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

  /* The cheapest total anyone quotes for this sailing, and whether the two
     sellers agree about it.
   *
     `.m` is the bill from the seller whose fee book this site was built on;
     `.p` is the second seller's, present only where its own disclosure is
     complete. Where both exist the page leads with the lower one, because a
     diver buying this trip pays the lower one -- showing the dearer as though
     it were the price would be a comparison site quoting somebody a number
     they need not pay.

     `varies` is the spread, and it is the point: two sites selling one berth
     on one boat on one date do not agree, and the page says by how much rather
     than quietly picking a side. Under `PADI_SAME` they are one price. */
  function best(row) {
    var ours = row.d.mandatory_known ? row.m : null;
    var theirs = row.p;
    if (!ours && !theirs) return null;
    if (!ours || !theirs) {
      var only = ours || theirs;
      return { m: only, cheapest: ours ? "ours" : "padi", varies: 0, both: false };
    }
    var gap = row.m.total - row.p.total;
    return {
      m: gap <= 0 ? row.m : row.p,
      cheapest: Math.abs(gap) < PADI_SAME ? "same" : gap < 0 ? "ours" : "padi",
      varies: Math.abs(gap),
      both: true
    };
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

     The operator is not lost: it stays on every itinerary in the dataset and
     in the search haystack, so typing a company's name still finds its whole
     fleet. It is one word in a search box rather than 42 buttons above the
     prices, which is the right weight for a question asked far less often
     than "which boat". */
  var BOATS = tally(function (i) { return [i.boat]; });

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
    { k: "boat", t: "Boat", v: function (d, i) { return i.boat; } },
    /* Berth price is per person, so this says whether you are buying into a
       boat of twelve or of thirty-four. Null where the description does not
       state it — about half the fleet, which is a gap in the scrape rather
       than an operator declining to say. */
    { k: "guests", t: "Guests", short: "Pax", num: true,
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
      v: function (d, i, m, row) { var b = best(row); return b ? b.m.base : d.base; },
      show: function (d, i, m, row) {
        var b = best(row);
        return eur(b ? b.m.base : d.base);
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
      v: function (d, i, m, row) { var b = best(row); return b ? b.m.total : Infinity; },
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
        /* The marker is on the number rather than beside it in another
           column, because it qualifies this number: read alone, €1,679 is a
           price, and what it actually is is the lower of two. Only where the
           two genuinely differ -- an agreeing pair is one price twice. */
        var varies = b.both && b.cheapest !== "same"
          ? '<span class="varies" title="Two sellers price this sailing and ' +
            'they differ by €' + Math.round(b.varies).toLocaleString("en-IE") +
            '. The lower is shown; the Sellers column says whose it is.">' +
            "varies</span>"
          : "";
        return "<b>" + span(m) + "</b>" +
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
    { k: "later", t: "Mandatory fees", num: true,
      v: function (d, i, m, row) { var b = best(row); return b ? b.m.later : m.later; },
      show: function (d, i, m, row) {
        var b = best(row);
        return b
          ? '<span class="later">+' + eur(b.m.later) + "</span>"
          : '<span class="dim">—</span>';
      } },
    /* 127 of 886 departures are sold out. Priced alongside bookable ones with
       no way to tell them apart, a cheapest-first sort could put a trip nobody
       can buy at the top of the list. */
    { k: "availability", t: "Places",
      v: function (d) {
        return d.availability === "sold_out" ? 2 : d.availability === "limited" ? 0 : 1;
      },
      show: function (d) {
        if (d.availability === "sold_out") return '<span class="pill gone">sold out</span>';
        if (d.availability === "limited") return '<span class="pill few">few left</span>';
        if (d.availability === "available") return '<span class="pill open">available</span>';
        return '<span class="dim">—</span>';
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

       Four states, and they are four different facts:

         both, differ  the spread, and who is cheaper. What the column is for.
         both, same    one price sold twice.
         price only    PADI sells the date and does not disclose a full bill,
                       so there is a second price and no second total -- 432
                       rows. Printing a berth gap here would be the old
                       column's mistake with a new heading.
         nothing       PADI does not sell that date. Evidence of nothing: its
                       calendar runs to a different depth on every boat.

       Sorted by the size of the disagreement so the widest come to the top,
       which is the one thing this column is for. */
    { k: "padi", t: "Sellers",
      hint: "Whether PADI Travel sells this same sailing, and how its total "
          + "compares -- berth plus the fees it discloses, against ours. Both "
          + "totals carry the same on-board extras, because nitrox and rental "
          + "gear are the vessel's charge whoever sold the berth. \u201cberth "
          + "only\u201d means PADI prices the date but does not publish a "
          + "complete fee book for the trip, so no second total is claimed. A "
          + "dash means PADI does not sell that date, which is evidence of "
          + "nothing.",
      v: function (d, i, m, row) {
        var b = best(row);
        /* Rows with nothing to compare sort last rather than as zero: no
           second quote is not agreement. */
        return b && b.both ? -b.varies : Infinity;
      },
      show: function (d, i, m, row) {
        var b = best(row);
        if (b && b.both) {
          if (b.cheapest === "same") return '<span class="dim">both, same</span>';
          /* Named, not graded. Both hues in `.cheaper`/`.dearer` mean good and
             bad elsewhere on this page, and neither applies: the Total column
             already prints whichever bill is lower, so there is no saving
             being missed and no seller being beaten. What is left to say is
             who quotes the cheaper bill and by how much. */
          return (b.cheapest === "padi" ? "PADI" : "here") +
            '<span class="dim"> −€' +
            Math.round(b.varies).toLocaleString("en-IE") + "</span>";
        }
        if (d.padi != null) {
          return '<span class="dim" title="PADI Travel advertises this berth ' +
            'at €' + Math.round(d.padi).toLocaleString("en-IE") + ' and does ' +
            'not publish a complete fee book for the trip, so its total is ' +
            'not known. A berth price is not comparable to a total.">' +
            "berth only</span>";
        }
        return '<span class="dim">—</span>';
      } },
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
    { k: "source", t: "Source",
      v: function (d, i) { return d.booking_url || i.source_url || ""; },
      show: function (d, i) {
        var url = d.booking_url || i.source_url;
        return url
          ? '<a href="' + esc(url) + '" target="_blank" rel="noopener">listing ↗</a>'
          : '<span class="dim">—</span>';
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
    "base", "nitrox", "later", "total", "perdive", "padi",
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
    "total", "base", "nitrox", "later", "perdive", "padi",
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
    "total", "base", "nitrox", "later", "perdive", "padi",
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
    "base", "nitrox", "later", "total", "perdive", "padi",
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
     order changed. Three fit a laptop; on a phone 82 + 78 + 132 would be three
     quarters of the screen, so a phone pins two and Return scrolls.

     Four on a wide screen, because Guests is a fact about the vessel -- how
     many people you share a dive deck with -- and the pinned group's closing
     rule is what says where the identity columns end. Left at three, that rule
     fell between Boat and Guests and filed the guest count as the first of the
     route columns. It is on the boat's side of it now.

     Three on a phone for the same reason and not a different one. Guests sits
     with the boat in PHONE_ORDER, so pinning two would put the closing rule
     between them and undo on a phone exactly what the fourth pin fixed on a
     desktop -- the group would say the guest count belongs to the money. Here
     the three are Depart, Boat, Guests: 24 + 66 + 96 + 45 of 390, with the
     Total whole in what remains.

     Two below 386px, where Guests is behind the money rather than in front of
     it (see TINY_ORDER). Pinning it there would freeze 59% of the screen to
     hold a column that is not even next to the boat any more. */
  function pinned() {
    return tiny.matches ? 2 : narrow.matches ? 3 : compact.matches ? 3 : 4;
  }

  function orderColumns() {
    /* The rule that closes the pinned group goes on whichever column is last
       in it, and that changes with the breakpoint. */
    var n = pinned();
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
    if (state.q) {
      var hay = (itin.boat + " " + itin.operator + " " + itin.name + " " +
                 itin.port_from + " " + itin.port_to + " " +
                 (itin.dive_sites || []).join(" ")).toLowerCase();
      if (hay.indexOf(state.q) < 0) return false;
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
     chosen yet should not outrank the prices you came to read. */
  var CHIP_LIMIT = 8;

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
      var shown = expanded ? live : live.filter(function (it, n) {
        var v = numeric ? +it.id : it.id;
        return n < CHIP_LIMIT || picked.has(v);
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

  var soldOut = document.getElementById("hideSold");
  soldOut.addEventListener("click", function () {
    state.hideSoldOut = !state.hideSoldOut;
    soldOut.setAttribute("aria-pressed", state.hideSoldOut);
    draw();
  });

  document.getElementById("q").addEventListener("input", debounce(function (event) {
    state.q = event.target.value.toLowerCase().trim();
    draw();
  }, 120));

  document.getElementById("reset").addEventListener("click", function () {
    state.months.clear(); state.ports.clear(); state.sites.clear();
    state.boats.clear();
    /* Marks go with the filters. Reset puts the table back to how it opened,
       and a highlight left behind on a row the visitor can no longer find is
       worse than no highlight at all. */
    state.marked.clear();
    state.q = "";
    state.nightsMin = state.nightsMax = null;
    state.hideSoldOut = false;
    soldOut.setAttribute("aria-pressed", "false");
    document.getElementById("q").value = "";
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
    ["months", "ports", "sites", "boats"].forEach(function (id) {
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

  /* Typing is the one input that fires per character, and a redraw is the
     most expensive thing this file does. Without this, "elphinstone" cost
     eleven full redraws; with it, one. 120ms is below the point a pause
     between keystrokes reads as lag. */
  function debounce(fn, ms) {
    var timer = null;
    return function () {
      var self = this, args = arguments;
      clearTimeout(timer);
      timer = setTimeout(function () { fn.apply(self, args); }, ms);
    };
  }

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

  document.getElementById("metaLine").textContent =
    D.meta.counts.departures.toLocaleString("en-IE") + " departures · " +
    /* "bookable by the berth", not "boats in Egypt" — charter-only vessels are
       never linked from the search pages, so the crawl cannot see them. */
    D.meta.counts.boats + " boats bookable by the berth · " +
    D.meta.counts.operators + " operators · all prices in " + D.meta.currency +
    " · built " + D.meta.generated;

  drawNotice();
  draw();
})();
