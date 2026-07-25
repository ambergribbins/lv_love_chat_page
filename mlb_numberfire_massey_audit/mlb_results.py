"""MLB final scores via the public MLB Stats API.

Preferred source: https://statsapi.mlb.com/api/v1/schedule

We key games by ``gamePk`` (unique per game) rather than date+teams, because
doubleheaders produce two games with the same date and teams.
"""

from __future__ import annotations

import logging
import time

import pandas as pd
import requests

import config
from team_map import normalize_team_name

logger = logging.getLogger(__name__)

STATS_API_SCHEDULE = "https://statsapi.mlb.com/api/v1/schedule"

RESULT_COLUMNS = [
    "gamePk",
    "date",
    "away_team",
    "home_team",
    "away_score",
    "home_score",
    "status",
    "final_flag",
    "doubleheader_flag",
    "game_number",
]


def _empty_results() -> pd.DataFrame:
    return pd.DataFrame(columns=RESULT_COLUMNS)


def _request_schedule(params: dict) -> dict | None:
    headers = {"User-Agent": config.NUMBERFIRE_USER_AGENT}
    last_exc: Exception | None = None
    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            resp = requests.get(
                STATS_API_SCHEDULE, params=params, headers=headers, timeout=30
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001 - network robustness
            last_exc = exc
            wait = 2 ** attempt
            logger.warning(
                "MLB schedule request failed (attempt %d/%d): %s; retrying in %ss",
                attempt,
                config.MAX_RETRIES,
                exc,
                wait,
            )
            time.sleep(min(wait, 16))
    logger.error("MLB schedule request permanently failed: %s", last_exc)
    return None


def _parse_schedule_json(payload: dict) -> pd.DataFrame:
    rows: list[dict] = []
    for day in payload.get("dates", []):
        date_str = day.get("date")
        for game in day.get("games", []):
            status = (game.get("status") or {}).get("detailedState", "")
            abstract = (game.get("status") or {}).get("abstractGameState", "")
            teams = game.get("teams", {})
            away = teams.get("away", {})
            home = teams.get("home", {})
            away_name = (away.get("team") or {}).get("name")
            home_name = (home.get("team") or {}).get("name")
            dh = game.get("doubleHeader", "N")
            rows.append(
                {
                    "gamePk": game.get("gamePk"),
                    "date": game.get("officialDate") or date_str,
                    "away_team": normalize_team_name(away_name) if away_name else None,
                    "home_team": normalize_team_name(home_name) if home_name else None,
                    "away_score": away.get("score"),
                    "home_score": home.get("score"),
                    "status": status,
                    "final_flag": abstract == "Final"
                    or status in {"Final", "Completed Early", "Game Over"},
                    "doubleheader_flag": dh in {"Y", "S"},
                    "game_number": game.get("gameNumber"),
                }
            )
    if not rows:
        return _empty_results()
    df = pd.DataFrame(rows)
    return df[RESULT_COLUMNS]


def get_mlb_final_scores(date: str) -> pd.DataFrame:
    """Return all MLB games for a single ``YYYY-MM-DD`` date.

    Includes non-final games (with ``final_flag`` marking completion) so the
    caller can distinguish scheduled/postponed from completed.
    """
    payload = _request_schedule(
        {"sportId": 1, "date": date, "hydrate": "team,linescore"}
    )
    if payload is None:
        logger.error("Could not fetch MLB scores for %s", date)
        return _empty_results()
    df = _parse_schedule_json(payload)
    if config.SLEEP_SECONDS:
        time.sleep(config.SLEEP_SECONDS)
    return df


def get_mlb_completed_games(start_date: str, end_date: str) -> pd.DataFrame:
    """Return all *completed* (final) MLB games in the inclusive date range."""
    payload = _request_schedule(
        {
            "sportId": 1,
            "startDate": start_date,
            "endDate": end_date,
            "hydrate": "team,linescore",
        }
    )
    if payload is None:
        logger.error(
            "Could not fetch MLB completed games for %s..%s", start_date, end_date
        )
        return _empty_results()
    df = _parse_schedule_json(payload)
    if df.empty:
        return df
    completed = df[df["final_flag"] == True].copy()  # noqa: E712
    # Drop games without numeric scores (e.g. postponed marked final oddly).
    completed = completed.dropna(subset=["away_score", "home_score"])
    completed["away_score"] = completed["away_score"].astype(int)
    completed["home_score"] = completed["home_score"].astype(int)
    if config.SLEEP_SECONDS:
        time.sleep(config.SLEEP_SECONDS)
    return completed.reset_index(drop=True)


def grade_moneyline_pick(
    pick_team: str,
    home_team: str,
    away_team: str,
    home_score,
    away_score,
) -> str:
    """Grade a moneyline pick. Returns 'W', 'L', or 'P' (push/tie).

    Teams are normalized before comparison. A tie (rare in MLB, but possible
    for suspended/called games) grades as a push.
    """
    pick = normalize_team_name(pick_team)
    home = normalize_team_name(home_team)
    away = normalize_team_name(away_team)

    hs = float(home_score)
    as_ = float(away_score)

    if hs == as_:
        return "P"
    winner = home if hs > as_ else away

    if pick == winner:
        return "W"
    if pick in {home, away}:
        return "L"
    # Pick team not in this game -> data error; surface as loss-safe push.
    raise ValueError(
        f"pick_team {pick!r} is neither home {home!r} nor away {away!r}"
    )
