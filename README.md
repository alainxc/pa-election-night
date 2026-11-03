# Pennsylvania legislature: election-night tracker (2026)

Live, unofficial tracker for the Pennsylvania State Senate (and House) on
November 3, 2026, built from the Department of State's county-level returns.
The published page is in `docs/` (GitHub Pages). Estimates are produced by a
documented model (`needle.py` / `docs/assets/needle.js`) and are not calls.

- `fetch_returns.py` one snapshot of every contest from electionreturns.pa.gov
- `poll.py`, `start.sh`, `stop.sh` the 3-minute loop that publishes `docs/data/`
- `needle.py` the estimate; `tests/` unit and Python/JS parity tests
- `build_baseline.py`, `build_priors.py` expected vote by county and method, priors
- `replay.py` the 2024 county-by-county dry run
- `data/overrides.csv` manual county counts that replace the feed for a seat
- `load_mail_report.py` loads the DoS daily mail ballot report if you download it
- `REPORT.md` feed investigation; `OPERATIONS.md` what to run on election night

Public data only. Nothing on the site is fabricated: unknown values are shown as
"no returns yet".

## Running it

The poller runs in GitHub Actions (`.github/workflows/poll.yml`): start, watch and stop it from the Actions tab or the GitHub mobile app. A laptop fallback (`./start.sh`, `./arm.sh`) is described in `OPERATIONS.md`.
