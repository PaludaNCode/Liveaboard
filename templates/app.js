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
    months: new Set(), ports: new Set(), sites: new Set(),
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

  /* ---------- columns ---------- */

  function disclosure(dep) {
    if (!dep.fees_known) return ["none", "not looked at"];
    if (!dep.mandatory_known) return ["partial", "optional only"];
    return ["full", "required stated"];
  }

  var COLS = [
    { k: "start", t: "Depart", stick: true, v: function (d) { return d.start; } },
    { k: "end", t: "Return", v: function (d) { return d.end; } },
    { k: "nights", t: "Nts", num: true, v: function (d) { return d.nights; } },
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
    { k: "total", t: "True cost", num: true,
      v: function (d, i, m) { return m.total; },
      show: function (d, i, m) {
        if (!d.mandatory_known) return '<span class="dim">—</span>';
        return "<b>" + span(m) + "</b>" +
          (m.tips === "unpriced" ? '<span class="plus"> + tips</span>' : "");
      } },
    /* Price per dive is what divers compare on, so it earns a column even
       though the denominator is worked out rather than published. Marked with
       a tilde wherever it is: a figure divided by an assumption is a weaker
       claim than one divided by a stated count, and the two must not look
       alike on the same page. */
    { k: "perdive", t: "Per dive", num: true,
      v: function (d, i, m) { return i.dives > 0 ? m.total / i.dives : -1; },
      show: function (d, i, m) {
        if (!d.mandatory_known || !i.dives) return '<span class="dim">—</span>';
        var value = eur(m.total / i.dives);
        return i.dives_estimated
          ? '<span class="est" title="' + i.dives +
            ' dives assumed: three a day for every full day at sea. The ' +
            'operator does not publish a count.">~' + value + "</span>"
          : value;
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
    { k: "unpriced", t: "Unpriced", num: true,
      v: function (d, i, m) { return m.unpriced.length; },
      show: function (d, i, m) {
        return m.unpriced.length
          ? '<span class="later">' + m.unpriced.length + "</span>"
          : '<span class="dim">0</span>';
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
      if (state.sites.size) {
        /* Any, not all: picking Brothers and Daedalus means "either", which is
           how somebody shops for a week rather than a checklist. */
        var sites = itin.dive_sites || [], hit = false;
        state.sites.forEach(function (s) { if (sites.indexOf(s) >= 0) hit = true; });
        if (!hit) return;
      }
      if (state.q) {
        var hay = (itin.boat + " " + itin.name + " " + itin.port_from + " " +
                   itin.port_to + " " + (itin.dive_sites || []).join(" ")).toLowerCase();
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

    document.getElementById("head").innerHTML = "<tr>" + COLS.map(function (c) {
      var dir = c.k === state.sort
        ? '<span class="dir">' + (state.dir > 0 ? "▲" : "▼") + "</span>" : "";
      return '<th tabindex="0" class="' + (c.num ? "num " : "") + (c.stick ? "stick" : "") +
        '" data-k="' + c.k + '">' + c.t + " " + dir + "</th>";
    }).join("") + "<th></th></tr>";

    document.getElementById("body").innerHTML = rows.length
      ? rows.map(function (row, n) {
          var tds = COLS.map(function (c) {
            var v = c.show ? c.show(row.d, row.i, row.m) : esc(c.v(row.d, row.i, row.m));
            return '<td class="' + (c.num ? "num " : "") + (c.cls || "") +
              (c.stick ? " stick" : "") + '">' + v + "</td>";
          }).join("");
          var open = state.open === row.d.id;
          return '<tr class="row' + (row.d.bookable ? "" : " gone") + '">' + tds + '<td><button class="expand" data-n="' + n +
            '" aria-expanded="' + open + '">' + (open ? "−" : "+") + "</button></td></tr>" +
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

  function chips(host, items, picked, numeric) {
    var node = document.getElementById(host);
    node.innerHTML = items.map(function (it) {
      return '<button class="chip" data-v="' +
        esc(it.id).replace(/"/g, "&quot;") + '" aria-pressed="false">' +
        esc(it.label || it.id) + ' <span class="dim">' + it.n + "</span></button>";
    }).join("");
    node.addEventListener("click", function (event) {
      var button = event.target.closest("button");
      if (!button) return;
      var v = numeric ? +button.dataset.v : button.dataset.v;
      if (picked.has(v)) picked.delete(v); else picked.add(v);
      button.setAttribute("aria-pressed", picked.has(v));
      draw();
    });
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
    state.q = "";
    state.nightsMin = state.nightsMax = null;
    state.hideSoldOut = false;
    soldOut.setAttribute("aria-pressed", "false");
    document.getElementById("q").value = "";
    nmin.value = ""; nmax.value = "";
    D.facets.toggles.forEach(function (t) { state.toggles[t.id] = t.default; });
    Array.prototype.forEach.call(document.querySelectorAll(".chip"), function (chip) {
      chip.setAttribute("aria-pressed",
        chip.dataset.t ? String(!!state.toggles[chip.dataset.t]) : "false");
    });
    draw();
  });

  document.getElementById("metaLine").textContent =
    D.meta.counts.departures.toLocaleString("en-IE") + " departures · " +
    D.meta.counts.boats + " boats · all prices in " + D.meta.currency +
    " · built " + D.meta.generated;

  drawNotice();
  draw();
})();
