  /* One toggler for every chip bank: a chip is a value in a list, and a
     second press takes it out again. */
  flip(bank, value) {
    var next = (this.state[bank] || []).slice();
    var at = next.indexOf(value);
    if (at < 0) next.push(value); else next.splice(at, 1);
    var patch = {}; patch[bank] = next;
    this.setState(patch);
  }

  facetChips(bank, list) {
    var self = this;
    var on = this.state[bank] || [];
    return list.map(function (e) {
      var live = on.indexOf(e[0]) >= 0;
      return {
        label: e[0], count: e[1].toLocaleString("en-IE"),
        style: chipStyle(live), countStyle: countStyle(live),
        pick: function () { self.flip(bank, e[0]); }
      };
    });
  }

  renderVals() {
    var self = this;
    var s = this.state;
    var v = VARIANT[(s.nitroxOn ? 1 : 0) + "," + (s.gearOn ? 1 : 0)];

    /* ---- the table ------------------------------------------------- */
    var kept = ROWS.filter(function (r) { return passes(r, s); });
    kept.sort(function (a, b) {
      var x = sortVal(a, s.sort, v), y = sortVal(b, s.sort, v);
      if (x < y) return -s.dir;
      if (x > y) return s.dir;
      return a.start < b.start ? -1 : 1;
    });

    var boats = {}, trips = {};
    kept.forEach(function (r) { boats[r.boat] = 1; trips[r.boat + r.trip] = 1; });

    var rows = kept.slice(0, 120).map(function (r) { return decorate(r, v, s.nitroxOn); });

    /* ---- header ---------------------------------------------------- */
    var groups = GROUPS.map(function (g) {
      var w = 0, seen = 0;
      COLS.forEach(function (c) { if (c.zone === g.zone) { w += c.w; seen++; } });
      var bill = g.zone === "bill";
      return {
        label: g.label,
        style: "grid-column:span " + seen + "; padding:5px 8px 4px; " +
          "font-family:var(--font-narrow); font-size:9.5px; text-transform:uppercase; " +
          "letter-spacing:.14em; color:" + (bill ? "var(--accent)" : "var(--ink-faint)") + "; " +
          (bill ? "background:var(--bill-head); border-left:1px solid var(--bill-edge); " +
                  "border-right:1px solid var(--bill-edge); text-align:right; font-weight:600"
                : "")
      };
    });

    var heads = COLS.map(function (c) {
      var live = s.sort === c.k;
      var bill = c.zone === "bill";
      return {
        label: c.short || c.t, title: c.title,
        dir: live ? (s.dir > 0 ? "▲" : "▼") : "",
        dirStyle: "color:var(--accent); font-weight:700; font-size:8px; margin-left:3px",
        style: "padding:6px 6px 5px; cursor:pointer; user-select:none; " +
          "font-family:var(--font-narrow); font-size:9.5px; font-weight:600; " +
          "text-transform:uppercase; letter-spacing:.05em; white-space:nowrap; " +
          "overflow:hidden; text-overflow:ellipsis; " +
          "color:" + (live ? "var(--accent)" : "var(--ink-dim)") + "; " +
          (c.num ? "text-align:right; " : "") +
          (bill ? "background:var(--bill); " : "") +
          (c.k === "base" ? "border-left:1px solid var(--bill-edge); " : "") +
          (c.k === "perdive" ? "border-right:1px solid var(--bill-edge); " : ""),
        sort: function () {
          self.setState(s.sort === c.k
            ? { dir: -s.dir }
            : { sort: c.k, dir: c.num || c.k === "start" ? 1 : 1 });
        }
      };
    });

    /* ---- toolbar --------------------------------------------------- */
    var monthChips = MONTHS.map(function (m) {
      var live = s.months.indexOf(m[0]) >= 0;
      return {
        label: m[1], count: (MONTH_COUNTS[m[0]] || 0).toLocaleString("en-IE"),
        style: chipStyle(live), countStyle: countStyle(live),
        pick: function () { self.flip("months", m[0]); }
      };
    });

    /* ---- what is switched on, said once --------------------------- */
    var pills = [];
    function pillsFor(bank, label) {
      (s[bank] || []).forEach(function (val) {
        pills.push({
          bank: label, label: val,
          drop: function () { self.flip(bank, val); }
        });
      });
    }
    pillsFor("months", "month");
    pillsFor("ports", "from");
    pillsFor("sites", "reef");
    pillsFor("boats", "boat");
    pillsFor("bars", "entry");
    pillsFor("sellers", "sold by");
    if (s.onSale) pills.push({ bank: "", label: "on sale", drop: function () { self.setState({ onSale: false }); } });
    if (s.hideSold) pills.push({ bank: "", label: "sold out hidden", drop: function () { self.setState({ hideSold: false }); } });
    if (s.nmin != null) pills.push({ bank: "nights", label: "from " + s.nmin, drop: function () { self.setState({ nmin: null }); } });
    if (s.nmax != null) pills.push({ bank: "nights", label: "to " + s.nmax, drop: function () { self.setState({ nmax: null }); } });

    /* ---- the drawer ------------------------------------------------ */
    var banks = BANKS.map(function (b) {
      var live = s.bank === b.k;
      var n = b.k === "nights"
        ? (s.nmin != null ? 1 : 0) + (s.nmax != null ? 1 : 0)
        : (s[b.k] || []).length;
      return {
        label: b.label, badge: n ? String(n) : "",
        badgeStyle: n
          ? "font-family:var(--font-mono); font-size:9.5px; background:var(--accent); color:#fff; border-radius:8px; padding:0 5px; line-height:15px"
          : "display:none",
        style: "width:100%; display:flex; align-items:center; justify-content:space-between; " +
          "gap:6px; font:inherit; font-size:12px; text-align:left; cursor:pointer; " +
          "padding:6px 12px; border:0; border-left:2px solid " +
          (live ? "var(--accent)" : "transparent") + "; background:" +
          (live ? "var(--panel)" : "transparent") + "; color:" +
          (live ? "var(--accent)" : "var(--ink-dim)") + "; font-weight:" +
          (live ? "600" : "400"),
        pick: function () { self.setState({ bank: b.k }); }
      };
    });

    var current = BANKS.filter(function (b) { return b.k === s.bank; })[0];
    var bankChips = s.bank === "nights" ? [] : this.facetChips(s.bank, FACETS[s.bank]);

    return {
      W: W, H: H,
      themeCls: (this.props.theme || "light") === "dark" ? "dark" : "",
      grid: GRID,
      shown: kept.length.toLocaleString("en-IE"),
      nAll: ROWS.length.toLocaleString("en-IE"),
      nSale: N_SALE.toLocaleString("en-IE"),
      nboats: String(Object.keys(boats).length),
      nitin: String(Object.keys(trips).length),
      rows: rows,
      tailNote: kept.length > rows.length
        ? "Showing the first " + rows.length + " of " + kept.length.toLocaleString("en-IE") +
          " — the live page pages the rest in as you scroll."
        : (kept.length ? "That is every departure matching these filters."
                       : "Nothing matches. Drop a filter above."),
      groups: groups,
      heads: heads,
      monthChips: monthChips,
      activePills: pills,
      anyFilter: pills.length > 0,
      nFilters: String(pills.length),
      clearAll: function () {
        self.setState({ months: [], sellers: [], ports: [], sites: [], boats: [],
                        bars: [], onSale: false, hideSold: false, nmin: null, nmax: null });
      },

      drawer: s.drawer,
      drawerShut: !s.drawer,
      toggleDrawer: function () { self.setState({ drawer: !s.drawer }); },
      drawerBtnStyle: "font:inherit; font-size:12px; cursor:pointer; " +
        "display:inline-flex; align-items:center; gap:6px; padding:4px 9px; " +
        "border-radius:2px; " + (s.drawer || pills.length
          ? "border:1px solid var(--accent); background:var(--accent-soft); color:var(--accent); font-weight:500"
          : "border:1px solid var(--rule-strong); background:transparent; color:var(--ink-dim)"),

      banks: banks,
      bankTitle: current.title,
      bankNote: current.note,
      bankChips: bankChips,
      isNights: s.bank === "nights",
      nminVal: s.nmin == null ? "" : String(s.nmin),
      nmaxVal: s.nmax == null ? "" : String(s.nmax),
      setNmin: function (e) {
        var n = parseInt(e.target.value, 10);
        self.setState({ nmin: isNaN(n) ? null : n });
      },
      setNmax: function (e) {
        var n = parseInt(e.target.value, 10);
        self.setState({ nmax: isNaN(n) ? null : n });
      },

      nitroxStyle: switchStyle(s.nitroxOn), nitroxDot: dotStyle(s.nitroxOn),
      gearStyle: switchStyle(s.gearOn), gearDot: dotStyle(s.gearOn),
      toggleNitrox: function () { self.setState({ nitroxOn: !s.nitroxOn }); },
      toggleGear: function () { self.setState({ gearOn: !s.gearOn }); },

      saleStyle: chipStyle(s.onSale), saleCountStyle: countStyle(s.onSale),
      soldStyle: chipStyle(s.hideSold),
      toggleSale: function () { self.setState({ onSale: !s.onSale }); },
      toggleSold: function () { self.setState({ hideSold: !s.hideSold }); }
    };
  }
