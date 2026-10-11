// Okshun front end: search, filters, lot list and lot drawer. No build step.
(() => {
  "use strict";

  const PAGE = 30;
  const MULTI = ["risk", "source", "province", "make", "code", "body"];
  const SINGLE = ["max_cost", "min_year", "max_year", "max_km"];
  const RISK_ORDER = ["Low Risk", "Medium Risk", "High Risk"];
  const RISK_CLASS = { "Low Risk": "low", "Medium Risk": "medium", "High Risk": "high" };
  const RISK_WORD = { "Low Risk": "Low", "Medium Risk": "Medium", "High Risk": "High" };
  const CODE_LABEL = { code_1: "Code 1 (new)", code_2: "Code 2 (used)", code_3: "Code 3 (rebuilt)",
    code_4: "Code 4 (parts only)", code_5: "Code 5 (demolished)", none: "No code", unknown: "Code unknown" };
  const TRI = { yes: "Yes", no: "No", unknown: "Unknown" };

  const $ = (sel, root = document) => root.querySelector(sel);
  const els = {
    summary: $("#summary"), lots: $("#lots"), empty: $("#empty"), more: $("#more"),
    q: $("#q"), sort: $("#sort"), searchForm: $("#search-form"), filterForm: $("#filter-form"),
    filters: $("#filters"), toggle: $("#filters-toggle"), count: $("#filter-count"),
    drawer: $("#drawer"), drawerBody: $("#drawer-body"), scrim: $("#scrim"), demoNote: $("#demo-note"),
    navWatch: $("#nav-watch"), watchCount: $("#watch-count"), navAlerts: $("#nav-alerts"), alertCount: $("#alert-count"),
    navAccount: $("#nav-account"), saveSearch: $("#save-search"), backAll: $("#back-all"), watchBanner: $("#watch-banner"),
    pageTitle: $("#page-title"), tray: $("#compare-tray"), trayText: $("#compare-text"), toast: $("#toast"),
    auth: $("#auth"), authForm: $("#auth-form"),
  };

  const state = { offset: 0, total: 0, facets: null, lastFocus: null, providers: [], verdictRules: { min_profit: 10000, good_margin: 0.15 },
    user: null, view: "all", compare: new Set(), authMode: "login", authReason: null, lotReminders: {}, pushDevices: 0 };
  const REMINDER_CHOICES = [[1440, "1 day"], [120, "2 hours"], [30, "30 minutes"], [15, "15 minutes"]];
  const COMPARE_MAX = 4;

  // ---------- this browser's storage (history checks, preferred service) ----------
  const store = {
    get(k, fallback = null) { try { const v = localStorage.getItem(k); return v == null ? fallback : JSON.parse(v); } catch { return fallback; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); return true; } catch { return false; } },
    del(k) { try { localStorage.removeItem(k); } catch { /* storage unavailable */ } },
  };
  const lotKey = (it) => `${it.source}/${it.source_lot_id}`;

  // ---------- server calls ----------
  async function api(path, { method = "GET", body } = {}) {
    const res = await fetch(path, {
      method, credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Okshun": "1" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw Object.assign(new Error(typeof data.detail === "string" ? data.detail : "Something went wrong. Try again."), { status: res.status });
    return data;
  }

  // ---------- buyer's own data: watchlist, history checks, repair edits ----------
  // Signed out: kept in this browser. Signed in: kept in the account, with this copy as a cache.
  const notes = {
    watch: new Set(),
    data: {}, // "kind|source/lot" -> object
    loadLocal() {
      this.watch = new Set(store.get("okshun.watchlist", []));
      this.data = {};
      try {
        for (let i = 0; i < localStorage.length; i++) {
          const k = localStorage.key(i);
          const m = k && k.match(/^okshun\.(check|repairs)\.(.+)$/);
          if (m) this.data[`${m[1]}|${m[2]}`] = store.get(k);
        }
      } catch { /* storage unavailable */ }
    },
    loadServer(d) { this.watch = new Set(d.watchlist || []); this.data = d.notes || {}; state.lotReminders = d.reminders || {}; state.pushDevices = d.push_devices || 0; },
    localSnapshot() { this.loadLocal(); return { watchlist: [...this.watch], notes: { ...this.data } }; },
    clearLocal() {
      store.del("okshun.watchlist");
      try {
        const doomed = [];
        for (let i = 0; i < localStorage.length; i++) { const k = localStorage.key(i); if (/^okshun\.(check|repairs)\./.test(k)) doomed.push(k); }
        doomed.forEach((k) => localStorage.removeItem(k));
      } catch { /* storage unavailable */ }
    },
    get(kind, it) { return this.data[`${kind}|${lotKey(it)}`] || null; },
    set(kind, it, value) {
      this.data[`${kind}|${lotKey(it)}`] = value;
      if (state.user) {
        api(`/api/me/notes/${kind}/${lotKey(it)}`, { method: "PUT", body: { data: value } }).catch((e) => toast(`Couldn't save to your account: ${e.message}`));
        return true;
      }
      return store.set(`okshun.${kind}.${lotKey(it)}`, value);
    },
    del(kind, it) {
      delete this.data[`${kind}|${lotKey(it)}`];
      if (state.user) api(`/api/me/notes/${kind}/${lotKey(it)}`, { method: "DELETE" }).catch((e) => toast(e.message));
      else store.del(`okshun.${kind}.${lotKey(it)}`);
    },
    isWatched(it) { return this.watch.has(lotKey(it)); },
    toggleWatch(it) {
      const k = lotKey(it), on = !this.watch.has(k);
      on ? this.watch.add(k) : this.watch.delete(k);
      if (state.user) api(`/api/me/watchlist/${k}`, { method: on ? "PUT" : "DELETE" }).catch((e) => toast(e.message));
      else store.set("okshun.watchlist", [...this.watch]);
      return on;
    },
  };
  const FLAGS = [
    ["finance", "Finance still owed"], ["stolen", "Stolen or police interest"],
    ["writeoff", "Written off or salvage code"], ["accident", "Accident or claim history"],
  ];
  const MILEAGE_TOLERANCE = 1000; // km; report readings this far above the lot's mileage count as a mismatch

  // ---------- formatting ----------
  // South African style: spaces between thousands (R 417 720).
  const nf = { format: (v) => Math.round(Number(v)).toString().replace(/\B(?=(\d{3})+(?!\d))/g, "\u00a0") };
  const rand = (v) => (v == null ? "–" : `${v < 0 ? "−" : ""}R\u00a0${nf.format(Math.abs(v))}`);
  const km = (v) => (v == null ? "–" : `${nf.format(v)} km`);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function closesIn(iso) {
    if (!iso) return { text: "Closing time not published", soon: false };
    const ms = new Date(iso) - new Date();
    if (ms <= 0) return { text: "Closed", soon: false };
    const h = Math.floor(ms / 3.6e6), m = Math.floor((ms % 3.6e6) / 6e4);
    if (h < 1) return { text: `Closes in ${m} min`, soon: true };
    if (h < 24) return { text: `Closes in ${h} h ${m} min`, soon: h < 6 };
    const d = Math.floor(h / 24);
    return { text: `Closes in ${d} day${d > 1 ? "s" : ""} ${h % 24} h`, soon: false };
  }
  // Live webcast sales: what matters is when the sale starts and the lot's place in the running order.
  function saleTiming(it) {
    if (it.auction_type === "live" && it.auction_start && new Date(it.auction_start) > new Date()) {
      const c = closesIn(it.auction_start);
      return { text: c.text.replace("Closes in", "Live sale starts in") + (it.lot_number ? `, lot ${it.lot_number}` : ""), soon: c.soon };
    }
    return closesIn(it.auction_end);
  }
  const closesAt = (iso) => iso ? new Date(iso).toLocaleString("en-ZA", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "Not published";
  const scoreText = (n) => (n > 0 ? `+${n}` : n < 0 ? `−${Math.abs(n)}` : "0");

  function disc(item, big = false) {
    const cls = RISK_CLASS[item.risk_label] || "";
    return `<div class="disc ${cls}${big ? " big" : ""}" role="img" aria-label="${esc(item.risk_label || "Not scored")}, score ${esc(item.risk_score)}">
      <span class="score">${item.risk_score == null ? "?" : scoreText(item.risk_score)}</span>
      <span class="word">${esc(RISK_WORD[item.risk_label] || "–")}</span></div>`;
  }

  // ---------- URL state ----------
  function readParams() {
    const p = new URLSearchParams(location.search);
    state.view = p.get("view") === "watch" ? "watch" : "all";
    p.delete("lot"); p.delete("alerts");
    els.q.value = p.get("q") || "";
    els.sort.value = p.get("sort") || "ending";
    return p;
  }

  function currentParams() {
    const p = new URLSearchParams();
    if (els.q.value.trim()) p.set("q", els.q.value.trim());
    if (els.sort.value !== "ending") p.set("sort", els.sort.value);
    const fd = new FormData(els.filterForm);
    for (const k of MULTI) fd.getAll(k).forEach((v) => p.append(k, v));
    for (const k of SINGLE) { const v = fd.get(k); if (v) p.set(k, v); }
    if (fd.get("runs")) p.set("runs", "true");
    return p;
  }

  function activeFilterCount(p) {
    let n = 0;
    for (const [k] of p) if (k !== "q" && k !== "sort" && k !== "view") n++;
    return n;
  }

  // ---------- facets ----------
  function renderFacets(f, p) {
    state.facets = f;
    const selected = (k) => new Set(p.getAll(k));
    for (const box of document.querySelectorAll("[data-facet]")) {
      const key = box.dataset.facet;
      let rows = f[key] || [];
      if (key === "risk") rows = [...rows].sort((a, b) => RISK_ORDER.indexOf(a.value) - RISK_ORDER.indexOf(b.value));
      const sel = selected(key);
      box.innerHTML = rows.map((r) => {
        const label = key === "source" ? r.label : key === "code" ? (CODE_LABEL[r.value] || r.value) : r.value;
        const dot = key === "risk" ? `<span class="dot" style="background:var(--risk-${RISK_CLASS[r.value]})"></span>` : "";
        return `<label class="check"><input type="checkbox" name="${key}" value="${esc(r.value)}"${sel.has(r.value) ? " checked" : ""}>${dot}${esc(label)}<span class="n">${r.count}</span></label>`;
      }).join("");
    }
    const { min_year, max_year } = f.stats;
    for (const s of document.querySelectorAll("[data-years]")) {
      const years = [];
      for (let y = max_year; y >= min_year; y--) years.push(y);
      s.innerHTML = `<option value="">Any</option>` + years.map((y) => `<option>${y}</option>`).join("");
      s.value = p.get(s.name) || "";
    }
    for (const k of ["max_cost", "max_km"]) els.filterForm.elements[k].value = p.get(k) || "";
    els.filterForm.elements.runs.checked = p.get("runs") === "true";
    els.demoNote.hidden = !(f.source || []).some((s) => /demo/i.test(s.label));
  }

  // ---------- repair estimate and profit ----------
  const V = { worth: "Worth a look", thin: "Thin margin", not: "Not worth it at this price", inspect: "Inspect first", parts: "Parts only" };
  const V_CLASS = { [V.worth]: "v-worth", [V.thin]: "v-thin", [V.not]: "v-not", [V.inspect]: "v-inspect", [V.parts]: "v-parts" };
  const randK = (v) => `${v < 0 ? "−" : ""}R ${nf.format(Math.round(Math.abs(v) / 1000))}k`;

  // Same logic as okshun/repairs.py, applied to the buyer's own edits.
  function computeDeal(it, edits = notes.get("repairs", it) || {}) {
    if (it.repair_verdict === V.parts) return { verdict: V.parts, items: [], parts: true };
    const over = edits.over || {}, skip = new Set(edits.skip || []);
    const items = (it.repair_items || []).filter((i) => !skip.has(i.id)).map((i) => {
      const o = over[i.id];
      return o == null || o === "" ? { ...i } : { ...i, low: Number(o), high: Number(o), inspect: false, yours: true };
    });
    for (const c of edits.custom || []) items.push({ id: c.id, label: c.label, kind: "repair", low: Number(c.cost), high: Number(c.cost), inspect: false, yours: true, custom: true });
    const sum = (kind, k) => items.filter((i) => i.kind === kind && !i.inspect).reduce((a, i) => a + i[k], 0);
    const d = { items, repLow: sum("repair", "low"), repHigh: sum("repair", "high"), roadLow: sum("road", "low"), roadHigh: sum("road", "high"),
      inspect: items.some((i) => i.inspect), resale: it.resale_value, allIn: it.est_all_in_cost, edited: !!(Object.keys(over).length || skip.size || (edits.custom || []).length) };
    if (!d.resale || !d.allIn) { d.verdict = d.inspect ? V.inspect : null; return d; }
    const outLow = d.allIn + d.repLow + d.roadLow, outHigh = d.allIn + d.repHigh + d.roadHigh;
    d.profitHigh = d.resale - outLow;
    d.profitLow = d.inspect ? null : d.resale - outHigh;
    const r = state.verdictRules;
    if (d.profitHigh < r.min_profit) d.verdict = V.not;
    else if (d.inspect) d.verdict = V.inspect;
    else if (d.profitLow >= r.min_profit && d.profitLow >= r.good_margin * outHigh) d.verdict = V.worth;
    else d.verdict = V.thin;
    return d;
  }

  function profitLine(it) {
    const d = computeDeal(it);
    if (!d.verdict) return "";
    let text;
    if (d.parts) text = "Parts only";
    else if (d.verdict === V.not) text = d.profitLow == null ? `Not worth it: at most ${randK(d.profitHigh)} profit` : `Not worth it: ${randK(d.profitLow)} to ${randK(d.profitHigh)}`;
    else if (d.profitLow == null) text = `Inspect first, at most ${randK(d.profitHigh)} profit`;
    else text = `Profit ${randK(d.profitLow)} to ${randK(d.profitHigh)}`;
    return `<div class="profit ${V_CLASS[d.verdict]}">${text}${d.edited ? " (yours)" : ""}</div>`;
  }

  // ---------- history check results ----------
  function assess(it, rec) {
    const lines = [];
    let bad = false;
    if (rec.km != null && it.mileage_km != null) {
      if (rec.km - it.mileage_km > MILEAGE_TOLERANCE) {
        bad = true;
        lines.push({ bad: true, text: `The report shows ${km(rec.km)}, more than the ${km(it.mileage_km)} on this lot. The odometer may have been rolled back.` });
      } else {
        lines.push({ bad: false, text: `Mileage is consistent: ${km(rec.km)} on the report, ${km(it.mileage_km)} on the lot.` });
      }
    } else if (rec.km != null) {
      lines.push({ bad: false, text: `The report shows ${km(rec.km)}. This lot doesn't list its mileage, so there's nothing to compare.` });
    }
    for (const [key, label] of FLAGS) if ((rec.flags || []).includes(key)) { bad = true; lines.push({ bad: true, text: label }); }
    if (!lines.length) lines.push({ bad: false, text: "Nothing flagged on the report." });
    return { bad, lines };
  }

  function rowChip(it) {
    const rec = notes.get("check", it);
    if (!rec) return "";
    return assess(it, rec).bad
      ? `<span class="chip bad">History flagged</span>`
      : `<span class="chip ok">History checked</span>`;
  }

  // ---------- lots ----------
  function lotRow(it) {
    const c = saleTiming(it);
    const bid = it.current_bid != null ? `Bid ${rand(it.current_bid)}` : `Starts at ${rand(it.starting_bid)}`;
    const facts = [km(it.mileage_km), it.damage_code_raw || CODE_LABEL[it.damage_code]];
    if (it.runs_and_drives === "no") facts.push(`<span class="warn">Non-runner</span>`);
    if (it.keys_available === "no") facts.push(`<span class="warn">No keys</span>`);
    return `<li class="lot"><button type="button" class="lot-btn" data-source="${esc(it.source)}" data-lot="${esc(it.source_lot_id)}">
      ${disc(it)}
      <div>
        <p class="lot-title">${esc(it.title)} <span class="lot-variant">${esc(it.variant || "")}</span></p>
        <div class="lot-meta"><span class="plate">${esc(it.source_lot_id)}</span><span>${esc(it.source_name)}</span><span>${esc(it.province || "")}</span><span class="chip-slot">${rowChip(it)}</span></div>
        <div class="facts">${facts.map((f) => `<span>${f}</span>`).join("")}</div>
      </div>
      <div class="money">
        <div><div class="allin">${rand(it.est_all_in_cost)}</div><div class="allin-label">all-in estimate</div></div>
        <div class="bidline">${bid}</div>
        <span class="profit-slot">${profitLine(it)}</span>
        <div class="closes${c.soon ? " soon" : ""}">${c.text}</div>
      </div></button>
      <div class="lot-actions">
        <button type="button" class="star" data-watch="${esc(lotKey(it))}" aria-pressed="${notes.isWatched(it)}" aria-label="Watch ${esc(it.title)}" title="Watch">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8L3.5 9.7l5.9-.9z"/></svg></button>
        <label class="cmp" title="Compare"><input type="checkbox" data-compare="${esc(lotKey(it))}"${state.compare.has(lotKey(it)) ? " checked" : ""}><span>Compare</span></label>
      </div></li>`;
  }

  async function load({ append = false } = {}) {
    const p = currentParams();
    if (state.view === "watch") p.set("view", "watch");
    if (!append) {
      state.offset = 0;
      history.replaceState(null, "", p.toString() ? `?${p}` : location.pathname);
    }
    const n = activeFilterCount(p);
    els.count.hidden = n === 0;
    els.count.textContent = n;
    const qp = new URLSearchParams(p);
    qp.delete("view");
    qp.set("limit", PAGE);
    qp.set("offset", state.offset);
    const watchView = state.view === "watch";
    if (watchView) {
      if (!notes.watch.size) return showEmptyWatch();
      qp.delete("keys");
      [...notes.watch].slice(0, 100).forEach((k) => qp.append("keys", k));
    }
    let data;
    try {
      const res = await fetch(`/api/listings?${qp}`);
      if (!res.ok) throw new Error(res.status);
      data = await res.json();
    } catch (e) {
      els.summary.textContent = "Couldn't load auctions. Check that the Okshun server is running, then refresh.";
      return;
    }
    state.total = data.total;
    const html = data.items.map(lotRow).join("");
    if (append) els.lots.insertAdjacentHTML("beforeend", html); else els.lots.innerHTML = html;
    state.offset += data.items.length;
    els.lots.hidden = data.total === 0;
    els.empty.hidden = data.total !== 0;
    els.more.hidden = state.offset >= data.total;
    const all = state.facets?.stats?.n ?? data.total;
    const houses = state.facets?.source?.length ?? 0;
    if (watchView) {
      els.summary.textContent = `${nf.format(data.total)} watched lot${data.total === 1 ? "" : "s"} still open${n || p.get("q") ? " that match your filters" : ""}.`;
    } else {
      els.summary.textContent = n || p.get("q")
        ? `${nf.format(data.total)} of ${nf.format(all)} cars match.`
        : `${nf.format(all)} cars from ${houses} auction houses, all still open for bids.`;
    }
    els.saveSearch.hidden = watchView || !(n || p.get("q"));
    els.empty.querySelector("p").innerHTML = "<strong>No cars match these filters.</strong>";
  }

  // ---------- drawer ----------
  function costTable(it) {
    const bid = it.current_bid ?? it.starting_bid;
    const label = it.current_bid != null ? "Current bid" : "Starting bid";
    const comm = bid != null && it.buyers_commission_pct ? bid * it.buyers_commission_pct / 100 : null;
    const vatRow = it.vat_on_hammer === false
      ? `<tr><td>VAT (15%)</td><td>added</td></tr>`
      : `<tr><td class="sub">VAT</td><td class="sub">included in bid</td></tr>`;
    let gap;
    if (it.gap_to_retail != null) {
      gap = `<p class="gap">Retail value ${rand(it.estimated_retail)}, so ${rand(it.gap_to_retail)} below retail <strong>before repairs</strong>.</p>`;
    } else if (it.damage_code === "code_4" || it.damage_code === "code_5") {
      gap = `<p class="gap">${esc(CODE_LABEL[it.damage_code])}: it can't be registered for the road again, so it's only worth its parts.</p>`;
    } else gap = "";
    return `<table class="costs"><tbody>
      <tr><td>${label}</td><td>${rand(bid)}</td></tr>
      <tr><td>Buyer's commission${it.buyers_commission_pct ? ` (${it.buyers_commission_pct}%)` : ""}</td><td>${rand(comm)}</td></tr>
      <tr><td>Admin and release fees</td><td>${rand(it.fixed_fees)}</td></tr>
      ${vatRow}
      <tr class="total"><td>All-in estimate</td><td>${rand(it.est_all_in_cost)}</td></tr>
    </tbody></table>${gap}`;
  }

  function reasonsList(it) {
    return `<ul class="reasons">${(it.risk_reasons || []).map((r) => {
      const m = r.match(/^(.*) \(([+-]\d+(?:\.\d+)?)\)$/);
      if (!m) return `<li><span>${esc(r)}</span></li>`;
      const pts = Number(m[2]);
      return `<li><span>${esc(m[1])}</span><span class="pts ${pts > 0 ? "up" : "down"}">${scoreText(pts)}</span></li>`;
    }).join("")}</ul>`;
  }

  function drawerHtml(it) {
    const specs = [
      ["Mileage", km(it.mileage_km)], ["Body", it.body_type], ["Code", it.damage_code_raw || CODE_LABEL[it.damage_code]],
      ["Damage", [it.primary_damage, it.secondary_damage].filter(Boolean).join("; ") || "None listed"],
      ["Runs and drives", TRI[it.runs_and_drives]], ["Keys", TRI[it.keys_available]],
      ["Odometer", it.odometer_status || "Not stated"], ["Location", [it.branch, it.province].filter(Boolean).join(", ")],
      ["Sale", it.auction_type === "live"
        ? `Live webcast${it.auction_start ? `, starts ${closesAt(it.auction_start)}` : ""}${it.lot_number ? `, lot ${it.lot_number} in the running order` : ""}`
        : it.auction_type === "timed" ? "Timed online" : "Not stated"],
      [it.auction_type === "live" ? "Sale ends about" : "Closes", closesAt(it.auction_end)],
    ];
    const action = it.demo
      ? `<span class="btn" aria-disabled="true">Bid on ${esc(it.source_name)}</span><p>Demo listing: there's no real auction to open.</p>`
      : `<a class="btn" href="${esc(it.url)}" target="_blank" rel="noopener">Bid on ${esc(it.source_name)}</a><p>Opens the lot on the auction house's site, where you register and bid.</p>`;
    return `
      <div class="d-top"><span class="plate">${esc(it.source_lot_id)}</span><span>${esc(it.source_name)}</span>
        <button type="button" class="star d-star" data-watch="${esc(lotKey(it))}" aria-pressed="${notes.isWatched(it)}" aria-label="Watch this lot" title="Watch">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8L3.5 9.7l5.9-.9z"/></svg></button>
        <button type="button" class="d-close" id="d-close" aria-label="Close lot details">×</button></div>
      <h2 id="d-title">${esc(it.title)}</h2>
      <p class="d-variant">${esc(it.variant || "")}</p>
      <div class="d-risk">${disc(it, true)}<div><h3>Why it scored ${scoreText(it.risk_score)}</h3>${reasonsList(it)}</div></div>
      <div class="section"><h3>What you'd pay</h3>${costTable(it)}</div>
      ${dealSection(it)}
      <div class="section"><h3>Vehicle</h3><dl class="specs">${specs.map(([k, v]) => `<dt>${k}</dt><dd>${esc(v || "–")}</dd>`).join("")}</dl></div>
      ${it.description ? `<div class="section"><h3>Auction house description</h3><p class="desc">${esc(it.description)}</p></div>` : ""}
      ${checkPanel(it)}
      ${reminderSection(it)}
      <div class="d-action">${action}</div>`;
  }

  // ---------- worth fixing? ----------
  const rangeText = (i) => (i.inspect ? "Inspect first" : i.low === i.high ? rand(i.low) : `${rand(i.low)} – ${rand(i.high)}`);

  function dealSection(it) {
    if (it.repair_verdict === V.parts) {
      return `<div class="section deal"><h3>Worth fixing?</h3>
        <div class="verdict-band v-parts"><strong>Parts only.</strong> ${esc(CODE_LABEL[it.damage_code] || "This code")} cars can't be registered for the road again, so there's no repair estimate. Value it on its parts.</div></div>`;
    }
    if (!it.repair_items || !it.repair_items.length) return "";
    const edits = notes.get("repairs", it) || {};
    const over = edits.over || {}, skip = new Set(edits.skip || []);
    const row = (i) => `<tr class="${skip.has(i.id) ? "skipped" : ""}">
        <td>${esc(i.label)}</td>
        <td class="est">${rangeText(i)}</td>
        <td><label class="visually-hidden" for="ov-${i.id}">Your cost for ${esc(i.label)}</label>
          <input class="yours" id="ov-${i.id}" data-id="${i.id}" type="number" min="0" step="100" inputmode="numeric" placeholder="Your cost" value="${over[i.id] ?? ""}"></td>
        <td><label class="skip"><input type="checkbox" data-skip="${i.id}"${skip.has(i.id) ? " checked" : ""}> Skip</label></td></tr>`;
    const custom = (edits.custom || []).map((c) => `<tr><td>${esc(c.label)}</td><td class="est">Your item</td><td class="yours-fixed">${rand(c.cost)}</td>
        <td><button type="button" class="linkish" data-remove-custom="${esc(c.id)}">Remove</button></td></tr>`).join("");
    const repairs = it.repair_items.filter((i) => i.kind === "repair"), road = it.repair_items.filter((i) => i.kind === "road");
    const resaleNote = it.damage_code === "code_3" ? ` <span class="sub">(retail ${rand(it.estimated_retail)}, less the Code 3 resale discount)</span>` : "";
    return `<div class="section deal" id="deal">
      <h3>Worth fixing?</h3>
      <div id="deal-summary"></div>
      <table class="deal-table">
        <thead><tr><th>Repairs</th><th>Estimate</th><th>Your cost</th><th><span class="visually-hidden">Skip</span></th></tr></thead>
        <tbody>${repairs.map(row).join("")}${custom}</tbody>
        <thead><tr><th colspan="4">Getting it on the road</th></tr></thead>
        <tbody>${road.map(row).join("")}</tbody>
      </table>
      <form class="add-item" id="add-item">
        <label class="field">Add a repair you spotted <input name="label" required maxlength="60" placeholder="e.g. Two front tyres"></label>
        <label class="field">Cost <span class="money-input"><span aria-hidden="true">R</span><input name="cost" type="number" min="0" step="100" required inputmode="numeric"></span></label>
        <button type="submit" class="btn btn-quiet">Add</button>
      </form>
      <p class="muted">Resale value: ${rand(it.resale_value)}${resaleNote}. Estimates come from the published damage details and placeholder price ranges, not a mechanic's quote. Your figures are saved ${state.user ? "to your account" : "in this browser"}.
        <button type="button" class="linkish" id="deal-reset">Reset to estimate</button></p>
    </div>`;
  }

  // Highest hammer bid that still leaves the minimum profit with the highest repair estimate.
  // Inverts estimate_all_in: all_in = (bid × (1 + commission) + fees) × VAT factor. undefined = can't tell.
  function maxBidFor(it, d = computeDeal(it)) {
    if (d.parts || d.inspect || !d.resale || (it.current_bid ?? it.starting_bid) == null) return undefined;
    const vat = it.vat_on_hammer === false ? 1.15 : 1;
    const comm = (it.buyers_commission_pct || 0) / 100;
    const room = d.resale - d.repHigh - d.roadHigh - state.verdictRules.min_profit;
    return Math.floor(((room / vat) - (it.fixed_fees || 0)) / (1 + comm) / 500) * 500;
  }

  function renderDealSummary(it) {
    const box = $("#deal-summary");
    if (!box) return;
    const d = computeDeal(it);
    const profit = d.profitLow == null
      ? (d.profitHigh == null ? "–" : `At most ${rand(d.profitHigh)}`)
      : `${rand(d.profitLow)} to ${rand(d.profitHigh)}`;
    const why = {
      [V.worth]: "Even with the highest repair estimate, the margin is healthy.",
      [V.thin]: "It can make money, but a few surprises would wipe out the margin.",
      [V.not]: "Even with the lowest repair estimate, there's too little left over.",
      [V.inspect]: "Some damage can't be priced from the description. Get it inspected, then enter your cost for those lines.",
    }[d.verdict] || "";
    const bidNow = it.current_bid ?? it.starting_bid;
    const bidLabel = it.current_bid != null ? "current bid" : "starting bid";
    let maxBid = "";
    const target = state.verdictRules.min_profit;
    const bid = maxBidFor(it, d);
    if (bid !== undefined) {
      maxBid = bid > 0
        ? `<p class="max-bid">Bid up to <strong>${rand(bid)}</strong> to keep at least ${rand(target)} profit, even with the highest repair estimate.</p>`
        : `<p class="max-bid">At these repair costs, no bid leaves ${rand(target)} profit.</p>`;
    }
    const basis = bidNow != null ? `<p class="muted">Profit is worked out at the ${bidLabel} of ${rand(bidNow)}. Every extra rand you bid comes off it, plus commission.</p>` : "";
    const limit = notes.get("limit", it);
    let limitHtml = "";
    if (limit) {
      limitHtml = `<p class="limit-set">You'll get an alert if bidding passes ${rand(limit.bid)}. <button type="button" class="linkish" id="limit-remove">Remove</button></p>`;
    } else if (bid > 0) {
      limitHtml = `<button type="button" class="btn btn-quiet limit-btn" id="limit-set" data-bid="${bid}">Tell me if bidding passes ${rand(bid)}</button>`;
    }
    box.innerHTML = `<div class="verdict-band ${V_CLASS[d.verdict] || ""}">
        <p class="vb-head">${esc(d.verdict || "Not enough information")}</p><p class="vb-why">${why}</p></div>
      <table class="costs deal-totals"><tbody>
        <tr><td>Resale value</td><td>${rand(d.resale)}</td></tr>
        <tr><td>All-in cost to buy</td><td>− ${rand(d.allIn)}</td></tr>
        <tr><td>Repairs${d.inspect ? " (priced lines only)" : ""}</td><td>− ${d.repLow === d.repHigh ? rand(d.repLow) : `${rand(d.repLow)} – ${rand(d.repHigh)}`}</td></tr>
        <tr><td>Getting it on the road</td><td>− ${d.roadLow === d.roadHigh ? rand(d.roadLow) : `${rand(d.roadLow)} – ${rand(d.roadHigh)}`}</td></tr>
        <tr class="total"><td>Profit</td><td>${profit}</td></tr>
      </tbody></table>${maxBid}${limitHtml}${basis}`;
    $("#limit-set")?.addEventListener("click", (e) => setLimit(it, Number(e.currentTarget.dataset.bid)));
    $("#limit-remove")?.addEventListener("click", () => { notes.del("limit", it); renderDealSummary(it); toast("Bid limit removed."); });
  }

  function refreshRowProfit(it) {
    const slot = els.lots.querySelector(`.lot-btn[data-source="${CSS.escape(it.source)}"][data-lot="${CSS.escape(it.source_lot_id)}"] .profit-slot`);
    if (slot) slot.innerHTML = profitLine(it);
  }

  function wireDeal(it) {
    const deal = $("#deal");
    if (!deal) return;
    const save = (mutate) => {
      const e = structuredClone(notes.get("repairs", it) || {});
      mutate(e);
      if (!notes.set("repairs", it, e)) $("#deal-summary").insertAdjacentHTML("beforeend", `<p class="muted">This browser blocks saving; your edits last until you close the lot.</p>`);
      renderDealSummary(it);
      refreshRowProfit(it);
    };
    deal.addEventListener("input", (ev) => {
      const inp = ev.target.closest("input.yours");
      if (!inp) return;
      save((e) => { e.over = e.over || {}; if (inp.value === "") delete e.over[inp.dataset.id]; else e.over[inp.dataset.id] = Number(inp.value); });
    });
    deal.addEventListener("change", (ev) => {
      const cb = ev.target.closest("input[data-skip]");
      if (!cb) return;
      cb.closest("tr").classList.toggle("skipped", cb.checked);
      save((e) => { const s = new Set(e.skip || []); cb.checked ? s.add(cb.dataset.skip) : s.delete(cb.dataset.skip); e.skip = [...s]; });
    });
    const rerender = () => { deal.outerHTML = dealSection(it); wireDeal(it); };
    deal.addEventListener("click", (ev) => {
      const rm = ev.target.closest("[data-remove-custom]");
      if (rm) { save((e) => { e.custom = (e.custom || []).filter((c) => c.id !== rm.dataset.removeCustom); }); rerender(); }
    });
    $("#add-item").addEventListener("submit", (ev) => {
      ev.preventDefault();
      const fd = new FormData(ev.target);
      save((e) => { e.custom = [...(e.custom || []), { id: `c${Date.now()}`, label: String(fd.get("label")).trim(), cost: Number(fd.get("cost")) }]; });
      rerender();
    });
    $("#deal-reset").addEventListener("click", () => { notes.del("repairs", it); renderDealSummary(it); refreshRowProfit(it); rerender(); });
    renderDealSummary(it);
  }

  // ---------- history / odometer check panel ----------
  function checkPanel(it) {
    if (!state.providers.length) return "";
    const ids = [["VIN", it.vin], ["Engine number", it.engine_number], ["Registration", it.registration]].filter(([, v]) => v);
    const idHtml = ids.length
      ? `<dl class="idents">${ids.map(([k, v]) => `<dt>${k}</dt><dd><code>${esc(v)}</code>
          <button type="button" class="linkish copy" data-copy="${esc(v)}">Copy</button></dd>`).join("")}</dl>`
      : `<p class="muted">${it.demo ? "This demo lot has no VIN. On real lots, Okshun copies the VIN for you." : "The auction house didn't publish a VIN for this lot. Ask them for it before you bid."}</p>`;
    const pref = store.get("okshun.provider") || state.providers[0].id;
    const opts = state.providers.map((p) => `<option value="${esc(p.id)}"${p.id === pref ? " selected" : ""}>${esc(p.name)}${p.mileage === "yes" ? " (mileage history)" : ""}</option>`).join("");
    const rec = notes.get("check", it) || {};
    const flags = FLAGS.map(([k, label]) => `<label class="check"><input type="checkbox" name="flag" value="${k}"${(rec.flags || []).includes(k) ? " checked" : ""}> ${label}</label>`).join("");
    return `<div class="section check-panel">
      <h3>Check its history</h3>
      <p class="muted">Optional. Use a vehicle history service you already have an account with. Okshun opens it for you; it never logs in or sees your account.</p>
      ${idHtml}
      <label class="field">Your history service <select id="chk-provider">${opts}</select></label>
      <p class="chk-info" id="chk-info"></p>
      <button type="button" class="btn btn-quiet" id="chk-open"></button>
      <form class="chk-form" id="chk-form">
        <h4>What did the report say?</h4>
        <label class="field">Latest mileage on the report
          <span class="money-input"><input type="number" name="km" min="0" step="1" inputmode="numeric" placeholder="e.g. 148000" value="${rec.km ?? ""}"><span aria-hidden="true">km</span></span>
        </label>
        <div class="opts">${flags}</div>
        <button type="submit" class="btn">Save my check</button>
      </form>
      <div class="chk-result" id="chk-result" aria-live="polite"></div>
    </div>`;
  }

  function showCheckResult(it) {
    const box = $("#chk-result");
    if (!box) return;
    const rec = notes.get("check", it);
    if (!rec) { box.innerHTML = ""; return; }
    const { bad, lines } = assess(it, rec);
    const prov = state.providers.find((p) => p.id === rec.provider);
    const when = new Date(rec.savedAt).toLocaleDateString("en-ZA", { day: "numeric", month: "short", year: "numeric" });
    box.innerHTML = `<div class="verdict ${bad ? "bad" : "ok"}">
      <p class="verdict-head">${bad ? "Check before you bid" : "No problems recorded"}</p>
      <ul>${lines.map((n) => `<li class="${n.bad ? "bad" : ""}">${esc(n.text)}</li>`).join("")}</ul>
      <p class="muted">Saved ${when}${prov ? ` from ${esc(prov.name)}` : ""}${state.user ? " to your account" : ", in this browser only"}.
        <button type="button" class="linkish" id="chk-remove">Remove my check</button></p></div>`;
    $("#chk-remove").addEventListener("click", () => {
      notes.del("check", it);
      $("#chk-form").reset();
      showCheckResult(it);
      refreshRowChip(it);
    });
  }

  function refreshRowChip(it) {
    const row = els.lots.querySelector(`.lot-btn[data-source="${CSS.escape(it.source)}"][data-lot="${CSS.escape(it.source_lot_id)}"] .chip-slot`);
    if (row) row.innerHTML = rowChip(it);
  }

  function wireCheck(it) {
    const sel = $("#chk-provider");
    if (!sel) return;
    const info = $("#chk-info"), open = $("#chk-open");
    const update = () => {
      const p = state.providers.find((x) => x.id === sel.value);
      store.set("okshun.provider", p.id);
      const mileageNote = p.mileage === "yes"
        ? "Its reports include mileage history, so you can spot a rolled-back odometer."
        : "Its site doesn't mention mileage history, so it may not catch a rolled-back odometer.";
      info.innerHTML = `<strong>Checks:</strong> ${esc(p.checks)}.<br><strong>Needs:</strong> ${esc(p.needs)}.<br>
        <strong>Access:</strong> ${esc(p.access)}.<br>${mileageNote}`;
      open.textContent = it.vin ? `Copy VIN and open ${p.name}` : `Open ${p.name}`;
    };
    sel.addEventListener("change", update);
    update();
    open.addEventListener("click", async () => {
      const p = state.providers.find((x) => x.id === sel.value);
      if (it.vin) { try { await navigator.clipboard.writeText(it.vin); } catch { /* clipboard blocked; VIN is shown above */ } }
      window.open(p.url, "_blank", "noopener");
    });
    for (const b of document.querySelectorAll("#drawer .copy")) {
      b.addEventListener("click", async () => {
        try { await navigator.clipboard.writeText(b.dataset.copy); b.textContent = "Copied"; } catch { b.textContent = "Select and copy"; }
      });
    }
    $("#chk-form").addEventListener("submit", (e) => {
      e.preventDefault();
      const fd = new FormData(e.target);
      const kmVal = fd.get("km");
      const rec = { provider: sel.value, km: kmVal === "" ? null : Number(kmVal), flags: fd.getAll("flag"), savedAt: new Date().toISOString() };
      if (!notes.set("check", it, rec)) {
        $("#chk-result").innerHTML = `<p class="verdict bad">Couldn't save: this browser blocks local storage. Your entry is still shown until you close the lot.</p>`;
        return;
      }
      showCheckResult(it);
      refreshRowChip(it);
    });
    showCheckResult(it);
  }

  function openPanel(html, { trigger, wide = false } = {}) {
    if (els.drawer.hidden) state.lastFocus = trigger || document.activeElement;
    els.drawerBody.innerHTML = html;
    els.drawer.classList.toggle("wide", wide);
    const wasHidden = els.drawer.hidden;
    els.drawer.hidden = false;
    els.scrim.hidden = false;
    if (wasHidden) {
      els.drawer.classList.add("entering");
      requestAnimationFrame(() => requestAnimationFrame(() => els.drawer.classList.remove("entering")));
    }
    els.drawer.scrollTop = 0;
    document.body.style.overflow = "hidden";
    $("#d-close").addEventListener("click", closeLot);
    $("#d-close").focus();
  }

  async function openLot(source, lot, trigger) {
    let it;
    try {
      const res = await fetch(`/api/listings/${encodeURIComponent(source)}/${encodeURIComponent(lot)}`);
      if (!res.ok) throw new Error(res.status);
      it = await res.json();
    } catch { toast("Couldn't open that lot. It may have closed."); return; }
    openPanel(drawerHtml(it), { trigger });
    wireReminders(it);
    $(".d-star").addEventListener("click", (e) => {
      toggleWatch(e.currentTarget);
      const row = els.lots.querySelector(`.star[data-watch="${CSS.escape(lotKey(it))}"]`);
      if (row) row.setAttribute("aria-pressed", e.currentTarget.getAttribute("aria-pressed"));
    });
    wireCheck(it);
    wireDeal(it);
  }

  function closeLot() {
    if (els.drawer.hidden) return;
    els.drawer.hidden = true;
    els.scrim.hidden = true;
    document.body.style.overflow = "";
    state.lastFocus?.focus?.();
  }

  // ---------- reminders, bid limits, calendar ----------
  const offsetsFor = (it) => state.lotReminders[lotKey(it)] ?? state.user?.reminder_offsets ?? [120];
  const whenText = (offs) => offs.length ? REMINDER_CHOICES.filter(([m]) => offs.includes(m)).map(([, l]) => l).join(", ") + " before" : "No reminders";

  function reminderSection(it) {
    const live = it.auction_type === "live" && it.auction_start;
    const moment = live ? "the live sale starts" : "bidding closes";
    const cal = (it.auction_start || it.auction_end)
      ? `<a class="btn btn-quiet cal-btn" href="/api/listings/${encodeURIComponent(it.source)}/${encodeURIComponent(it.source_lot_id)}/calendar.ics" download>Add to calendar</a>` : "";
    if (!state.user) {
      return `<div class="section reminders"><h3>Reminders</h3>
        <p class="muted">Get a reminder on your phone before ${moment}.</p>
        <div class="rem-actions"><button type="button" class="btn btn-quiet" id="rem-signin">Sign in for reminders</button>${cal}</div></div>`;
    }
    const offs = offsetsFor(it), custom = lotKey(it) in state.lotReminders, watched = notes.isWatched(it);
    return `<div class="section reminders"><h3>Reminders</h3>
      <p class="muted">${watched ? `Remind me before ${moment}:` : `Watch this lot to be reminded before ${moment}. Ticking a time watches it.`}</p>
      <div class="rem-opts">${REMINDER_CHOICES.map(([m, l]) => `<label class="check"><input type="checkbox" data-rem="${m}"${watched && offs.includes(m) ? " checked" : ""}> ${l} before</label>`).join("")}</div>
      <p class="muted rem-note">${custom ? `Custom times for this lot. <button type="button" class="linkish" id="rem-default">Use my usual times</button>.` : `Your usual times are set in your account.`}
        ${state.pushDevices ? "" : `To get them on your phone, turn on phone notifications in your account.`}</p>
      <div class="rem-actions">${cal}</div></div>`;
  }

  function wireReminders(it) {
    $("#rem-signin")?.addEventListener("click", () => openAuth("Sign in to get reminders before auctions close."));
    const boxes = [...els.drawerBody.querySelectorAll("input[data-rem]")];
    const save = async (offsets) => {
      try {
        await api(`/api/me/watchlist/${lotKey(it)}`, { method: "PUT", body: { reminders: offsets } });
        if (offsets === null) delete state.lotReminders[lotKey(it)]; else state.lotReminders[lotKey(it)] = offsets;
        if (!notes.isWatched(it)) { notes.watch.add(lotKey(it)); refreshStars(it); renderNav(); }
        const sec = els.drawerBody.querySelector(".reminders");
        sec.outerHTML = reminderSection(it);
        wireReminders(it);
        toast(offsets === null ? "Using your usual reminder times." : `Reminders: ${whenText(offsets).toLowerCase()}.`);
      } catch (e) { toast(e.message); }
    };
    boxes.forEach((b) => b.addEventListener("change", () => save(boxes.filter((x) => x.checked).map((x) => Number(x.dataset.rem)))));
    $("#rem-default")?.addEventListener("click", () => save(null));
  }

  function refreshStars(it) {
    document.querySelectorAll(`.star[data-watch="${CSS.escape(lotKey(it))}"]`).forEach((s) => s.setAttribute("aria-pressed", String(notes.isWatched(it))));
  }

  function setLimit(it, bid) {
    if (!state.user) return openAuth("Sign in to get an alert when bidding passes your limit.");
    notes.set("limit", it, { bid, setAt: new Date().toISOString() });
    if (!notes.isWatched(it)) { notes.toggleWatch(it); refreshStars(it); renderNav(); }
    renderDealSummary(it);
    toast(`We'll alert you if bidding passes ${rand(bid)}.`);
  }

  // ---------- phone notifications ----------
  const pushSupported = () => "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
  const isIOS = () => /iphone|ipad|ipod/i.test(navigator.userAgent);
  const standalone = () => window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;

  function keyBytes(b64) {
    const s = atob((b64 + "=".repeat((4 - (b64.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/"));
    return Uint8Array.from(s, (c) => c.charCodeAt(0));
  }

  async function currentSubscription() {
    if (!pushSupported()) return null;
    const reg = await navigator.serviceWorker.getRegistration();
    return reg ? reg.pushManager.getSubscription() : null;
  }

  async function enablePush() {
    if (!pushSupported()) {
      throw new Error(isIOS() && !standalone()
        ? "On iPhone, first add Okshun to your Home Screen (Share, then Add to Home Screen), open it from there, and turn this on again."
        : "This browser can't show notifications. Try Chrome, Edge or Firefox.");
    }
    const perm = await Notification.requestPermission();
    if (perm !== "granted") throw new Error("Notifications are blocked for this site. Allow them in your browser's site settings, then try again.");
    const reg = await navigator.serviceWorker.ready;
    const { key } = await api("/api/push/key");
    let sub;
    try {
      sub = (await reg.pushManager.getSubscription()) || await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(key) });
    } catch (e) {
      throw new Error(e && e.name === "NotAllowedError"
        ? "Notifications are blocked for this site. Allow them in your browser's site settings, then try again."
        : "This device couldn't sign up for notifications right now. Check your connection and try again.");
    }
    await api("/api/me/push", { method: "POST", body: sub.toJSON() });
    state.pushDevices = Math.max(1, state.pushDevices);
  }

  async function disablePush() {
    const sub = await currentSubscription();
    if (sub) {
      await api("/api/me/push", { method: "DELETE", body: { endpoint: sub.endpoint } }).catch(() => {});
      await sub.unsubscribe().catch(() => {});
    }
  }

  // ---------- small helpers ----------
  let toastTimer;
  function toast(msg) {
    els.toast.textContent = msg;
    els.toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { els.toast.hidden = true; }, 4000);
  }
  const shortDate = (iso) => new Date(iso).toLocaleString("en-ZA", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

  // ---------- views: all lots or watchlist ----------
  function setView(view) {
    state.view = view;
    els.navWatch.setAttribute("aria-pressed", String(view === "watch"));
    els.pageTitle.textContent = view === "watch" ? "Your watchlist" : "Every car auction in one search";
    els.backAll.hidden = view !== "watch";
    load();
  }

  function showEmptyWatch() {
    els.lots.innerHTML = "";
    els.lots.hidden = true;
    els.more.hidden = true;
    els.saveSearch.hidden = true;
    els.empty.hidden = false;
    els.empty.querySelector("p").innerHTML = "<strong>Your watchlist is empty.</strong>";
    els.summary.textContent = "Tap the star on any lot to watch it here.";
  }

  function renderNav() {
    const n = notes.watch.size;
    els.watchCount.hidden = n === 0;
    els.watchCount.textContent = n;
    els.navAccount.textContent = state.user ? "Account" : "Sign in";
    els.navAlerts.hidden = !state.user;
  }

  async function refreshWatchBanner() {
    if (!notes.watch.size) { els.watchBanner.hidden = true; return; }
    const qp = new URLSearchParams({ sort: "ending", limit: "100" });
    [...notes.watch].slice(0, 100).forEach((k) => qp.append("keys", k));
    try {
      const data = await (await fetch(`/api/listings?${qp}`)).json();
      const soon = data.items.filter((i) => i.auction_end && new Date(i.auction_end) - new Date() < 2 * 3.6e6);
      if (!soon.length) { els.watchBanner.hidden = true; return; }
      els.watchBanner.innerHTML = `${soon.length === 1 ? `${esc(soon[0].title)} on your watchlist closes` : `${soon.length} lots on your watchlist close`} within 2 hours.
        <button type="button" class="linkish" id="banner-open">${soon.length === 1 ? "Open it" : "Show watchlist"}</button>`;
      els.watchBanner.hidden = false;
      $("#banner-open").addEventListener("click", () => (soon.length === 1 ? openLot(soon[0].source, soon[0].source_lot_id) : setView("watch")));
    } catch { /* banner is a nicety */ }
  }

  function toggleWatch(btn) {
    const [source, ...rest] = btn.dataset.watch.split("/");
    const on = notes.toggleWatch({ source, source_lot_id: rest.join("/") });
    btn.setAttribute("aria-pressed", String(on));
    toast(on ? "Added to your watchlist." : "Removed from your watchlist.");
    renderNav();
    refreshWatchBanner();
    if (!on && state.view === "watch") load();
  }

  // ---------- compare ----------
  function updateTray() {
    const n = state.compare.size;
    els.tray.hidden = n === 0;
    els.trayText.textContent = n === 1 ? "1 lot picked. Pick at least one more to compare." : `${n} lots picked (up to ${COMPARE_MAX}).`;
    $("#compare-open").disabled = n < 2;
    document.body.classList.toggle("has-tray", n > 0);
  }

  function setCompare(key, on, box) {
    if (on && state.compare.size >= COMPARE_MAX) {
      if (box) box.checked = false;
      toast(`You can compare up to ${COMPARE_MAX} lots.`);
      return;
    }
    on ? state.compare.add(key) : state.compare.delete(key);
    updateTray();
  }

  async function openCompare() {
    const qp = new URLSearchParams({ limit: String(COMPARE_MAX) });
    state.compare.forEach((k) => qp.append("keys", k));
    let items;
    try { items = (await (await fetch(`/api/listings?${qp}`)).json()).items; } catch { toast("Couldn't load those lots."); return; }
    if (!items.length) { toast("Those lots have closed."); state.compare.clear(); updateTray(); return; }
    const deals = items.map((it) => computeDeal(it));
    const best = (vals, pick) => { const ok = vals.filter((v) => v != null); return ok.length > 1 ? pick(...ok) : null; };
    const allIns = items.map((i) => i.est_all_in_cost), lows = deals.map((d) => d.profitLow ?? null), risks = items.map((i) => i.risk_score);
    const bAll = best(allIns, Math.min), bProfit = best(lows, Math.max), bRisk = best(risks, Math.min);
    const mark = (v, b) => (b != null && v === b ? ` <span class="best">Best</span>` : "");
    const profitText = (d) => d.parts ? "Parts only" : d.profitLow == null ? (d.profitHigh == null ? "–" : `At most ${rand(d.profitHigh)}`) : `${rand(d.profitLow)} to ${rand(d.profitHigh)}`;
    const checkText = (it) => { const r = notes.get("check", it); return r ? (assess(it, r).bad ? `<span class="chip bad">Flagged</span>` : `<span class="chip ok">Checked</span>`) : "Not checked"; };
    const rows = [
      ["All-in cost", (it, i) => rand(it.est_all_in_cost) + mark(it.est_all_in_cost, bAll)],
      ["Bid now", (it) => it.current_bid != null ? rand(it.current_bid) : `Starts ${rand(it.starting_bid)}`],
      ["Profit after repairs", (it, i) => profitText(deals[i]) + mark(lows[i], bProfit)],
      ["Verdict", (it, i) => `<span class="profit ${V_CLASS[deals[i].verdict] || ""}">${esc(deals[i].verdict || "–")}</span>`],
      ["Bid up to", (it, i) => { const b = maxBidFor(it, deals[i]); return b === undefined ? "–" : b > 0 ? rand(b) : "No safe bid"; }],
      ["Risk", (it) => `${esc(it.risk_label || "–")} (${scoreText(it.risk_score)})` + mark(it.risk_score, bRisk)],
      ["Mileage", (it) => km(it.mileage_km)],
      ["Code", (it) => esc(it.damage_code_raw || CODE_LABEL[it.damage_code])],
      ["Damage", (it) => esc([it.primary_damage, it.secondary_damage].filter(Boolean).join("; ") || "None listed")],
      ["Runs / keys", (it) => `${TRI[it.runs_and_drives]} / ${TRI[it.keys_available]}`],
      ["Where", (it) => esc([it.source_name, it.province].filter(Boolean).join(", "))],
      ["Closes", (it) => esc(closesAt(it.auction_end))],
      ["History check", (it) => checkText(it)],
    ];
    const head = items.map((it) => `<th scope="col"><span class="plate">${esc(it.source_lot_id)}</span><br>${esc(it.title)}<br>
        <span class="sub">${esc(it.variant || "")}</span><br>
        <button type="button" class="linkish" data-open="${esc(lotKey(it))}">Open</button>
        <button type="button" class="linkish" data-uncompare="${esc(lotKey(it))}">Remove</button></th>`).join("");
    const body = rows.map(([label, fn]) => `<tr><th scope="row">${label}</th>${items.map((it, i) => `<td>${fn(it, i)}</td>`).join("")}</tr>`).join("");
    openPanel(`<div class="d-top"><span>Comparing ${items.length} lots</span>
        <button type="button" class="d-close" id="d-close" aria-label="Close comparison">×</button></div>
      <h2 id="d-title">Side by side</h2>
      <p class="muted">"Best" marks the lowest cost, the highest worst-case profit and the lowest risk. Profit uses your own repair figures where you've entered them.</p>
      <div class="compare-scroll"><table class="compare"><thead><tr><th></th>${head}</tr></thead><tbody>${body}</tbody></table></div>`, { wide: true });
    els.drawerBody.querySelectorAll("[data-open]").forEach((b) => b.addEventListener("click", () => {
      const [s, ...l] = b.dataset.open.split("/"); openLot(s, l.join("/"));
    }));
    els.drawerBody.querySelectorAll("[data-uncompare]").forEach((b) => b.addEventListener("click", () => {
      setCompare(b.dataset.uncompare, false);
      els.lots.querySelectorAll(`input[data-compare="${CSS.escape(b.dataset.uncompare)}"]`).forEach((c) => { c.checked = false; });
      state.compare.size >= 2 ? openCompare() : closeLot();
    }));
  }

  // ---------- sign in ----------
  function setAuthMode(mode) {
    state.authMode = mode;
    const signup = mode === "signup";
    $("#auth-title").textContent = signup ? "Create your account" : "Sign in";
    $("#auth-submit").textContent = signup ? "Create account" : "Sign in";
    $("#auth-switch-text").textContent = signup ? "Already have an account?" : "New to Okshun?";
    $("#auth-switch").textContent = signup ? "Sign in" : "Create an account";
    els.authForm.elements.password.autocomplete = signup ? "new-password" : "current-password";
    $("#auth-error").hidden = true;
  }

  function openAuth(reason) {
    state.authReason = reason || null;
    $("#auth-why").textContent = reason || "Keep your watchlist, checks and repair figures on every device, and get alerts.";
    setAuthMode(state.authMode);
    els.auth.showModal();
    els.authForm.elements.email.focus();
  }

  async function submitAuth(ev) {
    if (ev.submitter?.value === "cancel") return;
    ev.preventDefault();
    const f = els.authForm.elements, err = $("#auth-error");
    if (!f.email.value || f.password.value.length < 8) {
      err.textContent = f.email.value ? "Use a password of at least 8 characters." : "Enter your email address.";
      err.hidden = false;
      return;
    }
    try {
      const res = await api(state.authMode === "signup" ? "/api/auth/register" : "/api/auth/login",
        { method: "POST", body: { email: f.email.value, password: f.password.value } });
      state.user = res.user;
      f.password.value = "";
      els.auth.close();
      await afterSignIn();
    } catch (e) {
      err.textContent = e.message;
      err.hidden = false;
    }
  }

  async function afterSignIn() {
    const local = notes.localSnapshot();
    let moved = null;
    if (local.watchlist.length || Object.keys(local.notes).length) {
      try { moved = await api("/api/me/import", { method: "POST", body: local }); notes.clearLocal(); } catch { /* keep the local copy */ }
    }
    notes.loadServer(await api("/api/me/data"));
    renderNav();
    const bits = moved && (moved.watchlist || moved.notes)
      ? ` Moved ${moved.watchlist} watched lot${moved.watchlist === 1 ? "" : "s"} and ${moved.notes} note${moved.notes === 1 ? "" : "s"} from this browser into your account.` : "";
    toast(`Signed in as ${state.user.email}.${bits}`);
    refreshAlerts();
    refreshWatchBanner();
    load();
    if (state.authReason === SAVE_REASON) saveSearch();
  }

  // ---------- account ----------
  async function openAccount() {
    if (!state.user) return openAuth();
    let searches = [];
    try { searches = await api("/api/me/searches"); } catch { /* shown as empty */ }
    const list = searches.length
      ? `<ul class="searches">${searches.map((s) => `<li>
          <div><a href="/?${esc(s.params)}" class="search-name">${esc(s.name)}</a><span class="sub"> ${nf.format(s.matches)} open lot${s.matches === 1 ? "" : "s"} now</span></div>
          <label class="check"><input type="checkbox" data-search-email="${s.id}"${s.email ? " checked" : ""}> Email me new matches</label>
          <button type="button" class="linkish" data-search-delete="${s.id}">Delete</button></li>`).join("")}</ul>`
      : `<p class="muted">No saved searches yet. Set some filters, then choose "Save this search".</p>`;
    openPanel(`<div class="d-top"><span>${esc(state.user.email)}</span>
        <button type="button" class="d-close" id="d-close" aria-label="Close account">×</button></div>
      <h2 id="d-title">Your account</h2>
      <div class="section"><h3>Saved searches</h3>${list}</div>
      <div class="section"><h3>Reminders</h3>
        <p class="muted">Before a watched lot closes, or before its live sale starts, remind me:</p>
        <div class="rem-opts">${REMINDER_CHOICES.map(([m, l]) => `<label class="check"><input type="checkbox" data-acc-rem="${m}"${state.user.reminder_offsets.includes(m) ? " checked" : ""}> ${l} before</label>`).join("")}</div>
        <h4 class="acc-sub">Where to send them</h4>
        <label class="check"><input type="checkbox" id="acc-push"> Phone notifications on this device</label>
        <p class="muted" id="acc-push-status"></p>
        <button type="button" class="linkish" id="acc-push-test" hidden>Send a test notification</button>
        <label class="check"><input type="checkbox" id="acc-reminders"${state.user.email_reminders ? " checked" : ""}> Email</label>
        <p class="muted">Reminders always show under Alerts in the app. Emails go out once the server has email set up.</p></div>
      <div class="section acc-actions">
        <button type="button" class="btn btn-quiet" id="acc-signout">Sign out</button>
        <button type="button" class="linkish danger" id="acc-delete">Delete my account</button>
      </div>`);
    const body = els.drawerBody;
    body.querySelectorAll("[data-search-email]").forEach((c) => c.addEventListener("change", () =>
      api(`/api/me/searches/${c.dataset.searchEmail}`, { method: "PATCH", body: { email: c.checked } }).catch((e) => toast(e.message))));
    body.querySelectorAll("[data-search-delete]").forEach((b) => b.addEventListener("click", async () => {
      try { await api(`/api/me/searches/${b.dataset.searchDelete}`, { method: "DELETE" }); toast("Saved search deleted."); openAccount(); } catch (e) { toast(e.message); }
    }));
    body.querySelectorAll("[data-acc-rem]").forEach((c) => c.addEventListener("change", async () => {
      const offs = [...body.querySelectorAll("[data-acc-rem]:checked")].map((x) => Number(x.dataset.accRem));
      try { await api("/api/me", { method: "PATCH", body: { reminder_offsets: offs } }); state.user.reminder_offsets = offs; toast(`Reminders: ${whenText(offs).toLowerCase()}.`); }
      catch (er) { toast(er.message); }
    }));
    const pushBox = $("#acc-push"), pushStatus = $("#acc-push-status"), pushTest = $("#acc-push-test");
    const showPush = async () => {
      const sub = await currentSubscription().catch(() => null);
      pushBox.checked = Boolean(sub) && state.user.push_reminders !== false;
      pushTest.hidden = !pushBox.checked;
      pushStatus.textContent = !pushSupported()
        ? (isIOS() && !standalone() ? "On iPhone, add Okshun to your Home Screen first, then turn this on from there." : "This browser can't show notifications.")
        : pushBox.checked ? "On. Reminders pop up on this device even when Okshun is closed." : "Off on this device.";
    };
    showPush();
    pushBox.addEventListener("change", async () => {
      pushBox.disabled = true;
      try {
        if (pushBox.checked) { await enablePush(); state.user.push_reminders = true; toast("Phone notifications are on for this device."); }
        else { await disablePush(); toast("Phone notifications are off for this device."); }
      } catch (er) { pushBox.checked = false; toast(er.message); }
      pushBox.disabled = false;
      showPush();
    });
    pushTest.addEventListener("click", async () => {
      try { await api("/api/me/push/test", { method: "POST" }); toast("Test sent. It should pop up in a few seconds."); }
      catch (er) { toast(er.message); }
    });
    $("#acc-reminders").addEventListener("change", async (e) => {
      try { await api("/api/me", { method: "PATCH", body: { email_reminders: e.target.checked } }); state.user.email_reminders = e.target.checked; } catch (er) { toast(er.message); }
    });
    $("#acc-signout").addEventListener("click", async () => {
      await api("/api/auth/logout", { method: "POST" }).catch(() => {});
      state.user = null;
      notes.loadLocal();
      renderNav(); closeLot(); load(); refreshWatchBanner();
      toast("Signed out. Your data stays in your account.");
    });
    $("#acc-delete").addEventListener("click", async (e) => {
      const b = e.currentTarget;
      if (!b.dataset.armed) { b.dataset.armed = "1"; b.textContent = "Tap again to delete everything permanently"; return; }
      try {
        await api("/api/me", { method: "DELETE" });
        state.user = null; notes.loadLocal(); renderNav(); closeLot(); load();
        toast("Your account and everything in it has been deleted.");
      } catch (er) { toast(er.message); }
    });
  }

  // ---------- saved searches ----------
  const SAVE_REASON = "Sign in to save this search and get alerts when new lots match.";
  function describeSearch(p) {
    const parts = [];
    if (p.get("q")) parts.push(`"${p.get("q")}"`);
    for (const k of ["make", "body", "risk", "province"]) { const v = p.getAll(k); if (v.length) parts.push(v.join(" or ")); }
    if (p.get("max_cost")) parts.push(`under ${rand(Number(p.get("max_cost")))}`);
    if (p.get("min_year")) parts.push(`${p.get("min_year")} or newer`);
    if (p.get("runs")) parts.push("runners");
    return (parts.join(", ") || "My search").slice(0, 80);
  }

  async function saveSearch() {
    if (!state.user) return openAuth(SAVE_REASON);
    const p = currentParams();
    p.delete("sort");
    try {
      const s = await api("/api/me/searches", { method: "POST", body: { name: describeSearch(p), params: p.toString(), email: true } });
      toast(`Saved "${s.name}". You'll get an alert when new lots match.`);
    } catch (e) { toast(e.message); }
  }

  // ---------- alerts ----------
  async function refreshAlerts() {
    if (!state.user) return;
    try {
      const a = await api("/api/me/alerts");
      els.alertCount.hidden = !a.unread;
      els.alertCount.textContent = a.unread;
      return a;
    } catch { return null; }
  }

  async function openAlerts() {
    const a = await refreshAlerts();
    if (!a) return;
    const item = (x) => {
      const what = esc(x.headline || "Alert");
      const closed = x.status === "closed" || (x.auction_end && new Date(x.auction_end) < new Date());
      return `<li class="${x.read_at ? "" : "unread"}"><button type="button" class="alert-btn" data-open="${esc(x.source)}/${esc(x.source_lot_id)}"${closed ? " disabled" : ""}>
        <span class="alert-kind">${what}</span><span class="alert-title">${esc(x.title)}</span>
        <span class="sub">${esc(x.source_name)}${x.est_all_in_cost ? `, all-in ${rand(x.est_all_in_cost)}` : ""}${closed ? ", closed" : x.auction_end ? `, closes ${esc(closesAt(x.auction_end))}` : ""}</span>
        <span class="sub">${esc(shortDate(x.created_at))}</span></button></li>`;
    };
    openPanel(`<div class="d-top"><span>${a.unread ? `${a.unread} new` : "All caught up"}</span>
        <button type="button" class="d-close" id="d-close" aria-label="Close alerts">×</button></div>
      <h2 id="d-title">Alerts</h2>
      ${a.items.length ? `<ul class="alerts">${a.items.map(item).join("")}</ul>`
        : `<p class="muted">No alerts yet. Save a search to hear about new lots, or watch lots to be reminded before they close.</p>`}`);
    els.drawerBody.querySelectorAll("[data-open]").forEach((b) => b.addEventListener("click", () => {
      const [s, ...l] = b.dataset.open.split("/"); openLot(s, l.join("/"));
    }));
    if (a.unread) { api("/api/me/alerts/read", { method: "POST" }).then(() => { els.alertCount.hidden = true; }).catch(() => {}); }
  }

  // ---------- filter sheet (phones) ----------
  function setSheet(open) {
    els.filters.classList.toggle("open", open);
    els.toggle.setAttribute("aria-expanded", String(open));
    els.scrim.hidden = !open && els.drawer.hidden;
  }

  function clearAll() {
    els.filterForm.reset();
    for (const s of els.filterForm.querySelectorAll("select")) s.value = "";
    for (const c of els.filterForm.querySelectorAll("input[type=checkbox]")) c.checked = false;
    load();
  }

  // ---------- events ----------
  let debounce;
  els.q.addEventListener("input", () => { clearTimeout(debounce); debounce = setTimeout(load, 250); });
  els.searchForm.addEventListener("submit", (e) => { e.preventDefault(); load(); });
  els.sort.addEventListener("change", () => load());
  els.filterForm.addEventListener("change", () => load());
  els.filterForm.addEventListener("submit", (e) => e.preventDefault());
  $("#clear-filters").addEventListener("click", clearAll);
  $("#empty-clear").addEventListener("click", clearAll);
  els.more.addEventListener("click", () => load({ append: true }));
  els.toggle.addEventListener("click", () => setSheet(!els.filters.classList.contains("open")));
  $("#close-filters").addEventListener("click", () => setSheet(false));
  els.lots.addEventListener("click", (e) => {
    const star = e.target.closest(".star");
    if (star) return toggleWatch(star);
    const b = e.target.closest(".lot-btn");
    if (b) openLot(b.dataset.source, b.dataset.lot, b);
  });
  els.lots.addEventListener("change", (e) => {
    const box = e.target.closest("input[data-compare]");
    if (box) setCompare(box.dataset.compare, box.checked, box);
  });
  els.navWatch.addEventListener("click", () => setView(state.view === "watch" ? "all" : "watch"));
  els.backAll.addEventListener("click", () => setView("all"));
  els.navAccount.addEventListener("click", () => openAccount());
  els.navAlerts.addEventListener("click", () => openAlerts());
  els.saveSearch.addEventListener("click", () => saveSearch());
  $("#compare-open").addEventListener("click", () => openCompare());
  $("#compare-clear").addEventListener("click", () => {
    state.compare.clear();
    els.lots.querySelectorAll("input[data-compare]").forEach((c) => { c.checked = false; });
    updateTray();
  });
  els.authForm.addEventListener("submit", submitAuth);
  $("#auth-switch").addEventListener("click", () => setAuthMode(state.authMode === "signup" ? "login" : "signup"));
  els.scrim.addEventListener("click", () => { closeLot(); setSheet(false); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { closeLot(); setSheet(false); }
  });

  // ---------- start ----------
  (async () => {
    const deep = new URLSearchParams(location.search);   // read before load() rewrites the address
    const p = readParams();
    try {
      state.user = (await api("/api/me")).user;
      if (state.user) notes.loadServer(await api("/api/me/data"));
      else notes.loadLocal();
    } catch { notes.loadLocal(); }
    renderNav();
    if (state.view === "watch") {
      els.navWatch.setAttribute("aria-pressed", "true");
      els.pageTitle.textContent = "Your watchlist";
      els.backAll.hidden = false;
    }
    try {
      const [res, checks, rr] = await Promise.all([fetch("/api/facets"), fetch("/api/vehicle-checks"), fetch("/api/repair-rules")]);
      renderFacets(await res.json(), p);
      if (checks.ok) state.providers = await checks.json();
      if (rr.ok) Object.assign(state.verdictRules, (await rr.json()).verdict || {});
    } catch {
      els.summary.textContent = "Couldn't load auctions. Check that the Okshun server is running, then refresh.";
      return;
    }
    load();
    refreshWatchBanner();
    refreshAlerts();
    if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
    if (deep.get("lot")) {
      const [s, ...l] = deep.get("lot").split("/");
      openLot(s, l.join("/"));
    } else if (deep.get("alerts") && state.user) openAlerts();
    setInterval(() => { refreshAlerts(); refreshWatchBanner(); }, 5 * 60 * 1000);
  })();
})();
