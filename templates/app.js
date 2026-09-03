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

  /* How many sailings a seller has marked down. Read by four things -- the
     chip, the rail, the sale view's empty line and the decision to offer that
     view at all -- so it is counted once here rather than at each of them. */
  var onSaleCount = D.departures.filter(function (d) { return !!d.sale; }).length;
  /* Sold out is a *stated* sold-out and nothing else. `bookable` is "anything
     but a stated sold-out -- unknown is not a refusal", so counting `!bookable`
     keeps that distinction for free; counting "not available" would fold the
     limited sailings into a figure they are not part of. Whether the chip is
     offered at all turns on this: a checkout where everything is bookable has
     nothing for it to hide. */
  var soldOutCount = D.departures.filter(function (d) { return !d.bookable; }).length;

  /* THE SHELL IS WHAT THE WINDOW MEASURES, AND IT IS MEASURED RATHER THAN
     DECLARED.
   *
     `height:100dvh` in the stylesheet is the right rule and it is not enough
     on its own. `dvh` landed in Safari 15.4, and where it is missing the
     fallback is `height:100%` -- which resolves against the initial containing
     block, the viewport with the URL bar *hidden*, so the shell stands about
     120px taller than the area being looked through and those 120px are slack
     the whole page can be panned into: masthead and rail off the top, footer
     over bare canvas at the bottom.

     What gave it away is that backgrounding the app and coming back fixed it.
     That is not a layout being wrong, it is a layout being *stale* -- one
     forced reflow and it snapped right -- so the answer is to reflow it on
     every event that can change the visible height, off a number the browser
     has actually measured.

     `window.innerHeight` rather than `visualViewport.height`, deliberately:
     on iOS the two agree about the URL bar and disagree about the keyboard,
     and a shell that resized itself every time somebody tapped the nights
     field would be a worse bug than this one. `pageshow` is the case that
     found it, since coming back from the background restores from bfcache
     without a `resize`. And each event measures twice, because iOS reports
     the *old* height during `orientationchange` and animates the bar away for
     a moment after a `resize`. */
  var refitAgain = null;
  /* Where `dvh` exists the stylesheet already knows the height, and all this
     has to do is make the browser recompute it -- which was the whole finding:
     the layout was stale rather than wrong.
     Writing a pixel height instead *overrides* the unit, and on iOS
     `window.innerHeight` is the layout viewport, which is the tall one. So the
     number written back was the very slack `dvh` had just removed, on every
     `resize` -- and on an iPhone that is the shell relaid out from under a
     gesture: the UI jumps and the scroll it was tracking is clamped to
     nothing. Invalidate rather than assert. Toggling `min-height` dirties
     layout without this file claiming a height of its own, and the forced
     read is the reflow the stale layout wanted; `height` in pixels stays as
     the fallback for a browser that does not know the unit, where there is no
     unit to fight. */
  var HAS_DVH = !!(window.CSS && window.CSS.supports &&
                   window.CSS.supports("height", "100dvh"));
  function fitShell() {
    if (!HAS_DVH) {
      document.body.style.height = window.innerHeight + "px";
      return;
    }
    document.body.style.minHeight = "0px";
    void document.body.offsetHeight;
    document.body.style.minHeight = "";
    void document.body.offsetHeight;
  }
  function refitShell() {
    fitShell();
    if (refitAgain) clearTimeout(refitAgain);
    refitAgain = setTimeout(fitShell, 260);
  }
  fitShell();
  ["resize", "orientationchange", "pageshow"].forEach(function (name) {
    window.addEventListener(name, refitShell);
  });

  /* A READOUT FOR A PHONE THIS REPO CANNOT DRIVE.
   *
   * `tests/test_layout.py` drives Chromium, which is the right tool for every
   * claim about geometry that a desktop engine can settle. It cannot settle
   * one about iOS: `dvh`, `innerHeight` and the browser's own toolbar
   * disagree there in ways nothing here reproduces, and three fixes were shipped
   * against it on reasoning rather than on a number. This is the probe rule
   * applied to a device instead of a page -- read what came back, then fix.
   *
   * Off unless the URL asks (`?diag`), so it ships as nothing on a page
   * nobody has asked it of. It states what it reads and never what it thinks:
   * the build stamp first, because a phone hides the build line and a stale
   * cache explains every "still broken" for free. */
  if (/(^|[?&])diag(&|=|$)/.test(location.search)) {
    var diag = document.createElement("div");
    diag.id = "diag";
    diag.setAttribute("aria-hidden", "true");
    diag.style.cssText = "position:fixed;left:0;right:0;bottom:0;z-index:9999;" +
      "background:#000;color:#0f0;font:11px/1.35 ui-monospace,monospace;" +
      "padding:6px 8px;white-space:pre;pointer-events:auto";
    diag.addEventListener("click", function () { diag.remove(); });
    document.body.appendChild(diag);
    var events = { resize: 0, orient: 0, scroll: 0 };
    ["resize", "orientationchange"].forEach(function (name) {
      window.addEventListener(name, function () {
        events[name === "resize" ? "resize" : "orient"] += 1;
      });
    });
    var shellFor = function () { return document.querySelector(".shell"); };
    shellFor().addEventListener("scroll", function () { events.scroll += 1; },
                                { passive: true });
    var vv = window.visualViewport;
    var paint = function () {
      var shell = shellFor();
      var body = document.body.getBoundingClientRect();
      diag.textContent =
        "built " + (D.meta.built || "?") + "  (tap to dismiss)\n" +
        "inner " + window.innerWidth + "x" + window.innerHeight +
        "  visual " + (vv ? Math.round(vv.width) + "x" + Math.round(vv.height) +
                            " off" + Math.round(vv.offsetTop) : "none") +
        "  dvh " + (HAS_DVH ? "yes" : "no") + "\n" +
        "body " + Math.round(body.width) + "x" + Math.round(body.height) +
        "  style.h '" + document.body.style.height + "'" +
        "  page " + Math.round(window.scrollY) + "\n" +
        "shell " + Math.round(shell.clientHeight) + " of " +
        Math.round(shell.scrollHeight) + "  at " + Math.round(shell.scrollTop) +
        "  rows " + document.getElementById("cards").children.length + "/" +
        document.getElementById("body").children.length + "\n" +
        "events resize " + events.resize + "  orient " + events.orient +
        "  scroll " + events.scroll;
      requestAnimationFrame(paint);
    };
    requestAnimationFrame(paint);
  }

  /* Written on every draw by countRail(), which runs from afterDraw -- so they
     are looked up here rather than beside the rest of the view wiring, which
     is declared after the first draw has already happened. */
  var railTripsCount = document.getElementById("navTripsCount");
  var railSaleCount = document.getElementById("navSaleCount");

  var euro = new Intl.NumberFormat("en-IE", {
    style: "currency", currency: D.meta.currency,
    minimumFractionDigits: 0, maximumFractionDigits: 0
  });

  var state = {
    sort: "start", dir: 1,
    months: new Set(), ports: new Set(), sites: new Set(), boats: new Set(),
    /* The certification and logged dives a trip demands, as one value: the
       two halves are a single question -- "can I book this" -- and filtering
       them apart lets a reader clear the level and still be turned away at
       the dock by the dive count. */
    entry: new Set(),
    /* Who sells the sailing. Empty means every seller, like every other chip
       bank here -- a filter nobody has touched must never be a filter. */
    sellers: new Set(),
    nightsMin: null, nightsMax: null, hideSoldOut: false,
    /* Only sailings a seller has marked down. Off by default, like every
       other filter here: a page that opened showing 268 of 1,122 rows would
       be answering a question nobody asked. */
    onSaleOnly: false,
    /* Which of the three views is on screen -- "trips", "sale" or "history".
       Set by showView() before the first draw, and read by passes(): the sale
       view is not a copy of the table, it is this table with the markdown
       filter held on, and holding it here rather than by pressing the chip
       means one answer to "is the sale filter on" instead of two. */
    view: null,
    toggles: {},
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

  /* The same trip as PADI Travel bills it, or null.
   *
     Two things have to be true and they fail independently. PADI must sell
     that date -- 601 of 892 -- and its fee book for the trip must be complete,
     every charge it names classified and priced. 169 rows clear both. Where
     only the first holds there is a second price and no second total, and the
     page says exactly that rather than comparing a bill against half of one.

     Deliberately the same `metricsOf` that sums liveaboard.com's side. Two
     adders would drift, and the one thing this column must never do is show a
     difference that is an artefact of how the two were summed. */
  function padiMetricsFor(dep) {
    var itin = D.itineraries[dep.itinerary_id];
    if (!dep.padi_base_line || !itin.padi_lines || itin.padi_overlap) return null;
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
      /* Tips sit outside the total, because both sellers file them under
         Optional and this page reads the seller's own block rather than
         overruling it -- a mandatory $50 tip and one you choose the size of
         are different charges, and only the operator can say which it bills.
         One listed as Required would arrive as `mandatory` and be counted like
         any other, with no special case here.

         So the marker is on whether tips exist and are outside the arithmetic,
         priced or not: a stated EUR 120 the total does not carry is exactly as
         missing from it as an unstated one. `included` is the operator saying
         it is already covered, which needs no marker. */
      if (line.code === "gratuities") {
        tips = line.included ? "included"
             : line.tier === "mandatory" ? "counted"
             : line.has_price ? "extra" : "unpriced";
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
     `.lav` is liveaboard.com's bill and `.padi` is PADI Travel's, the second
     present only where its own disclosure is complete. Named for their sellers
     rather than as a source and a comparison to it: liveaboard.com was read
     first and PADI second, which is a fact about this project's history and
     not one about either seller, and it has no business inside code that
     decides a price (#139).

     Where both exist the page prints the **span**, not one of them. Picking the
     lower was the obvious thing and it was wrong in a way that took a fleet
     owner to see: the two sellers do not disclose at the same resolution.
     liveaboard.com publishes one fee figure per vessel; PADI publishes one per
     itinerary, and its numbers move with the trip -- Tala's northern week is
     €100 against its deep-south week at €280, where liveaboard.com's is €200
     for both. Taking the cheaper therefore takes the flat figure exactly where
     it understates and the per-trip one where the flat one overstates, and
     pulls the published number toward the low side at both ends. On a site
     whose argument is that advertised prices are too low, that is the house
     error.

     So neither is "the" price. The columns print both sellers' figures, and
     **each end is one seller's whole bill** -- the cheaper seller's Advertised
     and Mandatory fees make the low end, the dearer seller's make the high
     end. Not the minimum of each part: one seller's berth is sometimes the
     cheaper while the other's fee book is, and min(base) + min(fees) is then a
     bill neither seller quoted. Measured before it was believed -- taking the parts
     independently broke the sum on 74 of the 108 rows where both bills add
     up. That ordering is why Advertised and Mandatory fees print through
     `sellerPair` and not `sellerSpan`: they follow the Total's seller order
     rather than running low to high, and on 27 rows that order is backwards.

     `cheaper` still says who is lower -- named `"liveaboard"` or `"padi"`,
     because a value naming one seller and calling the other `"ours"` is the
     project's reading order asserted as a relationship. Under `PADI_SAME` they
     are one price and the span collapses. */
  function best(row) {
    /* No total where the disclosure names one charge twice -- `fee_overlap`
       joins `mandatory_known` rather than replacing it, because they are two
       ways for a bill not to add up and the sentences differ. Withheld here
       and not in `metricsFor`: `row.lav` is an object the Nitrox and Places
       columns read fields off, so nulling it there empties the table. */
    var lav = row.d.mandatory_known && !row.d.fee_overlap ? row.lav : null;
    var padi = row.padi;
    if (!lav && !padi) return null;
    if (!lav || !padi) {
      var only = lav || padi;
      return {
        bill: only, cheaper: lav ? "liveaboard" : "padi", varies: 0, both: false,
        lo: only.total, hi: only.totalMax,
        baseLo: only.base, baseHi: only.base,
        /* The berth is one figure and the ranges sit on the fees, so a ranged
           total is a ranged fee bill. Printing the midpoint here beside a
           ranged Total would put the difference nowhere. */
        laterLo: only.later, laterHi: only.totalMax - only.base
      };
    }
    var gap = row.lav.total - row.padi.total;
    var same = Math.abs(gap) < PADI_SAME;
    var cheap = gap <= 0 ? row.lav : row.padi;
    var dear = gap <= 0 ? row.padi : row.lav;
    var top = cheap.totalMax > dear.totalMax ? cheap : dear;
    return {
      /* The bill the expanded row leads with, and the one the proportion bar
         is drawn from: the cheaper, because a bar and a fee table have to come
         from one coherent bill even when the headline is a span. */
      bill: cheap,
      cheaper: same ? "same" : gap < 0 ? "liveaboard" : "padi",
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
    /* Each seller under its own reading date, rather than one date appended to
       a list of them: the two books are read by two jobs two days apart, and a
       sale is exactly the kind of claim that expires between them. */
    var who = namedReadings(d.sale.sellers);
    /* No percentage has two causes — the discounting seller is not the one
       whose fare this row prints, or the markdown rounds to nothing — and the
       row does not carry which. So the tooltip states what is true of both
       rather than picking one and being wrong on the other. */
    if (!d.sale.pct) {
      return '<span class="sale-mark" title="' +
        esc("On sale through " + who.join(" and ") +
            ", with no percentage stated against the fare on this row") +
        '">on sale</span>';
    }
    /* "Down from" only where there is a figure to be down from. `was` is
       withheld when the seller stated no currency, and a converted amount
       built from a missing one printed "€NaN" into the tooltip. */
    var title = (d.sale.was ? "Down from " + eur(d.sale.was) + ", per " : "Marked down by ") +
      who.join(" and ");
    return '<span class="sale-mark" title="' + esc(title) + '">−' + d.sale.pct + "%</span>";
  }

  /* Why the Advertised column is one figure rather than two.

     A single number there means three different things, and until this mark
     they printed identically: both sellers quote it (155 rows), only one of
     them has been asked (513 rows), or PADI quotes the sailing too but does not
     disclose enough of its own bill for the row to price it, so its berth price
     exists and no column shows it (454 rows). "€2,371" agreed by two sellers
     and "€2,371" from the only one anybody asked are not the same claim, and a
     reader had to reach column seventeen to tell them apart.

     Said on hover, not in a column and no longer in a word beside the figure:
     three columns had each grown a `2 sellers` mark for three different facts,
     so one row could state the phrase twice and mean something else each time.
     The third case has nothing on it, which is what the rest of the page means
     by one seller.

     Nothing here is recomputed: `d.padi` and `row.padi` are the same two keys the
     Total and the Seller column branch on. A second derivation would be a
     second answer to "who priced this". */
  function advertisedNote(d, row) {
    if (d.padi == null) return "";
    if (row && row.padi) {
      /* Both bills add up, so the pair beside this is genuinely two sellers'
         and the fee panel shows each of them. Nothing to add here. */
      return "";
    }
    var same = Math.round(d.padi) === Math.round(d.base);
    return same
      ? "PADI Travel advertises this berth at the same price. It does not " +
        "publish a complete set of required extras for this trip, so there " +
        "is a second price and no second total — open the row for both."
      : "PADI Travel advertises this berth at " + eur(d.padi) + ". It does " +
        "not publish a complete set of required extras for this trip, so " +
        "the two berth prices are not a comparison of two bills — open the " +
        "row for what each seller does state.";
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
  /* Spelt out, for the history view's day headings: those are days the refresh
     ran rather than days a boat sails, and a log wants a date a reader can
     place in a year. */
  var MONTH_NAMES = ["January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"];

  /* "2027-05-01" printed as "01 May". Formatted from the string rather than
     through Date, which would read the ISO date as UTC midnight and print the
     day before for anyone west of Greenwich. */
  function shortDate(iso) {
    var p = String(iso).split("-");
    if (p.length !== 3) return esc(iso);
    return p[2] + " " + MONTHS[+p[1] - 1];
  }

  /* With the year, for the history view: those dates are days the refresh ran
     rather than days a boat sails, and a season's worth of departures all fall
     in one year where a log does not. */
  function longDate(iso) {
    var p = String(iso).split("-");
    if (p.length !== 3) return esc(iso);
    return (+p[2]) + " " + MONTH_NAMES[+p[1] - 1] + " " + p[0];
  }

  /* Depart and Return in one cell. The month is printed once where the trip
     does not cross one — 1,067 of 1,122 — and twice where it does, because
     "29–05 Jul" is a range running backwards and a reader has to stop and
     work out that it is not. */
  function dateSpan(start, end) {
    var a = String(start).split("-"), b = String(end).split("-");
    if (a.length !== 3 || b.length !== 3) return esc(start);
    return a[1] === b[1]
      ? a[2] + "–" + b[2] + " " + MONTHS[+a[1] - 1]
      : a[2] + " " + MONTHS[+a[1] - 1] + " – " + b[2] + " " + MONTHS[+b[1] - 1];
  }

  /* What the bar under the total said in words, which is the half of it worth
     keeping (#148).

     The bar encoded two different things. Its *length* was this total against
     the dearest total on screen -- a comparison down the column, which the
     table's own sort already answers, and nothing to replace. Its two
     *segments* were the advertised fare's share and the required extras on top
     of it, which is the whole argument of this site expressed as a proportion
     and the part a reader loses if the graphic simply goes. So the split comes
     back as the two figures it was drawn from.

     Money rather than a percentage: "+42%" is more compact and re-introduces
     the proportion that is being removed. */
  var SPLIT_TITLE = "The advertised berth price, then the required extras " +
    "added to it — the two figures this total is the sum of. A range sits on " +
    "the extras and never on the berth, so both ends are a whole bill.";
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

  /* The day a seller's book was read. Two crawls, two days, and the date is
     the whole of what makes a count or a markdown a claim rather than a fact. */
  function sellerRead(seller) {
    return seller === 1 ? D.meta.padi_berths_read : D.meta.berths_read;
  }
  function readOn(block) { return sellerRead(block[BLOCK_SELLER]); }

  /* "liveaboard.com (28 Aug) and padi.com (30 Aug)" — every seller in a list,
     each under its own reading date.

     One date over two sellers dates half of them wrong. The berth counts have
     obeyed that since they were published; the sale marks did not, and stamped
     the cabin crawl's day on 124 rows whose evidence is partly PADI's book from
     two days later and on 2 that are entirely it. Same fact, same rule, and now
     the same function. */
  function namedReadings(sellers) {
    return (sellers || []).map(function (s) {
      var day = sellerRead(s);
      return (SELLER_NAMES[s] || "a seller") + (day ? " (" + shortDate(day) + ")" : "");
    });
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

  /* The entry bar as one phrase, and as one number to sort it by.
   *
     The bar is two facts -- a certification and a count of logged dives -- and
     the fleet spreads across both. Printed as a level alone it is three values
     with 47% of the rows on one of them; printed as the pair it is seventeen
     with 26% on the largest, and the difference is not decoration: an *Open
     Water* week demanding 30 logged dives turns away a diver that an *Advanced*
     week demanding none would take. Neither half answers "can I book this" on
     its own.

     `D.entry_bars` carries the vocabulary and the order together, so the
     certification's short name and the dives a level implies are the dataset's
     words rather than a second copy written here. See taxonomy.DIVER_LEVEL_BARS.

     Two facts, one string, because the string is what the reader compares --
     but the *rank* keeps them apart: level first, then dives, which is
     `DIVER_LEVEL_ORDER`'s own claim about which bar is the harder one rather
     than an arithmetic this file invented. */
  var BAR_RANK = {}, BAR_CERT = {}, BAR_DIVES = {};
  (D.entry_bars || []).forEach(function (bar, n) {
    BAR_RANK[bar[0]] = n; BAR_CERT[bar[0]] = bar[1]; BAR_DIVES[bar[0]] = bar[2];
  });

  /* The greater of what the trip states and what its level already implies.
     Never the smaller: a safety requirement is not softened here, and an
     `advanced_50` trip that stated 20 would otherwise print a bar 30 dives
     below the certification it also demands. */
  function entryDives(req) {
    return Math.max(req.min_logged_dives || 0, BAR_DIVES[req.min_level] || 0);
  }

  /* The certification's short form. "Open Water" and "Advanced" are what
     BAR_CERT holds -- the shorthand every operator's own listing writes,
     already shorter than the card's full name -- and the column, the filter
     chips and the expanded row all read this one function, so there is one
     spelling rather than three that could drift apart.

     Abbreviated further still, to "OW" and "ADV": the Entry bar column sits
     among the money columns, where every character is width the Total does
     not get, and the full word is one hover away in the expanded panel. The
     word "dives" goes with it -- "ADV + 50" says the same thing "Advanced + 50
     dives" did, in a fifth of the space. */
  var CERT_SHORT = { "Open Water": "OW", "Advanced": "ADV" };
  function entryText(itin) {
    var req = itin.requirements;
    if (!req || !req.min_level || BAR_CERT[req.min_level] === undefined) return "";
    var dives = entryDives(req);
    var cert = BAR_CERT[req.min_level];
    return (CERT_SHORT[cert] || cert) + (dives ? " + " + dives : "");
  }

  /* -1 for a trip stating no bar, so it sorts to the same end as an unstated
     guest count or dive count does. Every one of the 402 itineraries states
     one today; the branch is here because "nobody said" must never sort as
     though somebody had said "nothing required". */
  function entryRank(itin) {
    var req = itin.requirements;
    if (!req || !req.min_level || BAR_RANK[req.min_level] === undefined) return -1;
    return BAR_RANK[req.min_level] * 1000 + entryDives(req);
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

     The operator is off the page entirely now, and this comment used to say
     otherwise: "it stays on every itinerary in the dataset and in the expanded
     row". Only the first half was ever true. Nothing printed the company, and
     the field went on shipping to every visitor 402 times a page -- so the
     sentence described a fallback that did not exist, which is how a fact gets
     withdrawn without anyone deciding to withdraw it.

     It is a decision now: a diver picks the boat. The filter bank went because
     a company with six boats returned six boats' worth of rows and no way to
     tell them apart; the search box that reached it went too, being a second
     way to ask what the chips ask, redrawing the table on every keystroke, for
     a question asked far less often than "which boat"; and a per-operator
     score went before either, for reading as a league table. */
  var BOATS = tally(function (i) { return [i.boat]; });

  /* The entry bar, and the one bank that is not ordered by how many rows carry
     it. `tally` sorts by count because a port or a boat has no order of its
     own; a bar does -- least demanding to most -- and it is an order the reader
     is reading the bank *along*, looking for the last chip they still clear.
     Sorted by count, "Advanced + 50 dives" opened the bank and "Open Water"
     sat fourth, which reads as a list of popular options rather than as a
     ladder. Ranked by `entryRank`, so the bank and the column's sort agree. */
  var ENTRY = (function () {
    var n = {}, rank = {};
    D.departures.forEach(function (dep) {
      var itin = D.itineraries[dep.itinerary_id];
      var text = entryText(itin);
      if (!text) return;
      n[text] = (n[text] || 0) + 1;
      rank[text] = entryRank(itin);
    });
    return Object.keys(n).sort(function (a, b) {
      return rank[a] - rank[b] || a.localeCompare(b);
    }).map(function (v) { return { id: v, n: n[v] }; });
  })();

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
    /* Read, and self-contradictory. Third rather than folded into `partial`:
       that sentence says the operator stated no required extras, and this one
       stated them twice. */
    if (dep.fee_overlap) return ["overlap", "counted twice"];
    return ["full", "required stated"];
  }

  /* Why a row has no fee figure, in the words the Mandatory fees cell hovers
     and the Total's dash borrows. Two states and they are not the same
     failure: one is a page nobody has read, the other is a page that was read
     and named only optional extras. Neither is "no fees" -- every Egyptian
     liveaboard pays park and port dues -- and the sentences say so, because a
     blank in a money column reads as zero unless something stops it. */
  var FEE_WHY = {
    none: "Nobody has read this vessel's fee panel yet, so its required " +
          "extras are unknown. Every Egyptian liveaboard pays park and port " +
          "fees, so this is missing information rather than a trip without " +
          "them.",
    partial: "The operator publishes only optional extras, so its required " +
             "ones are unstated. They are still charged — bundled into the " +
             "berth or collected at the dock — and the listing does not say " +
             "which, so no total is claimed here.",
    overlap: "The seller bills one of these charges twice — once on its own " +
             "line and once inside a bundle that names it. Both are published " +
             "as required, and nothing in the listing says which is the real " +
             "one, so adding them up would state a price nobody quotes. The " +
             "fee lines are all here; the total is not."
  };

  /* Sixteen columns became twelve, and not one fact went with the four.
   *
     Depart and Return were two columns printing "01 May" and "08 May" over
     one another's heads; Guests was 45px of digits filed among the route;
     From and To were two columns of city names repeated down 1,122 rows. Each
     of them is a second fact about a column that was already there, so each
     is a second line inside it -- the dates with the nights between them, the
     boat with the deck it sleeps, the trip with the harbours it runs between.
     Twelve columns fit a 1440px window whole, which is what the four bought:
     the Total is on screen at rest on a laptop with nothing folded away.

     What is lost is a sort on each of the four, and that is the price. Return
     sorts with Depart on every trip of one length; the port is what the
     Departs from bank filters on, which is the question a reader actually
     asks of it; and a guest count nobody can sort on is still a guest count
     printed on every row.

     `zone` is what the group header above these reads -- see `groups()`. It
     is a property of the column rather than of its position, so an order that
     moves the price block in front of the descriptive columns relabels the
     bands rather than mislabelling them. */
  var COLS = [
    /* Sorted on the ISO string and printed short. Every departure here is in
       one season, so the year is the same four characters on 1,122 rows and
       repeating it crowds out the day and month, which is the part being
       compared. The heading still names the season.

       "01–08 May" collapses to one span where the trip does not cross a
       month, which is 1,067 of them; the other 55 print both months, because
       "29–05 Jul" would be a date range running backwards. */
    { k: "start", t: "Dates", cls: "when", zone: "when",
      hint: "Departure and return, and the nights between",
      v: function (d) { return d.start; },
      show: function (d) {
        return '<span class="d-span">' + dateSpan(d.start, d.end) + "</span>" +
          '<span class="sub">' + d.nights + (d.nights === 1 ? " night" : " nights") +
          "</span>";
      } },
    /* Berth price is per person, so the second line says whether you are
       buying into a boat of twelve or of thirty-four. Null where the
       description does not state it — about half the fleet, which is a gap in
       the scrape rather than an operator declining to say, and the line says
       which. */
    { k: "boat", t: "Boat", cls: "boat", zone: "when",
      v: function (d, i) { return i.boat; },
      show: function (d, i) {
        return '<span class="b-name" title="' + esc(i.boat) + '">' + esc(i.boat) +
          "</span>" + '<span class="sub">' +
          (i.guests == null ? "guests not stated" : i.guests + " guests") +
          "</span>";
      } },
    /* The trip, and under it the two harbours. They were From and To, and
       what a reader wants from them is the route rather than two cells to
       compare across: "Hurghada → Port Ghalib" is a one-way run and
       "Hurghada · return" is the other 900 of them, said in the width one of
       the two columns used. */
    { k: "trip", t: "Trip", cls: "trip", zone: "trip",
      v: function (d, i) { return tripName(i); },
      show: function (d, i) {
        var name = tripName(i);
        return '<span class="t-name" title="' + esc(name) + '">' + esc(name) +
          "</span>" + '<span class="sub">' + esc(i.port_from) +
          (i.port_to && i.port_to !== i.port_from
            ? " → " + esc(i.port_to) : " · return") + "</span>";
      } },
    { k: "sites", t: "Dive sites", cls: "sites", zone: "trip",
      v: function (d, i) {
        return (i.dive_sites || []).join(", ") || i.region || "—";
      },
      show: function (d, i) {
        if (i.dive_sites && i.dive_sites.length) {
          /* Separated by a middle dot rather than commas. The reefs are a set
             and not a sentence, and at 11px a comma between two place names
             reads as part of the second one. */
          return '<span title="' + esc(i.dive_sites.join(", ")) + '">' +
            esc(i.dive_sites.join(" · ")) + "</span>";
        }
        /* The operator named no reef. Their own word for the region, marked as
           the weaker statement it is. */
        if (i.region) return '<span class="region">' + esc(i.region) + ", sites not named</span>";
        return '<span class="dim">—</span>';
      } },
    /* The entry bar, which decides whether a row is a trip you can book at all.
     *
       It had a column of its own once and lost it, for three reasons recorded
       where the expanded row prints the same fact. The first is answered by
       printing the pair rather than the level: it is no longer "the same three
       words on most rows" (seventeen values, the largest 26% of rows, against
       three and 47%). The second was that its disagreement marker looked like
       the Disclosure pill beside it; there is no marker at all now, and the
       sentence it stood for is in the panel the bar opens.

       The third was width, and that is answered by where it sits: after Dive
       sites, so on every layout below 1700px it falls behind the price block
       and the Total's position is unchanged. See ORDER.

       The fact itself is the operator's safety claim, so the cell states it
       and never softens it; where the two sellers disagree the stricter is
       shown and the mark says so. */
    { k: "entry", t: "Entry bar", short: "Entry", cls: "entry-col", zone: "trip",
      v: function (d, i) { return entryRank(i); },
      show: function (d, i) {
        var text = entryText(i);
        if (!text) return '<span class="dim">—</span>';
        /* No disagreement marker beside the bar. It said `2 sellers` on 25
           rows in 120 -- the same two words the two money columns were using
           for two other facts -- and the sentence it abbreviated is already
           printed in full, under its own heading, in the panel this button
           opens. A word on the row that only points at the panel is a word the
           panel does not need.

           The stated requirement in full, on hover or click. It was the head
           of the fee dropdown, which is the wrong home for it twice over: it
           is not a fee, and it was reachable only by opening a bill (#149). */
        return '<button class="entry-open" type="button" data-entry="' + esc(i.id) +
          '" aria-expanded="false" aria-haspopup="dialog">' + esc(text) +
          '<span class="caret" aria-hidden="true">▾</span></button>';
      } },
    /* The berth price of whichever seller's bill this row is printing. It read
       liveaboard.com's unconditionally, which on a row won by PADI put two
       sellers' numbers in one arithmetic: Advertised plus Mandatory fees no
       longer made the Total, and a reader checking the sum would find the page
       wrong rather than find two sellers. One row, one bill. */
    { k: "base", t: "Advertised", num: true, cls: "money", zone: "bill",
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
        var figure = b ? sellerPair(b.baseLo, b.baseHi) : eur(d.base);
        /* The second seller's berth price, on the figure it qualifies rather
           than in a word beside it. It read `2 sellers` on 454 rows, which is
           two words of chrome on two fifths of the table to say something the
           expanded row says in a sentence -- and said it in the same words as
           the Total's own marker, so one row could carry the phrase twice
           about two different facts. */
        var why = advertisedNote(d, row);
        if (why) {
          figure = '<span title="' + esc(why).replace(/"/g, "&quot;") + '">' +
            figure + "</span>";
        }
        /* The sale tag stays under the figure rather than beside it: inline it
           set this column's width on every row in the table -- 164px for a
           figure that needs 96 -- and it is not read down the column the way
           the price is. */
        var marks = saleTag(d);
        return figure + (marks ? '<span class="marks">' + marks + "</span>" : "");
      } },
    /* The cheapest bill anyone quotes for this sailing, not this site's own.
     *
       Two sites sell the same berth on the same boat on the same day and they
       do not agree -- 84 of the 179 trips where both fee books can be added up
       differ, the widest by €140. Printing one seller's number
       as "Total" was answering "what does liveaboard.com charge" on a page
       whose question is "what does this trip cost". Where the second seller's
       disclosure is complete and cheaper, its bill is the one printed, marked
       so nobody mistakes which. */
    { k: "total", t: "Total", num: true, cls: "cost", zone: "bill",
      /* Sorted on the low end, so "cheapest first" still means what it says. */
      v: function (d, i, m, row) { var b = best(row); return b ? b.lo : Infinity; },
      show: function (d, i, m, row) {
        var b = best(row);
        /* No total, and the dash says why on hover. The Mandatory fees column
           beside it prints the reason in words; a bare dash here used to send
           a reader to a third column to find out, and now sends them one cell
           left. */
        if (!b) {
          return '<span class="dim" title="' + esc(FEE_WHY[disclosure(d)[0]]) +
            '">—</span>';
        }
        m = b.bill;
        /* The two figures the total is the sum of, under it.
         *
           Each end is a whole bill, so the split is a span exactly where the
           total is one -- `best` carries `baseLo/baseHi` and `laterLo/laterHi`
           for that reason, and the ranges sit on the fee lines rather than on
           the berth. Printing one split under a ranged total would be claiming
           a precision the row does not have; printing two spans claims only
           what the two bills say.

           Drawn wherever there is a total at all, which is the rule the bar
           had: `best` is null unless one seller's required extras are stated,
           and that case has already returned the dash above. So a zero here is
           an operator that adds nothing rather than a trip nobody read -- the
           same reason an included fee stays in the breakdown at zero. */
        var split = '<span class="split" title="' + SPLIT_TITLE + '">' +
          sellerSpan(b.baseLo, b.baseHi) + ' <i>+</i> ' +
          sellerSpan(b.laterLo, b.laterHi) + '</span>';
        /* The span *is* the answer, and it no longer says so in words.
           €1,757–2,057 is not an operator quoting a range, it is two sellers
           who do not agree -- but the two words that used to say that sat on
           the row and read as a value of their own, in the same phrase the
           Advertised column used for a different fact. What names the cause
           now is the row itself: the Seller column says which end is whose and
           the fee panel prints both bills. This is the sentence, on the figure
           it qualifies. */
        var why = b.both && b.cheaper !== "same"
          ? "Two sellers price this sailing and they differ by €" +
            Math.round(b.varies).toLocaleString("en-IE") +
            ". Both are shown; the Seller column says which end is whose. " +
            "They disclose at different resolutions — liveaboard.com states " +
            "one fee figure per boat, PADI Travel one per itinerary — so " +
            "neither end is the price."
          : "";
        return "<b" + (why ? ' title="' + esc(why).replace(/"/g, "&quot;") + '"'
                            : "") + ">" + sellerSpan(b.lo, b.hi) + "</b>" +
          (m.tips === "unpriced" || m.tips === "extra"
            ? '<span class="plus" title="The operator lists crew tips under ' +
              'its own Optional extras, so they are not in this total.">' +
              ' + tips</span>'
            : "") +
          split;
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
    { k: "perdive", t: "Per dive", num: true, cls: "perdive", zone: "bill",
      /* Divides the total the row prints -- the cheaper seller's -- so the two
         money columns cannot disagree about what a dive costs on one row. */
      v: function (d, i, m, row) {
        var b = best(row);
        return i.dives > 0 && b ? b.bill.total / i.dives : -1;
      },
      show: function (d, i, m, row) {
        var b = best(row);
        if (b) m = b.bill;
        /* Two silences, and the cell says which. `dives_read` means somebody
           opened this trip's own itinerary and the seller left the count
           blank — one trip of 352, Aphrodite's North Dolphins, a snorkelling
           week whose entry bar is "No Certificate needed". Everywhere else the
           zero means no fragment has been read at all: 74 itineraries, 41 of
           them on boats liveaboard.com publishes no vessel page for.

           Same rule as `fees_known` two columns over — no fee lines means
           nobody looked, not that there are none — and it matters for the
           same reason: neither can produce a price per dive, but only one of
           them is a fact about the trip. */
        if (!i.dives) {
          return i.dives_read
            ? '<span class="dim" title="The seller published this trip&#39;s ' +
              'own itinerary and stated no dive count in it.">none stated</span>'
            : '<span class="dim" title="No source read for this trip publishes ' +
              'a dive count. Assuming one would divide the bill by a number ' +
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
      },
      /* The same figure where a phone puts it: inside the money block, under
         the total it is derived from.
       *
         On the card this was a bare `€95` on the meta line, one gap away from
         `+€400 → 500` and in the same weight -- two euro figures, neither
         named, and the only thing telling them apart was a column heading that
         a phone does not draw. And `↓ 17+` carried its meaning in a `title`,
         on the one device that cannot open one.

         So it moves to the money block and says what it is in words. It is
         the only column that draws differently on a card, and it declares that
         here rather than in `renderCards`, so the two renderings stay one
         column's business: the value is `v` either way and there is nowhere
         for a second reading of the data to appear.

         The two silences survive the move, because they are the point of the
         cell -- but they need a subject in here. Under a total, "not stated"
         alone reads as a fact about the money. */
      card: function (d, i, m, row) {
        var b = best(row);
        if (!i.dives) {
          return '<span class="perline dim" title="' + (i.dives_read
            ? "The seller published this trip&#39;s own itinerary and stated " +
              "no dive count in it."
            : "No source read for this trip publishes a dive count. Assuming " +
              "one would divide the bill by a number nobody stated.") +
            '">dives: ' + (i.dives_read ? "none stated" : "not stated") +
            "</span>";
        }
        if (!b) return "";
        /* The count is the fewest the operator states, so the figure is a
           ceiling -- said as "17+" rather than as an arrow, for the same
           reason the words are here at all. */
        return '<span class="perline" title="' + i.dives + '+ dives — the ' +
          'fewest this operator states for the week. Boats that cross ' +
          'further, or spend longer in the parks where night dives are not ' +
          'allowed, fit fewer in."><b>' + eur(b.bill.total / i.dives) +
          "</b> a dive <span class=\"dim\">\u00b7 " + i.dives + "+</span></span>";
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
    { k: "nitrox", t: "Nitrox", num: true, cls: "nitrox", zone: "bill",
      v: function (d, i, m, row) {
        var b = best(row); if (b) m = b.bill;
        return !m.nitrox ? 9e9 : m.nitrox.included ? -1
             : m.nitrox.price != null ? m.nitrox.price : 9e8;
      },
      show: function (d, i, m, row) {
        var b = best(row); if (b) m = b.bill;
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
    /* The required extras, and where there are none to print, why.
     *
       This cell was a dash and a column called Disclosure two places to its
       right carried the reason -- "not looked at" or "optional only" -- as a
       pill of its own. Two cells for one fact, and the one a reader lands on
       while reading the bill was the mute one: a dash in a fee column reads as
       "no fees", which is the opposite of what it means. Every Egyptian
       liveaboard pays park and port fees, so an empty disclosure is missing
       information rather than a free trip.

       So the reason is printed here, in the column it is about, and the
       Disclosure column is gone. The two states stay distinct because they are
       different failures: nobody read the vessel's fee panel, or the operator
       published a panel that names only optional extras. */
    { k: "later", t: "Mandatory fees", short: "Mandatory", num: true, cls: "mfees", zone: "bill",
      /* Unstated sorts last, next to nothing: an unread trip is not a cheap
         one, and it must not collide with a genuine zero. */
      v: function (d, i, m, row) {
        var b = best(row);
        return b ? Math.min(b.laterLo, b.laterHi) : Infinity;
      },
      show: function (d, i, m, row) {
        var b = best(row);
        if (!b) {
          var why = disclosure(d);
          return '<span class="unstated ' + why[0] + '" title="' +
            esc(FEE_WHY[why[0]]) + '">' + why[1] + "</span>";
        }
        /* The figure opens the bill it is the sum of, on hover or click, which
           is where a `+` column and a full-width dropdown used to be (#149).
           The dropdown pushed every row below it down to answer a question
           about one row, and it cost 26px of pinned width on every row that
           was never opened.

           A button rather than a cell with a handler, so it is reachable by
           keyboard without anything being added for that: the dropdown's `+`
           was a button and this may not be a step back from it. */
        return '<button class="fees-open" type="button" data-fees="' + esc(d.id) +
          '" aria-expanded="false" aria-haspopup="dialog">' +
          '<span class="later">+' + sellerPair(b.laterLo, b.laterHi) + "</span>" +
          '<span class="caret" aria-hidden="true">▾</span></button>';
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
    { k: "availability", t: "Places", cls: "places", zone: "seats",
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
       This replaced a column that measured PADI's berth price against
       liveaboard.com's and called the gap "vs PADI". It was comparing the wrong halves. Both
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
     * Both named, always. There are two sellers here and neither of them is
     * the house: liveaboard.com was read first and PADI Travel second, which
     * is a fact about this project rather than about either source. A label
     * that names one and leaves the other as the unmarked default -- and
     * "listing" was that default, printed on liveaboard.com rows and on no
     * PADI row ever -- hands a visitor to a site the page never named (#139).
     *
     * PADI's link is per boat and printed only where PADI prices *this date*.
     * A vessel having a PADI page says nothing about whether a given sailing
     * is on its calendar, and a link landing on a calendar without the trip on
     * it is worse than no link. */
    { k: "source", t: "Seller", cls: "source", zone: "seats",
      v: function (d, i) { return d.booking_url || i.source_url || ""; },
      show: function (d, i) {
        var links = [];
        var url = d.booking_url || i.source_url;
        var padi = d.padi != null ? (D.padi_urls || {})[i.boat_id] : null;
        if (url) {
          /* Which seller this url belongs to, never a generic word for it.
             230 sailings are sold only by PADI -- liveaboard.com does not list
             the date, and on 22 boats does not sell berths at all -- so their
             single url points at travel.padi.com; every other single url
             points at liveaboard.com. Both cases are one seller reached from
             this column and both say which. */
          links.push('<a href="' + esc(url) + '" target="_blank" rel="noopener">' +
            (d.padi_only ? "PADI" : "liveaboard") + " ↗</a>");
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
     the whole table was. Identity first, then everything the money is for,
     then the money, then the provenance a visitor only wants once they care.

     Anything missing here is appended rather than dropped, and says so, because
     a column that silently vanished would be a fact the page stopped
     publishing.

     Twelve columns come to 1,290px, so this order fits a 1440px window whole
     -- which is what merging Return, Guests, From and To into their
     neighbours' second lines bought. See COLS. */
  var ORDER = [
    "start", "boat", "trip", "sites", "entry",
    "base", "nitrox", "later", "total", "perdive",
    "availability", "source"
  ];

  /* The same columns wherever there is not room for the reading order above.
     Identity, then the money, then everything the money is for.

     The wide order reads as a bill and puts the Total last, which is right on
     paper and expensive on screen: with Trip, Dive sites and Entry bar ahead
     of the price block, the Total's right edge lands at 1,054px inside the
     table, so it wants about 1,190px of table to be visible at all. Below
     that the price block moves in front of the descriptive columns --
     Advertised, Nitrox, Mandatory fees, Total, in that order still.

     The group band above the header follows it rather than being written out
     twice: `groups()` reads each column's `zone` off whichever order is in
     force, so this order prints THE BILL between DEPARTURE and THE TRIP
     instead of mislabelling anything. */
  var COMPACT_ORDER = [
    "start", "boat",
    "base", "nitrox", "later", "total", "perdive",
    "trip", "sites", "entry",
    "availability", "source"
  ];

  /* Two breakpoints, because the page has two different problems. `compact`
     is about how much room there is before the money column; `narrow` is
     about whether there is room for a table at all -- below it the rows are
     drawn as cards and there are no columns to order. */
  var compact = window.matchMedia("(max-width: 1180px)");
  var narrow = window.matchMedia("(max-width: 760px)");

  /* How many of the leading columns are pinned. By position, never by name:
     two pinned columns with a third between them overlap exactly as badly as
     two with a wrong offset, and naming them let that happen the moment the
     order changed.

     Two, and they are the two that identify a row: when it sails and what it
     sails on. It was four, because Return and Guests were columns of their
     own and the pinned group had to close after the vessel rather than in the
     middle of it; they are second lines inside these two now, so the group
     that has to hold still is half the width it was -- 300px rather than 354
     -- and the money is read next to both facts instead of one.

     None on a phone, where the rows are not a table. */
  function pinned() {
    return narrow.matches ? 0 : 2;
  }

  function orderColumns() {
    /* The rule that closes the pinned group goes on whichever column is last
       in it, and that changes with the breakpoint. */
    var n = pinned();
    document.body.classList.toggle("pins-2", n === 2);
    var order = compact.matches ? COMPACT_ORDER : ORDER;
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
    [["ORDER", ORDER], ["COMPACT_ORDER", COMPACT_ORDER]].forEach(function (pair) {
      if (pair[1].indexOf(c.k) < 0 && window.console) {
        console.warn("column " + c.k + " is not in " + pair[0] + "; printed last");
      }
    });
  });
  orderColumns();

  /* THE COLUMN THAT SOAKS UP A WIDE WINDOW.
   *
     `table { width:max-content; min-width:100% }` means a window wider than
     the table stretches it, and auto table layout hands the surplus to
     whichever columns are not pinned to a width. The five descriptive ones
     are pinned -- deliberately, so they cannot crowd the money out -- which
     left the money as the only thing that could grow: at 2560px the Total was
     **478px wide for a 60px figure**, Advertised 312, and every row's numbers
     drifted apart from the fees they are the sum of. The one thing this table
     exists to line up, unlined-up, on the widest screens.

     So one empty header cell at the end asks for `width:100%` and takes the
     lot. Every real column then holds the width its content needs at every
     window size, and the Total's right edge stops moving: 1226px at 1300 and
     at 2560 alike.

     It is a real column, in the body as well as the header, and that costs
     about 22KB of the payload -- 20 bytes on each of 1,122 rows. Leaving the
     body rows one cell short was tried first and is what page weight would
     prefer: HTML allows it, hover paints across the gap because it is on the
     `tr`, and it looks wrong, because the *row rule* is on the cells. The
     header's rule ran to the right-hand edge and every row's stopped 700px
     short of it, so the table appeared to end in one place and be underlined
     in another. An empty cell per row is what carries the rule out there, and
     is the price of the column being a column. The empty-state row spans it
     too, or its sentence would stop where the figures do. */
  var SPACER = '<th class="sp" aria-hidden="true"></th>';

  /* The band over the header: what the columns under it are about.
   *
     Twelve columns of one weight gave the eye nothing to land on, so the
     money -- the thing this site exists to publish -- was exactly as findable
     as the return port. Four bands now, and the bill's is tinted from the
     band down through every cell under it.

     Computed from contiguous runs of `zone` in whatever order is in force,
     never written out per order: the compact order moves the price block in
     front of the descriptive columns, and a hand-written band list would have
     to be kept in step with it or start lying. A run is a band; a zone that
     appears twice would print twice, which is why the zones are cut where the
     orders cut them. */
  var ZONE_LABELS = {
    when: "Departure", trip: "The trip", bill: "The bill", seats: "Availability"
  };
  function groups() {
    var out = [];
    COLS.forEach(function (c) {
      var last = out[out.length - 1];
      if (last && last.zone === c.zone) last.span++;
      else out.push({ zone: c.zone, span: 1 });
    });
    return out;
  }


  /* ---------- filtering and sorting ---------- */

  /* Only sailings a seller has marked down: the On sale chip, and nothing
     else. The sale *view* used to hold this down too, on the reasoning that a
     filter and a destination are two ways to ask one thing -- which was the
     wrong reading of what that view is for. It is the discount overview: which
     boats are marked down, by how much, and what moved since yesterday. Which
     departures are discounted is a table question, and the table already has a
     control for it. Asking it twice gave the rail an entry that duplicated a
     chip, and left the overview folded into a `details` above the table where
     nobody looking for it would go. */
  function saleOnly() { return state.onSaleOnly; }

  /* One predicate, so the table and the filter counts can never disagree
     about what a filter means. `skip` names a facet to ignore, which is what
     makes a chip's number the answer to "what if I picked this too?" rather
     than "what did I already pick". */
  function passes(dep, itin, skip) {
    if (skip !== "months" && state.months.size && !state.months.has(dep.month)) return false;
    if (skip !== "soldout" && state.hideSoldOut && !dep.bookable) return false;
    if (skip !== "sale" && saleOnly() && !dep.sale) return false;
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
    /* Any-of for the same reason: one trip states one bar. Matched on the
       printed phrase rather than on the level, so what the chip says and what
       the column says cannot come apart -- the same rule the site chips follow
       against the Dive sites column. */
    if (skip !== "entry" && state.entry.size && !state.entry.has(entryText(itin))) {
      return false;
    }
    return true;
  }

  var byId = {};
  D.departures.forEach(function (d) { byId[d.id] = d; });

  function visible() {
    var out = [];
    D.departures.forEach(function (dep) {
      var itin = D.itineraries[dep.itinerary_id];
      if (passes(dep, itin, null)) {
        out.push({ d: dep, i: itin, lav: metricsFor(dep), padi: padiMetricsFor(dep) });
      }
    });

    var col = COLS.filter(function (c) { return c.k === state.sort; })[0] || COLS[0];
    out.sort(function (a, b) {
      var x = col.v(a.d, a.i, a.lav, a), y = col.v(b.d, b.i, b.lav, b);
      if (typeof x === "number" && typeof y === "number") return (x - y) * state.dir;
      return String(x).localeCompare(String(y)) * state.dir;
    });
    return out;
  }

  /* ---------- the sort control ---------- */

  /* Which way, in the column's own words rather than as an arrow.
     "Cheapest first" is what a reader is asking for; the triangle is what the
     table does about it, and the header still prints that. Keyed off the
     column wherever the generic pair would be wrong -- a date is early rather
     than cheap, a berth count is few rather than cheap, and an entry bar is a
     bar. Everything numeric falls to the money pair, which is what the five
     money columns are; everything else sorts as text and says so. */
  var SORT_WORDS = {
    start:        ["Earliest first", "Latest first"],
    entry:        ["Easiest first", "Strictest first"],
    availability: ["Fewest places", "Most places"]
  };
  function dirWords(k) {
    if (SORT_WORDS[k]) return SORT_WORDS[k];
    var col = COLS.filter(function (c) { return c.k === k; })[0];
    return col && col.num
      ? ["Cheapest first", "Dearest first"] : ["A\u2013Z", "Z\u2013A"];
  }

  /* Built once, from ORDER rather than from COLS.
     COLS is re-sorted at the compact breakpoint, so a menu drawn from it
     would rearrange itself when the window did -- and a menu whose entries
     move is a menu nobody can learn. Grouped by `zone` under the same words
     the header band uses, so the dropdown and the table agree about what a
     column is about; the runs come out of ORDER for the same reason `groups`
     takes them off COLS, which is that a zone written out by hand drifts from
     the zone the columns actually carry. */
  function buildSortMenu() {
    var html = "", zone = null;
    ORDER.forEach(function (k) {
      var col = COLS.filter(function (c) { return c.k === k; })[0];
      if (!col) return;
      if (col.zone !== zone) {
        if (zone !== null) html += "</optgroup>";
        html += '<optgroup label="' + esc(ZONE_LABELS[col.zone] || "") + '">';
        zone = col.zone;
      }
      /* The full name wherever there is a table, because the picker has room
         the column header does not. On a phone the picker *is* the toolbar's
         widest item -- a `select` is as wide as its longest option whichever
         one is showing -- and 30px of "Mandatory fees" is the difference
         between one row of chrome and two. The column's own `short`, never a
         new abbreviation: the header already resorted to one and this is the
         same column. */
      html += '<option value="' + esc(col.k) + '">' +
        esc(narrow.matches && col.short ? col.short : col.t) + "</option>";
    });
    document.getElementById("sortBy").innerHTML =
      html + (zone === null ? "" : "</optgroup>");
  }

  /* One function writes both renderings of the sort, so they cannot come
     apart: clicking a column heading moves the dropdown, and picking from the
     dropdown moves the header's arrow. Called from `draw`, which is what
     every path that changes the sort ends in -- a fourth path added later
     gets this without knowing it has to. */
  function paintSort() {
    var by = document.getElementById("sortBy");
    if (by.value !== state.sort) by.value = state.sort;
    var words = dirWords(state.sort), now = state.dir > 0 ? 0 : 1;
    var btn = document.getElementById("sortDir");
    /* Both renderings written, and the stylesheet shows one -- the same rule
       the table and the cards follow. A phone gets the header's own triangle,
       because a phone has no header and this control is standing in for it;
       "Cheapest first" beside a select naming the column would put the
       toolbar on a third row at 360px, which is where the words stop being
       worth what they cost. Everything wider gets the words. */
    btn.innerHTML = '<span class="swap" aria-hidden="true">\u21c5</span>' +
      '<span class="dirword">' + esc(words[now]) + "</span>" +
      '<span class="dirmark" aria-hidden="true">' +
      (now ? "\u25bc" : "\u25b2") + "</span>";
    /* One accessible name at both widths, and it has to state where the table
       is *and* what pressing does: a button reading "Cheapest first" is a
       control whose effect is a coin toss, and a bare triangle is not a name
       at all. */
    btn.setAttribute("aria-label", words[now] + " \u2014 press for " +
      words[1 - now].toLowerCase());
  }

  /* One row, rebuilt from a departure id.
   *
     What `visible()` builds per row, on demand for a panel that is opened
     rather than drawn. Rebuilt rather than cached against the trigger, because
     `metricsFor` reads the toggles: a panel holding a row from the last draw
     would show a bill with rental gear in it after the visitor switched gear
     off. */
  function rowFor(id) {
    var dep = byId[id];
    if (!dep) return null;
    var itin = D.itineraries[dep.itinerary_id];
    if (!itin) return null;
    return { d: dep, i: itin, lav: metricsFor(dep), padi: padiMetricsFor(dep) };
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
   * There is a column for it again -- see COLS -- and this is not the leftover
   * of its absence. The two say different things about one fact. The column
   * states the bar, which is what a reader scanning 1,122 rows for a trip they
   * can book needs; this says who stated it, which nobody needs until they
   * have found the row.
   *
   * That split is what the column's first removal got wrong rather than right.
   * Three things were true of it -- sixteen columns already competed for the
   * width, the bar was the same three words on most rows, and its "sources
   * disagree" marker was styled like the disclosure pill beside it, so two
   * unrelated warnings looked identical in adjacent columns -- and none of
   * them was an argument against the fact. Removing the column removed the
   * fact: `level_labels` and every itinerary's `requirements` went on shipping
   * in the payload with no code reading either, and 892 departures carried a
   * stated safety requirement a visitor could only reach by downloading the
   * JSON. The answer was a better column, and the width, the repetition and
   * the marker are each answered where the column is defined.
   *
   * What stays here is the part a column cannot hold: liveaboard.com and PADI
   * Travel disagree about the bar on 60 of the 127 trips both describe, the
   * stricter one is what is shown, and a diver deserves to know the two
   * sources do not agree rather than to see one number presented as settled.
   *
   * The dim line under it is the sources' own wording, printed for the same
   * reason the fee rows print the operator's: a normalised label is this
   * project's phrasing, and the sentence it was normalised from is the
   * evidence for it. */
  function entryBar(itin) {
    var req = itin.requirements;
    if (!req || !req.min_level) return "";
    /* The same phrase the Entry bar column prints, from the same function.
       This used to build its own -- the level's full label, then the dive
       count appended unless a regex spotted the label already stating it --
       and that was two renderings of one fact which could disagree about the
       commonest bar in the fleet. The column made the duplication worse rather
       than introducing it, so the phrase moved to `entryText` and both read it.
       The full certification name is still what the note below says, because a
       sentence naming a source wants the card's real name. */
    var level = entryText(itin);
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
    return '<p class="entry"><b>Entry bar</b> <span>' + esc(level) +
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
      /* Each cell names itself. Below 760px a fee line is not a row of
         columns and the stylesheet restacks these five -- addressed by name,
         because a positional rule is a rule that silently moves the day a
         sixth column is added. */
      return '<tr class="' + (on ? "" : "off") + '"><td class="fmark">' +
        (on ? "▪" : "▫") +
        '</td><td class="flabel">' + esc(line.label) + '</td><td class="famt num">' +
        amount + '</td><td class="ftier">' + esc(line.tier) + '</td><td class="prov">' +
        esc(prov.join(" · ")) + "</td></tr>";
    }).join("");
  }

  function billPanel(row) {
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
    } else if (row.lav.unpriced.length) {
      caveat = "Plus " + row.lav.unpriced.join(", ") + ": listed by the operator " +
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
    if (row.padi) {
      var gap = row.lav.total - row.padi.total;
      padi = "PADI Travel sells this same sailing and publishes its own "
        + "required extras. Its bill comes to €"
        + Math.round(row.padi.total).toLocaleString("en-IE") + " against €"
        + Math.round(row.lav.total).toLocaleString("en-IE") + " here"
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
    /* Each table in a scroller of its own. The fee table's columns need 460px
       to line up and the panel is narrower than that on a phone, so without
       this the table overflowed the panel and took the panel's own header
       sideways with it -- the same rule the deals tables follow: wide content
       scrolls inside its own box and never widens what holds it. */
    var second = row.padi
      ? '<p class="whose">PADI Travel\u2019s bill for the same trip</p>' +
        '<div class="fee-scroll"><table class="fees"><tbody>' +
        feeRows([row.d.padi_base_line].concat(row.i.padi_lines)) +
        "</tbody></table></div>"
      : "";
    return '<p class="pwho">' + esc(row.i.boat) + " &middot; " +
      shortDate(row.d.start) + " &middot; " + row.i.nights + " nights</p>" +
      (second ? '<p class="whose">This site\u2019s source, liveaboard.com</p>' : "") +
      '<div class="fee-scroll"><table class="fees"><tbody>' + body +
        "</tbody></table></div>" +
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
  /* And a much smaller step once it is being scrolled, because the two
     answer different questions. The first page is paid before anything is on
     screen, where 120 rows is what fills a desktop table. Every page after
     it is paid *inside* a scroll, where what is felt is one frame's stall:
     laying out a card costs about 1.4ms on a mid-range phone, so a page of
     120 was a 190ms lurch every time the reader reached the bottom of one --
     twelve frames, once per page, which is the stutter. 20 is one or two.
     Total work is unchanged; the same rows arrive in smaller pieces, which
     is the whole point, because a hitch is felt per page and not per row.
     Twenty and not fewer: the append fires 600px from the end, so a step has
     to add more than 600px of rows or the threshold is still met after it
     and the next scroll event appends again. A table row is 47px and the
     shortest card 143, so 13 rows clears it on the table and 5 on the
     cards -- 20 is the smallest round step that clears both with room, which
     is a number this file can derive rather than one measured off a screen
     it cannot see (#150). */
  var STEP_ROWS = 20;
  var drawn = 0;
  /* How far each host is actually filled. `drawn` is what the page has
     committed to showing; these two are the bookkeeping for filling the host
     nobody is looking at afterwards -- see `appendPage`. `draw` writes both
     hosts whole, so it sets both to `drawn` and any flush queued before it
     becomes a no-op. */
  var filled = { body: 0, cards: 0 };
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

    /* `stick1`..`stickN` on the leading columns, so the CSS offsets line up
       with the order actually being rendered. */
    pins = pinned();

    /* Two tiers. The band names what the columns under it are about and the
       row under that is the sort control, which is why only the second one is
       focusable: the band is a label, not a thing to press, and a tab stop on
       it would be a stop that does nothing.

       `aria-hidden` on the band for the same reason a screen reader gets the
       band's word from the header cell's own tooltip instead: read aloud, a
       row of four cells with colspans between a heading and the sortable
       header is furniture. */
    paintSort();
    document.getElementById("head").innerHTML =
      '<tr class="band" aria-hidden="true">' +
      groups().map(function (g) {
        return '<th colspan="' + g.span + '" class="band-' + g.zone + '">' +
          esc(ZONE_LABELS[g.zone] || "") + "</th>";
      }).join("") + SPACER + "</tr><tr>" +
      COLS.map(function (c, n) {
      var dir = c.k === state.sort
        ? '<span class="dir">' + (state.dir > 0 ? "▲" : "▼") + "</span>" : "";
      /* The short label where one is set and the screen is compact. A column
         is as wide as its widest row and no wider, so "MANDATORY FEES" wants
         100px of the 104 that column's figures need -- the header would be
         setting the width of a column of money. The same call the date column
         already made when it took a smaller font rather than print "DEPAR…":
         a truncated value can be read as truncated, a truncated column name
         cannot. */
      var label = c.short && (narrow.matches || compact.matches) ? c.short : c.t;
      /* The tooltip only where the word was shortened, so it names the column
         rather than repeating it -- or, on a column that needs a sentence
         rather than a name, whatever `hint` says. A heading a visitor cannot
         interpret is worse than no heading: "vs PADI" printed a dash on 291
         rows and read as missing data rather than as a second seller that does
         not sell that date. */
      var full = label !== c.t ? ' title="' + esc(c.t) + '"'
               : c.hint ? ' title="' + esc(c.hint) + '"' : "";
      /* `aria-sort` on the column being sorted, because the arrow is the only
         thing that says so and an arrow is not read out. */
      var sorted = c.k === state.sort
        ? ' aria-sort="' + (state.dir > 0 ? "ascending" : "descending") + '"' : "";
      return '<th tabindex="0" class="' + (c.num ? "num " : "") + "zone-" + c.zone +
        " " + pin(n) + '" data-k="' + c.k + '"' + full + sorted + ">" +
        label + " " + dir + "</th>";
    }).join("") + SPACER + "</tr>";

    /* The chip is a filter the visitor pressed, so an empty result under it is
       "nothing matches those filters" like any other -- what it is not is a
       statement about the sellers, who have not been asked anything here. The
       one case worth its own sentence is a build that read no markdown at all,
       where the emptiness is the data's and not the filters'. */
    var nothing = saleOnly() && !onSaleCount
      ? "No sailing in this build carries a list price beside the one it is " +
        "sold at, so there is no markdown to show."
      : "Nothing matches those filters.";
    document.getElementById("body").innerHTML = rows.length
      ? renderRows(rows, 0, target)
      : '<tr><td class="empty" colspan="' + (COLS.length + 1) + '">' +
        nothing + "</td></tr>";
    /* The same rows again as cards, for a screen with no room for columns.
       Both hosts are always filled and the stylesheet shows one: a phone that
       is rotated crosses the breakpoint with no redraw, and a `Ctrl+F` on a
       laptop still finds text the phone layout would have held. */
    document.getElementById("cards").innerHTML = rows.length
      ? renderCards(rows, 0, target)
      : '<p class="empty">' + nothing + "</p>";
    drawn = Math.min(rows.length, target);
    filled.body = filled.cards = drawn;
    afterDraw(rows);
  }

  /* Which cells a card is built from, by column key. Not a second set of
     renderers: every one of these reads the same `show` the table cell reads,
     so the card cannot drift from the row it is the same departure as -- and
     the three panel triggers inside them (the bill, the ladder, the entry
     bar) come across working, because they are the same buttons. */
  function cell(key, row) {
    var c = COLS.filter(function (x) { return x.k === key; })[0];
    if (!c) return "";
    return c.show ? c.show(row.d, row.i, row.lav, row)
                  : esc(c.v(row.d, row.i, row.lav, row));
  }

  /* The card's rendering of a column, which is the column's own `show` unless
     that column has said otherwise. A card cell reading anything a column did
     not write is how the two layouts start disagreeing about a departure --
     so a card that needs different words asks for them here, on the column,
     rather than composing them in `renderCards`. Exactly one column does. */
  function cardCell(key, row) {
    var c = COLS.filter(function (x) { return x.k === key; })[0];
    if (!c) return "";
    return c.card ? c.card(row.d, row.i, row.lav, row) : cell(key, row);
  }

  /* The rows as cards, which is what a phone gets instead of the table.
   *
     What this replaces was the table with columns folded off the front of the
     row until the Total fit -- `MONEY_FOLD`, `PHONE_ORDER` and a measuring
     pass after every draw. It worked, and it was answering the wrong
     question: the money only stayed on screen by hiding the boat behind it,
     and the widths that decided which columns went are set by whichever rows
     are on screen, so the fold moved when a filter changed and the reader
     lost a column for reasons they could not see.

     A card has no columns to fold. The Total sits in its own corner at every
     width, the boat and the dates are beside it rather than instead of it,
     and nothing has to be measured to keep either there. It costs rows --
     five on a 844px screen against ten -- and buys every row being readable
     without scrolling sideways at all, which is what the fold was spending
     those rows to approximate.

     `data-id` and the row classes are the table's, so marking, the sold-out
     treatment and the panels all work here without knowing which layout drew
     them. */
  function renderCards(rows, from, count) {
    return rows.slice(from, from + count).map(function (row) {
      var marked = state.marked.has(row.d.id);
      return '<article class="card row' + (row.d.bookable ? "" : " gone") +
        (marked ? " marked" : "") + '" aria-selected="' + marked +
        '" data-id="' + esc(row.d.id) + '">' +
        '<div class="card-head">' +
          '<div class="card-id">' + cell("boat", row) + cell("start", row) + "</div>" +
          '<div class="card-money cost">' + cell("total", row) +
            cardCell("perdive", row) + "</div>" +
        "</div>" +
        '<div class="card-trip trip">' + cell("trip", row) + "</div>" +
        '<div class="card-sites sites">' + cell("sites", row) + "</div>" +
        '<div class="card-meta">' +
          '<span class="cm mfees">' + cell("later", row) + "</span>" +
          '<span class="cm nitrox"><i>nitrox</i>' + cell("nitrox", row) + "</span>" +
          '<span class="cm entry-col">' + cell("entry", row) + "</span>" +
          '<span class="cm places">' + cell("availability", row) + "</span>" +
          '<span class="cm source">' + cell("source", row) + "</span>" +
        "</div>" +
        "</article>";
    }).join("");
  }

  /* Kept as a separate function so appending on scroll and drawing from
     scratch build a row exactly the same way. */
  function renderRows(rows, from, count) {
    return rows.slice(from, from + count).map(function (row, offset) {
          var n = from + offset;
          var tds = COLS.map(function (c, col) {
            var v = c.show ? c.show(row.d, row.i, row.lav, row)
                           : esc(c.v(row.d, row.i, row.lav, row));
            return '<td class="' + (c.num ? "num " : "") + (c.cls || "") +
              " " + pin(col) + '">' + v + "</td>";
          }).join("") + '<td class="sp"></td>';
          /* Banding is written here from the row's own position, not left to
             `:nth-of-type(even)`. That selector counts every `tr` in the tbody,
             and the expanded row this used to inject was one -- so opening any
             row inverted the stripes of every row below it. The panel is a
             popover now and injects nothing, but the banding stays computed:
             it is correct either way and it is one fewer thing that a later
             row-level `tr` could quietly break. */
          var marked = state.marked.has(row.d.id);
          return '<tr class="row' + (n % 2 ? " alt" : "") +
            (row.d.bookable ? "" : " gone") + (marked ? " marked" : "") +
            '" aria-selected="' + marked + '" data-id="' + esc(row.d.id) + '">' +
            tds + "</tr>";
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
    countRail();
    /* Here rather than in each control's own handler. The fold now hides nine
       kinds of filter, and a label that goes stale is a fold concealing an
       active one -- so it is refreshed by the thing every one of them already
       does, which is redraw the table. One call, and no way to add a tenth
       control and forget it. */
    labelFilters();
  }

  /* What each rail item opens, counted the way that view actually answers.
     Both were written once at boot and never again, so filtering to one month
     left the rail reading "Trips 1,122" beside a stat block reading "12 rows
     shown" -- two numbers about one table, one of them frozen.

     They are counted differently on purpose, because the two views are.
     **Trips** is the filtered table, so its number is filter-relative and
     agrees with `rows shown`, for the reason the On sale chip's does (#129):
     every other number on this page answers "what if I picked this too?", and
     one that does not teaches the reader only that this one lies. **On sale**
     is an overview of the whole deals book and no filter touches it, so a
     filter-relative number there would promise a narrowing the view does not
     perform -- and a rail item's number is a promise about what opening it
     gives you, broken by the click rather than merely disagreeing.

     The trips figure skips the sale facet for the same reason the chip does:
     with the chip on, a count that included it would collapse onto the visible
     rows and stop being the way back. */
  function countRail() {
    var trips = 0;
    D.departures.forEach(function (dep) {
      if (passes(dep, D.itineraries[dep.itinerary_id], "sale")) trips++;
    });
    railTripsCount.textContent = trips.toLocaleString("en-IE");
    /* A count only where there are sailings behind it. PADI can be advertising
       a sale on boats whose sailings carry no marked-down fare here, and a "0"
       beside the name would read as "no sale on" over a view listing one. */
    railSaleCount.textContent = onSaleCount
      ? onSaleCount.toLocaleString("en-IE") : "";
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

  /* `opts.limit` overrides the default cap, and `opts.moreWord` the disclosure's
     noun. Exactly one bank sets either, and the reason is that the cap means
     something different to a *ladder* than to a list.

     For ports or boats the eight commonest are a fair sample of a set with no
     order of its own, and "+ 69 more" is an honest offer. The entry bar is
     ordered least demanding to most, so a count-based cut is arbitrary and
     brutal: at 8 it hides 738 of 1122 rows, every Advanced rung, and the
     single biggest bar there is -- Advanced + 50 dives, on 289 rows -- which
     sorts *last* precisely because it is the strictest. Sorting the bank by
     popularity instead would fix the cap and break the reading.

     So the entry bank cuts at its certification boundary rather than at a
     number: Open Water rungs shown, Advanced rungs behind the disclosure. That
     is a fold a reader can predict, and the label says which way it opens --
     "+ 9 stricter" rather than "+ 9 more", because on a ladder the direction
     is the information. It happens to be 8 chips today, which is what the
     default cap would have given anyway; the difference is that it stays on
     the boundary when the fleet changes or the screen narrows.

     This bank was uncapped until then, on the reasoning that seventeen short
     chips cost about what Ports already does and the panel scrolls. Capping it
     is a deliberate reversal: the panel is long enough that the reversal is
     worth the fold, given the fold is meaningful. */
  function chips(host, items, picked, numeric, skip, pick, opts) {
    opts = opts || {};
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
      /* Re-ordered by the count it is now showing, not the one it opened
         with.
       *
         A bank built by `tally` is a list in popularity order, and after any
         filter that order is a fact about a table nobody is looking at. Dive
         sites is where it bites, because that bank is ANDed: pick Brothers and
         every other reef's number becomes "trips that visit both", which
         reshuffles the whole list -- and the chips stayed in the order they
         booted in, so the reefs that actually combine with Brothers sat behind
         "+34 more" while ones that barely do led the bank. Ports and boats
         re-order for the same reason a beat later, when a month or a reef
         moves their numbers; neither can move under a finger, because picking
         inside an OR bank does not change that bank's own counts.

         Only where the order *was* a count. Months are chronological, the
         entry bar is ranked by how strict it is, and the two sellers are
         listed in neither's favour -- sorting any of those by popularity would
         replace a meaning with a ranking.

         Chosen chips lead, in the same order among themselves. They are shown
         regardless of the cap already; leading it is what makes that coherent
         rather than a pressed chip appearing at position 40 for no stated
         reason. */
      if (opts.byCount && counts) {
        var rank = function (it) { return picked.has(numeric ? +it.id : it.id) ? 0 : 1; };
        live = live.slice().sort(function (a, b) {
          return rank(a) - rank(b) ||
            (counts[b.id] || 0) - (counts[a.id] || 0) ||
            String(a.id).localeCompare(String(b.id));
        });
      }
      var limit = opts.limit ? opts.limit(live) : chipLimit();
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
            (expanded ? "− fewer"
               : "+ " + hidden + " " + (opts.moreWord || "more")) + "</button>"
          : "");
    }

    node.addEventListener("click", function (event) {
      var button = event.target.closest("button");
      if (!button) return;
      if (button.dataset.more) { expanded = !expanded; paint(); return; }
      var v = numeric ? +button.dataset.v : button.dataset.v;
      if (picked.has(v)) picked.delete(v); else picked.add(v);
      button.setAttribute("aria-pressed", picked.has(v));
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

  /* ---------- what each seller is discounting ---------- */

  /* PADI Travel's deals listing, read daily and diffed against the last day
     the book holds. Everything here was settled in Python: the join that
     decided which of these boats is one in the fleet, the conversion into euro,
     and
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
  /* ---------- the sales, and the trips on sale ---------- */

  /* One row per sale, and a sale is one seller's claim about one boat.
   *
     This was a single boat-keyed table joining the two sellers side by side,
     with PADI's five columns in a group to the right and dashes on twelve of
     the twenty-two rows (#145). The join was the problem: what the two sellers
     publish are not two halves of one record. liveaboard.com strikes a list
     price through on a booking page, so its evidence is a *run* of that boat's
     discounted sailings; PADI publishes a named offer against one sailing. A
     row per offer states each as what it is, and a boat both sellers discount
     gets two rows rather than one row asserting a join nobody made.

     Sorted by boat so those two rows sit together, which is what the join was
     reaching for and all it was reaching for. */
  /* What comes off, in money, where a rate is not what PADI stated. Its "Free
     night(s)" and "Free upgrade" kinds take nothing off a nightly rate, so the
     money is the whole of what can be said -- and the kind is the whole of it
     where there is no money either. */
  function offerSaving(offer) {
    var saved = offer.was - offer.price;
    if (saved > 0) return eur(saved);
    return offer.kind || "offer";
  }

  function salesRows() {
    var deals = D.deals || {};
    var rows = [];
    ((deals.on_sale || {}).boats || []).forEach(function (r) {
      rows.push({
        boat: r.boat_name, from: r.first, to: r.last,
        off: r.pct ? r.pct + (r.pct_max ? "–" + r.pct_max : "") + "% off" : null,
        /* No name, because the seller publishes none: this is a struck-through
           price on a booking page and not an advertised campaign. A dash here
           is a fact about the disclosure rather than a hole in the row. */
        title: null, url: null,
        sellers: r.sellers, read: r.read,
        of: r.sailings && r.of ? r.sailings + " of " + r.of : null
      });
    });
    (deals.offers || []).forEach(function (o) {
      rows.push({
        boat: o.boat_name, from: o.start, to: o.end,
        /* The rate where PADI states one, the money where it does not. A "Free
           night(s)" offer takes nothing off a nightly rate, and dividing one
           of its prices by the other would print a discount PADI never
           claimed. */
        off: o.kind === "Discount %" && o.value ? o.value + "% off" : offerSaving(o),
        title: o.title || null, url: o.url || null,
        sellers: [1], read: [deals.read], of: null,
        /* One sailing, and it must not read as a window. PADI publishes no
           validity dates with an offer -- only the sailing it advertises it
           against -- so the cell says which it is. */
        exemplar: true
      });
    });
    return rows.sort(function (a, b) {
      var x = (a.boat || "").toLowerCase(), y = (b.boat || "").toLowerCase();
      if (x !== y) return x < y ? -1 : 1;
      return (a.sellers[0] || 0) - (b.sellers[0] || 0);
    });
  }

  /* Who marked it down and when they were read, one seller per line.

     The date is per seller and not per panel. These two books are read by two
     jobs days apart, and stamping the cabin crawl's day across the whole table
     dated ten of these rows wrong — the same rule the berth counts have
     followed since they were published. */
  function markedDownBy(row) {
    var td = el("td", "d-native");
    /* `read` is parallel to `sellers` and is not indexed by seller id: promote
       emits the day of each seller *in the order it names them*. Reading it by
       id drops the date off every row a single seller marked down. */
    (row.sellers || []).forEach(function (seller, n) {
      var day = (row.read || [])[n];
      td.appendChild(el("span", "reading",
        (SELLER_NAMES[seller] || "?") + (day ? " · " + shortDate(day) : "")));
    });
    return td;
  }

  /* A table that fills the panel and scrolls inside itself.
   *
     `display:block` on the table did the scrolling and cost the width: a
     table told to be a block box hands its own rows to an anonymous
     inline-table, which shrink-wraps to its content -- so six columns sat
     crammed against the left margin of a 1,200px panel with a third of it
     blank, and the two tables came out different widths from each other
     because each was as wide as its own longest boat name (#147). The
     scrolling belongs to a wrapper; the table stays a table, at `width:100%`,
     which spreads the columns and makes both tables the same width by
     construction rather than by coincidence. */
  function scroller(table) {
    var box = el("div", "deals-scroll");
    box.appendChild(table);
    return box;
  }

  function salesTable(rows) {
    var table = el("table", "deals-table");
    var head = document.createElement("thead");
    var hr = el("tr", null);
    ["Boat", "From", "To", "Off", "Offer", "Seller"].forEach(function (h) {
      hr.appendChild(el("th", null, h));
    });
    head.appendChild(hr);
    table.appendChild(head);

    var body = document.createElement("tbody");
    rows.forEach(function (r) {
      var tr = el("tr", null);
      tr.appendChild(el("td", "d-boat", "")).appendChild(boatLink(r.boat));
      var from = el("td", "d-when", r.from ? shortDate(r.from) : "—");
      var to = el("td", "d-when", r.to ? shortDate(r.to) : "—");
      if (r.exemplar) {
        from.title = to.title = "The sailing PADI advertises this offer " +
          "against. It publishes no dates for the offer itself, so this is one " +
          "sailing rather than the window the discount covers.";
      } else if (r.of) {
        from.title = to.title = r.of + " of this boat’s sailings are " +
          "discounted, and these are the first and last of them.";
      }
      tr.appendChild(from);
      tr.appendChild(to);
      tr.appendChild(r.off
        ? el("td", "d-off", r.off)
        : el("td", "d-none", "rate not stated"));

      var offer = el("td", r.title ? "d-offer" : "d-none", "");
      if (r.title && r.url) {
        var a = document.createElement("a");
        a.href = r.url;
        a.rel = "noopener";
        a.target = "_blank";
        a.textContent = r.title;
        a.title = "The PADI Travel page this offer was read from";
        offer.appendChild(a);
      } else if (r.title) {
        offer.textContent = r.title;
      } else {
        /* Verbatim where there is one, and nothing invented where there is
           not: naming this "sale" would be the page writing the seller's copy
           for it. */
        offer.textContent = "—";
        offer.title = "liveaboard.com publishes no name for it. The discount " +
          "is a list price struck through beside the one it charges, read off " +
          "the booking page.";
      }
      tr.appendChild(offer);
      tr.appendChild(markedDownBy(r));
      body.appendChild(tr);
    });
    table.appendChild(body);
    return scroller(table);
  }

  /* Every discounted sailing, in the order it sails.
   *
     Reachable before only by switching the On sale chip on over the trips
     table, which is a filter and not this view's answer (#145): "which
     departures are cut" is a table question and "what are the sales" is a
     page, and the page was missing the half a reader came for.

     By date rather than by depth. A discount nobody can take the week off for
     is not a cheaper trip, so the date is the first thing a reader has to
     check and this is a list they read down — and it is the first column for
     the same reason, which keeps the order of the rows and the order of the
     eye the same. Depth breaks a tie inside one day, and a sailing whose
     seller stated no percentage sorts last of its day and says so: it is on
     sale, and it is not 0% off. */
  function tripsOnSale() {
    var rows = [];
    D.departures.forEach(function (dep) {
      if (!dep.sale) return;
      var itin = D.itineraries[dep.itinerary_id];
      rows.push({ d: dep, itin: itin, pct: dep.sale.pct || 0 });
    });
    return rows.sort(function (a, b) {
      if (a.d.start !== b.d.start) return a.d.start < b.d.start ? -1 : 1;
      return b.pct - a.pct;
    });
  }

  function tripsOnSaleTable(rows) {
    var table = el("table", "deals-table");
    var head = document.createElement("thead");
    var hr = el("tr", null);
    ["Sails", "Boat", "Trip", "Off", "Was", "Now"].forEach(function (h) {
      hr.appendChild(el("th", null, h));
    });
    head.appendChild(hr);
    table.appendChild(head);

    var body = document.createElement("tbody");
    rows.forEach(function (r) {
      var tr = el("tr", null);
      tr.appendChild(el("td", "d-when", shortDate(r.d.start)));
      tr.appendChild(el("td", "d-boat", "")).appendChild(boatLink(r.itin.boat));
      tr.appendChild(el("td", "d-offer", r.itin.title));
      /* What comes off, then the two prices in the order the cut happened:
         the rate, the fare before it, the fare now. */
      tr.appendChild(r.pct
        ? el("td", "d-off", "−" + r.pct + "%")
        : el("td", "d-none", "rate not stated"));
      /* The converted figure on both sides. `sale.was` is already in the
         display currency and the payload's own price is the sailing's, so the
         two may only ever be read beside `base`. */
      tr.appendChild(el("td", r.d.sale.was ? "d-was" : "d-none",
        r.d.sale.was ? eur(r.d.sale.was) : "not stated"));
      tr.appendChild(el("td", "d-now", eur(r.d.base)));
      body.appendChild(tr);
    });
    table.appendChild(body);
    return scroller(table);
  }

  /* ---------- the history view ---------- */

  /* The change reports, rendered rather than transcribed.
   *
     What this replaces was `liveaboard.cli changes`' stdout, escaped into a
     `<pre>`: column-aligned monospace truncated to a width no browser has, so
     the visitor got `MY Odyssey Liveaboar` and `Deep South - Rocky & Zabargad
     Isla` on a page that has a table renderer. Every line named a boat, a date
     and a trip that exist as a row a few hundred pixels above, and not one of
     them could be clicked (#143).

     The structured report was always there -- `changes.compare` builds
     dataclasses, `changes.render` flattened them to text, the CLI wrote the
     text to Markdown and `render` read it back out. It now travels as data as
     well, and this draws it. */

  var CHANGE_ROWS = 8;

  /* One kind of change: what to call it, and how to lay a row of it out.
     Ordered as the text report orders them, which is the order the reader of
     a refresh wants -- what appeared, what went, what moved. */
  var CHANGE_BLOCKS = [
    { k: "added", t: "New departures", kind: "trip" },
    { k: "returned", t: "Bookable again", kind: "trip" },
    { k: "sold_out", t: "Now sold out", kind: "trip" },
    { k: "withdrawn", t: "Withdrawn", kind: "trip" },
    { k: "price_up", t: "Fares up", kind: "move" },
    { k: "price_down", t: "Fares down", kind: "move" },
    { k: "fees", t: "Fee lines changed", kind: "fee" },
    { k: "vessels_new", t: "Vessels new to the page", kind: "name" },
    { k: "vessels_gone", t: "Vessels that lost every departure", kind: "name",
      warn: "Most likely a failed fetch rather than a cancelled season." },
    { k: "months_gone", t: "Vessel-months that emptied", kind: "name",
      warn: "A page that came back unreadable, not a withdrawn month — those " +
            "sailings are missing from the site until the next crawl reads them." },
    { k: "fx", t: "Exchange rates", kind: "fx" }
  ];

  /* A boat name that takes you to its sailings. Every row of every report
     names a boat that is a row in the trips table, and none of them could be
     reached from here -- the panel that answers "did this get cheaper" could
     not get you to the thing that did. */
  function boatLink(name) {
    var a = el("button", "change-boat", name);
    a.type = "button";
    a.title = "Show " + name + "’s sailings";
    a.addEventListener("click", function () {
      state.boats.clear();
      state.boats.add(name);
      /* The bank has to be rebuilt, not just re-pressed: this boat may be in
         its hidden tail, and a chip that is holding a filter must be visible
         or the reader cannot take it off again. Same call the Reset button
         makes, and for the same reason. */
      var bank = document.getElementById("boats");
      if (bank && bank.repaint) bank.repaint();
      window.location.hash = "#trips";
      draw(false);
      if (shellEl) shellEl.scrollTop = 0;
    });
    return a;
  }

  function changeRow(row, kind) {
    var tr = el("tr", null);
    if (kind === "name") {
      tr.appendChild(el("td", "c-boat", "")).appendChild(boatLink(row));
      return tr;
    }
    if (kind === "fx") {
      tr.appendChild(el("td", "c-boat", row.currency));
      tr.appendChild(el("td", "c-move",
        row.was.toFixed(4) + " → " + row.now.toFixed(4)));
      tr.appendChild(el("td", row.pct < 0 ? "c-down" : "c-up",
        (row.pct > 0 ? "+" : "") + row.pct.toFixed(1) + "%"));
      return tr;
    }
    if (kind === "fee") {
      tr.appendChild(el("td", "c-boat", "")).appendChild(boatLink(row.boat));
      tr.appendChild(el("td", "c-trip", row.code));
      tr.appendChild(el("td", "c-move", row.was + " → " + row.now));
      return tr;
    }
    tr.appendChild(el("td", "c-when", shortDate(row.start)));
    tr.appendChild(el("td", "c-boat", "")).appendChild(boatLink(row.boat));
    tr.appendChild(el("td", "c-trip", row.title));
    if (kind === "move") {
      /* Both ends and the difference, in the currency the seller quoted --
         never converted here. The percentage carries a decimal because a €20
         move on a €2,400 berth is 0.8%, and "+1%" reads as the rounding this
         report is at pains to exclude. */
      tr.appendChild(el("td", "c-move",
        Math.round(row.was).toLocaleString("en-IE") + " → " +
        Math.round(row.now).toLocaleString("en-IE") + " " + row.currency));
      tr.appendChild(el("td", row.delta < 0 ? "c-down" : "c-up",
        (row.delta > 0 ? "+" : "") +
        Math.round(row.delta).toLocaleString("en-IE") +
        " (" + (row.pct > 0 ? "+" : "") + row.pct.toFixed(1) + "%)"));
    } else {
      tr.appendChild(el("td", "c-move", row.price === null || row.price === undefined
        ? "no price"
        : Math.round(row.price).toLocaleString("en-IE") + " " + row.currency));
    }
    return tr;
  }

  /* One block, with the rest of it behind a control rather than behind a
     sentence saying it exists. The text report has to confess a truncation --
     "... and 24 more not shown" -- because a terminal cannot expand. This can,
     so the honest form here is showing them. What the *book* dropped for
     weight is still a confession, because those rows are not in the page at
     all. */
  function changeBlock(report, spec) {
    var rows = report[spec.k] || [];
    if (!rows.length) return null;
    var wrap = el("div", "change-block");
    wrap.appendChild(el("h4", spec.warn ? "change-warn" : null,
      spec.t + " (" + rows.length +
      ((report.more || {})[spec.k] ? " of " + (rows.length + report.more[spec.k]) : "") +
      ")"));
    if (spec.warn) wrap.appendChild(el("p", "deals-note", spec.warn));

    var table = el("table", "change-table");
    var body = document.createElement("tbody");
    rows.forEach(function (row, n) {
      var tr = changeRow(row, spec.kind);
      if (n >= CHANGE_ROWS) tr.hidden = true;
      body.appendChild(tr);
    });
    table.appendChild(body);
    wrap.appendChild(table);

    if (rows.length > CHANGE_ROWS) {
      var more = el("button", "link-button change-more",
        "Show " + (rows.length - CHANGE_ROWS) + " more");
      more.type = "button";
      more.addEventListener("click", function () {
        var hidden = body.querySelectorAll("tr[hidden]").length > 0;
        [].forEach.call(body.rows, function (tr, n) {
          if (n >= CHANGE_ROWS) tr.hidden = !hidden;
        });
        more.textContent = hidden
          ? "Show fewer" : "Show " + (rows.length - CHANGE_ROWS) + " more";
      });
      wrap.appendChild(more);
    }
    /* And what the book itself did not carry, which no control can reveal. */
    if ((report.more || {})[spec.k]) {
      wrap.appendChild(el("p", "deals-note",
        report.more[spec.k] + " further " + spec.t.toLowerCase() +
        " are not in this page: a report is paid for by every visitor, so the " +
        "book keeps the first " + rows.length + " of each kind. The full " +
        "report is in data/CHANGES.md."));
    }
    return wrap;
  }

  function drawChanges() {
    var host = document.getElementById("changeLog");
    var book = D.changes || [];
    if (!host || !book.length) return;

    var days = [];
    book.forEach(function (r) {
      if (days.length && days[days.length - 1].day === r.day) {
        days[days.length - 1].reports.push(r);
      } else {
        days.push({ day: r.day, reports: [r] });
      }
    });

    var n = book.length;
    var span = days.length > 1
      ? "from " + longDate(book[n - 1].day) + " to " + longDate(book[0].day)
      : "on " + longDate(book[0].day);
    host.appendChild(el("p", "history-lead",
      n + " refresh" + (n === 1 ? "" : "es") + " recorded " + span +
      ". A day with no entry is a day the refresh did not run, which is not " +
      "the same as a day nothing moved."));

    days.forEach(function (d) {
      var label = longDate(d.day);
      if (d.reports.length > 1) label += " · " + d.reports.length + " refreshes";
      host.appendChild(el("h3", "history-day", label));
      /* One refresh, one block. They used to be appended straight under the
         day heading, so two runs on one day were two lists of the same shape
         with nothing between them saying where the first ended -- and on a
         quiet day, two identical "Nothing moved." lines reading as a stutter
         rather than as two runs. The block is what the rule down the left
         edge is drawn on. */
      d.reports.forEach(function (report) {
        var run = el("div", "change-report");
        host.appendChild(run);
        if (report.quiet) {
          run.appendChild(el("p", "change-quiet", report.price_rounding
            ? "Nothing moved, beyond " + report.price_rounding +
              " fare(s) shifting by under €5."
            : "Nothing moved."));
          return;
        }
        if (report.availability_newly_read) {
          run.appendChild(el("p", "deals-note",
            "The earlier dataset stated availability nowhere, so sold-out and " +
            "bookable-again are not compared here. Nobody had looked before."));
        }
        CHANGE_BLOCKS.forEach(function (spec) {
          var block = changeBlock(report, spec);
          if (block) run.appendChild(block);
        });
        if (report.price_rounding) {
          run.appendChild(el("p", "deals-note",
            report.price_rounding + " further fare(s) moved by under €5 and " +
            "are not listed: at that size a move is the rounding on a " +
            "converted price rather than a reprice."));
        }
      });
    });
  }

  /* The discount moves, under the heading that already says "what changed".
   *
     They were drawn inside the sale panel, which left one page reporting
     refresh news in two places: the sale panel said what is on sale *and* what
     moved, and this section said what moved about everything else (#146). The
     panel keeps the first half and this takes the second.

     What they may not do is fold into the report above them, and the reason is
     that the three run on different clocks. The changelog is a diff between
     two committed datasets -- `HEAD~1` -- computed in Python. These two are
     each a diff between the last two readings of *one seller*: 28 Aug for
     liveaboard.com, 30 Aug for padi.com, precomputed by `promote` from
     `data/sales.json` and `data/deals.json`. Neither is the commit boundary
     and they are not each other's. So each keeps its own seller's name and its
     own "since" date in its own heading, which is the rule the sale panel's
     own heading follows: a summary is only as fresh as its stalest half. */
  function drawSaleMoves() {
    var host = document.getElementById("saleMoves");
    var deals = D.deals;
    if (!host || !deals) return;
    host.textContent = "";
    var shifted = deals.on_sale_changes;
    if (shifted && !shifted.first_reading) host.appendChild(salesChanges(shifted));
    if ((deals.offers || []).length && deals.previous) {
      host.appendChild(dealsChanges(deals));
    }
  }

  /* ---------- the sale view's own bands ---------- */

  /* Four figures, not a sentence. The line this replaces carried the same
     facts -- how many sailings, how many boats, what moved, when it was read --
     run together in prose, which is the shape a `<summary>` needs and the
     wrong one for the top of a page: a reader skims a strip and reads a
     sentence, and these are numbers to be skimmed.

     The reading date is one of the four rather than a footnote, because a
     discount is a claim with a date on it and this one can end overnight. */
  function saleStrip(deals, rates) {
    var strip = el("div", "sale-strip");
    function fig(value, label, title) {
      var box = el("div", "sale-fig");
      var b = el("b", null, value);
      box.appendChild(b);
      box.appendChild(el("span", null, label));
      if (title) box.title = title;
      strip.appendChild(box);
    }
    var sale = deals.on_sale || {};
    var boats = (sale.boats || []).length;
    if (sale.sailings) fig(sale.sailings.toLocaleString("en-IE"), "sailings cut");
    if (boats) fig(boats, boats === 1 ? "boat" : "boats");

    /* The depth as a range: the discounts are not one number, and "10-30%
       off" says at a glance what a column of per-boat rates makes you scan
       for. This is the whole of what the bracket table below it was for, and
       the only part of it that was a fact about anything (#147). */
    if (rates.length) {
      fig(rates.length === 1 ? rates[0] + "%"
        : rates[rates.length - 1] + "–" + rates[0] + "%", "off");
    }
    /* The oldest day anything here was read, not the freshest: three books
       feed this view and a summary is as fresh as its stalest half. */
    var days = [deals.read, D.meta.berths_read, D.meta.padi_berths_read]
      .filter(Boolean).sort();
    if (days.length) {
      fig(shortDate(days[0]), "read",
          "The oldest of the readings behind this page. A discount is what a " +
          "seller claimed on the day beside its name, and it can end overnight.");
    }
    return strip;
  }

  /* Which rates the fleet is discounting at, deepest first.
   *
     All that is left of a table this used to feed, and the reason it went: a
     bracket row grouped sailings that shared only a percentage, so every
     column on it -- 01 May to 28 Aug, EUR664 to EUR1,427, "liveaboard.com ·
     padi.com" -- was an aggregate over an arbitrary set and a fact about
     nothing anybody can book (#147). Nobody books by discount bracket. The
     range across the brackets *is* a fact about the sale, so it survives as
     one figure in the strip.

     A sailing marked down with no stated rate is not a nought and is not a
     rate: it drops out of this list and says so on its own row, through
     `saleTag`, which is where a claim about one sailing belongs. */
  function discountRates() {
    var seen = {};
    D.departures.forEach(function (dep) {
      if (dep.sale && dep.sale.pct) seen[dep.sale.pct] = 1;
    });
    return Object.keys(seen).map(Number).sort(function (a, b) { return b - a; });
  }

  /* How far this panel's answer reaches, which is the half of it a count of
     discounts cannot state. Three absences print identically to "not on sale"
     unless it says so -- a ladder thrown away as stale, a sailing neither
     seller published a list price for, and a trip-name banner the seller read
     for it contradicts.

     A muted line of counts now rather than three paragraphs of reasoning
     (#145). The counts are the load-bearing part and they stay on the page:
     the invariant is that the panel states what it could not read, not that it
     explains it in body copy above a table. The reasoning is one hover away
     per count, and unabridged in `CLAUDE.md`.

     Drawn only from what promote counted. Nothing here is re-derived from the
     rows, because the rows are precisely where these facts have gone missing. */
  function coverageNote(coverage) {
    var line = el("p", "deals-coverage");
    var said = [];
    function part(text, title) {
      var span = el("span", "cov", text);
      span.title = title;
      said.push(span);
    }
    var dropped = coverage.dropped;
    if (dropped) {
      part(dropped.sailings + " reading" + (dropped.sailings === 1 ? "" : "s") +
        " dropped as stale",
        "On " + dropped.boats.join(", ") + ", the booking page’s cheapest " +
        "cabin sat too far from the price beside it to still be that " +
        "sailing’s — last week’s prices on this week’s shelf. Those readings " +
        "were thrown away, so those sailings are absent here rather than " +
        "reported as full price.");
    }
    if (coverage.unread) {
      part(coverage.unread + " sailing" + (coverage.unread === 1 ? "" : "s") +
        " with no list price",
        "Neither seller published a list price for " +
        (coverage.unread === 1 ? "it" : "them") + ", so whether " +
        (coverage.unread === 1 ? "it is" : "they are") +
        " discounted is unknown rather than no.");
    }
    if (coverage.banner_unsupported) {
      part(coverage.banner_unsupported + " trip-name banner" +
        (coverage.banner_unsupported === 1 ? "" : "s") + " unsupported",
        "The trip name claims a discount the seller read for it does not " +
        "support. A banner is a claim about a number; the struck-through list " +
        "price is the number, and it wins.");
    }
    if (!said.length) return null;
    said.forEach(function (span, n) {
      if (n) line.appendChild(el("span", "cov-sep", " · "));
      line.appendChild(span);
    });
    return line;
  }

  /* The vessels PADI advertises that no boat here joins to are *not* drawn.
   *
     They are still named, and naming them is still the point: the query asks
     PADI for the USA as well as Egypt because three Egyptian boats are filed
     there, the same breadth returns Caribbean ones, and only a name tells an
     unpaired Egyptian boat from one sailing another ocean. But the reader of
     this view is shopping the sales, and a list of boats the page does not
     carry is the pipeline talking to its maintainer over the visitor's
     shoulder. So the name goes where the maintainer is: `promote` keeps
     `deals.unmatched` and `cli` prints a `::warning::` per vessel, which is
     the build log rather than the page. Do not re-add it here without moving
     the audience it is for.

     What that leaves the panel is `coverageNote`, which is the reader's own
     boundary -- what could not be read about the boats this page *does*
     carry. */

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

  /* Returns whether there was anything to draw. The panel's own visibility is
     showView()'s to decide -- it belongs to the sale view -- but whether it
     has content at all is a fact about the data, and the rail needs it too:
     with no markdown and no deals book there is no sale view to offer. */
  function drawDeals() {
    var deals = D.deals;
    var host = document.getElementById("dealsBody");
    if (!host || !deals) return false;
    var offers = deals.offers || [], fleet = (deals.on_sale || {}).boats || [];
    /* What is on sale, and nothing about what moved. The moves used to make
       this view exist on their own -- the day every sale on the fleet ended,
       "36 sailings are no longer 33% off" was the whole of what the panel had
       to say -- and they are reported in the refresh history now (#146). So a
       day with no discount anywhere has no sale view, which is the honest
       answer to "what is on sale" rather than an empty page. */
    if (!offers.length && !fleet.length) return false;

    var body = host;
    body.textContent = "";

    /* The figures, skimmable. This was a run-on sentence, which is the shape a
       collapsed `<summary>` needs and the wrong one for the top of a page. */
    var line = document.getElementById("dealsLine");
    line.textContent = "";
    line.appendChild(saleStrip(deals, discountRates()));

    /* Two sections, and the order is the two questions in the order they are
       asked (#145): what the sales are, then which trips carry them. Three
       paragraphs of reasoning used to sit between the reader and both, and the
       data was arranged by boat rather than by either question. */
    var sales = salesRows();
    if (sales.length) {
      body.appendChild(el("h4", null, "The sales"));
      var note = deals.coverage ? coverageNote(deals.coverage) : null;
      if (note) body.appendChild(note);
      body.appendChild(salesTable(sales));
    }

    var trips = tripsOnSale();
    if (trips.length) {
      body.appendChild(el("h4", null, trips.length === 1
        ? "The trip on sale" : "The " + trips.length + " trips on sale"));
      body.appendChild(tripsOnSaleTable(trips));
    }

    return true;
  }

  /* ---------- wiring ---------- */

  buildSortMenu();
  /* A new column starts ascending, exactly as clicking its heading does. Two
     controls onto one pair of state fields, so a divergence here would be the
     dropdown and the header disagreeing about what picking a column means. */
  document.getElementById("sortBy").addEventListener("change", function () {
    state.sort = this.value;
    state.dir = 1;
    draw(true);
  });
  document.getElementById("sortDir").addEventListener("click", function () {
    state.dir = -state.dir;
    draw(true);
  });

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

  document.querySelector(".shell").addEventListener("click", function (event) {
    /* Anywhere on a row marks it, so the visitor can keep their place while
       scrolling sixteen columns sideways.
     *
       Three things it must not do. It must not steal a click meant for a
       link -- the Source column opens the operator's own listing, and a
       marked row is no consolation for not going there. It must not fire on a
       panel's own trigger, which is a button the visitor pressed to read
       something and not part of the row's own surface -- and the `button` test
       covers all three of them, which is why the fee panel needed no new
       branch here when it stopped being a row of its own. And it must not fire
       at the end of a drag: selecting a price to copy it ends in a mouseup
       over the row, and toggling a highlight underneath the text being
       selected reads as the page fighting back. `isCollapsed` is false exactly
       when text was dragged, which is the distinction wanted -- not whether a
       selection exists, since an old one elsewhere on the page would then
       block every mark. */
    if (event.target.closest("a, button")) return;
    /* `.row` rather than `tr.row`, and on the shell rather than the `tbody`,
       for the same reason the panels moved: a card is `article.card.row` and
       carries the same `data-id`, so a phone could not mark a row either. Both
       layouts write that class and that attribute, which is what makes one
       selector right for both. */
    var tr = event.target.closest(".row[data-id]");
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

  function fillLadder(host, d) {
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
    if (!blocks.length) { host.innerHTML = head; return; }
    /* One section per seller, and both fill one now (#92). The seller is named
       whenever two are speaking, and also whenever the only one speaking has
       no ladder — a bare count with nobody's name on it is a number the page
       is asking to be taken on trust. */
    host.innerHTML = head + blocks.map(function (block) {
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
  function place(host, trigger) {
    var box = trigger.getBoundingClientRect();
    host.hidden = false;
    host.style.visibility = "hidden";
    var w = host.offsetWidth, h = host.offsetHeight, pad = 8;
    var left = Math.min(Math.max(pad, box.left), window.innerWidth - w - pad);
    var below = window.innerHeight - box.bottom;
    var top = below > h + pad || below > box.top ? box.bottom + 4 : box.top - h - 4;
    host.style.left = Math.round(left) + "px";
    host.style.top = Math.round(Math.min(Math.max(pad, top), window.innerHeight - h - pad)) + "px";
    host.style.visibility = "";
  }

  /* One mechanism, three panels (#149).
   *
     The ladder's wiring was written for the ladder and is what the fee panel
     and the entry note now use: the fee breakdown moved out of a per-row
     dropdown, and writing a second copy of hover-peek/click-pin/Escape-close
     would have been three implementations of one interaction, drifting apart
     on exactly the parts that are easy to get wrong.

     Both gestures, and focus as well as the pointer. Hover does not exist on a
     phone and this page is built to work on one in a dive shop; and a panel is
     the column's content rather than a reward for owning a mouse, so tabbing
     to the cell opens it too.

     Opening one closes the others. Two panels anchored to two cells of the
     same row would overlap, and the second would be read as belonging to
     whichever cell it happened to land on. */
  var panels = [];

  /* `opts.hoverOpens` (default true) governs the pointer half only -- click
     and keyboard focus still open every panel this drives, on every trigger.
     The Entry bar panel turns it off (#151): opening a dialog every time the
     pointer crosses that cell, while it is one of three the row rebuilds on
     demand, made scrolling the mouse down the column a slideshow. Berths and
     the fee bill keep hovering, because a diver comparing cabin ladders or fee
     books wants them without a click each time. */
  function hoverPanel(host, selector, fill, opts) {
    var hoverOpens = !opts || opts.hoverOpens !== false;
    var held = null, peeked = null, dismissed = null, openTimer = 0, shutTimer = 0;
    /* `.shell`, not the `tbody`, because the rows are not always a table.
     *
       Every listener here hung off `#body`, and below 760px that element is
       `display:none` and the rows are `#cards` -- so on a phone not one of the
       three panels was wired to anything. The triggers rendered, because a
       card cell is the same column's renderer and the markup came across
       exactly as intended; only the events did not. "The three panel triggers
       come across working" was true of everything except the part that makes
       them work.

       The shell is the one box holding both hosts, so this is wired once and
       stays wired if a third layout ever draws rows. The three panel hosts sit
       outside it, and the header inside it carries no trigger, so a wider net
       catches nothing new. */
    var body = document.querySelector(".shell");

    function shut() {
      host.hidden = true;
      var open = document.querySelector(selector + '[aria-expanded="true"]');
      if (open) open.setAttribute("aria-expanded", "false");
      held = null;
      peeked = null;
    }

    function show(trigger) {
      if (fill(host, trigger) === false) return;
      place(host, trigger);
      trigger.setAttribute("aria-expanded", "true");
    }

    function others() {
      panels.forEach(function (panel) { if (panel.host !== host) panel.shut(); });
    }

    /* HOVER IS A MOUSE. A finger has no hover state, and `pointerover` fires
       for one anyway -- on touchstart, before the drag that follows is known
       to be a drag. So a swipe that began on one of these buttons opened its
       panel 120ms later, and a card's meta row is three of them: on a phone
       most swipes started on a trigger, the panel appeared over the list and
       the scroll died under it. Five to ten cards a gesture, which is what a
       scroll that keeps being interrupted looks like.
       It only became reachable when the listeners moved off the `tbody` onto
       `.shell` -- before that nothing on a phone was wired to anything, so
       nothing could interrupt. The panels were the thing fixed; this is the
       half of `hoverOpens` that should never have applied there.
       `pointerType` and not a media query: a laptop with a touch screen has
       both, and the answer is per gesture rather than per device. Touch opens
       these panels by tapping, which is the `click` handler below and is
       untouched -- and `pen` is grouped with touch because a pen that is
       drawing is dragging, not hovering. */
    function hovering(event) {
      return hoverOpens && event.pointerType === "mouse";
    }

    body.addEventListener("pointerover", function (event) {
      if (!hovering(event)) return;
      var trigger = event.target.closest(selector);
      if (!trigger || held || trigger === peeked) return;
      clearTimeout(shutTimer);
      clearTimeout(openTimer);
      /* A beat before opening, so running the pointer down the column does not
         flash a panel open on every row it crosses. */
      openTimer = setTimeout(function () {
        others();
        peeked = trigger;
        show(trigger);
      }, 120);
    });

    body.addEventListener("pointerout", function (event) {
      if (!hovering(event) || held || !event.target.closest(selector)) return;
      clearTimeout(openTimer);
      shutTimer = setTimeout(shut, 160);
    });

    /* Staying open while the pointer is inside it means a six-rung ladder --
       or a two-seller bill -- can be read without pinning it first. */
    host.addEventListener("pointerenter", function () { clearTimeout(shutTimer); });
    host.addEventListener("pointerleave", function () {
      if (!held) shutTimer = setTimeout(shut, 160);
    });

    body.addEventListener("click", function (event) {
      var trigger = event.target.closest(selector);
      if (!trigger) return;
      clearTimeout(openTimer);
      clearTimeout(shutTimer);
      var again = held === trigger;
      shut();
      if (again) return;
      others();
      held = trigger;
      show(trigger);
    });

    body.addEventListener("focusin", function (event) {
      var trigger = event.target.closest(selector);
      if (!trigger || held) return;
      /* Escape returns focus to the button it dismissed, which lands right
         back here -- so without this the panel reopened the instant it closed
         and Escape did nothing at all. Cleared as soon as focus reaches any
         other trigger, so dismissing one cell does not mute the next. */
      if (trigger === dismissed) return;
      dismissed = null;
      others();
      peeked = trigger;
      show(trigger);
    });

    /* Leaving the cell forgets that it was dismissed, so tabbing away and back
       opens it again. Without this, one Escape muted that cell for good. */
    body.addEventListener("focusout", function (event) {
      if (dismissed && event.target === dismissed) dismissed = null;
    });

    document.addEventListener("keydown", function (event) {
      if (event.key !== "Escape" || host.hidden) return;
      var trigger = held || peeked;
      shut();
      if (trigger && document.contains(trigger)) {
        dismissed = trigger;
        /* Same reason as the pane: the trigger is inside `.shell`, which does
           scroll, and returning focus to it must put the keyboard back where
           it was rather than move the rows under the reader. */
        trigger.focus({ preventScroll: true });
      }
    });

    document.addEventListener("click", function (event) {
      if (held && !event.target.closest(selector) && !host.contains(event.target)) {
        shut();
      }
    });

    /* Fixed positioning is relative to the viewport, so the panel has to be
       moved with whatever scrolled -- the page or the table. Closing on resize
       rather than chasing it: a reflow can move the button out from under it. */
    window.addEventListener("scroll", function () {
      var trigger = held || peeked;
      if (host.hidden || !trigger) return;
      if (!document.contains(trigger)) { shut(); return; }
      place(host, trigger);
    }, true);
    window.addEventListener("resize", shut);

    var api = { host: host, shut: shut };
    panels.push(api);
    return api;
  }

  hoverPanel(document.getElementById("berths"), ".berths", function (host, trigger) {
    var d = byId[trigger.dataset.berths];
    if (!d) return false;
    fillLadder(host, d);
  });

  /* The bill, out of the dropdown it used to expand into (#149). Everything
     that dropdown held except the entry bar, which is not a fee and has a
     panel of its own on the column it belongs to. */
  hoverPanel(document.getElementById("feePanel"), ".fees-open", function (host, trigger) {
    var row = rowFor(trigger.dataset.fees);
    if (!row) return false;
    host.innerHTML = billPanel(row);
  });

  /* The stated requirement, in full, from the column that prints its short
     form. Its own panel and not a line in the fee one: whether a diver may
     board at all is prior to what boarding costs, and filing a safety
     requirement under "Mandatory fees" would be the wrong name for it. */
  hoverPanel(document.getElementById("entryPanel"), ".entry-open", function (host, trigger) {
    var itin = D.itineraries[trigger.dataset.entry];
    if (!itin) return false;
    var note = entryBar(itin);
    if (!note) return false;
    host.innerHTML = note;
  }, { hoverOpens: false });


  /* Both names written and the stylesheet shows one, like the table and the
     cards: a rotation crosses the breakpoint with nothing to redraw.
     The accessible name says what the switch *does* rather than repeating
     what it is called, because on a phone the word INCLUDE is not on screen
     and a lit chip reading "Nitrox" beside a row of filters is a chip that
     looks like one. Visible text inside the accessible name, so the two
     cannot be read as different controls. */
  document.getElementById("toggles").innerHTML = D.facets.toggles.map(function (t) {
    return '<button class="chip" data-t="' + t.id + '" aria-pressed="' + t.default +
      '" aria-label="Include ' + esc(t.label.toLowerCase()) + ' in every total">' +
      '<span class="tog-long">' + esc(t.label) + "</span>" +
      '<span class="tog-short">' + esc(t.short || t.label) + "</span></button>";
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
        function (i) { return [i.port_from]; }, { byCount: true });
  /* Not skipped: these chips are ANDed, so the number is what you narrow to. */
  chips("sites", SITES, state.sites, false, null,
        function (i) { return i.dive_sites || []; }, { byCount: true });
  chips("boats", BOATS, state.boats, false, "boats",
        function (i) { return [i.boat]; }, { byCount: true });
  /* Cut where the certification changes, not after N chips: the rungs on
     screen are then "every Open Water bar" and the fold is "the Advanced ones",
     which a reader can predict before opening it. Falls back to the ordinary
     cap if the ladder ever has only one certification in it, because a fold
     that hides nothing meaningful should just be the normal one. */
  chips("entry", ENTRY, state.entry, false, "entry",
        function (i) { return [entryText(i)]; },
        {
          moreWord: "stricter",
          limit: function (live) {
            var first = live.length ? live[0].id.split(" + ")[0] : "";
            var n = 0;
            while (n < live.length && live[n].id.split(" + ")[0] === first) n += 1;
            return n === live.length ? chipLimit() : n;
          }
        });
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

  /* The On sale chip, counted like every other bank: against the rows that
     pass all the *other* filters, so the number answers "what if I picked
     this too?".

     It used to hold a count of the whole dataset and never move, on the
     reasoning that it should say how much there is to find rather than how
     much the filters have left, and that a 0 under an unrelated month filter
     would read as "no sales" rather than "none in June".

     That reasoning does not survive the rest of the panel. Every other number
     here is filter-relative, and one that is not teaches the reader nothing
     except that this one lies: "On sale 268" beside a June table with no sale
     in it is a promise the click then breaks, and the reader discovers "none
     in June" by ending up with an empty table instead of by reading a 0. The
     stale count did not avoid the confusion, it deferred it past a click.

     So it counts live, and `passes` gained a `"sale"` skip so it can exclude
     itself the way `months`, `ports` and `boats` already do -- without that,
     switching it on would make its own count equal the visible rows and it
     could never guide the way back.

     Zero keeps the chip rather than hiding it, which is where this departs
     from `chips()` — and the reason is stronger than the symmetry it breaks.
     **"Nothing here is on sale" is an answer.** A reader deciding between two
     weeks wants to know that one of them has no discount to wait for, and a
     control that disappears tells them nothing at all; a bank can drop an
     unreachable option because the neighbouring chips still carry the rule,
     but a lone toggle has no neighbours to carry it.

     So it stays, dimmed and unclickable, saying 0 against a title that spells
     out what the 0 is about. Still clickable while it is switched *on*, for
     the same reason a picked chip survives at zero: the way out must not
     disappear. */
  var onSale = document.getElementById("onSale");
  if (onSaleCount) {
    /* Shown from here on, and hidden again only by showView() on the sale
       view, where the filter *is* the view and the chip could only ever be
       pressed to no effect. Zero does not hide it -- see above. */
    onSale.hidden = false;
    BANKS.push({
      recount: function () {
        var n = 0;
        D.departures.forEach(function (dep) {
          if (!dep.sale) return;
          if (passes(dep, D.itineraries[dep.itinerary_id], "sale")) n += 1;
        });
        onSale.textContent = "On sale " + n;
        var dead = n === 0 && !state.onSaleOnly;
        onSale.disabled = dead;
        onSale.title = dead
          ? "No sailing on sale among the trips these filters leave"
          : n + (n === 1 ? " sailing is" : " sailings are") + " on sale here";
      }
    });
    onSale.addEventListener("click", function () {
      state.onSaleOnly = !state.onSaleOnly;
      onSale.setAttribute("aria-pressed", state.onSaleOnly);
      draw();
    });
  }

  /* The last chip with no number on it, and the one whose effect was hardest
     to guess: it removes about one row in seven and nothing said so until you
     pressed it and counted what moved (#141).

     Counted the way the On sale chip is counted, which is what "the way every
     other filter carries one" means -- live, against the rows the *other*
     filters leave, so it answers "what if I pressed this too?" rather than
     standing at a season total that disagrees with everything around it. That
     needs `passes` to let this facet exclude itself, exactly as `months`,
     `ports`, `boats` and `sale` already do: without it, switching the chip on
     would take its own count to zero and the way back would disappear.

     The number is what pressing it **leaves**, which is what every other chip
     on this page means by a number: On sale says how many rows you get, a
     month chip says how many rows you get. This one shipped once counting what
     it removes -- the sold-out sailings themselves -- on the reading that the
     label says "hide" so the figure should be the size of what is hidden. That
     makes it the one chip whose number has to be subtracted from something
     before it means anything, and it puts the largest number on the emptiest
     result.

     Zero keeps the chip, disabled, for the reason the On sale chip keeps its
     own: "nothing here is sold out" is an answer, and a control that vanishes
     tells the reader nothing at all. Still clickable while it is switched
     *on*, so the way out never disappears. */
  var soldOut = document.getElementById("hideSold");
  if (soldOutCount) {
    soldOut.hidden = false;
    BANKS.push({
      recount: function () {
        var n = 0;
        D.departures.forEach(function (dep) {
          if (!dep.bookable) return;
          if (passes(dep, D.itineraries[dep.itinerary_id], "soldout")) n += 1;
        });
        soldOut.textContent = "Hide sold out " + n;
        /* Zero here is "every trip these filters leave is sold out", which is
           a different sentence from the other chips' zero and worth saying in
           full: the rows are there, they are all unbookable, and pressing this
           would empty the table. */
        var dead = n === 0 && !state.hideSoldOut;
        soldOut.disabled = dead;
        soldOut.title = dead
          ? "Every trip these filters leave is sold out, so this would empty "
            + "the table"
          : n + (n === 1 ? " sailing is" : " sailings are") +
            " still bookable here" +
            (state.hideSoldOut ? " — the rest are hidden" : "");
      }
    });
  }
  soldOut.addEventListener("click", function () {
    state.hideSoldOut = !state.hideSoldOut;
    soldOut.setAttribute("aria-pressed", state.hideSoldOut);
    draw();
  });

  document.getElementById("reset").addEventListener("click", function () {
    state.months.clear(); state.ports.clear(); state.sites.clear();
    state.boats.clear(); state.entry.clear();
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
    /* The Include switches are left where the visitor put them. "Clear all"
       sits inside the bar that names the live filters and clears what that bar
       lists, and the switches are no longer on it -- so resetting them here
       would be an unnamed side effect that silently moves every total on the
       page. Turning one back on is the switch itself, which is on screen. */
    Array.prototype.forEach.call(document.querySelectorAll(".chip"), function (chip) {
      /* The "more" control is a disclosure, not a filter -- writing
         aria-pressed onto it would give it toggle semantics it does not have. */
      if (chip.dataset.more) return;
      chip.setAttribute("aria-pressed",
        chip.dataset.t ? String(!!state.toggles[chip.dataset.t]) : "false");
    });
    repaintBanks();
    labelFilters();
    draw();
  });

  /* A bank that was expanded to reach a chip, or that is holding a chosen chip
     out of its hidden tail, has to be rebuilt from the set that changed --
     repainting is the only thing that puts those chips back where they belong.
     Every bank, listed once: it was five of the six, and Entry bar was the one
     left out, so clearing a bar chip left its chip pressed. */
  function repaintBanks() {
    ["months", "ports", "sites", "boats", "entry", "sellers"].forEach(function (id) {
      var node = document.getElementById(id);
      if (node && node.repaint) node.repaint();
    });
  }

  /* One more page into both hosts -- but the one on screen first, and the
     other after the frame that shows it.
     Both are still always filled, which is the contract: a phone rotated to
     landscape crosses the breakpoint with no redraw, and a table that had
     been scrolled would otherwise meet a card list holding the first page.
     What changed is when the second one is paid. Below 760px `#body` is
     `display:none` and above it `#cards` is, so half of every append was
     parsed inside the scroll for a layout nobody was looking at: 30ms of the
     190 a page of 120 cost on a mid-range phone, in the frame the reader was
     waiting on. It is a `setTimeout` and not `requestAnimationFrame` --
     rendering happens at the end of a frame, so a rAF callback stalls the
     paint it was meant to get out of the way of.
     `filled` rather than a queue of pending slices: each host records how far
     it is filled and `fillRest` appends whatever it is behind by, so a flush
     that runs late, twice, or after a `draw` that rebuilt both hosts is
     harmless. Nothing here can leave a host short -- `drawEverything` flushes
     synchronously, because `Ctrl+F` searching a host a task behind is the
     silent truncation the whole append exists to avoid. */
  var flushing = 0;
  function fillRest(host) {
    var n = drawn - filled[host];
    if (n <= 0) return;
    var render = host === "body" ? renderRows : renderCards;
    document.getElementById(host).insertAdjacentHTML(
      "beforeend", render(lastRows, filled[host], n));
    filled[host] = drawn;
  }

  function appendPage(count) {
    var n = Math.min(count, lastRows.length - drawn);
    if (n <= 0) return;
    drawn += n;
    fillRest(narrow.matches ? "cards" : "body");
    if (!flushing) {
      flushing = setTimeout(function () {
        flushing = 0;
        fillRest("body");
        fillRest("cards");
      }, 0);
    }
  }

  /* Append the next page of rows as the table is scrolled. */
  document.querySelector(".shell").addEventListener("scroll", function () {
    if (drawn >= lastRows.length) return;
    var shell = this;
    if (shell.scrollTop + shell.clientHeight < shell.scrollHeight - 600) return;
    appendPage(STEP_ROWS);
  }, { passive: true });

  /* Draw the rest of the rows the moment the browser's own find is opened.
     Chunking means only the drawn rows are in the DOM, so Ctrl+F would search
     120 of 838 -- the one thing it costs. The keydown arrives before the find
     bar does, so the remaining rows can be in place by the time it is typed
     into. Cmd+F on a Mac, F3 on Windows, and "/" in Firefox's quick find. */
  function drawEverything() {
    appendPage(lastRows.length - drawn);
    /* Both hosts now, not a task from now: the find bar is about to be typed
       into and a host a page behind is a search of 120 rows out of 1,122
       reported as a search of all of them. */
    clearTimeout(flushing);
    flushing = 0;
    fillRest("body");
    fillRest("cards");
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
  /* The menu too: its labels shorten below the card breakpoint, and `draw`
     puts the selected value back through `paintSort`. */
  var onWidthChange = function () { orderColumns(); buildSortMenu(); draw(true); };
  [compact, narrow].forEach(function (mq) {
    if (mq.addEventListener) mq.addEventListener("change", onWidthChange);
    else if (mq.addListener) mq.addListener(onWidthChange);
  });


  /* THE DRAWER, AND WHAT IT MAY NOT HIDE.
   *
     The banks fold away at every width now rather than under 1000px, so the
     rule that used to apply to phones applies to everything: a chosen filter
     can be behind a closed panel, and a table quietly answering a narrower
     question than the one on screen is the failure this page exists to report
     in other people. Two things stop it. The button carries a count -- of the
     filters behind it -- and the bar under it names each one and drops it on
     a press.

     Both are about *hidden* controls, which is what fixes their scope. A chip
     in a closed bank has nothing on screen saying it is on; the two Include
     switches are on the toolbar at every width, lit, an inch from the button,
     and so belong to neither. They were in both once, on the reasoning that
     the count should cover every control not as it opened, and that gave a
     badge reading "2" over a drawer holding nothing that put it there. */
  var filtersToggle = document.getElementById("filtersToggle");
  var filtersCount = document.getElementById("filtersCount");
  var activeBar = document.getElementById("activeBar");
  var activePills = document.getElementById("activePills");

  /* What the button is counting is what is behind it.
   *
     The two Include switches used to be counted here, on the reasoning that
     the number should measure every control that is not as it was when the
     page opened. That was the wrong question. This number exists because a
     closed drawer can hide an active filter -- and the switches are never
     behind it: they sit on the toolbar at every width, lit, an inch from the
     button, saying their own state. Counting them made the badge read "2"
     over a drawer holding nothing that put it there, and a badge that
     promises something the drawer does not hold is a worse lie than the one
     it was guarding against. */
  function activeFilters() {
    var n = state.months.size + state.ports.size + state.sites.size +
      state.boats.size + state.entry.size + state.sellers.size;
    if (state.hideSoldOut) n += 1;
    if (state.onSaleOnly) n += 1;
    if (state.nightsMin !== null) n += 1;
    if (state.nightsMax !== null) n += 1;
    return n;
  }

  /* Every live filter as one pill, each naming the bank it came from.
     The bank's name is on the pill because the value alone is ambiguous
     across banks -- "Hurghada" is a port and could as easily have been a reef
     -- and because the reader is being told what to reopen if they want more
     of the same. */
  var PILL_BANKS = [
    { set: "months", label: "month", name: function (v) {
        var m = D.facets.months.filter(function (x) { return x.id === v; })[0];
        return m ? m.label : String(v);
      } },
    { set: "ports", label: "from" },
    { set: "sites", label: "reef" },
    { set: "boats", label: "boat" },
    { set: "entry", label: "entry" },
    { set: "sellers", label: "sold by", name: function (v) {
        return SELLER_LABELS[v] || v;
      } }
  ];

  function paintActive() {
    var out = [];
    PILL_BANKS.forEach(function (bank) {
      state[bank.set].forEach(function (v) {
        out.push({ bank: bank.label, text: bank.name ? bank.name(v) : String(v),
                   set: bank.set, value: v });
      });
    });
    if (state.onSaleOnly) out.push({ bank: "", text: "on sale", set: "onSaleOnly" });
    if (state.hideSoldOut) out.push({ bank: "", text: "sold out hidden", set: "hideSoldOut" });
    if (state.nightsMin !== null) {
      out.push({ bank: "nights", text: "from " + state.nightsMin, set: "nightsMin" });
    }
    if (state.nightsMax !== null) {
      out.push({ bank: "nights", text: "to " + state.nightsMax, set: "nightsMax" });
    }
    /* No pill for the Include switches. This bar names what is filtering the
       table and offers to drop it; a switch filters nothing -- it changes what
       every total means -- and an "EXCLUDING nitrox" pill under a heading
       reading "Filtering on" said otherwise. Nothing is lost by leaving it
       out: unlike a chip in a closed bank, the switch is on screen with its
       own state showing, which is why it is on the toolbar at all. */

    activeBar.hidden = out.length === 0;
    activePills.innerHTML = out.map(function (p) {
      return '<button type="button" class="pill-drop" data-set="' + esc(p.set) +
        '" data-v="' + esc(p.value === undefined ? "" : p.value).replace(/"/g, "&quot;") +
        '" title="Remove this filter">' +
        (p.bank ? '<span class="pb">' + esc(p.bank) + "</span>" : "") +
        "<span>" + esc(p.text) + "</span><span class=\"px\" aria-hidden=\"true\">×</span>" +
        "</button>";
    }).join("");
  }

  activePills.addEventListener("click", function (event) {
    var button = event.target.closest("button.pill-drop");
    if (!button) return;
    var set = button.dataset.set, v = button.dataset.v;
    if (set === "onSaleOnly") {
      state.onSaleOnly = false;
      onSale.setAttribute("aria-pressed", "false");
    } else if (set === "hideSoldOut") {
      state.hideSoldOut = false;
      soldOut.setAttribute("aria-pressed", "false");
    } else if (set === "nightsMin") {
      state.nightsMin = null; nmin.value = "";
    } else if (set === "nightsMax") {
      state.nightsMax = null; nmax.value = "";
    } else {
      /* The months bank holds numbers and every other one holds strings; the
         chip that set it knows which, and so does the set it went into. */
      state[set].delete(state[set].has(+v) ? +v : v);
      repaintBanks();
    }
    draw();
  });

  function labelFilters() {
    var n = activeFilters();
    filtersCount.textContent = n ? String(n) : "";
    filtersCount.hidden = n === 0;
    filtersToggle.classList.toggle("active", n > 0);
    paintActive();
    paintBankPick();
  }
  filtersToggle.addEventListener("click", function () {
    var open = filtersToggle.getAttribute("aria-expanded") !== "true";
    filtersToggle.setAttribute("aria-expanded", String(open));
    document.getElementById("filterPanel").hidden = !open;
  });

  /* WHICH BANK IS SHOWING.
   *
     Five banks stacked made the panel as tall as the sum of the longest of
     each -- 77 boat chips under 35 reefs under six ports -- so the fold saved
     a screen of buttons and put a screen of buttons behind it. One at a time,
     the panel is the height of the bank that was asked for.

     The picker carries a count per bank for the same reason the button
     carries one: a filter set in a bank you are not looking at is exactly the
     thing a one-at-a-time panel could hide. */
  var BANK_META = [
    /* Month leads, and the drawer opens on it: it is the filter most readers
       reach for, and it stood on the toolbar until the toolbar stopped
       carrying row filters at all. The two sale chips follow it, because a
       reader who used to press them out here will look at the top of the
       drawer first. */
    { k: "months", label: "Month",
      note: "the month the trip departs in — a week crossing into the next " +
            "one is filed under the month it sails" },
    { k: "flags", label: "Sale & sold out",
      note: "what a seller has marked down, and whether trips nobody can " +
            "still book stay on the table" },
    { k: "ports", label: "Departs from",
      note: "the harbour the trip leaves from — a one-way run returning " +
            "elsewhere still leaves from the port you pick" },
    { k: "sites", label: "Dive sites",
      note: "read from the operator’s own description of the trip, never " +
            "from the key-regions list beside it" },
    { k: "boats", label: "Boat",
      note: "everything one vessel runs across the season" },
    { k: "entry", label: "Entry bar",
      note: "certification and logged dives together, least demanding first — " +
            "holding the level alone still turns you away at the dock" },
    { k: "sellers", label: "Sold by",
      note: "both sites list the sailing, or only one of them does" },
    { k: "nights", label: "Nights",
      note: "blank on either side means unbounded there" }
  ];
  var bankPick = document.getElementById("bankPick");
  var bankTitle = document.getElementById("bankTitle");
  var bankNote = document.getElementById("bankNote");
  var openBank = "months";

  function bankCount(k) {
    if (k === "nights") {
      return (state.nightsMin !== null ? 1 : 0) + (state.nightsMax !== null ? 1 : 0);
    }
    /* Two switches rather than a set of chips, so there is nothing to take a
       `size` of -- and both count, because the picker's number answers "is
       anything on in a bank I am not looking at". */
    if (k === "flags") {
      return (state.onSaleOnly ? 1 : 0) + (state.hideSoldOut ? 1 : 0);
    }
    return state[k] ? state[k].size : 0;
  }

  function paintBankPick() {
    bankPick.innerHTML = BANK_META.map(function (b) {
      var n = bankCount(b.k);
      return '<button type="button" class="bank-tab" role="tab" data-bank="' + b.k +
        '" aria-selected="' + (b.k === openBank) + '">' + esc(b.label) +
        (n ? '<span class="bcount">' + n + "</span>" : "") + "</button>";
    }).join("");
    var meta = BANK_META.filter(function (b) { return b.k === openBank; })[0] || BANK_META[0];
    bankTitle.textContent = meta.label;
    bankNote.textContent = meta.note;
    Array.prototype.forEach.call(document.querySelectorAll(".bank"), function (node) {
      node.hidden = node.dataset.bank !== openBank;
    });
  }
  bankPick.addEventListener("click", function (event) {
    var button = event.target.closest("button[data-bank]");
    if (!button) return;
    openBank = button.dataset.bank;
    paintBankPick();
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
    D.meta.counts.boats + " boats bookable by the berth · all prices in " +
    D.meta.currency;

  /* The build, to the minute, in the colophon rather than in the toolbar.
     It belongs beside the crawl date, which is the other half of "how current
     is this" and was already down there; over the table it was a fourth clause
     on a line about the fleet, answering a question nobody reading that line
     had asked. Minutes because the page is rebuilt several times an hour on a
     busy day and a date alone cannot tell two of those apart, which is the
     whole point of showing it.

     Written from the payload, so the only literal build stamp in the file
     stays the one inside `"built":"..."` -- see the note in the markup. */
  var builtStamp = document.getElementById("builtStamp");
  if (builtStamp) {
    builtStamp.textContent = " · page built " +
      (D.meta.built || D.meta.generated);
  }

  /* ---------- the three views ---------- */

  /* Trips, on sale, and the change history, switched in one document rather
     than served as three. The payload is inlined and the sale view is the
     trips view's own rows with the markdown filter held on, so a second
     document would ship those megabytes again to answer a question the first
     one already holds the data for.

     The hash is the address -- #trips, #sale, #history -- which is the whole
     reason a view can be linked to or reloaded into. Nothing else on this page
     writes to the URL, so there is nothing to collide with. */
  var VIEWS = ["trips", "sale", "history"];
  /* What each view is called where a view has to be named: the rail item it
     lights and the browser tab. All three views printed one title before, so a
     bookmark of #history said "trips" and three history entries shared one
     name -- the title being the only thing a bookmark, a tab strip or a
     history list has to read. */
  var VIEW_TITLES = { trips: "Trips", sale: "On sale", history: "History" };
  var BASE_TITLE = document.title;

  /* One pane each. The sale view had no pane of its own once and drew into the
     table's with the markdown filter held down, which made the rail's middle
     entry a second way to press a chip and left the discount overview folded
     above the table. Three questions, three panes. */
  var panes = {
    trips: document.getElementById("tablePane"),
    sale: document.getElementById("salePane"),
    history: document.getElementById("historyPane")
  };
  var navItems = {
    trips: document.getElementById("navTrips"),
    sale: document.getElementById("navSale"),
    history: document.getElementById("navHistory")
  };
  var statsHost = document.getElementById("stats");
  var shellEl = document.querySelector(".shell");
  /* Whether the deals book held anything, and therefore whether there is a
     sale view to offer at all. Settled at boot, below. */
  var saleView = false;

  function viewFromHash() {
    var name = (window.location.hash || "").replace(/^#/, "");
    return VIEWS.indexOf(name) < 0 ? "trips" : name;
  }

  /* A view the page declined to give is not one the address bar may go on
     claiming. `showView` silently rewrote `#nonsense`, and `#sale` where there
     is no deals book, to trips -- and left the hash alone, so what a visitor
     bookmarked was a link to a view they had not been shown, with nothing
     saying so. `replace` rather than assignment: correcting a wrong address is
     not a place to go back to.

     An empty hash is left empty. It claims nothing, so it is not lying, and
     rewriting a bare URL the moment the page loads is a change the visitor did
     not ask for. */
  function settleHash(name) {
    var raw = window.location.hash;
    if (raw && raw !== "#" + name) window.location.replace("#" + name);
  }

  function showView(name, focus) {
    if (VIEWS.indexOf(name) < 0) name = "trips";
    /* A view with nothing behind it is not offered and cannot be reached by
       typing its name into the address bar either. Same rule as the On sale
       chip's: a control that does nothing must not be dressed as one that
       does. */
    if (name === "sale" && !saleView) name = "trips";
    settleHash(name);
    if (name === state.view) return;

    state.view = name;

    VIEWS.forEach(function (id) {
      panes[id].hidden = id !== name;
      if (id === name) navItems[id].setAttribute("aria-current", "page");
      else navItems[id].removeAttribute("aria-current");
    });

    /* Rows shown, boats, itineraries count what the table is showing, so they
       belong to the one view that has a table. Left up elsewhere they would be
       three numbers about a table that is not on screen.

       Blanked rather than removed, which is the whole of what keeps the header
       still. `hidden` took the block out of the flow and the masthead is the
       taller of the title and these numbers, so switching view resized it --
       72px to 57 on a laptop, 87 to 44 on a phone -- and the rail, the toolbar
       and the first row of prices all jumped with it. `visibility` keeps the
       box and its height while taking the numbers off the screen and out of
       the accessibility tree, so the two rules do not have to be traded
       against each other. Reserving the space here rather than as a
       `min-height` on the masthead means the reserve is always exactly what
       the numbers need, instead of a constant that drifts the day the font
       or the wording changes. */
    statsHost.classList.toggle("off", name !== "trips");

    /* The tab, the history entry and the bookmark. Prefixed rather than
       appended, because every one of those three truncates from the end and
       the part that differs is the part worth keeping. Trips is the default
       view and keeps the plain title. */
    document.title = name === "trips"
      ? BASE_TITLE : VIEW_TITLES[name] + " \u00b7 " + BASE_TITLE;

    /* The table is drawn once, by the trips view, and nothing else disturbs
       it: leaving and returning filters nothing, so what was drawn and where
       it was scrolled to are both kept. It used to be redrawn on every
       crossing because the sale view was the same table under a filter.
       `drawn === 0` is the first visit. */
    if (name === "trips" && !drawn) draw(false);

    /* Focus the pane that just appeared. The rail items are links to `#trips`,
       `#sale` and `#history`, which match no element id -- they are addresses
       for this page's own router, not fragments -- so the browser has nothing
       to move focus to and does not try: focus stayed on the link while the
       whole content area was replaced behind it, and back and forward, which
       have no link at all, stranded it wherever it sat.

       Not at boot: nothing has been activated yet, and taking focus off the
       document on load is a change the visitor did not ask for. */
    /* `preventScroll`, because this shell has no scroll to give. Focusing an
       element asks the browser to bring it into view, and the nearest thing it
       can scroll here is the document -- which is `overflow:hidden` and
       exactly the window tall, so there is nothing to bring into view and
       nothing should move. iOS obliges anyway when the layout has any slack at
       all, and what moves is the whole shell: the masthead and the rail go off
       the top and the footer floats over bare canvas. The pane still takes
       focus, which is the part a screen reader and a keyboard need. */
    if (focus) panes[name].focus({ preventScroll: true });
  }

  /* Nothing here has a scroll position worth restoring, and restoring one is
     how the shell ends up panned.
   *
     The window does not scroll -- `body` is `overflow:hidden` and exactly the
     viewport tall -- so the only offset a browser could put back is slack it
     found in the layout, which is the iOS bug the `dvh` rule in the stylesheet
     exists to remove. `dvh` removes the slack on iOS 15.4 and up; this removes
     the thing that goes looking for it, on every browser and every version.
     The table's own scroll position is inside `.shell` and is not what this
     governs. */
  if ("scrollRestoration" in history) history.scrollRestoration = "manual";

  window.addEventListener("hashchange", function () { showView(viewFromHash(), true); });

  drawNotice();
  drawChanges();
  drawSaleMoves();
  /* The overview is built once, whether or not it is the view being opened:
     it is what decides whether there is a sale view at all, and it is a few
     dozen rows against a table of 1,122. */
  saleView = drawDeals();
  if (saleView) navItems.sale.hidden = false;

  /* Draws the table as part of settling the view, so there is no first paint
     of a view the address bar did not ask for. The trips count is written by
     that draw; the sale count is not filter-relative and is set here. */
  showView(viewFromHash(), false);
  if (saleView && !drawn) countRail();
})();
