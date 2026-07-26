"""Sportsbook moneyline odds loader.

The first-class path is a manual CSV per date:

    data/manual/mlb_moneyline_odds_YYYY-MM-DD.csv

An API collector is scaffolded (The Odds API-style) but not required; it only
activates when ODDS_SOURCE=api and ODDS_API_KEY is set.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import time
from pathlib import Path

import pandas as pd

try:
    import requests
except Exception:  # pragma: no cover
    requests = None  # type: ignore

try:
    from zoneinfo import ZoneInfo

    _ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - fall back to fixed UTC-4 if tzdata absent
    _ET = _dt.timezone(_dt.timedelta(hours=-4))

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


# ---------------------------------------------------------------------------
# The Odds API (the-odds-api.com) collector
# ---------------------------------------------------------------------------
def _raw_odds_dir(date: str) -> Path:
    d = config.RAW_ODDS_DIR / date
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save_raw_odds(date: str, name: str, payload) -> None:
    if not config.SAVE_RAW_RESPONSES:
        return
    path = _raw_odds_dir(date) / name
    try:
        with open(path, "w") as fh:
            json.dump(payload, fh)
        logger.info("Saved raw odds response: %s", path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not save raw odds (%s): %s", path, exc)


def _odds_api_get(url: str, params: dict) -> dict | list | None:
    """GET with basic retry/backoff. Returns parsed JSON or None."""
    if requests is None:
        logger.error("requests is not installed; cannot call The Odds API")
        return None
    last_exc: Exception | None = None
    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            resp = requests.get(url, params=params, timeout=30)
            if resp.status_code == 401:
                logger.error("The Odds API returned 401 (bad/expired ODDS_API_KEY)")
                return None
            if resp.status_code == 422:
                logger.error(
                    "The Odds API 422 for %s (invalid params, or historical not on "
                    "your plan): %s",
                    url,
                    resp.text[:300],
                )
                return None
            if resp.status_code == 429:
                logger.warning("The Odds API 429 rate-limited; backing off")
                time.sleep(min(2 ** attempt, 16))
                continue
            resp.raise_for_status()
            # Useful quota headers, if present.
            remaining = resp.headers.get("x-requests-remaining")
            if remaining is not None:
                logger.info("The Odds API requests remaining: %s", remaining)
            return resp.json()
        except Exception as exc:  # noqa: BLE001 - network robustness
            last_exc = exc
            wait = min(2 ** attempt, 16)
            logger.warning(
                "The Odds API GET failed (%s) attempt %d/%d: %s; retry in %ss",
                url,
                attempt,
                config.MAX_RETRIES,
                exc,
                wait,
            )
            time.sleep(wait)
        finally:
            if config.SLEEP_SECONDS:
                time.sleep(config.SLEEP_SECONDS)
    logger.error("The Odds API GET permanently failed for %s: %s", url, last_exc)
    return None


def _eastern_date_of(commence_iso: str) -> str | None:
    """Return the US/Eastern calendar date (YYYY-MM-DD) of a UTC ISO timestamp.

    MLB "official date" tracks local ballpark date closely; US/Eastern is a good
    single-tz approximation for joining odds to game dates.
    """
    if not commence_iso:
        return None
    try:
        ts = _dt.datetime.fromisoformat(commence_iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=_dt.timezone.utc)
    return ts.astimezone(_ET).date().isoformat()


def _snapshot_timestamp(date: str) -> str:
    """ISO8601 Z timestamp at the configured ET hour of ``date`` (pre-game)."""
    d = _dt.date.fromisoformat(date)
    local = _dt.datetime(
        d.year, d.month, d.day, config.ODDS_API_SNAPSHOT_HOUR_ET, 0, 0, tzinfo=_ET
    )
    return local.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_odds_api_games(games: list, date: str, snapshot_iso: str | None) -> list[dict]:
    """Flatten The Odds API game/bookmaker/h2h structure into odds rows.

    One row per (game, bookmaker) that carries an h2h market. Only games whose
    US/Eastern commence date equals ``date`` are kept.
    """
    rows: list[dict] = []
    allowed_books = {
        b.strip().lower()
        for b in config.ODDS_API_BOOKMAKERS.split(",")
        if b.strip()
    }
    observed = snapshot_iso or _dt.datetime.now(_dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    for game in games or []:
        commence = game.get("commence_time")
        game_date = _eastern_date_of(commence)
        if game_date != date:
            continue
        home_raw = game.get("home_team")
        away_raw = game.get("away_team")
        if not home_raw or not away_raw:
            continue
        home = normalize_team_name(home_raw)
        away = normalize_team_name(away_raw)

        for book in game.get("bookmakers", []) or []:
            book_key = (book.get("key") or "").lower()
            if allowed_books and book_key not in allowed_books:
                continue
            h2h = next(
                (m for m in book.get("markets", []) or [] if m.get("key") == "h2h"),
                None,
            )
            if not h2h:
                continue
            price_by_team: dict[str, float] = {}
            for outcome in h2h.get("outcomes", []) or []:
                name = outcome.get("name")
                price = outcome.get("price")
                if name is None or price is None:
                    continue
                price_by_team[normalize_team_name(name)] = price
            if home not in price_by_team or away not in price_by_team:
                continue

            rows.append(
                {
                    "odds_observed_at_utc": book.get("last_update") or observed,
                    "date": date,
                    "sportsbook": book.get("title") or book.get("key"),
                    "away_team": away,
                    "home_team": home,
                    "away_moneyline": int(price_by_team[away]),
                    "home_moneyline": int(price_by_team[home]),
                    "opening_away_moneyline": pd.NA,
                    "opening_home_moneyline": pd.NA,
                    "closing_away_moneyline": pd.NA,
                    "closing_home_moneyline": pd.NA,
                    "source_type": "api",
                    "source_notes": (
                        f"the-odds-api {config.ODDS_API_SPORT} h2h"
                        + (f" @ snapshot {snapshot_iso}" if snapshot_iso else " (current)")
                    ),
                }
            )
    return rows


def _fetch_current_odds(date: str) -> list:
    url = f"{config.ODDS_API_BASE}/v4/sports/{config.ODDS_API_SPORT}/odds"
    params = {
        "apiKey": config.ODDS_API_KEY,
        "regions": config.ODDS_API_REGIONS,
        "markets": "h2h",
        "oddsFormat": "american",
        "dateFormat": "iso",
    }
    if config.ODDS_API_BOOKMAKERS:
        params["bookmakers"] = config.ODDS_API_BOOKMAKERS
    data = _odds_api_get(url, params)
    if data is None:
        return []
    _save_raw_odds(date, "odds_api_current.json", data)
    return data if isinstance(data, list) else []


def _fetch_historical_odds(date: str) -> tuple[list, str | None]:
    """Fetch a historical snapshot near noon ET of ``date``.

    Returns (games, snapshot_iso). Historical requires a paid Odds API plan.
    """
    snapshot_iso = _snapshot_timestamp(date)
    url = (
        f"{config.ODDS_API_BASE}/v4/historical/sports/"
        f"{config.ODDS_API_SPORT}/odds"
    )
    params = {
        "apiKey": config.ODDS_API_KEY,
        "regions": config.ODDS_API_REGIONS,
        "markets": "h2h",
        "oddsFormat": "american",
        "dateFormat": "iso",
        "date": snapshot_iso,
    }
    if config.ODDS_API_BOOKMAKERS:
        params["bookmakers"] = config.ODDS_API_BOOKMAKERS
    data = _odds_api_get(url, params)
    if data is None:
        return [], snapshot_iso
    _save_raw_odds(date, "odds_api_historical.json", data)
    # Historical responses wrap games in {"timestamp":..., "data":[...]}.
    if isinstance(data, dict):
        return (data.get("data") or [], data.get("timestamp") or snapshot_iso)
    if isinstance(data, list):
        return (data, snapshot_iso)
    return [], snapshot_iso


def _is_past_date(date: str) -> bool:
    try:
        return _dt.date.fromisoformat(date) < _dt.date.today()
    except ValueError:
        return False


def load_api_moneyline_odds(date: str) -> pd.DataFrame:
    """Fetch MLB moneyline odds for ``date`` from The Odds API.

    Mode is controlled by ODDS_API_MODE:
      * "historical" -> snapshot near noon ET of ``date`` (needs paid plan)
      * "current"    -> live/upcoming odds only
      * "auto"       -> historical for past dates, current for today/future

    Saves raw JSON to data/raw/odds/<date>/ and returns rows shaped like the
    manual loader (source_type="api"). Returns empty (never raises) on any
    failure so the caller can fall back to manual CSV.
    """
    if not config.ODDS_API_KEY:
        logger.warning(
            "ODDS_SOURCE=api but ODDS_API_KEY is not set; cannot fetch odds for %s",
            date,
        )
        return _empty_odds()

    mode = config.ODDS_API_MODE
    use_historical = mode == "historical" or (mode == "auto" and _is_past_date(date))

    if use_historical:
        games, snapshot_iso = _fetch_historical_odds(date)
        rows = _parse_odds_api_games(games, date, snapshot_iso)
        if rows:
            return _finalize(pd.DataFrame(rows), "api")
        logger.warning(
            "The Odds API historical returned no MLB h2h rows for %s "
            "(empty snapshot, plan without historical access, or no games).",
            date,
        )
        # In auto mode, don't try current for a clearly past date (prices would
        # be stale/irrelevant); just report empty.
        if mode == "historical":
            return _empty_odds()
        if _is_past_date(date):
            return _empty_odds()

    games = _fetch_current_odds(date)
    rows = _parse_odds_api_games(games, date, None)
    if rows:
        return _finalize(pd.DataFrame(rows), "api")
    logger.warning("The Odds API returned no MLB h2h rows for %s", date)
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
