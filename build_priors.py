#!/usr/bin/env python3
"""
build_priors.py - per-seat priors and chamber settings for the needle.

--target 2026 (default): straight from forecast-inputs/forecast-output/forecast.json
   (mu, sd_district, fixed_prob, candidates, holder party, region) plus the
   chamber holdovers and majority thresholds from the same file.

--target 2024 (replay): the forecast was not run for the odd Senate seats in
   2024, so a prior is RECONSTRUCTED with the forecast's own recipe using only
   information available before election day 2024: district lean from the
   pre-2024 reference races in processed/district_presidential_baseline.csv
   (pres 2020, gov 2022, US Sen 2022, with the config's weights renormalised),
   the 2024 backtest's generic legislative margin (+0.89), the config's 2.8 pt
   incumbency bonus, sd = 6.5. Uncontested seats get fixed_prob 0.995 like
   the forecast does. It is labelled "reconstructed" in the output.

Output: docs/data/priors_<target>.json
"""
import argparse
import csv
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
INPUTS = os.path.join(HERE, "forecast-inputs")
OUT_DIR = os.path.join(HERE, "docs", "data")


def load_baseline(target):
    return json.load(open(os.path.join(OUT_DIR, f"baseline_{target}.json")))


def dem_side_for(seat):
    dc = seat.get("dem_cand") or ""
    if "(Independent)" in dc:
        return "IND"
    return "DEM"


def build_2026():
    f = json.load(open(os.path.join(INPUTS, "forecast-output", "forecast.json")))
    base = load_baseline(2026)
    seats = {}
    for s in f["seats"]:
        key = s["seat"]
        b = base["seats"].get(key)
        seats[key] = {
            "seat": key, "chamber": s["chamber"], "district": s["district"], "region": s["region"],
            "holder_party": s["holder_party"],
            "dem_cand": s.get("dem_cand"), "rep_cand": s.get("rep_cand"), "other_cands": s.get("other_cands"),
            "incumbent_party": s.get("incumbent_party"), "open_seat": s.get("open_seat"),
            "uncontested": s.get("uncontested"),
            "mu": s["mu"], "sd": s["sd_district"], "fixed_prob": s.get("fixed_prob"),
            "forecast_p_dem": s["p_dem"], "forecast_rating": s["rating"],
            "forecast_margin_p05": s.get("margin_p05"), "forecast_margin_p95": s.get("margin_p95"),
            "dem_side": dem_side_for(s),
            "ref_margin": b["ref_margin"] if b else None,
            "source": "forecast.json " + f["generated_at"],
        }
    ch = f["chambers"]
    chambers = {
        "senate": {"holdover_dem": ch["senate"]["holdover_dem"], "holdover_rep": ch["senate"]["holdover_rep"],
                   "total_seats": ch["senate"]["total_seats"], "majority": ch["senate"]["majority"],
                   "forecast_p_dem_control": ch["senate"]["p_dem_control"],
                   "forecast_p_rep_control": ch["senate"]["p_rep_control"],
                   "forecast_dem_seats_mean": ch["senate"]["dem_seats_mean"],
                   "seats_up": sorted(k for k, v in seats.items() if v["chamber"] == "senate")},
        "house": {"holdover_dem": 0, "holdover_rep": 0,
                  "total_seats": ch["house"]["total_seats"], "majority": ch["house"]["majority"],
                  "forecast_p_dem_control": ch["house"]["p_dem_control"],
                  "forecast_p_rep_control": ch["house"]["p_rep_control"],
                  "forecast_dem_seats_mean": ch["house"]["dem_seats_mean"],
                  "seats_up": sorted(k for k, v in seats.items() if v["chamber"] == "house")},
    }
    return {"target": 2026, "kind": "forecast", "generated_from": f["generated_at"],
            "environment": {k: v for k, v in f["environment_distribution"].items() if k not in ("hist", "hist_bins")},
            "regional_sd_pts": 2.2, "chambers": chambers, "seats": seats}


def build_2024():
    base = load_baseline(2024)
    # statewide margins for the leans
    sw = {}
    for r in csv.DictReader(open(os.path.join(INPUTS, "processed", "actual_statewide.csv"))):
        sw[(int(r["cycle"]), r["race"])] = float(r["margin_dem"])
    weights = {"gov2022": 0.20, "ussen2022": 0.20, "pres2020": 0.15}
    swm = {"gov2022": sw[(2022, "pa_governor")], "ussen2022": sw[(2022, "pa_us_senate")],
           "pres2020": sw[(2020, "pa_president")]}
    generic_2024 = 0.8925715486948725  # backtest.json 2024 environment.generic_legislative_margin
    regions = {(r["chamber"], int(r["district"])): r["region"]
               for r in csv.DictReader(open(os.path.join(INPUTS, "processed", "district_regions.csv")))}
    leg = {}
    for r in csv.DictReader(open(os.path.join(INPUTS, "processed", "legislative_results.csv"))):
        leg[(int(r["year"]), r["chamber"], int(r["district"]))] = r
    pres = {}
    for r in csv.DictReader(open(os.path.join(INPUTS, "processed", "district_presidential_baseline.csv"))):
        pres[(r["chamber"], int(r["district"]))] = r

    seats = {}
    for chamber, prefix in (("senate", "S"), ("house", "H")):
        for (y, ch, d), row in leg.items():
            if y != 2024 or ch != chamber:
                continue
            pb = pres[(chamber, d)]
            lean_num = lean_den = 0.0
            for race, w in weights.items():
                dv, rv = float(pb[f"{race}_dem_votes"] or 0), float(pb[f"{race}_rep_votes"] or 0)
                if dv + rv <= 0:
                    continue
                lean_num += w * (100 * (dv - rv) / (dv + rv) - swm[race])
                lean_den += w
            lean = lean_num / lean_den if lean_den else 0.0
            inc = row.get("incumbent_party_pre") or ""
            inc_adj = 2.8 if inc == "D" else -2.8 if inc == "R" else 0.0
            mu = lean + generic_2024 + inc_adj
            uncontested = row["uncontested"] == "True"
            fixed = None
            if uncontested:
                fixed = 0.995 if row["winner_party"] == "D" else 0.005
            key = f"{prefix}{d}"
            b = base["seats"].get(key)
            seats[key] = {
                "seat": key, "chamber": chamber, "district": d, "region": regions.get((chamber, d)),
                "holder_party": inc or None,
                "dem_cand": row["dem_cand"] or None, "rep_cand": row["rep_cand"] or None, "other_cands": None,
                "incumbent_party": inc or None, "open_seat": None, "uncontested": uncontested,
                "mu": mu, "sd": 6.5, "fixed_prob": fixed,
                "forecast_p_dem": None, "forecast_rating": "reconstructed",
                "dem_side": "DEM", "ref_margin": b["ref_margin"] if b else None,
                "actual_2024": {"dem": int(row["dem_votes"]), "rep": int(row["rep_votes"]),
                                "other": int(row["other_votes"]), "winner": row["winner_party"]},
                "source": "reconstructed from pre-2024 leans + 2024 backtest environment",
            }
    # holdovers for the 2024 replay: even Senate seats, elected 2022
    hd = sum(1 for (y, ch, d), r in leg.items() if y == 2022 and ch == "senate" and r["winner_party"] == "D")
    hr = sum(1 for (y, ch, d), r in leg.items() if y == 2022 and ch == "senate" and r["winner_party"] == "R")
    chambers = {
        "senate": {"holdover_dem": hd, "holdover_rep": hr, "total_seats": 50, "majority": 26,
                   "seats_up": sorted(k for k, v in seats.items() if v["chamber"] == "senate")},
        "house": {"holdover_dem": 0, "holdover_rep": 0, "total_seats": 203, "majority": 102,
                  "seats_up": sorted(k for k, v in seats.items() if v["chamber"] == "house")},
    }
    return {"target": 2024, "kind": "reconstructed", "generic_legislative_margin": generic_2024,
            "regional_sd_pts": 2.2, "chambers": chambers, "seats": seats}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=2026)
    a = ap.parse_args()
    out = build_2026() if a.target == 2026 else build_2024()
    os.makedirs(OUT_DIR, exist_ok=True)
    p = os.path.join(OUT_DIR, f"priors_{a.target}.json")
    json.dump(out, open(p, "w"), indent=0)
    n = len(out["seats"])
    print(f"wrote {p}: {n} seats; senate holdovers D{out['chambers']['senate']['holdover_dem']} "
          f"R{out['chambers']['senate']['holdover_rep']}")
