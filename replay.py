#!/usr/bin/env python3
"""
replay.py - dry run of the needle on the 2024 general, county by county.

Takes the real 2024 final returns per seat x county x method
(data/validation_2024_general.json, pulled from electionreturns.pa.gov), the
2024-replay baseline (built only from 2020/2022 data) and the reconstructed
2024 priors, and releases the vote in a SCENARIO of county reporting order:

  big_cities   (Philadelphia, Allegheny): in-person fast, mail late
  collar       (Montgomery, Chester, Bucks, Delaware): mail first, in-person later
  everyone else: mixed; provisionals last everywhere

The order is an assumption about how PA counties tend to report, chosen to
stress the needle with the mail-first skew. It is not a record of what
happened in 2024. Every vote count released is a fraction of the real final.

Outputs
  data/replay/snapshots/NNN.json   returns.json-shaped snapshots (for poll.py --replay)
  data/replay/replay_2024.json     estimate timeline per seat + chamber odds
  data/replay/replay_2024.png      chart of estimate over time for selected seats
"""
import argparse
import copy
import datetime as dt
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import needle  # noqa: E402

GROUPS = {
    "big_cities": {"PHILADELPHIA", "ALLEGHENY"},
    "collar": {"MONTGOMERY", "CHESTER", "BUCKS", "DELAWARE"},
}
# curves: minutes after 8 pm -> fraction counted, piecewise linear
CURVES = {
    "big_cities": {"ed": [(0, 0), (30, 0.2), (120, 0.7), (240, 0.95), (420, 1.0)],
                   "mail": [(0, 0), (120, 0.0), (180, 0.25), (240, 0.5), (420, 0.95), (600, 1.0)],
                   "prov": [(0, 0), (300, 0.0), (420, 0.3), (600, 1.0)]},
    "collar": {"ed": [(0, 0), (60, 0.1), (120, 0.5), (180, 0.9), (300, 1.0)],
               "mail": [(0, 0), (15, 0.7), (90, 0.95), (240, 1.0)],
               "prov": [(0, 0), (300, 0.0), (420, 0.3), (600, 1.0)]},
    "mixed": {"ed": [(0, 0), (45, 0.3), (120, 0.7), (180, 0.9), (300, 1.0)],
              "mail": [(0, 0), (60, 0.4), (120, 0.7), (180, 0.9), (300, 1.0)],
              "prov": [(0, 0), (300, 0.0), (420, 0.3), (600, 1.0)]},
}
METHOD_FIELD = {"ed": "election_day_votes", "mail": "mail_in_votes", "prov": "provisional_votes"}


def group_of(county):
    for g, s in GROUPS.items():
        if county in s:
            return g
    return "mixed"


def curve(points, t):
    return needle.interp(points, t)


def released_row(final_row, t):
    """Scale each candidate's per-method votes by the county's curve at time t."""
    g = group_of(final_row["county"])
    row = copy.deepcopy(final_row)
    for c in row["candidates"]:
        total = 0
        for m, f in METHOD_FIELD.items():
            v = c.get(f)
            if v is None:
                continue
            rel = int(math.floor(v * curve(CURVES[g][m], t)))
            c[f] = rel
            total += rel
        c["votes"] = total
    # party sums
    for side, key in (("dem_votes", "DEM"), ("rep_votes", "REP")):
        row[side] = sum(c["votes"] for c in row["candidates"] if c["party"] == key)
    row["other_votes"] = sum(c["votes"] for c in row["candidates"] if c["party"] not in ("DEM", "REP")) or None
    row["total_votes"] = sum(c["votes"] for c in row["candidates"])
    ed_frac = curve(CURVES[g]["ed"], t)
    if row.get("precincts_total"):
        row["precincts_reporting"] = int(round(row["precincts_total"] * ed_frac))
        row["pct_precincts_reporting"] = round(100.0 * row["precincts_reporting"] / row["precincts_total"], 2)
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", type=int, default=3, help="minutes between snapshots")
    ap.add_argument("--hours", type=float, default=7.0)
    ap.add_argument("--out", default=os.path.join(HERE, "data", "replay"))
    ap.add_argument("--seats", default="S37,S45,S49,S5,S15,S9")
    a = ap.parse_args()

    final = json.load(open(os.path.join(HERE, "data", "validation_2024_general.json")))
    base = json.load(open(os.path.join(HERE, "docs", "data", "baseline_2024.json")))
    pri = json.load(open(os.path.join(HERE, "docs", "data", "priors_2024.json")))
    snaps_dir = os.path.join(a.out, "snapshots")
    os.makedirs(snaps_dir, exist_ok=True)
    for f in os.listdir(snaps_dir):
        os.remove(os.path.join(snaps_dir, f))

    seats_up = pri["chambers"]["senate"]["seats_up"]
    t0 = dt.datetime(2024, 11, 5, 20, 0, tzinfo=dt.timezone(dt.timedelta(hours=-5)))
    timeline = []
    display = {}
    n = 0
    t = 0
    while t <= a.hours * 60:
        now = t0 + dt.timedelta(minutes=t)
        snap = copy.deepcopy(final)
        snap["rows"] = [released_row(r, t) for r in final["rows"]]
        snap["meta"]["fetched_at_utc"] = now.astimezone(dt.timezone.utc).isoformat(timespec="seconds")
        snap["meta"]["replay_minutes_after_8pm"] = t
        for r in snap["rows"]:
            r["fetched_at"] = snap["meta"]["fetched_at_utc"]
        json.dump(snap, open(os.path.join(snaps_dir, f"{n:03d}.json"), "w"))

        point = {"t": t, "time": now.strftime("%H:%M"), "seats": {}}
        probs = []
        for key in seats_up:
            p = pri["seats"][key]
            b = base["seats"].get(key)
            rows = [r for r in snap["rows"] if f"S{r['seat']}" == key]
            est = needle.estimate_seat({"mu": p["mu"], "sd": p["sd"], "fixed_prob": p["fixed_prob"],
                                        "dem_side": p["dem_side"], "ref_margin": p["ref_margin"]}, b, rows)
            display[key] = needle.damp(display.get(key), est, now.timestamp())
            d = display[key]
            point["seats"][key] = {"mu": est["mu"], "sd": est["sd"], "p": est["p_dem"], "frac": est["frac"],
                                   "disp_mu": d["mu"], "disp_p": d["p_dem"], "status": est["status"],
                                   "m_ret": est["m_ret"], "swing": est["pooled_swing"]}
            probs.append((d["p_dem"], 0.0 if est["fixed"] and d["p_dem"] > 0.5 else (1 - d["p_dem"]) if not est["fixed"] else 1 - d["p_dem"]))
        ch = pri["chambers"]["senate"]
        odds = needle.chamber_odds(probs, ch["holdover_dem"], ch["holdover_rep"], ch["total_seats"], ch["majority"])
        point["chamber"] = {k: odds[k] for k in ("p_dem_majority", "p_rep_majority", "p_no_majority",
                                                  "dem_seats_mean", "dem_seats_p05", "dem_seats_p95")}
        timeline.append(point)
        n += 1
        t += a.step

    # actual results for comparison
    actual = {}
    for key in seats_up:
        p = pri["seats"][key]["actual_2024"]
        two = p["dem"] + p["rep"]
        actual[key] = 100.0 * (p["dem"] - p["rep"]) / two if two else None
    out = {"scenario": {"groups": {g: sorted(s) for g, s in GROUPS.items()}, "curves": CURVES,
                        "note": "assumed reporting order, not a record of 2024"},
           "actual_margin": actual, "timeline": timeline}
    json.dump(out, open(os.path.join(a.out, "replay_2024.json"), "w"))

    # convergence report
    print(f"{len(timeline)} snapshots; final-step errors (estimate - actual), pts:")
    worst_swing = {}
    for key in seats_up:
        if actual[key] is None or pri["seats"][key]["fixed_prob"] is not None:
            continue
        mus = [pt["seats"][key]["disp_mu"] for pt in timeline]
        err = mus[-1] - actual[key]
        jumps = max(abs(b - a_) for a_, b in zip(mus, mus[1:]))
        early = [pt for pt in timeline if pt["t"] <= 120]
        early_err = max(abs(pt["seats"][key]["disp_mu"] - actual[key]) for pt in early)
        wrong_calls = sum(1 for pt in timeline if pt["seats"][key]["status"].startswith("called") and
                          ((pt["seats"][key]["mu"] > 0) != (actual[key] > 0)))
        worst_swing[key] = jumps
        print(f"  {key:4s} prior {pri['seats'][key]['mu']:6.1f}  final est {mus[-1]:6.1f}  actual {actual[key]:6.1f}"
              f"  err {err:5.1f}  max 1-cycle move {jumps:4.1f}  max err by 10pm {early_err:5.1f}  wrong calls {wrong_calls}")
    last = timeline[-1]["chamber"]
    print("chamber at end:", {k: round(v, 3) for k, v in last.items()})

    # chart
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed; skipping chart")
        return
    sel = [s for s in a.seats.split(",") if s in seats_up]
    fig, axes = plt.subplots(len(sel) + 1, 1, figsize=(10, 2.6 * (len(sel) + 1)), sharex=True)
    ts = [pt["t"] / 60.0 for pt in timeline]
    for ax, key in zip(axes, sel):
        mu = [pt["seats"][key]["disp_mu"] for pt in timeline]
        raw = [pt["seats"][key]["mu"] for pt in timeline]
        sd = [pt["seats"][key]["sd"] for pt in timeline]
        naive = []
        for pt, s in zip(timeline, [None] * len(timeline)):
            naive.append(None)
        ax.fill_between(ts, [m - 1.645 * s for m, s in zip(raw, sd)], [m + 1.645 * s for m, s in zip(raw, sd)],
                        color="#9cc0f0", alpha=0.35, label="90% range")
        ax.plot(ts, raw, color="#2a6fdb", lw=1, alpha=0.6, label="computed")
        ax.plot(ts, mu, color="#1b4ea3", lw=2, label="displayed (damped)")
        ax.axhline(actual[key], color="#d9402b", ls="--", lw=1.2, label="actual 2024")
        ax.axhline(pri["seats"][key]["mu"], color="#888", ls=":", lw=1, label="prior")
        ax.axhline(0, color="#000", lw=0.5)
        ax.set_ylabel(f"{key}\nDem margin")
        fr = [pt["seats"][key]["frac"] for pt in timeline]
        ax2 = ax.twinx()
        ax2.plot(ts, [100 * f for f in fr], color="#999", lw=0.8)
        ax2.set_ylim(0, 105)
        ax2.set_ylabel("% in", color="#999")
        if ax is axes[0]:
            ax.legend(loc="upper right", fontsize=7, ncol=5)
    ax = axes[-1]
    ax.plot(ts, [pt["chamber"]["p_dem_majority"] for pt in timeline], color="#2a6fdb", label="P(Dem majority)")
    ax.plot(ts, [pt["chamber"]["p_rep_majority"] for pt in timeline], color="#d9402b", label="P(Rep majority)")
    ax.plot(ts, [pt["chamber"]["p_no_majority"] for pt in timeline], color="#b9a7d6", label="P(no majority)")
    ax.set_ylim(0, 1)
    ax.set_ylabel("chamber odds")
    ax.set_xlabel("hours after 8 pm (assumed reporting order; 2024 odd Senate seats)")
    ax.legend(fontsize=7, ncol=3)
    fig.suptitle("2024 replay: needle estimate vs actual, county by county", fontsize=11)
    fig.tight_layout()
    png = os.path.join(a.out, "replay_2024.png")
    fig.savefig(png, dpi=130)
    print("chart:", png)


if __name__ == "__main__":
    main()
