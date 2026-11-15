/* Faceted explorer: loads api/explorer.json, filters (AND across groups, OR within a group), sorts, and keeps the
   whole view in the URL hash. Every value from the data is inserted with textContent, never as markup.
   Hash keys: one per facet group (sub, type, unit, region, access, status, api, conf), from, to, sort, q, and
   ids=<id>,<id>,... with an optional via=<context>: a selection of datasets (used by the merge-partner links on
   dataset pages). Unknown ids are dropped; while a selection is active a removable note names it. */
(function () {
  "use strict";
  var PAGE = 100, COLLAPSED = 10;
  var cfg, records = [];
  var searchIds = null, searchToken = 0, searchTimer = 0;
  var shown = PAGE, current = [];
  var knownIds = {}, idSel = null;
  var VIA_MAX = 160;
  var state = blank();
  var GROUPS = [
    { key: "sub", get: function (r) { return [r.pd].concat(r.sd); } },
    { key: "type", get: function (r) { return [r.type == null ? "" : String(r.type)]; } },
    { key: "unit", get: function (r) { return [r.unit_class]; } },
    { key: "region", get: function (r) { return r.regions; } },
    { key: "access", get: function (r) { return [r.access]; } },
    { key: "status", get: function (r) { return [r.status]; } },
    { key: "api", get: function (r) { return [String(r.api)]; } },
    { key: "conf", get: function (r) { return [r.conf]; } }
  ];
  var SORTS = ["best", "cites", "name", "start", "lu"];
  var options = {};   // key -> [{value, label}]
  var inputs = {};    // key|value -> checkbox
  var counters = {};  // key|value -> count span
  var $ = function (id) { return document.getElementById(id); };

  function blank() {
    return { sub: [], type: [], unit: [], region: [], access: [], status: [], api: [], conf: [],
             from: "", to: "", sort: "", q: "", ids: [], via: "" };
  }

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) { e.className = cls; }
    if (text != null) { e.textContent = text; }
    return e;
  }

  /* ---------------------------------------------------------------- hash state */

  function year(v) {
    v = (v || "").trim();
    return /^-?\d{1,4}$/.test(v) ? v : "";
  }

  function parseIds(v) {
    var out = [], seen = {};
    (v || "").split(",").forEach(function (x) {
      x = x.trim();
      if (x && !seen[x]) { seen[x] = 1; out.push(x); }
    });
    return out;
  }

  function cleanVia(v) {
    return (v || "").replace(/[\u0000-\u001f\u007f-\u009f]/g, " ").replace(/\s+/g, " ").trim().slice(0, VIA_MAX);
  }

  function parseHash(str) {
    var s = blank(), p = new URLSearchParams(str.replace(/^#/, ""));
    GROUPS.forEach(function (g) { s[g.key] = p.getAll(g.key).filter(Boolean); });
    s.from = year(p.get("from"));
    s.to = year(p.get("to"));
    s.sort = SORTS.indexOf(p.get("sort")) >= 0 ? p.get("sort") : "";
    s.q = (p.get("q") || "").trim();
    s.ids = parseIds(p.get("ids"));
    s.via = s.ids.length ? cleanVia(p.get("via")) : "";
    return s;
  }

  function serialize(s) {
    var p = new URLSearchParams();
    GROUPS.forEach(function (g) { s[g.key].forEach(function (v) { p.append(g.key, v); }); });
    if (s.from) { p.set("from", s.from); }
    if (s.to) { p.set("to", s.to); }
    if (s.sort && !(s.sort === "best" && !s.q)) { p.set("sort", s.sort); }
    if (s.q) { p.set("q", s.q); }
    if (s.ids.length) {
      p.set("ids", s.ids.join(","));
      if (s.via) { p.set("via", s.via); }
    }
    return p.toString().replace(/%2C/gi, ",");
  }

  function writeHash() {
    var h = serialize(state), url = location.pathname + (h ? "#" + h : "");
    if (location.hash.replace(/^#/, "") !== h || location.search) { history.replaceState(null, "", url); }
  }

  /* ---------------------------------------------------------------- filtering */

  function inPeriod(r) {
    var from = state.from === "" ? null : +state.from, to = state.to === "" ? null : +state.to;
    if (from == null && to == null) { return true; }
    if (r.start_year == null && r.end_year == null) { return false; }
    var s = r.start_year == null ? -Infinity : r.start_year, e = r.end_year == null ? Infinity : r.end_year;
    return (to == null || s <= to) && (from == null || e >= from);
  }

  function failures(r) {
    var f = [];
    GROUPS.forEach(function (g) {
      var sel = state[g.key];
      if (sel.length && !g.get(r).some(function (v) { return sel.indexOf(v) >= 0; })) { f.push(g.key); }
    });
    if (!inPeriod(r)) { f.push("period"); }
    if (searchIds && !searchIds.has(r.id)) { f.push("q"); }
    if (idSel && !idSel[r.id]) { f.push("ids"); }
    return f;
  }

  function cmpName(a, b) {
    return a.name.localeCompare(b.name, undefined, { sensitivity: "base" }) || (a.id < b.id ? -1 : 1);
  }
  function nullsLast(get, dir) {
    return function (a, b) {
      var x = get(a), y = get(b);
      if (x == null && y == null) { return 0; }
      if (x == null) { return 1; }
      if (y == null) { return -1; }
      return dir * (x - y);
    };
  }
  function effectiveSort() {
    if (state.sort === "best") { return state.q ? "best" : "cites"; }
    return state.sort || (state.q ? "best" : "cites");
  }
  function comparator() {
    var byCites = nullsLast(function (r) { return r.cites; }, -1);
    var first = {
      best: function (a, b) {
        var x = searchIds ? searchIds.get(a.id) : null, y = searchIds ? searchIds.get(b.id) : null;
        return x == null || y == null ? byCites(a, b) : y - x;
      },
      cites: function (a, b) { return byCites(a, b) || b.guides - a.guides; },
      name: function () { return 0; },
      start: nullsLast(function (r) { return r.start_year; }, 1),
      lu: nullsLast(function (r) { return r.lu; }, -1)
    }[effectiveSort()];
    return function (a, b) { return first(a, b) || cmpName(a, b); };
  }

  /* ---------------------------------------------------------------- facets */

  function humanLabel(group, v) {
    var map = (cfg.labels && cfg.labels[group]) || {};
    if (map[v]) { return map[v]; }
    if (v.indexOf("single-country:") === 0) { return v.slice(15); }
    var t = v.replace(/-/g, " ");
    return t.charAt(0).toUpperCase() + t.slice(1);
  }

  function buildOptions() {
    var domName = {};
    cfg.domains.forEach(function (d) { domName[d[0]] = d[1]; });
    GROUPS.forEach(function (g) {
      var counts = {};
      records.forEach(function (r) {
        var seen = {};
        g.get(r).forEach(function (v) { if (v && !seen[v]) { seen[v] = 1; counts[v] = (counts[v] || 0) + 1; } });
      });
      var vals = Object.keys(counts), label = function (v) { return v; };
      if (g.key === "sub") {
        vals = cfg.domains.map(function (d) { return d[0]; }).filter(function (v) { return counts[v]; });
        label = function (v) { return domName[v] + " (" + v + ")"; };
      } else if (g.key === "type") {
        vals.sort();
        label = function (v) { return cfg.types[v] || "Type " + v; };
      } else if (g.key === "api") {
        vals.sort().reverse();
        label = function (v) { return v === "1" ? "API or software package" : "No API or package noted"; };
      } else if (g.key === "conf") {
        vals = ["High", "Medium", "Low"].filter(function (v) { return counts[v]; });
      } else if (g.key === "access") {
        var order = ["open", "registration", "restricted", "licensed", "commercial"];
        vals.sort(function (a, b) {
          var x = order.indexOf(a), y = order.indexOf(b);
          return (x < 0 ? 9 : x) - (y < 0 ? 9 : y) || (a < b ? -1 : 1);
        });
      } else {
        vals.sort(function (a, b) { return counts[b] - counts[a] || (a < b ? -1 : 1); });
        if (g.key === "region" || g.key === "unit") { label = function (v) { return humanLabel(g.key, v); }; }
      }
      options[g.key] = vals.map(function (v) { return { value: v, label: label(v) }; });
    });
  }

  function pruneState() {
    GROUPS.forEach(function (g) {
      var known = options[g.key].map(function (o) { return o.value; });
      state[g.key] = state[g.key].filter(function (v) { return known.indexOf(v) >= 0; });
    });
    state.ids = state.ids.filter(function (id) { return knownIds[id] === 1; });
    if (!state.ids.length) { state.via = ""; }
  }

  function facetBox(key) { return document.querySelector('fieldset[data-facet="' + key + '"]'); }

  function buildFacets() {
    GROUPS.forEach(function (g) {
      var fs = facetBox(g.key), box = fs.querySelector(".opts");
      options[g.key].forEach(function (o) {
        var lab = el("label", "opt"), cb = el("input");
        cb.type = "checkbox";
        cb.value = o.value;
        if (g.key === "sub") { cb.setAttribute("aria-label", o.label.replace(/ \([A-Z]+\)$/, "") + " (" + o.value + ")"); }
        cb.addEventListener("change", function () { toggle(g.key, o.value, cb.checked); });
        lab.appendChild(cb);
        lab.appendChild(el("span", "lt", o.label));
        var ct = el("span", "ct", "");
        lab.appendChild(ct);
        box.appendChild(lab);
        inputs[g.key + "|" + o.value] = cb;
        counters[g.key + "|" + o.value] = ct;
      });
      if (options[g.key].length > COLLAPSED) {
        var more = el("button", "ex-more-opts", "");
        more.type = "button";
        more.setAttribute("aria-expanded", "false");
        more.addEventListener("click", function () {
          var open = fs.classList.toggle("all");
          more.setAttribute("aria-expanded", open ? "true" : "false");
          syncUi();
        });
        fs.appendChild(more);
      }
    });
  }

  function toggle(key, value, on) {
    var a = state[key], i = a.indexOf(value);
    if (on && i < 0) { a.push(value); }
    if (!on && i >= 0) { a.splice(i, 1); }
    changed();
  }

  function syncUi() {
    GROUPS.forEach(function (g) {
      var fs = facetBox(g.key), all = fs.classList.contains("all"), hidden = 0;
      options[g.key].forEach(function (o, i) {
        var cb = inputs[g.key + "|" + o.value], on = state[g.key].indexOf(o.value) >= 0;
        cb.checked = on;
        var hide = !all && !on && i >= COLLAPSED;
        cb.parentNode.hidden = hide;
        if (hide) { hidden++; }
      });
      var more = fs.querySelector(".ex-more-opts");
      if (more) {
        more.textContent = all ? "Show fewer" : "Show " + hidden + " more";
        more.hidden = !all && hidden === 0;
      }
    });
    $("ex-q").value = state.q;
    var best = $("ex-sort").querySelector('option[value="best"]');
    best.hidden = best.disabled = !state.q;
    $("ex-sort").value = effectiveSort();
    $("ex-from").value = state.from;
    $("ex-to").value = state.to;
  }

  /* ---------------------------------------------------------------- update + render */

  function syncSelection() {
    var box = $("ex-sel"), n = state.ids.length;
    idSel = null;
    if (!n) {
      box.hidden = true;
      return;
    }
    idSel = {};
    state.ids.forEach(function (id) { idSel[id] = 1; });
    $("ex-sel-text").textContent = "Showing only the " + n.toLocaleString("en-US") + " dataset" + (n === 1 ? "" : "s") +
      " " + (state.via || "selected by the link you followed") + ".";
    box.hidden = false;
  }

  function update() {
    syncSortOption();
    syncSelection();
    var counts = {}, vis = [];
    records.forEach(function (r) {
      var f = failures(r);
      if (f.length === 0) { vis.push(r); }
      GROUPS.forEach(function (g) {
        if (f.length === 0 || (f.length === 1 && f[0] === g.key)) {
          var seen = {};
          g.get(r).forEach(function (v) {
            if (v && !seen[v]) { seen[v] = 1; counts[g.key + "|" + v] = (counts[g.key + "|" + v] || 0) + 1; }
          });
        }
      });
    });
    Object.keys(counters).forEach(function (k) {
      var n = counts[k] || 0;
      counters[k].textContent = " (" + n.toLocaleString("en-US") + ")";
      counters[k].parentNode.classList.toggle("zero", n === 0);
    });
    vis.sort(comparator());
    current = vis;
    shown = PAGE;
    $("ex-count").textContent = vis.length.toLocaleString("en-US") + " of " + records.length.toLocaleString("en-US") +
      " dataset" + (records.length === 1 ? "" : "s");
    renderList(false);
  }

  function syncSortOption() {
    var sel = $("ex-sort"), best = sel.querySelector('option[value="best"]');
    best.hidden = best.disabled = !state.q;
    sel.value = effectiveSort();
  }

  function changed() {
    writeHash();
    update();
  }

  function renderList(append) {
    var list = $("ex-list"), start = append ? list.children.length : 0;
    if (!append) { list.textContent = ""; }
    current.slice(start, shown).forEach(function (r) { list.appendChild(renderRow(r)); });
    var more = $("ex-more"), left = current.length - shown;
    more.hidden = left <= 0;
    more.textContent = "Show " + Math.min(PAGE, Math.max(left, 0)) + " more (" + Math.max(left, 0).toLocaleString("en-US") + " not shown)";
    var note = $("ex-note");
    if (!note.dataset.msg) {
      note.hidden = current.length > 0;
      note.textContent = current.length ? "" : "No dataset matches these filters. Remove a filter or clear them all.";
    }
  }

  function safeHref(u) { return /^\/(?!\/)|^https?:\/\//.test(u) ? u : "#"; }

  function domainName(code) {
    for (var i = 0; i < cfg.domains.length; i++) { if (cfg.domains[i][0] === code) { return cfg.domains[i][1]; } }
    return code;
  }

  function renderRow(r) {
    var li = el("li", "res"), main = el("div", "res-main");
    var btn = el("button", "res-toggle", "+");
    btn.type = "button";
    btn.setAttribute("aria-expanded", "false");
    btn.setAttribute("aria-controls", "x-" + r.id);
    btn.setAttribute("aria-label", "Show details for " + r.name);
    main.appendChild(btn);
    var a = el("a", "res-name", r.name);
    a.href = safeHref(r.url);
    main.appendChild(a);
    if (r.acronym) { main.appendChild(el("span", "acr", r.acronym)); }
    var badge = el("span", "badge dom-" + r.pd, r.pd);
    badge.title = domainName(r.pd);
    main.appendChild(badge);
    li.appendChild(main);

    var meta = el("p", "res-meta"), bits = [];
    if (r.unit_class) { bits.push(r.unit_class); }
    bits.push(r.start_year != null ? r.start_year + "–" + (r.end_year != null ? r.end_year : "") : "years n/a");
    meta.appendChild(document.createTextNode(bits.join(" · ") + " · "));
    if (r.access) {
      meta.appendChild(el("span", "chip", r.access));
      meta.appendChild(document.createTextNode(" · "));
    }
    meta.appendChild(document.createTextNode(r.cites != null ? r.cites.toLocaleString("en-US") + " citations" : "citations n/a"));
    li.appendChild(meta);

    var detail = el("div", "res-detail");
    detail.id = "x-" + r.id;
    detail.hidden = true;
    li.appendChild(detail);
    btn.addEventListener("click", function () {
      var open = btn.getAttribute("aria-expanded") !== "true";
      if (open && !detail.firstChild) { fillDetail(detail, r); }
      btn.setAttribute("aria-expanded", open ? "true" : "false");
      btn.textContent = open ? "−" : "+";
      detail.hidden = !open;
    });
    return li;
  }

  function fillDetail(box, r) {
    box.appendChild(el("p", "res-summary", r.summary));
    if (!r.q) {
      box.appendChild(el("p", "meta", "Not scored: some quality dimensions could not be confirmed."));
      return;
    }
    var dl = el("dl", "dims");
    r.q.forEach(function (n, i) {
      dl.appendChild(el("dt", "", cfg.dims[i]));
      var dd = el("dd"), dots = el("span", "dots");
      dots.setAttribute("role", "img");
      dots.setAttribute("aria-label", n + " of 5");
      for (var k = 1; k <= 5; k++) { dots.appendChild(el("span", k <= n ? "dot on" : "dot")); }
      dd.appendChild(dots);
      dd.appendChild(document.createTextNode(" "));
      dd.appendChild(el("span", "n", String(n)));
      dl.appendChild(dd);
    });
    box.appendChild(dl);
  }

  /* ---------------------------------------------------------------- search + events */

  function runSearch() {
    var q = state.q, token = ++searchToken, note = $("ex-note");
    if (!q) {
      searchIds = null;
      note.dataset.msg = "";
      update();
      return;
    }
    note.dataset.msg = "1";
    note.hidden = false;
    note.textContent = "Loading the search index";
    window.CatalogSearch.ids(q).then(function (ids) {
      if (token !== searchToken) { return; }
      searchIds = ids;
      note.dataset.msg = "";
      update();
    }, function () {
      if (token !== searchToken) { return; }
      searchIds = null;
      update();
      note.dataset.msg = "";
      note.hidden = false;
      note.textContent = "Text search is unavailable right now; filters still work.";
    });
  }

  function setQuery(q, immediate) {
    state.q = q.trim();
    writeHash();
    clearTimeout(searchTimer);
    if (immediate) { runSearch(); } else { searchTimer = setTimeout(runSearch, 150); }
  }

  function fromHash() {
    state = parseHash(location.hash);
    pruneState();
    syncUi();
    update();
    runSearch();
  }

  function init() {
    var q0 = new URLSearchParams(location.search).get("q");
    state = parseHash(location.hash);
    if (q0 && !state.q) { state.q = q0.trim(); }
    window.CatalogSearch.configure(cfg.search_url);
    records.forEach(function (r) { knownIds[r.id] = 1; });
    buildOptions();
    buildFacets();
    pruneState();
    syncUi();
    writeHash();
    $("ex-q").addEventListener("input", function (e) { setQuery(e.target.value, false); });
    $("ex-form").addEventListener("submit", function (e) { e.preventDefault(); });
    $("ex-sort").addEventListener("change", function (e) { state.sort = e.target.value; changed(); });
    ["from", "to"].forEach(function (k) {
      $("ex-" + k).addEventListener("input", function (e) {
        var v = e.target.value.trim();
        state[k] = /^-?\d{1,4}$/.test(v) ? v : "";
        changed();
      });
    });
    $("ex-sel-clear").addEventListener("click", function () {
      state.ids = [];
      state.via = "";
      changed();
      $("ex-count").focus();
    });
    $("ex-clear").addEventListener("click", function () {
      state = blank();
      syncUi();
      writeHash();
      runSearch();
    });
    $("ex-more").addEventListener("click", function () {
      var list = $("ex-list"), first = list.children.length;
      shown += PAGE;
      renderList(true);
      var row = list.children[first];
      if (row) { row.querySelector(".res-name").focus(); }
    });
    var head = document.querySelector(".site-header form.search");
    if (head) {
      head.addEventListener("submit", function (e) {
        e.preventDefault();
        var v = head.querySelector("input[name=q]").value;
        $("ex-q").value = v;
        setQuery(v, true);
      });
    }
    window.addEventListener("hashchange", fromHash);
    update();
    runSearch();
  }

  function start() {
    cfg = JSON.parse($("explorer-config").textContent);
    fetch(cfg.explorer_url).then(function (r) {
      if (!r.ok) { throw new Error("HTTP " + r.status); }
      return r.json();
    }).then(function (data) {
      records = data;
      init();
    }).catch(function () {
      $("ex-count").textContent = "The dataset list could not be loaded.";
    });
  }

  if (document.readyState === "loading") { document.addEventListener("DOMContentLoaded", start); } else { start(); }
})();
