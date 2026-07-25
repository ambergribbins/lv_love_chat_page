import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mlb_results import grade_moneyline_pick  # noqa: E402


def test_home_team_wins_pick_home():
    # BOS home beats NYY away 5-3, pick home -> W
    assert grade_moneyline_pick("BOS", "BOS", "NYY", 5, 3) == "W"


def test_away_team_wins_pick_away():
    # NYY away beats BOS home 6-2, pick away -> W
    assert grade_moneyline_pick("NYY", "BOS", "NYY", 2, 6) == "W"


def test_pick_loses():
    # Pick BOS (home), NYY wins -> L
    assert grade_moneyline_pick("BOS", "BOS", "NYY", 2, 6) == "L"


def test_pick_wins_with_alias():
    # Pick 'Oakland Athletics' which normalizes to ATH, ATH home wins
    assert grade_moneyline_pick("Oakland Athletics", "ATH", "SEA", 4, 1) == "W"


def test_push_on_tie():
    assert grade_moneyline_pick("BOS", "BOS", "NYY", 3, 3) == "P"
