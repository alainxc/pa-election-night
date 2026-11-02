/* app.js - PA election-night tracker front end (vanilla JS + D3 v7).
   Loads priors/baseline (static), returns.json + estimates.json + timeline.json
   (published by the poller), recomputes every seat with needle.js, and renders.
   Refreshes every 60 s. Never invents a number: unknown -> "no returns yet". */
(function () {
  "use strict";
  const REFRESH_MS = 60000;
  const STALE_MS = 10 * 60 * 1000;
  const page = document.body.dataset.page || "index";
  const params = new URLSearchParams(location.search);
  const seatParam = (params.get("seat") || "").toUpperCase();
  const chamber = page === "seat" ? (seatParam.startsWith("H") ? "house" : "senate") : (document.body.dataset.chamber || "senate");
  const suffix = chamber === "house" ? "_house" : "";
  let TARGET = 2026;  // which priors/baseline year; the poller's estimates.json can switch it (2024 replay)
  const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const fmt = {
    pct: (x, d = 0) => (x === null || x === undefined || Number.isNaN(x)) ? "—" : (100 * x).toFixed(d) + "%",
    pts: (x, d = 1) => (x === null || x === undefined || Number.isNaN(x)) ? "—" : (x > 0 ? "D+" : x < 0 ? "R+" : "") + Math.abs(x).toFixed(d),
    int: x => (x === null || x === undefined) ? "—" : Math.round(x).toLocaleString(),
    num: (x, d = 1) => (x === null || x === undefined) ? "—" : (+x).toFixed(d),
  };
  const NOW = () => Date.now() / 1000;
  let STATIC = null;   // {priors, baseline, mail}
  let LIVE = null;     // {returns, estimates, timeline}
  let sortKey = "p", sortAsc = true;

  async function getJSON(url, bust) {
    const r = await fetch(url + (bust ? ((url.includes("?") ? "&" : "?") + "t=" + Date.now()) : ""), { cache: "no-store" });
    if (!r.ok) throw new Error(url + " " + r.status);
    return r.json();
  }
  async function loadStatic() {
    try { const e = await getJSON(`data/estimates${suffix}.json`, true); if (e && e.target) TARGET = e.target; } catch (err) { /* keep default */ }
    const [priors, baseline] = await Promise.all([getJSON(`data/priors_${TARGET}.json`), getJSON(`data/baseline_${TARGET}.json`)]);
    let mail = null;
    try { mail = await getJSON(`data/mail_${TARGET}.json`, true); } catch (e) { mail = null; }
    STATIC = { priors, baseline, mail };
  }
  async function loadLive() {
    const out = {};
    const [r, e, t] = await Promise.allSettled([getJSON(`data/returns${suffix}.json`, true), getJSON(`data/estimates${suffix}.json`, true), getJSON(`data/timeline${suffix}.json`, true)]);
    out.returns = r.status === "fulfilled" ? r.value : null;
    out.estimates = e.status === "fulfilled" ? e.value : null;
    out.timeline = t.status === "fulfilled" ? t.value : null;
    LIVE = out;
  }

  // ---- compute -------------------------------------------------------------
  function seatKeyOf(row) { return (chamber === "house" ? "H" : "S") + row.seat; }
  function computeAll() {
    const cfg = STATIC.priors.chambers[chamber];
    const rowsBySeat = {};
    if (LIVE.returns) for (const r of LIVE.returns.rows) (rowsBySeat[seatKeyOf(r)] = rowsBySeat[seatKeyOf(r)] || []).push(r);
    const extra = STATIC.mail ? { mail_returned: STATIC.mail.returned || {}, mail_requested: STATIC.mail.requested || {}, county_mail_ref: STATIC.baseline.county_mail_ref || {} } : {};
    const prevDisplay = (LIVE.estimates && LIVE.estimates.seats) || {};
    const seats = {}; const probs = [];
    for (const key of cfg.seats_up) {
      const p = STATIC.priors.seats[key], b = STATIC.baseline.seats[key];
      if (!b) continue;
      const rows = rowsBySeat[key] || [];
      const est = Needle.estimateSeat({ mu: p.mu, sd: p.sd, fixed_prob: p.fixed_prob, dem_side: p.dem_side, ref_margin: p.ref_margin }, b, rows, extra);
      // damping: continue from the poller's displayed state when it exists
      const prev = prevDisplay[key] ? { mu: prevDisplay[key].display.mu, sd: prevDisplay[key].display.sd, p_dem: prevDisplay[key].display.p_dem, target_mu: prevDisplay[key].target.mu, swing_until: prevDisplay[key].display.swing_until } : null;
      const disp = est.fixed ? { mu: est.mu, sd: est.sd, p_dem: est.p_dem, target_mu: est.mu, swing_until: 0 } : Needle.damp(prev, est, NOW());
      const pd = p.dem_side === "DEM" ? disp.p_dem : 0.0;
      const pr = 1 - disp.p_dem;
      probs.push([pd, pr]);
      seats[key] = { key, prior: p, baseline: b, rows, est, disp, override: rows.some(r => r.override) };
    }
    const odds = Needle.chamberOdds(probs, cfg.holdover_dem, cfg.holdover_rep, cfg.total_seats, cfg.majority);
    return { seats, odds, cfg };
  }

  // ---- status bar ----------------------------------------------------------
  function renderStatus() {
    const dot = document.getElementById("dot"), upd = document.getElementById("updated");
    const prec = document.getElementById("precincts"), srcT = document.getElementById("source-time");
    const banner = document.getElementById("banner");
    const meta = LIVE.returns && LIVE.returns.meta;
    if (!meta) { dot.className = "dot dead"; upd.textContent = "no data file yet"; return; }
    const t = new Date(meta.fetched_at_utc);
    const age = Date.now() - t.getTime();
    const stale = age > STALE_MS;
    dot.className = "dot" + (stale ? " stale" : "");
    upd.textContent = `Last updated ${t.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}` + (stale ? ` — ${Math.round(age / 60000)} min old, data may be stale` : "");
    const sr = meta.statewide_reporting;
    if (prec) prec.textContent = sr && sr.precincts_total ? `Statewide: ${fmt.int(sr.precincts_reporting)} of ${fmt.int(sr.precincts_total)} precincts reporting in person (${fmt.num(sr.pct_precincts_reporting, 1)}%)` : "";
    if (srcT) srcT.textContent = meta.source_last_updated_local ? `State site last updated ${meta.source_last_updated_local.replace(/\.\d+$/, "")} ET` : "";
    const msgs = [];
    if (meta.election && meta.election.is_testing) msgs.push("The state site is in TESTING MODE: counts shown are test data until polls close at 8 pm on November 3.");
    if (meta.overrides && meta.overrides.length) msgs.push(`Manual overrides in effect for: ${meta.overrides.join(", ")} (typed from county sources by the operator; replaces the state feed for those seats).`);
    if (meta.replay_source) msgs.push("REHEARSAL: this is a replay of 2024 returns, not live 2026 data.");
    if (LIVE.estimates && LIVE.estimates.rss_check && LIVE.estimates.rss_check.ok === false) msgs.push("The state's RSS feed and data interface disagree on statewide totals right now; figures may be mid-update.");
    banner.textContent = msgs.join(" ");
    banner.classList.toggle("hidden", msgs.length === 0);
  }

  // ---- gauges --------------------------------------------------------------
  function gauge(el, opts) {
    // semicircular gauge. domain [lo,hi]; value; band [b0,b1]; majority marker; colors by side
    const W = opts.width || 420, H = W * 0.6, R = W * 0.42, cx = W / 2, cy = H * 0.88;
    const sel = d3.select(el); sel.selectAll("*").remove();
    const svg = sel.append("svg").attr("viewBox", `0 0 ${W} ${H}`).attr("width", "100%").attr("role", "img").attr("aria-label", opts.aria || "gauge");
    const x = d3.scaleLinear().domain([opts.lo, opts.hi]).range([-Math.PI / 2, Math.PI / 2]).clamp(true);
    const arc = d3.arc().innerRadius(R * 0.72).outerRadius(R);
    const g = svg.append("g").attr("transform", `translate(${cx},${cy})`);
    // background halves: rep side (left, lower values) / dem side (right)
    const mid = opts.mid;
    g.append("path").attr("d", arc({ startAngle: x(opts.lo), endAngle: x(mid) })).attr("fill", css("--rep-l")).attr("opacity", 0.55);
    g.append("path").attr("d", arc({ startAngle: x(mid), endAngle: x(opts.hi) })).attr("fill", css("--dem-l")).attr("opacity", 0.55);
    if (opts.band && opts.band[0] !== null && opts.band[1] !== null) {
      // the uncertainty band is always purple (the "tie" color), never a party color
      g.append("path").attr("d", arc({ startAngle: x(opts.band[0]), endAngle: x(opts.band[1]) })).attr("fill", css("--tie")).attr("opacity", 0.85);
    }
    // ticks
    const ticks = opts.ticks || x.ticks(6);
    for (const tv of ticks) {
      const a = x(tv) - Math.PI / 2;
      const r0 = R * 1.02, r1 = R * 1.08;
      g.append("line").attr("x1", Math.cos(a) * r0).attr("y1", Math.sin(a) * r0).attr("x2", Math.cos(a) * r1).attr("y2", Math.sin(a) * r1).attr("stroke", css("--muted")).attr("stroke-width", 1);
      g.append("text").attr("x", Math.cos(a) * R * 1.19).attr("y", Math.sin(a) * R * 1.19 + 4).attr("text-anchor", "middle").attr("font-size", 11).attr("fill", css("--muted")).text(opts.tickFmt ? opts.tickFmt(tv) : tv);
    }
    // majority marker
    if (opts.marker !== undefined) {
      const a = x(opts.marker) - Math.PI / 2;
      g.append("line").attr("x1", Math.cos(a) * R * 0.70).attr("y1", Math.sin(a) * R * 0.70).attr("x2", Math.cos(a) * R * 1.03).attr("y2", Math.sin(a) * R * 1.03).attr("stroke", css("--fg")).attr("stroke-width", 2).attr("stroke-dasharray", "4 3");
      g.append("text").attr("x", Math.cos(a) * R * 0.62).attr("y", Math.sin(a) * R * 0.62 + 4).attr("text-anchor", "middle").attr("font-size", 11).attr("fill", css("--fg")).text(opts.markerLabel || "");
    }
    // needle
    if (opts.value !== null && opts.value !== undefined) {
      const a = x(opts.value) - Math.PI / 2;
      g.append("line").attr("x1", 0).attr("y1", 0).attr("x2", Math.cos(a) * R * 0.98).attr("y2", Math.sin(a) * R * 0.98).attr("stroke", css("--fg")).attr("stroke-width", 3).attr("stroke-linecap", "round");
      g.append("circle").attr("r", 6).attr("fill", css("--fg"));
    }
    g.append("text").attr("x", 0).attr("y", -R * 0.30).attr("text-anchor", "middle").attr("font-size", opts.bigSize || 34).attr("font-weight", 700).attr("fill", css("--fg")).text(opts.label || "");
    g.append("text").attr("x", 0).attr("y", -R * 0.30 + 22).attr("text-anchor", "middle").attr("font-size", 13).attr("fill", css("--muted")).text(opts.sublabel || "");
    g.append("text").attr("x", -R * 0.86).attr("y", 22).attr("text-anchor", "middle").attr("font-size", 12).attr("fill", css("--rep")).text(opts.leftLabel || "");
    g.append("text").attr("x", R * 0.86).attr("y", 22).attr("text-anchor", "middle").attr("font-size", 12).attr("fill", css("--dem")).text(opts.rightLabel || "");
  }

  function miniNeedle(p, fixed) {
    const W = 64, H = 36, R = 26, cx = 32, cy = 32;
    const svg = d3.create("svg").attr("viewBox", `0 0 ${W} ${H}`).attr("width", W).attr("height", H);
    const x = d3.scaleLinear().domain([0, 1]).range([-Math.PI / 2, Math.PI / 2]).clamp(true);
    const arc = d3.arc().innerRadius(R * 0.62).outerRadius(R);
    const g = svg.append("g").attr("transform", `translate(${cx},${cy})`);
    g.append("path").attr("d", arc({ startAngle: x(0), endAngle: x(0.5) })).attr("fill", css("--rep-l"));
    g.append("path").attr("d", arc({ startAngle: x(0.5), endAngle: x(1) })).attr("fill", css("--dem-l"));
    if (p !== null && p !== undefined) {
      const a = x(p) - Math.PI / 2;
      g.append("line").attr("x1", 0).attr("y1", 0).attr("x2", Math.cos(a) * R * 0.95).attr("y2", Math.sin(a) * R * 0.95).attr("stroke", fixed ? css("--muted") : css("--fg")).attr("stroke-width", 2.2).attr("stroke-linecap", "round");
      g.append("circle").attr("r", 3).attr("fill", fixed ? css("--muted") : css("--fg"));
    }
    return svg.node();
  }

  // ---- line charts ---------------------------------------------------------
  function timelineChart(el, points, series, opts) {
    const sel = d3.select(el); sel.selectAll("*").remove();
    const W = 520, H = 230, m = { t: 10, r: opts.rightAxis ? 44 : 14, b: 28, l: 40 };
    const svg = sel.append("svg").attr("viewBox", `0 0 ${W} ${H}`).attr("width", "100%");
    if (!points || points.length < 2) {
      svg.append("text").attr("x", W / 2).attr("y", H / 2).attr("text-anchor", "middle").attr("fill", css("--muted")).attr("font-size", 13).text(points && points.length === 1 ? "one data point so far" : "no returns yet");
      return;
    }
    const x = d3.scaleTime().domain(d3.extent(points, d => new Date(d.t))).range([m.l, W - m.r]);
    const y = d3.scaleLinear().domain(opts.yDomain).range([H - m.b, m.t]);
    svg.append("g").attr("transform", `translate(0,${H - m.b})`).call(d3.axisBottom(x).ticks(5).tickFormat(d3.timeFormat("%-I:%M %p"))).attr("font-size", 10).attr("color", css("--muted"));
    svg.append("g").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(5).tickFormat(opts.yFmt)).attr("font-size", 10).attr("color", css("--muted"));
    if (opts.zero !== undefined) svg.append("line").attr("x1", m.l).attr("x2", W - m.r).attr("y1", y(opts.zero)).attr("y2", y(opts.zero)).attr("stroke", css("--line"));
    if (opts.band) {
      const area = d3.area().x(d => x(new Date(d.t))).y0(d => y(opts.band[0](d))).y1(d => y(opts.band[1](d))).defined(d => opts.band[0](d) !== null);
      svg.append("path").datum(points).attr("d", area).attr("fill", css("--dem-l")).attr("opacity", 0.45);
    }
    for (const s of series) {
      const line = d3.line().x(d => x(new Date(d.t))).y(d => y(s.v(d))).defined(d => s.v(d) !== null && s.v(d) !== undefined);
      svg.append("path").datum(points).attr("d", line).attr("fill", "none").attr("stroke", s.color).attr("stroke-width", s.width || 2).attr("stroke-dasharray", s.dash || null);
    }
    if (opts.rightAxis) {
      const y2 = d3.scaleLinear().domain(opts.rightAxis.domain).range([H - m.b, m.t]);
      svg.append("g").attr("transform", `translate(${W - m.r},0)`).call(d3.axisRight(y2).ticks(4).tickFormat(opts.rightAxis.fmt)).attr("font-size", 10).attr("color", css("--muted"));
      const line = d3.line().x(d => x(new Date(d.t))).y(d => y2(opts.rightAxis.v(d))).defined(d => opts.rightAxis.v(d) !== null);
      svg.append("path").datum(points).attr("d", line).attr("fill", "none").attr("stroke", opts.rightAxis.color || css("--muted")).attr("stroke-width", 1.5).attr("stroke-dasharray", "3 3");
    }
    // legend
    let lx = m.l + 4;
    for (const s of series.concat(opts.rightAxis ? [{ label: opts.rightAxis.label, color: opts.rightAxis.color || css("--muted") }] : [])) {
      if (!s.label) continue;
      svg.append("rect").attr("x", lx).attr("y", m.t).attr("width", 10).attr("height", 3).attr("fill", s.color);
      svg.append("text").attr("x", lx + 14).attr("y", m.t + 5).attr("font-size", 10).attr("fill", css("--muted")).text(s.label);
      lx += 14 + s.label.length * 5.6 + 12;
    }
  }

  // ---- index page -----------------------------------------------------------
  function statusPill(s, est, disp, prior) {
    const span = document.createElement("span");
    let cls = "none", txt = s;
    if (s.startsWith("fixed")) { txt = "fixed (" + (prior.fixed_prob >= 0.5 ? "D" : "R") + ")"; cls = prior.fixed_prob >= 0.5 ? "D" : "R"; }
    else if (/ D/.test(s)) cls = "D"; else if (/ R/.test(s)) cls = "R";
    span.className = "pill " + cls; span.textContent = txt;
    const wrap = document.createElement("span"); wrap.appendChild(span);
    if (disp.swing_until && disp.swing_until > NOW()) { const sw = document.createElement("span"); sw.className = "pill swing"; sw.style.marginLeft = "4px"; sw.textContent = "early returns, expect swings"; wrap.appendChild(sw); }
    return wrap;
  }
  function shortName(n) {
    // on phones show the surname only (drop suffixes and a parenthetical party note)
    const base = n.replace(/\s*\(.*?\)\s*/g, " ").trim().split(/\s+/).filter(w => !/^(jr\.?|sr\.?|ii|iii|iv)$/i.test(w));
    return base.length ? base[base.length - 1] : n;
  }
  function candLabel(p, short) {
    const parts = [];
    const f = short ? shortName : (x => x);
    if (p.dem_cand) parts.push(`<span class="dem">${f(p.dem_cand)}</span>`);
    if (p.rep_cand) parts.push(`<span class="rep">${f(p.rep_cand)}</span>`);
    if (!p.dem_cand && !p.rep_cand) parts.push("—");
    return parts.join(" vs ");
  }
  function renderIndex(res) {
    const { seats, odds, cfg } = res;
    const anyReport = Object.values(seats).some(s => s.est.any_report);
    const fc = cfg;
    gauge(document.getElementById("chamber-needle"), {
      lo: cfg.holdover_dem, hi: cfg.holdover_dem + cfg.seats_up.length, mid: cfg.majority - 0.5,
      value: odds.dem_seats_mean, band: [odds.dem_seats_p05, odds.dem_seats_p95], marker: cfg.majority - 0.5, markerLabel: `${cfg.majority} = majority`,
      label: odds.dem_seats_mean.toFixed(1), sublabel: `est. Democratic seats (90%: ${odds.dem_seats_p05}–${odds.dem_seats_p95})`,
      leftLabel: cfg.holdover_rep ? `Rep ${cfg.holdover_rep} hold over` : "Rep", rightLabel: cfg.holdover_dem ? `Dem ${cfg.holdover_dem} hold over` : "Dem",
      ticks: d3.range(cfg.holdover_dem, cfg.holdover_dem + cfg.seats_up.length + 1, cfg.seats_up.length > 50 ? 25 : 5),
      aria: "Chamber needle: estimated Democratic seats",
    });
    const o = document.getElementById("chamber-odds");
    o.innerHTML = `<div class="dem"><div class="v">${fmt.pct(odds.p_dem_majority)}</div><div class="l">Democratic majority</div></div>
      <div class="tie"><div class="v">${fmt.pct(odds.p_no_majority)}</div><div class="l">No majority</div></div>
      <div class="rep"><div class="v">${fmt.pct(odds.p_rep_majority)}</div><div class="l">Republican majority</div></div>`;
    const note = document.getElementById("chamber-note");
    note.textContent = (anyReport ? "Tracker estimate from returns blended with the pre-election forecast. " : "No returns yet: these are the pre-election priors combined seat by seat. ") +
      (fc.forecast_p_dem_control !== undefined ? `For reference, the forecast's simulations (which include shared statewide swings) gave Democrats ${fmt.pct(fc.forecast_p_dem_control)} and Republicans ${fmt.pct(fc.forecast_p_rep_control)} for control.` : "");
    // timeline
    const pts = (LIVE.timeline && LIVE.timeline.points) || [];
    timelineChart(document.getElementById("chamber-timeline"), pts, [
      { v: d => d.chamber.p_dem_majority, color: css("--dem"), label: "Dem majority" },
      { v: d => d.chamber.p_rep_majority, color: css("--rep"), label: "Rep majority" },
      { v: d => d.chamber.p_no_majority, color: css("--tie"), label: "No majority" },
    ], { yDomain: [0, 1], yFmt: d => (100 * d) + "%", rightAxis: { domain: [cfg.holdover_dem, cfg.holdover_dem + cfg.seats_up.length], v: d => d.chamber.dem_seats_mean, fmt: d => d, label: "Dem seats" } });
    renderTable(res);
  }
  function renderTable(res) {
    const { seats } = res;
    const rows = Object.values(seats).map(s => {
      const p = s.prior, est = s.est, d = s.disp;
      return {
        key: s.key, seatNo: p.district, cands: candLabel(p, window.innerWidth < 700), p: d.p_dem, fixed: est.fixed,
        mu: est.any_report || est.fixed ? d.mu : p.mu, lo: est.lo, hi: est.hi, frac: est.frac, status: est.status, prior: p.mu, rating: p.forecast_rating,
        holder: p.holder_party, est, disp: d, priorObj: p, anyReport: est.any_report, demSide: p.dem_side, override: s.override,
      };
    });
    const keyFn = { seat: r => r.seatNo, cands: r => r.cands, p: r => r.p, mu: r => r.mu, frac: r => r.frac, status: r => r.status, prior: r => r.prior, holder: r => r.holder };
    rows.sort((a, b) => { const ka = keyFn[sortKey](a), kb = keyFn[sortKey](b); return (ka > kb ? 1 : ka < kb ? -1 : 0) * (sortAsc ? 1 : -1); });
    // competitive first by default: sort by |p-0.5|
    if (sortKey === "p") rows.sort((a, b) => (Math.abs(a.p - 0.5) - Math.abs(b.p - 0.5)) * (sortAsc ? 1 : -1));
    const tb = document.querySelector("#seats tbody"); tb.innerHTML = "";
    for (const r of rows) {
      const tr = document.createElement("tr");
      const seatTd = document.createElement("td"); seatTd.innerHTML = `<a href="seat.html?seat=${r.key}"><b>${r.key.replace(/^S/, "SD-").replace(/^H/, "HD-")}</b></a>${r.override ? ' <span class="pill swing" title="manual override">override</span>' : ""}`; tr.appendChild(seatTd);
      const cTd = document.createElement("td"); cTd.innerHTML = r.cands + (r.demSide !== "DEM" ? ' <span class="small">(Dem side = independent)</span>' : ""); tr.appendChild(cTd);
      const nTd = document.createElement("td"); nTd.className = "mini"; const wrap = document.createElement("div"); wrap.style.display = "flex"; wrap.style.alignItems = "center"; wrap.style.gap = "6px"; wrap.appendChild(miniNeedle(r.p, r.fixed)); const pl = document.createElement("span"); pl.className = "small"; pl.textContent = r.fixed ? fmt.pct(r.p) : (r.anyReport ? fmt.pct(r.p) : fmt.pct(r.p) + " prior"); wrap.appendChild(pl); nTd.appendChild(wrap); tr.appendChild(nTd);
      const mTd = document.createElement("td"); mTd.className = "num";
      if (r.fixed) mTd.innerHTML = `<span class="muted">${fmt.pts(r.prior)} (fixed)</span>`;
      else if (!r.anyReport) mTd.innerHTML = `<span class="muted">no returns yet</span>`;
      else mTd.innerHTML = `<b class="${r.mu >= 0 ? "dem" : "rep"}">${fmt.pts(r.mu)}</b> <span class="small">(${fmt.pts(r.lo)} to ${fmt.pts(r.hi)})</span>`;
      tr.appendChild(mTd);
      const fTd = document.createElement("td"); fTd.className = "num"; fTd.textContent = r.anyReport ? fmt.pct(r.frac) : "0%"; tr.appendChild(fTd);
      const sTd = document.createElement("td"); sTd.appendChild(statusPill(r.status, r.est, r.disp, r.priorObj)); tr.appendChild(sTd);
      const pTd = document.createElement("td"); pTd.className = "num hide-sm"; pTd.innerHTML = `${fmt.pts(r.prior)} <span class="small">${r.rating || ""}</span>`; tr.appendChild(pTd);
      const hTd = document.createElement("td"); hTd.className = "hide-sm"; hTd.innerHTML = r.holder ? `<span class="pill ${r.holder}">${r.holder}</span>` : "—"; tr.appendChild(hTd);
      tb.appendChild(tr);
    }
    document.querySelectorAll("#seats th").forEach(th => { th.classList.toggle("sorted", th.dataset.k === sortKey); th.classList.toggle("asc", sortAsc); });
  }
  function bindSort() {
    document.querySelectorAll("#seats th").forEach(th => th.addEventListener("click", () => {
      const k = th.dataset.k; if (sortKey === k) sortAsc = !sortAsc; else { sortKey = k; sortAsc = true; }
      render();
    }));
  }

  // ---- seat page -----------------------------------------------------------
  function renderSeat(res) {
    const s = res.seats[seatParam];
    if (!s) { document.getElementById("title").textContent = "Unknown seat"; return; }
    const p = s.prior, est = s.est, d = s.disp, b = s.baseline;
    const label = seatParam.replace(/^S/, "Senate District ").replace(/^H/, "House District ");
    document.title = `${label}: PA Election Night`;
    document.getElementById("title").textContent = label;
    document.getElementById("subtitle").innerHTML = `${candLabel(p)}${p.other_cands ? ` <span class="muted">· also on ballot: ${p.other_cands}</span>` : ""}. Held by <span class="pill ${p.holder_party || "none"}">${p.holder_party || "—"}</span>. Pre-election forecast: ${fmt.pts(p.mu)} (${p.forecast_rating}). Counties: ${Object.keys(b.counties).map(c => c[0] + c.slice(1).toLowerCase()).join(", ")}.`;
    const shown = est.any_report || est.fixed ? d.mu : p.mu;
    const lim = Math.max(30, Math.ceil(Math.max(Math.abs(shown), Math.abs(est.lo), Math.abs(est.hi), Math.abs(p.mu)) / 10) * 10);
    gauge(document.getElementById("seat-needle"), {
      lo: -lim, hi: lim, mid: 0, value: est.fixed ? p.mu : (est.any_report ? d.mu : p.mu), band: est.any_report ? [est.lo, est.hi] : [p.mu - 1.645 * p.sd, p.mu + 1.645 * p.sd], marker: 0, markerLabel: "",
      label: est.fixed ? "fixed" : (est.any_report ? fmt.pts(d.mu) : "no returns yet"), sublabel: est.fixed ? `no major-party opponent; held at ${fmt.pct(p.fixed_prob)}` : (est.any_report ? `est. final margin (90%: ${fmt.pts(est.lo)} to ${fmt.pts(est.hi)})` : `prior ${fmt.pts(p.mu)} ± ${fmt.num(1.645 * p.sd, 0)}`),
      leftLabel: p.rep_cand || "Rep", rightLabel: p.dem_cand || "Dem side", tickFmt: v => fmt.pts(v, 0), ticks: d3.range(-lim, lim + 1, lim / 3), bigSize: 30, aria: "Seat needle: estimated margin",
    });
    const pD = p.dem_side === "DEM" ? d.p_dem : 0, pI = p.dem_side === "DEM" ? 0 : d.p_dem, pR = 1 - d.p_dem;
    document.getElementById("seat-odds").innerHTML = `<div class="dem"><div class="v">${fmt.pct(p.dem_side === "DEM" ? pD : pI)}</div><div class="l">${p.dem_side === "DEM" ? "Democrat" : "Independent"} wins</div></div>
      <div class="tie"><div class="v">${est.any_report ? fmt.pct(est.frac) : "0%"}</div><div class="l">of expected vote in</div></div>
      <div class="rep"><div class="v">${fmt.pct(pR)}</div><div class="l">Republican wins</div></div>`;
    const sw = d.swing_until && d.swing_until > NOW() ? ' <span class="pill swing">early returns, expect swings</span>' : "";
    document.getElementById("seat-note").innerHTML = `Status: <b>${est.status}</b>${sw}. ${est.any_report ? `Returns-only projection ${fmt.pts(est.m_ret)} (sd ${fmt.num(est.sd_ret)}), weight on returns ${fmt.pct(est.w_returns)}; counted votes so far ${fmt.pts(est.m_counted)}; pooled swing vs 2024 benchmark ${fmt.pts(est.pooled_swing)} (forecast implied ${fmt.pts(est.prior_swing)}).` : "Nothing counted yet; the gauge shows the pre-election prior."}${s.override ? " <b>Manual override in effect for this seat.</b>" : ""}`;
    // timeline
    const pts = ((LIVE.timeline && LIVE.timeline.points) || []).filter(pt => pt.seats[seatParam]);
    const sdNow = est.sd;
    timelineChart(document.getElementById("seat-timeline"), pts, [{ v: pt => pt.seats[seatParam].mu, color: css("--dem"), label: "est. margin" }],
      { yDomain: [-lim, lim], yFmt: v => fmt.pts(v, 0), zero: 0, band: [pt => pt.seats[seatParam].mu - 1.645 * sdNow, pt => pt.seats[seatParam].mu + 1.645 * sdNow],
        rightAxis: { domain: [0, 1], v: pt => pt.seats[seatParam].frac, fmt: d3.format(".0%"), label: "% in" } });
    // candidates
    const ctb = document.querySelector("#cands tbody"); ctb.innerHTML = "";
    const cands = {};
    for (const r of s.rows) for (const c of r.candidates) { const k = c.candidate + "|" + c.party; const a = cands[k] = cands[k] || { candidate: c.candidate, party: c.party, votes: 0, ed: 0, mail: 0, prov: 0, split: true }; a.votes += c.votes || 0; if (c.election_day_votes === null || c.election_day_votes === undefined) a.split = false; else { a.ed += c.election_day_votes; a.mail += c.mail_in_votes; a.prov += c.provisional_votes; } }
    const tot = Object.values(cands).reduce((a, c) => a + c.votes, 0);
    if (!Object.keys(cands).length) ctb.innerHTML = `<tr><td colspan="7" class="muted">no returns yet</td></tr>`;
    for (const c of Object.values(cands).sort((a, b2) => b2.votes - a.votes)) {
      ctb.insertAdjacentHTML("beforeend", `<tr><td>${c.candidate}</td><td><span class="pill ${c.party === "DEM" ? "D" : c.party === "REP" ? "R" : "none"}">${c.party}</span></td><td class="num">${fmt.int(c.votes)}</td><td class="num">${tot ? fmt.pct(c.votes / tot, 1) : "—"}</td><td class="num hide-sm">${c.split ? fmt.int(c.ed) : "—"}</td><td class="num hide-sm">${c.split ? fmt.int(c.mail) : "—"}</td><td class="num hide-sm">${c.split ? fmt.int(c.prov) : "—"}</td></tr>`);
    }
    // counties
    const tb = document.querySelector("#counties tbody"); tb.innerHTML = "";
    const names = { ed: "Election Day", mail: "Mail", prov: "Provisional", all: "All (no split)" };
    const rowBy = {}; for (const r of s.rows) rowBy[r.county.toUpperCase()] = r;
    for (const [cname, co] of Object.entries(est.counties)) {
      const cb = b.counties[cname]; const r = rowBy[cname];
      const pretty = cname[0] + cname.slice(1).toLowerCase();
      const prTxt = co.precincts_total ? `${fmt.int(co.precincts_reporting)}/${fmt.int(co.precincts_total)} precincts` : "precincts: —";
      const methods = co.bym ? ["ed", "mail", "prov"] : ["all"];
      methods.forEach((m, i) => {
        const expv = m === "all" ? Object.values(co.exp_adj).reduce((a, v) => a + v, 0) : co.exp_adj[m];
        const cnt = m === "all" ? Object.values(co.counted).reduce((a, v) => a + v, 0) : Object.values(co.bym[m]).reduce((a, v) => a + v, 0);
        const dv = m === "all" ? co.counted.dem : co.bym[m].dem, rv = m === "all" ? co.counted.rep : co.bym[m].rep;
        const share = dv + rv > 0 ? dv / (dv + rv) : null;
        const bench = m === "all" ? co.comp_share : cb.share[m];
        const swing = (share !== null && bench !== null) ? 200 * (share - bench) : null;
        const denom = Math.max(expv, cnt, 1);
        const bar = `<div class="bar" title="counted ${fmt.int(cnt)} of expected ${fmt.int(expv)}"><span class="exp" style="width:${Math.min(100, 100 * expv / denom)}%"></span><span style="width:${Math.min(100, 100 * (dv) / denom)}%;background:var(--dem)"></span><span style="left:${Math.min(100, 100 * dv / denom)}%;width:${Math.min(100, 100 * rv / denom)}%;background:var(--rep)"></span></div>`;
        tb.insertAdjacentHTML("beforeend", `<tr>${i === 0 ? `<td rowspan="${methods.length}"><b>${pretty}</b><br><span class="small">${prTxt}${co.turnout_ratio !== null && co.turnout_ratio !== undefined ? `<br>in-person ${fmt.num(100 * co.turnout_ratio, 0)}% of expected` : ""}${co.mail_pinned ? "<br>mail pinned by DoS file" : ""}</span></td>` : ""}<td>${names[m]}</td><td class="num">${fmt.int(expv)}</td><td class="num">${cnt ? fmt.int(cnt) : '<span class="muted">—</span>'}</td><td class="num">${cnt ? fmt.int(dv) : "—"}</td><td class="num">${cnt ? fmt.int(rv) : "—"}</td><td class="num">${share === null ? "—" : fmt.pct(share, 1)}</td><td class="num hide-sm">${bench === null ? "—" : fmt.pct(bench, 1)}</td><td class="num hide-sm">${swing === null ? "—" : fmt.pts(swing)}</td><td>${bar}</td></tr>`);
      });
    }
    document.getElementById("county-note").textContent = `Expected total for the seat ${fmt.int(est.exp_total)} votes (${b.exp_total ? fmt.int(b.exp_total) + " before live turnout adjustment" : ""}); counted ${fmt.int(est.counted_total)}; outstanding by the model ${fmt.int(est.out_total)}. "2024 bench" is the Democratic-side share of the two-party vote in 2024 for the same county and method; "Swing" is the reported share against it, in margin points.`;
    // diagnostics
    const dg = document.querySelector("#diag tbody"); dg.innerHTML = "";
    const items = [
      ["Prior (forecast)", `${fmt.pts(p.mu)} ± ${fmt.num(p.sd)} sd${p.fixed_prob !== null && p.fixed_prob !== undefined ? `; fixed probability ${fmt.pct(p.fixed_prob)}` : ""}`],
      ["2024 presidential benchmark in district", fmt.pts(p.ref_margin)],
      ["Share of expected vote counted / matched by method", `${fmt.pct(est.frac)} / ${fmt.pct(est.frac_matched)}`],
      ["Weight on returns (cap)", `${fmt.pct(est.w_returns)} (${fmt.pct(est.cap)})`],
      ["Returns-only estimate ± sd", `${fmt.pts(est.m_ret)} ± ${fmt.num(est.sd_ret)}`],
      ["Counted margin / projected outstanding margin", `${fmt.pts(est.m_counted)} / ${fmt.pts(est.m_out)}`],
      ["Learned in-person turnout vs expectation", fmt.pct(est.pooled_turnout)],
      ["Share of expected vote in counties with 0 precincts in", fmt.pct(est.zero_precinct_share)],
      ["Share of counted vote lacking a method split", fmt.pct(est.unmatched_share)],
      ["Displayed (damped) estimate", `${fmt.pts(d.mu)} ± ${fmt.num(d.sd)}; target ${fmt.pts(est.mu)}`],
    ];
    for (const [k, v] of items) dg.insertAdjacentHTML("beforeend", `<tr><td class="muted">${k}</td><td>${v}</td></tr>`);
  }

  // ---- main loop -----------------------------------------------------------
  let lastRes = null;
  function render() {
    if (!STATIC || !LIVE) return;
    renderStatus();
    lastRes = computeAll();
    if (page === "seat") renderSeat(lastRes); else renderIndex(lastRes);
  }
  async function tick() {
    try { await loadLive(); render(); }
    catch (e) { console.error(e); const u = document.getElementById("updated"); if (u) u.textContent = "could not load data: " + e.message; }
  }
  (async function init() {
    const navH = document.getElementById("nav-house");
    try { await loadStatic(); } catch (e) { document.getElementById("updated").textContent = "static data missing: " + e.message; return; }
    if (navH) { try { const r = await fetch("house.html", { method: "HEAD" }); if (!r.ok) navH.style.display = "none"; } catch (e) { navH.style.display = "none"; } }
    if (page !== "seat") bindSort();
    await tick();
    setInterval(tick, REFRESH_MS);
    setInterval(renderStatus, 30000);
  })();
})();
