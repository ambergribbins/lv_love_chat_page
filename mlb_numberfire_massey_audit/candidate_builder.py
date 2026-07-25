"""Build numberFire value candidates and join Massey as an independent filter.

Faithful to the original chat model:
    predictive_model_edge = nf_probability - market_implied_probability
    theoretical_ev        = EV from nf_probability and American odds
Massey is joined *afterward* as a separate agreement label -- it is never
averaged into the numberFire probability.
"""

from __future__ import annotations

import logging

import pandas as pd

import config
from massey import (
    calculate_massey_ratings,
    massey_agreement_for_pick,
    massey_projected_home_margin,
)
from numberfire_scraper import get_numberfire_predictions
from odds_collector import load_moneyline_odds
from odds_math import (
    american_to_implied_prob,
    ev_from_prob_and_american_odds,
    model_edge,
)
from team_map import normalize_team_name

logger = logging.getLogger(__name__)

CANDIDATE_COLUMNS = [
    "date",
    "gamePk",
    "sportsbook",
    "away_team",
    "home_team",
    "pick_team",
    "pick_side_home_away",
    "moneyline",
    "nf_probability",
    "market_implied_probability",
    "predictive_model_edge",
    "theoretical_ev",
    "value_label",
    "nf_source_url",
    "nf_data_quality",
    "odds_source_type",
    "notes",
]

MASSEY_CANDIDATE_COLUMNS = [
    "home_massey_rating",
    "away_massey_rating",
    "massey_projected_home_margin",
    "massey_pick_margin",
    "massey_agreement",
    "massey_games_used",
    "massey_status",
]


def _empty_candidates() -> pd.DataFrame:
    return pd.DataFrame(columns=CANDIDATE_COLUMNS)


def _classify(edge: float, ev: float) -> str:
    if edge >= config.EDGE_THRESHOLD_NF and ev > config.MIN_EV_THRESHOLD:
        return "value_candidate"
    if edge > 0:
        return "lean_only"
    return "pass"


def build_numberfire_candidates(
    date: str,
    nf_df: pd.DataFrame | None = None,
    odds_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Create one candidate row per team side where numberFire + odds both exist.

    ``nf_df`` / ``odds_df`` may be injected (used by the backtest to avoid
    re-fetching); otherwise they are loaded for ``date``.
    """
    if nf_df is None:
        nf_df = get_numberfire_predictions(date)
    if odds_df is None:
        odds_df = load_moneyline_odds(date)

    if nf_df is None or nf_df.empty:
        logger.warning("No numberFire predictions for %s; no candidates", date)
        return _empty_candidates()
    if odds_df is None or odds_df.empty:
        logger.warning("No odds for %s; no candidates", date)
        return _empty_candidates()

    nf = nf_df.copy()
    odds = odds_df.copy()
    for col in ("home_team", "away_team"):
        nf[col] = nf[col].map(normalize_team_name)
        odds[col] = odds[col].map(normalize_team_name)

    merged = nf.merge(
        odds,
        on=["date", "home_team", "away_team"],
        how="inner",
        suffixes=("_nf", "_odds"),
    )
    if merged.empty:
        logger.warning(
            "numberFire and odds did not join on %s (team/date mismatch)", date
        )
        return _empty_candidates()

    rows: list[dict] = []
    for r in merged.to_dict(orient="records"):
        home = r["home_team"]
        away = r["away_team"]
        for side in ("home", "away"):
            if side == "home":
                team = home
                ml = r.get("home_moneyline")
                prob = r.get("nf_home_win_prob")
            else:
                team = away
                ml = r.get("away_moneyline")
                prob = r.get("nf_away_win_prob")

            if ml is None or pd.isna(ml) or prob is None or pd.isna(prob):
                continue
            try:
                ml = int(ml)
                prob = float(prob)
            except (TypeError, ValueError):
                continue

            implied = american_to_implied_prob(ml)
            edge = model_edge(prob, ml)
            ev = ev_from_prob_and_american_odds(prob, ml)

            rows.append(
                {
                    "date": date,
                    "gamePk": r.get("gamePk"),
                    "sportsbook": r.get("sportsbook"),
                    "away_team": away,
                    "home_team": home,
                    "pick_team": team,
                    "pick_side_home_away": side,
                    "moneyline": ml,
                    "nf_probability": round(prob, 4),
                    "market_implied_probability": round(implied, 4),
                    "predictive_model_edge": round(edge, 4),
                    "theoretical_ev": round(ev, 4),
                    "value_label": _classify(edge, ev),
                    "nf_source_url": r.get("source_url"),
                    "nf_data_quality": r.get("data_quality"),
                    "odds_source_type": r.get("source_type"),
                    "notes": r.get("parse_notes"),
                }
            )

    if not rows:
        return _empty_candidates()
    return pd.DataFrame(rows)[CANDIDATE_COLUMNS]


def add_massey_to_candidates(
    candidates_df: pd.DataFrame,
    completed_games_before_date: pd.DataFrame,
) -> pd.DataFrame:
    """Attach Massey agreement to candidates using only prior completed games.

    ``completed_games_before_date`` MUST already be filtered to games before the
    prediction date (see massey.get_games_before_date). This function does not
    re-filter, so the caller owns the no-leakage guarantee.
    """
    df = candidates_df.copy()
    for col in MASSEY_CANDIDATE_COLUMNS:
        df[col] = pd.NA

    if df.empty:
        return df

    if completed_games_before_date is None or completed_games_before_date.empty:
        df["massey_agreement"] = "insufficient_data"
        df["massey_status"] = "insufficient_data"
        df["massey_games_used"] = 0
        return df

    ratings = calculate_massey_ratings(completed_games_before_date)
    if ratings.empty:
        df["massey_agreement"] = "insufficient_data"
        df["massey_status"] = "insufficient_data"
        df["massey_games_used"] = 0
        return df

    rating_lookup = ratings.set_index("team")["massey_rating"]
    games_used = int(len(completed_games_before_date))

    for i, r in df.iterrows():
        home = r["home_team"]
        away = r["away_team"]
        pick = r["pick_team"]
        home_rating = (
            float(rating_lookup[home]) if home in rating_lookup.index else None
        )
        away_rating = (
            float(rating_lookup[away]) if away in rating_lookup.index else None
        )
        margin = massey_projected_home_margin(
            home, away, ratings, config.DEFAULT_HOME_FIELD_RUNS
        )
        agreement = massey_agreement_for_pick(pick, home, away, margin)

        # pick-relative margin: positive means Massey favors the pick.
        pick_margin = None
        if margin is not None:
            pick_margin = margin if pick == home else -margin

        df.at[i, "home_massey_rating"] = home_rating
        df.at[i, "away_massey_rating"] = away_rating
        df.at[i, "massey_projected_home_margin"] = (
            round(margin, 4) if margin is not None else pd.NA
        )
        df.at[i, "massey_pick_margin"] = (
            round(pick_margin, 4) if pick_margin is not None else pd.NA
        )
        df.at[i, "massey_agreement"] = agreement
        df.at[i, "massey_games_used"] = games_used
        df.at[i, "massey_status"] = (
            "ok" if agreement != "insufficient_data" else "insufficient_data"
        )

    return df
