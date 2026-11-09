# Saltum Labs

*Natura non facit saltum. Markets do.*

Saltum Labs is an S&P 500 options terminal.
It shows the move SPX options price in before a macro release, and the move that followed.
It is for learning how option prices encode expected moves. It gives no trade recommendations.

Inspired by working through Akuna Capital's Options 101 and Options 201 courses.

## What it measures

- Before a scheduled release (CPI, the jobs report, an FOMC decision), SPX options
  price in how far the index might move by the close on release day.
- The project measures that priced move from SPX and SPXW option chains.
- It compares it with what the S&P 500 actually did.
- It starts with one CPI release. Then it repeats the same calculation for every
  trading day in the data window.

## Data

- **SPX and SPXW options:** ThetaData (end-of-day option chains). The pipeline
  now takes the quotes at the 16:15 ET options close; see "Snapshot time".
- **Treasury bills and VIX:** FRED (Federal Reserve Bank of St. Louis).
- **S&P 500 closes and trading days:** FRED series `SP500`.
- **Release and decision dates:** BLS and the Federal Reserve, in
  `event_calendar.csv`.

## Data and results

- The analysis uses SPX and SPXW options from ThetaData, plus Treasury bills,
  VIX and S&P 500 closes from FRED.
- The ThetaData data and the results derived from it are kept private, under
  ThetaData's personal-use terms. This repo has no raw chains, no database and
  no result files.
- The code, the method and the tests are public. The site's preview runs on
  invented sample data.

## Files

| File | What it does |
|---|---|
| `config.py` | Every setting: dates, symbol, spread threshold, tolerances, paths |
| `theta.py` | Creates the ThetaData client; spaces and retries requests |
| `fetch_chain.py` | Single event: pulls the chain, saves raw Parquet, loads DuckDB, runs an example query |
| `analyze_event.py` | Single event report. Also holds the shared method (`measure`) used for every date |
| `event_calendar.csv` | Official release / decision dates with their source URL |
| `build_dates.py` | Multi-event step 2: builds the date list |
| `pull_chains.py` | Multi-event step 3: pulls one chain per date (resumable) |
| `calc_moves.py` | Multi-event step 4: implied and actual move for every date |
| `summarize.py` | Multi-event step 5: summary tables, event premium, figure |
| `fetch_surface.py` | Vol surface step 1: all expiries (1 day to ~6 months) for each snapshot date |
| `vol_surface.py` | Vol surface steps 2-6: forward and discount factor, implied vols, Greeks, checks |
| `surface_figures.py` | Vol surface step 7: smiles, term structure, surface, before/after |
| `surface_3d.py` | Interactive 3D smiles and surface (Plotly), one standalone HTML file |
| `vix_check.py` | Replicates the VIX close from the chains (Cboe methodology) and compares with FRED VIXCLS |
| `stage7_all_dates.py` | Stage 7: surfaces and Stage 5 metrics for every trading day since 2020, appended in batches (resumable) |
| `api/` | Product stage 1: the shared read-only API (FastAPI), see "Shared API" at the end |
| `web/` | Product stage 2: the terminal frame and the local dev login, see "Terminal and login" at the end |
| `tests/` | pytest tests for the API (checked against the frozen output files) and for the web server (`tests/web/`) |
| `make_ca_bundle.py` | Optional fix for antivirus HTTPS interception (see below) |

## Setup

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
cp .env.example .env     # then fill in the keys
```

`.env` is git-ignored. `.env.example` lists every variable the project reads.

## How to run

All commands are for Git Bash. Start in the project folder:

```bash
cd /c/macro-wire-poc
```

**Single event** (set in `config.py`, currently August 2026 CPI):

```bash
.venv/Scripts/python.exe fetch_chain.py      # 2 ThetaData requests, a few seconds
.venv/Scripts/python.exe analyze_event.py    # reads DuckDB + downloads FRED CSV
```

**Every date in the window:**

```bash
.venv/Scripts/python.exe build_dates.py      # date list from event_calendar.csv + FRED trading days
.venv/Scripts/python.exe pull_chains.py      # ~840 requests, ~70 min (about 5 s each); safe to stop and rerun
.venv/Scripts/python.exe calc_moves.py       # offline, under a minute
.venv/Scripts/python.exe summarize.py        # tables + data/implied_vs_actual.png
```

- `pull_chains.py` skips any trade date whose Parquet file is already saved
  and readable.
- A half-written file from an interrupted run is deleted and pulled again.
- `pull_chains.py --limit 5` pulls only the next 5 missing dates, for testing.

The `thetadata` library reads the API key from `.env` (`THETADATA_API_KEY=...`).
No script reads, prints or stores it. Don't turn on INFO-level logging: the
library logs its login response at that level.

### Data window

The docs disagree on how much history the free tier has: the text says one year,
a table says from 2023-06-01. Single test requests on this account (2026-10-09)
settled it:
- 2023-06-01 and later return full chains.
- 2023-05-31 and earlier return `PERMISSION_DENIED ... requires a VALUE subscription`.

So chains ran from 2023-06-01 on the free tier.

**Options Value subscription (from 2026-10).** History reaches back to
2020-01-02: 2019-12-31 returns "requires a STANDARD subscription". The event study
now runs from event dates 2020-01-03 onward (`DATA_START = 2020-01-02`). See
"Out-of-sample check: 2020-01 to 2023-05".

### Output locations

- Raw chains: `data/raw/spxw_eod/trade_date=YYYY-MM-DD/*.parquet` (one partition per trade date).
- DuckDB `data/macro_wire.duckdb`, with these tables:
  - `spxw_eod`: all raw chains.
  - `date_list`: the study dates.
  - `event_moves`: one result row per date.
  - `event_premium`: one row per event day.
- `data/date_list.csv`: the date list.
- `data/pull_errors.csv`: failed requests from the last pull.
- `data/skipped_dates.csv`: dates with no result, and why.
- `data/implied_vs_actual.png`: the figure.

### If you see `CERTIFICATE_VERIFY_FAILED`

Some antivirus products (Avast's web shield, for example) re-sign HTTPS
traffic with their own root certificate. Python doesn't trust it. Build a
combined bundle once:

```bash
.venv/Scripts/python.exe make_ca_bundle.py "C:/ProgramData/Avast Software/Avast/wscert.pem"
```

If `certs/ca_bundle.pem` exists, the scripts use it for both the ThetaData login
(httpx) and the ThetaData data channel (gRPC). Certificate checking stays on.
Delete the file to go back to the defaults.

## Method (identical for the single event and for every date)

1. **Chain.** `option_at_time_quote` for `SPXW` returns, for every strike, the
   last NBBO bid and ask at the **options close** of the previous trading day:
   16:15 ET, or 13:15 ET on a half day. It requests only the expiry on date D.
   This needs the Options Value subscription. Until 2026-10 the chain came from
   `option_history_eod` (ThetaData's end-of-day report, about 17:15 ET); see
   "Snapshot time" below for why that changed. The end-of-day results are kept
   as `event_moves_eod` and `event_premium_eod`.
2. **Cleaning.** Drop quotes with a missing or zero bid and crossed quotes
   (ask < bid). Keep a quote if its spread is at most `MAX_RELATIVE_SPREAD`
   (10%) of mid **or** at most `MAX_ABSOLUTE_SPREAD` (0.30) index points. The
   second rule keeps cheap options, whose 0.05–0.30-point spreads are a large
   fraction of a small price. All later math uses `mid = (bid + ask) / 2`.
3. **Forward (F).** Put-call parity says `C - P = F - K` when rates are
   ignored. Over one day the discount factor is about 0.9999, so ignoring rates
   is safe. Among strikes with both a clean call and a clean put, take the one
   where `|C - P|` is smallest. Then `F = K + (C - P)`.
4. **ATM straddle.** Call mid + put mid at that **same strike K**.
5. **Implied move = straddle / F.** This is roughly the market's *expected
   absolute* move to expiry, the average size of the move in either direction.
6. **Implied 1-sigma = 1.25 x straddle / F.** For a normal distribution the
   expected absolute move is about 0.8 sigma (sqrt(2/pi) = 0.798), so
   sigma ≈ 1.25 x straddle. This is a rule-of-thumb conversion, not a model fit.
7. **Actual move.** `|close(D) / F - 1|`, with the close from FRED series
   `SP500`. It starts from the forward, not the previous day's cash close: the
   straddle was priced off F at about 16:15, so the actual move has to start from
   the same point. The two starting points can differ enough to flip the ratio
   from below 1 to above 1, which is one reason a single event tells you little.

## Single-event report: what each line means

| Line | Meaning |
|---|---|
| Event (release) date | Release day (CPI at 08:30 ET) |
| Chain snapshot date | Trading day before the release; option prices are from its EOD report |
| Option expiry used | The SPXW option that expires at the close on the release day |
| Strike used (ATM) | Strike where call and put mids are closest, i.e. nearest the forward |
| Forward | Index level implied by the options for expiry, from put-call parity |
| ATM straddle | Cost of buying the call and the put at that strike (mid prices) |
| Implied move | Straddle / forward: the priced-in expected absolute move |
| Implied 1-sigma | 1.25 x implied move: a one-standard-deviation move |
| Actual move | Absolute change from the forward to the S&P 500 close on the release day |
| Ratio actual / implied | Above 1: the index moved more than the options priced in. Below 1: less. |

## Multi-event study

### Date list (`date_list` table, `data/date_list.csv`)

| Column | Meaning |
|---|---|
| `date` | Date D: the release / decision day, or any other trading day |
| `event_type` | `CPI`, `NFP` (Employment Situation), `FOMC` (decision day = 2nd day of the meeting), or `NONE` |
| `trade_date` | Trading day before D; the chain is taken from its EOD report (added so each row is self-contained) |

- **Sources** (`event_calendar.csv`): the BLS CPI and Employment Situation release
  archives, and the Federal Reserve FOMC calendar. Trading days come from FRED
  `SP500`, which has no value on market holidays.
- **Two events on one day:** the day gets two rows (2024-06-12 was CPI and FOMC).
- **Release days when the market was closed** are skipped and printed by
  `build_dates.py`, for example NFP on Good Friday, 2026-04-03.
- **Not in the list:**
  - October 2025 CPI and October 2025 Employment Situation, which BLS did not
    publish because of the 2025 lapse in appropriations.
  - The 2025-08-22 FOMC notation vote, which was not a scheduled decision day.

### Results (`event_moves` table, one row per date)

| Column | Meaning |
|---|---|
| `date` | Date D (option expiry and the day whose move is measured) |
| `event_type` | As above. A day with two events gets a combined label such as `CPI+FOMC` and counts under both in the summaries |
| `trade_date` | Day the chain was taken from |
| `strike` | Parity strike K (closest call and put mids) |
| `forward` | F = K + (C - P) |
| `straddle` | Call mid + put mid at K, in index points |
| `implied_move` | straddle / F (expected absolute move, as a fraction) |
| `actual_move` | \|close(D) / F - 1\| (fraction) |
| `ratio` | actual_move / implied_move |
| `quotes_kept` | Quotes (calls + puts) left after cleaning |
| `after_hours_gap` | True if the forward is more than 0.5% from the FRED close on `trade_date` (likely after-hours news; kept, not skipped) |

### Event premium (`event_premium` table, one row per event day)

A **rough estimate** of how much larger the implied move is on an event day than
on nearby ordinary days:
- `baseline_implied`: the median implied move of NONE days within 20 trading
  days either side. `baseline_n` counts those days.
- `premium = implied_move - baseline_implied`.
- `variance_premium = implied_move² - baseline_implied²`. This is in straddle
  units: (straddle/F)² is about 0.64 sigma², so multiply by 1.56 for sigma².

### How to read the summary

- **Sample sizes.** Every row shows n. Any group with fewer than 30 days is
  flagged as too few to rely on. FOMC has 27 days in this window.
- **What "matching" looks like.** If the straddle were exactly the expected
  absolute move of a normal distribution:
  - mean actual ≈ mean implied,
  - median actual / implied ≈ 0.85 (0.674 sigma / 0.798 sigma),
  - actual would exceed implied on about 42% of days.

  Fat tails push the last two lower still. A median ratio below 1, or a share
  below 50%, does not by itself mean options were priced too high.
- **Baseline days are not quiet days.** NONE days still carry other news:
  company earnings, other economic data (PPI, retail sales, jobless claims), Fed
  speeches, geopolitics. The baseline is "days without these three releases",
  not "days without news", so the event premium understates what a release adds
  compared with a truly empty day.
- **Weekends and holidays.** For a Monday the chain is from Friday, so the option
  covers three calendar days. After a holiday it covers more. Those days sit in
  the baselines too. `summarize.py` prints the median NONE implied move for
  one-day vs 3+-day spans so you can see the size of this effect.
- **One window.** All numbers come from roughly three years (mid-2023 to late
  2026), one market regime, one strike-selection rule. They describe this sample
  only.

## Sanity checks

The single-event scripts stop with `SANITY CHECK FAILED`. The multi-event scripts
log the date to `data/skipped_dates.csv` (or `data/pull_errors.csv`) and continue.

- The expiry is not listed for SPXW, or the chain comes back empty.
- The chain has rows for the wrong expiry, or is missing calls or puts.
- Fewer than `MIN_PAIRED_STRIKES` (3) strikes have both legs after cleaning.
- The parity strike is the lowest or highest usable strike.
- The forward is more than `MAX_FORWARD_STRIKE_GAP` (10 points, two strike steps)
  from the parity strike, which means the strikes nearest the money were cleaned
  away.
- The forward is more than `FORWARD_TOLERANCE` (0.5%) from the FRED close on the
  trade date. This **stops the single-event report**. In the multi-event study it
  is **not** a skip: the date is kept with `after_hours_gap = true`. Option quotes
  are taken about 16:15 ET, after the 16:00 cash close, so a large gap usually
  means real after-hours news. Examples: 2025-04-03, whose chain is from
  2025-04-02, the evening tariffs were announced; and 2024-02-02, after big tech
  earnings on 2024-02-01. `summarize.py` prints the summary with and without
  these dates. The event-premium table includes them.
- Single event only: `TRADE_DATE` is not the last FRED trading day before
  `EVENT_DATE`, or FRED has no close for the release day.

`MIN_PAIRED_STRIKES` was 20 for the first single-event run. One-day chains from
2023 often keep only a handful of clean pairs: cheap wing options fail the 10%
spread rule while the strikes next to the money are fine. A count of 20 would
have dropped many calm days, which are mostly NONE days, and biased the
baseline. So it was lowered to 3 and replaced with the forward-to-strike check
above, which tests what the straddle actually needs.

## Implied volatility surface (two snapshot dates)

Snapshots (`SURFACE_DATES` in `config.py`), each the day before and the day of
a release:
- August 2026 CPI: 2026-09-10, 2026-09-11.
- FOMC decision: 2026-09-15, 2026-09-16.
- Payrolls: 2026-10-01, 2026-10-02.
- March 2025 CPI: 2025-04-09, 2025-04-10 (stressed market).

```bash
.venv/Scripts/python.exe fetch_surface.py     # 3 requests (1 expiry list + 1 chain per date), ~15 s
.venv/Scripts/python.exe vol_surface.py       # offline except FRED T-bill yields / VIXCLS; prints checks
.venv/Scripts/python.exe surface_figures.py   # PNGs in data/figures/
```

**Data.** `option_at_time_quote` with `expiration="*"` returns every listed expiry
in one request, as the last NBBO quote at the options close (16:15 ET, 13:15 on
half days). See "Snapshot time".
- The endpoint ignores `max_dte`, so expiries beyond `SURFACE_MAX_DTE` (185
  days) are dropped locally.
- The same-day expiry is dropped too: it settles at this close.
- Raw chains go to `data/raw/spxw_surface_1615/` and table `spxw_surface_1615`.
- The end-of-day chains used before (`spxw_eod_surface`) and their results
  (`vol_surface_eod`, `surface_expiries_eod`, `data/figures/vol_page_eod.html`)
  are kept. The 51-date validation (`pull_validation.py`, `validate.py`) still
  uses the end-of-day chains.
- `vix_check.py` uses the 16:15 chains. It fetches the SPX monthly at the options
  close for each date that needs one (`spx_vix_1615`). It runs per date: a
  missing chain is reported in the table, not a stop.
- The script compares the expiries it gets against today's expiry list. A
  missing expiry within 30 days stops the run. Later ones are only reported,
  because short-dated SPXW expiries (e.g. Tuesday and Thursday dailies) are
  listed only a few weeks ahead, so they did not exist yet on an older snapshot
  date.

**Method, per expiry**
1. **Clean** the quotes with the same rule as the event study (zero bid or
   crossed quotes dropped; spread at most 10% of mid or 0.30 points).
2. **Forward and discount factor.** Regress `C - P` on `K` over the 20 clean
   paired strikes nearest the money. Because `C - P = DF·F - DF·K`, the slope is
   `-DF` and `F = intercept / DF`. Dividends are absorbed into F. The implied
   rate is `r = -ln(DF) / t`.
3. **Implied vol.** Out-of-the-money options only (puts with K < F, calls with
   K ≥ F), at mid. Black-76 on the forward, European exercise, discounted with
   DF.
   - **Quote rule (surface only).** An option is kept if it passes the
     price-spread rule (10% of mid or 0.30 points), **or** if bid > 0 and its
     IV at the ask minus its IV at the bid is at most
     `SURFACE_MAX_VOL_SPREAD_POINTS` (3 vol points). That difference is stored
     as `vol_spread`.
   - The forward and DF still use only quotes that pass the price-spread rule.
     The event study and the VIX replication are unchanged.
   - The vol-spread rule matters on stressed days, when far out-of-the-money
     quotes are wide in price but still informative in vol. There it adds many
     options and reaches much lower call deltas. On calm dates it adds few and
     changes nothing on the volatility page.
   - **Sensitivity to the limit** (`vol_spread_sensitivity.py`, saved data only,
     setting unchanged at 3): the surface is rebuilt in memory at 2, 3 and 4 vol
     points, to show what `vol_page.py` would draw. The results are private (see "Data and results").
   - **The solver** is bisection on sigma in [0.0001, 5]. Prices outside that
     bracket, and prices at or below intrinsic value, are dropped and counted.
     The intrinsic count is always 0, because an OTM option has zero intrinsic
     value.
4. **Time to expiry** (the convention). Calendar time, ACT/365, from **16:15 ET
   on the snapshot date** (when SPX options close and VIX is struck) to **16:00
   ET on the expiry date** (SPXW PM settlement).
5. **Greeks** from the same model at these implied vols. All are in index
   points; multiply by 100 for dollars per SPX contract.
   - **delta:** with respect to the forward. Calls 0 to 1, puts -1 to 0, both
     scaled by DF.
   - **gamma:** change in delta per 1 index point.
   - **vega:** change in price per 1 vol point (e.g. 15% → 16%).
   - **theta:** change in price per calendar day, with the forward and vol held
     fixed.

**Table `vol_surface` (one row per option)**

| Column | Meaning |
|---|---|
| `trade_date` | Snapshot date |
| `expiration` | Expiry date |
| `strike`, `right` | Strike (index points), `CALL` or `PUT` |
| `mid` | (bid + ask) / 2, index points |
| `forward` | F for that expiry, from the regression |
| `discount_factor` | DF for that expiry, from the regression |
| `t_years` | Time to expiry, ACT/365, convention above |
| `log_moneyness` | ln(K / F) |
| `iv` | Black-76 implied vol (0.15 = 15%) |
| `vol_spread` | IV at the ask minus IV at the bid (0.02 = 2 vol points); NaN if either side cannot be solved |
| `delta`, `gamma`, `vega`, `theta` | Greeks, units above |

**Table `surface_expiries` (one row per snapshot and expiry)**

| Column | Meaning |
|---|---|
| `dte` | Calendar days to expiry |
| `regression_strikes` | Strikes used in the forward regression |
| `forward`, `discount_factor`, `t_years` | As in `vol_surface` |
| `implied_rate` | -ln(DF) / t |
| `bill_rate` | Treasury bill yield of matching maturity (FRED 1M/3M/6M/1Y CMT, linear in days), decimal |
| `half_day` | True if the snapshot date was a market half day (unreliable EOD quotes) |
| `atm_iv` | IV at ln(K/F) = 0, linear in log-moneyness between the nearest OTM put and call |
| `options` | Options with an implied vol |

**Checks printed by `vol_surface.py`.** "Stops" means the script exits with
`SANITY CHECK FAILED`. "Flags" means it prints `FLAG` and continues.
- **Stops** if repricing any option at its implied vol misses the mid by more
  than 1e-6.
- **Flags** an expiry with too few clean paired strikes for the regression. It
  gets no forward and no vols.
- **Flags** a discount factor outside (0.95, 1.001].
- **Notes (information, not a failure)** an implied rate more than 1.5
  percentage points from the Treasury bill yield of matching maturity. This
  covers every expiry. Short expiries dominate these notes because their rate is
  mostly noise: a DF error of 0.0001 at one day is a 3.65-point rate error.
- **Flags** total ATM variance (iv² × t) that falls as expiry lengthens.
- **Flags** if VIX exceeds the 30-day ATM vol by more than 40% of that vol, or
  if ATM vol is at or above VIX. The 30-day vol comes from interpolating total variance
  linearly in t. VIX should sit a few points above ATM vol: it prices 30-day
  variance from the whole OTM strip, and the put skew lifts it.
- **Flags** implied vols outside 3%–150%.
**Figures** (`data/figures/`). Each caption gives the date and the number of
options plotted. Smiles and surface show |ln(K/F)| ≤ 4 × ATM vol × sqrt(t), so
quotes stuck at the minimum price tick far in the wings don't dominate.
- `smiles_<date>.png`: computed points for six expiries (nearest 1, 7, 30, 60,
  91 and 182 days).
- `term_structure_<date>.png`: ATM vol per expiry.
- `surface_<date>.png`: the only picture that interpolates. Colours between
  points are linear interpolation on a triangulation. Triangles wider than 0.06
  in ln(K/F) are hidden, so gaps show where strikes are missing.
- `term_structure_before_after.png`: both snapshots against expiry date, so the
  same contracts line up.

No model fitting or smoothing anywhere.

**Limitations**
- **Quote timing.** EOD quotes are each strike's last NBBO when ThetaData builds
  the report, not a single synchronised snapshot. Some expiries end up with
  stale or very wide last quotes, keep no clean pairs and have no vols.
- **Calendar time includes weekends.** Seen from a Friday, the Monday expiry has
  three calendar days but about one trading day, so its vol looks low. A
  trading-time or event-weighted clock would change the short end a lot.
- **Early closes.** Expiries on half days (e.g. 2026-11-27, 2026-12-24) settle at
  a 13:00 close, but the code would treat them as 16:00. Neither appears in
  these two snapshots: they weren't listed yet.
- **Start time.** The 16:15 start matters for one-day options. Starting at 16:00
  instead changes the one-day vol by about 0.5% relative.
- **Implied rates** from a single day's quotes are noisy, and SPX implied rates
  often differ from T-bill yields. The rate notes show the comparison; they are
  not data errors.
- **Half days.** `config.HALF_DAYS` lists the early closes from NYSE's published
  calendars (ICE releases of 2022-12-21 and 2023-11-10, and nyse.com for 2026).
  Snapshots taken on those days are marked `half_day = true` in `date_list`,
  `event_moves`, `event_premium`, `vol_surface`, `surface_expiries` and
  `validation_results`. Their EOD quotes come hours after the early close, so
  treat them as unreliable.
- **Missing quotes.** A quote with ask = 0 is treated as no quote everywhere
  quotes are read: in the cleaning step shared by the event study and the
  surface, and in the VIX replication.
- **Model.** Black-76 assumes a lognormal forward. These are implied vols of
  that model, not model-free quantities. Greeks hold the forward and vol fixed.

### Interactive 3D view (`data/figures/surface_3d.html`)

**Superseded by `vol_page.py`** (see "Volatility page"). `surface_3d.py` is kept
for reference but is no longer maintained. It was built for two snapshot dates,
and its date buttons are not laid out for the current list.

```bash
.venv/Scripts/python.exe surface_3d.py        # needs vol_surface.py to have run; no downloads
```

One standalone HTML file with plotly.js embedded (about 8 MB). It opens in any
browser, offline, with no Python running. Uses the same options and plotting
window as the 2D figures (|ln(K/F)| ≤ 4 × ATM vol × sqrt(t)). The x axis is
ln(K/F), y is days to expiry, z is implied vol in %, and colour is implied vol
in % on one Viridis scale shared by both dates.

- **Smiles (points only):** one line per expiry through the computed points.
  Nothing is interpolated.
- **Surface (interpolated):** each expiry's smile is interpolated linearly onto a
  common ln(K/F) grid (step 0.005), only inside the range that expiry actually
  covers; the grid is left empty (gaps) outside it, with no extrapolation.
  Plotly shades linearly between neighbouring expiries. Black dots are the
  computed points, so you can see what is data and what is interpolation.
- **Hover:** strike, expiry, days to expiry, implied vol and delta. On dots
  these are computed values. On the surface, strike = F·e^x and delta comes from
  Black-76 at the interpolated vol, so they are labelled "interpolated".
- **Buttons** (Plotly's own): date (2026-09-10 / 2026-09-11), view (surface /
  smiles), and y axis (days, or square root of days to spread out the short
  expiries). A short script embedded in the file keeps the three choices
  combined, because Plotly 3D has no square-root axis and separate button groups
  would otherwise reset each other.

### VIX replication check (`vix_check.py`)

```bash
.venv/Scripts/python.exe vix_check.py         # 1 ThetaData request the first time (SPX monthly), then none
```

Follows the Cboe VIX Methodology (version 6.0, revised 2026-02-26) and the Cboe
Volatility Index Mathematics Methodology:
- **Eligible options:** AM-settled SPX monthlies, plus PM-settled SPXW expiring
  on Fridays, excluding SPXW on the same date as an SPX monthly.
- **Bracket method:** near term = last eligible expiry with ≤ 30 days; next term
  = the next eligible expiry. On both snapshot dates that is SPXW 2026-10-09
  (PM) and SPX 2026-10-16 (AM, third Friday). The SPX monthly is not in the
  SPXW data, so it is pulled once into `data/raw/spx_eod_vix/` (table
  `spx_eod_vix`).
- **Time:** minutes, rounded down, from 16:15 ET (the VIX close) to 09:30 ET
  for SPX AM expiries and 16:00 ET for SPXW, divided by 525,600.
- **Rates:** Treasury CMT yields from FRED (1M, 3M, 6M, 1Y, 2Y), previous
  business day as in the Cboe worked example, natural cubic spline with the
  Cboe bounds, BEY → APY → continuous.
- **Forward:** F = K + e^(RT)(C − P) at the strike with the smallest |C − P|.
  K0 = the first strike at or below F.
- **Strip:** OTM puts below K0 and calls above. Options with a zero bid or ask
  are excluded, and the walk stops after two consecutive zeros. Q(K0) is the
  average of the put and call mids. ΔK is half the gap to neighbouring
  included strikes.
- **Variance:** σ² = 2/T Σ ΔK/K² e^(RT) Q(K) − (F/K0 − 1)²/T per term,
  interpolated to 30 days in minutes.
- The script flags a difference above 0.5 points from VIXCLS.

**Differences from Cboe that we cannot remove:**
- Cboe uses Cboe-only quotes (C1) at 16:15 with its quote and index filters.
  We use ThetaData's consolidated last NBBO at report time.
- FRED has no 2-month CMT series.

### Validation on 51 dates (surface checks and VIX replication)

```bash
.venv/Scripts/python.exe validation_dates.py          # fixed date list (built once, then only printed)
.venv/Scripts/python.exe pull_validation.py --estimate # request count, no requests
.venv/Scripts/python.exe pull_validation.py           # 74 requests, ~12 min; resumable
.venv/Scripts/python.exe validate.py                  # all checks on all dates, table + figure
```

**Dates, chosen by rule before any results** (`data/validation_dates.csv`, never
rebuilt once written):
- One random trading day per calendar month, 2023-06 to 2026-10, with fixed
  seed 20261009: 41 dates.
- The 10 trading days with the highest VIX close (FRED VIXCLS): 7 in April
  2025, plus 2024-08-05 and 2026-03-27.

**Tables**
- `validation_results` (one row per date):
  - `vix_replicated`, `vix_published`, `difference`;
  - `vix_terms`: the two expiries with their forward, K0, strike count and term vol;
  - one count per check: `reprice`, `total_variance`, `iv_range`, `rate`,
    `no_forward`, `discount_factor`, `no_atm`, `atm30_vs_vix`;
  - `iv_options_outside` and `failure` (reason text).
- `validation_flags`: every flag message.
- `validation_results_before_null_fix`: the run before the one method change
  below.

**Figure:** `data/figures/vix_replication.png`.

**One method change, applied to all dates (before/after kept).** On some dates a
deep in-the-money call has a bid 0 / ask 0 quote (no market), and the code had
priced it as 0. Paired with a cheap put, that strike won the "smallest |C − P|"
search, so the forward came out far too low. Cboe excludes null quotes from the
forward search and removes them before the strip walk. A quote with ask = 0 is
now treated as null.

The results are private (see "Data and results").

### Clean mode (default in `surface_3d.html`) and clean smiles PNGs

```bash
.venv/Scripts/python.exe clean_surface.py   # fits, gap and variance checks, data/figures/smiles_clean_<date>.png
.venv/Scripts/python.exe surface_3d.py      # rebuilds the HTML with Clean and Full (raw) modes
```

- **Scope:** Friday expiries (weeklies and monthlies) from 14 days to about 6
  months. Only computed OTM vols between the 10-delta put and the 10-delta call
  (x = 1 − undiscounted call delta, from 0.10 to 0.90).
- **Clean smile:** per expiry, a least-squares cubic polynomial of implied vol in
  ln(K/F), fitted to that expiry's computed points. It is drawn only within
  their range, with the real points as markers. At most 7 expiries are shown
  (nearest 14, 28, 45, 63, 91, 126 and 168 days), each labelled.
- **Clean surface:** every fitted smile evaluated on a regular 41-point grid in
  x (delta, or strike 90–110% of forward), with one row per expiry. Light
  smoothing between expiries: at each x, total variance = ½ own value + ½ the
  straight line in time through the two neighbouring expiries. The first and
  last expiry are not smoothed, and nothing is extrapolated.
- **Hover:** the smoothed value plus the nearest computed point on the same
  expiry, both labelled.
- **Controls:**
  - Mode: Clean (default) / Full (raw). Full is the earlier view, unchanged.
  - View: Smiles / Surface.
  - Date: 2026-09-10 / 2026-09-11.
  - X axis (Clean only): Delta / Strike % of forward.
  - Y axis: days / √days.

**Method change, made once and applied to both dates.** The smoothing was first
a plain 1-2-1 average of total variance across neighbouring expiries. Because
total variance grows with time, that pulls the first and last expiries toward
their single neighbour, and it is biased when expiries are unevenly spaced. The
spacing-aware version above equals a 1-2-1 average for evenly spaced expiries.

**Limits**
- A cubic is a shape assumption. It fits these 10-to-90-delta ranges closely,
  but says nothing outside them.
- Expiries with few clean points cover a narrower delta range.
- Rows sit at the listed Friday expiries, not on an evenly spaced time grid.

## Snapshot time

**The pipeline now uses quotes at the options close: 16:15 ET, or 13:15 ET on
half days** (`SNAPSHOT_TIME`, `SNAPSHOT_TIME_HALF_DAY`), from
`option_at_time_quote`.

**Why it changed.** ThetaData's end-of-day report is built at about 17:15 ET.
Its bid and ask are the last quotes before that time, which falls after the
16:15 options close and after Cboe's extended session for SPX options. Its
quotes can therefore reflect moves made after the close. On the half day the
end-of-day report had almost no quotes at all.

**The test that decided it** (`fixed_minute_test.py`, tables
`fixed_minute_vix`, `fixed_minute_forward`, `fixed_minute_quality`). It
replicated the VIX close from each snapshot on 16 dates: the 10 stressed days
from the validation, the half day, and 5 calm days, with the method unchanged.
SPX and SPXW options trade only on Cboe, so the NBBO at 16:15 is Cboe's own
quote, the same input the index uses. Quotes at exactly 16:00 are not used.

**Effect on the event study** (`compare_snapshots.py`; end-of-day results kept as
`event_moves_eod`, `event_premium_eod`). The results are private (see "Data and results").

**Note on `max_dte`.** `option_at_time_quote` ignores `max_dte`: it returns
every listed expiry. The event study therefore requests the single expiry it
needs.

## Out-of-sample check: 2020-01 to 2023-05

The event study was extended back to 2020 with the method, cleaning rules and
limits frozen. Nothing was changed after seeing these results.

- **Run:** `stage3_periods.py`, after `build_dates.py`, `pull_chains.py`,
  `calc_moves.py` and `summarize.py`.
- **Event dates:** the BLS CPI and Employment Situation archives, and the Fed
  FOMC calendars (`fomchistorical2020.htm` for 2020, `fomccalendars.htm` for
  2021 on), merged into `event_calendar.csv` with their source. Unscheduled FOMC
  actions (2020-03-03, and Sunday 2020-03-15) are not included.
- **Half days 2020–2022:** 2020-11-27, 2020-12-24, 2021-11-26 and 2022-11-25,
  from the NYSE Group releases of 2019-12-09 and 2021-12-27.
- **Event days with no same-day SPXW expiry are left out**, with the reason
  recorded. SPXW expired only on Mondays, Wednesdays and Fridays before
  2022-05-13. That removes 15 event days: 14 CPI days and 1 FOMC day
  (2020-11-05), all on a Tuesday or Thursday. Ordinary days are affected the
  same way (201 removed), so the 2020–22 baseline uses Monday, Wednesday and
  Friday only.
- **Other skips, new period:** ordinary days where the forward was more than
  10 points from the nearest clean strike, the existing sanity check. Release
  days with the market closed: 2020-04-10 CPI, 2021-04-02 payrolls and
  2023-04-07 payrolls (all Good Friday). The last was omitted from this list at
  first and added later.

The results are private (see "Data and results").

### Exploratory, chosen after the fact

`exploratory_nfp_friday.py` recomputes the payrolls premium with a Friday-only
baseline: ordinary Fridays within the same 20-trading-day window. It was chosen
after seeing the Stage 3 results, so it is exploratory, not a test. No table,
rule or limit was changed. Its output is private.

## Rate limits

- **Options Value subscription (current):** the docs limit *concurrent*
  requests to 2 and say requests are otherwise "NOT rate-limited in any way".
  `theta.py` therefore allows 2 requests in flight (`MAX_CONCURRENT_REQUESTS`),
  starts them at least 0.5 s apart, and retries with doubling back-off on
  `RESOURCE_EXHAUSTED`, `UNAVAILABLE` and `DEADLINE_EXCEEDED`.
  `pull_chains.py` runs 2 worker threads.
- **Free tier (until 2026-10):** one request at a time, at least 3 s apart.
  The docs disagreed on the limit (20/min vs 30/min).

## Volatility page (vol_page.py)

`vol_page.py` builds one interactive page, `data/figures/vol_page.html`, with three
figures for each snapshot date: the clean 3D surface, the clean smiles, and the
at-the-money term structure. A date picker switches between dates, and a date is
drawn next to its neighbouring snapshot when one is at most
`VOL_PAGE_PAIR_MAX_DAYS` days away (so a day before an event and the event day
share one scale).

**Run**

```bash
.venv/Scripts/python.exe vol_page.py                         # the original 8 dates -> vol_page.html
.venv/Scripts/python.exe vol_page.py 2026-09-10 2026-09-11   # only these dates
.venv/Scripts/python.exe vol_page.py --events                # one page per event + index (Stage 4)
```

With no dates it covers the original 8 surface dates (`SURFACE_DATES_ORIGINAL`).
It reads the database read-only and writes only under `data/figures/`.

**To add dates**

1. `.venv/Scripts/python.exe build_surface_dates.py` writes
   `data/surface_dates.csv`. It covers every CPI, FOMC and payrolls day in the
   date list, the trading day before each, and the original 8. `config.SURFACE_DATES`
   reads this file.
2. `.venv/Scripts/python.exe fetch_surface.py` downloads the chains. It runs 2 at
   a time, can be resumed, and saves Parquet only under
   `data/raw/spxw_surface_1615/`, not in DuckDB. A failed date is recorded in
   `data/surface_fetch_errors.csv` and the run continues.
3. `.venv/Scripts/python.exe vol_surface.py` computes implied vols for every saved
   date. A failed date is recorded in `data/surface_failures.csv`. It also writes
   one summary row per date (options, expiries, 30-day ATM vol, VIX, flags,
   seconds) to the `surface_date_summary` table.
4. `.venv/Scripts/python.exe vol_page.py --events` rebuilds the event pages.

### Event pages (Stage 4)

`vol_page.py --events` writes one page per CPI, FOMC and payrolls event to
`data/figures/events/`:
- **Each event page** shows the event day next to the trading day before, at the
  16:15 close (13:15 on half days). It uses `templates/vol_page.html` unchanged,
  including Split view.
- **One copy of `plotly-strict.min.js`** sits in that folder and all event pages
  share it.
- **`index.html`** is a sortable table with one row per event: date (linking to
  the page), type, range drawn, expiries, fit gaps (max and mean), 30-day ATM vol
  before and after, the change, and flags.
- **`data/vol_event_pages.csv`** records every event, including those with no
  page, with the reason.
- **`data/surface_date_status.csv`** records every surface date: drawn, drawn
  and flagged, or failed, with the reason.

The results are private (see "Data and results").

Event labels ("CPI day", "day before CPI") come from the `date_list` table.

**Files**

- `vol_page.py`: the calculation and the checks.
- `templates/vol_page.html`: layout, styling and chart code. The script fills in the data.
  The page has a **Split view** button: it puts two dates side by side, each with its own
  date picker, surface and smiles, and the term-structure chart below compares the two
  chosen dates. "Same vol scale" keeps both sides on one scale and "Link rotation" turns
  both surfaces together. All of this lives in the template; `vol_page.py` is unchanged.
- `vendor/plotly-strict.min.js`: the charting library (Plotly 2.35.2, strict
  build). It is copied next to the page so the page opens offline. Keep the two
  files in the same folder.

**Method**

- Friday expiries from `CLEAN_MIN_DTE` to `CLEAN_MAX_DTE` days.
- x axis: call-equivalent delta N(d1), from the 10-delta put to the 10-delta
  call at most.
- **Delta range per date.** Each date's range is the widest one, never wider
  than 10-delta put to 10-delta call, that at least half of the eligible
  expiries cover. An expiry is eligible when it has `VOL_PAGE_MIN_POINTS`
  computed options in the 10-to-10 range plus `VOL_PAGE_FIT_PAD`. The range ends
  are taken from the expiries' own coverage, rounded inward to 0.01.
- **Only expiries that cannot cover that range are left out**, and the page
  names them.
- **When the range is narrower than 10-to-10**, the standfirst and the Figure 1
  caption say so, and the axis ticks and the numbers table follow the range.
  The printed table has a "range drawn" column.
- **Smile:** a cubic in log-moneyness per expiry, fitted to that expiry's
  computed implied vols (those within the range plus the pad). No smoothing
  across expiries, unlike `clean_surface.py`.
- **No extrapolation.**

**Checks printed on every run**

- Mean and largest gap between the fitted smiles and the computed options, in
  vol points. A date is flagged when the largest gap exceeds `CLEAN_GAP_LIMIT`.
- Number of grid points where total variance falls as expiry lengthens. Any
  above zero is flagged.
- Half-day snapshot dates are marked on the page and in the output.

**Limits**

- **Checked on the 8 dates in `SURFACE_DATES`**, all drawn over the full 10-to-10
  range.
- **The stressed pair 2025-04-09/10 is drawable over the full range only because
  of the surface's vol-spread rule.** With the price-spread rule alone, their
  call quotes stop well short of the 10-delta call.
- **Stressed dates can be flagged.** The largest fit gaps sit at the range ends,
  on options the vol-spread rule admitted, whose mids are uncertain by about a
  vol point. A cubic also fits steep stressed smiles less well. The limit was
  not changed to clear a flag.
- Each page embeds its dates' numbers (about 40 KB per date). That is why the
  surface dates are split into one small page per event plus an index,
  rather than one page. The original page embeds every date's numbers, so it grows
  with the number of dates.
- The page shows figures derived from ThetaData quotes licensed for personal
  use. Do not publish it.

## Skew and vanna (Stage 5)

From the existing event surfaces: no downloads, and no change to the method,
cleaning rules or limits. **These definitions were written before any metric was
computed and are not changed after seeing results.**

### Definitions

- **Dates.** Every surface date that `vol_page.py` draws (event days and the
  days before). Metrics come from the page's fitted smiles: a cubic in ln(K/F)
  per Friday expiry, 14 days to about 6 months, evaluated on the date's delta
  grid. Between grid points, IV is interpolated linearly in delta. A metric
  whose delta lies outside the date's drawn range is left empty (no
  extrapolation).
- **Delta.** Call-equivalent delta N(d1), undiscounted, as on the page:
  - **ATM** is N(d1) = 0.50;
  - **the 25-delta put** is 0.75 and **the 25-delta call** is 0.25;
  - **the 10-delta put** is 0.90.
- **At one expiry, in vol points:**
  - ATM vol = IV(0.50);
  - 25-delta risk reversal **RR25** = IV(25-delta put) − IV(25-delta call) =
    IV(0.75) − IV(0.25). Positive means puts are dearer than calls;
  - **put wing** = IV(10-delta put) − ATM = IV(0.90) − IV(0.50).
- **At 30 days.** For each delta separately, total variance w = IV² × days to
  expiry is interpolated linearly in days between the drawn Friday expiries
  either side of 30 days (or the expiry at exactly 30 days). Then IV30 =
  sqrt(w30 / 30), and the three metrics are formed from these IVs. If no drawn
  expiry lies on one side of 30 days, the metric is empty.
- **Shortest expiry.** The same three metrics at the shortest drawn expiry, with
  its days to expiry. No interpolation, so this expiry usually differs between
  the day before and the event day.
- **Table `surface_metrics`:** one row per drawn date, with the six metrics, the
  shortest expiry and its days, and whether the date is flagged.
- **Flagged date:** on the page, the largest fit gap is above 0.5 vol points, or
  total variance falls with longer expiry.
- **Event change.** For each event with both days drawn: metric on the event day
  − metric on the day before, in vol points.
- **Forward return.** The forward of one expiry on both days: the drawn Friday
  expiry nearest 30 days on the event day that also has a forward on the day
  before. Return = F(event day) / F(day before) − 1, in %. F is the regression
  forward from `surface_expiries`.
- **Table `event_skew_changes`:** one row per event with both days drawn.
- **Summaries:** by event type (CPI, NFP, FOMC; a day with two events counts
  under both) and by period of the event date (2020-01..2023-05,
  2023-06..now, combined). For each change: n, median, mean, and the share of
  events where it is positive.
- **Spot-vol relationship:** ordinary least-squares slope of the 30-day ATM
  change (vol points) on the forward return (%), so vol points per 1% move,
  together with the Pearson correlation and n.
- **Reported twice:** all events, and excluding events where either day is
  flagged.
- **Vanna.** A new column `vanna` in `vol_surface`. Black-76 on the forward,
  consistent with the existing Greeks:
  - vanna = ∂²V/∂F∂σ = −DF × n(d1) × d2 / σ, the same for calls and puts;
  - d1 = (ln(F/K) + σ²t/2) / (σ√t), d2 = d1 − σ√t, and n is the standard
    normal density.
  - **Units:** change in delta (per 1 index point of F) per 1 vol point, so the
    formula above divided by 100. Equivalently, the change in vega (per vol
    point) per 1 index point of F.
  - Option-level only. No dealer or open-interest exposure is computed.

### Results

- **Run:** `.venv/Scripts/python.exe skew_metrics.py`, after `vol_surface.py`.
  The full tables go to `data/skew_summary.txt`.
- The results are private (see "Data and results").

## Stage 5b: controls for the mechanical effects

Two controls, added as new, separately labelled measures. **Stage 5's
definitions and tables (`surface_metrics`, `event_skew_changes`) are unchanged.**
These definitions were written before anything below was computed.

### Definitions

- **Fixed-strike 30-day ATM vol (new; Stage 5's 50-delta ATM is unchanged).**
  - **The strike.** On the day before, each of the two drawn Friday expiries
    either side of 30 days has a 50-delta strike: the fitted smile's strike at
    call-equivalent delta 0.50, interpolated linearly in delta along its grid.
    K* is the linear interpolation of those two strikes in days to 30 days.
  - **The vol at K\*.** On each day, IV at K* is read from each of that day's
    two bracketing expiries by interpolating along the fitted smile in strike.
    If K* lies outside an expiry's drawn strikes, the value is empty (no
    extrapolation). The 30-day value combines the two expiries in total
    variance, as in Stage 5: w = IV² × days, linear in days, IV30 = sqrt(w30/30).
  - **The change** is IV30 at K* on the event day minus IV30 at K* on the day
    before, in vol points. The strike is the same on both days and the maturity
    is 30 days from each day.
  - **The spot-vol slope** uses this change in place of the 50-delta change:
    ordinary least squares on the forward return (%), as in Stage 5.
- **Ordinary-day control.**
  - **Eligible pairs.** Two consecutive trading days (day 1, day 2) from the
    date list. Neither day, nor the trading day after day 2, is a CPI, FOMC or
    payrolls day. Day 2 must be a date the event study could measure: event
    type NONE, with trade date = day 1.
  - **Selection.** 100 pairs, spread evenly in time. The eligible pairs from
    2020-01 to now are split, in date order, into 100 equal-sized consecutive
    groups, and one pair is drawn from each group with a fixed seed
    (`CONTROL_SEED`). The list is written once to `data/control_pairs.csv` and
    never redrawn.
  - **Plan cap.** If the pairs would add more than 210 new surface dates, stop
    and ask.
  - **Processing.** The surfaces are fetched and computed with the existing
    scripts and rules. The same Stage 5 metrics are computed for each day, and
    their changes from day 1 to day 2, with day 1 standing in for "the day
    before" and day 2 for "the event day". The forward return uses the same
    rule as for events.
  - **Tables.** `control_surface_metrics` and `control_pair_changes`. Fixed-strike
    changes for events go in `event_fixed_strike`.
- **Comparison.**
  - **Groups.** CPI, NFP and FOMC events (a day with two events counts under
    both), and the ordinary pairs. All periods are combined.
  - **Measures.** The median change of 30-day ATM (50-delta), 30-day ATM
    (fixed-strike), 30-day RR25 and the 30-day put wing, with the share
    positive and n.
  - **Event minus ordinary.** The median of the event group minus the median of
    the ordinary pairs.
  - **Bootstrap 90% interval.** Events and ordinary pairs are resampled
    independently with replacement, 5,000 times, with a fixed seed. The
    interval runs from the 5th to the 95th percentile of the difference in
    medians.
  - **Reported twice:** all, and excluding any event or pair with a flagged day
    (Stage 5's flag definition).
- **"Survives both controls"** is decided by this rule, fixed now:
  - for 30-day ATM: the event-minus-ordinary difference of the **fixed-strike**
    change has a 90% interval that excludes 0, both with and without flagged
    dates;
  - for RR25 and the put wing: the event-minus-ordinary difference has a 90%
    interval that excludes 0, both with and without flagged dates.
- **Output.** All figures in the README are written by the script from its
  output (`data/stage5b_summary.txt`, `data/stage5b_readme.md`), not typed by
  hand.

### Results

Generated by `stage5b_controls.py` into `data/stage5b_summary.txt`. The results are private (see "Data and results").

## Stage 7: surfaces for every trading day since 2020

Same method, cleaning rules and limits as Stages 4–5; none were changed.
`data/findings_v1.md` and the page templates were not touched, and no pages
were built for the new dates.

### What was run

- **Scope:** every trading day in the date list from 2020-01-02 to 2026-10-09.
  Days not yet in `vol_surface` were added in batches of up to 100
  (`data/stage7_todo.csv`).
- **Each batch** (`stage7_all_dates.py --batch N`, in its own process):
  1. fetch the missing chains with `fetch_surface.py` and the existing coverage
     rule (a missing expiry within 14 days fails the date; 15–30 days is a
     warning);
  2. compute the new dates with `vol_surface.py` and append their rows to
     `vol_surface`, `surface_expiries` and `surface_date_summary`;
  3. compute their Stage 5 metrics with the existing definitions and append them
     to `surface_metrics`;
  4. check the row checksums below.
  Steps 2–3 run in one transaction, so an interrupted batch writes nothing.
  Saved chains and computed dates are skipped on rerun.
- **Code changes, needed to append rather than rebuild (no change to results):**
  - `vol_surface.py`: the per-date loop moved into `compute_dates`; new
    `append_dates` inserts rows for dates not yet in the tables.
  - `vol_page.load` takes an optional list of dates.
- **Follow-up (2026-10-15), so either script can be run alone without dropping
  rows (verified 2026-10-15: both rerun, all 7 Stage 7 checksums match, and the
  whole tables are identical to a backup taken before the rerun):**
  - `vol_surface.py` now covers every date in `config.SURFACE_DATES` and
    `data/all_dates_status.csv` (`config.all_surface_dates()`). It appends
    only dates not yet stored and never rebuilds existing rows. A full
    rebuild would change every row's checksum through the `seconds` timing
    column, even with no result changed.
  - `skew_metrics.py` now recomputes `surface_metrics` for every drawn date
    except the Stage 5b control dates, 100 dates at a time. The event changes
    and `data/skew_summary.txt` still use event days and the days before only,
    so the frozen summary file is unchanged.
- **Checksums:** before the first fetch, the row count and
  `sum(hash(row))` of every existing row were saved to
  `data/stage7_checksums_before.csv` (`vol_surface`, `surface_expiries`,
  `surface_date_summary`, `surface_metrics` on the dates present before
  Stage 7; `event_skew_changes`, `event_fixed_strike`, `control_pair_changes`
  in full). They were rechecked after every batch and at the end.

The results are private (see "Data and results").

### Interruptions (none changed any stored row)

- Claude Code's background tasks were stopped for low memory several times; the
  last batches were run from a normal PowerShell window.
- ThetaData requests have no timeout, and twice one hung with no reply. The
  batch was stopped and rerun.
- Network drops, twice: chains failed with "failed to connect to all
  addresses", then the FRED bill-rate download failed (`getaddrinfo failed`).
  The batch transaction rolled back, and the rerun fetched the missing chains.
- `.env` must hold the line `THETADATA_API_KEY=...`. A bare key works only when
  the variable is already set in the shell.

### Caveats

- Stage 5b control-pair dates are in `control_surface_metrics`, not in
  `surface_metrics`.
- `data/surface_fetch_errors.csv` held only the last fetch run, so it was
  overwritten by each Stage 7 batch. Since 2026-10-15, `fetch_surface.py`
  appends to it instead, with a `run_date` column (date and time of the run).
  Rows from earlier runs are kept, and the existing rows have `run_date`
  blank. The full fetch log for Stage 7 is `data/all_dates_fetch_errors.csv`,
  and surface-step failures go to `data/all_dates_surface_failures.csv`.

## Shared API (product stage 1)

A read-only API over the database for the pages in `docs/prd.md`. It lives in
`api/`, separate from the pipeline, and nothing in the pipeline was changed.

### Endpoints

Every endpoint requires a token, and every response has the same envelope:
`source` (tables or files), `n`, `period`, `params`, `caveat` and `data`.

| Endpoint | Returns | n |
|---|---|---|
| `GET /events?type=&period=` | Stage 3 summary per event type, the event rows, and the event days not measured | events |
| `GET /events/{date}` | One event: moves, baseline, premium | 1 |
| `GET /events/index?type=&period=&exclude_flagged=&include_ordinary=` | Stage 5b summary, event rows (optionally with the 100 ordinary pairs), and events missing a day with the reason | rows |
| `GET /events/{date}/skew` | Skew numbers for the day before and the event day, and their change | 2 |
| `GET /surface/{date}` | Clean smiles, term structure, metrics and per-option vanna for one day | expiries |
| `GET /dates` | Every drawn date, marked event day / day before / ordinary, plus the 7 failed dates | drawn dates |
| `GET /findings` | `data/findings_v1.md`, section by section, with its SHA-256 | sections |
| `GET /events/next?type=` | The next scheduled release per type (from `event_calendar.csv`), what past events of that type show, and "priced now" (added in product stage 3, see below) | types with an upcoming date |

- **Inputs:**
  - `type`: `CPI`, `NFP` (or `payrolls`), `FOMC` or `all` (the default).
  - `period`: `2020-01..2023-05`, `2023-06..now` or `both` (the default).
  - Dates are `YYYY-MM-DD`.
  - `exclude_flagged` and `include_ordinary` are `true` or `false`.
  - Bad input gets 400 with a message. A valid date with no data gets 404 and
    says why.
- **Units:** in the field names, as in the frozen files: `_pct` (%), `_pp`
  (percentage points), `_pct2` (%², straddle units) and `_volpts`. Values are
  unrounded.
- **Same numbers as the pipeline:**
  - `/surface` uses `vol_page.build_date`, the function that drew the
    prototype pages.
  - The index summary uses `stage5b_controls.compare` and `survives` on the
    tables in their stored order, so the seeded bootstrap reproduces
    `data/stage5b_summary.txt`. It is computed once per server start, which
    takes about a second.
- **Decisions (2026-10-15):**
  - `type=all` returns the three per-type summaries side by side, with no
    pooled summary. A CPI+FOMC day counts under both, as in Stage 3.
  - With a single `period`, `/events/index` filters the rows and returns an
    empty summary with a note, because the Stage 5b summary pools both periods.
  - There are no "small, not reliable" labels yet, only Stage 5b's
    `survives_both_controls` (null for measures outside the survival rule).
  - Day status comes from `data/all_dates_status.csv`.
  - Vanna is change in delta per vol point (README, "Skew and vanna").
- **Never returned:** the quote tables (`spxw_*`, `spx_*`), `mid`,
  `vol_spread`, and any price, bid or ask. Only derived numbers are returned.
- **Read-only:** DuckDB is opened with `read_only=True`. It cannot be opened
  while the pipeline is writing to the database, so don't run both at once.

### Auth

- Send `Authorization: Bearer <token>`. The token is accepted in this header
  only, never in the URL.
- A missing or wrong token gets 401. `/docs`, `/redoc` and `/openapi.json` are
  switched off, so there is no endpoint without auth.
- For now the token is a local dev token: `MACRO_WIRE_DEV_TOKEN` in `.env`,
  loaded the same way as the ThetaData key. It is never printed or logged. If
  it is missing, the server says so at start-up and refuses every request.
- **Pluggable:** `app.state.authenticator` is any object with
  `verify(token) -> user or None`. A Supabase JWT authenticator can replace
  `DevTokenAuth` in `api/auth.py` later.

### How to run (localhost only)

```bash
.venv/Scripts/python.exe -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

Keep `--host 127.0.0.1`, so nothing outside this PC can reach it; nothing is
deployed. Stop it with Ctrl+C.

To try it without typing the token (so it never lands in the shell history),
read it from `.env` into a variable that is not echoed:

```bash
t=$(grep '^MACRO_WIRE_DEV_TOKEN=' .env | cut -d= -f2-)
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $t" "http://127.0.0.1:8000/events?type=CPI&period=2023-06..now"
unset t
```

It should print 200.

### Tests

```bash
.venv/Scripts/python.exe -m pytest -q
```

The tests use their own token, never the one in `.env`, and read the real
database read-only. They check:
- every `/events` summary (each type, each period, and `all`) against
  `data/stage3_periods.txt`, and the premiums, positive-premium counts,
  combined table and the 15 days not measured against `data/findings_v1.md`;
- the `/events/index` summary against `data/stage5b_summary.txt`, row for row
  in its printed format, for all events and excluding flagged events, plus the
  survival table and the filters;
- that the event counts in the caveats match the returned summaries;
- `/surface/2026-09-10` and `/surface/2026-09-11` against the data in
  `data/figures/events/2026-09-11_CPI.html`: grid, smiles, strikes, computed
  options, term structure, fit numbers and skew metrics;
- refusal without a token, with a wrong token or scheme, and with the token in
  the URL; 400 for bad input and 404 with a reason; the envelope; and that no
  quote field appears in any response.

Result on 2026-10-15: 72 passed (the API tests now number 92 with `/events/next`).

## Terminal and login (product stage 2)

The terminal frame (`docs/prd.md`, Page 5) and the login (Page 7). It lives in
`web/` and talks to the API through a proxy. The API, the pipeline and the
data are unchanged. Page 1 (event study, product stage 3), Page 2 (Vol surface, product
stage 4) and Page 3 (Event index, product stage 5) are built, see below, and the AI tutor (product stage 6).

### How to start (two windows, both on localhost)

```bash
.venv/Scripts/python.exe -m uvicorn api.main:app --host 127.0.0.1 --port 8000
.venv/Scripts/python.exe -m uvicorn web.server:app --host 127.0.0.1 --port 8080
```

Then open <http://127.0.0.1:8080>, create an account and log in. Stop each
server with Ctrl+C. While the API is running it holds a read-only connection
to the database, so stop it before a pipeline run that writes.

### How it fits together

- **The browser only talks to the web server** (port 8080).
  `GET /api/<path>` is forwarded to the API on `127.0.0.1:8000`, and only for
  the API's endpoints. Because everything is on one origin, the API needs no
  CORS changes.
- **The API token stays on the server.**
  - With a valid session, the proxy adds `Authorization: Bearer <dev token>`.
    The token is read from `.env` the same way as in `api/auth.py`.
  - The browser never receives the token, and it isn't printed or logged.
  - A logged-out request is refused before it reaches the API.
  - If the API refuses the server's token, the page shows a 502 that names
    `MACRO_WIRE_DEV_TOKEN`; it doesn't log you out.
- **Pluggable login.** `web/auth.py` defines `AuthProvider` (sign up, log in,
  log out, current user, delete, password reset, and the API token for a
  user). `DevAuthProvider` is the local version. A Supabase provider can
  replace it later; then the proxy passes on each user's JWT instead of the
  dev token.
- **Local dev login:**
  - Sign up with an email, a password of at least 10 characters (stored as
    salted PBKDF2-SHA256) and the terms box, which is required: education
    only, no redistribution of data.
  - Accounts are confirmed at once, because there is no mail server locally.
  - Password reset answers "comes with Supabase".
  - The session is a random value in an HttpOnly, SameSite=Strict cookie, and
    only its SHA-256 is stored. Sessions last 12 hours.
  - Anything that changes state must be same-origin JSON.
- **Per user** (`web/var/users.sqlite`, git-ignored, separate from DuckDB):
  - email, password hash, terms accepted;
  - `plan`: `student` or `tutor_plus`;
  - tutor message counters by UTC day and month;
  - layout: whether Split view and the tutor were open.

  The limits are in `web/settings.py`, 5 a day for Student and 300 a month
  for Tutor plus, but they are not enforced until the tutor stage. There are
  no payments, Stripe or Supabase accounts yet.

### The terminal

- **Top bar:** SPX, the latest close (from `GET /dates`), and the account menu
  (email, plan, tutor messages used, log out, delete account).
- **Left navigation:** Event study, Event index, Vol surface, Help. The tutor
  is a panel, not a page (see "Tutor panel: sessions and width" below).
- **Main area:** one pane, or Split view with any two pages. Each pane keeps
  its own page, dates and filters, and the active pane has a cobalt top edge.
- **Tutor panel:** opens on the right. For now it is a placeholder that shows
  what the tutor will see: the active pane's page, dates and filters.
- **Footer:** "Education only, not advice" and the data sources.
- **The URL holds the whole view,** so a copied link reopens it, including
  after a login. For example:
  `/terminal?a=event-index&a.type=CPI&b=vol-surface&b.d1=2026-09-10&b.d2=2026-09-11&split=1&tutor=1&active=b`.
  - Invalid values fall back to the defaults.
  - Filters that two pages share (type, period) carry over when a pane
    switches page.
  - If the URL doesn't say whether Split view and the tutor are open, the
    saved layout decides.
- **Keyboard shortcuts** (off while typing in a field; Tab moves focus
  normally):

  | Key | Action |
  |---|---|
  | `1`–`4` | Open that page in the active pane |
  | `S` | Toggle Split view |
  | `T` | Open or close the tutor |
  | `P` | Switch the active pane |
  | `[` / `]` | Previous / next date on the Vol surface page. Date 2 moves one drawn date, and date 1 becomes the drawn date before it |
  | `?` | Show the shortcuts |

- **Phone width** (720 px and below): the navigation becomes a strip, the panes
  stack, and the tutor opens full screen.
- **Look:**
  - Inter for text, Newsreader for headings, IBM Plex Mono for
    numbers;
  - cream paper, ink text and one cobalt accent (`#1F3A9E`), with no
    ornaments;
  - the fonts are saved in `web/static/fonts/` with their SIL Open Font
    Licence texts, so the terminal makes no outside requests.
- **Front end:** plain JavaScript modules, no build step
  (`web/static/js/`: `app.js` for the frame and shortcuts, `state.js` for the
  URL state, `pages.js` for the placeholders, `api.js` and `login.js`).

### Tests

`.venv/Scripts/python.exe -m pytest -q` runs the API tests and the web tests
(`tests/web/`). The web tests use a temporary user store and a test token, and
send proxied requests to the real API in-process (read-only). They cover:
- sign-up rules (the terms box, email, password length, duplicates);
- log in and log out, and that a session is dead after logout;
- the cookie flags, and that passwords and sessions are stored hashed;
- deleting an account, and password reset answering "Supabase";
- refusing cross-origin and non-JSON requests that change state;
- the proxy: logged-out requests stopped before the API, the token added
  server-side and never shown to the browser, only the API's endpoints forwarded,
  and clear 502s;
- plan values, the day and month counter reset, the saved layout, the login
  redirect that keeps the view, static files and fonts, and security headers.

Result on 2026-10-15: 105 passed (72 API, 33 web). The terminal was also
checked in headless Edge on this computer:
- desktop Split view with the tutor open; all three fonts loaded;
- the shortcuts `1`–`5`, `S`, `T`, `P`, `[` and `?`, and that keys are ignored
  in a field;
- a copied URL reopening the same view, and a logged-out redirect to login;
- at 390 px: panes stacked with their content inside them, no sideways scroll,
  and the tutor full screen;
- no console errors.

## Event study page (product stage 3)

Page 1 of `docs/prd.md`, inside the terminal. Open it with `1`, or with
`/terminal?a=event-study&a.type=CPI&a.period=both`.

### What it shows (top to bottom)

1. **Controls:** event type (all, CPI, payrolls, FOMC) and period (both,
   2020-01 to 2023-05, 2023-06 to now). They write to the URL.
2. **Next event panel** (`GET /events/next`), described below.
3. **Headline numbers:** the median implied move, median baseline, median
   premium, median variance premium and n, with one column per type. "All"
   shows the three types side by side, with no pooled number.
4. **Figure 1:** the implied and actual move for each event over time, with
   the baseline as a line.
5. **Figure 2:** each event's premium by type, with the median from the API
   marked and labelled.
6. **Event table:** sortable, newest first, with a link to each event's
   two-day Vol surface page. In Split view the link opens in the other pane.
   A note under the table gives the days left out and why: 15 in all (14 CPI,
   1 FOMC), with no same-day SPXW expiry.
7. **Caveats:** n per type, and whether the premium held, quoted word for
   word from `findings_v1.md` (Finding 1.2 for CPI and FOMC, Finding 1.3 for
   payrolls). The page never says "did not hold" in its own words.
8. **Definitions:** the PRD's five terms. They are static until
   `GET /definitions` arrives in the tutor stage.

Every figure and headline shows n and its source tables, and each figure has
its numbers in a fold underneath. The browser calculates nothing: every
number comes from `GET /events`, `GET /events/next` or `GET /findings`; the
page only formats and sorts.

### `GET /events/next` (read-only; no new tables)

- **Next date:** the next date on or after today (New York date) for each
  type, from `event_calendar.csv`, with the countdown in days. The panel shows
  the soonest event of the selected type, or of all three for `type=all`.
- **Priced now:** the stored implied move from `event_moves`, but only when a
  row exists whose day-before 16:15 snapshot is on or before today. There is
  no look-ahead, and the actual move is never returned. Otherwise it says
  "not priced yet". The data ends at the 2026-10-09 close, so every upcoming
  event is "not priced yet" until a later stage adds a daily day-before
  snapshot.
- **In the past:** for the same type, per period, the median implied move,
  median actual move, the share of days where the actual move was smaller
  than priced, and n. The medians match `stage3_periods.txt`, and the share
  is 100% minus Stage 3's "actual > implied" (there are no ties).
- **After the event, in the past:** the Stage 5b rows for fixed-strike 30-day
  ATM, RR25 and put wing (event, ordinary pairs, the difference, the 90%
  interval, and whether it survives both controls), from the same frozen
  summary as `/events/index`.
- **Wording:** "in the past" only. The caveat is "History, not a forecast.
  Education only, not advice."

### Event calendar update (2026-10-15)

37 rows were added to `event_calendar.csv`, each with its source URL. No
existing row and no stored table changed.
- **CPI:** 2026-10-14 (September CPI; it was missing, and the data ends
  before it), then 2026-11-10 to 2027-12-10. Source: the BLS CPI schedule,
  checked against the BLS monthly schedules for October to December 2026.
- **Payrolls:** 2026-11-06 to 2027-12-03. Source: the BLS Employment
  Situation schedule.
- **FOMC:** the 2027 decision days, the second day of each meeting, from the
  Fed's calendar. The 2026 rows already in the file match it.

`build_dates.py` keeps only calendar events inside the downloaded trading
days, so the future rows enter `date_list` only once those days have closes.
2026-10-14 will join on the next pipeline rebuild.

### Other changes

- The web server's security policy now allows inline styles, because Plotly
  sets them. Scripts are still limited to the site itself. Plotly 2.35.2 (the
  strict build) is served from `web/static/vendor/`.
- `web/settings.py`: `MW_USERS_DB` and `MW_API_BASE` can override the user
  store and the API address, for the page tests.
- API caveats give one number when every count is the same ("78 events per
  type", not "78 to 78").

### Tests

`.venv/Scripts/python.exe -m pytest -q`: 146 passed on 2026-10-15 (92 API,
54 web). New in this stage:
- **`tests/test_next_event.py`:**
  - the next date per type at fixed "today" dates, including an event day and
    after the last calendar date;
  - today's answer (FOMC 2026-10-28, NFP 2026-11-06, CPI 2026-11-10, none
    priced);
  - "priced now" equal to the stored implied move and nothing else;
  - no look-ahead;
  - "in the past" matching `stage3_periods.txt`, and "after the event"
    matching the frozen Stage 5b summary;
  - the wording.
- **`tests/web/test_event_study_page.py`:** headless Edge. It starts the API
  and web server on test ports (18000, 18080) with a test token and a
  temporary user store, opens the page for every type and period, and reads
  the numbers the page displays. It checks:
  - the headline numbers match `stage3_periods.txt`, and the premiums and n
    match `findings_v1.md`, text for text;
  - every figure and headline shows n and a source;
  - the table, its links and the left-out note;
  - the findings quotes are word for word;
  - the next-event panel;
  - Split view with two differently filtered panes, and phone width;
  - no console errors.

  It is skipped if Edge or Node is missing. The harness
  (`tests/web/browser_harness.py`, `tests/web/browser/cdp_visit.mjs`) drives
  Edge through its DevTools protocol, with no extra packages. Afterwards it
  stops only the Edge processes started with its own test profile.

## Vol surface page (product stage 4)

Page 2 of `docs/prd.md`, ported from the prototype (`templates/vol_page.html`,
`data/figures/events/`) to the API. Open it with `3`, or with
`/terminal?a=vol-surface&a.d1=2020-03-10&a.d2=2020-03-11`. Without dates it opens
on the latest event pair: 2026-10-01 and 2026-10-02, the day before payrolls
and payrolls day.

### What it shows

1. **Header:** each date with its label, in the prototype's wording ("CPI
   day", "day before CPI", "FOMC decision day and day before payrolls", or
   "ordinary day"). A note gives the range drawn, a half day, and a flag with
   its reason.
2. **Picker:**
   - Date 1 and Date 2 lists, grouped by year, each with ‹ › buttons.
   - Event days and the days before them are marked ◆ with their label, and
     flagged dates say "(flagged)". Only drawn dates are offered.
   - A jump list of the event pairs and the 100 ordinary control pairs.
   - Previous and next event pair buttons.
3. **Options:**
   - Both days, Date 1 or Date 2;
   - same vol scale and link rotation (both on by default);
   - show computed options (off by default);
   - reset view.

   All of these are kept in the URL (`d1`, `d2`, `view`, `same`, `link`,
   `pts`).
4. **Skew numbers:** 30-day ATM vol, the 25-delta risk reversal and the put
   wing for each day, from `/surface` (`surface_metrics`, or
   `control_surface_metrics` for control dates), and the change between
   them. Where the change comes from depends on the pair:
   - **Event pair:** the change and the fixed-strike change are the stored
     numbers (`GET /events/{date}/skew`).
   - **One of the 100 control pairs:** the stored numbers from
     `control_pair_changes` (through `/events/index?include_ordinary=true`).
   - **Any other pair:** the change is Date 2 minus Date 1, worked out in the
     browser. This is the only calculation the page makes. The fixed-strike
     row says "only for event and control pairs".
5. **Figure 1, the surface:** the clean view, in 3D, one per day.
   - Each expiry's smile is drawn as a line.
   - With "computed options" on, the options behind the smiles are shown as
     points, and their hover gives expiry, strike, delta, IV and vanna (delta
     per vol point).
   - A flagged day shows the flag next to the chart.
   - Underneath: the fitted IVs at the grid's own deltas (10Δ put … ATM …
     10Δ call, not interpolated), and a table of computed options with vanna
     for a chosen expiry.
   - Without 3D graphics it falls back to a flat contour map, and the caption
     says so.
6. **Figure 2:** the smiles for about 7 expiries against delta (the
   prototype's targets: 14, 28, 45, 63, 91, 126 and 168 days).
7. **Figure 3:** the term structure for both days, with the numbers
   underneath. They are lined up by expiry date when the days are close
   together, and by days to expiry when they are far apart.
8. **Flags and caveats:** fit gap, partial range, expiries left out, "One
   event proves nothing on its own", and "Education only, not advice".

**No split inside a split.** The two days sit side by side only when the
pane is at least 980 px wide (a CSS container query on the pane). In the
terminal's Split view, or on a phone, they stack: Date 1, then Date 2.

**Differences from the prototype (agreed 2026-10-15):**
- the terminal palette (a cream-to-cobalt surface, Date 1 in ink, Date 2 in
  cobalt);
- the table under Figure 1 shows the fitted IV at the grid's own deltas
  instead of an interpolated 25 delta;
- no dark mode and no local storage, because the URL holds the state;
- `[` and `]` move one drawn date, as before.

### Other changes in this stage

- `web/static/js/lib.js` now holds the helpers both pages use (elements, the
  cached API reads, formatting, loading Plotly once). The terminal frame
  reads the date list through the same cache, so it is fetched only once.
- `app.js`: a Vol surface pane without dates starts on the latest event pair.
- Test harness:
  - each Edge run gets its own DevTools port;
  - the driver's errors are reported;
  - pages get up to 90 seconds to draw;
  - visits can click (`actions`) and pass Edge flags (`edge_args`);
  - after each run, clean-up repeats until no Edge process with the test
    profile is left.

### Tests

`.venv/Scripts/python.exe -m pytest -q`: 162 passed on 2026-10-15 (92 API,
70 web). New in this stage: `tests/web/test_vol_surface_page.py` (headless
Edge, 16 tests).
- **Against the prototype pages, field by field,** for 4 event pairs
  (2020-03-10/11 CPI, 2025-04-09/10 CPI, 2026-09-10/11 CPI, 2020-11-04/05
  FOMC). The test reads the page's own charts and text:
  - the delta grid, and each expiry's date, days, fitted IVs and strikes;
  - every computed option (delta, IV, strike);
  - the term structure for both days;
  - the label, range, half day, fit numbers and point counts;
  - the prototype's metrics sentence, word for word;
  - total-variance drops and expiries left out.
- **Against the API:** every computed option's vanna, and the skew metrics.
- **Flags:** shown next to the chart for 2020-03-10, 2020-03-11 and
  2025-04-09, and not for 2025-04-10 or 2026-09-11.
- **Pairs:** an event pair and a control pair use the stored changes; another
  pair shows the difference and no fixed-strike change.
- **3D graphics turned off:** contour maps, and the caption saying so.
- **The picker:** ◆ labels and flagged marks, the drawn dates offered, the
  default latest event pair, the previous and next pair buttons, and `]`.
- **Layout:** Split view (days stacked in each half pane), phone width (all
  picker controls on screen, no sideways scroll), and a wide single pane
  (days side by side).
- **Clean-up:** no Edge process with the test profile is left afterwards.

## Fix: no surfaces drawn (2026-10-15)

**What was wrong.** The servers running on ports 8000 and 8080 had been
started before the latest code changes. A server keeps the code it started
with, so:
- **The web server** still sent the old security policy (`style-src 'self'`).
  That blocked Plotly's inline styles; Plotly crashed while starting
  (`Cannot read properties of null (reading 'insertRule')`), and no chart
  could be drawn, in a single pane or in Split view.
- **The API** predated `GET /events/next`, so `/api/events/next` returned
  400.
- **The page** then failed silently: `P.purge` on a missing Plotly left
  empty chart boxes with no message.

The `/api/surface` requests, WebGL and the page code itself were fine. With
freshly started servers, both surfaces draw in a normal (non-headless) Edge
window, side by side in one pane and stacked in Split view.

**What changed.**
- `codecheck.py`: a server marks its responses with `X-MW-Code-Changed`
  (`api`, `web`, or both) when a `.py` file in its folders was saved after it
  started. The web server passes the API's mark through. The terminal then
  shows a banner: restart both servers.
- A chart that cannot be drawn now says so in its box ("This chart could not
  be drawn: …; if the code was updated recently, restart both servers"), on
  both pages. `loadPlotly` reports a library that loads but does not start.
- The frame lays out top to bottom with flexbox, so the banner fits above the
  workspace.
- Test harness: `headed` (a normal, visible Edge window, used for this
  debugging) and `block` (URLs to block, for example Plotly).

**Tests added** (168 pass):
- `test_surfaces_draw_in_a_single_pane_and_in_split_view`: every surface must
  be a drawn 3D scene with a canvas of real size, side by side in a single
  pane and stacked in Split view, with no exceptions. Run once against the
  old security policy, it fails with the same error the browser showed.
- `test_charts_say_so_when_plotly_cannot_start`: with Plotly blocked, every
  chart box shows the message instead of staying blank.
- `tests/web/test_code_check.py`: fresh servers send no mark; old ones are
  named, with the API's mark passed through the proxy.

**After any code update, restart both servers** (Ctrl+C in each window, then
start them again as in "How to start").

## Event index page (product stage 5)

Page 3 of `docs/prd.md`, inside the terminal. Open it with `2`, or with
`/terminal?a=event-index&a.type=CPI&a.exclude_flagged=true`.

### What it shows

1. **Filters:** event type, period, "Exclude flagged days", and "Show the 100
   ordinary control pairs". They are kept in the URL.
2. **Summary (Stage 5b):** for each measure, the ordinary pairs and then each
   selected type, with n, the median change, the share above zero, event
   minus ordinary, the 90% interval, and whether it survives both controls.
   - "Survives" is Yes or No for the 9 tested combinations, and "not tested"
     for the 50-delta ATM and the ordinary rows.
   - The cells use the same text format as `data/stage5b_summary.txt`.
   - With one period selected, the summary is empty and the API's note
     explains why.
   - There are no labels beyond the survives column.
3. **Event table:** one row per event, newest first. The columns are date,
   type, days drawn, flags, 30-day ATM before and after, fixed-strike ATM
   change, RR25 change, implied move, actual move, premium, and Open.
   - **Flags** say which day is flagged ("⚑ day before", "⚑ event day"),
     from `/dates`.
   - **Control pairs,** when shown, are rows with the type "ordinary".
   - **Events with a missing day** are rows with "1 of 2" or "0 of 2" days
     drawn and the reason from `data/all_dates_status.csv` (Python's date
     notation is shown as a plain date). There are 3 today: CPI 2022-04-12,
     payrolls 2022-05-06 and CPI 2022-05-11.
   - **Sorting:** click a heading. The first click sorts largest first and
     the second reverses it; blanks always go last.
   - **Opening a pair:** click a row, or its Open link, for the two-day Vol
     surface page. In Split view it opens in the other pane.
4. **Caveats:** the API's caveat, which stays true for the current selection:
   "9 combinations were tested by the survival rule, so one could pass by
   chance. The summary pools both periods, with 52 to 77 events per type."
   With flagged events excluded it says 37 to 54.

**Filters and what they change:** type, period and "exclude flagged" change
both the summary and the table. "Show ordinary pairs" changes only the
table, because the ordinary rows are always in the summary as the comparison
base.

Every number comes from `GET /events/index`; the browser only formats and
sorts. If the API fails, both tables show a visible error in their place.

### Tests

`.venv/Scripts/python.exe -m pytest -q`: 180 passed on 2026-10-15 (92 API,
88 web). New: `tests/web/test_event_index_page.py` (headless Edge, 12 tests).
- **Summary:** matches `stage5b_summary.txt` text for text, for all events
  and with flagged excluded; the survives column matches its table.
- **Filters:**
  - type CPI keeps the ordinary and CPI summary rows and CPI events only;
  - excluding flagged switches the summary and leaves only unflagged rows;
  - a single period gives an empty summary with the note;
  - "ordinary pairs" adds 100 rows and leaves the summary unchanged.
- **Missing day:** CPI 2022-04-12 shows "1 of 2" and its full reason;
  payrolls 2022-05-06 shows "0 of 2".
- **Flags:** say which day is flagged.
- **The table:** newest first, the numbers match the API, and a row's link
  opens the right pair.
- **Sorting:** both directions.
- **Opening a row:** a click opens the Vol surface page, in the other pane in
  Split view.
- **Visible error:** with `/api/events/index` blocked, both tables show the
  error.
- **Layout:** Split view side by side and phone width stacked, with no
  sideways page scroll and the filters on screen.
- **Clean-up:** no Edge process with the test profile is left.

## AI tutor (product stage 6)

The tutor panel (`T`) answers questions using the site's own numbers, through
the Claude API. All calls are made by the web server. The browser never sees
a key.

### Set-up

- Add `ANTHROPIC_API_KEY=...` to `.env`. It is loaded like the other secrets
  and read by the Anthropic SDK from the environment. It is never printed,
  logged or sent to the browser.
- Without a key, the panel says the tutor is not configured.
- Package: `anthropic` (installed in `.venv`).

### How a question is answered (`web/tutor/`)

1. **Limits** are checked first, so a refused question costs nothing:
   - Student: 5 questions a day, resetting at midnight UTC.
   - Tutor plus: 300 a month, resetting on the 1st (UTC).

   A question counts only if an answer is shown.
2. **Routing** (`service.route`, a rule with no extra model call):
   - A short "what is / define …" question with no date, event or claim goes
     to **Claude Haiku 5.5**, with only `get_definition`.
   - Everything else goes to **Claude Sonnet 5.5**, with all five tools.
   - If Haiku answers `NEED_DATA`, or its answer fails the number check,
     Sonnet answers instead.
3. **Tools** (`web/tutor/tools.py`) are read-only over the existing API, with
   results rounded to 3 decimals, as on the pages:
   - `list_events`: `GET /events`;
   - `get_event`: `GET /events/{date}`, `/skew` and `/dates`;
   - `get_surface_metrics`: `GET /surface/{date}`, metrics only;
   - `get_findings`: `GET /findings`, one section;
   - `get_definition`: `GET /definitions`.

   Each result keeps its `source`, `n`, `period` and `caveat`. There are up to
   6 tool rounds per question. The system prompt and tool definitions use
   prompt caching.
4. **System prompt** (`web/tutor/prompt.py`):
   - every number must come from a tool, with its table named;
   - no sums, differences or made-up examples;
   - refuse trade recommendations and say it is an education site;
   - say when n is below 30 or a result did not survive the controls;
   - check claims and push back with numbers;
   - plain language; "in the past", never forecasts.
5. **Page context:** the active pane's page, dates and filters are sent with
   the question. The server keeps only known keys with valid values and marks
   them as data, not instructions.
6. **Number check** (`web/tutor/numbers.py`), on every answer:
   - Every number and date must appear in a tool result from this session, in
     the question, or in the page context, at the precision written. A few
     method constants are allowed (16:15, 25-delta, 90%, the 0.5 vol point
     flag, S&P 500).
   - If the check fails, the answer is regenerated once.
   - If it still fails, the user sees "I couldn't verify the numbers in that
     answer, please rephrase it." The question is logged and not counted.
7. **Sessions:**
   - Saved per user (`tutor_sessions`, `tutor_messages`) when the plan is in
     `TUTOR_SAVE_SESSIONS_PLANS`. That is every plan for now; set it to
     `("tutor_plus",)` to limit saving later.
   - Listed in the tutor panel (Sessions), to reopen, rename or delete.
   - Deleted when the account is deleted.
   - The last 10 exchanges are sent as history.
8. **Cost log** (`tutor_usage`):
   - **Per question:** the route, status, whether it counted, tool calls,
     regeneration, the tokens per model (input, output, cache write, cache
     read), the cost in US dollars, and any numbers that could not be traced.
   - **No message text.** When an account is deleted, its rows are kept
     without the user link.
   - **Prices** are in `web/settings.py`, from Anthropic's pricing page on
     2026-10-15. In US dollars per million tokens:
     - Sonnet 5.5: input 2.00, cache write 2.50, cache read 0.10, output
       10.00;
     - Haiku 5.5: input 0.10, cache write 0.125, cache read 0.01, output
       0.50.
   - **Review:** `.venv/Scripts/python.exe -m web.tutor_costs`.

### `GET /definitions` (new API endpoint)

15 terms, each with its source:
- **The PRD's five, word for word:** implied move, actual move, baseline,
  premium, variance premium.
- **Ten more, from the README's definitions (Stages 5 and 5b):** 30-day ATM
  vol, 25-delta risk reversal (RR25), put wing, fixed-strike ATM change,
  vanna, flagged day, ordinary control pair, survives both controls, 90%
  interval, event minus ordinary.

`?term=` looks one up.

### Tests

- **Offline** (run every time, no cost): `tests/web/test_tutor.py` (26 tests,
  with a fake Claude client and the real API in-process),
  `tests/test_definitions.py` and `tests/web/test_tutor_panel.py` (Edge). They
  cover:
  - routing, the tool loop, prompt caching and the page-context clean-up;
  - the number check, including findings' "−" minus sign, regeneration, and
    the unverified message (not counted, logged);
  - limits: the 6th Student question in a day, the 300th Tutor plus question
    in a month, and the daily reset;
  - cost from the token counts, and API failures (not counted);
  - the missing key, and that the key never appears in any response;
  - sessions: saved, continued, listed, deleted, private to the user, removed
    with the account, and the plan setting.

  The browser test servers run without a Claude key, so they never spend
  money.
- **Live** (opt-in, costs money): `tests/live/test_tutor_live.py`, run with
  `MW_RUN_LIVE_TUTOR=1 .venv/Scripts/python.exe -m pytest tests/live`. It is
  skipped without that or without a key.
  - **The 20 questions:** 8 look-ups (with expected numbers and the source
    named), 4 trade-advice refusals, 5 false claims to push back on, and 3
    definitions (Haiku).
  - **Checks:** the number check on every answer.
  - **Output:** transcripts and the cost go to `web/var/tutor_live_report.md`.
- **Result on 2026-10-15:**
  - **First live run:** 11 checks failed, at a cost of $0.2945. The causes
    were a bug in the number check (findings' "−" minus was escaped in JSON
    and read as a different number), "S&P 500" read as a number, the grader
    not accepting 4 decimals, and regenerated answers that mentioned the
    removed numbers.
  - **Debug run:** the 8 failing questions only, $0.2128.
  - **After the fixes:** all 20 pass (22 checks), at a cost of $0.1483, or
    $0.0074 per question on average.
  - **All 211 offline tests pass.**

## Tutor panel: sessions and width (2026-10-16)

The separate Tutor page is gone. Saved sessions now live inside the tutor
panel, and the panel can be resized.

- **Navigation:** Event study `1`, Event index `2`, Vol surface `3`, Help
  `4`. The Tutor button and `T` open and close the panel, as before. An old
  link with `a=tutor` opens the Event study.
- **Sessions in the panel:**
  - **Sessions** (at the top) opens the list of saved sessions, newest first.
  - **Open** a session to continue it in the panel. Its row is marked, and it
    stays open while you look at the list.
  - **Rename** a session inline (1 to 80 characters; `PUT
    /tutor/sessions/{id}`, owner only), or **Delete** it after a confirm
    step.
  - **New** (at the top, or in the list) starts an empty session.
  - **Saving** is still per user, behind `TUTOR_SAVE_SESSIONS_PLANS`, and
    sessions are deleted with the account.
  - **The questions left** are still shown under the question box.
- **Resizing:**
  - Drag the panel's left edge. Or focus that edge (it is a separator in the
    tab order) and use the left and right arrow keys: 16 px a step, 64 px
    with Shift, Home for the narrowest and End for the widest.
  - **Limits:** the panel is at least 280 px, and at most 60% of the
    workspace; the page always keeps at least 360 px beside the navigation.
  - **Remembered per user** with the saved layout (`tutor_width`, 280 to
    1,600 px; `PUT /account/layout` now merges, so saving one setting keeps
    the others).
  - Charts in the panes redraw to their new width after a resize.
  - At phone width the tutor stays full screen and has no handle.
- **Tests** (all pass):
  - `tests/web/test_tutor_panel.py` (Edge):
    - no Tutor page, and the renumbered keys;
    - the questions left;
    - sessions newest first: open (stays current), rename, delete, new;
    - resizing by keyboard (steps, Home and End, the limits, and the page
      keeping at least 360 px);
    - resizing by drag, with the width saved and used again on the next
      visit;
    - full screen at phone width;
    - the Edge clean-up check.
  - Server: renaming (tidied, length limits, owner only) and the saved width
    (range checked, merged with the other settings).
  - The browser test servers use a dummy Claude key. The panel then shows
    the questions left, but nothing can be spent, and no question is asked.
- **PRD:** Pages 4 and 5 are updated to match.

## Daily update (product stage 7)

`daily_update.py` appends new trading days, new events and "priced now" with the frozen method, rules and
limits, reusing the pipeline's own functions. It never rewrites a stored row, and never writes a frozen file.

```bash
.venv/Scripts/python.exe daily_update.py --plan        # what a run would do now (reads FRED; no ThetaData, no writes)
.venv/Scripts/python.exe daily_update.py               # catch up and update now
bash scripts/register_daily_update.sh                     # schedule it (run once, yourself)
```

### What a run does

Each run catches up every trading day not yet processed, in date order.

1. **Downloads first.**
   - FRED: the S&P 500 closes, which decide which days traded, plus the bill
     yields and VIX.
   - ThetaData, 2 requests at a time, with the 14-day coverage rule: for each
     new trading day, the surface chain (`fetch_surface`) and the event-study
     chain for the next trading day's expiry (`fetch_chain`, as
     `pull_chains` does).
2. **Then one database transaction.** Each step runs only once its inputs
   exist, so no stored number ever needs to change:
   - **Event-study quotes:** the saved chains are added to `spxw_1615`.
   - **`date_list`:** rebuilt from FRED and `event_calendar.csv` by
     `build_dates.build_date_list`. The rows already stored must come out
     identical, or the run stops; the new rows are appended.
   - **`event_moves`:** a row for each new date once FRED has its close and
     the day before's (`analyze_event.measure`). An event's actual move
     appears after it closes.
   - **`event_priced` ("priced now"):** for an upcoming event whose
     day-before chain is saved, the implied move is stored the same evening.
     The after-hours check waits for FRED's close.
   - **`event_premium`:** written once, when the event's 20 following trading
     days are in (`summarize.event_premium`, unchanged, on the live date
     list).
   - **`event_premium_provisional`:** until then, the same calculation over
     the window days available so far, labelled provisional, with how many
     of the 40 window days it used. It is recomputed each run and not
     updated again once the final row exists.
   - **Surfaces and skew:** once every FRED bill series and VIX has data for
     the day or any later date (`daily.run.fred_ready`), the surface uses the
     latest yield on or before the day, as the frozen method does. A
     bond-market holiday when stocks trade (Columbus Day 2026-10-12, Veterans
     Day 2026-11-11) has no bill yield, so it waits only for the next one and
     never for a day FRED will not publish; `--plan` marks it `yes*`.
     `vol_surface.append_dates` adds the surface,
     then `surface_metrics` is added, and `event_skew_changes` and
     `event_fixed_strike` for each event whose two days are drawn (the Stage
     5 and 5b functions).
3. **Checks.**
   - Before the writes, every guarded table's rows are summed by row hash;
     after them, the sum over every row except this run's new ones must be
     unchanged. Otherwise the transaction rolls back and the run fails.
   - The frozen v1 rows (dated on or before 2026-10-09) also have fixed
     checksums, saved once in `data/live/v1_checksums.csv` and checked every
     run.
   - The frozen files listed in the freeze record are hashed before and after
     the run.
   - **`event_premium_provisional` is the only table whose rows are updated,
     so it is not guarded.**
4. **Live files** go in `data/live/`; the frozen files are never written:
   - `date_list.csv` and `all_dates_status.csv`, the live copies;
   - `skipped_dates.csv`;
   - `fetch_errors.csv` (`fetch_surface`'s log is redirected here, because
     `data/surface_fetch_errors.csv` is a frozen file);
   - `update_log.txt` and `update_status.json`.

### Frozen v1 vs live

- **The cut-off** `config.V1_END = 2026-10-09`: rows dated on or before it are
  the data behind `findings_v1.md`.
- **API:**
  - Summaries use v1 rows by default. `GET /events` accepts `data=live`, for
    the event-study medians only, labelled "includes data after 2026-10-09,
    not in findings_v1"; its premium medians use final premiums only.
  - The Stage 5b summary is always v1.
  - Rows after the cut-off are included and carry `live: true`. Every
    response carries `v1_end`.
- **Pages:**
  - Summaries say "Frozen results: findings v1, data to 2026-10-09".
  - Live rows carry a "live" badge, and provisional premiums say
    "provisional (n of 40 days)".
  - The event study has "Include live data (after 2026-10-09) in the
    medians".
  - The top bar shows "Data to <date> · v1 frozen to 2026-10-09".
  - A red banner appears if the last update failed, or the data is more than
    2 trading days behind.
- **Tutor:** it says when it uses live or provisional numbers.

### The API and the database

The API now opens a short read-only connection per request and closes it
afterwards. The update can then take its brief write lock between requests.
A request that meets the lock retries for 4 seconds, then gets 503: "The data
is updating. Try again in a moment." Restart the API after this change: a
server started earlier keeps one connection open, which blocks the update.

### Scheduling (Task Scheduler)

`scripts/register_daily_update.sh` runs `scripts/register_daily_update.ps1`,
which registers the task for your user. The task is still named "Macro Wire
daily update". Task Scheduler runs `.venv/Scripts/python.exe daily_update.py
--scheduled` directly, not a script.
- **Triggers:** daily at 21:45 and 22:45 UK time, and at log-on.
- **Missed starts:** run as soon as possible after a missed start, so a PC
  that was off catches up.
- **Overlap:** no overlapping runs, and a 2-hour limit.
- **Logon:** only while you are logged on.
- **Holidays:** `config.NYSE_HOLIDAYS` holds NYSE's 2026 and 2027 holidays,
  and `config.HALF_DAYS` the early closes to 2027-11-26, from
  nyse.com/markets/hours-calendars. Extend both each year.
- **Time zones:** the script decides in New York time. Before 16:45 New York
  (13:45 on early closes, `config.HALF_DAYS`) it leaves today for the next
  run, so UK and US clock changes don't matter.
- **Failures:** the run exits with code 1, logs `RUN FAILED: <reason>` in
  `data/live/update_log.txt`, records it in `update_status.json`, and the
  terminal shows the red banner.

### Tests (offline, no downloads)

`tests/test_daily.py`:
- New York time across the UK/US clock changes, half days and holidays;
- a bond-market holiday (2026-11-11): the surface waits for the next bill
  yield, then uses the one before;
- the guard, which accepts appends and catches a changed row or a duplicate;
- the v1 checksums, which ignore live rows;
- provisional premiums (25 of 40 days, the frozen calculation) and the final
  one once the window is complete;
- the frozen files, which match the freeze record;
- the update writing only to `data/live/`;
- the API's 503 while a writer holds the lock.

Also in the web tests: the update-status route. The page and API tests now
hold before and after live rows exist.

## Homepage (product stage 8)

The last page of version 1 (`docs/prd.md`, Page 6). It's a public product tour
in an editorial style: cream paper, black ink, one cobalt accent and thin rules.
It holds no real numbers, dates or data-provider credit. All the fonts are
self-hosted, so the security policy is unchanged ('self' only).

### Viewing it

Start the API and the web server as before. Then open http://127.0.0.1:8080/.

```bash
.venv/Scripts/python.exe -m uvicorn api.main:app --host 127.0.0.1 --port 8000
.venv/Scripts/python.exe -m uvicorn web.server:app --host 127.0.0.1 --port 8080
```

- `/` serves the homepage to everyone. Logged-out visitors land on it, and
  logged-in users can still reach it: the server adds a class to the body, and
  their buttons say "Open the terminal".
- The terminal's "Saltum Labs" name links back to the homepage.
- `/login?mode=signup` opens straight on the sign-up form.

### The sections

1. Hero.
2. Terminal preview: the real terminal in a window frame, on sample data.
3. Scripted tutor demo, on sample figures.
4. Findings: two cards in words, with a link to the numbers in the terminal.
5. Honest by design: four one-line points and a link to `/method`.
6. Pricing, under one line on what is coming next.
7. Footer.

`/method` is a public page in the same style, with the full method in words. It
follows the same rules as the homepage: no real numbers, dates or provider credit.

### Where the numbers come from

- **No data route.** The homepage makes no data request; `/home/data` and
  `web/home.py` are gone. Every data and tutor route needs a login.
- **Prices** are written into `home.html`; a test checks them against
  `web/settings.py`.
- **The demo** (`web/static/js/home_demo.js`) is a fixed script. It makes no
  requests, and its figures come from the preview's sample file (CPI, later period).
- **The terminal preview** is `/preview` in a frame: the real `terminal.html`
  and its code, with the same CSS. On that path `api.js` answers every read
  from `web/static/preview/sample.json` (invented numbers, in the API's shape)
  and saves nothing, so it calls no API, tutor or account route. The sample holds
  one example: CPI in the later period, one event row, one pair of dates and one
  tutor question. Every other choice (dropdowns, dates, arrows, Ask, New,
  Sessions) opens a sign-up pop-up instead, and Split view, Shortcuts and their
  Help rows are hidden. Periods are the ids `earlier` and `later` in every
  public file.
- **Periods:** the browser sends and receives only those ids. The `/api` proxy
  turns them into the API's real values and back (`PERIOD_IDS` in
  `web/settings.py`), and the logged-in `/account/me` carries the labels and the
  rest of the terminal's own wording (`TERMINAL_WORDING`).
  `tests/web/test_preview.py` checks the sample's keys against the API.
- **Motion:** the demo plays when scrolled into view and has Replay. With
  reduced motion set, it shows the whole conversation at once.

### Fonts

Inter (text) and Newsreader (headings) come from the `@fontsource-variable` 5.3.0
packages (latin), with their OFL licences in `web/static/fonts/`. Numbers and
labels use IBM Plex Mono.

### Tests

`tests/web/test_home_page.py`:
- **Routes:**
  - `/` returns 200 logged out and logged in;
  - `/home/data` is gone;
  - every data route still refuses logged-out requests;
  - the security policy is unchanged.
- **Database:** every test run reads its own copy of the database (`tests/conftest.py`), so the
  daily update can't collide with it. It waits for a running update before copying.
- **No real numbers:**
  - no figure or date from `findings_v1.md`, and no median from the API, is in
    the homepage files or on the rendered page, and a control checks that the
    guard catches a real figure;
  - every number on the rendered page is a price, a sample figure or "500";
  - no internal wording;
  - no public HTML, JS or CSS file holds a real figure, date, period boundary or
    login-only wording, and `sample.json` holds none in its text, dates or
    medians. Its other invented numbers are not compared one by one: some match
    a figure in the findings by chance. The vendored Plotly file is not checked.
- **In headless Edge:**
  - the six sections render in order;
  - the findings cards hold no digits and link to the terminal;
  - the page calls nothing but its own files and the preview;
  - "Education only, not advice" is visible without scrolling;
  - the demo is labelled as sample data, and replays and switches;
  - reduced motion shows the demo at once;
  - nothing is cut off at 390 px;
  - logged-in and logged-out buttons are correct.

The browser test script (`tests/web/browser/cdp_visit.mjs`) gained three
options: logged-out visits, emulated media (reduced motion) and a separate
`wait_after`. Arrow and other named keys are now sent with their key codes.
