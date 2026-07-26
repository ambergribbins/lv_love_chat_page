import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import manual_entry as me  # noqa: E402


# --- parse_prob ------------------------------------------------------------
def test_parse_prob_percent():
    assert me.parse_prob("52.5%") == 0.525


def test_parse_prob_bare_percent_number():
    assert me.parse_prob("52.5") == 0.525


def test_parse_prob_decimal():
    assert me.parse_prob("0.525") == 0.525
    assert me.parse_prob(".61") == 0.61


def test_parse_prob_out_of_range():
    with pytest.raises(me.ManualEntryError):
        me.parse_prob("150%")


def test_parse_prob_garbage():
    with pytest.raises(me.ManualEntryError):
        me.parse_prob("abc")


# --- make_row --------------------------------------------------------------
def test_make_row_infers_home_prob():
    row = me.make_row("2026-07-25", "NYY", "BOS", "52.5%")
    assert row["nf_away_win_prob"] == 0.525
    assert row["nf_home_win_prob"] == 0.475
    assert row["nf_pick_team"] == "NYY"  # away favored
    assert row["nf_pick_probability"] == 0.525


def test_make_row_pick_is_home_on_tie_or_home_favored():
    row = me.make_row("2026-07-25", "SF", "LAD", "0.39", "0.61")
    assert row["nf_pick_team"] == "LAD"
    assert row["nf_pick_probability"] == 0.61


def test_make_row_normalizes_aliases():
    row = me.make_row("2026-07-25", "Oakland Athletics", "CWS", "0.55")
    assert row["away_team"] == "ATH"
    assert row["home_team"] == "CHW"


def test_make_row_rejects_same_team():
    with pytest.raises(me.ManualEntryError):
        me.make_row("2026-07-25", "NYY", "NYY", "0.5")


def test_make_row_bad_data_quality():
    with pytest.raises(me.ManualEntryError):
        me.make_row("2026-07-25", "NYY", "BOS", "0.5", data_quality="bogus")


# --- structured parsing ----------------------------------------------------
def test_parse_structured_basic():
    text = """
    # comment line
    NYY, BOS, 52.5%, , from game page
    LAD, SF, 0.610, 0.390
    """
    rows = me.parse_structured(text, "2026-07-25",
                               data_quality="exact_historical_prediction")
    assert len(rows) == 2
    assert rows[0]["home_team"] == "BOS"
    assert rows[0]["notes"] == "from game page"
    assert rows[0]["data_quality"] == "exact_historical_prediction"
    assert rows[1]["nf_pick_team"] == "LAD"


def test_parse_structured_too_few_fields():
    with pytest.raises(me.ManualEntryError):
        me.parse_structured("NYY, BOS", "2026-07-25")


# --- loose parsing ---------------------------------------------------------
def test_parse_loose_team_then_prob_lines():
    text = """
    Yankees
    52.5%
    Red Sox
    47.5%
    """
    rows = me.parse_loose(text, "2026-07-25")
    assert len(rows) == 1
    assert rows[0]["away_team"] == "NYY"
    assert rows[0]["home_team"] == "BOS"
    assert rows[0]["nf_away_win_prob"] == 0.525


def test_parse_loose_inline_team_and_prob():
    text = "Dodgers 61%\nGiants 39%\n"
    rows = me.parse_loose(text, "2026-07-25")
    assert len(rows) == 1
    assert rows[0]["away_team"] == "LAD"
    assert rows[0]["home_team"] == "SF"


def test_parse_loose_home_first_flag():
    text = "Red Sox 47.5%\nYankees 52.5%\n"
    rows = me.parse_loose(text, "2026-07-25", home_first=True)
    assert rows[0]["home_team"] == "BOS"
    assert rows[0]["away_team"] == "NYY"


def test_parse_loose_odd_count_raises():
    text = "Yankees 52.5%\nRed Sox 47.5%\nCubs 55%\n"
    with pytest.raises(me.ManualEntryError):
        me.parse_loose(text, "2026-07-25")


# --- writing ---------------------------------------------------------------
def test_write_and_append(tmp_path, monkeypatch):
    monkeypatch.setattr(me.config, "MANUAL_DIR", tmp_path)
    monkeypatch.setattr(me.config, "ensure_directories", lambda: None)

    rows1 = me.parse_structured("NYY, BOS, 0.525", "2026-07-25")
    path = me.write_manual_csv("2026-07-25", rows1, append=False)
    assert path.exists()

    # Append a new game; duplicate of the first is skipped.
    rows2 = me.parse_structured(
        "NYY, BOS, 0.60\nLAD, SF, 0.61", "2026-07-25"
    )
    me.write_manual_csv("2026-07-25", rows2, append=True)

    import csv as _csv
    with open(path) as fh:
        out = list(_csv.DictReader(fh))
    # NYY@BOS (original, dup skipped) + LAD@SF = 2 rows
    assert len(out) == 2
    keys = {(r["away_team"], r["home_team"]) for r in out}
    assert keys == {("NYY", "BOS"), ("LAD", "SF")}


def test_write_bad_date_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(me.config, "MANUAL_DIR", tmp_path)
    with pytest.raises(me.ManualEntryError):
        me.write_manual_csv("07/25/2026", [], append=False)


# --- round-trip: helper output loads via the scraper's manual loader -------
def test_roundtrip_into_scraper_loader(tmp_path, monkeypatch):
    monkeypatch.setattr(me.config, "MANUAL_DIR", tmp_path)
    monkeypatch.setattr(me.config, "ensure_directories", lambda: None)
    rows = me.parse_structured("NYY, BOS, 0.525\nLAD, SF, 0.61", "2026-07-25",
                               data_quality="exact_historical_prediction")
    me.write_manual_csv("2026-07-25", rows, append=False)

    import numberfire_scraper as nf
    monkeypatch.setattr(nf.config, "MANUAL_DIR", tmp_path)
    loaded = nf.load_manual_numberfire_predictions("2026-07-25")
    assert len(loaded) == 2
    assert set(loaded["nf_pick_team"]) == {"NYY", "LAD"}
    assert set(loaded["data_quality"]) == {"exact_historical_prediction"}
