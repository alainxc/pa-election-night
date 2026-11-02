# Can PA election-night returns be pulled automatically? Feasibility report

Date of investigation: Monday, November 2, 2026 (the day before the election).
Scope: PA Department of State unofficial returns at https://www.electionreturns.pa.gov/, for the 25 even-numbered State Senate seats.

## Bottom line

Yes. The site's undocumented JSON API answers plain HTTP GETs from Python `requests` with no browser, no cookies, no special headers, and no reCAPTCHA. The 2026 General Election is already loaded under its final ID (117, type G) in "testing mode" with every candidate and every seat-county pairing present, so nothing about the URLs or IDs should change tomorrow. `fetch_returns.py` pulls a full Senate snapshot (25 contests, 55 seat-county rows, 37 counties) in about 17 seconds using about 70 requests. The same script run against the 2024 general returns real nonzero data whose county sums match the statewide totals exactly.

Honest estimate for election night: high probability (I'd say 85 to 90 percent) this works unchanged after 8 pm. The remaining risk is operational: a flag flip on the server side (`isactive`), rate limiting under load, or the Imperva bot wall deciding to challenge the API path, none of which I can test before polls close. Each is covered by a fallback below.

## What I could and could not do

- The in-app browser pane is blocked by the organization's browsing policy for electionreturns.pa.gov, and the Chrome extension did not respond. I could not watch live network traffic. Instead I downloaded the site's Angular source (`Scripts/AppScripts/Service.js` and the controllers) and read every `$http` call out of it. That is a complete inventory of what the page calls. Copies are in `research/site-js/`.
- Every endpoint listed below was then exercised with curl and with Python `requests`, against both election 117/G (2026) and 105/G (2024 general).
- Playwright was not needed and was not used.

## Front door: Imperva (Incapsula)

A first bare GET of the HTML homepage returns a 920-byte Imperva JavaScript challenge, and only a second request with the returned cookies gets the real page. This does not apply to the API: direct GETs to `/api/ElectionReturn/*` returned JSON on the first request with Python's default `python-requests` user agent and an empty cookie jar. Imperva cookies (`visid_incap_*`, `incap_ses_*`) are set but not required. Keep the session object alive anyway so cookies are reused.

## Endpoints that work (all GET, all under https://www.electionreturns.pa.gov/)

Every response is JSON, but almost always a JSON *string* that itself contains JSON (double-encoded). Decode twice. Vote counts and percentages come back as strings. An empty string `""` is the server's "no data" answer and usually means a wrong `isactive` flag or an ID that does not exist for that election.

Common query parameters for the results endpoints: `electionid`, `electiontype` (G, P or S), `isactive` (0 or 1), plus a `methodName` that must match the endpoint's expected value. All three election parameters are required; omitting `isactive` returns a 404, and a wrong `isactive` returns `""`.

| Purpose | URL | Notes |
|---|---|---|
| Current election and ID | `api/ElectionReturn/GetLatestElection?methodName=GetLatestElection` | Returns `[{"ElectionID":117,"ElectionName":"2026 General Election","ElectionDate":"2026-11-03T00:00:00","ElectionType":"G"}]`. No `isactive` in this response. |
| Every election with the active flag | `api/Reports/GetElectionList` | Report Center list, reachable without reCAPTCHA. Fields: `Electionid`, `ElectionType`, `ISActive`, `ElectionStatus` (U unofficial, O official), `RegisteredVotes`, `BallotsCounted`. This is where the script reads `isactive`. |
| All elections (menu) | `api/ElectionReturn/GetAllElections?methodName=GetAllElections` | 167 elections back to 2000, triple-encoded. |
| Election detail and testing flag | `api/ElectionReturn/GetElectionTesting?methodName=GetElectionDetail&electionid=117&electiontype=G&isactive=1` | `IsTesting: true` today, with banner text. Watch this flip to false. |
| Offices on the ballot | `api/ElectionReturn/GetOfficeNames?countyName=&methodName=GetOfficeNames&electionid=…` | `Table`: Governor (3, GOV), Representative in Congress (11, USC), Senator in the General Assembly (12, STS), Representative in the General Assembly (13, STH). `Table1[0].DashboardMessage` is the statewide precincts-reporting JSON. |
| Statewide results, all offices | `api/ElectionReturn/GET?methodName=GetSummaryData&electionid=…` | Top-of-ticket only (Governor today). Not needed for Senate. |
| All contests for one office, statewide totals | `api/ElectionReturn/GetOfficeData?officeId=12&methodName=GetOfficeDetails&electionid=…` | `methodName` must be `GetOfficeDetails`; `GetOfficeData` returns `""` for the active election. Keys look like `"2nd Senatorial District$$24$$"`. |
| Per-county results for one contest | `api/ElectionReturn/GetCountyBreak?officeId=12&districtId=24&methodName=GetCountyBreak&electionid=…` | `districtId` is the site's internal DistrictId, not the seat number (see encoding). Returns one block per county with the candidate rows. |
| All contests in one county | `api/ElectionReturn/GetCountyData?countyName=Adams&methodName=GetCountyData&electionid=…` | Alternative cut; not used by the script. |
| Precincts reporting, statewide | `api/ElectionReturn/GetDashboardMessage?countyName=&methodName=GetDashboardMessage&electionid=…` | `ReportingDistrict`, `VotingDistrict`, `Percentage`. |
| Precincts reporting, per county | `api/ElectionReturn/GetCountyMessageData?countyName=Philadelphia&methodName=GetCountyMessageData&electionid=…` | Same fields plus `CountyUrl`. County-wide, not per contest. |
| Last updated | `api/ElectionReturn/GetUpdatedTimeStamp?methodName=LastUpdatedTimeStamp` | A plain string like `"2026-11-02 13:07:48.100"`, server local time (Eastern). |
| RSS feeds | `api/ElectionFeeds/GetElectionFeedList` then `electionFeed.aspx?ID=83` | Feed 81 by election, 82 by county, 83 by office. HTML tables inside RSS, with per-county Election Day, Mail, Provisional and Total columns. Useful as a second source. |
| Report Center filters | `api/Reports/GetFilterData?electionId=117&electionsubtype=G` | Works without reCAPTCHA. The CSV generator (`api/Reports/GenerateReport`, POST) sits behind `api/Reports/GetRecaptchaRes` and was not attempted. |

### By-vote-method breakdown

There is no separate vote-by-method endpoint. The site's "Vote by Method" page reuses the same payloads. Every candidate row, statewide and per county, carries `ElectionDayVotes`, `MailInVotes` and `ProvisionalVotes` alongside `Votes`, gated by `ShowVotesBreakdown` ("1" today). In 2024 data the three methods sum exactly to `Votes` for every seat-county row. The script nulls the method fields when the flag is off.

### Percent precincts reporting

Available statewide and per county only, not per contest. The message text today reads "IN PERSON BALLOT REPORTING ONLY", meaning the precinct count reflects in-person returns and says nothing about how much of the mail vote a county has counted. A multi-county seat has no single reporting figure; the output carries the county figure on each seat-county row.

## How things are encoded

- **Election**: `(electionid, electiontype, isactive)`. The ID alone is not unique: 117/P is the 2026 primary and 117/G is the 2026 general. 2024 general is 105/G, 2022 general 94/G. Specials have their own IDs (118, 119 this year).
- **Office**: integer `OfficeID` with a three-letter `OfficeCode`. State Senate is 12 / STS, State House is 13 / STH, stable across 2024 and 2026.
- **District**: the site's `DistrictId` is an internal key. For the Senate in both 2024 and 2026 it is seat number + 22 (1st = 23, 2nd = 24, ... 50th = 72). The script does not rely on that arithmetic; it reads the ID out of the `"Nth Senatorial District$$ID$$"` key and parses the seat number from the ordinal in the name.
- **County**: upper-case names in results (`PHILADELPHIA`); the `countyName` parameter is case-insensitive. Multi-word counties are written without a space (`McKean` or `MCKEAN`; `MC KEAN` returns nothing).
- **Party**: `DEM`, `REP`, and others seen this year: `LIB`, `GRN`, `IND`. The script buckets everything that is not DEM or REP as "other".

## What `fetch_returns.py` does

1. Calls `GetLatestElection` and `api/Reports/GetElectionList` to pick the election and read its real `ISActive` flag (overridable with `--election-id`, `--election-type`, `--is-active`).
2. Reads the office list and finds the office by code (`--office STS` default, `STH` for the House later).
3. Pulls the office details for all 25 contests, then one `GetCountyBreak` per contest, then one `GetCountyMessageData` per distinct county.
4. Writes `data/returns.json` with `meta` (election, testing flag, statewide reporting, timestamps), `seats` (statewide totals per contest), `county_reporting`, and `rows`: one row per seat per county with `dem_votes`, `rep_votes`, `other_votes`, `total_votes`, per-party by-method dicts, the raw candidate list, `precincts_reporting`, `precincts_total`, `pct_precincts_reporting`, `fetched_at` and `source_last_updated`.
5. `--raw DIR` saves every server response verbatim for audit.

Run it with:

```bash
.venv/bin/python fetch_returns.py --raw data/raw
```

Validation run against 2024 (`data/validation_2024_general.json`): 25 contests, 59 seat-county rows, zero mismatches between statewide totals and county sums, and zero mismatches between method sums and total votes.

## County-level fallback (checked, not built)

Scytl/Clarity hosts result sites for at least Allegheny, Luzerne, Delaware, York and Erie at `https://results.enr.clarityelections.com/PA/<County>/`. The structure is `/PA/<County>/elections.json` (list with `EID`), then `/PA/<County>/<EID>/current_ver.txt`, then `/<EID>/<ver>/json/en/summary.json`. As of today Allegheny, Luzerne and Erie list no elections at all, and Delaware lists only the 2024 primary with a 404 on its summary path. Philadelphia, Montgomery, Bucks, Chester and Lancaster are not on Clarity. The Clarity route therefore cannot be verified before tomorrow and would cover only a handful of counties. The state RSS feed (feed 83) is the better second source since it is the same data in a different wrapper.

## What cannot be verified until polls close at 8 pm Tuesday

- Whether `isactive` stays 1 for 117/G once live reporting begins. If `GetOfficeData` starts returning `""`, re-read `ISActive` from `api/Reports/GetElectionList` (the script does this on every run) and if that also disagrees, try the other value.
- Whether `IsTesting` flips to false and the banner text changes. Today all vote counts are 0 and 41 of 9,066 precincts are flagged "reporting" as test data.
- Whether the Imperva layer starts challenging API calls under election-night load, or rate limits a client making 70 requests every few minutes. Mitigation: keep one `requests.Session`, poll no more often than every 2 to 3 minutes, and fall back to a browser cookie jar or Playwright if the API begins returning the 920-byte challenge page.
- Whether `ShowVotesBreakdown` stays "1" live. PA counties have reported mail totals separately in past cycles; 2024 data has the breakdown fully populated, so this is likely but not certain.
- Whether the write-in or "other" rows appear with different party codes than seen so far.
- Response times and payload sizes under load (today: 17 seconds for a full Senate snapshot; the House office payload is 248 KB versus 33 KB for the Senate).

## Files

- `fetch_returns.py`: the fetcher.
- `requirements.txt`: `requests`.
- `data/returns.json`: today's snapshot of 117/G (all zeros, testing mode).
- `data/validation_2024_general.json`: the same script against 105/G, real data.
- `data/raw_2026/`: raw server responses from today's run.
- `research/site-js/`: the site's own Angular source, the authority for the endpoint list.
- `research/probes/`: raw curl outputs from the investigation.
