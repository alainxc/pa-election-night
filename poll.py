#!/usr/bin/env python3
"""
poll.py - election-night loop. Every cycle (default 3 minutes):

  1. pull a full snapshot of every contest for the office(s) from
     electionreturns.pa.gov with one persistent HTTP session (fetch_returns.build_snapshot)
  2. apply any manual overrides from data/overrides.csv (replaces the feed for a seat)
  3. write data/returns.json, data/history/<utc timestamp>.json and docs/data/returns.json
  4. run the needle (needle.py) for every seat, damp against the previous
     cycle, append to the timeline, write docs/data/estimates.json
  5. every 5th cycle, sanity-check statewide totals against the state's RSS feed
  6. git commit + push docs/data (a failed push is logged and retried next cycle)

Never crashes on a bad response: errors are logged, the cycle is skipped and
the wait grows (backoff up to 30 minutes when the state site challenges or
rate-limits us). Ctrl-C or stop.sh stops it.

Rehearsal:  python poll.py --replay data/replay/snapshots --target 2024 --interval 20 --replay-every 10
            plays the 2024 snapshots through the whole pipeline (and pushes).

Start/stop on election night: ./start.sh  and  ./stop.sh
"""
import argparse
import csv
import datetime as dt
import json
import logging
import os
import re
import subprocess
import sys
import time
from html.parser import HTMLParser

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fetch_returns as fr  # noqa: E402
import needle  # noqa: E402

DATA = os.path.join(HERE, "data")
DOCS_DATA = os.path.join(HERE, "docs", "data")
LOGS = os.path.join(HERE, "logs")
STATE_PATH = os.path.join(DATA, "state.json")
OVERRIDES_PATH = os.path.join(DATA, "overrides.csv")
OFFICES = {"senate": "STS", "house": "STH"}
TIMELINE_MAX = 600  # points kept per file (~30 hours at 3 minutes)


def log_setup():
    os.makedirs(LOGS, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(os.path.join(LOGS, "poll.log")),
                                  logging.StreamHandler(sys.stdout)])


# ---------------------------------------------------------------------------
# overrides
# ---------------------------------------------------------------------------
OVERRIDE_COLUMNS = ["seat", "county", "dem_votes", "rep_votes", "other_votes",
                    "dem_ed", "dem_mail", "dem_prov", "rep_ed", "rep_mail", "rep_prov",
                    "precincts_reporting", "precincts_total", "note"]


def ensure_overrides_template():
    if not os.path.exists(OVERRIDES_PATH):
        os.makedirs(DATA, exist_ok=True)
        with open(OVERRIDES_PATH, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(OVERRIDE_COLUMNS)
            w.writerow(["# example (delete the # to activate): S46", "WASHINGTON", 41000, 52000, 900,
                        "", "", "", "", "", "", 180, 183, "typed from county site 10:40 pm"])


def load_overrides():
    """Returns {seat_key: [rows]} in returns.json row shape. A seat present
    here replaces ALL feed rows for that seat. Method columns are optional;
    when any is blank the row carries no method split."""
    if not os.path.exists(OVERRIDES_PATH):
        return {}
    out = {}
    with open(OVERRIDES_PATH, newline="") as f:
        for r in csv.DictReader(f):
            seat = (r.get("seat") or "").strip()
            if not seat or seat.startswith("#"):
                continue
            seat = seat.upper()
            m = re.match(r"^([SH])(\d+)$", seat)
            if not m:
                logging.warning("overrides: bad seat key %r", seat)
                continue
            def num(k):
                v = (r.get(k) or "").strip().replace(",", "")
                return int(v) if v else None
            dem, rep, oth = num("dem_votes"), num("rep_votes"), num("other_votes")
            split = all(num(k) is not None for k in ("dem_ed", "dem_mail", "dem_prov", "rep_ed", "rep_mail", "rep_prov"))
            cands = []
            for party, tot, pre in (("DEM", dem, "dem"), ("REP", rep, "rep")):
                if tot is None:
                    continue
                c = {"candidate": f"{party} (override)", "party": party, "votes": tot, "pct": None,
                     "election_day_votes": num(pre + "_ed") if split else None,
                     "mail_in_votes": num(pre + "_mail") if split else None,
                     "provisional_votes": num(pre + "_prov") if split else None}
                cands.append(c)
            if oth:
                cands.append({"candidate": "other (override)", "party": "OTH", "votes": oth, "pct": None,
                              "election_day_votes": None, "mail_in_votes": None, "provisional_votes": None})
            out.setdefault(seat, []).append({
                "seat": int(m.group(2)), "chamber_prefix": m.group(1), "county": (r.get("county") or "").strip().upper(),
                "dem_votes": dem, "rep_votes": rep, "other_votes": oth,
                "total_votes": sum(x for x in (dem, rep, oth) if x is not None),
                "candidates": cands, "precincts_reporting": num("precincts_reporting"),
                "precincts_total": num("precincts_total"), "override": True,
                "override_note": (r.get("note") or "").strip() or None,
            })
    return out


def apply_overrides(snapshot, overrides, prefix):
    applied = []
    for key, rows in overrides.items():
        if not key.startswith(prefix):
            continue
        seat_no = int(key[1:])
        snapshot["rows"] = [r for r in snapshot["rows"] if r["seat"] != seat_no]
        for r in rows:
            r = dict(r)
            r["fetched_at"] = snapshot["meta"]["fetched_at_utc"]
            r["source_last_updated"] = "manual override"
            snapshot["rows"].append(r)
        applied.append(key)
    snapshot["meta"]["overrides"] = applied
    return applied


# ---------------------------------------------------------------------------
# RSS sanity check
# ---------------------------------------------------------------------------
class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self._row, self._cell, self._in_td = [], [], "", False

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag == "td":
            self._in_td, self._cell = True, ""

    def handle_endtag(self, tag):
        if tag == "td":
            self._row.append(self._cell.strip())
            self._in_td = False
        elif tag == "tr" and self._row:
            self.rows.append(self._row)

    def handle_data(self, data):
        if self._in_td:
            self._cell += data


def rss_check(session, snapshot, office_name):
    """Compare per-candidate statewide totals in the snapshot with the state's
    'Results by Office' RSS feed. Returns a small dict; never raises."""
    try:
        feeds = session.get(fr.BASE + "api/ElectionFeeds/GetElectionFeedList", timeout=30).json()
        feeds = json.loads(feeds) if isinstance(feeds, str) else feeds
        fid = None
        for t in feeds.get("Table", []):
            if "by Office" in t.get("RSSFeedName", ""):
                fid = t["RSSFeedID"]
        if fid is None:
            return {"ok": None, "note": "no by-office feed listed"}
        xml = session.get(fr.BASE + f"electionFeed.aspx?ID={fid}", timeout=60).text
        # the feed is not always well-formed XML (bare '&' in titles), so scan it
        m = re.search(r"<lastBuildDate>(.*?)</lastBuildDate>", xml, re.S)
        build = m.group(1).strip() if m else ""
        totals = {}
        for item_xml in re.findall(r"<item>(.*?)</item>", xml, re.S):
            t = re.search(r"<title>(.*?)</title>", item_xml, re.S)
            if not t or t.group(1).strip() != office_name:
                continue
            d = re.search(r"<description>(.*?)</description>", item_xml, re.S)
            desc = d.group(1) if d else ""
            desc = re.sub(r"^\s*<!\[CDATA\[|\]\]>\s*$", "", desc.strip())
            p = _TableParser()
            p.feed(desc)
            for row in p.rows:
                if len(row) < 7:
                    continue
                cand, party = row[1], row[2]
                try:
                    tot = int(row[6].replace(",", ""))
                except ValueError:
                    continue
                totals[(cand.upper(), party)] = totals.get((cand.upper(), party), 0) + tot
        if not totals:
            return {"ok": None, "note": f"office not in feed {fid}", "feed_built": build}
        diffs = []
        for s in snapshot["seats"]:
            for c in s["candidates"]:
                k = (c["candidate"].upper(), c["party"])
                if k in totals and c["votes"] is not None:
                    diffs.append(abs(totals[k] - c["votes"]))
        return {"ok": (max(diffs) <= 0) if diffs else None, "max_abs_diff": max(diffs) if diffs else None,
                "candidates_matched": len(diffs), "feed_built": build, "feed_id": fid}
    except Exception as e:  # noqa: BLE001
        return {"ok": None, "note": f"rss check failed: {e}"}


def consistency_check(snapshot):
    """County rows summed per seat vs the statewide per-seat totals in the same
    snapshot (two different state endpoints). Overridden seats are skipped."""
    by_seat = {}
    for r in snapshot["rows"]:
        if r.get("override"):
            continue
        for c in r["candidates"]:
            k = (r["seat"], c["party"], c["candidate"].upper())
            by_seat[k] = by_seat.get(k, 0) + (c["votes"] or 0)
    diffs, seats_off = [], set()
    for s in snapshot["seats"]:
        for c in s["candidates"]:
            k = (s["seat"], c["party"], c["candidate"].upper())
            if k in by_seat and c["votes"] is not None:
                d = abs(by_seat[k] - c["votes"])
                diffs.append(d)
                if d:
                    seats_off.add(s["seat"])
    return {"ok": (not seats_off) if diffs else None, "max_abs_diff": max(diffs) if diffs else None,
            "seats_off": len(seats_off), "compared": len(diffs)}


# ---------------------------------------------------------------------------
# estimates
# ---------------------------------------------------------------------------
def load_json(path, default=None):
    try:
        return json.load(open(path))
    except (OSError, json.JSONDecodeError):
        return default


def compute_estimates(chamber, snapshot, priors, baseline, mail, state, now_ts):
    prefix = "S" if chamber == "senate" else "H"
    cfg = priors["chambers"][chamber]
    seats_up = cfg["seats_up"]
    by_seat = {}
    for r in snapshot["rows"]:
        by_seat.setdefault(f"{prefix}{r['seat']}", []).append(r)
    extra = {}
    if mail:
        extra = {"mail_returned": mail.get("returned") or {}, "mail_requested": mail.get("requested") or {},
                 "county_mail_ref": baseline.get("county_mail_ref") or {}}
    seats_out, probs = {}, []
    disp_state = state.setdefault(chamber, {})
    for key in seats_up:
        p = priors["seats"][key]
        b = baseline["seats"].get(key)
        if b is None:
            continue
        est = needle.estimate_seat({"mu": p["mu"], "sd": p["sd"], "fixed_prob": p["fixed_prob"],
                                    "dem_side": p["dem_side"], "ref_margin": p["ref_margin"]},
                                   b, by_seat.get(key, []), extra)
        disp_state[key] = needle.damp(disp_state.get(key), est, now_ts)
        d = disp_state[key]
        if est["fixed"]:
            pd, prr = (est["p_dem"], 1 - est["p_dem"]) if p["dem_side"] == "DEM" else (0.0, 1 - est["p_dem"])
        else:
            pd, prr = (d["p_dem"], 1 - d["p_dem"]) if p["dem_side"] == "DEM" else (0.0, 1 - d["p_dem"])
        probs.append((pd, prr))
        seats_out[key] = {
            "display": {"mu": d["mu"], "sd": d["sd"], "p_dem": d["p_dem"], "swing_until": d["swing_until"]},
            "target": {k: est[k] for k in ("mu", "sd", "p_dem", "lo", "hi", "frac", "frac_matched", "counted_total",
                                           "exp_total", "out_total", "m_ret", "sd_ret", "m_counted", "m_out",
                                           "pooled_swing", "prior_swing", "pooled_turnout", "w_returns",
                                           "zero_precinct_share", "unmatched_share", "mail_gap_share", "status",
                                           "any_report", "fixed")},
            "status": est["status"], "override": any(r.get("override") for r in by_seat.get(key, [])),
        }
    odds = needle.chamber_odds(probs, cfg["holdover_dem"], cfg["holdover_rep"], cfg["total_seats"], cfg["majority"])
    return seats_out, odds


def update_timeline(path, chamber, now_iso, seats_out, odds):
    tl = load_json(path, {"points": []})
    pt = {"t": now_iso,
          "chamber": {k: odds[k] for k in ("p_dem_majority", "p_rep_majority", "p_no_majority",
                                           "dem_seats_mean", "dem_seats_p05", "dem_seats_p95")},
          "seats": {k: {"mu": round(v["display"]["mu"], 2), "p": round(v["display"]["p_dem"], 4),
                        "frac": round(v["target"]["frac"], 4)} for k, v in seats_out.items()}}
    tl["points"].append(pt)
    tl["points"] = tl["points"][-TIMELINE_MAX:]
    tl["chamber"] = chamber
    json.dump(tl, open(path, "w"), separators=(",", ":"))


# ---------------------------------------------------------------------------
# git
# ---------------------------------------------------------------------------
def git_push(paths, message):
    try:
        subprocess.run(["git", "add", "--"] + paths, cwd=HERE, check=True, capture_output=True, text=True)
        st = subprocess.run(["git", "status", "--porcelain", "--"] + paths, cwd=HERE, capture_output=True, text=True)
        if not st.stdout.strip():
            return "nothing to commit"
        subprocess.run(["git", "commit", "-q", "-m", message], cwd=HERE, check=True, capture_output=True, text=True)
        r = subprocess.run(["git", "push", "-q"], cwd=HERE, capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            return f"push failed: {r.stderr.strip()[:200]}"
        return "pushed"
    except subprocess.TimeoutExpired:
        return "push timed out"
    except subprocess.CalledProcessError as e:
        return f"git error: {(e.stderr or str(e)).strip()[:200]}"


# ---------------------------------------------------------------------------
# one cycle
# ---------------------------------------------------------------------------
def run_cycle(a, client, cycle_no, replay_file=None):
    now = dt.datetime.now(dt.timezone.utc)
    now_iso = now.isoformat(timespec="seconds")
    priors_all = load_json(os.path.join(DOCS_DATA, f"priors_{a.target}.json"))
    baseline_all = load_json(os.path.join(DOCS_DATA, f"baseline_{a.target}.json"))
    mail = load_json(os.path.join(DOCS_DATA, f"mail_{a.target}.json"))
    state = load_json(STATE_PATH, {})
    overrides = load_overrides()
    pushed_paths = []
    summary = []
    for chamber in a.chambers:
        office_code = OFFICES[chamber]
        suffix = "" if chamber == "senate" else "_house"
        if replay_file:
            snap = json.load(open(replay_file))
            snap["meta"]["replay_source"] = os.path.basename(replay_file)
            snap["meta"]["fetched_at_utc"] = now_iso
        else:
            snap = fr.build_snapshot(client, a.election_id, a.election_type, a.is_active, office_code)
        applied = apply_overrides(snap, overrides, "S" if chamber == "senate" else "H")
        snap["meta"]["pipeline"] = {"cycle": cycle_no, "generated_at_utc": now_iso, "target": a.target}
        # write returns
        os.makedirs(os.path.join(DATA, "history"), exist_ok=True)
        os.makedirs(DOCS_DATA, exist_ok=True)
        ts = now.strftime("%Y%m%dT%H%M%SZ")
        json.dump(snap, open(os.path.join(DATA, f"returns{suffix}.json"), "w"), separators=(",", ":"))
        json.dump(snap, open(os.path.join(DATA, "history", f"{ts}{suffix}.json"), "w"), separators=(",", ":"))
        json.dump(snap, open(os.path.join(DOCS_DATA, f"returns{suffix}.json"), "w"), separators=(",", ":"))
        # estimates
        seats_out, odds = compute_estimates(chamber, snap, priors_all, baseline_all, mail, state, now.timestamp())
        rss = None
        if not replay_file and cycle_no % 5 == 1:
            rss = rss_check(client.s, snap, snap["meta"]["office"]["OfficeName"])
            if rss.get("ok") is False:
                logging.warning("RSS check: statewide totals differ, max diff %s", rss.get("max_abs_diff"))
        # internal consistency: county rows (GetCountyBreak) must sum to the
        # statewide contest totals (GetOfficeDetails); a mismatch means the
        # state's two endpoints were mid-update when we read them
        cons = consistency_check(snap)
        if cons["ok"] is False:
            logging.warning("consistency: %d seats where county sums differ from statewide totals (max %s)",
                            cons["seats_off"], cons["max_abs_diff"])
        est_path = os.path.join(DOCS_DATA, f"estimates{suffix}.json")
        prev = load_json(est_path, {})
        est = {
            "generated_at_utc": now_iso, "chamber": chamber, "target": a.target,
            "election": snap["meta"]["election"], "statewide_reporting": snap["meta"]["statewide_reporting"],
            "source_last_updated_local": snap["meta"]["source_last_updated_local"],
            "overrides": applied, "rss_check": rss if rss is not None else prev.get("rss_check"),
            "consistency_check": cons,
            "mail_file": bool(mail), "chamber_odds": odds, "seats": seats_out,
            "replay": bool(replay_file),
        }
        json.dump(est, open(est_path, "w"), separators=(",", ":"))
        update_timeline(os.path.join(DOCS_DATA, f"timeline{suffix}.json"), chamber, now_iso, seats_out, odds)
        pushed_paths += [f"docs/data/returns{suffix}.json", f"docs/data/estimates{suffix}.json",
                         f"docs/data/timeline{suffix}.json"]
        sr = snap["meta"]["statewide_reporting"]
        summary.append(f"{chamber}: {len(snap['rows'])} rows, statewide {sr.get('pct_precincts_reporting')}% precincts, "
                       f"P(D maj)={odds['p_dem_majority']:.3f} P(R maj)={odds['p_rep_majority']:.3f} "
                       f"Dem seats {odds['dem_seats_mean']:.1f} [{odds['dem_seats_p05']},{odds['dem_seats_p95']}]"
                       + (f", overrides {applied}" if applied else ""))
    json.dump(state, open(STATE_PATH, "w"))
    git_msg = f"returns {now_iso}" + (" (replay)" if replay_file else "")
    push = git_push(pushed_paths, git_msg) if not a.no_push else "push disabled"
    logging.info("cycle %d | %s | git: %s", cycle_no, " | ".join(summary), push)


def looks_like_challenge(exc):
    s = str(exc).lower()
    return any(k in s for k in ("incapsula", "<html", "expecting value", "429", "403", "challenge", "503"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=180, help="seconds between cycles")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--chambers", default="senate", help="comma list: senate,house")
    ap.add_argument("--target", type=int, default=2026, help="which priors/baseline: 2026 or 2024 (replay)")
    ap.add_argument("--election-id", type=int)
    ap.add_argument("--election-type")
    ap.add_argument("--is-active", type=int)
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--start-at", help="ISO time to start polling, e.g. 2026-11-03T20:00:00-05:00")
    ap.add_argument("--replay", help="directory of returns-shaped snapshots to play instead of fetching")
    ap.add_argument("--replay-every", type=int, default=1, help="use every Nth snapshot")
    ap.add_argument("--reset-state", action="store_true", help="forget damping state and timelines first")
    a = ap.parse_args()
    a.chambers = [c.strip() for c in a.chambers.split(",") if c.strip()]
    log_setup()
    ensure_overrides_template()
    if a.reset_state:
        for p in [STATE_PATH] + [os.path.join(DOCS_DATA, f) for f in os.listdir(DOCS_DATA) if f.startswith("timeline")]:
            if os.path.exists(p):
                os.remove(p)
        logging.info("state and timelines reset")
    if a.start_at:
        t0 = dt.datetime.fromisoformat(a.start_at)
        wait = (t0 - dt.datetime.now(dt.timezone.utc)).total_seconds()
        if wait > 0:
            logging.info("waiting %.0f s until %s", wait, a.start_at)
            time.sleep(wait)
    client = fr.Client()
    client.s.headers["User-Agent"] = "pa-election-night tracker/0.1 (+python-requests)"
    replay_files = None
    if a.replay:
        replay_files = sorted(os.path.join(a.replay, f) for f in os.listdir(a.replay) if f.endswith(".json"))
        replay_files = replay_files[::a.replay_every] + ([replay_files[-1]] if (len(replay_files) - 1) % a.replay_every else [])
        logging.info("replay: %d snapshots from %s", len(replay_files), a.replay)
    cycle, backoff, idx = 0, 1.0, 0
    while True:
        cycle += 1
        rf = None
        if replay_files is not None:
            if idx >= len(replay_files):
                logging.info("replay finished")
                break
            rf = replay_files[idx]
            idx += 1
        try:
            run_cycle(a, client, cycle, rf)
            backoff = 1.0
        except KeyboardInterrupt:
            raise
        except Exception as e:  # noqa: BLE001
            if looks_like_challenge(e):
                backoff = min(backoff * 2.0, 10.0)
                logging.error("cycle %d: state site challenge/rate-limit suspected (%s); backoff x%.0f", cycle, e, backoff)
                client = fr.Client()  # fresh session, fresh cookies
                client.s.headers["User-Agent"] = "pa-election-night tracker/0.1 (+python-requests)"
            else:
                backoff = min(backoff * 1.5, 4.0)
                logging.error("cycle %d failed: %s; backoff x%.1f", cycle, e, backoff)
        if a.once:
            break
        time.sleep(a.interval * backoff)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logging.info("stopped")
