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

  const state = { offset: 0, total: 0, facets: null, lastFocus: null, providers: [], verdictRules: { min_profit: 10000, good_margin: 0.15 } };

  // ---------- this browser's storage (history checks, preferred service) ----------
  const store = {
    get(k, fallback = null) { try { const v = localStorage.getItem(k); return v == null ? fallback : JSON.parse(v); } catch { return fallback; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); return true; } catch { return false; } },
    del(k) { try { localStorage.removeItem(k); } catch { /* storage unavailable */ } },
  };
  const checkKey = (it) => `okshun.check.${it.source}/${it.source_lot_id}`;
  const FLAGS = [
    ["finance", "Finance still owed"], ["stolen", "Stolen or police interest"],
    ["writeoff", "Written off or salvage code"], ["accident", "Accident or claim history"],
  ];
  const MILEAGE_TOLERANCE = 1000; // km; report readings this far above the lot's mileage count as a mismatch

  // ---------- formatting ----------
  // South African style: spaces between thousands (R 417 720).
  const nf = { format: (v) => Math.round(Number(v)).toString().replace(/\B(?=(\d{3})+(?!\d))/g, "\u00a0") };
  const rand = (v) => (v == null ? "–" : `R\u00a0${nf.format(v)}`);
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

  // ---------- repair estimate and profit ----------
  const repairKey = (it) => `okshun.repairs.${it.source}/${it.source_lot_id}`;
  const V = { worth: "Worth a look", thin: "Thin margin", not: "Not worth it at this price", inspect: "Inspect first", parts: "Parts only" };
  const V_CLASS = { [V.worth]: "v-worth", [V.thin]: "v-thin", [V.not]: "v-not", [V.inspect]: "v-inspect", [V.parts]: "v-parts" };
  const randK = (v) => `${v < 0 ? "−" : ""}R ${nf.format(Math.round(Math.abs(v) / 1000))}k`;

  // Same logic as okshun/repairs.py, applied to the buyer's own edits.
  function computeDeal(it, edits = store.get(repairKey(it)) || {}) {
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
    else if (d.profitLow == null) text = `Inspect first, at most ${randK(d.profitHigh)} profit`;
    else text = `Profit ${randK(d.profitLow)} to ${randK(d.profitHigh)}`;
    return `<div class="profit ${V_CLASS[d.verdict]}">${text}${d.edited ? " (yours)" : ""}</div>`;
  }

  // ---------- history check results ----------
  function assess(it, rec) {
    const notes = [];
    let bad = false;
    if (rec.km != null && it.mileage_km != null) {
      if (rec.km - it.mileage_km > MILEAGE_TOLERANCE) {
        bad = true;
        notes.push({ bad: true, text: `The report shows ${km(rec.km)}, more than the ${km(it.mileage_km)} on this lot. The odometer may have been rolled back.` });
      } else {
        notes.push({ bad: false, text: `Mileage is consistent: ${km(rec.km)} on the report, ${km(it.mileage_km)} on the lot.` });
      }
    } else if (rec.km != null) {
      notes.push({ bad: false, text: `The report shows ${km(rec.km)}. This lot doesn't list its mileage, so there's nothing to compare.` });
    }
    for (const [key, label] of FLAGS) if ((rec.flags || []).includes(key)) { bad = true; notes.push({ bad: true, text: label }); }
    if (!notes.length) notes.push({ bad: false, text: "Nothing flagged on the report." });
    return { bad, notes };
  }

  function rowChip(it) {
    const rec = store.get(checkKey(it));
    if (!rec) return "";
    return assess(it, rec).bad
      ? `<span class="chip bad">History flagged</span>`
      : `<span class="chip ok">History checked</span>`;
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
        <div class="lot-meta"><span class="plate">${esc(it.source_lot_id)}</span><span>${esc(it.source_name)}</span><span>${esc(it.province || "")}</span><span class="chip-slot">${rowChip(it)}</span></div>
        <div class="facts">${facts.map((f) => `<span>${f}</span>`).join("")}</div>
      </div>
      <div class="money">
        <div><div class="allin">${rand(it.est_all_in_cost)}</div><div class="allin-label">all-in estimate</div></div>
        <div class="bidline">${bid}</div>
        <span class="profit-slot">${profitLine(it)}</span>
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
      ${dealSection(it)}
      <div class="section"><h3>Vehicle</h3><dl class="specs">${specs.map(([k, v]) => `<dt>${k}</dt><dd>${esc(v || "–")}</dd>`).join("")}</dl></div>
      ${it.description ? `<div class="section"><h3>Auction house description</h3><p class="desc">${esc(it.description)}</p></div>` : ""}
      ${checkPanel(it)}
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
    const edits = store.get(repairKey(it)) || {};
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
      <p class="muted">Resale value: ${rand(it.resale_value)}${resaleNote}. Estimates come from the published damage details and placeholder price ranges, not a mechanic's quote. Your figures are saved in this browser.
        <button type="button" class="linkish" id="deal-reset">Reset to estimate</button></p>
    </div>`;
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
    if (!d.inspect && d.resale && bidNow != null) {
      // Invert estimate_all_in: all_in = (bid × (1 + commission) + fees) × VAT factor
      const target = state.verdictRules.min_profit;
      const vat = it.vat_on_hammer === false ? 1.15 : 1;
      const comm = (it.buyers_commission_pct || 0) / 100;
      const room = d.resale - d.repHigh - d.roadHigh - target;
      const bid = Math.floor(((room / vat) - (it.fixed_fees || 0)) / (1 + comm) / 500) * 500;
      maxBid = bid > 0
        ? `<p class="max-bid">Bid up to <strong>${rand(bid)}</strong> to keep at least ${rand(target)} profit, even with the highest repair estimate.</p>`
        : `<p class="max-bid">At these repair costs, no bid leaves ${rand(target)} profit.</p>`;
    }
    const basis = bidNow != null ? `<p class="muted">Profit is worked out at the ${bidLabel} of ${rand(bidNow)}. Every extra rand you bid comes off it, plus commission.</p>` : "";
    box.innerHTML = `<div class="verdict-band ${V_CLASS[d.verdict] || ""}">
        <p class="vb-head">${esc(d.verdict || "Not enough information")}</p><p class="vb-why">${why}</p></div>
      <table class="costs deal-totals"><tbody>
        <tr><td>Resale value</td><td>${rand(d.resale)}</td></tr>
        <tr><td>All-in cost to buy</td><td>− ${rand(d.allIn)}</td></tr>
        <tr><td>Repairs${d.inspect ? " (priced lines only)" : ""}</td><td>− ${d.repLow === d.repHigh ? rand(d.repLow) : `${rand(d.repLow)} – ${rand(d.repHigh)}`}</td></tr>
        <tr><td>Getting it on the road</td><td>− ${d.roadLow === d.roadHigh ? rand(d.roadLow) : `${rand(d.roadLow)} – ${rand(d.roadHigh)}`}</td></tr>
        <tr class="total"><td>Profit</td><td>${profit}</td></tr>
      </tbody></table>${maxBid}${basis}`;
  }

  function refreshRowProfit(it) {
    const slot = els.lots.querySelector(`.lot-btn[data-source="${CSS.escape(it.source)}"][data-lot="${CSS.escape(it.source_lot_id)}"] .profit-slot`);
    if (slot) slot.innerHTML = profitLine(it);
  }

  function wireDeal(it) {
    const deal = $("#deal");
    if (!deal) return;
    const key = repairKey(it);
    const save = (mutate) => {
      const e = store.get(key) || {};
      mutate(e);
      if (!store.set(key, e)) $("#deal-summary").insertAdjacentHTML("beforeend", `<p class="muted">This browser blocks saving; your edits last until you close the lot.</p>`);
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
    $("#deal-reset").addEventListener("click", () => { store.del(key); renderDealSummary(it); refreshRowProfit(it); rerender(); });
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
    const rec = store.get(checkKey(it)) || {};
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
    const rec = store.get(checkKey(it));
    if (!rec) { box.innerHTML = ""; return; }
    const { bad, notes } = assess(it, rec);
    const prov = state.providers.find((p) => p.id === rec.provider);
    const when = new Date(rec.savedAt).toLocaleDateString("en-ZA", { day: "numeric", month: "short", year: "numeric" });
    box.innerHTML = `<div class="verdict ${bad ? "bad" : "ok"}">
      <p class="verdict-head">${bad ? "Check before you bid" : "No problems recorded"}</p>
      <ul>${notes.map((n) => `<li class="${n.bad ? "bad" : ""}">${esc(n.text)}</li>`).join("")}</ul>
      <p class="muted">Saved ${when}${prov ? ` from ${esc(prov.name)}` : ""}, in this browser only.
        <button type="button" class="linkish" id="chk-remove">Remove my check</button></p></div>`;
    $("#chk-remove").addEventListener("click", () => {
      store.del(checkKey(it));
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
      if (!store.set(checkKey(it), rec)) {
        $("#chk-result").innerHTML = `<p class="verdict bad">Couldn't save: this browser blocks local storage. Your entry is still shown until you close the lot.</p>`;
        return;
      }
      showCheckResult(it);
      refreshRowChip(it);
    });
    showCheckResult(it);
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
    wireCheck(it);
    wireDeal(it);
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
      const [res, checks, rr] = await Promise.all([fetch("/api/facets"), fetch("/api/vehicle-checks"), fetch("/api/repair-rules")]);
      renderFacets(await res.json(), p);
      if (checks.ok) state.providers = await checks.json();
      if (rr.ok) Object.assign(state.verdictRules, (await rr.json()).verdict || {});
    } catch {
      els.summary.textContent = "Couldn't load auctions. Check that the Okshun server is running, then refresh.";
      return;
    }
    load();
  })();
})();
