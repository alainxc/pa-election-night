"""Parity test: needle.py vs docs/assets/needle.js on the same fixtures, run with
macOS JavaScriptCore (jsc). Fixtures are the real 2026 baseline/priors plus
synthetic returns at several stages, and the unit-test fixtures."""
import json
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import needle  # noqa: E402

JSC = "/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc"


def synth_rows(baseline_seat, frac_by_method, swing_share=0.0, drop_split=()):
    rows = []
    for cname, cb in baseline_seat["counties"].items():
        cands = {"DEM": {}, "REP": {}}
        for m in needle.METHODS:
            f = frac_by_method.get((cname, m), frac_by_method.get(m, 0.0))
            tot = int(cb["exp"][m] * f)
            s = (cb["share"][m] or 0.5) + swing_share
            cands["DEM"][m] = int(round(tot * s))
            cands["REP"][m] = tot - cands["DEM"][m]
        clist = []
        for party in ("DEM", "REP"):
            c = {"party": party, "votes": sum(cands[party].values())}
            if cname in drop_split:
                c.update({"election_day_votes": None, "mail_in_votes": None, "provisional_votes": None})
            else:
                c.update({"election_day_votes": cands[party]["ed"], "mail_in_votes": cands[party]["mail"],
                          "provisional_votes": cands[party]["prov"]})
            clist.append(c)
        pr = int(100 * frac_by_method.get((cname, "ed"), frac_by_method.get("ed", 0.0)))
        rows.append({"county": cname, "candidates": clist, "precincts_reporting": pr, "precincts_total": 100})
    return rows


class TestParity(unittest.TestCase):
    def test_parity(self):
        if not os.path.exists(JSC):
            self.skipTest("jsc not available")
        base = json.load(open(os.path.join(ROOT, "docs", "data", "baseline_2026.json")))
        pri = json.load(open(os.path.join(ROOT, "docs", "data", "priors_2026.json")))
        cases = []
        for key in ("S46", "S24", "S6", "S36", "S2"):
            b, p = base["seats"][key], pri["seats"][key]
            prior = {"mu": p["mu"], "sd": p["sd"], "fixed_prob": p["fixed_prob"], "dem_side": p["dem_side"],
                     "ref_margin": p["ref_margin"]}
            for fr, sw in (({}, 0.0), ({"mail": 0.6}, 0.0), ({"ed": 0.3, "mail": 0.9, "prov": 0.0}, -0.03),
                           ({"ed": 1.0, "mail": 1.0, "prov": 1.0}, 0.02), ({"ed": 0.5, "mail": 0.5}, 0.01)):
                rows = synth_rows(b, fr, sw, drop_split=(list(b["counties"])[0],) if sw == 0.01 else ())
                extra = {"mail_requested": {c: int(b["counties"][c]["exp"]["mail"] * 1.6) for c in b["counties"]}} if sw == -0.03 else {}
                cases.append({"prior": prior, "baseline": b, "rows": rows, "extra": extra})
        py = [needle.estimate_seat(c["prior"], c["baseline"], c["rows"], c["extra"]) for c in cases]
        probs = [(e["p_dem"], 0.0 if e["fixed"] else 1 - e["p_dem"]) for e in py]
        py_ch = needle.chamber_odds(probs, 10, 15, 50, 26)
        py_damp = needle.damp(needle.damp(None, py[0], 0), py[1], 180)

        fixture = os.path.join(HERE, "_parity_fixture.json")
        json.dump({"cases": cases, "probs": probs}, open(fixture, "w"))
        js = f"""
        load({json.dumps(os.path.join(ROOT, 'docs', 'assets', 'needle.js'))});
        const fx = JSON.parse(readFile({json.dumps(fixture)}));
        const out = fx.cases.map(c => Needle.estimateSeat(c.prior, c.baseline, c.rows, c.extra));
        const ch = Needle.chamberOdds(fx.probs, 10, 15, 50, 26);
        const d = Needle.damp(Needle.damp(null, out[0], 0), out[1], 180);
        print(JSON.stringify({{out, ch, d}}));
        """
        res = subprocess.run([JSC, "-e", js], capture_output=True, text=True, timeout=60)
        self.assertEqual(res.returncode, 0, res.stderr)
        got = json.loads(res.stdout.strip().splitlines()[-1])
        os.remove(fixture)
        keys = ("mu", "sd", "p_dem", "frac", "frac_matched", "m_ret", "sd_ret", "pooled_swing", "w_returns",
                "zero_precinct_share", "unmatched_share", "mail_gap_share", "lo", "hi")
        for i, (a, b) in enumerate(zip(py, got["out"])):
            for k in keys:
                av, bv = a[k], b[k]
                if av is None or bv is None:
                    self.assertEqual(av, bv, f"case {i} {k}")
                else:
                    self.assertAlmostEqual(av, bv, places=9, msg=f"case {i} {k}: py {av} js {bv}")
            self.assertEqual(a["status"], b["status"], f"case {i} status")
        for k in ("p_dem_majority", "p_rep_majority", "p_no_majority", "dem_seats_mean"):
            self.assertAlmostEqual(py_ch[k], got["ch"][k], places=9, msg=k)
        self.assertEqual(py_ch["dem_seats_p05"], got["ch"]["dem_seats_p05"])
        for k in ("mu", "sd", "p_dem", "swing_until"):
            self.assertAlmostEqual(py_damp[k], got["d"][k], places=9, msg=k)


if __name__ == "__main__":
    unittest.main()
