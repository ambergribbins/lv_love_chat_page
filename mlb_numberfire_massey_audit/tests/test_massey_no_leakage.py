import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from massey import (  # noqa: E402
    calculate_massey_ratings,
    get_games_before_date,
    massey_projected_home_margin,
)


def _fake_games():
    """Games before, on, and after the target date 2026-05-10."""
    return pd.DataFrame(
        [
            # --- before target date ---
            {"date": "2026-05-01", "home_team": "NYY", "away_team": "BOS",
             "home_score": 5, "away_score": 1},
            {"date": "2026-05-02", "home_team": "BOS", "away_team": "TB",
             "home_score": 2, "away_score": 4},
            {"date": "2026-05-05", "home_team": "TB", "away_team": "NYY",
             "home_score": 3, "away_score": 3},
            # --- ON target date (must be excluded) ---
            {"date": "2026-05-10", "home_team": "NYY", "away_team": "TB",
             "home_score": 10, "away_score": 0},
            # --- after target date (must be excluded) ---
            {"date": "2026-05-15", "home_team": "BOS", "away_team": "NYY",
             "home_score": 9, "away_score": 1},
        ]
    )


TARGET = "2026-05-10"


def test_get_games_before_date_excludes_on_and_after():
    prior = get_games_before_date(_fake_games(), TARGET)
    dates = set(prior["date"])
    assert dates == {"2026-05-01", "2026-05-02", "2026-05-05"}
    assert TARGET not in dates
    assert "2026-05-15" not in dates


def test_ratings_use_only_prior_games():
    all_games = _fake_games()
    prior = get_games_before_date(all_games, TARGET)
    ratings = calculate_massey_ratings(prior)
    # Only NYY, BOS, TB appear (3 teams, 3 prior games).
    assert set(ratings["team"]) == {"NYY", "BOS", "TB"}
    # Ratings sum to zero (constraint row).
    assert abs(ratings["massey_rating"].sum()) < 1e-8


def test_same_day_result_does_not_change_rating():
    all_games = _fake_games()

    prior = get_games_before_date(all_games, TARGET)
    ratings_prior = calculate_massey_ratings(prior)
    margin_prior = massey_projected_home_margin("NYY", "TB", ratings_prior, 0.15)

    # Now include the same-day blowout and recompute WITHOUT the leakage guard.
    including_same_day = all_games[all_games["date"] <= TARGET]
    ratings_leaked = calculate_massey_ratings(including_same_day)
    margin_leaked = massey_projected_home_margin("NYY", "TB", ratings_leaked, 0.15)

    # The 10-0 same-day result would sharply change NYY's rating; proving the
    # guarded ratings are unaffected demonstrates no leakage.
    assert margin_prior != margin_leaked


def test_insufficient_data_returns_none_margin():
    ratings = calculate_massey_ratings(pd.DataFrame(columns=[
        "date", "home_team", "away_team", "home_score", "away_score"]))
    assert ratings.empty
    assert massey_projected_home_margin("NYY", "BOS", ratings) is None
