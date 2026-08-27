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

  var DATA = JSON.parse(document.getElementById("payload").textContent);

  /* Tiers that count without the visitor asking. Mirrors DEFAULT_ON_TIERS in
     liveaboard/taxonomy.py — change one and you must change the other. */
  var DEFAULT_ON_TIERS = { base: true, mandatory: true, customary: true };

  var LEVEL_ORDER = DATA.facets.levels.map(function (l) { return l.id; });

  var euro = new Intl.NumberFormat("en-IE", {
    style: "currency", currency: DATA.meta.currency,
    minimumFractionDigits: 0, maximumFractionDigits: 0
  });

  var state = {
    view: "date",
    sort: "date",
    toggles: {},
    months: new Set(),
    routes: new Set(),
    levels: new Set(),
    themes: new Set(),
    open: new Set()
  };

  DATA.facets.toggles.forEach(function (t) { state.toggles[t.id] = t.default; });

  /* ---------- pricing ---------- */

  function lineCharged(line) {
    if (line.included || line.tier === "optional") return 0;
    if (line.toggle) return state.toggles[line.toggle] ? line.display.amount : 0;
    return DEFAULT_ON_TIERS[line.tier] ? line.display.amount : 0;
  }

  function totalFor(dep) {
    var sum = 0;
    for (var i = 0; i < dep.lines.length; i++) sum += lineCharged(dep.lines[i]);
    return sum;
  }

  function metricsFor(dep) {
    var total = totalFor(dep);
    return {
      total: total,
      perNight: dep.nights > 0 ? total / dep.nights : total,
      surcharge: total - dep.base,
      markup: dep.base > 0 ? (total - dep.base) / dep.base * 100 : 0
    };
  }

  /* ---------- filtering ---------- */

  function passesFilters(dep) {
    var itin = DATA.itineraries[dep.itinerary_id];
    if (state.months.size && !state.months.has(dep.month)) return false;
    if (state.routes.size && !state.routes.has(itin.route)) return false;
    if (state.themes.size) {
      var hit = itin.themes.some(function (t) { return state.themes.has(t); });
      if (!hit) return false;
    }
    if (state.levels.size) {
      /* "I am qualified to X" means show everything at or below X, not only
         trips demanding exactly X. */
      var ceiling = -1;
      state.levels.forEach(function (l) {
        ceiling = Math.max(ceiling, LEVEL_ORDER.indexOf(l));
      });
      if (LEVEL_ORDER.indexOf(itin.level) > ceiling) return false;
    }
    return true;
  }

  var SORTERS = {
    date: function (a, b) { return a.start < b.start ? -1 : a.start > b.start ? 1 : 0; },
    true_cost: function (a, b) { return metricsFor(a).total - metricsFor(b).total; },
    per_night: function (a, b) { return metricsFor(a).perNight - metricsFor(b).perNight; },
    base: function (a, b) { return a.base - b.base; },
    markup: function (a, b) { return metricsFor(b).markup - metricsFor(a).markup; },
    transparency: function (a, b) { return b.transparency - a.transparency; }
  };

  function groupsFor(departures) {
    var buckets = new Map();
    departures.forEach(function (dep) {
      var key, label;
      if (state.view === "boat") {
        var itin = DATA.itineraries[dep.itinerary_id];
        key = itin.boat_id;
        label = itin.boat + " — " + itin.operator;
      } else {
        key = dep.start.slice(0, 7);
        label = new Date(dep.start + "T00:00:00Z").toLocaleDateString("en-GB", {
          month: "long", year: "numeric", timeZone: "UTC"
        });
      }
      if (!buckets.has(key)) buckets.set(key, { key: key, label: label, items: [] });
      buckets.get(key).items.push(dep);
    });

    var groups = Array.from(buckets.values());
    groups.sort(function (a, b) {
      return state.view === "boat" ? a.label.localeCompare(b.label)
                                   : (a.key < b.key ? -1 : 1);
    });
    groups.forEach(function (g) { g.items.sort(SORTERS[state.sort]); });
    return groups;
  }

  /* ---------- rendering ---------- */

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function dateRange(dep) {
    var opts = { day: "numeric", month: "short", timeZone: "UTC" };
    var a = new Date(dep.start + "T00:00:00Z").toLocaleDateString("en-GB", opts);
    var b = new Date(dep.end + "T00:00:00Z").toLocaleDateString("en-GB", opts);
    return a + " – " + b;
  }

  function honestyClass(score) {
    return score >= 0.85 ? "good" : score >= 0.7 ? "mid" : "bad";
  }

  function labelFor(list, id) {
    for (var i = 0; i < list.length; i++) if (list[i].id === id) return list[i].label;
    return id;
  }

  function buildBadges(dep, itin) {
    var badges = el("div", "badges");
    if (itin.route) badges.appendChild(el("span", "badge route", labelFor(DATA.facets.routes, itin.route)));
    badges.appendChild(el("span", "badge level", labelFor(DATA.facets.levels, itin.level)));
    if (itin.requirements.min_logged_dives > 0) {
      badges.appendChild(el("span", "badge", itin.requirements.min_logged_dives + "+ dives"));
    }
    dep.peak_themes.forEach(function (t) {
      badges.appendChild(el("span", "badge peak", labelFor(DATA.facets.themes, t) + " in season"));
    });
    if (dep.spaces_left !== null && dep.spaces_left !== undefined && dep.spaces_left <= 4) {
      badges.appendChild(el("span", "badge spaces", dep.spaces_left + " places left"));
    }
    return badges;
  }

  function buildPriceBlock(dep, m) {
    var block = el("div", "price-block");

    var cost = el("div", "true-cost");
    cost.appendChild(el("span", "cur", "€"));
    cost.appendChild(document.createTextNode(Math.round(m.total).toLocaleString("en-IE")));
    block.appendChild(cost);

    block.appendChild(el("div", "per-night", euro.format(m.perNight) + " per night"));

    var advertised = el("div", "advertised");
    if (m.surcharge > 0.5) {
      advertised.appendChild(el("span", "was", "advertised " + euro.format(dep.base)));
      advertised.appendChild(document.createTextNode(" · "));
      advertised.appendChild(el("span", "markup", "+" + Math.round(m.markup) + "%"));
    } else {
      advertised.appendChild(el("span", "markup none", "no extras to add"));
    }
    block.appendChild(advertised);

    var honesty = el("div", "honesty");
    honesty.appendChild(el("span", "honesty-label", "Honesty "));
    honesty.appendChild(el("span", "honesty-value", Math.round(dep.transparency * 100) + "%"));
    var bar = el("div", "honesty-bar");
    var fill = el("div", "honesty-fill " + honestyClass(dep.transparency));
    fill.style.width = Math.round(dep.transparency * 100) + "%";
    bar.appendChild(fill);
    honesty.appendChild(bar);
    block.appendChild(honesty);

    return block;
  }

  function buildFeeTable(dep, m) {
    var table = el("table", "fees");
    var head = el("thead");
    var headRow = el("tr");
    ["Line", "Type", "Amount", "Counted"].forEach(function (h, i) {
      headRow.appendChild(el("th", i >= 2 ? "num" : "", h));
    });
    head.appendChild(headRow);
    table.appendChild(head);

    var body = el("tbody");
    dep.lines.forEach(function (line) {
      var charged = lineCharged(line);
      var isBase = line.code === "base_fare";
      var counted = isBase || charged > 0;
      var row = el("tr", (counted ? "" : "off") + (isBase ? " base-row" : ""));

      var nameCell = el("td");
      nameCell.appendChild(document.createTextNode(line.label));
      if (line.note) nameCell.appendChild(el("span", "fee-note", line.note));
      if (line.converted && line.fx) {
        nameCell.appendChild(el("span", "fee-note",
          "converted from " + line.quoted.amount + " " + line.quoted.currency +
          " at " + line.fx.rate + " (" + line.fx.as_of + ")"));
      }
      row.appendChild(nameCell);

      var tierCell = el("td");
      if (!isBase) tierCell.appendChild(el("span", "tier " + line.tier, line.tier));
      row.appendChild(tierCell);

      row.appendChild(el("td", "num", euro.format(line.display.amount)));

      var statusCell = el("td", "num");
      if (line.included) {
        statusCell.appendChild(el("span", "pill-included", "in the fare"));
      } else if (counted) {
        statusCell.appendChild(document.createTextNode(euro.format(charged)));
      } else {
        statusCell.appendChild(el("span", "pill-off",
          line.tier === "optional" ? "optional" : "switched off"));
      }
      row.appendChild(statusCell);

      body.appendChild(row);
    });

    var totalRow = el("tr", "total");
    totalRow.appendChild(el("td", null, "True cost"));
    totalRow.appendChild(el("td"));
    totalRow.appendChild(el("td"));
    totalRow.appendChild(el("td", "num", euro.format(m.total)));
    body.appendChild(totalRow);

    table.appendChild(body);
    return table;
  }

  function buildDetail(dep, itin, m) {
    var detail = el("div", "detail");
    detail.hidden = !state.open.has(dep.id);
    detail.appendChild(buildFeeTable(dep, m));

    var dl = el("dl", "detail-meta");
    if (itin.summary) {
      dl.appendChild(el("dt", null, "The trip"));
      dl.appendChild(el("dd", null, itin.summary));
    }
    dl.appendChild(el("dt", null, "Dive sites"));
    dl.appendChild(el("dd", null, itin.dive_sites.join(" · ")));

    if (itin.themes.length) {
      dl.appendChild(el("dt", null, "Classified as"));
      dl.appendChild(el("dd", null, itin.themes.map(function (t) {
        return labelFor(DATA.facets.themes, t);
      }).join(" · ")));
    }

    dl.appendChild(el("dt", null, "Requirements"));
    var req = itin.requirements;
    var parts = [labelFor(DATA.facets.levels, itin.level)];
    if (req.min_logged_dives) parts.push(req.min_logged_dives + " logged dives");
    if (req.max_depth_m) parts.push("dives to " + req.max_depth_m + " m");
    if (req.strong_current) parts.push("strong current");
    if (req.nitrox_recommended) parts.push("nitrox recommended");
    dl.appendChild(el("dd", null, parts.join(" · ")));
    if (req.notes) dl.appendChild(el("dd", null, req.notes));

    dl.appendChild(el("dt", null, "Ports"));
    dl.appendChild(el("dd", null,
      itin.one_way ? itin.port_from + " to " + itin.port_to + " (one way)"
                   : "round trip from " + itin.port_from));

    detail.appendChild(dl);
    return detail;
  }

  function buildTrip(dep) {
    var itin = DATA.itineraries[dep.itinerary_id];
    var m = metricsFor(dep);

    var card = el("article", "trip");
    var main = el("div", "trip-main");

    var left = el("div");
    left.appendChild(el("h3", "trip-title", itin.name));

    var sub = el("p", "trip-sub");
    sub.appendChild(document.createTextNode(dateRange(dep)));
    sub.appendChild(el("span", "sep", "·"));
    sub.appendChild(document.createTextNode(itin.nights + " nights, " + itin.dives + " dives"));
    sub.appendChild(el("span", "sep", "·"));
    sub.appendChild(document.createTextNode(itin.boat));
    left.appendChild(sub);

    left.appendChild(buildBadges(dep, itin));
    main.appendChild(left);
    main.appendChild(buildPriceBlock(dep, m));
    card.appendChild(main);

    var isOpen = state.open.has(dep.id);
    var button = el("button", "disclose",
      isOpen ? "Hide the breakdown" : "Where does " + euro.format(m.total) + " come from?");
    button.type = "button";
    button.setAttribute("aria-expanded", isOpen ? "true" : "false");
    button.addEventListener("click", function () {
      if (state.open.has(dep.id)) state.open.delete(dep.id);
      else state.open.add(dep.id);
      draw();
    });
    card.appendChild(button);
    card.appendChild(buildDetail(dep, itin, m));

    return card;
  }

  /* ---------- controls ---------- */

  function chipRow(containerId, options, selection, valueOf) {
    var container = document.getElementById(containerId);
    container.textContent = "";
    options.forEach(function (opt) {
      var value = valueOf ? valueOf(opt) : opt.id;
      var chip = el("button", "chip" + (selection.has(value) ? " on" : ""), opt.label);
      chip.type = "button";
      chip.setAttribute("aria-pressed", selection.has(value) ? "true" : "false");
      chip.addEventListener("click", function () {
        if (selection.has(value)) selection.delete(value);
        else selection.add(value);
        draw();
      });
      container.appendChild(chip);
    });
  }

  function drawToggles() {
    var container = document.getElementById("toggles");
    container.textContent = "";
    DATA.facets.toggles.forEach(function (t) {
      var on = state.toggles[t.id];
      var chip = el("button", "chip" + (on ? " on" : ""), (on ? "✓ " : "") + t.label);
      chip.type = "button";
      chip.setAttribute("aria-pressed", on ? "true" : "false");
      chip.addEventListener("click", function () {
        state.toggles[t.id] = !state.toggles[t.id];
        draw();
      });
      container.appendChild(chip);
    });
  }

  function drawNotice() {
    var host = document.getElementById("dataNotice");
    host.textContent = "";
    if (DATA.meta.verified) return;
    var notice = el("div", "notice");
    notice.appendChild(el("strong", null, "Seed data — these are not real quotes"));
    notice.appendChild(document.createTextNode(
      DATA.meta.notes || "Prices are researched placeholders pending a live scrape."
    ));
    host.appendChild(notice);
  }

  function drawMeta() {
    var c = DATA.meta.counts;
    document.getElementById("metaLine").textContent =
      c.departures + " departures · " + c.itineraries + " itineraries · " +
      c.boats + " boats · all prices in " + DATA.meta.currency +
      " · built " + DATA.meta.generated;
  }

  /* ---------- main draw ---------- */

  function draw() {
    drawToggles();
    chipRow("months", DATA.facets.months, state.months);
    chipRow("routes", DATA.facets.routes, state.routes);
    chipRow("levels", DATA.facets.levels, state.levels);
    chipRow("themes", DATA.facets.themes, state.themes);

    var matching = DATA.departures.filter(passesFilters);
    var results = document.getElementById("results");
    results.textContent = "";

    var summary = document.getElementById("summary");
    if (!matching.length) {
      summary.textContent = "";
      var empty = el("div", "empty",
        "No trips match those filters. Try widening the month or route.");
      results.appendChild(empty);
      return;
    }

    var totals = matching.map(function (d) { return metricsFor(d).total; });
    var cheapest = Math.min.apply(null, totals);
    var dearest = Math.max.apply(null, totals);
    summary.innerHTML = "";
    summary.appendChild(document.createTextNode("Showing "));
    summary.appendChild(el("b", null, String(matching.length)));
    summary.appendChild(document.createTextNode(
      " of " + DATA.departures.length + " departures · true cost " +
      euro.format(cheapest) + " to " + euro.format(dearest)
    ));

    groupsFor(matching).forEach(function (group) {
      var heading = el("h2", "group-heading");
      heading.appendChild(el("span", null, group.label));
      heading.appendChild(el("span", "group-count",
        group.items.length + (group.items.length === 1 ? " departure" : " departures")));
      results.appendChild(heading);
      group.items.forEach(function (dep) { results.appendChild(buildTrip(dep)); });
    });
  }

  /* ---------- wiring ---------- */

  document.querySelectorAll("[data-view]").forEach(function (button) {
    button.addEventListener("click", function () {
      state.view = button.getAttribute("data-view");
      document.querySelectorAll("[data-view]").forEach(function (other) {
        var on = other === button;
        other.classList.toggle("on", on);
        other.setAttribute("aria-checked", on ? "true" : "false");
      });
      /* Grouping by boat while sorted by date reads as noise; default the
         sort to something that makes the grouping worth having. */
      if (state.view === "boat" && state.sort === "date") {
        state.sort = "true_cost";
        document.getElementById("sort").value = "true_cost";
      }
      draw();
    });
  });

  document.getElementById("sort").addEventListener("change", function (event) {
    state.sort = event.target.value;
    draw();
  });

  document.getElementById("reset").addEventListener("click", function () {
    state.months.clear();
    state.routes.clear();
    state.levels.clear();
    state.themes.clear();
    DATA.facets.toggles.forEach(function (t) { state.toggles[t.id] = t.default; });
    draw();
  });

  drawNotice();
  drawMeta();
  draw();
})();
