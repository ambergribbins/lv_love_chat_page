"""Central configuration for the MLB numberFire + Massey audit.

All values are read from environment variables (optionally loaded from a
``.env`` file) with safe defaults. Nothing here performs network I/O.

The project is intentionally faithful to the original chat workflow:
    predictive_model_edge = numberFire_probability - market_implied_probability
Massey is treated as an independent *filter*, never averaged into the
numberFire probability.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv is optional at runtime
    pass


def _get_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _get_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# --- Paths -----------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
if not DATA_DIR.is_absolute():
    DATA_DIR = PROJECT_ROOT / DATA_DIR

RAW_DIR = DATA_DIR / "raw"
RAW_NUMBERFIRE_DIR = RAW_DIR / "numberfire"
RAW_ODDS_DIR = RAW_DIR / "odds"
MANUAL_DIR = DATA_DIR / "manual"
PROCESSED_DIR = DATA_DIR / "processed"
RESULTS_DIR = DATA_DIR / "results"
REPORTS_DIR = DATA_DIR / "reports"
LOGS_DIR = DATA_DIR / "logs"

# --- Scraping / HTTP -------------------------------------------------------
DEFAULT_USER_AGENT = (
    "mlb-numberfire-massey-audit/1.0 (research; polite; contact via project owner)"
)
NUMBERFIRE_USER_AGENT = os.getenv("NUMBERFIRE_USER_AGENT") or DEFAULT_USER_AGENT
SLEEP_SECONDS = _get_float("SLEEP_SECONDS", 2.0)
MAX_RETRIES = _get_int("MAX_RETRIES", 3)
SAVE_RAW_RESPONSES = _get_bool("SAVE_RAW_RESPONSES", True)
ENABLE_WAYBACK_BACKFILL = _get_bool("ENABLE_WAYBACK_BACKFILL", False)

# --- Model / edge thresholds ----------------------------------------------
EDGE_THRESHOLD_NF = _get_float("EDGE_THRESHOLD_NF", 0.03)
MIN_EV_THRESHOLD = _get_float("MIN_EV_THRESHOLD", 0.00)

# --- Massey filter thresholds (runs) --------------------------------------
MASSEY_AGREE_MARGIN_RUNS = _get_float("MASSEY_AGREE_MARGIN_RUNS", 0.25)
MASSEY_CONFLICT_MARGIN_RUNS = _get_float("MASSEY_CONFLICT_MARGIN_RUNS", -0.25)
DEFAULT_HOME_FIELD_RUNS = _get_float("DEFAULT_HOME_FIELD_RUNS", 0.15)

# --- Odds source -----------------------------------------------------------
ODDS_SOURCE = os.getenv("ODDS_SOURCE", "manual").strip().lower()
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "").strip()

# --- Season ----------------------------------------------------------------
SEASON_YEAR = _get_int("SEASON_YEAR", 2026)

# Rough regular-season window used by --season when no explicit range is given.
# These are conservative defaults; override with --start-date/--end-date.
SEASON_START_MMDD = "03-26"
SEASON_END_MMDD = "09-30"


def ensure_directories() -> None:
    """Create all data directories if they do not yet exist."""
    for d in (
        RAW_NUMBERFIRE_DIR,
        RAW_ODDS_DIR,
        MANUAL_DIR,
        PROCESSED_DIR,
        RESULTS_DIR,
        REPORTS_DIR,
        LOGS_DIR,
    ):
        d.mkdir(parents=True, exist_ok=True)


def summary() -> dict:
    """Return a plain dict of the active config (for logging / debugging)."""
    return {
        "DATA_DIR": str(DATA_DIR),
        "NUMBERFIRE_USER_AGENT": NUMBERFIRE_USER_AGENT,
        "SLEEP_SECONDS": SLEEP_SECONDS,
        "MAX_RETRIES": MAX_RETRIES,
        "EDGE_THRESHOLD_NF": EDGE_THRESHOLD_NF,
        "MIN_EV_THRESHOLD": MIN_EV_THRESHOLD,
        "MASSEY_AGREE_MARGIN_RUNS": MASSEY_AGREE_MARGIN_RUNS,
        "MASSEY_CONFLICT_MARGIN_RUNS": MASSEY_CONFLICT_MARGIN_RUNS,
        "DEFAULT_HOME_FIELD_RUNS": DEFAULT_HOME_FIELD_RUNS,
        "ODDS_SOURCE": ODDS_SOURCE,
        "ODDS_API_KEY_set": bool(ODDS_API_KEY),
        "ENABLE_WAYBACK_BACKFILL": ENABLE_WAYBACK_BACKFILL,
        "SAVE_RAW_RESPONSES": SAVE_RAW_RESPONSES,
        "SEASON_YEAR": SEASON_YEAR,
    }
