import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from team_map import normalize_matchup, normalize_team_name  # noqa: E402


def test_oak_to_ath():
    assert normalize_team_name("OAK") == "ATH"


def test_oakland_athletics_to_ath():
    assert normalize_team_name("Oakland Athletics") == "ATH"
    assert normalize_team_name("Athletics") == "ATH"


def test_cws_to_chw():
    assert normalize_team_name("CWS") == "CHW"
    assert normalize_team_name("Chicago White Sox") == "CHW"


def test_wsn_to_wsh():
    assert normalize_team_name("WSN") == "WSH"
    assert normalize_team_name("Washington Nationals") == "WSH"


def test_misc_aliases():
    assert normalize_team_name("AZ") == "ARI"
    assert normalize_team_name("SDP") == "SD"
    assert normalize_team_name("SFG") == "SF"
    assert normalize_team_name("TBR") == "TB"
    assert normalize_team_name("KCR") == "KC"


def test_canonical_passthrough():
    assert normalize_team_name("NYY") == "NYY"
    assert normalize_team_name("ATH") == "ATH"


def test_unknown_non_strict_returns_key():
    # Non-strict: warns and returns the uppercased token.
    assert normalize_team_name("Nowhere FC") == "NOWHERE FC"


def test_unknown_strict_raises():
    with pytest.raises(ValueError):
        normalize_team_name("Nowhere FC", strict=True)


def test_normalize_matchup():
    home, away = normalize_matchup("Boston Red Sox", "NYY")
    assert home == "BOS"
    assert away == "NYY"
