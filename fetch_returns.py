#!/usr/bin/env python3
"""
fetch_returns.py - pull one snapshot of every Pennsylvania State Senate contest
from the PA Department of State's unofficial election-night returns site
(https://www.electionreturns.pa.gov) and write a normalized file to
data/returns.json: one row per Senate seat per county.

The site has no documented API. The endpoints below were recovered from the
site's own Angular code (Scripts/AppScripts/Service.js) on 2026-11-02 and
verified with plain HTTP GETs (no browser, no cookies, no special headers).

Usage:
    python fetch_returns.py                       # latest election (auto)
    python fetch_returns.py --election-id 105 --election-type G   # 2024 general
    python fetch_returns.py --office STH          # State House instead of Senate
    python fetch_returns.py --out data/x.json --raw data/raw

Never fabricates values: every number in the output is parsed from a server
response, and anything the server does not supply is null.
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
import time

import requests

BASE = "https://www.electionreturns.pa.gov/"
API = BASE + "api/ElectionReturn/"
REPORTS_API = BASE + "api/Reports/"

# Party codes the site uses. Anything else is bucketed as "other".
DEM = "DEM"
REP = "REP"

DISTRICT_KEY_RE = re.compile(r"^(?P<name>.+?)\$\$(?P<id>\d+)\$\$$")
ORDINAL_RE = re.compile(r"^(\d+)(st|nd|rd|th)\b", re.I)


class Client:
    def __init__(self, raw_dir=None, pause=0.15, timeout=30, retries=3):
        self.s = requests.Session()
        # A UA is not required (verified), but identify ourselves politely.
        self.s.headers["User-Agent"] = "pa-election-night/0.1 (+requests)"
        self.raw_dir = raw_dir
        self.pause = pause
        self.timeout = timeout
        self.retries = retries
        if raw_dir:
            os.makedirs(raw_dir, exist_ok=True)

    def get_json(self, url, params, raw_name):
        """GET and decode. Responses are JSON strings containing JSON (double
        encoded) for most endpoints; handle both single and double encoding."""
        last_exc = None
        for attempt in range(self.retries):
            try:
                r = self.s.get(url, params=params, timeout=self.timeout)
                r.raise_for_status()
                text = r.text
                if self.raw_dir:
                    with open(os.path.join(self.raw_dir, raw_name + ".json"), "w") as f:
                        f.write(text)
                time.sleep(self.pause)
                outer = json.loads(text)
                if isinstance(outer, str):
                    if outer.strip() == "":
                        return None  # server's way of saying "no data"
                    try:
                        return json.loads(outer)
                    except json.JSONDecodeError:
                        return outer  # a plain string payload (e.g. timestamp)
                return outer
            except (requests.RequestException, json.JSONDecodeError) as e:
                last_exc = e
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"failed {url} {params}: {last_exc}")


def to_int(v):
    if v is None:
        return None
    if isinstance(v, int):
        return v
    s = str(v).strip().replace(",", "")
    if s == "":
        return None
    try:
        return int(s)
    except ValueError:
        try:
            return int(float(s))
        except ValueError:
            return None


def to_float(v):
    if v is None:
        return None
    s = str(v).strip().replace(",", "").rstrip("%")
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def strip_html(s):
    if not s:
        return None
    return " ".join(re.sub(r"<[^>]+>", " ", s).replace("&nbsp;", " ").split()) or None


def seat_number(district_name):
    m = ORDINAL_RE.match(district_name.strip())
    return int(m.group(1)) if m else None


# ----------------------------------------------------------------------------
# Endpoint wrappers (all verified 2026-11-02)
# ----------------------------------------------------------------------------

def get_latest_election(c):
    # -> [{"ElectionID":117,"ElectionName":"2026 General Election",
    #      "ElectionDate":"2026-11-03T00:00:00","ElectionType":"G"}]
    return c.get_json(API + "GetLatestElection", {"methodName": "GetLatestElection"},
                      "GetLatestElection")[0]


def get_election_list(c):
    # Report Center's election list. Reachable WITHOUT reCAPTCHA. Has ISActive,
    # ElectionStatus ("U" unofficial / "O" official), RegisteredVotes,
    # BallotsCounted. Keys: Electionid, ElectionType, ...
    d = c.get_json(REPORTS_API + "GetElectionList", {}, "Reports_GetElectionList")
    return d["Table"]


def common_params(election):
    return {
        "electionid": election["id"],
        "electiontype": election["type"],
        "isactive": election["is_active"],
    }


def get_election_detail(c, election):
    # IsTesting flag + banner text. Name comes back as "...,Unofficial".
    p = {"methodName": "GetElectionDetail"}
    p.update(common_params(election))
    d = c.get_json(API + "GetElectionTesting", p, "GetElectionTesting")
    return d[0] if d else None


def get_office_names(c, election):
    # Table: [{"OfficeID":12,"OfficeCode":"STS","OfficeName":"Senator in the General Assembly"}, ...]
    # Table1[0].DashboardMessage: statewide precincts reporting (JSON string)
    p = {"countyName": "", "methodName": "GetOfficeNames"}
    p.update(common_params(election))
    return c.get_json(API + "GetOfficeNames", p, "GetOfficeNames")


def get_office_details(c, election, office_id):
    # NOTE: methodName must be "GetOfficeDetails" ("GetOfficeData" returns ""
    # for the active election). Returns
    # {"Election":{"<OfficeName>":[{"<District>$$<DistrictId>$$":[{"DistrictId":..,
    #   "District":..,"Candidates":[{...}]}]}, ...]}}
    p = {"officeId": office_id, "methodName": "GetOfficeDetails"}
    p.update(common_params(election))
    return c.get_json(API + "GetOfficeData", p, f"GetOfficeData_{office_id}")


def get_county_break(c, election, office_id, district_id):
    # {"Election":{"<District name>":[{"<COUNTY>":[{candidate rows}]}, ...]}}
    # district_id is the site's internal DistrictId (e.g. 24 for the 2nd
    # Senatorial District), NOT the seat number.
    p = {"officeId": office_id, "districtId": district_id, "methodName": "GetCountyBreak"}
    p.update(common_params(election))
    return c.get_json(API + "GetCountyBreak", p, f"GetCountyBreak_{office_id}_{district_id}")


def get_county_message(c, election, county):
    # Per-county precincts reporting. countyName is case-insensitive; the
    # site's own spelling for multi-word counties is "McKean" (no space).
    p = {"countyName": county, "methodName": "GetCountyMessageData"}
    p.update(common_params(election))
    return c.get_json(API + "GetCountyMessageData", p, f"GetCountyMessageData_{county}")


def get_dashboard_message(c, election):
    p = {"countyName": "", "methodName": "GetDashboardMessage"}
    p.update(common_params(election))
    return c.get_json(API + "GetDashboardMessage", p, "GetDashboardMessage")


def get_last_updated(c):
    # -> "2026-11-02 13:07:48.100" (server local time, America/New_York)
    return c.get_json(API + "GetUpdatedTimeStamp", {"methodName": "LastUpdatedTimeStamp"},
                      "GetUpdatedTimeStamp")


# ----------------------------------------------------------------------------
# Normalization
# ----------------------------------------------------------------------------

def parse_reporting(msg):
    """Dashboard/County message -> dict of precincts reporting."""
    if not msg:
        return {"precincts_reporting": None, "precincts_total": None,
                "pct_precincts_reporting": None, "reporting_note": None}
    return {
        "precincts_reporting": to_int(msg.get("ReportingDistrict")),
        "precincts_total": to_int(msg.get("VotingDistrict")),
        "pct_precincts_reporting": to_float(msg.get("Percentage")),
        "reporting_note": (msg.get("DistrictMessage") or "").strip() or None,
        "show_votes_breakdown": msg.get("ShowVotesBreakdown") == "1",
    }


def candidate_row(r):
    breakdown = r.get("ShowVotesBreakdown") == "1"
    return {
        "candidate": (r.get("CandidateName") or "").strip(),
        "party": (r.get("PartyName") or "").strip(),
        "votes": to_int(r.get("Votes")),
        "pct": to_float(r.get("Percentage")),
        "election_day_votes": to_int(r.get("ElectionDayVotes")) if breakdown else None,
        "mail_in_votes": to_int(r.get("MailInVotes")) if breakdown else None,
        "provisional_votes": to_int(r.get("ProvisionalVotes")) if breakdown else None,
    }


def sum_party(cands, party_pred, field):
    vals = [c[field] for c in cands if party_pred(c["party"]) and c[field] is not None]
    return sum(vals) if vals else (0 if any(party_pred(c["party"]) for c in cands) and field == "votes" else None)


def party_totals(cands):
    def tot(pred):
        return {
            "votes": sum_party(cands, pred, "votes"),
            "election_day": sum_party(cands, pred, "election_day_votes"),
            "mail_in": sum_party(cands, pred, "mail_in_votes"),
            "provisional": sum_party(cands, pred, "provisional_votes"),
        }
    dem = tot(lambda p: p == DEM)
    rep = tot(lambda p: p == REP)
    oth = tot(lambda p: p not in (DEM, REP))
    total = sum(c["votes"] for c in cands if c["votes"] is not None) if cands else None
    return dem, rep, oth, total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--election-id", type=int, help="override ElectionID (e.g. 117)")
    ap.add_argument("--election-type", choices=["G", "P", "S"], help="override type (G/P/S)")
    ap.add_argument("--is-active", type=int, choices=[0, 1],
                    help="override isactive flag (must match the server's own flag)")
    ap.add_argument("--office", default="STS", help="OfficeCode: STS (Senate, default) or STH (House)")
    ap.add_argument("--out", default="data/returns.json")
    ap.add_argument("--raw", default=None, help="directory to dump raw server responses")
    ap.add_argument("--no-county-reporting", action="store_true",
                    help="skip the per-county precincts-reporting calls")
    args = ap.parse_args()
    c = Client(raw_dir=args.raw)
    out = build_snapshot(c, args.election_id, args.election_type, args.is_active, args.office,
                         county_reporting=not args.no_county_reporting)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(out, f, indent=1)
    m = out["meta"]
    print(f"wrote {args.out}: {m['seat_count']} contests, {m['row_count']} seat-county rows; "
          f"election {m['election']['name']} (id {m['election']['id']}/{m['election']['type']}, "
          f"active={m['election']['is_active']}, testing={m['election']['is_testing']}); "
          f"statewide {m['statewide_reporting']['reporting_note']}")


def build_snapshot(c, election_id=None, election_type=None, is_active_override=None,
                   office_code="STS", county_reporting=True):
    """One full snapshot for one office. Raises RuntimeError on a bad response
    so a long-running caller can back off instead of crashing."""
    fetched_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    # 1. Which election?
    latest = get_latest_election(c)
    elist = get_election_list(c)
    eid = election_id or latest["ElectionID"]
    etype = election_type or latest["ElectionType"]
    match = [e for e in elist if e["Electionid"] == eid and e["ElectionType"] == etype]
    if not match:
        raise RuntimeError(f"election {eid}/{etype} not in api/Reports/GetElectionList")
    einfo = match[0]
    is_active = is_active_override if is_active_override is not None else einfo["ISActive"]
    election = {"id": eid, "type": etype, "is_active": is_active}

    detail = get_election_detail(c, election)
    offices = get_office_names(c, election)
    if offices is None:
        # the server's isactive flag may lag the list; try the other value once
        election["is_active"] = 1 - int(is_active)
        offices = get_office_names(c, election)
        if offices is None:
            raise RuntimeError(f"no office list for election {eid}/{etype} with either isactive flag")
        is_active = election["is_active"]
    office = [o for o in offices["Table"] if o["OfficeCode"] == office_code]
    if not office:
        raise RuntimeError(f"office {office_code} not on ballot; available: "
                           f"{[o['OfficeCode'] for o in offices['Table']]}")
    office = office[0]

    statewide_msg = json.loads(offices["Table1"][0]["DashboardMessage"]) if offices.get("Table1") else None
    last_updated = get_last_updated(c)

    # 2. Every contest for the office (statewide totals per district)
    details = get_office_details(c, election, office["OfficeID"])
    if details is None:
        raise RuntimeError("GetOfficeData returned empty; election may not be active")
    blocks = details["Election"][office["OfficeName"]]
    seats = []
    for blk in blocks:
        for key, lst in blk.items():
            m = DISTRICT_KEY_RE.match(key)
            d = lst[0]
            district_id = int(d.get("DistrictId") or (m.group("id") if m else 0))
            name = (d.get("District") or (m.group("name") if m else key)).strip()
            cands = [candidate_row(r) for r in d.get("Candidates", [])]
            dem, rep, oth, total = party_totals(cands)
            seats.append({
                "seat": seat_number(name),
                "district_id": district_id,
                "district": name,
                "candidates": cands,
                "dem": dem, "rep": rep, "other": oth, "total_votes": total,
            })
    seats.sort(key=lambda s: (s["seat"] or 0))

    # 3. Per-county breakdown for every seat
    rows = []
    counties = set()
    for s in seats:
        cb = get_county_break(c, election, office["OfficeID"], s["district_id"])
        if cb is None:
            print(f"warn: no county breakdown for {s['district']}", file=sys.stderr)
            continue
        for dist_name, lst in cb["Election"].items():
            for blk in lst:
                for county, cand_rows in blk.items():
                    cands = [candidate_row(r) for r in cand_rows]
                    dem, rep, oth, total = party_totals(cands)
                    counties.add(county)
                    rows.append({
                        "seat": s["seat"],
                        "district_id": s["district_id"],
                        "district": dist_name.strip(),
                        "county": county,
                        "dem_votes": dem["votes"],
                        "rep_votes": rep["votes"],
                        "other_votes": oth["votes"],
                        "total_votes": total,
                        "dem_by_method": {k: dem[k] for k in ("election_day", "mail_in", "provisional")},
                        "rep_by_method": {k: rep[k] for k in ("election_day", "mail_in", "provisional")},
                        "other_by_method": {k: oth[k] for k in ("election_day", "mail_in", "provisional")},
                        "candidates": cands,
                    })

    # 4. Per-county precincts reporting (county-wide, not per contest)
    county_rep = {}
    if county_reporting:
        for county in sorted(counties):
            msg = get_county_message(c, election, county)
            county_rep[county] = parse_reporting(msg)
            if msg:
                county_rep[county]["county_url"] = msg.get("CountyUrl") or None
    for r in rows:
        rep_ = county_rep.get(r["county"], parse_reporting(None))
        r["precincts_reporting"] = rep_["precincts_reporting"]
        r["precincts_total"] = rep_["precincts_total"]
        r["pct_precincts_reporting"] = rep_["pct_precincts_reporting"]
        r["fetched_at"] = fetched_at
        r["source_last_updated"] = last_updated

    out = {
        "meta": {
            "source": BASE,
            "fetched_at_utc": fetched_at,
            "source_last_updated_local": last_updated,
            "election": {
                "id": eid, "type": etype, "is_active": is_active,
                "name": einfo["ElectionName"], "date": einfo["ElectionDate"],
                "status": einfo.get("ElectionStatus"),
                "registered_voters": to_int(einfo.get("RegisteredVotes")),
                "ballots_counted": to_int(einfo.get("BallotsCounted")),
                "is_testing": bool(detail and detail.get("IsTesting")),
                "banner": strip_html((detail or {}).get("BannerMessage")),
            },
            "office": office,
            "statewide_reporting": parse_reporting(statewide_msg),
            "row_count": len(rows),
            "seat_count": len(seats),
        },
        "seats": seats,
        "county_reporting": county_rep,
        "rows": rows,
    }
    return out


if __name__ == "__main__":
    main()
