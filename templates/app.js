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

  var euro = new Intl.NumberFormat("en-IE", {
    style: "currency", currency: D.meta.currency,
    minimumFractionDigits: 0, maximumFractionDigits: 0
  });

  var state = {
    sort: "start", dir: 1, q: "",
    months: new Set(), ports: new Set(), sites: new Set(), operators: new Set(),
    nightsMin: null, nightsMax: null, hideSoldOut: false,
    toggles: {}, open: null
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
    var low = 0, high = 0, unpriced = [], required = 0;
    var nitrox = null, tips = null;
    linesFor(dep).forEach(function (line) {
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
      total: low, totalMax: high, isRange: high > low + 0.5,
      unpriced: unpriced, required: required, nitrox: nitrox, tips: tips,
      later: low - dep.base
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
     figure Lands later is worked out from. Said out loud, because a graphic
     that answers a narrower question than the number above it should not do so
     silently. */
  var BAR_TITLE = "Advertised, then what lands later. Scaled against the " +
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

  /* The company that runs the trip, which the source names on every departure.
     Grouping by it is useful — one operator's boats may all bundle nitrox
     while another's all bill for it, and that is a fact about prices. Ranking
     them is not: a per-operator honesty score was removed for reading as a
     league table, and naming who sells a trip must not bring it back.

     A filter and a search term, deliberately not a column. Who sells a trip is
     how you narrow the table, not something you compare two rows on: the
     column repeated one of 42 names on every row and answered no question the
     price columns beside it did not answer better. The field stays in the
     dataset and in the search haystack, so a diver looking for a particular
     company still finds them. */
  var OPERATORS = tally(function (i) { return [i.operator]; });

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
    { k: "guests", t: "Guests", num: true,
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
    { k: "base", t: "Advertised", num: true,
      v: function (d) { return d.base; }, show: function (d) { return eur(d.base); } },
    { k: "total", t: "True cost", num: true, cls: "cost",
      v: function (d, i, m) { return m.total; },
      show: function (d, i, m) {
        if (!d.mandatory_known) return '<span class="dim">—</span>';
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
        return "<b>" + span(m) + "</b>" +
          (m.tips === "unpriced" ? '<span class="plus"> + tips</span>' : "") + bar;
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
      v: function (d, i, m) { return i.dives > 0 ? m.total / i.dives : -1; },
      show: function (d, i, m) {
        if (!i.dives) {
          return '<span class="dim" title="This operator does not publish a ' +
                 'dive count. Assuming one would divide the bill by a number ' +
                 'nobody stated.">not stated</span>';
        }
        if (!d.mandatory_known) return '<span class="dim">—</span>';
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
    /* Included or extra, said plainly. Half this fleet bundles nitrox and half
       bills for it, and on a page for comparing trips that difference has to be
       readable without opening a row. */
    { k: "nitrox", t: "Nitrox", num: true,
      v: function (d, i, m) {
        return !m.nitrox ? 9e9 : m.nitrox.included ? -1
             : m.nitrox.price != null ? m.nitrox.price : 9e8;
      },
      show: function (d, i, m) {
        if (!m.nitrox) return '<span class="dim">not listed</span>';
        if (m.nitrox.included) return '<span class="inc">included</span>';
        if (m.nitrox.price != null) return eur(m.nitrox.price);
        return '<span class="dim">extra, no price</span>';
      } },
    { k: "later", t: "Lands later", num: true,
      v: function (d, i, m) { return m.later; },
      show: function (d, i, m) {
        return d.mandatory_known
          ? '<span class="later">+' + eur(m.later) + "</span>"
          : '<span class="dim">—</span>';
      } },
    { k: "required", t: "Required fees", num: true,
      v: function (d, i, m) { return m.required; },
      show: function (d, i, m) {
        return m.required > 0 ? eur(m.required) : '<span class="dim">—</span>';
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
    { k: "disclosure", t: "Disclosure", v: function (d) { return disclosure(d)[1]; },
      show: function (d) {
        var s = disclosure(d);
        return '<span class="pill ' + s[0] + '">' + s[1] + "</span>";
      } },
    { k: "source", t: "Source", v: function (d, i) { return i.source_url || ""; },
      show: function (d, i) {
        return i.source_url
          ? '<a href="' + esc(i.source_url) + '" target="_blank" rel="noopener">listing ↗</a>'
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
    "start", "end", "boat", "guests", "from", "to", "trip",
    "total", "later", "base", "perdive", "nitrox", "sites",
    "required", "availability", "disclosure", "source"
  ];
  /* The same columns on a phone, reordered again.
     A phone shows about two columns beside the two pinned ones, and on the
     desktop order those two were Nts and Trip -- so the table opened on a trip
     title and you scrolled 600px to find out what it cost. Here the money
     comes first and the descriptive columns sit behind it: nothing is hidden,
     the reading order is just inverted to match how much screen there is. */
  /* The same columns wherever there is not room for the reading order above.
     Identity, then the money, then everything the money is for.

     Measured: with Guests, From, To and Trip ahead of it, True cost sits at
     x 919-1083, so it needs 1083px of window to be on screen at all -- it fell
     off a 900px and a 1024px laptop, which is most of them. The wide order is
     the better read where it fits; this is the same table where it does not. */
  /* A phone fits the expander, two pinned columns and the price, and nothing
     else before it: 24 + 66 + 96 + 160 is 346 of 390. A third pinned column
     makes 412, and Return merely sitting third rather than pinned still makes
     424 -- either way the number the page exists to show is off the edge.
     So here Return follows the money rather than leading it. Of the two
     identifiers, the boat's name is what a row is compared by; the return date
     you read once you have found the row. */
  var PHONE_ORDER = [
    "start", "boat",
    "total", "later", "base", "perdive", "nitrox",
    "end", "guests", "from", "to", "trip", "sites",
    "required", "availability", "disclosure", "source"
  ];

  var COMPACT_ORDER = [
    "start", "end", "boat",
    "total", "later", "base", "perdive", "nitrox",
    "guests", "from", "to", "trip", "sites",
    "required", "availability", "disclosure", "source"
  ];

  /* Two questions, two breakpoints. `compact` is about how much room there is
     before the money column; `narrow` is about how much room there is at all,
     and drives the pinned-column widths and the folded filter banks. */
  var compact = window.matchMedia("(max-width: 1100px)");
  var narrow = window.matchMedia("(max-width: 760px)");

  /* How many of the leading columns are pinned. By position, never by name:
     two pinned columns with a third between them overlap exactly as badly as
     two with a wrong offset, and naming them let that happen the moment the
     order changed. Three fit a laptop; on a phone 82 + 78 + 132 would be three
     quarters of the screen, so a phone pins two and Return scrolls. */
  function pinned() { return narrow.matches ? 2 : 3; }

  function orderColumns() {
    /* The rule that closes the pinned group goes on whichever column is last
       in it, and that changes with the breakpoint. */
    document.body.classList.toggle("pins-2", narrow.matches);
    var order = narrow.matches ? PHONE_ORDER
              : compact.matches ? COMPACT_ORDER : ORDER;
    COLS.sort(function (a, b) {
      var x = order.indexOf(a.k), y = order.indexOf(b.k);
      return (x < 0 ? order.length : x) - (y < 0 ? order.length : y);
    });
  }
  COLS.forEach(function (c) {
    /* Appended rather than dropped, and said out loud: a column that quietly
       vanished from both lists would be a fact the page stopped publishing. */
    if (ORDER.indexOf(c.k) < 0 && window.console) {
      console.warn("column " + c.k + " is not in ORDER; printed last");
    }
  });
  orderColumns();

  /* ---------- filtering and sorting ---------- */

  function visible() {
    var out = [];
    D.departures.forEach(function (dep) {
      var itin = D.itineraries[dep.itinerary_id];
      if (state.months.size && !state.months.has(dep.month)) return;
      if (state.hideSoldOut && !dep.bookable) return;
      if (state.nightsMin !== null && dep.nights < state.nightsMin) return;
      if (state.nightsMax !== null && dep.nights > state.nightsMax) return;
      if (state.ports.size && !state.ports.has(itin.port_from)) return;
      if (state.operators.size && !state.operators.has(itin.operator)) return;
      if (state.sites.size) {
        /* Any, not all: picking Brothers and Daedalus means "either", which is
           how somebody shops for a week rather than a checklist. */
        var sites = itin.dive_sites || [], hit = false;
        state.sites.forEach(function (s) { if (sites.indexOf(s) >= 0) hit = true; });
        if (!hit) return;
      }
      if (state.q) {
        var hay = (itin.boat + " " + itin.operator + " " + itin.name + " " +
                   itin.port_from + " " + itin.port_to + " " +
                   (itin.dive_sites || []).join(" ")).toLowerCase();
        if (hay.indexOf(state.q) < 0) return;
      }
      out.push({ d: dep, i: itin, m: metricsFor(dep) });
    });

    var col = COLS.filter(function (c) { return c.k === state.sort; })[0] || COLS[0];
    out.sort(function (a, b) {
      var x = col.v(a.d, a.i, a.m), y = col.v(b.d, b.i, b.m);
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

  function feeTable(row) {
    var body = linesFor(row.d).map(function (line) {
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

    var caveat = "";
    if (!row.d.fees_known) {
      caveat = "Marine park fees, port dues, fuel, nitrox and gratuities are not " +
        "included above because they have not been captured for this trip yet. " +
        "On comparable trips they add 30–60%.";
    } else if (!row.d.mandatory_known) {
      caveat = "This operator publishes no required extras. Every Egyptian " +
        "liveaboard pays marine park and port fees, so either they are already " +
        "inside the advertised price or they are collected at the dock — the " +
        "listing does not say which, so no true cost is claimed here.";
    } else if (row.m.unpriced.length) {
      caveat = "Plus " + row.m.unpriced.join(", ") + ": listed by the operator " +
        "with no price, so it cannot be added up here. It is not free.";
    }
    return '<table class="fees"><tbody>' + body + "</tbody></table>" +
      (caveat ? '<p class="caveat">' + esc(caveat) + "</p>" : "");
  }

  function draw() {
    var rows = visible();

    /* Before any cell is rendered: the anchor bars scale against the dearest
       trip on screen, so filtering to three boats redraws the bars against
       those three rather than against a fleet maximum that is no longer
       visible. Only priced rows count -- an unpriced one has no total. */
    barMax = rows.reduce(function (top, r) {
      return r.d.mandatory_known && r.m.total > top ? r.m.total : top;
    }, 0);

    /* `stick1`..`stickN` on the leading columns, so the CSS offsets line up
       with the order actually being rendered. */
    var pins = pinned();
    function pin(index) { return index < pins ? "stick" + (index + 1) : ""; }

    document.getElementById("head").innerHTML = '<tr><th class="expander"></th>' +
      COLS.map(function (c, n) {
      var dir = c.k === state.sort
        ? '<span class="dir">' + (state.dir > 0 ? "▲" : "▼") + "</span>" : "";
      return '<th tabindex="0" class="' + (c.num ? "num " : "") + pin(n) +
        '" data-k="' + c.k + '">' + c.t + " " + dir + "</th>";
    }).join("") + "</tr>";

    document.getElementById("body").innerHTML = rows.length
      ? rows.map(function (row, n) {
          var tds = COLS.map(function (c, col) {
            var v = c.show ? c.show(row.d, row.i, row.m) : esc(c.v(row.d, row.i, row.m));
            return '<td class="' + (c.num ? "num " : "") + (c.cls || "") +
              " " + pin(col) + '">' + v + "</td>";
          }).join("");
          var open = state.open === row.d.id;
          /* Banding is written here from the row's own position, not left to
             `:nth-of-type(even)`. That selector counts every `tr` in the
             tbody, and an expanded row injects one -- so opening any row
             inverted the stripes of every row below it. */
          return '<tr class="row' + (n % 2 ? " alt" : "") +
            (row.d.bookable ? "" : " gone") + '">' +
            '<td class="expander"><button class="expand" data-n="' + n +
            '" aria-expanded="' + open + '">' + (open ? "−" : "+") + "</button></td>" +
            tds + "</tr>" +
            (open ? '<tr class="detail"><td colspan="' + (COLS.length + 1) + '">' +
              feeTable(row) + "</td></tr>" : "");
        }).join("")
      : '<tr><td class="empty" colspan="' + (COLS.length + 1) +
        '">Nothing matches those filters.</td></tr>';

    document.getElementById("shown").textContent = rows.length.toLocaleString("en-IE");
    var boats = {}, itins = {};
    rows.forEach(function (r) { boats[r.i.boat_id] = 1; itins[r.i.id] = 1; });
    document.getElementById("nboats").textContent = Object.keys(boats).length;
    document.getElementById("nitin").textContent = Object.keys(itins).length;

    /* Only average what has a figure: a trip whose required extras are
       unstated has no gap to average, and counting it as zero would drag the
       number toward "nothing lands later". */
    var known = rows.filter(function (r) { return r.d.mandatory_known; });
    document.getElementById("gap").textContent = known.length
      ? eur(known.reduce(function (s, r) { return s + r.m.later; }, 0) / known.length)
      : "—";
  }

  /* How many chips a bank shows before the rest go behind "more".
     42 operators, 17 dive sites and 6 ports were all printed at once, which
     put 66 buttons above the table: the first row of data began 596px down a
     1440x900 window and 1708px down a phone, where nothing was visible at all
     without scrolling past two screens of filters. A filter you have not
     chosen yet should not outrank the prices you came to read. */
  var CHIP_LIMIT = 8;

  function chips(host, items, picked, numeric) {
    var node = document.getElementById(host);
    var expanded = false;

    function paint() {
      /* A chosen filter is always shown, wherever it sits in the list: a
         chip hidden behind "more" while switched on is a filter that appears
         to have been ignored. */
      var shown = expanded ? items : items.filter(function (it, n) {
        var v = numeric ? +it.id : it.id;
        return n < CHIP_LIMIT || picked.has(v);
      });
      var hidden = items.length - shown.length;
      node.innerHTML = shown.map(function (it) {
        var v = numeric ? +it.id : it.id;
        return '<button class="chip" data-v="' +
          esc(it.id).replace(/"/g, "&quot;") + '" aria-pressed="' + picked.has(v) + '">' +
          esc(it.label || it.id) + ' <span class="dim">' + it.n + "</span></button>";
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
    draw();
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
    if (!button) return;
    var row = visible()[+button.dataset.n];
    state.open = state.open === row.d.id ? null : row.d.id;
    draw();
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
  }), state.months, true);
  chips("ports", PORTS, state.ports, false);
  chips("sites", SITES, state.sites, false);
  chips("operators", OPERATORS, state.operators, false);

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

  document.getElementById("q").addEventListener("input", function (event) {
    state.q = event.target.value.toLowerCase().trim();
    draw();
  });

  document.getElementById("reset").addEventListener("click", function () {
    state.months.clear(); state.ports.clear(); state.sites.clear();
    state.operators.clear();
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
    ["months", "ports", "sites", "operators"].forEach(function (id) {
      var node = document.getElementById(id);
      if (node && node.repaint) node.repaint();
    });
    labelFilters();
    draw();
  });

  /* Rotating the device changes which order the columns should be in, and a
     table left in the other one is the bug this exists to prevent. */
  var onWidthChange = function () { orderColumns(); draw(); };
  [compact, narrow].forEach(function (mq) {
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
    var n = state.ports.size + state.sites.size + state.operators.size;
    filtersToggle.textContent = n
      ? n + (n === 1 ? " filter" : " filters") + " on — port, site or operator"
      : "Filter by port, site or operator";
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
