  flip(bank, value) {
    var next = (this.state[bank] || []).slice();
    var at = next.indexOf(value);
    if (at < 0) next.push(value); else next.splice(at, 1);
    var patch = {}; patch[bank] = next;
    this.setState(patch);
  }

  facetChips(bank, list, cap) {
    var self = this;
    var on = this.state[bank] || [];
    return list.slice(0, cap).map(function (e) {
      var live = on.indexOf(e[0]) >= 0;
      return {
        label: e[0], count: e[1].toLocaleString("en-IE"),
        style: touchChip(live), countStyle: countStyle(live),
        pick: function () { self.flip(bank, e[0]); }
      };
    });
  }

  renderVals() {
    var self = this;
    var s = this.state;
    var v = VARIANT[(s.nitroxOn ? 1 : 0) + "," + (s.gearOn ? 1 : 0)];

    var kept = ROWS.filter(function (r) { return passes(r, s); });
    kept.sort(function (a, b) {
      var x = sortVal(a, s.sort, v), y = sortVal(b, s.sort, v);
      if (x < y) return -s.dir;
      if (x > y) return s.dir;
      return a.start < b.start ? -1 : 1;
    });

    /* Three orders, not twelve headers: a phone has no header row to press,
       and these are the three questions a diver on a dock actually asks. */
    var ORDERS = [
      { k: "start", dir: 1, label: "soonest" },
      { k: "total", dir: 1, label: "cheapest" },
      { k: "perdive", dir: 1, label: "per dive" }
    ];
    var at = 0;
    for (var i = 0; i < ORDERS.length; i++) if (ORDERS[i].k === s.sort) at = i;

    var pills = 0;
    ["months", "ports", "sites", "boats", "bars", "sellers"].forEach(function (b) {
      pills += (s[b] || []).length;
    });
    if (s.onSale) pills++;
    if (s.hideSold) pills++;

    var sheet = [
      { title: "Month", key: "months",
        chips: MONTHS.map(function (m) {
          var live = s.months.indexOf(m[0]) >= 0;
          return { label: m[1], count: (MONTH_COUNTS[m[0]] || 0).toLocaleString("en-IE"),
                   style: touchChip(live), countStyle: countStyle(live),
                   pick: function () { self.flip("months", m[0]); } };
        }) },
      { title: "Departs from", key: "ports", chips: this.facetChips("ports", FACETS.ports, 8) },
      { title: "Dive sites", key: "sites", chips: this.facetChips("sites", FACETS.sites, 12) },
      { title: "Entry bar", key: "bars", chips: this.facetChips("bars", FACETS.bars, 17) },
      { title: "Sold by", key: "sellers", chips: this.facetChips("sellers", FACETS.sellers, 3) }
    ].map(function (b) {
      var n = (s[b.key] || []).length;
      return {
        title: b.title, chips: b.chips, badge: n ? String(n) : "",
        badgeStyle: n
          ? "font-family:var(--font-mono); font-size:9.5px; background:var(--accent); color:#fff; border-radius:8px; padding:0 5px; line-height:15px"
          : "display:none"
      };
    });

    return {
      W: W, H: H,
      themeCls: (this.props.theme || "light") === "dark" ? "dark" : "",
      shown: kept.length.toLocaleString("en-IE"),
      rows: kept.slice(0, 40).map(function (r) { return decorate(r, v, s.nitroxOn); }),
      tailNote: kept.length
        ? "The live page pages the rest in as you scroll."
        : "Nothing matches. Drop a filter.",

      drawer: s.drawer,
      listing: !s.drawer,
      toggleDrawer: function () { self.setState({ drawer: !s.drawer }); },
      drawerBtnStyle: touchButton(s.drawer || pills > 0),
      anyFilter: pills > 0,
      nFilters: String(pills),
      clearAll: function () {
        self.setState({ months: [], sellers: [], ports: [], sites: [], boats: [],
                        bars: [], onSale: false, hideSold: false, nmin: null, nmax: null });
      },
      sheet: sheet,

      sortLabel: ORDERS[at].label,
      sortBtnStyle: touchButton(false),
      cycleSort: function () {
        var next = ORDERS[(at + 1) % ORDERS.length];
        self.setState({ sort: next.k, dir: next.dir });
      }
    };
  }
