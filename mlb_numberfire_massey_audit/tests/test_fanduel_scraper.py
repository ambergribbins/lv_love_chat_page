"""Offline tests for the FanDuel Research parser.

Uses a synthetic FanDuel-shaped HTML fixture (real markup TBD via calibration).
Verifies URL building, the visible-text win-prob extraction, and the honest
empty-on-nothing behavior. No network.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fanduel_scraper as fd  # noqa: E402


# Synthetic page: two games, team names followed by numberFire win % and a
# nearby moneyline. Mirrors the shape confirmed via search (e.g. "Cubs 50.7%,
# +102"). Real FanDuel markup will be locked once a saved page is provided.
_FIXTURE = """
<html><head><title>MLB Odds June 24</title></head><body>
<script>var x = 1;</script>
<section>
  <p>Predictions according to numberFire.</p>
  <div class="game">
    New York Mets 49.3% (-118) at Chicago Cubs 50.7% (+102)
  </div>
  <div class="game">
    Los Angeles Dodgers 61.0% (-155) at San Francisco Giants 39.0% (+134)
  </div>
</section>
</body></html>
"""


def test_fanduel_date_url_day_not_padded():
    # June 4 -> day not zero-padded, month padded.
    assert fd.fanduel_date_url("2026-06-04") == (
        "https://www.fanduel.com/research/mlb-betting-odds-06-4-2026"
    )
    assert fd.fanduel_date_url("2026-06-24") == (
        "https://www.fanduel.com/research/mlb-betting-odds-06-24-2026"
    )


def test_parse_fixture_two_games():
    df = fd.parse_fanduel_html(_FIXTURE, "2026-06-24")
    assert len(df) == 2
    g1 = df.iloc[0]
    assert g1["away_team"] == "NYM"
    assert g1["home_team"] == "CHC"
    assert g1["nf_away_win_prob"] == 0.493
    assert g1["nf_home_win_prob"] == 0.507
    assert g1["nf_pick_team"] == "CHC"  # 50.7% > 49.3%
    assert g1["data_quality"] == "article_timestamp"


def test_parse_fixture_second_game_and_moneyline_capture():
    df = fd.parse_fanduel_html(_FIXTURE, "2026-06-24")
    g2 = df.iloc[1]
    assert g2["away_team"] == "LAD"
    assert g2["home_team"] == "SF"
    assert g2["nf_pick_team"] == "LAD"
    # moneylines seen in the window are recorded in parse_notes
    assert "moneylines_seen" in (g2["parse_notes"] or "")


def test_parse_empty_when_no_percentages():
    df = fd.parse_fanduel_html("<html><body>no games here</body></html>", "2026-06-24")
    assert df.empty
    assert list(df.columns) == fd.NF_COLUMNS


def test_characterize_flags_visible_text():
    info = fd._characterize(_FIXTURE)
    assert info["mentions_numberfire"] is True
    assert info["percent_tokens"] >= 4
    assert info["parser_detected"] in {"visible_text_regex", "html_table"}


def test_to_manual_rows_maps_columns():
    df = fd.parse_fanduel_html(_FIXTURE, "2026-06-24")
    rows = fd.to_manual_rows(df)
    from manual_entry import MANUAL_COLUMNS
    assert all(set(MANUAL_COLUMNS).issubset(r.keys()) for r in rows)
    assert rows[0]["data_quality"] == "article_timestamp"
