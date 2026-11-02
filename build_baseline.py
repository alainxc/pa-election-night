#!/usr/bin/env python3
"""
build_baseline.py - expected votes by seat x county x vote method, plus the
matched 2-party baseline share, for the election-night needle.

Two targets:
  --target 2026  (default) reference race = 2024 President (precinct file
                 erstat_2024_g), method shares = 2024 county API data,
                 expected turnout = 2022 Governor / 2024 President by county
                 and method (a midterm looks like 2022, not 2024).
  --target 2024  the replay. reference race = 2022 Governor (precinct file
                 ElectionReturns_2022_General), method shares = 2022 county
                 API data, expected turnout = 2020 President / 2022 Governor
                 by county and method. Nothing from 2024 is used.

Output: docs/data/baseline_<target>.json
  seats[S46].counties[WASHINGTON] = {
     exp:   {ed, mail, prov}        expected 2-party+other votes this cycle
     share: {ed, mail, prov}        reference Dem share of 2-party vote
     ref:   {ed:{dem,rep,oth}, ...} reference votes (district slice x county method mix)
  }
Every number is derived from the public files named above; nothing is typed in.
"""
import argparse
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
INPUTS = os.path.join(HERE, "forecast-inputs")
BASE_DIR = os.path.join(HERE, "data", "baseline")
OUT_DIR = os.path.join(HERE, "docs", "data")

# PA DoS county codes (erstat readme) -> site county names (upper case)
COUNTY_CODES = {
    1: "ADAMS", 2: "ALLEGHENY", 3: "ARMSTRONG", 4: "BEAVER", 5: "BEDFORD", 6: "BERKS", 7: "BLAIR",
    8: "BRADFORD", 9: "BUCKS", 10: "BUTLER", 11: "CAMBRIA", 12: "CAMERON", 13: "CARBON", 14: "CENTRE",
    15: "CHESTER", 16: "CLARION", 17: "CLEARFIELD", 18: "CLINTON", 19: "COLUMBIA", 20: "CRAWFORD",
    21: "CUMBERLAND", 22: "DAUPHIN", 23: "DELAWARE", 24: "ELK", 25: "ERIE", 26: "FAYETTE", 27: "FOREST",
    28: "FRANKLIN", 29: "FULTON", 30: "GREENE", 31: "HUNTINGDON", 32: "INDIANA", 33: "JEFFERSON",
    34: "JUNIATA", 35: "LACKAWANNA", 36: "LANCASTER", 37: "LAWRENCE", 38: "LEBANON", 39: "LEHIGH",
    40: "LUZERNE", 41: "LYCOMING", 42: "MCKEAN", 43: "MERCER", 44: "MIFFLIN", 45: "MONROE",
    46: "MONTGOMERY", 47: "MONTOUR", 48: "NORTHAMPTON", 49: "NORTHUMBERLAND", 50: "PERRY",
    51: "PHILADELPHIA", 52: "PIKE", 53: "POTTER", 54: "SCHUYLKILL", 55: "SNYDER", 56: "SOMERSET",
    57: "SULLIVAN", 58: "SUSQUEHANNA", 59: "TIOGA", 60: "UNION", 61: "VENANGO", 62: "WARREN",
    63: "WASHINGTON", 64: "WAYNE", 65: "WESTMORELAND", 66: "WYOMING", 67: "YORK",
}

METHODS = ("ed", "mail", "prov")
FIELD = {"ed": "ElectionDayVotes", "mail": "MailInVotes", "prov": "ProvisionalVotes"}


def party_bucket(p):
    return "dem" if p == "DEM" else "rep" if p == "REP" else "oth"


def county_methods_from_api(path):
    """county -> party -> method -> votes, from a saved GetCountyBreak payload
    for a statewide race."""
    d = json.load(open(path))
    out = {}
    for _dist, lst in d["Election"].items():
        for blk in lst:
            for county, rows in blk.items():
                c = out.setdefault(county.upper(), {"dem": {m: 0 for m in METHODS},
                                                    "rep": {m: 0 for m in METHODS},
                                                    "oth": {m: 0 for m in METHODS}})
                for r in rows:
                    pb = party_bucket(r["PartyName"])
                    if r.get("ShowVotesBreakdown") != "1":
                        raise SystemExit(f"no method breakdown in {path} for {county}")
                    for m in METHODS:
                        c[pb][m] += int(str(r[FIELD[m]]).replace(",", "") or 0)
    return out


def precinct_aggregate(path, office):
    """(chamber, district, county) -> party -> votes for one office code."""
    agg = {}
    with open(path, newline="", encoding="latin-1") as fh:
        for row in csv.reader(fh):
            if row[8] != office:
                continue
            votes = int(row[15] or 0)
            county = COUNTY_CODES[int(row[2])]
            pb = party_bucket(row[9])
            sd, hd = int(row[19] or 0), int(row[20] or 0)
            for chamber, dist in (("senate", sd), ("house", hd)):
                if dist <= 0:
                    continue
                k = (chamber, dist, county)
                a = agg.setdefault(k, {"dem": 0, "rep": 0, "oth": 0})
                a[pb] += votes
    return agg


def build(target):
    if target == 2026:
        ref_file = os.path.join(INPUTS, "raw-returns", "pados", "erstat_2024_g_268768_20250129.txt")
        ref_office, ref_label = "USP", "2024 President"
        ref_cty = county_methods_from_api(os.path.join(BASE_DIR, "president_2024_by_county_raw.json"))
        tgt_cty = county_methods_from_api(os.path.join(BASE_DIR, "governor_2022_by_county_raw.json"))
        tgt_label = "2022 Governor"
        seat_prefix = {"senate": "S", "house": "H"}
        method_specific_ratio = True
    elif target == 2024:
        ref_file = os.path.join(INPUTS, "raw-returns", "pados", "ElectionReturns_2022_General_PrecinctReturns.txt")
        ref_office, ref_label = "GOV", "2022 Governor"
        ref_cty = county_methods_from_api(os.path.join(BASE_DIR, "governor_2022_by_county_raw.json"))
        tgt_cty = county_methods_from_api(os.path.join(BASE_DIR, "president_2020_by_county_raw.json"))
        tgt_label = "2020 President"
        seat_prefix = {"senate": "S", "house": "H"}
        method_specific_ratio = False
    else:
        sys.exit("target must be 2026 or 2024")

    agg = precinct_aggregate(ref_file, ref_office)

    # county-wide method mix per party in the reference race, and the
    # method-specific turnout ratio target-like-year / reference-year
    mix, ratio, county_tot = {}, {}, {}
    for county, parties in ref_cty.items():
        mix[county] = {}
        for pb in ("dem", "rep", "oth"):
            tot = sum(parties[pb].values())
            mix[county][pb] = {m: (parties[pb][m] / tot if tot else 0.0) for m in METHODS}
        ref_m = {m: sum(parties[pb][m] for pb in parties) for m in METHODS}
        tgt_m = {m: sum(tgt_cty[county][pb][m] for pb in tgt_cty[county]) for m in METHODS}
        if method_specific_ratio:
            ratio[county] = {m: (tgt_m[m] / ref_m[m] if ref_m[m] else 0.0) for m in METHODS}
        else:
            # one ratio for all methods: keeps the reference election's method
            # mix (used for the 2024 replay, where the only presidential-year
            # comparison is pandemic 2020 and its mail share is not a usable proxy)
            r_all = sum(tgt_m.values()) / sum(ref_m.values()) if sum(ref_m.values()) else 0.0
            ratio[county] = {m: r_all for m in METHODS}
        county_tot[county] = {"ref": ref_m, "target_like": tgt_m}

    seats = {}
    for (chamber, dist, county), votes in agg.items():
        key = f"{seat_prefix[chamber]}{dist}"
        s = seats.setdefault(key, {"chamber": chamber, "district": dist, "counties": {}})
        ref = {m: {pb: votes[pb] * mix[county][pb][m] for pb in ("dem", "rep", "oth")} for m in METHODS}
        exp = {m: sum(ref[m].values()) * ratio[county][m] for m in METHODS}
        share = {}
        for m in METHODS:
            two = ref[m]["dem"] + ref[m]["rep"]
            share[m] = ref[m]["dem"] / two if two else None
        s["counties"][county] = {
            "exp": {m: round(exp[m], 1) for m in METHODS},
            "share": {m: (round(share[m], 5) if share[m] is not None else None) for m in METHODS},
            "ref": {m: {pb: round(ref[m][pb], 1) for pb in ref[m]} for m in METHODS},
        }
    for s in seats.values():
        s["exp_total"] = round(sum(sum(c["exp"].values()) for c in s["counties"].values()), 1)
        ref_d = sum(c["ref"][m]["dem"] for c in s["counties"].values() for m in METHODS)
        ref_r = sum(c["ref"][m]["rep"] for c in s["counties"].values() for m in METHODS)
        s["ref_margin"] = round(100 * (ref_d - ref_r) / (ref_d + ref_r), 3) if ref_d + ref_r else None

    out = {
        "target": target,
        "reference_race": ref_label,
        "turnout_scaling": f"{tgt_label} / {ref_label}, by county" + (" and vote method" if method_specific_ratio else " (all methods)"),
        "county_mail_ref": {c: county_tot[c]["ref"]["mail"] for c in county_tot},
        "method_mix_source": f"{ref_label} county results by method (electionreturns.pa.gov)",
        "county_totals": county_tot,
        "county_turnout_ratio": ratio,
        "seats": dict(sorted(seats.items(), key=lambda kv: (kv[1]["chamber"], kv[1]["district"]))),
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"baseline_{target}.json")
    json.dump(out, open(path, "w"), separators=(",", ":"))
    n_s = sum(1 for s in seats.values() if s["chamber"] == "senate")
    n_h = sum(1 for s in seats.values() if s["chamber"] == "house")
    print(f"wrote {path}: {n_s} senate + {n_h} house districts, {len(ratio)} counties, ref {ref_label}")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=2026)
    a = ap.parse_args()
    build(a.target)
