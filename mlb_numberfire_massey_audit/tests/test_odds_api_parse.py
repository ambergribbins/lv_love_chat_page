"""Offline tests for The Odds API JSON -> odds-row mapping.

No network: we feed synthetic payloads (shaped like the-odds-api.com v4) into
the pure parsing helpers and assert the resulting rows.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import odds_collector as oc  # noqa: E402


# A 7:10pm ET game on 2026-07-25 -> 23:10 UTC same calendar day.
_GAME_TODAY = {
    "id": "abc123",
    "sport_key": "baseball_mlb",
    "commence_time": "2026-07-25T23:10:00Z",
    "home_team": "Boston Red Sox",
    "away_team": "New York Yankees",
    "bookmakers": [
        {
            "key": "draftkings",
            "title": "DraftKings",
            "last_update": "2026-07-25T16:00:00Z",
            "markets": [
                {
                    "key": "h2h",
                    "outcomes": [
                        {"name": "Boston Red Sox", "price": 105},
                        {"name": "New York Yankees", "price": -115},
                    ],
                }
            ],
        },
        {
            "key": "fanduel",
            "title": "FanDuel",
            "last_update": "2026-07-25T16:00:00Z",
            "markets": [
                {
                    "key": "h2h",
                    "outcomes": [
                        {"name": "Boston Red Sox", "price": 100},
                        {"name": "New York Yankees", "price": -110},
                    ],
                }
            ],
        },
    ],
}

# A late West-coast game whose ET date is the NEXT day -> must be excluded when
# querying 2026-07-25 (10:15pm PT on 7/25 = 05:15 UTC 7/26 = 1:15am ET 7/26).
_GAME_OTHER_DATE = {
    "id": "def456",
    "sport_key": "baseball_mlb",
    "commence_time": "2026-07-26T05:15:00Z",
    "home_team": "Los Angeles Dodgers",
    "away_team": "San Francisco Giants",
    "bookmakers": [
        {
            "key": "draftkings",
            "title": "DraftKings",
            "markets": [
                {
                    "key": "h2h",
                    "outcomes": [
                        {"name": "Los Angeles Dodgers", "price": -140},
                        {"name": "San Francisco Giants", "price": 120},
                    ],
                }
            ],
        }
    ],
}


def test_eastern_date_of():
    assert oc._eastern_date_of("2026-07-25T23:10:00Z") == "2026-07-25"
    # 05:15 UTC on the 26th is 01:15 ET on the 26th.
    assert oc._eastern_date_of("2026-07-26T05:15:00Z") == "2026-07-26"


def test_parse_maps_home_away_and_book():
    rows = oc._parse_odds_api_games(
        [_GAME_TODAY, _GAME_OTHER_DATE], "2026-07-25", "2026-07-25T16:00:00Z"
    )
    # Only the 7/25 game, two books -> two rows.
    assert len(rows) == 2
    dk = next(r for r in rows if r["sportsbook"] == "DraftKings")
    assert dk["home_team"] == "BOS"
    assert dk["away_team"] == "NYY"
    assert dk["home_moneyline"] == 105
    assert dk["away_moneyline"] == -115
    assert dk["source_type"] == "api"
    assert dk["date"] == "2026-07-25"


def test_other_date_game_excluded():
    rows = oc._parse_odds_api_games([_GAME_OTHER_DATE], "2026-07-25", None)
    assert rows == []


def test_bookmaker_filter(monkeypatch):
    monkeypatch.setattr(oc.config, "ODDS_API_BOOKMAKERS", "fanduel")
    rows = oc._parse_odds_api_games([_GAME_TODAY], "2026-07-25", None)
    assert len(rows) == 1
    assert rows[0]["sportsbook"] == "FanDuel"


def test_historical_snapshot_timestamp_is_noon_et(monkeypatch):
    monkeypatch.setattr(oc.config, "ODDS_API_SNAPSHOT_HOUR_ET", 12)
    ts = oc._snapshot_timestamp("2026-07-25")
    # Noon ET on 2026-07-25 (EDT, UTC-4) == 16:00 UTC.
    assert ts == "2026-07-25T16:00:00Z"


def test_load_api_no_key_returns_empty(monkeypatch):
    monkeypatch.setattr(oc.config, "ODDS_API_KEY", "")
    df = oc.load_api_moneyline_odds("2026-07-25")
    assert df.empty
    assert list(df.columns) == oc.OUTPUT_COLUMNS
