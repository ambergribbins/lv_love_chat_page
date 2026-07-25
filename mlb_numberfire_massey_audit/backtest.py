"""Backtest: numberFire alone vs numberFire + Massey filter.

No future leakage:
  * Massey ratings for date D use only games completed strictly before D
    (via massey.get_games_before_date).
  * Target-date final scores are used ONLY to grade results, never to build
    that date's Massey ratings.
  * Entry price is the observed/opening moneyline, not the closing line
    (closing-only diagnostics are out of scope here).
"""

from __future__ import annotations

import datetime as _dt
import logging

import pandas as pd

import config
from candidate_builder import (
    add_massey_to_candidates,
    build_numberfire_candidates,
)
from massey import get_games_before_date
from mlb_results import (
    get_mlb_completed_games,
    get_mlb_final_scores,
    grade_moneyline_pick,
)
from numberfire_scraper import get_numberfire_predictions
from odds_collector import load_moneyline_odds
from odds_math import profit_1u_risked

logger = logging.getLogger(__name__)

GRADED_COLUMNS = [
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
    "massey_agreement",
    "massey_projected_home_margin",
    "massey_pick_margin",
    "massey_games_used",
    "massey_status",
    "home_score",
    "away_score",
    "result",
    "pnl_1u",
    "nf_data_quality",
    "odds_source_type",
    "nf_source_url",
    "notes",
]


def _daterange(start_date: str, end_date: str):
    start = _dt.date.fromisoformat(start_date)
    end = _dt.date.fromisoformat(end_date)
    cur = start
    while cur <= end:
        yield cur.isoformat()
        cur += _dt.timedelta(days=1)


def _grade_candidates_for_date(
    date: str,
    candidates: pd.DataFrame,
    finals: pd.DataFrame,
) -> list[dict]:
    """Grade each candidate against final scores; skip ungradable games."""
    graded: list[dict] = []
    if candidates.empty:
        return graded

    # Build a lookup of finals by normalized matchup. gamePk preferred.
    finals = finals.copy()
    for r in candidates.to_dict(orient="records"):
        match = None
        gp = r.get("gamePk")
        if gp is not None and not pd.isna(gp) and "gamePk" in finals.columns:
            hit = finals[finals["gamePk"] == gp]
            if not hit.empty:
                match = hit.iloc[0]
        if match is None:
            hit = finals[
                (finals["home_team"] == r["home_team"])
                & (finals["away_team"] == r["away_team"])
            ]
            if len(hit) == 1:
                match = hit.iloc[0]
            elif len(hit) > 1:
                logger.warning(
                    "Multiple finals for %s %s@%s (doubleheader) and no gamePk; "
                    "skipping to avoid mis-grading",
                    date,
                    r["away_team"],
                    r["home_team"],
                )
                continue

        if match is None:
            logger.info(
                "No final score for %s %s@%s; skipping",
                date,
                r["away_team"],
                r["home_team"],
            )
            continue

        try:
            result = grade_moneyline_pick(
                r["pick_team"],
                r["home_team"],
                r["away_team"],
                match["home_score"],
                match["away_score"],
            )
        except ValueError as exc:
            logger.warning("Grading error on %s: %s", date, exc)
            continue

        pnl = profit_1u_risked(r["moneyline"], result)
        out = {k: r.get(k) for k in GRADED_COLUMNS if k in r}
        out.update(
            {
                "home_score": match["home_score"],
                "away_score": match["away_score"],
                "result": result,
                "pnl_1u": round(pnl, 4),
            }
        )
        graded.append(out)
    return graded


def backtest_numberfire_massey(start_date: str, end_date: str) -> pd.DataFrame:
    """Run the full backtest over an inclusive date range.

    Returns a graded candidate DataFrame (also written to
    data/results/nf_massey_candidates_START_END.csv by the reports step).
    """
    config.ensure_directories()

    # Pull all completed games once, from season start through end_date, so we
    # can slice "games before date D" cheaply and leak-free per date.
    season_start = f"{_dt.date.fromisoformat(start_date).year}-01-01"
    all_completed = get_mlb_completed_games(season_start, end_date)
    logger.info(
        "Loaded %d completed games %s..%s for Massey history",
        len(all_completed),
        season_start,
        end_date,
    )

    graded_rows: list[dict] = []
    unavailable_nf: list[str] = []
    unavailable_odds: list[str] = []

    for date in _daterange(start_date, end_date):
        nf_df = get_numberfire_predictions(date)
        odds_df = load_moneyline_odds(date)

        if nf_df is None or nf_df.empty:
            unavailable_nf.append(date)
        if odds_df is None or odds_df.empty:
            unavailable_odds.append(date)

        candidates = build_numberfire_candidates(date, nf_df=nf_df, odds_df=odds_df)
        if candidates.empty:
            continue

        # Massey: strictly-prior games only.
        prior_games = get_games_before_date(all_completed, date)
        candidates = add_massey_to_candidates(candidates, prior_games)

        # Final scores for grading (target-date only used for results).
        finals = get_mlb_final_scores(date)
        finals = finals[finals["final_flag"] == True]  # noqa: E712
        finals = finals.dropna(subset=["home_score", "away_score"])

        graded_rows.extend(_grade_candidates_for_date(date, candidates, finals))

    if not graded_rows:
        logger.warning(
            "Backtest produced no graded candidates for %s..%s. "
            "Likely missing numberFire predictions and/or odds. "
            "nf unavailable dates: %d, odds unavailable dates: %d",
            start_date,
            end_date,
            len(unavailable_nf),
            len(unavailable_odds),
        )
        df = pd.DataFrame(columns=GRADED_COLUMNS)
    else:
        df = pd.DataFrame(graded_rows)
        for c in GRADED_COLUMNS:
            if c not in df.columns:
                df[c] = pd.NA
        df = df[GRADED_COLUMNS]

    # Stash availability metadata as attrs for the reports layer.
    df.attrs["unavailable_nf_dates"] = unavailable_nf
    df.attrs["unavailable_odds_dates"] = unavailable_odds
    df.attrs["start_date"] = start_date
    df.attrs["end_date"] = end_date
    return df


# ---------------------------------------------------------------------------
# Summary metrics
# ---------------------------------------------------------------------------
def _record(df: pd.DataFrame) -> dict:
    """Compute record/P&L metrics for a slice (value candidates only)."""
    if df.empty:
        return {
            "bets": 0,
            "wins": 0,
            "losses": 0,
            "pushes": 0,
            "pnl_1u": 0.0,
            "roi": 0.0,
            "win_rate": 0.0,
            "avg_edge": 0.0,
            "avg_ev": 0.0,
            "avg_massey_margin": 0.0,
        }
    wins = int((df["result"] == "W").sum())
    losses = int((df["result"] == "L").sum())
    pushes = int((df["result"] == "P").sum())
    bets = len(df)
    pnl = float(df["pnl_1u"].sum())
    decided = wins + losses
    return {
        "bets": bets,
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "pnl_1u": round(pnl, 4),
        "roi": round(pnl / bets, 4) if bets else 0.0,
        "win_rate": round(wins / decided, 4) if decided else 0.0,
        "avg_edge": round(float(df["predictive_model_edge"].mean()), 4),
        "avg_ev": round(float(df["theoretical_ev"].mean()), 4),
        "avg_massey_margin": round(
            float(pd.to_numeric(df["massey_projected_home_margin"], errors="coerce").mean()),
            4,
        )
        if df["massey_projected_home_margin"].notna().any()
        else 0.0,
    }


def summarize_backtest(df: pd.DataFrame) -> dict:
    """Produce the full set of comparison-group summaries."""
    summary: dict = {
        "start_date": df.attrs.get("start_date"),
        "end_date": df.attrs.get("end_date"),
        "unavailable_nf_dates": df.attrs.get("unavailable_nf_dates", []),
        "unavailable_odds_dates": df.attrs.get("unavailable_odds_dates", []),
    }

    if df.empty:
        summary["value_candidates"] = _record(df)
        return summary

    # numberFire value candidates only (the baseline strategy).
    value = df[df["value_label"] == "value_candidate"].copy()
    summary["value_candidates"] = _record(value)

    # numberFire + Massey buckets (within value candidates).
    for bucket in ("agree", "neutral", "conflict", "insufficient_data"):
        summary[f"massey_{bucket}"] = _record(
            value[value["massey_agreement"] == bucket]
        )

    # Edge buckets (on value candidates).
    edge = pd.to_numeric(value["predictive_model_edge"], errors="coerce")
    summary["edge_ge_3pp"] = _record(value[edge >= 0.03])
    summary["edge_ge_5pp"] = _record(value[edge >= 0.05])
    summary["edge_0_2pp"] = _record(value[(edge >= 0.0) & (edge < 0.02)])
    summary["edge_2_3pp"] = _record(value[(edge >= 0.02) & (edge < 0.03)])
    summary["edge_3_5pp"] = _record(value[(edge >= 0.03) & (edge < 0.05)])

    # Favorite / dog split.
    ml = pd.to_numeric(value["moneyline"], errors="coerce")
    summary["plus_money_dogs"] = _record(value[ml > 0])
    summary["favorites"] = _record(value[ml < 0])

    # By sportsbook.
    by_book = {}
    for book, sub in value.groupby(value["sportsbook"].fillna("unknown")):
        by_book[str(book)] = _record(sub)
    summary["by_sportsbook"] = by_book

    # By data quality.
    by_dq = {}
    for dq, sub in value.groupby(value["nf_data_quality"].fillna("unknown")):
        by_dq[str(dq)] = _record(sub)
    summary["by_data_quality"] = by_dq

    return summary
