"""Unit tests for needle.py. Run: .venv/bin/python -m pytest -q  (or python -m unittest)"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import needle  # noqa: E402


def mk_baseline():
    # two counties: BIG (mail-heavy, Dem) and SMALL (ED-heavy, Rep)
    return {
        "exp_total": 100000.0,
        "counties": {
            "BIG": {"exp": {"ed": 40000.0, "mail": 28000.0, "prov": 2000.0},
                    "share": {"ed": 0.55, "mail": 0.75, "prov": 0.60}},
            "SMALL": {"exp": {"ed": 24000.0, "mail": 5500.0, "prov": 500.0},
                      "share": {"ed": 0.30, "mail": 0.50, "prov": 0.35}},
        },
    }


def row(county, d_ed, r_ed, d_mail, r_mail, d_prov=0, r_prov=0, split=True, pr=None, pt=None):
    cands = []
    for party, ed, mail, prov in (("DEM", d_ed, d_mail, d_prov), ("REP", r_ed, r_mail, r_prov)):
        c = {"party": party, "votes": ed + mail + prov}
        if split:
            c.update({"election_day_votes": ed, "mail_in_votes": mail, "provisional_votes": prov})
        else:
            c.update({"election_day_votes": None, "mail_in_votes": None, "provisional_votes": None})
        cands.append(c)
    return {"county": county, "candidates": cands, "precincts_reporting": pr, "precincts_total": pt}


# Reference district margin implied by the baseline (two-party, pts)
def ref_margin(b):
    d = r = 0.0
    for c in b["counties"].values():
        for m in needle.METHODS:
            d += c["exp"][m] * c["share"][m]
            r += c["exp"][m] * (1 - c["share"][m])
    return 100 * (d - r) / (d + r)


class TestNeedle(unittest.TestCase):
    def setUp(self):
        self.b = mk_baseline()
        self.ref = ref_margin(self.b)
        self.prior = {"mu": self.ref + 3.0, "sd": 6.0, "fixed_prob": None, "dem_side": "DEM",
                      "ref_margin": self.ref}

    def test_no_returns_is_prior(self):
        e = needle.estimate_seat(self.prior, self.b, [])
        self.assertEqual(e["status"], "no returns yet")
        self.assertAlmostEqual(e["mu"], self.prior["mu"])
        self.assertAlmostEqual(e["sd"], 6.0)
        self.assertEqual(e["w_returns"], 0.0)

    def test_full_count_is_counted_margin(self):
        rows = [row("BIG", 22000, 18000, 21000, 7000, 1200, 800),
                row("SMALL", 7200, 16800, 2750, 2750, 175, 325)]
        e = needle.estimate_seat(self.prior, self.b, rows)
        d = 22000 + 21000 + 1200 + 7200 + 2750 + 175
        r = 18000 + 7000 + 800 + 16800 + 2750 + 325
        m = 100 * (d - r) / (d + r)
        self.assertAlmostEqual(e["frac"], 1.0)
        self.assertAlmostEqual(e["m_ret"], m, places=6)
        self.assertAlmostEqual(e["mu"], m, places=3)        # cap is 1.0 at 100%
        self.assertLessEqual(e["sd"], 1.0)
        self.assertIn("called", e["status"])

    def test_early_mail_skew_does_not_lurch(self):
        # Only BIG's mail is in (very Dem). A naive share would show D+50.
        rows = [row("BIG", 0, 0, 21000, 7000, 0, 0, pr=0, pt=500)]
        e = needle.estimate_seat(self.prior, self.b, rows)
        naive = 100 * (21000 - 7000) / 28000
        self.assertGreater(naive, 45)
        # matched benchmark: BIG mail reference share is 0.75 -> observed 0.75 -> zero swing
        self.assertAlmostEqual(e["pooled_swing"], 3.0 * (needle.PRIOR_SWING_PSEUDO * e["exp_total"]) /
                               (needle.PRIOR_SWING_PSEUDO * e["exp_total"] + 28000), places=3)
        # estimate stays within a few points of the prior, far from the naive number
        self.assertLess(abs(e["mu"] - self.prior["mu"]), 4.0)
        self.assertLess(e["w_returns"], 0.40)
        self.assertGreaterEqual(e["sd"], needle.EARLY_FLOOR)

    def test_swing_is_applied_to_outstanding(self):
        # BIG mail comes in 10 pts (margin) more Republican than 2024: share 0.70 instead of 0.75
        rows = [row("BIG", 0, 0, 19600, 8400)]
        e = needle.estimate_seat(self.prior, self.b, rows)
        self.assertLess(e["pooled_swing"], 0.0)
        self.assertLess(e["m_ret"], self.ref)   # returns-only estimate below the benchmark

    def test_unmatched_county_downweighted(self):
        rows_split = [row("SMALL", 6000, 14000, 2000, 2000)]
        rows_nosplit = [row("SMALL", 6000, 14000, 2000, 2000, split=False)]
        e1 = needle.estimate_seat(self.prior, self.b, rows_split)
        e2 = needle.estimate_seat(self.prior, self.b, rows_nosplit)
        self.assertGreater(e2["sd_ret"], e1["sd_ret"])
        self.assertEqual(e2["unmatched_share"], 1.0)

    def test_zero_precinct_county_inflates(self):
        rows = [row("SMALL", 7200, 16800, 2750, 2750, 175, 325, pr=120, pt=120),
                row("BIG", 0, 0, 0, 0, pr=0, pt=900)]
        e = needle.estimate_seat(self.prior, self.b, rows)
        self.assertGreater(e["zero_precinct_share"], 0.6)
        self.assertGreater(e["sd_ret"], 6.0)

    def test_cap_schedule(self):
        self.assertAlmostEqual(needle.interp(needle.CAP_POINTS, 0.0), 0.10)
        self.assertAlmostEqual(needle.interp(needle.CAP_POINTS, 0.25), 0.35)
        self.assertAlmostEqual(needle.interp(needle.CAP_POINTS, 0.60), 0.85)
        self.assertAlmostEqual(needle.interp(needle.CAP_POINTS, 1.0), 1.0)
        # below 25 % counted the prior has at least 65 % of the weight
        rows = [row("SMALL", 3600, 8400, 1375, 1375)]  # ~15 % of expected
        e = needle.estimate_seat(self.prior, self.b, rows)
        self.assertLess(e["frac"], 0.25)
        self.assertLessEqual(e["w_returns"], 0.35)

    def test_fixed_seat(self):
        p = dict(self.prior, fixed_prob=0.995)
        e = needle.estimate_seat(p, self.b, [])
        self.assertEqual(e["status"], "fixed")
        self.assertEqual(e["p_dem"], 0.995)

    def test_never_call_on_prior(self):
        p = dict(self.prior, mu=40.0, sd=3.0)
        e = needle.estimate_seat(p, self.b, [])
        self.assertEqual(e["status"], "no returns yet")
        rows = [row("SMALL", 3600, 8400, 1375, 1375)]
        e = needle.estimate_seat(p, self.b, rows)
        self.assertNotIn("called", e["status"])

    def test_damping(self):
        s0 = needle.damp(None, {"mu": 0.0, "sd": 6.0, "p_dem": 0.5}, 1000)
        s1 = needle.damp(s0, {"mu": 10.0, "sd": 4.0, "p_dem": 0.9}, 1180)
        self.assertAlmostEqual(s1["mu"], 5.0)
        self.assertGreater(s1["swing_until"], 1180)       # > 5 pt move flags
        s2 = needle.damp(s1, {"mu": 10.0, "sd": 4.0, "p_dem": 0.9}, 1360)
        self.assertAlmostEqual(s2["mu"], 7.5)
        self.assertEqual(s2["swing_until"], s1["swing_until"])  # no new flag

    def test_poisson_binomial(self):
        d = needle.poisson_binomial([0.5, 0.5])
        self.assertAlmostEqual(sum(d), 1.0)
        self.assertAlmostEqual(d[1], 0.5)
        ch = needle.chamber_odds([(1.0, 0.0)] * 16 + [(0.0, 1.0)] * 9, 10, 15, 50, 26)
        self.assertAlmostEqual(ch["p_dem_majority"], 1.0)
        self.assertAlmostEqual(ch["p_rep_majority"], 0.0)
        ch = needle.chamber_odds([(0.5, 0.5)] * 25, 10, 15, 50, 26)
        self.assertAlmostEqual(ch["p_dem_majority"] + ch["p_rep_majority"] + ch["p_no_majority"], 1.0)
        self.assertAlmostEqual(ch["dem_seats_mean"], 22.5)
        # an independent-side seat counts for neither party
        ch = needle.chamber_odds([(0.0, 0.0)] + [(1.0, 0.0)] * 15 + [(0.0, 1.0)] * 9, 10, 15, 50, 26)
        self.assertAlmostEqual(ch["p_no_majority"], 1.0)


if __name__ == "__main__":
    unittest.main()
