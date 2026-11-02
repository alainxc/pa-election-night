/* needle.js - browser port of needle.py. Keep the two in step; tests/test_parity.py
   runs both on the same fixtures and fails on any difference > 1e-9. */
(function (root) {
  "use strict";
  const METHODS = ["ed", "mail", "prov"];
  const ROW_FIELD = { ed: "election_day_votes", mail: "mail_in_votes", prov: "provisional_votes" };

  const T = {
    SWING_SD0: 8.0, SWING_HALF: 0.10, COUNTY_SHRINK: 0.25, PRIOR_SWING_PSEUDO: 0.02,
    UNMATCHED_WEIGHT: 0.35, UNMATCHED_SD: 2.0,
    SIZE_U_ED: 0.20, SIZE_U_MAIL: 0.60, SIZE_U_MAIL_PINNED: 0.06, SIZE_U_UNMATCHED: 0.40,
    MAIL_RETURNED_COUNT_RATE: 0.97, COUNT_SD: 0.5, ZERO_PRECINCT_SD: 10.0, MAIL_GAP_SD: 6.0,
    TURNOUT_COMPLETE: 0.95, TURNOUT_PSEUDO: 0.10, TURNOUT_CLIP: [0.5, 1.5],
    EARLY_FLOOR: 4.0, LATE_FLOOR: 0.75,
    CAP_POINTS: [[0.0, 0.10], [0.25, 0.35], [0.60, 0.85], [0.90, 1.0]],
    CALL_P: 0.99, CALL_FRAC: 0.90, LIKELY_P: 0.95, LEAN_P: 0.80,
    DAMP: 0.5, SWING_FLAG_PTS: 5.0, SWING_FLAG_SECONDS: 15 * 60,
  };

  // erf: W. J. Cody's rational approximations, |err| < 1e-16 (matches math.erf)
  function erf(x) {
    const ax = Math.abs(x);
    let r;
    if (ax < 0.5) {
      const t = x * x;
      const a = [3.16112374387056560e00, 1.13864154151050156e02, 3.77485237685302021e02, 3.20937758913846947e03, 1.85777706184603153e-1];
      const b = [2.36012909523441209e01, 2.44024637934444173e02, 1.28261652607737228e03, 2.84423683343917062e03];
      let num = a[4] * t, den = t;
      for (let i = 0; i < 3; i++) { num = (num + a[i]) * t; den = (den + b[i]) * t; }
      return x * (num + a[3]) / (den + b[3]);
    }
    if (ax <= 4.0) {
      const c = [5.64188496988670089e-1, 8.88314979438837594e00, 6.61191906371416295e01, 2.98635138197400131e02, 8.81952221241769090e02, 1.71204761263407058e03, 2.05107837782607147e03, 1.23033935479799725e03, 2.15311535474403846e-8];
      const d = [1.57449261107098347e01, 1.17693950891312499e02, 5.37181101862009858e02, 1.62138957456669019e03, 3.29079923573345963e03, 4.36261909014324716e03, 3.43936767414372164e03, 1.23033935480374942e03];
      let num = c[8] * ax, den = ax;
      for (let i = 0; i < 7; i++) { num = (num + c[i]) * ax; den = (den + d[i]) * ax; }
      r = (num + c[7]) / (den + d[7]);
    } else {
      const p = [3.05326634961232344e-1, 3.60344899949804439e-1, 1.25781726111229246e-1, 1.60837851487422766e-2, 6.58749161529837803e-4, 1.63153871373020978e-2];
      const q = [2.56852019228982242e00, 1.87295284992346725e00, 5.27905102951428412e-1, 6.05183413124413191e-2, 2.33520497626869185e-3];
      const t = 1.0 / (ax * ax);
      let num = p[5] * t, den = t;
      for (let i = 0; i < 4; i++) { num = (num + p[i]) * t; den = (den + q[i]) * t; }
      r = t * (num + p[4]) / (den + q[4]);
      r = (0.5641895835477562869 - r) / ax;
    }
    const z = Math.floor(ax * 16) / 16;
    const del = (ax - z) * (ax + z);
    r = Math.exp(-z * z) * Math.exp(-del) * r;
    r = 1.0 - r;
    return x < 0 ? -r : r;
  }
  function normCdf(x) { return 0.5 * (1.0 + erf(x / Math.SQRT2)); }

  function interp(points, x) {
    if (x <= points[0][0]) return points[0][1];
    for (let i = 0; i < points.length - 1; i++) {
      const [x0, y0] = points[i], [x1, y1] = points[i + 1];
      if (x <= x1) return y0 + (y1 - y0) * (x - x0) / (x1 - x0);
    }
    return points[points.length - 1][1];
  }
  const clip = (x, lo, hi) => (x < lo ? lo : x > hi ? hi : x);
  const isNil = v => v === null || v === undefined;
  const sideOf = (party, demSide) => (party === demSide ? "dem" : party === "REP" ? "rep" : "oth");

  function countedByMethod(row, demSide) {
    const totals = { dem: 0, rep: 0, oth: 0 };
    const bym = {}; METHODS.forEach(m => bym[m] = { dem: 0, rep: 0, oth: 0 });
    let matched = true;
    for (const c of (row.candidates || [])) {
      const s = sideOf(c.party, demSide);
      totals[s] += c.votes || 0;
      const parts = METHODS.map(m => c[ROW_FIELD[m]]);
      if (parts.some(isNil)) matched = false;
      else METHODS.forEach((m, i) => bym[m][s] += parts[i]);
    }
    if (matched) {
      for (const s of Object.keys(totals)) {
        if (METHODS.reduce((a, m) => a + bym[m][s], 0) !== totals[s]) { matched = false; break; }
      }
    }
    return [totals, matched ? bym : null];
  }
  const sumVals = o => Object.values(o).reduce((a, b) => a + b, 0);

  function estimateSeat(prior, baseline, rows, extra) {
    extra = extra || {};
    const mailReq = extra.mail_requested || {};
    const mailRet = extra.mail_returned || {};
    const countyMailRef = extra.county_mail_ref || {};
    const counties = baseline.counties || {};
    let expTotal = baseline.exp_total;
    if (isNil(expTotal)) { expTotal = 0; for (const c of Object.values(counties)) for (const m of METHODS) expTotal += c.exp[m]; }
    const demSide = prior.dem_side || "DEM";
    const mu0 = prior.mu, sd0 = prior.sd;
    const refMargin = prior.ref_margin;
    const priorSwing = !isNil(refMargin) ? (mu0 - refMargin) : 0.0;
    const byCounty = {}; for (const r of rows) byCounty[r.county.toUpperCase()] = r;

    const units = [];
    let countedTotal = 0, countedDem = 0, countedRep = 0, countedDemMat = 0, countedRepMat = 0;
    let unmatchedCounted = 0, zeroPrecinctShare = 0, anyReport = false;
    const countyOut = {};
    for (const [cname, cb] of Object.entries(counties)) {
      const expC = {}; METHODS.forEach(m => expC[m] = +cb.exp[m]);
      const expCTot = sumVals(expC);
      const r = byCounty[cname];
      const [totals, bym] = r ? countedByMethod(r, demSide) : [{ dem: 0, rep: 0, oth: 0 }, null];
      const cTot = totals.dem + totals.rep + totals.oth;
      countedTotal += cTot; countedDem += totals.dem; countedRep += totals.rep;
      if (cTot > 0) anyReport = true;
      const pr = r ? r.precincts_reporting : null;
      const pt = r ? r.precincts_total : null;
      const prFrac = (!isNil(pr) && pt) ? pr / pt : null;
      if ((pr === 0 || isNil(pr)) && cTot === 0 && expTotal > 0) zeroPrecinctShare += expCTot / expTotal;
      let twoExp = 0, acc = 0;
      for (const m of METHODS) if (!isNil(cb.share[m])) { twoExp += expC[m]; acc += expC[m] * cb.share[m]; }
      const compShare = twoExp > 0 ? acc / twoExp : null;
      if (bym !== null && cTot > 0) {
        for (const m of METHODS) {
          const vd = bym[m].dem, vr = bym[m].rep, n2 = vd + vr, s = isNil(cb.share[m]) ? null : cb.share[m];
          let delta = null;
          if (n2 > 0 && s !== null) { delta = 200.0 * (vd / n2 - s); countedDemMat += vd; countedRepMat += vr; }
          units.push({ county: cname, method: m, n2, delta, weight: 1.0, exp: expC[m], counted: sumVals(bym[m]), share: s, matched: true, pr_frac: prFrac });
        }
      } else {
        const n2 = totals.dem + totals.rep;
        const delta = (n2 > 0 && compShare !== null) ? 200.0 * (totals.dem / n2 - compShare) : null;
        unmatchedCounted += cTot;
        units.push({ county: cname, method: "all", n2, delta, weight: T.UNMATCHED_WEIGHT, exp: expCTot, counted: cTot, share: compShare, matched: false, pr_frac: prFrac });
      }
      let ratioC = null;
      if (prFrac !== null && prFrac >= T.TURNOUT_COMPLETE && bym !== null && expC.ed > 0) ratioC = clip(sumVals(bym.ed) / expC.ed, T.TURNOUT_CLIP[0], T.TURNOUT_CLIP[1]);
      countyOut[cname] = { exp: expC, counted: totals, bym, comp_share: compShare, precincts_reporting: isNil(pr) ? null : pr, precincts_total: isNil(pt) ? null : pt, pr_frac: prFrac, turnout_ratio: ratioC };
    }

    let tNum = T.TURNOUT_PSEUDO * expTotal * 1.0, tDen = T.TURNOUT_PSEUDO * expTotal, completeExp = 0;
    for (const co of Object.values(countyOut)) {
      if (co.turnout_ratio !== null) { const wC = sumVals(co.exp); tNum += wC * co.turnout_ratio; tDen += wC; completeExp += wC; }
    }
    const pooledTurnout = tDen > 0 ? tNum / tDen : 1.0;
    const completeShare = expTotal > 0 ? completeExp / expTotal : 0.0;
    for (const [cname, co] of Object.entries(countyOut)) {
      const ratio = co.turnout_ratio !== null ? co.turnout_ratio : pooledTurnout;
      const adj = {}; METHODS.forEach(m => adj[m] = co.exp[m] * ratio);
      let pinned = false;
      const ret = mailRet[cname];
      if (!isNil(ret) && co.exp.mail > 0) {
        const ref = countyMailRef[cname];
        const sliceShare = ref ? co.exp.mail / ref : null;
        if (sliceShare !== null) { adj.mail = ret * T.MAIL_RETURNED_COUNT_RATE * sliceShare; pinned = true; }
      }
      co.exp_adj = adj; co.mail_pinned = pinned;
    }
    for (const u of units) {
      const co = countyOut[u.county];
      u.exp_adj = u.method === "all" ? sumVals(co.exp_adj) : co.exp_adj[u.method];
    }
    let expAdjTotal = 0; for (const co of Object.values(countyOut)) expAdjTotal += sumVals(co.exp_adj);
    const expEff = Math.max(expAdjTotal, countedTotal);
    const frac = expEff > 0 ? countedTotal / expEff : 0.0;
    const fracMatched = expEff > 0 ? (countedDemMat + countedRepMat) / expEff : 0.0;

    let num = T.PRIOR_SWING_PSEUDO * expEff * priorSwing, den = T.PRIOR_SWING_PSEUDO * expEff;
    for (const u of units) if (u.delta !== null && u.n2 > 0) { num += u.weight * u.n2 * u.delta; den += u.weight * u.n2; }
    const pooledSwing = den > 0 ? num / den : priorSwing;

    const mCounted = (countedDem + countedRep) > 0 ? 100.0 * (countedDem - countedRep) / (countedDem + countedRep) : null;
    const twoShare = countedTotal > 0 ? (countedDem + countedRep) / countedTotal : 0.985;
    let outTotal = 0, outDem = 0, outRep = 0;
    const sizeVarByCounty = {};
    for (const u of units) {
      const co = countyOut[u.county];
      let out = Math.max(0.0, u.exp_adj - u.counted);
      if (u.method === "ed" && u.pr_frac !== null) out = Math.min(out, u.exp_adj * Math.max(0.0, 1.0 - u.pr_frac));
      let n0;
      if (u.method === "all") {
        n0 = T.COUNTY_SHRINK * u.exp / T.UNMATCHED_WEIGHT;
        if (u.pr_frac !== null && u.pr_frac >= T.TURNOUT_COMPLETE) out = Math.min(out, Math.max(0.0, co.exp_adj.mail + co.exp_adj.prov));
      } else n0 = T.COUNTY_SHRINK * u.exp;
      const own = u.delta !== null ? (u.n2 * u.delta + n0 * pooledSwing) / (u.n2 + n0) : pooledSwing;
      const s = u.share;
      if (s === null) continue;
      const ps = clip(s + own / 200.0, 0.02, 0.98);
      const twoOut = out * twoShare;
      outTotal += out; outDem += twoOut * ps; outRep += twoOut * (1.0 - ps);
      u.out = out; u.ps = ps;
      let su;
      if (u.method === "ed") su = u.pr_frac !== null ? T.SIZE_U_ED : T.SIZE_U_MAIL;
      else if (u.method === "all") su = T.SIZE_U_UNMATCHED;
      else su = (co.mail_pinned && u.method === "mail") ? T.SIZE_U_MAIL_PINNED : T.SIZE_U_MAIL;
      u.size_u = su;
    }
    const projDem = countedDem + outDem, projRep = countedRep + outRep;
    const two = projDem + projRep;
    const mRet = two > 0 ? 100.0 * (projDem - projRep) / two : mu0;
    const mOut = (outDem + outRep) > 0 ? 100.0 * (outDem - outRep) / (outDem + outRep) : null;

    const outShare = expEff > 0 ? outTotal / expEff : 1.0;
    const unmatchedShare = countedTotal > 0 ? unmatchedCounted / countedTotal : 0.0;
    const sdSwing = Math.sqrt(T.SWING_SD0 ** 2 / (1.0 + fracMatched / T.SWING_HALF) + (T.UNMATCHED_SD ** 2) * unmatchedShare);
    let v = (outShare * sdSwing) ** 2 + T.COUNT_SD ** 2;
    const baseM = mCounted !== null ? mCounted : mRet;
    for (const u of units) {
      if (u.out === undefined || u.out <= 0 || two <= 0) continue;
      const bucketMargin = 100.0 * (2.0 * u.ps - 1.0);
      const effect = u.size_u * (u.out * twoShare / two) * (bucketMargin - baseM);
      sizeVarByCounty[u.county] = (sizeVarByCounty[u.county] || 0.0) + Math.abs(effect);
    }
    for (const sv of Object.values(sizeVarByCounty)) v += sv * sv;
    v += (T.ZERO_PRECINCT_SD * zeroPrecinctShare) ** 2;
    let mailGapShare = 0.0;
    if (Object.keys(mailReq).length) {
      for (const [cname, co] of Object.entries(countyOut)) {
        const req = mailReq[cname];
        if (isNil(req) || co.mail_pinned) continue;
        const ref = countyMailRef[cname];
        if (!ref) continue;
        const sliceShare = co.exp.mail / ref;
        const countedMail = co.bym ? sumVals(co.bym.mail) : 0;
        const gap = Math.max(0.0, 0.8 * req * sliceShare - Math.max(countedMail, co.exp_adj.mail));
        mailGapShare += expEff > 0 ? gap / expEff : 0.0;
      }
      v += (T.MAIL_GAP_SD * mailGapShare) ** 2;
    }
    const sdRet = Math.sqrt(v);

    const cap = interp(T.CAP_POINTS, frac);
    const wPrec = (sd0 ** 2 + sdRet ** 2) > 0 ? sd0 ** 2 / (sd0 ** 2 + sdRet ** 2) : 0.0;
    let w = anyReport ? Math.min(wPrec, cap) : 0.0;
    if (anyReport && outTotal <= 0.0) w = 1.0;
    const muPost = w * mRet + (1.0 - w) * mu0;
    let sdPost = Math.sqrt((w * sdRet) ** 2 + ((1.0 - w) * sd0) ** 2);
    const floor = frac < 0.5 ? T.EARLY_FLOOR : interp([[0.5, T.EARLY_FLOOR], [1.0, T.LATE_FLOOR]], frac);
    sdPost = Math.max(sdPost, floor);

    const fixed = prior.fixed_prob;
    let p, status;
    if (!isNil(fixed)) { p = +fixed; status = "fixed"; }
    else { p = normCdf(muPost / sdPost); status = classify(p, frac, w, anyReport, muPost, mCounted); }

    for (const co of Object.values(countyOut)) { co.out = { ed: 0, mail: 0, prov: 0, all: 0 }; }
    for (const u of units) if (u.out !== undefined) countyOut[u.county].out[u.method] = u.out;

    return {
      mu: muPost, sd: sdPost, p_dem: p, lo: muPost - 1.645 * sdPost, hi: muPost + 1.645 * sdPost,
      frac, frac_matched: fracMatched, counted_total: countedTotal, exp_total: expEff, out_total: outTotal,
      m_ret: mRet, sd_ret: sdRet, m_counted: mCounted, m_out: mOut,
      pooled_swing: pooledSwing, prior_swing: priorSwing, pooled_turnout: pooledTurnout, complete_share: completeShare,
      w_returns: w, cap, zero_precinct_share: zeroPrecinctShare, unmatched_share: unmatchedShare, mail_gap_share: mailGapShare,
      status, any_report: anyReport, fixed: !isNil(fixed), counties: countyOut,
    };
  }

  function classify(p, frac, w, anyReport, mu, mCounted) {
    if (!anyReport) return "no returns yet";
    const lead = p >= 0.5 ? "D" : "R";
    const q = Math.max(p, 1.0 - p);
    const agrees = (isNil(mu) || isNil(mCounted) || ((mu > 0) === (mCounted > 0)));
    if (q >= T.CALL_P && frac >= T.CALL_FRAC && w >= 0.85 && agrees) return `called ${lead} (tracker)`;
    if (q >= T.LIKELY_P) return `likely ${lead}`;
    if (q >= T.LEAN_P) return `leaning ${lead}`;
    return "toss-up";
  }

  function damp(prev, target, nowTs) {
    if (!prev) return { mu: target.mu, sd: target.sd, p_dem: target.p_dem, target_mu: target.mu, swing_until: 0 };
    const mu = prev.mu + T.DAMP * (target.mu - prev.mu);
    const sd = prev.sd + T.DAMP * (target.sd - prev.sd);
    const p = prev.p_dem + T.DAMP * (target.p_dem - prev.p_dem);
    let swingUntil = prev.swing_until || 0;
    const prevTarget = isNil(prev.target_mu) ? target.mu : prev.target_mu;
    if (Math.abs(target.mu - prevTarget) > T.SWING_FLAG_PTS) swingUntil = nowTs + T.SWING_FLAG_SECONDS;
    return { mu, sd, p_dem: p, target_mu: target.mu, swing_until: swingUntil };
  }

  function poissonBinomial(ps) {
    let dist = [1.0];
    for (const p of ps) {
      const nxt = new Array(dist.length + 1).fill(0.0);
      dist.forEach((v, k) => { nxt[k] += v * (1.0 - p); nxt[k + 1] += v * p; });
      dist = nxt;
    }
    return dist;
  }

  function chamberOdds(seatProbs, holdDem, holdRep, totalSeats, majority) {
    const dDist = poissonBinomial(seatProbs.map(p => clip(p[0], 0, 1)));
    const rDist = poissonBinomial(seatProbs.map(p => clip(p[1], 0, 1)));
    let pDem = 0, pRep = 0, mean = holdDem, cum = 0, p05 = null, p95 = null;
    dDist.forEach((v, k) => { if (k + holdDem >= majority) pDem += v; mean += k * v; });
    rDist.forEach((v, k) => { if (k + holdRep >= majority) pRep += v; });
    dDist.forEach((v, k) => { cum += v; if (p05 === null && cum >= 0.05) p05 = k + holdDem; if (p95 === null && cum >= 0.95) p95 = k + holdDem; });
    return {
      p_dem_majority: pDem, p_rep_majority: pRep, p_no_majority: Math.max(0.0, 1.0 - pDem - pRep),
      dem_seats_mean: mean, dem_seats_p05: p05, dem_seats_p95: p95,
      dem_dist: dDist.map((v, k) => ({ seats: k + holdDem, p: v })),
      holdover_dem: holdDem, holdover_rep: holdRep, total_seats: totalSeats, majority,
    };
  }

  const api = { T, METHODS, normCdf, interp, clip, estimateSeat, classify, damp, poissonBinomial, chamberOdds, countedByMethod };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.Needle = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
