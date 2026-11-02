"""
needle.py - the election-night estimate for one legislative seat, and the
chamber odds. This module is pure arithmetic: no I/O, no network. It is
mirrored line for line by docs/assets/needle.js, and tests/test_parity.py
checks the two agree on the same fixtures.

Design goals (in order): never fabricate, expect the mail-first skew rather
than react to it, keep the prior in charge until a real share of the vote is
counted, and move slowly.

All margins are Democratic-side two-party margins in percentage points
(positive = the Democratic-side candidate ahead). For a seat where the
Democratic side is an independent (Senate 46 in 2026), "dem" below means that
candidate; priors.json says which party code is the Democratic side.

Inputs per seat
  prior:    {"mu": float, "sd": float, "fixed_prob": float|None,
             "dem_side": "DEM"|"IND"|..., "ref_margin": float}
            ref_margin is the reference race's two-party margin in the
            district (from the baseline), so mu - ref_margin is the swing the
            forecast expects relative to the benchmark.
  baseline: {"exp_total": float, "counties": {COUNTY: {"exp": {ed, mail, prov},
             "share": {ed, mail, prov}}}}  (docs/data/baseline_<year>.json)
  rows:     the seat's rows from returns.json (one per county), each with
            candidates[{party, votes, election_day_votes, mail_in_votes,
            provisional_votes}], precincts_reporting, precincts_total
  extra:    optional {"mail_returned": {COUNTY: int}, "mail_requested": {COUNTY: int}}
            from the DoS daily mail ballot report (county-wide ballots), if the
            user has placed docs/data/mail_<year>.json. Returned ballots pin the
            county's expected mail vote; requested ballots only widen the range.

Output: see estimate_seat().
"""

import math

METHODS = ("ed", "mail", "prov")
ROW_FIELD = {"ed": "election_day_votes", "mail": "mail_in_votes", "prov": "provisional_votes"}

# ---- tunables (documented on the Methods page) -----------------------------
SWING_SD0 = 8.0          # pts: sd of the district swing before any matched vote is in
SWING_HALF = 0.10        # matched fraction at which swing sd has fallen by sqrt(2)
COUNTY_SHRINK = 0.25     # a county-method's own swing gets full weight once it has
                         # reported 4x this share of its expected vote
PRIOR_SWING_PSEUDO = 0.02  # pseudo-votes (share of expected) pulling pooled swing to prior
UNMATCHED_WEIGHT = 0.35  # weight on a county's swing when it has no method split
UNMATCHED_SD = 2.0       # pts added (in quadrature, scaled by unmatched share)
# relative uncertainty on the SIZE of each outstanding bucket
SIZE_U_ED = 0.20         # in-person, with precincts reporting known
SIZE_U_MAIL = 0.60       # mail/provisional with no DoS ballots-returned figure
SIZE_U_MAIL_PINNED = 0.06  # mail when ballots returned is known for the county
SIZE_U_UNMATCHED = 0.40  # county with no method split
MAIL_RETURNED_COUNT_RATE = 0.97  # share of returned mail ballots that end up counted
COUNT_SD = 0.5           # pts: late corrections, provisionals, write-ins
ZERO_PRECINCT_SD = 10.0  # pts per unit share of expected vote in counties with 0 precincts in
MAIL_GAP_SD = 6.0        # pts per unit share of expected vote in requested-but-uncounted mail
TURNOUT_COMPLETE = 0.95  # a county's in-person count is "complete" at this share of precincts
TURNOUT_PSEUDO = 0.10    # pseudo-weight (share of expected vote) pulling the learned turnout ratio to 1
TURNOUT_CLIP = (0.5, 1.5)
EARLY_FLOOR = 4.0        # pts: sd cannot fall below this until 50% of expected vote is in
LATE_FLOOR = 0.75        # pts: sd floor once everything is counted
CAP_POINTS = ((0.0, 0.10), (0.25, 0.35), (0.60, 0.85), (0.90, 1.0))
CALL_P = 0.99
CALL_FRAC = 0.90
LIKELY_P = 0.95
LEAN_P = 0.80
DAMP = 0.5               # fraction of the gap closed per cycle
SWING_FLAG_PTS = 5.0
SWING_FLAG_SECONDS = 15 * 60


def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def interp(points, x):
    if x <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return points[-1][1]


def clip(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def side_of(party, dem_side):
    if party == dem_side:
        return "dem"
    if party == "REP":
        return "rep"
    return "oth"


def counted_by_method(row, dem_side):
    """Return (totals, by_method) where by_method[m] = {dem, rep, oth}, or None
    when the row has no usable method split."""
    totals = {"dem": 0, "rep": 0, "oth": 0}
    bym = {m: {"dem": 0, "rep": 0, "oth": 0} for m in METHODS}
    matched = True
    for c in row.get("candidates") or []:
        s = side_of(c.get("party"), dem_side)
        v = c.get("votes") or 0
        totals[s] += v
        parts = [c.get(ROW_FIELD[m]) for m in METHODS]
        if any(p is None for p in parts):
            matched = False
        else:
            for m, p in zip(METHODS, parts):
                bym[m][s] += p
    if matched:
        for s in totals:
            if sum(bym[m][s] for m in METHODS) != totals[s]:
                matched = False
                break
    return totals, (bym if matched else None)


def estimate_seat(prior, baseline, rows, extra=None):
    extra = extra or {}
    mail_req = extra.get("mail_requested") or {}
    mail_ret = extra.get("mail_returned") or {}
    counties = baseline.get("counties", {})
    exp_total = baseline.get("exp_total") or sum(sum(c["exp"].values()) for c in counties.values())
    dem_side = prior.get("dem_side", "DEM")
    mu0, sd0 = prior["mu"], prior["sd"]
    ref_margin = prior.get("ref_margin")
    prior_swing = (mu0 - ref_margin) if ref_margin is not None else 0.0

    by_county = {r["county"].upper(): r for r in rows}

    # ---- pass 1: counted votes, matched swings -----------------------------
    units = []
    counted_total = 0.0
    counted_dem = counted_rep = 0.0
    counted_dem_mat = counted_rep_mat = 0.0
    unmatched_counted = 0.0
    zero_precinct_share = 0.0
    county_out = {}
    any_report = False
    for cname, cb in counties.items():
        exp_c = {m: float(cb["exp"][m]) for m in METHODS}
        exp_c_tot = sum(exp_c.values())
        r = by_county.get(cname)
        totals, bym = counted_by_method(r, dem_side) if r else ({"dem": 0, "rep": 0, "oth": 0}, None)
        c_tot = sum(totals.values())
        counted_total += c_tot
        counted_dem += totals["dem"]
        counted_rep += totals["rep"]
        if c_tot > 0:
            any_report = True
        pr = (r or {}).get("precincts_reporting")
        pt = (r or {}).get("precincts_total")
        pr_frac = (pr / pt) if (pr is not None and pt) else None
        if (pr == 0 or pr is None) and c_tot == 0 and exp_total > 0:
            zero_precinct_share += exp_c_tot / exp_total
        two_exp = sum(exp_c[m] for m in METHODS if cb["share"][m] is not None)
        comp_share = (sum(exp_c[m] * cb["share"][m] for m in METHODS if cb["share"][m] is not None) / two_exp
                      if two_exp > 0 else None)
        if bym is not None and c_tot > 0:
            for m in METHODS:
                vd, vr = bym[m]["dem"], bym[m]["rep"]
                n2 = vd + vr
                s = cb["share"][m]
                delta = None
                if n2 > 0 and s is not None:
                    delta = 200.0 * (vd / n2 - s)
                    counted_dem_mat += vd
                    counted_rep_mat += vr
                units.append({"county": cname, "method": m, "n2": n2, "delta": delta,
                              "weight": 1.0, "exp": exp_c[m], "counted": sum(bym[m].values()),
                              "share": s, "matched": True, "pr_frac": pr_frac})
        else:
            n2 = totals["dem"] + totals["rep"]
            delta = 200.0 * (totals["dem"] / n2 - comp_share) if (n2 > 0 and comp_share is not None) else None
            unmatched_counted += c_tot
            units.append({"county": cname, "method": "all", "n2": n2, "delta": delta,
                          "weight": UNMATCHED_WEIGHT, "exp": exp_c_tot, "counted": c_tot,
                          "share": comp_share, "matched": False, "pr_frac": pr_frac})
        ratio_c = None
        if pr_frac is not None and pr_frac >= TURNOUT_COMPLETE and bym is not None and exp_c["ed"] > 0:
            ratio_c = clip(sum(bym["ed"].values()) / exp_c["ed"], TURNOUT_CLIP[0], TURNOUT_CLIP[1])
        county_out[cname] = {"exp": exp_c, "counted": totals, "bym": bym, "comp_share": comp_share,
                             "precincts_reporting": pr, "precincts_total": pt, "pr_frac": pr_frac,
                             "turnout_ratio": ratio_c}

    # pooled in-person turnout ratio from counties whose in-person count is complete
    t_num = TURNOUT_PSEUDO * exp_total * 1.0
    t_den = TURNOUT_PSEUDO * exp_total
    complete_exp = 0.0
    for cname, co in county_out.items():
        if co["turnout_ratio"] is not None:
            w_c = sum(co["exp"].values())
            t_num += w_c * co["turnout_ratio"]
            t_den += w_c
            complete_exp += w_c
    pooled_turnout = t_num / t_den if t_den > 0 else 1.0
    complete_share = complete_exp / exp_total if exp_total > 0 else 0.0
    # adjusted expectations: in-person ratio applies to every method, except
    # that returned mail ballots (when known) pin the county's mail expectation
    for cname, co in county_out.items():
        ratio = co["turnout_ratio"] if co["turnout_ratio"] is not None else pooled_turnout
        adj = {m: co["exp"][m] * ratio for m in METHODS}
        pinned = False
        ret = mail_ret.get(cname)
        if ret is not None and co["exp"]["mail"] > 0:
            # the district's slice of the county's returned ballots, in proportion to the baseline
            county_mail_ref = extra.get("county_mail_ref", {}).get(cname)
            slice_share = (co["exp"]["mail"] / county_mail_ref) if county_mail_ref else None
            if slice_share is not None:
                adj["mail"] = ret * MAIL_RETURNED_COUNT_RATE * slice_share
                pinned = True
        co["exp_adj"] = adj
        co["mail_pinned"] = pinned
    for u in units:
        co = county_out[u["county"]]
        u["exp_adj"] = sum(co["exp_adj"].values()) if u["method"] == "all" else co["exp_adj"][u["method"]]
    exp_adj_total = sum(sum(co["exp_adj"].values()) for co in county_out.values())
    exp_eff = max(exp_adj_total, counted_total)
    frac = counted_total / exp_eff if exp_eff > 0 else 0.0
    frac_matched = (counted_dem_mat + counted_rep_mat) / exp_eff if exp_eff > 0 else 0.0

    # ---- pass 2: pooled swing vs benchmark ---------------------------------
    num = PRIOR_SWING_PSEUDO * exp_eff * prior_swing
    den = PRIOR_SWING_PSEUDO * exp_eff
    for u in units:
        if u["delta"] is not None and u["n2"] > 0:
            num += u["weight"] * u["n2"] * u["delta"]
            den += u["weight"] * u["n2"]
    pooled_swing = num / den if den > 0 else prior_swing

    # ---- pass 3: project the outstanding vote ------------------------------
    m_counted = (100.0 * (counted_dem - counted_rep) / (counted_dem + counted_rep)
                 if counted_dem + counted_rep > 0 else None)
    two_share = (counted_dem + counted_rep) / counted_total if counted_total > 0 else 0.985
    out_total = 0.0
    out_dem = out_rep = 0.0
    size_var_by_county = {}
    for u in units:
        co = county_out[u["county"]]
        out = max(0.0, u["exp_adj"] - u["counted"])
        if u["method"] == "ed" and u["pr_frac"] is not None:
            out = min(out, u["exp_adj"] * max(0.0, 1.0 - u["pr_frac"]))
        if u["method"] == "all":
            n0 = COUNTY_SHRINK * u["exp"] / UNMATCHED_WEIGHT
            if u["pr_frac"] is not None and u["pr_frac"] >= TURNOUT_COMPLETE:
                out = min(out, max(0.0, co["exp_adj"]["mail"] + co["exp_adj"]["prov"]))
        else:
            n0 = COUNTY_SHRINK * u["exp"]
        if u["delta"] is not None:
            own = (u["n2"] * u["delta"] + n0 * pooled_swing) / (u["n2"] + n0)
        else:
            own = pooled_swing
        s = u["share"]
        if s is None:
            continue
        ps = clip(s + own / 200.0, 0.02, 0.98)
        two_out = out * two_share
        out_total += out
        out_dem += two_out * ps
        out_rep += two_out * (1.0 - ps)
        u["out"] = out
        u["ps"] = ps
        # how uncertain is the SIZE of this bucket?
        if u["method"] == "ed":
            su = SIZE_U_ED if u["pr_frac"] is not None else SIZE_U_MAIL
        elif u["method"] == "all":
            su = SIZE_U_UNMATCHED
        else:
            su = SIZE_U_MAIL_PINNED if co["mail_pinned"] and u["method"] == "mail" else SIZE_U_MAIL
        u["size_u"] = su
    proj_dem = counted_dem + out_dem
    proj_rep = counted_rep + out_rep
    two = proj_dem + proj_rep
    m_ret = 100.0 * (proj_dem - proj_rep) / two if two > 0 else mu0
    m_out = 100.0 * (out_dem - out_rep) / (out_dem + out_rep) if out_dem + out_rep > 0 else None

    # ---- pass 4: uncertainty of the returns estimate -----------------------
    out_share = out_total / exp_eff if exp_eff > 0 else 1.0
    unmatched_share = unmatched_counted / counted_total if counted_total > 0 else 0.0
    sd_swing = math.sqrt(SWING_SD0 ** 2 / (1.0 + frac_matched / SWING_HALF) + (UNMATCHED_SD ** 2) * unmatched_share)
    var = (out_share * sd_swing) ** 2 + COUNT_SD ** 2
    # size uncertainty: if a bucket turns out su% bigger or smaller, the final
    # margin moves by its margin-vs-counted gap times that share. Perfectly
    # correlated within a county, independent across counties.
    base_m = m_counted if m_counted is not None else m_ret
    for u in units:
        if "out" not in u or u["out"] <= 0 or two <= 0:
            continue
        bucket_margin = 100.0 * (2.0 * u["ps"] - 1.0)
        effect = u["size_u"] * (u["out"] * two_share / two) * (bucket_margin - base_m)
        size_var_by_county[u["county"]] = size_var_by_county.get(u["county"], 0.0) + abs(effect)
    for v in size_var_by_county.values():
        var += v * v
    var += (ZERO_PRECINCT_SD * zero_precinct_share) ** 2
    mail_gap_share = 0.0
    if mail_req:
        for cname, co in county_out.items():
            req = mail_req.get(cname)
            if req is None or co["mail_pinned"]:
                continue
            county_mail_ref = extra.get("county_mail_ref", {}).get(cname)
            if not county_mail_ref:
                continue
            slice_share = co["exp"]["mail"] / county_mail_ref
            counted_mail = sum(co["bym"]["mail"].values()) if co["bym"] else 0
            gap = max(0.0, 0.8 * req * slice_share - max(counted_mail, co["exp_adj"]["mail"]))
            mail_gap_share += gap / exp_eff if exp_eff > 0 else 0.0
        var += (MAIL_GAP_SD * mail_gap_share) ** 2
    sd_ret = math.sqrt(var)

    # ---- pass 5: blend with the prior, floors -----------------------------
    cap = interp(CAP_POINTS, frac)
    w_prec = sd0 ** 2 / (sd0 ** 2 + sd_ret ** 2) if (sd0 ** 2 + sd_ret ** 2) > 0 else 0.0
    w = min(w_prec, cap) if any_report else 0.0
    if any_report and out_total <= 0.0:
        w = 1.0  # nothing outstanding: the prior has nothing left to say
    mu_post = w * m_ret + (1.0 - w) * mu0
    sd_post = math.sqrt((w * sd_ret) ** 2 + ((1.0 - w) * sd0) ** 2)
    floor = EARLY_FLOOR if frac < 0.5 else interp(((0.5, EARLY_FLOOR), (1.0, LATE_FLOOR)), frac)
    sd_post = max(sd_post, floor)

    fixed = prior.get("fixed_prob")
    if fixed is not None:
        p = float(fixed)
        status = "fixed"
    else:
        p = norm_cdf(mu_post / sd_post)
        status = classify(p, frac, w, any_report, mu_post, m_counted)

    # per-county detail for the seat page
    for cname, co in county_out.items():
        co["out"] = {m: 0.0 for m in METHODS}
        co["out"]["all"] = 0.0
    for u in units:
        if "out" in u:
            county_out[u["county"]]["out"][u["method"]] = u["out"]

    return {
        "mu": mu_post, "sd": sd_post, "p_dem": p,
        "lo": mu_post - 1.645 * sd_post, "hi": mu_post + 1.645 * sd_post,
        "frac": frac, "frac_matched": frac_matched,
        "counted_total": counted_total, "exp_total": exp_eff, "out_total": out_total,
        "m_ret": m_ret, "sd_ret": sd_ret, "m_counted": m_counted, "m_out": m_out,
        "pooled_swing": pooled_swing, "prior_swing": prior_swing,
        "pooled_turnout": pooled_turnout, "complete_share": complete_share,
        "w_returns": w, "cap": cap,
        "zero_precinct_share": zero_precinct_share, "unmatched_share": unmatched_share,
        "mail_gap_share": mail_gap_share,
        "status": status, "any_report": any_report, "fixed": fixed is not None,
        "counties": county_out,
    }


def classify(p, frac, w, any_report, mu=None, m_counted=None):
    if not any_report:
        return "no returns yet"
    lead = "D" if p >= 0.5 else "R"
    q = max(p, 1.0 - p)
    counted_agrees = (mu is None or m_counted is None or (mu > 0) == (m_counted > 0))
    if q >= CALL_P and frac >= CALL_FRAC and w >= 0.85 and counted_agrees:
        return f"called {lead} (tracker)"
    if q >= LIKELY_P:
        return f"likely {lead}"
    if q >= LEAN_P:
        return f"leaning {lead}"
    return "toss-up"


# ---- damping across refresh cycles ----------------------------------------

def damp(prev, target, now_ts):
    """prev: {"mu","sd","p_dem","target_mu","swing_until"} or None.
    Returns the displayed state for this cycle."""
    if prev is None:
        return {"mu": target["mu"], "sd": target["sd"], "p_dem": target["p_dem"],
                "target_mu": target["mu"], "swing_until": 0}
    mu = prev["mu"] + DAMP * (target["mu"] - prev["mu"])
    sd = prev["sd"] + DAMP * (target["sd"] - prev["sd"])
    p = prev["p_dem"] + DAMP * (target["p_dem"] - prev["p_dem"])
    swing_until = prev.get("swing_until", 0)
    if abs(target["mu"] - prev.get("target_mu", target["mu"])) > SWING_FLAG_PTS:
        swing_until = now_ts + SWING_FLAG_SECONDS
    return {"mu": mu, "sd": sd, "p_dem": p, "target_mu": target["mu"], "swing_until": swing_until}


# ---- chamber odds -----------------------------------------------------------

def poisson_binomial(ps):
    dist = [1.0]
    for p in ps:
        nxt = [0.0] * (len(dist) + 1)
        for k, v in enumerate(dist):
            nxt[k] += v * (1.0 - p)
            nxt[k + 1] += v * p
        dist = nxt
    return dist


def chamber_odds(seat_probs, holdover_dem, holdover_rep, total_seats, majority):
    """seat_probs: list of (p_dem, p_rep) per contested seat; p_other = 1-p_dem-p_rep.
    Returns P(dem majority), P(rep majority), P(no majority), and the Dem seat
    distribution (including holdovers) with mean / 5th / 95th percentiles."""
    d_dist = poisson_binomial([clip(p[0], 0.0, 1.0) for p in seat_probs])
    r_dist = poisson_binomial([clip(p[1], 0.0, 1.0) for p in seat_probs])
    p_dem_maj = sum(v for k, v in enumerate(d_dist) if k + holdover_dem >= majority)
    p_rep_maj = sum(v for k, v in enumerate(r_dist) if k + holdover_rep >= majority)
    p_none = max(0.0, 1.0 - p_dem_maj - p_rep_maj)
    mean = sum(k * v for k, v in enumerate(d_dist)) + holdover_dem
    cum, p05, p95 = 0.0, None, None
    for k, v in enumerate(d_dist):
        cum += v
        if p05 is None and cum >= 0.05:
            p05 = k + holdover_dem
        if p95 is None and cum >= 0.95:
            p95 = k + holdover_dem
    return {"p_dem_majority": p_dem_maj, "p_rep_majority": p_rep_maj, "p_no_majority": p_none,
            "dem_seats_mean": mean, "dem_seats_p05": p05, "dem_seats_p95": p95,
            "dem_dist": [{"seats": k + holdover_dem, "p": v} for k, v in enumerate(d_dist)],
            "holdover_dem": holdover_dem, "holdover_rep": holdover_rep,
            "total_seats": total_seats, "majority": majority}
