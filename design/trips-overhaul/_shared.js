/* Shared helpers for the trips-overview artboards.
   build.py splices this into each .dc.html ahead of the Component class.
   Plain classic JS: Design Components take no import/export. */

/* The four Include-switch readings, in the order build.py packs them. Each
   row carries all four as they were read off the live page rather than
   arithmetic done here: nitrox and gear normalise per basis in pricing.py,
   and a prototype re-deriving them would be inventing a price. */
var VARIANT = { "1,1": 0, "0,1": 1, "0,0": 2, "1,0": 3 };

/* Chip label against the value the rows carry. The season is four months, so
   the pair is written out rather than derived. */
var MONTHS = [["May", "May"], ["Jun", "June"], ["Jul", "July"], ["Aug", "August"]];
var SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
             "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/* Least demanding first, which is not the order the counts give. The reader
   goes down this bank looking for the last bar they still clear, so it is a
   ladder rather than a list of popular options. */
var BAR_ORDER = ["OW", "OW + 5", "OW + 10", "OW + 15", "OW + 20", "OW + 25",
                 "OW + 30", "OW + 40", "ADV", "ADV + 10", "ADV + 15",
                 "ADV + 20", "ADV + 25", "ADV + 30", "ADV + 35", "ADV + 40",
                 "ADV + 50"];

var COLS = [
  { k: "start",   t: "Dates",          w: 112, zone: "trip",
    title: "Departure and return, and the nights between" },
  { k: "boat",    t: "Boat",           w: 138, zone: "trip",
    title: "The vessel, and how many guests it sleeps" },
  { k: "trip",    t: "Trip",           w: 170, zone: "trip",
    title: "The itinerary as its operator names it, and the harbours" },
  { k: "sites",   t: "Dive sites",     w: 128, zone: "trip",
    title: "Reefs read from the operator’s own description of the trip" },
  { k: "entry",   t: "Entry bar",      w: 74,  short: "Entry",  zone: "trip",
    title: "Certification and logged dives the operator requires" },
  { k: "base",    t: "Advertised",     w: 104, zone: "bill", num: true,
    title: "The berth price the seller leads with" },
  { k: "nitrox",  t: "Nitrox",         w: 66,  zone: "bill", num: true,
    title: "What breathing gas costs on this boat, or that it is bundled" },
  { k: "fees",    t: "Mandatory fees", w: 104, short: "Mandatory", zone: "bill", num: true,
    title: "Everything charged on top that you cannot decline" },
  { k: "total",   t: "Total",          w: 158, zone: "bill", num: true,
    title: "The cheapest whole bill any seller quotes for this sailing" },
  { k: "perdive", t: "Per dive",       w: 78,  zone: "bill", num: true,
    title: "Total over the fewest dives the operator states — a ceiling" },
  { k: "places",  t: "Places",         w: 80,  zone: "seats", num: true,
    title: "The seller’s claim on its booking page, true when it was read" },
  { k: "seller",  t: "Seller",         w: 78,  zone: "seats",
    title: "Who lists this sailing" }
];

var GRID = COLS.map(function (c) { return c.w + "px"; }).join(" ");

var GROUPS = [
  { label: "The trip",     span: 5, zone: "trip" },
  { label: "The bill",     span: 5, zone: "bill" },
  { label: "Availability", span: 2, zone: "seats" }
];

var BANKS = [
  { k: "ports",  label: "Departs from", title: "Departs from",
    note: "the harbour the trip leaves from" },
  { k: "sites",  label: "Dive sites",   title: "Dive sites",
    note: "read from the operator’s own description, never its region list" },
  { k: "boats",  label: "Boat",         title: "Boat",
    note: "everything one vessel runs across the season" },
  { k: "bars",   label: "Entry bar",    title: "Entry bar",
    note: "least demanding first — certification and logged dives together" },
  { k: "sellers", label: "Sold by",     title: "Sold by",
    note: "both sites list it, or only one of them does" },
  { k: "nights", label: "Nights",       title: "Trip length",
    note: "" }
];

function eur(n) { return "€" + Math.round(n).toLocaleString("en-IE"); }

function eurRange(lo, hi) {
  if (lo == null) return "—";
  return Math.round(lo) === Math.round(hi)
    ? eur(lo)
    : eur(lo) + "–" + Math.round(hi).toLocaleString("en-IE");
}

function dayOf(iso) { return ("0" + Number(iso.slice(3, 5))).slice(-2); }
function monOf(iso) { return SHORT[Number(iso.slice(0, 2)) - 1]; }

/* "01–08 May", and the long form only where the trip crosses a month. */
function dateSpan(start, end) {
  return monOf(start) === monOf(end)
    ? dayOf(start) + "–" + dayOf(end) + " " + monOf(start)
    : dayOf(start) + " " + monOf(start) + " – " + dayOf(end) + " " + monOf(end);
}

function initialState(drawerOpen, bank) {
  return {
    sort: "start", dir: 1,
    months: [], sellers: [], ports: [], sites: [], boats: [], bars: [],
    onSale: false, hideSold: false,
    nitroxOn: true, gearOn: true,
    nmin: null, nmax: null,
    drawer: !!drawerOpen,
    bank: bank || "ports"
  };
}

/* ------------------------------------------------------------------ */
/* Filtering: OR inside a bank, AND across banks, which is what the chips on
   the live page do. */

function passes(r, s) {
  if (s.months.length && s.months.indexOf(monOf(r.start)) < 0) return false;
  if (s.ports.length && s.ports.indexOf(r.from) < 0) return false;
  if (s.boats.length && s.boats.indexOf(r.boat) < 0) return false;
  if (s.bars.length && s.bars.indexOf(r.entry) < 0) return false;
  if (s.sites.length) {
    var hit = false;
    for (var i = 0; i < s.sites.length; i++) {
      if (r.sites.indexOf(s.sites[i]) >= 0) { hit = true; break; }
    }
    if (!hit) return false;
  }
  if (s.sellers.length && s.sellers.indexOf(sellerCase(r)) < 0) return false;
  if (s.onSale && !r.sale) return false;
  if (s.hideSold && (r.places.kind === "soldout" || r.places.n === 0)) return false;
  if (s.nmin != null && r.nights < s.nmin) return false;
  if (s.nmax != null && r.nights > s.nmax) return false;
  return true;
}

/* Three cases because there are three, and both sellers are named: "here"
   asked the reader to work out which of the two sites this page is. */
function sellerCase(r) {
  if (r.sellers.length === 2) return "Both";
  return r.sellers[0] === "padi.com" ? "PADI only" : "liveaboard.com only";
}

function pad(n) { return ("00000" + (Math.round(Number(n)) + 100000)).slice(-6); }

function sortVal(r, k, v) {
  switch (k) {
    case "start": return r.start;
    case "boat": return r.boat.toLowerCase();
    case "trip": return r.trip.toLowerCase();
    case "sites": return (r.sites[0] || "zzz").toLowerCase();
    case "entry": return pad(BAR_ORDER.indexOf(r.entry));
    case "base": return pad(r.baseLo);
    case "nitrox": return pad(r.nitrox == null ? -1 : r.nitrox);
    case "fees": return pad(r.v[v][0]);
    case "total": return pad(r.v[v][2]);
    case "perdive": return pad(r.perdive == null ? 99999 : r.perdive);
    case "places": return pad(r.places.n == null ? -1 : r.places.n);
    case "seller": return sellerCase(r);
  }
  return "";
}

/* ------------------------------------------------------------------ */
/* Display. A {{hole}} is a dotted lookup, so every string and every style
   the markup prints is finished here. */

var MONO = "font-family:var(--font-mono); font-variant-numeric:tabular-nums;";

function decorate(r, v, nitroxOn) {
  var vv = r.v[v];
  var feeLo = vv[0], feeHi = vv[1], totLo = vv[2], totHi = vv[3];
  var p = r.places, num, word, color, weight;
  if (p.kind === "soldout") {
    num = "sold out"; word = ""; color = "var(--sold)"; weight = "600";
  } else if (p.kind === "word") {
    num = p.word; word = "operator’s word"; color = "var(--unknown)"; weight = "400";
  } else {
    num = String(p.n);
    word = p.kind === "aboard" ? "aboard" : "at this price";
    color = p.n === 0 ? "var(--warn)" : "var(--ink)";
    weight = p.n === 0 ? "600" : "500";
  }
  var placeStyle = p.kind === "soldout" || p.kind === "word"
    ? "font-size:11px; font-weight:" + weight + "; color:" + color + "; white-space:nowrap"
    : MONO + " font-size:12.5px; font-weight:" + weight + "; color:" + color + "; white-space:nowrap";

  return {
    dates: dateSpan(r.start, r.end),
    nights: r.nights + " nights",
    boat: r.boat,
    pax: r.guests == null ? "guests not stated" : r.guests + " guests",
    trip: r.trip,
    route: r.from === r.to ? r.from + " · return" : r.from + " → " + r.to,
    sites: r.sites.length ? r.sites.join(" · ") : "not named",
    sitesFull: r.sites.length ? r.sites.join(", ") : "The operator names no reef",
    entry: r.entry,
    entryTwo: !!r.entryTwo,
    base: eurRange(r.baseLo, r.baseHi),
    sale: r.sale ? "−" + r.sale + "%" : "",
    hasSale: !!r.sale,
    saleTitle: r.sale
      ? "Down from " + eur(r.saleFrom) + ", per " +
        (r.saleWho === "both" ? "liveaboard.com and padi.com" : r.saleWho) +
        " — read 31 Aug, and a markdown can end overnight"
      : "",
    nitrox: r.nitrox == null ? "included" : eur(r.nitrox),
    nitroxStyle: r.nitrox == null
      ? "font-size:11px; color:var(--ok); white-space:nowrap"
      : (nitroxOn
          ? MONO + " font-size:12px; white-space:nowrap"
          : MONO + " font-size:12px; color:var(--ink-faint); text-decoration:line-through; white-space:nowrap"),
    fees: "+" + eurRange(feeLo, feeHi),
    total: eurRange(totLo, totHi),
    split: eur(r.baseLo) + " + " + eurRange(feeLo, feeHi),
    tips: !!r.tips,
    varies: !!r.varies,
    perdive: r.perdive == null ? "—" : eur(r.perdive),
    dives: r.dives == null ? "not stated" : r.dives + "+ dives",
    placeNum: num,
    placeWord: word,
    placeNumStyle: placeStyle,
    lav: r.sellers.indexOf("liveaboard.com") >= 0,
    padi: r.sellers.indexOf("padi.com") >= 0
  };
}

/* ------------------------------------------------------------------ */
/* Chips. One vocabulary for every bank, so a pressed chip looks the same
   wherever it sits. */

var CHIP = "font:inherit; font-size:11.5px; cursor:pointer; padding:3px 9px; " +
           "border:1px solid var(--rule-strong); background:transparent; " +
           "color:var(--ink-dim); border-radius:2px; white-space:nowrap; " +
           "display:inline-flex; align-items:center; gap:5px";
var CHIP_ON = "font:inherit; font-size:11.5px; cursor:pointer; padding:3px 9px; " +
              "border:1px solid var(--accent); background:var(--accent-soft); " +
              "color:var(--accent); font-weight:500; border-radius:2px; " +
              "white-space:nowrap; display:inline-flex; align-items:center; gap:5px";

function chipStyle(on) { return on ? CHIP_ON : CHIP; }
function countStyle(on) {
  return MONO + " font-size:9.5px; color:" +
    (on ? "var(--accent)" : "var(--ink-faint)") + "; opacity:.85";
}

/* A switch, not a filter: it changes what every total in the table means,
   so it reads as on/off rather than as one more chip. */
function switchStyle(on) {
  return "font:inherit; font-size:11.5px; cursor:pointer; padding:3px 9px 3px 7px; " +
    "border-radius:2px; display:inline-flex; align-items:center; gap:6px; " +
    (on ? "border:1px solid var(--accent); background:var(--accent-soft); color:var(--accent); font-weight:500"
        : "border:1px solid var(--rule-strong); background:transparent; color:var(--ink-faint)");
}
function dotStyle(on) {
  return "width:7px; height:7px; border-radius:50%; background:" +
    (on ? "var(--accent)" : "var(--rule-strong)");
}

/* Phone chrome. 44px minimum on anything a thumb has to hit — the page is
   built to work on a phone in a dive shop, where hover does not exist. */
function touchChip(on) {
  return "font:inherit; font-size:12.5px; cursor:pointer; min-height:34px; " +
    "padding:6px 12px; border-radius:2px; display:inline-flex; align-items:center; " +
    "gap:6px; white-space:nowrap; " +
    (on ? "border:1px solid var(--accent); background:var(--accent-soft); color:var(--accent); font-weight:500"
        : "border:1px solid var(--rule-strong); background:transparent; color:var(--ink-dim)");
}
function touchButton(on) {
  return "font:inherit; font-size:13px; cursor:pointer; min-height:44px; " +
    "padding:0 14px; border-radius:2px; display:inline-flex; align-items:center; " +
    "gap:7px; " +
    (on ? "border:1px solid var(--accent); background:var(--accent-soft); color:var(--accent); font-weight:500"
        : "border:1px solid var(--rule-strong); background:var(--panel); color:var(--ink-dim)");
}
