"""Pre-game Massey ratings from score differential.

Massey's method solves, for every game, a linear system where the difference
of two team ratings equals the score margin:

    home_rating - away_rating = home_score - away_score

Assembled over all games:
    M[home, home] += 1;  M[away, away] += 1
    M[home, away] -= 1;  M[away, home] -= 1
    b[home]       += margin
    b[away]       -= margin

The system is singular (ratings are only defined up to an additive constant),
so we replace the final row of M with all ones and the final b entry with 0,
forcing ratings to sum to zero.

CRITICAL (no leakage): ratings for a given prediction date must use ONLY
games completed *before* that date. :func:`get_games_before_date` enforces this
and the backtest calls it per date. Never pass full-season games in.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

import config
from team_map import normalize_team_name

logger = logging.getLogger(__name__)

MASSEY_COLUMNS = ["team", "massey_rating", "games_played", "avg_run_diff"]


def get_games_before_date(all_games_df: pd.DataFrame, target_date: str) -> pd.DataFrame:
    """Return only games strictly before ``target_date`` (YYYY-MM-DD).

    Excludes games on the target date and any games after it. This is the
    leakage guard for the entire backtest.
    """
    if all_games_df is None or all_games_df.empty:
        return all_games_df.copy() if all_games_df is not None else pd.DataFrame()
    df = all_games_df.copy()
    dates = pd.to_datetime(df["date"], errors="coerce")
    cutoff = pd.to_datetime(target_date)
    mask = dates < cutoff
    return df[mask].reset_index(drop=True)


def calculate_massey_ratings(games_df: pd.DataFrame) -> pd.DataFrame:
    """Compute Massey ratings from a set of completed games.

    Expects columns: home_team, away_team, home_score, away_score.
    The caller is responsible for having already filtered to games that occurred
    before the prediction date (use :func:`get_games_before_date`).
    """
    if games_df is None or games_df.empty:
        return pd.DataFrame(columns=MASSEY_COLUMNS)

    df = games_df.copy()
    df = df.dropna(subset=["home_team", "away_team", "home_score", "away_score"])
    if df.empty:
        return pd.DataFrame(columns=MASSEY_COLUMNS)

    df["home_team"] = df["home_team"].map(normalize_team_name)
    df["away_team"] = df["away_team"].map(normalize_team_name)
    df["home_score"] = df["home_score"].astype(float)
    df["away_score"] = df["away_score"].astype(float)

    teams = sorted(set(df["home_team"]) | set(df["away_team"]))
    if len(teams) < 2:
        return pd.DataFrame(columns=MASSEY_COLUMNS)
    idx = {t: i for i, t in enumerate(teams)}
    n = len(teams)

    M = np.zeros((n, n))
    b = np.zeros(n)

    games_played = {t: 0 for t in teams}
    run_diff_sum = {t: 0.0 for t in teams}

    for row in df.itertuples(index=False):
        h = idx[row.home_team]
        a = idx[row.away_team]
        margin = float(row.home_score) - float(row.away_score)
        M[h, h] += 1
        M[a, a] += 1
        M[h, a] -= 1
        M[a, h] -= 1
        b[h] += margin
        b[a] -= margin
        games_played[row.home_team] += 1
        games_played[row.away_team] += 1
        run_diff_sum[row.home_team] += margin
        run_diff_sum[row.away_team] -= margin

    # Constrain ratings to sum to zero: replace last row with ones, last b with 0.
    M[-1, :] = 1.0
    b[-1] = 0.0

    try:
        ratings = np.linalg.solve(M, b)
    except np.linalg.LinAlgError:
        logger.warning("Massey matrix singular; falling back to least squares")
        ratings, *_ = np.linalg.lstsq(M, b, rcond=None)

    out = pd.DataFrame(
        {
            "team": teams,
            "massey_rating": ratings,
            "games_played": [games_played[t] for t in teams],
            "avg_run_diff": [
                (run_diff_sum[t] / games_played[t]) if games_played[t] else 0.0
                for t in teams
            ],
        }
    )
    return out[MASSEY_COLUMNS]


def massey_projected_home_margin(
    home_team: str,
    away_team: str,
    ratings_df: pd.DataFrame,
    home_field_runs: float = config.DEFAULT_HOME_FIELD_RUNS,
) -> float | None:
    """Projected home margin in runs.

    projected_home_margin = home_rating - away_rating + home_field_runs

    Returns None if either team is missing from ``ratings_df`` (insufficient
    prior data).
    """
    if ratings_df is None or ratings_df.empty:
        return None
    home = normalize_team_name(home_team)
    away = normalize_team_name(away_team)
    lookup = ratings_df.set_index("team")["massey_rating"]
    if home not in lookup.index or away not in lookup.index:
        return None
    return float(lookup[home]) - float(lookup[away]) + float(home_field_runs)


def massey_agreement_for_pick(
    pick_team: str,
    home_team: str,
    away_team: str,
    projected_home_margin: float | None,
) -> str:
    """Classify Massey's view of a numberFire pick.

    Returns one of: 'agree', 'neutral', 'conflict', 'insufficient_data'.

    Thresholds come from config (MASSEY_AGREE_MARGIN_RUNS / _CONFLICT_).
    """
    if projected_home_margin is None:
        return "insufficient_data"

    pick = normalize_team_name(pick_team)
    home = normalize_team_name(home_team)
    away = normalize_team_name(away_team)
    m = float(projected_home_margin)
    agree_t = config.MASSEY_AGREE_MARGIN_RUNS
    conflict_t = config.MASSEY_CONFLICT_MARGIN_RUNS

    if pick == home:
        if m >= agree_t:
            return "agree"
        if m <= conflict_t:
            return "conflict"
        return "neutral"
    if pick == away:
        # From the away side, flip the sign of the margin.
        if m <= -agree_t:
            return "agree"
        if m >= agree_t:
            return "conflict"
        return "neutral"

    logger.warning(
        "massey_agreement_for_pick: pick %r not in matchup %r/%r", pick, home, away
    )
    return "insufficient_data"
