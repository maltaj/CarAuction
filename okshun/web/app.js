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
  };

  const state = { offset: 0, total: 0, facets: null, lastFocus: null };

  // ---------- formatting ----------
  // South African style: spaces between thousands (R 417 720).
  const nf = { format: (v) => Math.round(Number(v)).toString().replace(/\B(?=(\d{3})+(?!\d))/g, "\u00a0") };
  const rand = (v) => (v == null ? "–" : `R ${nf.format(v)}`);
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
    for (const [k] of p) if (k !== "q" && k !== "sort") n++;
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

  // ---------- lots ----------
  function lotRow(it) {
    const c = closesIn(it.auction_end);
    const bid = it.current_bid != null ? `Bid ${rand(it.current_bid)}` : `Starts at ${rand(it.starting_bid)}`;
    const facts = [km(it.mileage_km), it.damage_code_raw || CODE_LABEL[it.damage_code]];
    if (it.runs_and_drives === "no") facts.push(`<span class="warn">Non-runner</span>`);
    if (it.keys_available === "no") facts.push(`<span class="warn">No keys</span>`);
    return `<li class="lot"><button type="button" class="lot-btn" data-source="${esc(it.source)}" data-lot="${esc(it.source_lot_id)}">
      ${disc(it)}
      <div>
        <p class="lot-title">${esc(it.title)} <span class="lot-variant">${esc(it.variant || "")}</span></p>
        <div class="lot-meta"><span class="plate">${esc(it.source_lot_id)}</span><span>${esc(it.source_name)}</span><span>${esc(it.province || "")}</span></div>
        <div class="facts">${facts.map((f) => `<span>${f}</span>`).join("")}</div>
      </div>
      <div class="money">
        <div><div class="allin">${rand(it.est_all_in_cost)}</div><div class="allin-label">all-in estimate</div></div>
        <div class="bidline">${bid}</div>
        <div class="closes${c.soon ? " soon" : ""}">${c.text}</div>
      </div></button></li>`;
  }

  async function load({ append = false } = {}) {
    const p = currentParams();
    if (!append) {
      state.offset = 0;
      history.replaceState(null, "", p.toString() ? `?${p}` : location.pathname);
    }
    const n = activeFilterCount(p);
    els.count.hidden = n === 0;
    els.count.textContent = n;
    const api = new URLSearchParams(p);
    api.set("limit", PAGE);
    api.set("offset", state.offset);
    let data;
    try {
      const res = await fetch(`/api/listings?${api}`);
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
    els.summary.textContent = n || p.get("q")
      ? `${nf.format(data.total)} of ${nf.format(all)} cars match.`
      : `${nf.format(all)} cars from ${houses} auction houses, all still open for bids.`;
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
      ["Sale", it.auction_type === "live" ? "Live webcast" : it.auction_type === "timed" ? "Timed online" : "Not stated"],
      ["Closes", closesAt(it.auction_end)],
    ];
    const action = it.demo
      ? `<span class="btn" aria-disabled="true">Bid on ${esc(it.source_name)}</span><p>Demo listing: there's no real auction to open.</p>`
      : `<a class="btn" href="${esc(it.url)}" target="_blank" rel="noopener">Bid on ${esc(it.source_name)}</a><p>Opens the lot on the auction house's site, where you register and bid.</p>`;
    return `
      <div class="d-top"><span class="plate">${esc(it.source_lot_id)}</span><span>${esc(it.source_name)}</span>
        <button type="button" class="d-close" id="d-close" aria-label="Close lot details">×</button></div>
      <h2 id="d-title">${esc(it.title)}</h2>
      <p class="d-variant">${esc(it.variant || "")}</p>
      <div class="d-risk">${disc(it, true)}<div><h3>Why it scored ${scoreText(it.risk_score)}</h3>${reasonsList(it)}</div></div>
      <div class="section"><h3>What you'd pay</h3>${costTable(it)}</div>
      <div class="section"><h3>Vehicle</h3><dl class="specs">${specs.map(([k, v]) => `<dt>${k}</dt><dd>${esc(v || "–")}</dd>`).join("")}</dl></div>
      ${it.description ? `<div class="section"><h3>Auction house description</h3><p class="desc">${esc(it.description)}</p></div>` : ""}
      <div class="d-action">${action}</div>`;
  }

  async function openLot(source, lot, trigger) {
    let it;
    try {
      const res = await fetch(`/api/listings/${encodeURIComponent(source)}/${encodeURIComponent(lot)}`);
      if (!res.ok) throw new Error(res.status);
      it = await res.json();
    } catch { return; }
    state.lastFocus = trigger || document.activeElement;
    els.drawerBody.innerHTML = drawerHtml(it);
    els.drawer.hidden = false;
    els.scrim.hidden = false;
    els.drawer.classList.add("entering");
    requestAnimationFrame(() => requestAnimationFrame(() => els.drawer.classList.remove("entering")));
    document.body.style.overflow = "hidden";
    $("#d-close").addEventListener("click", closeLot);
    $("#d-close").focus();
  }

  function closeLot() {
    if (els.drawer.hidden) return;
    els.drawer.hidden = true;
    els.scrim.hidden = true;
    document.body.style.overflow = "";
    state.lastFocus?.focus?.();
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
    const b = e.target.closest(".lot-btn");
    if (b) openLot(b.dataset.source, b.dataset.lot, b);
  });
  els.scrim.addEventListener("click", () => { closeLot(); setSheet(false); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { closeLot(); setSheet(false); }
  });

  // ---------- start ----------
  (async () => {
    const p = readParams();
    try {
      const res = await fetch("/api/facets");
      renderFacets(await res.json(), p);
    } catch {
      els.summary.textContent = "Couldn't load auctions. Check that the Okshun server is running, then refresh.";
      return;
    }
    load();
  })();
})();
