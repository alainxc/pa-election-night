# Election night operations: Tuesday, November 3, 2026

Site: https://alainxc.github.io/pa-election-night/ (Senate at `/`, House at `/house.html`, seat pages at `/seat.html?seat=S46`).
Repository: https://github.com/alainxc/pa-election-night (public; Pages deploys from `docs/` by Actions on every push, about 30 to 60 seconds).

## Cloud mode (recommended): runs on GitHub's servers, no laptop needed

The poller runs as a GitHub Actions job in the repository (`.github/workflows/poll.yml`). It fetches the state feed, commits the data files, and triggers the Pages deploy itself. Your laptop can be closed, asleep or off; you only need a phone to watch or to stop it.

**Status on election night 2026:** a run was launched at 5:49 pm Eastern on Nov 3 and is waiting for 8:00 pm. A scheduled backstop also fires at 7:45 pm. Nothing else needs to happen.

How it works:

- One job may run at most 6 hours, so the poller exits after 5 h 40 min and launches a fresh copy of itself, until `chain_until` (default 7 am Eastern Nov 4). Each job runs about 2 hours of waiting then 3 h 40 min of polling, then its successor starts within about a minute. Damping state restarts clean at that boundary; the timelines and data files are in the repository, so the site does not notice.
- Only one poller runs at a time (concurrency group). A second launch queues behind the running one.
- Each cycle: `git pull --rebase`, fetch both chambers, compute, commit, push, `gh workflow run pages.yml` (a push made by Actions does not trigger the deploy on its own). Deploy takes about 50 seconds.

From your phone (GitHub mobile app or github.com → repository → Actions → "Election night poller"):

- **Watch:** open the running job; the live log shows one line per cycle (rows, precincts, P(Dem majority), estimated seats, "pushed; pages deploy queued").
- **Stop:** cancel the run. A cancelled run does not relaunch itself.
- **Start or restart:** "Run workflow" with `start_at` = `now` (or an ISO time), `interval` = `180`. Leave `chain_until` as is to keep it relaunching overnight; blank it for a single 5 h 40 min job.
- **Test the feed without pushing:** "Run workflow" with `once` ticked.
- **Override a county:** edit `data/overrides.csv` on github.com and commit; the poller pulls before every cycle and picks it up within 3 minutes.

Verified on Nov 3 at 5:40 pm Eastern: the state API answers from GitHub's servers (both chambers, no challenge), a pushing cycle commits and deploys in under a minute.

## Laptop mode (fallback)

```bash
./arm.sh
```

That installs a per-user scheduled job (launchd) that starts the poller at 7:55 pm on November 3. The poller resets the rehearsal state, waits until 8:00 pm, then polls both chambers every 3 minutes and pushes, with `caffeinate` keeping the Mac awake while it runs. Requirements: the Mac is powered on, plugged in, lid open (or on an external display), and you are logged in; a locked screen is fine, a closed lid is not (the Mac sleeps and polling stops until it wakes). Check it is armed with:

```bash
launchctl print gui/$(id -u)/com.pa-election-night.poll | head -5
```

`./disarm.sh` removes the schedule; `./stop.sh` stops the poller once it is running. Logs land in `logs/poll.log` as usual. Do not run laptop mode and cloud mode at the same time: both push to the same branch and the estimates would jitter between two damping states. If the cloud run dies and you fall back to the laptop, cancel any queued cloud run first.

## Before 8 pm (manual laptop path, if you prefer to run it yourself)

1. Open a terminal in this folder (`cd ~/Desktop/pa-election-night`).
2. Make sure the state feed answers and the election is still ID 117 / type G:

```bash
.venv/bin/python fetch_returns.py --out /tmp/check.json
```

   Expect "25 contests, 55 seat-county rows ... active=1". If `testing=True` is still printed after 8 pm, the state has not switched the site to live data yet; the page shows the TESTING MODE banner automatically.

3. Optional but valuable: download the Department of State's daily mail ballot report in your browser (the file sits behind a bot wall, so the script cannot fetch it):
   https://www.pavoterservices.pa.gov/2026%20General%20Daily%20Mail%20Ballot%20Report.xlsx
   Save it under `data/baseline/mail/` and run:

```bash
.venv/bin/python load_mail_report.py data/baseline/mail/<file>.xlsx
```

   It prints the columns it matched and writes `docs/data/mail_2026.json`. If it matched the wrong columns, rerun with `--county-col`, `--requested-col`, `--returned-col` set to the exact header text. Then commit it:

```bash
git add docs/data/mail_2026.json && git commit -m "mail ballot report" && git push
```

   With this file loaded, each county's expected mail vote is pinned to ballots returned and the mail uncertainty drops from ±60 percent to ±6 percent.

4. Clear the rehearsal state so the night starts clean (this forgets the 2024 replay timelines):

```bash
.venv/bin/python poll.py --once --no-push --reset-state --chambers senate,house
```

   This also fetches one pre-8 pm snapshot (all zeros, testing mode). It does not push.

## At 8 pm: start

One command. It runs both chambers every 3 minutes and pushes to GitHub:

```bash
./start.sh
```

Or arm it earlier and let it wait:

```bash
./start.sh --start-at 2026-11-03T20:00:00-05:00
```

Watch it:

```bash
tail -f logs/poll.log
```

Each cycle logs one line per chamber: rows fetched, statewide precincts, P(Dem majority), P(Rep majority), estimated Dem seats with the 90 percent range, any overrides, and the git result ("pushed", "nothing to commit", or the push error).

## Stop

```bash
./stop.sh
```

## Manual override (county counts typed from a county site)

Edit `data/overrides.csv` (a template with an example row is created on the first run). One row per county for the seat; the seat's feed rows are replaced entirely by what is in the file, so list every county you want shown. Method columns are optional; leave them blank to enter totals only. Save the file; the next cycle picks it up and the site shows an "override" tag on the seat and a banner. Delete the rows (or comment them out with `#`) to return to the feed.

Columns: `seat,county,dem_votes,rep_votes,other_votes,dem_ed,dem_mail,dem_prov,rep_ed,rep_mail,rep_prov,precincts_reporting,precincts_total,note`. Seat keys are `S46` or `H145`; county names as the state spells them (`WASHINGTON`, `MCKEAN`).

## If something breaks

- **Feed returns empty strings or errors after 8 pm.** The poller already retries with the other `isactive` value. If it keeps failing, check the election list the state publishes:

```bash
.venv/bin/python -c "import fetch_returns as f; c=f.Client(); print([e for e in f.get_election_list(c) if e['ElectionYear']==2026])"
```

  If the ID or type changed, restart with explicit values: `./stop.sh && ./start.sh --election-id 117 --election-type G`.

- **State site challenges or rate-limits (log says "challenge/rate-limit suspected", backoff grows).** The poller opens a fresh session and waits up to 10x the interval. Do nothing unless it persists for 30 minutes; then try `./stop.sh`, wait five minutes, `./start.sh --interval 300`.

- **Push fails (log says "push failed").** The site keeps showing the last published data and the page shows a stale warning after 10 minutes. The poller keeps running and retries the push every cycle. Check `gh auth status` and the network; fix and the next cycle pushes everything.

- **Pages deploy slow.** Check https://github.com/alainxc/pa-election-night/actions. Each push runs a 30 to 60 second deploy. If Actions is degraded, the data is still in the repository; viewers only see the delay.

- **The feed is up but the estimates look wrong for one seat.** Open the seat page and read the Diagnostics table: weight on returns, learned turnout, zero-precinct share, unmatched share. If a county is clearly misreported, override that seat from the county's own site.

- **Cloud run stopped on its own (Actions shows a failed or cancelled run).** Open Actions, "Run workflow" with `start_at` = `now`. If GitHub Actions itself is down (https://www.githubstatus.com), fall back to the laptop: `./start.sh`.

- **Everything is down.** The last `docs/data/returns.json` committed is what the page shows. `data/history/` has every snapshot the poller wrote, one file per cycle.

## What the files are

- `docs/data/returns.json`, `returns_house.json`: the raw state feed, normalized, one row per seat per county.
- `docs/data/estimates.json`, `estimates_house.json`: the needle for every seat (computed and displayed values), chamber odds, consistency check.
- `docs/data/timeline.json`, `timeline_house.json`: one point per cycle for the charts.
- `docs/data/priors_2026.json`, `baseline_2026.json`: static inputs built before the election.
- `data/history/`: every snapshot (not published).
- `logs/poll.log`: one line per cycle.
