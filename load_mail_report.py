#!/usr/bin/env python3
"""
load_mail_report.py - turn the Department of State's daily mail ballot report
into docs/data/mail_2026.json = {"requested": {COUNTY: n}, "returned": {COUNTY: n}, ...}

The xlsx sits behind the state's bot wall, so download it yourself in a browser:
  https://www.pavoterservices.pa.gov/2026%20General%20Daily%20Mail%20Ballot%20Report.xlsx
and save it under data/baseline/mail/. Then:
  .venv/bin/python load_mail_report.py data/baseline/mail/<file>.xlsx

The loader looks for a sheet with a county column and columns whose header
contains "request"/"issued" and "return"/"received"; it prints what it matched
so you can confirm before the numbers are used. If the layout is different,
pass --county-col/--requested-col/--returned-col with the exact header text.
"""
import argparse
import json
import os
import re
import sys

import openpyxl

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "docs", "data", "mail_2026.json")


def norm_county(s):
    s = re.sub(r"\s+county$", "", str(s).strip(), flags=re.I)
    return re.sub(r"[^A-Z]", "", s.upper())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("xlsx")
    ap.add_argument("--sheet")
    ap.add_argument("--county-col")
    ap.add_argument("--requested-col")
    ap.add_argument("--returned-col")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    wb = openpyxl.load_workbook(a.xlsx, read_only=True, data_only=True)
    sheets = [wb[a.sheet]] if a.sheet else wb.worksheets
    for ws in sheets:
        rows = list(ws.iter_rows(values_only=True))
        header_idx = None
        for i, row in enumerate(rows[:30]):
            cells = [str(c).strip().lower() if c is not None else "" for c in row]
            if any("county" in c for c in cells):
                header_idx = i
                break
        if header_idx is None:
            continue
        header = [str(c).strip() if c is not None else "" for c in rows[header_idx]]
        low = [h.lower() for h in header]

        def find(explicit, *keys):
            if explicit:
                return header.index(explicit)
            for k in keys:
                for j, h in enumerate(low):
                    if k in h:
                        return j
            return None
        ci = find(a.county_col, "county")
        ri = find(a.requested_col, "request", "issued", "applications")
        bi = find(a.returned_col, "return", "received", "cast")
        if ci is None or (ri is None and bi is None):
            continue
        print(f"sheet {ws.title!r}: county={header[ci]!r} requested={header[ri] if ri is not None else None!r} "
              f"returned={header[bi] if bi is not None else None!r}")
        requested, returned = {}, {}
        for row in rows[header_idx + 1:]:
            if row is None or ci >= len(row) or row[ci] is None:
                continue
            c = norm_county(row[ci])
            if not c or c in ("TOTAL", "STATEWIDE", "GRANDTOTAL"):
                continue
            for idx, dest in ((ri, requested), (bi, returned)):
                if idx is None or idx >= len(row):
                    continue
                v = row[idx]
                try:
                    dest[c] = int(float(str(v).replace(",", "")))
                except (TypeError, ValueError):
                    pass
        if len(requested) < 60 and len(returned) < 60:
            print(f"  only {len(requested)}/{len(returned)} counties parsed; skipping this sheet")
            continue
        out = {"source": os.path.basename(a.xlsx), "sheet": ws.title, "requested": requested, "returned": returned,
               "note": "county-wide mail ballots; the needle scales each district's slice by its baseline share"}
        json.dump(out, open(a.out, "w"), indent=0)
        print(f"wrote {a.out}: {len(requested)} counties requested, {len(returned)} returned; "
              f"statewide returned {sum(returned.values()):,}")
        return
    sys.exit("no usable sheet found; pass --sheet/--county-col/--requested-col/--returned-col")


if __name__ == "__main__":
    main()
