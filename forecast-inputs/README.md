Copied on 2026-11-02 from the Pennsylvania Legislature 2026 forecast project (the model behind
https://alainxc.github.io/Pennsylvania-State-Legislature-Election-Model-/). Read-only inputs for the election-night tracker.

processed/          Cleaned tables used by the forecast. Key ones:
  district_counties.csv               which counties each House/Senate district touches (share of district area)
  district_presidential_baseline.csv  2024 pres, 2022 gov/US Sen, 2020 pres totals per district
  legislative_results.csv             2020/2022/2024 legislative results per district
  current_holders.csv, candidates_2026.csv, district_regions.csv
raw-returns/        PA DoS precinct-level bulk returns (pados/), LRC crosswalks, Wikipedia/Ballotpedia pulls.
                    See raw-returns/NOTES.md and MANIFEST.json for file-by-file provenance.
forecast-output/    forecast.json = full per-seat forecast (mu, sd, probabilities, environment); seats.csv = flat table;
                    backtest.json = 2022/2024 backtests.
config/             model_config.yaml = the forecast's simulation settings (regional SD, holdovers, majority thresholds).
Seat keys in the forecast are S46 (Senate 46) and H145 (House 145) style.
