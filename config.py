"""All settings for the CPI implied-move example. Edit values here only."""
import datetime as dt
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent

# --- Event -----------------------------------------------------------------
# August 2026 CPI, released Fri Sep 11, 2026 08:30 ET.
# Source: BLS CPI release schedule, https://www.bls.gov/schedule/news_release/cpi.htm
EVENT_NAME = "US CPI (August 2026)"
EVENT_DATE = dt.date(2026, 9, 11)   # release day
TRADE_DATE = dt.date(2026, 9, 10)   # trading day before the release (chain snapshot date)
EXPIRATION = EVENT_DATE             # SPXW weekly that expires on the release day

# --- ThetaData ---------------------------------------------------------------
OPTION_SYMBOL = "SPXW"              # SPX weeklies (PM-settled), per ThetaData symbology docs
DOTENV_PATH = PROJECT_DIR / ".env"  # holds THETADATA_API_KEY; theta.make_client() applies it over any shell value
# Options Value subscription: the docs limit concurrent requests (2) but say requests are
# "NOT rate-limited in any way". (The free tier was 20/min, one at a time: 3.0 s and 1.)
MAX_CONCURRENT_REQUESTS = 2
MIN_SECONDS_BETWEEN_REQUESTS = 0.5  # small gap between request starts
MAX_RETRIES = 4
RETRY_BACKOFF_SECONDS = 5.0         # doubles on each retry

# Optional CA bundle for machines where antivirus/proxy re-signs HTTPS traffic
# (e.g. Avast web shield). Created by make_ca_bundle.py; if the file is absent
# the library defaults are used. TLS verification is never turned off. See README.
_CA_BUNDLE_FILE = PROJECT_DIR / "certs" / "ca_bundle.pem"
CA_BUNDLE = _CA_BUNDLE_FILE if _CA_BUNDLE_FILE.exists() else None

# --- Cleaning ----------------------------------------------------------------
# Drop quotes whose (ask - bid) / mid is above this. 0.10 = 10% of mid.
MAX_RELATIVE_SPREAD = 0.10
# ...OR keep it if (ask - bid) is at most this many index points (cheap options quote 0.05-0.30 wide).
MAX_ABSOLUTE_SPREAD = 0.30

# --- Sanity checks -----------------------------------------------------------
# Strikes with both a clean call and a clean put. Was 20; lowered to 3 after the multi-event
# pull showed one-day chains often keep only a few pairs (cheap wings fail the spread rule)
# while the strikes next to the money are fine. What the straddle needs is checked below.
MIN_PAIRED_STRIKES = 3
# Max |forward - parity strike| in index points (2 SPXW strike steps near the money).
MAX_FORWARD_STRIKE_GAP = 10.0
# Max allowed |forward / FRED close on trade date - 1|. EOD quotes are snapshotted
# after the options close (~16:15 ET), not at the 16:00 cash close, so a small gap is normal.
FORWARD_TOLERANCE = 0.005

# --- FRED --------------------------------------------------------------------
FRED_SERIES = "SP500"
FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"

# --- Paths -------------------------------------------------------------------
# Event-study chains: SPXW quotes at the options close (16:15 ET; 13:15 on half days), from
# option_at_time_quote. The end-of-day chains (ThetaData EOD report, ~17:15 ET) are kept as
# RAW_DIR_EOD / DUCKDB_TABLE_EOD for comparison. Why: see README, "Snapshot time".
RAW_DIR = PROJECT_DIR / "data" / "raw" / "spxw_1615"   # Parquet, partitioned by trade_date
DUCKDB_PATH = PROJECT_DIR / "data" / "macro_wire.duckdb"
DUCKDB_TABLE = "spxw_1615"
RAW_DIR_EOD = PROJECT_DIR / "data" / "raw" / "spxw_eod"
DUCKDB_TABLE_EOD = "spxw_eod"

# --- Multi-event study ---------------------------------------------------------
# Earliest trade date the free account can read (probed 2026-10-09: 2023-06-01 works,
# 2023-05-31 and earlier return PERMISSION_DENIED "requires a VALUE subscription").
DATA_START = dt.date(2020, 1, 2)   # Options Value subscription (was 2023-06-01 on the free tier)
# Official release / decision dates (BLS CPI and Employment Situation archives, Fed FOMC
# calendar; FOMC = second day of each scheduled meeting). Source URL per row.
EVENT_CALENDAR = PROJECT_DIR / "event_calendar.csv"
DATE_LIST_CSV = PROJECT_DIR / "data" / "date_list.csv"
DATE_LIST_TABLE = "date_list"
RESULTS_TABLE = "event_moves"           # end-of-day version kept as event_moves_eod
SKIPPED_LOG = PROJECT_DIR / "data" / "skipped_dates.csv"   # dates with no result row, and why
PULL_ERRORS = PROJECT_DIR / "data" / "pull_errors.csv"     # chain requests that failed in the last pull run
BASELINE_WINDOW = 20                # trading days either side for the event-premium baseline
SMALL_SAMPLE = 30                   # below this, statistics are flagged as too few to rely on
FIGURE_PATH = PROJECT_DIR / "data" / "implied_vs_actual.png"

# --- Implied vol surface (Part B) ------------------------------------------------
# The original 8 snapshot dates. surface_figures.py, surface_3d.py, clean_surface.py, vix_check.py
# and the default vol_page.py page stay pinned to these.
SURFACE_DATES_ORIGINAL = [
    dt.date(2026, 9, 10), dt.date(2026, 9, 11),     # day before / day of Aug 2026 CPI
    dt.date(2026, 9, 15), dt.date(2026, 9, 16),     # day before / day of FOMC decision (latest in data)
    dt.date(2026, 10, 1), dt.date(2026, 10, 2),     # day before / day of payrolls (latest in data)
    dt.date(2025, 4, 9), dt.date(2025, 4, 10),      # day before / day of Mar 2025 CPI (stressed market)
]
# All surface dates (fetch_surface.py, vol_surface.py): every CPI / FOMC / payrolls day in the date
# list and the trading day before it, plus the original 8. Written by build_surface_dates.py.
SURFACE_DATES_CSV = PROJECT_DIR / "data" / "surface_dates.csv"


def _surface_dates():
    if SURFACE_DATES_CSV.exists():
        lines = SURFACE_DATES_CSV.read_text(encoding="utf-8").split()[1:]
        return [dt.date.fromisoformat(x.split(",")[0]) for x in lines if x]
    return list(SURFACE_DATES_ORIGINAL)


SURFACE_DATES = _surface_dates()
# Stage 7: one row per trading day since 2020 (stage7_all_dates.py). vol_surface.py and
# skew_metrics.py cover these dates as well as SURFACE_DATES.
ALL_DATES_STATUS_CSV = PROJECT_DIR / "data" / "all_dates_status.csv"


def all_surface_dates():
    days = set(SURFACE_DATES)
    if ALL_DATES_STATUS_CSV.exists():
        lines = ALL_DATES_STATUS_CSV.read_text(encoding="utf-8").split("\n")[1:]
        days |= {dt.date.fromisoformat(x.split(",")[0]) for x in lines if x.strip()}
    return sorted(days)


SURFACE_FETCH_ERRORS_CSV = PROJECT_DIR / "data" / "surface_fetch_errors.csv"   # every fetch_surface.py run (run_date column)
SURFACE_FAILURES_CSV = PROJECT_DIR / "data" / "surface_failures.csv"           # last vol_surface.py run
SURFACE_SUMMARY_TABLE = "surface_date_summary"   # one row per snapshot date (options, 30-day ATM vol, flags)
SURFACE_MAX_WORKERS = 2                          # fetch_surface.py requests in flight (Value: 2 allowed)
# Coverage check in fetch_surface.py: a listed expiry missing from a date's response fails the date
# if it is within SURFACE_COVERAGE_HARD_DAYS, and is only a warning out to SURFACE_COVERAGE_WARN_DAYS
# (expiries were listed only a few weeks ahead in 2020-22; see README "Event pages").
SURFACE_COVERAGE_HARD_DAYS = 14
SURFACE_COVERAGE_WARN_DAYS = 30
SURFACE_MAX_DTE = 185               # expiries from 1 day to about 6 months
# Surface chains at the options close (16:15 ET; 13:15 on half days), all expiries.
SURFACE_RAW_DIR = PROJECT_DIR / "data" / "raw" / "spxw_surface_1615"   # separate from the event study
SURFACE_RAW_TABLE = "spxw_surface_1615"
# End-of-day surface chains: kept, and still used by the 51-date validation.
SURFACE_RAW_DIR_EOD = PROJECT_DIR / "data" / "raw" / "spxw_eod_surface"
SURFACE_RAW_TABLE_EOD = "spxw_eod_surface"
SURFACE_TABLE = "vol_surface"       # one row per option (end-of-day version kept as vol_surface_eod)
SURFACE_EXPIRY_TABLE = "surface_expiries"   # one row per (trade_date, expiration); EOD: surface_expiries_eod
# Time to expiry: calendar time, ACT/365, from SPX options close on the trade date
# (16:15 ET, also the VIX close) to the 16:00 ET settlement on expiry.
SNAPSHOT_TIME = dt.time(16, 15)
SNAPSHOT_TIME_HALF_DAY = dt.time(13, 15)   # options close on early-close days
EXPIRY_TIME = dt.time(16, 0)
MARKET_TZ = "America/New_York"
# Forward / discount factor: regress (C - P) on K over the N clean paired strikes nearest the money.
REGRESSION_STRIKES = 20
MIN_REGRESSION_STRIKES = 6
DF_BOUNDS = (0.95, 1.001)           # flag a discount factor outside this range
# Implied rate vs Treasury bill yield of matching maturity (linear in days between these FRED
# CMT series, flat beyond the ends). Reported as information, not as a failure.
BILL_SERIES = {"DGS1MO": 30, "DGS3MO": 91, "DGS6MO": 182, "DGS1": 365}
RATE_TOLERANCE = 0.015              # note |implied rate - bill yield| above 1.5 percentage points
VIX_SERIES = "VIXCLS"
ATM_VIX_MAX_REL_GAP = 0.40          # flag VIX above 30d ATM vol by more than 40% of that vol (or ATM >= VIX)
IV_BOUNDS = (0.03, 1.50)            # flag implied vols outside 3% to 150%
SOLVER_BOUNDS = (1e-4, 5.0)         # bisection bracket for sigma
SOLVER_ITERATIONS = 80
SMILE_DTES = [1, 7, 30, 60, 91, 182]    # smiles plotted for the expiries nearest these
PLOT_SD = 4.0                       # plot |log(K/F)| <= PLOT_SD * atm_iv * sqrt(t)
FIG_DIR = PROJECT_DIR / "data" / "figures"

# --- VIX replication (Cboe VIX Methodology v6.0, 2026-02-26, + Mathematics Methodology) -----
VIX_CALC_TIME = dt.time(16, 15)     # VIX close (end of RTH dissemination)
VIX_CONSTANT_MATURITY_DAYS = 30
VIX_SPX_RAW_DIR = PROJECT_DIR / "data" / "raw" / "spx_vix_1615"   # AM-settled SPX monthlies, options close
VIX_SPX_RAW_TABLE = "spx_vix_1615"
VIX_SPX_RAW_DIR_EOD = PROJECT_DIR / "data" / "raw" / "spx_eod_vix"   # end of day; used by the validation
VIX_SPX_RAW_TABLE_EOD = "spx_eod_vix"
VIX_MAX_GAP_POINTS = 0.5            # investigate if |replicated - VIXCLS| exceeds this
# Treasury CMT series on FRED and their day counts in the Cboe spline (FRED has no 2-month series).
CMT_SERIES = {"DGS1MO": 30, "DGS3MO": 91, "DGS6MO": 182, "DGS1": 365, "DGS2": 730}

# --- Interactive 3D views (surface_3d.py) ------------------------------------------
SURFACE_3D_GRID_STEP = 0.005        # common log-moneyness grid for the interpolated surface
SURFACE_3D_DAY_TICKS = [1, 7, 14, 30, 60, 90, 120, 180]   # tick labels on the square-root-of-days axis
SURFACE_3D_PATH = FIG_DIR / "surface_3d.html"

# --- Multi-date validation of the surface and the VIX replication ---------------------
VALIDATION_DATES_CSV = PROJECT_DIR / "data" / "validation_dates.csv"   # fixed once written
VALIDATION_SEED = 20261009          # seed for the one-random-trading-day-per-month draw
VALIDATION_TOP_VIX = 10             # plus the highest VIX closes in the window
VALIDATION_TABLE = "validation_results"
VALIDATION_FLAGS_TABLE = "validation_flags"
VALIDATION_FIGURE = FIG_DIR / "vix_replication.png"

# --- US market half days (1:00 p.m. ET close; 1:15 p.m. for eligible options) ------------
# Source: NYSE Group holiday and early-closings calendars (ICE press releases of 2019-12-09,
# 2021-12-27, 2022-12-21 and 2023-11-10) and nyse.com/markets/hours-calendars (2026, 2027).
# Snapshots on these days use the 13:15 ET options close (SNAPSHOT_TIME_HALF_DAY).
HALF_DAYS = {
    dt.date(2020, 11, 27), dt.date(2020, 12, 24), dt.date(2021, 11, 26), dt.date(2022, 11, 25),
    dt.date(2023, 7, 3), dt.date(2023, 11, 24),
    dt.date(2024, 7, 3), dt.date(2024, 11, 29), dt.date(2024, 12, 24),
    dt.date(2025, 7, 3), dt.date(2025, 11, 28), dt.date(2025, 12, 24),
    dt.date(2026, 11, 27), dt.date(2026, 12, 24), dt.date(2027, 11, 26),
}

# --- Clean mode for smiles and surface (clean_surface.py, surface_3d.py) ----------------
CLEAN_MIN_DTE = 14                  # expiries from 14 days...
CLEAN_MAX_DTE = SURFACE_MAX_DTE     # ...to about 6 months; Friday weeklies and monthlies only
CLEAN_DELTA_RANGE = (0.10, 0.90)    # x = 1 - call delta: 0.10 = 10-delta put ... 0.90 = 10-delta call
CLEAN_STRIKE_PCT = (90.0, 110.0)    # alternative x: strike as % of forward
CLEAN_FIT_DEGREE = 3                # least-squares cubic polynomial in log-moneyness, per expiry
CLEAN_MIN_POINTS = 8                # expiries with fewer real points in range are not fitted
CLEAN_GRID_POINTS = 41              # regular x grid for the clean surface
# Light smoothing across expiries: total variance at each expiry = 1/2 own value + 1/2 the straight
# line (in t) through its two neighbours at the same x. Equals a 1-2-1 average when expiries are evenly
# spaced; unlike a plain 1-2-1 average it is not biased by uneven spacing. First/last expiry unsmoothed.
# (A plain 1-2-1 average was tried first: surface gaps up to 1.84 vol points; see README.)
CLEAN_SMOOTH_OWN_WEIGHT = 0.5
CLEAN_SMILE_TARGET_DTES = [14, 28, 45, 63, 91, 126, 168]   # at most 7 smiles shown, nearest expiries
CLEAN_GAP_LIMIT = 0.005             # report any smoothed-vs-real gap above half a vol point
CLEAN_SMILES_PNG = FIG_DIR / "smiles_clean_{date}.png"

# --- Volatility page (vol_page.py) ----------------------------------------------------
# One self-contained HTML page (clean surface, smiles, term structure) covering every snapshot
# date in the vol_surface table. Smiles are a per-expiry cubic (CLEAN_FIT_DEGREE) with no
# smoothing across expiries, so its numbers differ slightly from clean_surface.py.
VOL_PAGE_TEMPLATE = PROJECT_DIR / "templates" / "vol_page.html"
VOL_PAGE_LIBRARY = PROJECT_DIR / "vendor" / "plotly-strict.min.js"   # copied next to the page
VOL_PAGE_PATH = FIG_DIR / "vol_page.html"
VOL_PAGE_DATA = FIG_DIR / "vol_page_data.json"
VOL_PAGE_MIN_POINTS = 12            # computed options needed in range before an expiry is fitted
VOL_PAGE_FIT_PAD = 0.03             # the fit also uses options this far (in delta) beyond the 10-delta ends
VOL_PAGE_PAIR_MAX_DAYS = 5          # compare a date with a neighbouring snapshot at most this many days away
VOL_PAGE_EVENT_NAMES = {"CPI": "CPI", "NFP": "payrolls", "FOMC": "FOMC decision"}

# --- Fixed-minute quote test (fixed_minute_test.py; Value subscription; test only) -------
FM_DATES_CSV = PROJECT_DIR / "data" / "fixed_minute_dates.csv"   # fixed once written
FM_CALM_COUNT = 5                   # calm control dates drawn from the validation's monthly sample
FM_SEED = 20261011
FM_TIMES = ("16:00:00.000", "16:15:00.000")          # cash close, options close
FM_TIMES_HALF_DAY = ("13:00:00.000", "13:15:00.000")
FM_MAX_DTE = 45                     # enough for the VIX bracket and the expiry nearest 30 days
FM_SPXW_RAW_DIR = PROJECT_DIR / "data" / "raw" / "spxw_at_time"
FM_SPX_RAW_DIR = PROJECT_DIR / "data" / "raw" / "spx_at_time"
FM_SPXW_TABLE = "spxw_at_time"
FM_SPX_TABLE = "spx_at_time"

# --- Surface quote rule (vol_surface.py only; the event study and the VIX are unchanged) ---------
# Forward and discount factor still use the price-spread rule (MAX_RELATIVE_SPREAD or
# MAX_ABSOLUTE_SPREAD). An out-of-the-money option is also kept for the surface if bid > 0 and
# its implied vol at the ask minus its implied vol at the bid is at most this many vol points.
SURFACE_MAX_VOL_SPREAD_POINTS = 3.0

# --- Event pages (vol_page.py --events) ------------------------------------------------
VOL_EVENTS_DIR = FIG_DIR / "events"          # one page per event + index.html + one plotly-strict.min.js
VOL_EVENTS_STATUS_CSV = PROJECT_DIR / "data" / "vol_event_pages.csv"   # every event: drawn or not, and why


# --- Daily update (product stage 7, daily_update.py) -------------------------------------------------------
# Frozen version 1: every table row dated on or before V1_END is the data behind data/findings_v1.md. The daily
# update only appends later rows ("live"); the API's summaries use v1 rows unless asked for live data.
V1_END = dt.date(2026, 10, 9)
LIVE_DIR = PROJECT_DIR / "data" / "live"                      # live copies; the frozen files are never written
LIVE_DATE_LIST_CSV = LIVE_DIR / "date_list.csv"
LIVE_STATUS_CSV = LIVE_DIR / "all_dates_status.csv"
LIVE_SKIPPED_CSV = LIVE_DIR / "skipped_dates.csv"
LIVE_FETCH_ERRORS_CSV = LIVE_DIR / "fetch_errors.csv"
UPDATE_LOG = LIVE_DIR / "update_log.txt"
UPDATE_STATUS = LIVE_DIR / "update_status.json"
V1_CHECKSUMS_CSV = LIVE_DIR / "v1_checksums.csv"
PRICED_TABLE = "event_priced"                                  # "priced now": implied move before the event closes
PROVISIONAL_TABLE = "event_premium_provisional"                # the only table whose rows are updated
# NYSE holidays (market closed), from nyse.com/markets/hours-calendars, for the daily update's scheduling (FRED's
# closes still decide which days traded). Bond-market-only holidays (Columbus Day, Veterans Day) are not listed.
NYSE_HOLIDAYS = {
    dt.date(2026, 1, 1), dt.date(2026, 1, 19), dt.date(2026, 2, 16), dt.date(2026, 4, 3), dt.date(2026, 5, 25),
    dt.date(2026, 6, 19), dt.date(2026, 7, 3), dt.date(2026, 9, 7), dt.date(2026, 11, 26), dt.date(2026, 12, 25),
    dt.date(2027, 1, 1), dt.date(2027, 1, 18), dt.date(2027, 2, 15), dt.date(2027, 3, 26), dt.date(2027, 5, 31),
    dt.date(2027, 6, 18), dt.date(2027, 7, 5), dt.date(2027, 9, 6), dt.date(2027, 11, 25), dt.date(2027, 12, 24),
}
