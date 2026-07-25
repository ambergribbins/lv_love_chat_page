"""Sportsbook moneyline odds loader.

The first-class path is a manual CSV per date:

    data/manual/mlb_moneyline_odds_YYYY-MM-DD.csv

An API collector is scaffolded (The Odds API-style) but not required; it only
activates when ODDS_SOURCE=api and ODDS_API_KEY is set.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

import config
from team_map import normalize_team_name

logger = logging.getLogger(__name__)

OUTPUT_COLUMNS = [
    "odds_observed_at_utc",
    "date",
    "sportsbook",
    "away_team",
    "home_team",
    "away_moneyline",
    "home_moneyline",
    "opening_away_moneyline",
    "opening_home_moneyline",
    "closing_away_moneyline",
    "closing_home_moneyline",
    "source_type",
    "source_notes",
]

_OPTIONAL_COLS = [
    "opening_away_moneyline",
    "opening_home_moneyline",
    "closing_away_moneyline",
    "closing_home_moneyline",
]


def _empty_odds() -> pd.DataFrame:
    return pd.DataFrame(columns=OUTPUT_COLUMNS)


def manual_odds_path(date: str) -> Path:
    return config.MANUAL_DIR / f"mlb_moneyline_odds_{date}.csv"


def _finalize(df: pd.DataFrame, source_type: str) -> pd.DataFrame:
    """Normalize teams, ensure all output columns exist, coerce order."""
    if df.empty:
        return _empty_odds()
    df = df.copy()
    df["away_team"] = df["away_team"].map(normalize_team_name)
    df["home_team"] = df["home_team"].map(normalize_team_name)
    for col in OUTPUT_COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA
    if "source_type" not in df.columns or df["source_type"].isna().all():
        df["source_type"] = source_type
    df["source_type"] = df["source_type"].fillna(source_type)
    return df[OUTPUT_COLUMNS]


def load_manual_moneyline_odds(date: str) -> pd.DataFrame:
    path = manual_odds_path(date)
    if not path.exists():
        logger.info("No manual odds file for %s at %s", date, path)
        return _empty_odds()
    df = pd.read_csv(path)
    for col in _OPTIONAL_COLS:
        if col not in df.columns:
            df[col] = pd.NA
    if "source_type" not in df.columns:
        df["source_type"] = "manual"
    logger.info("Loaded %d manual odds rows for %s", len(df), date)
    return _finalize(df, "manual")


def load_api_moneyline_odds(date: str) -> pd.DataFrame:
    """Scaffold for an odds API collector (e.g. The Odds API).

    Not required for the project to work. Returns empty and logs if the API is
    not configured. Implementations should still respect rate limits and save
    raw JSON to ``data/raw/odds/YYYY-MM-DD/``.
    """
    if not config.ODDS_API_KEY:
        logger.warning(
            "ODDS_SOURCE=api but ODDS_API_KEY is not set; cannot fetch odds for %s",
            date,
        )
        return _empty_odds()

    # --- Scaffold only ---------------------------------------------------
    # Example (left unimplemented on purpose to avoid unverified network calls):
    #
    #   import requests
    #   url = "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds"
    #   params = {"apiKey": config.ODDS_API_KEY, "regions": "us",
    #             "markets": "h2h", "oddsFormat": "american", "date": date}
    #   resp = requests.get(url, params=params, timeout=30)
    #   ... save raw to data/raw/odds/<date>/ ...
    #   ... map to OUTPUT_COLUMNS, source_type="api" ...
    #
    logger.warning(
        "API odds collector is a scaffold and not yet implemented; "
        "falling back to manual for %s",
        date,
    )
    return _empty_odds()


def load_moneyline_odds(date: str) -> pd.DataFrame:
    """Load moneyline odds for ``date`` from the configured source.

    Always returns a DataFrame with :data:`OUTPUT_COLUMNS`. Falls back to
    manual CSV if the API source yields nothing.
    """
    if config.ODDS_SOURCE == "api":
        df = load_api_moneyline_odds(date)
        if not df.empty:
            return df
        # fall through to manual as a safety net
        return load_manual_moneyline_odds(date)
    return load_manual_moneyline_odds(date)
