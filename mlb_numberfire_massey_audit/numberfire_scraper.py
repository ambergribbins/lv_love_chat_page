"""numberFire MLB prediction scraper.

IMPORTANT ETHICS / FAITHFULNESS RULES
-------------------------------------
* Respect robots.txt and site terms; use polite rate limiting (config.SLEEP_SECONDS).
* Do NOT bypass paywalls.
* Do NOT fabricate missing historical predictions. If a page does not expose a
  historical prediction, we log ``unavailable`` and return an empty frame with
  the correct columns.
* Every row carries a ``data_quality`` note describing exactly what it is
  (e.g. observed_current_page vs exact_historical_prediction).
* Raw responses are saved under data/raw/numberfire/YYYY-MM-DD/ when
  SAVE_RAW_RESPONSES is True.

numberFire does not publish a stable, documented historical-prediction API, and
its live pages are JS-rendered. This module therefore *probes* candidate URLs,
saves whatever it finds, and parses defensively. When it cannot recover a real
historical prediction it says so rather than inventing data.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import re
import time
from pathlib import Path

import pandas as pd
import requests

try:
    from bs4 import BeautifulSoup
except Exception:  # pragma: no cover
    BeautifulSoup = None  # type: ignore

import config
from team_map import normalize_team_name

logger = logging.getLogger(__name__)

# --- Output schema ---------------------------------------------------------
NF_COLUMNS = [
    "nf_observed_at_utc",
    "date",
    "source_url",
    "game_url",
    "gamePk",
    "matchup_text",
    "away_team",
    "home_team",
    "nf_away_win_prob",
    "nf_home_win_prob",
    "nf_pick_team",
    "nf_pick_probability",
    "nf_fair_odds_if_available",
    "nf_projected_score_if_available",
    "raw_text_snippet",
    "prediction_timestamp_raw",
    "prediction_timestamp_type",
    "parse_status",
    "parse_notes",
    "data_quality",
]

DATA_QUALITY_VALUES = {
    "exact_historical_prediction",
    "observed_current_page",
    "article_timestamp",
    "wayback_snapshot",
    "manual_import",
    "unavailable",
}

# Candidate numberFire MLB URLs to probe for a given date. numberFire's URL
# scheme has changed over time; we try a few known/plausible shapes.
_CANDIDATE_URL_TEMPLATES = [
    "https://www.numberfire.com/mlb/games",
    "https://www.numberfire.com/mlb/games/{date}",
    "https://www.numberfire.com/mlb/predictions",
    "https://www.numberfire.com/mlb/daily-fantasy/daily-baseball-projections",
]


def _empty_nf() -> pd.DataFrame:
    return pd.DataFrame(columns=NF_COLUMNS)


def _utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _raw_dir(date: str) -> Path:
    d = config.RAW_NUMBERFIRE_DIR / date
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save_raw(date: str, name: str, content: str | bytes) -> Path | None:
    if not config.SAVE_RAW_RESPONSES:
        return None
    d = _raw_dir(date)
    path = d / name
    mode = "wb" if isinstance(content, bytes) else "w"
    with open(path, mode) as fh:
        fh.write(content)
    logger.info("Saved raw numberFire response: %s", path)
    return path


def _http_get(url: str) -> requests.Response | None:
    headers = {
        "User-Agent": config.NUMBERFIRE_USER_AGENT,
        "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
    }
    last_exc: Exception | None = None
    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=30)
            return resp
        except Exception as exc:  # noqa: BLE001 - network robustness
            last_exc = exc
            wait = min(2 ** attempt, 16)
            logger.warning(
                "numberFire GET failed (%s) attempt %d/%d: %s; retry in %ss",
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
    logger.error("numberFire GET permanently failed for %s: %s", url, last_exc)
    return None


# ---------------------------------------------------------------------------
# Probe
# ---------------------------------------------------------------------------
def probe_numberfire_sources(date: str) -> dict:
    """Try likely numberFire MLB pages for a date and report what was found.

    Saves raw responses and returns a diagnostic dict. This does not attempt to
    fully parse predictions; it characterizes the page so we can design/verify
    the parser against real HTML.
    """
    result = {
        "date": date,
        "status_code": None,
        "source_url": None,
        "parser_detected": None,
        "candidate_tables_found": 0,
        "notes": [],
        "attempts": [],
    }

    for template in _CANDIDATE_URL_TEMPLATES:
        url = template.format(date=date)
        resp = _http_get(url)
        attempt = {"url": url, "status_code": None, "bytes": 0}
        if resp is None:
            attempt["error"] = "request_failed"
            result["attempts"].append(attempt)
            continue

        attempt["status_code"] = resp.status_code
        attempt["bytes"] = len(resp.content)
        safe_name = re.sub(r"[^A-Za-z0-9]+", "_", url)[-80:] + ".html"
        _save_raw(date, f"probe_{safe_name}", resp.content)
        result["attempts"].append(attempt)

        if resp.status_code != 200:
            result["notes"].append(f"{url} -> HTTP {resp.status_code}")
            continue

        # First 200 OK becomes the primary reported source.
        if result["source_url"] is None:
            result["source_url"] = url
            result["status_code"] = resp.status_code

        text = resp.text
        parser, tables = _characterize_html(text)
        result["parser_detected"] = result["parser_detected"] or parser
        result["candidate_tables_found"] += tables
        result["notes"].append(
            f"{url} -> HTTP 200, parser~{parser}, tables~{tables}"
        )

    if result["source_url"] is None:
        result["notes"].append(
            "No candidate numberFire URL returned HTTP 200. numberFire may block "
            "scraping, require JS rendering, or have changed its URL scheme. "
            "Use the manual CSV fallback."
        )
    return result


def _characterize_html(html: str) -> tuple[str, int]:
    """Return (parser_hint, approximate_table_count) for a page."""
    if not html:
        return ("empty", 0)
    parser = "unknown"
    if "__NEXT_DATA__" in html:
        parser = "nextjs_next_data"
    elif "window.__INITIAL_STATE__" in html or "__APOLLO_STATE__" in html:
        parser = "embedded_json_state"
    elif "application/ld+json" in html:
        parser = "ld_json"
    elif "<table" in html:
        parser = "html_table"

    tables = html.count("<table")
    return (parser, tables)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------
_PCT_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")


def _pct_to_prob(text: str) -> float | None:
    """Convert '56.2%' -> 0.562. Returns None if no percentage found."""
    if text is None:
        return None
    m = _PCT_RE.search(str(text))
    if not m:
        return None
    val = float(m.group(1))
    if val > 1.0:
        val = val / 100.0
    if 0.0 <= val <= 1.0:
        return round(val, 4)
    return None


def _extract_next_data(html: str) -> dict | None:
    """Pull the Next.js __NEXT_DATA__ JSON blob, if present."""
    if not html or "__NEXT_DATA__" not in html:
        return None
    m = re.search(
        r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        html,
        re.DOTALL,
    )
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        logger.warning("Found __NEXT_DATA__ but could not parse JSON")
        return None


def _blank_row(date: str, source_url: str) -> dict:
    """A fully-populated row template with None values."""
    row = {c: None for c in NF_COLUMNS}
    row["nf_observed_at_utc"] = _utc_now_iso()
    row["date"] = date
    row["source_url"] = source_url
    row["parse_status"] = "unparsed"
    return row


def _parse_html_tables_for_winprob(
    html: str, date: str, source_url: str
) -> list[dict]:
    """Best-effort parse of visible HTML tables for game win probabilities.

    numberFire's live game pages historically show two team rows with a win%
    column. This is defensive: if the structure isn't recognized we return [].
    """
    if BeautifulSoup is None or not html:
        return []
    rows: list[dict] = []
    soup = BeautifulSoup(html, "lxml")

    for table in soup.find_all("table"):
        header_text = " ".join(
            th.get_text(" ", strip=True).lower() for th in table.find_all("th")
        )
        if "win" not in header_text and "%" not in header_text:
            continue

        team_probs: list[tuple[str, float]] = []
        for tr in table.find_all("tr"):
            cells = tr.find_all(["td", "th"])
            if len(cells) < 2:
                continue
            label = cells[0].get_text(" ", strip=True)
            prob = None
            for c in cells[1:]:
                prob = _pct_to_prob(c.get_text(" ", strip=True))
                if prob is not None:
                    break
            if prob is None or not label:
                continue
            try:
                team = normalize_team_name(label, strict=False)
            except Exception:  # noqa: BLE001
                continue
            if team:
                team_probs.append((team, prob))

        # Interpret pairs of teams as a matchup (away listed first on numberFire).
        for i in range(0, len(team_probs) - 1, 2):
            away, ap = team_probs[i]
            home, hp = team_probs[i + 1]
            row = _blank_row(date, source_url)
            # If only one side is clearly shown, infer the other as 1 - p.
            if ap is not None and hp is None:
                hp = round(1.0 - ap, 4)
            elif hp is not None and ap is None:
                ap = round(1.0 - hp, 4)
            row.update(
                {
                    "away_team": away,
                    "home_team": home,
                    "nf_away_win_prob": ap,
                    "nf_home_win_prob": hp,
                    "matchup_text": f"{away} @ {home}",
                    "raw_text_snippet": f"{away} {ap} / {home} {hp}",
                    "parse_status": "parsed_html_table",
                    "data_quality": "observed_current_page",
                }
            )
            _finalize_pick(row)
            rows.append(row)
    return rows


def _finalize_pick(row: dict) -> None:
    """Populate nf_pick_team / nf_pick_probability from the two win probs."""
    ap = row.get("nf_away_win_prob")
    hp = row.get("nf_home_win_prob")
    if ap is None and hp is None:
        return
    if ap is None:
        ap = round(1.0 - hp, 4)
        row["nf_away_win_prob"] = ap
    if hp is None:
        hp = round(1.0 - ap, 4)
        row["nf_home_win_prob"] = hp
    if hp >= ap:
        row["nf_pick_team"] = row.get("home_team")
        row["nf_pick_probability"] = hp
    else:
        row["nf_pick_team"] = row.get("away_team")
        row["nf_pick_probability"] = ap


# ---------------------------------------------------------------------------
# Primary scrape
# ---------------------------------------------------------------------------
def scrape_numberfire_mlb_predictions(date: str) -> pd.DataFrame:
    """Scrape one day of numberFire MLB game win probabilities.

    Returns a DataFrame with :data:`NF_COLUMNS`. If nothing can be recovered,
    returns an empty (but correctly-shaped) DataFrame and logs the failure --
    it never fabricates predictions.

    NOTE: numberFire live pages are JS-rendered and its historical predictions
    are not cleanly exposed. This function will typically only succeed for the
    *current* day (data_quality='observed_current_page'). For past dates use
    the manual CSV fallback or Wayback backfill.
    """
    all_rows: list[dict] = []
    primary_url = None

    for template in _CANDIDATE_URL_TEMPLATES:
        url = template.format(date=date)
        resp = _http_get(url)
        if resp is None or resp.status_code != 200:
            continue
        primary_url = primary_url or url
        safe_name = re.sub(r"[^A-Za-z0-9]+", "_", url)[-80:] + ".html"
        _save_raw(date, f"scrape_{safe_name}", resp.content)
        html = resp.text

        # Try structured JSON first, then visible tables.
        next_data = _extract_next_data(html)
        if next_data is not None:
            _save_raw(date, "scrape_next_data.json", json.dumps(next_data)[:500000])
            # numberFire's JSON shape is not documented/stable; we do not guess
            # at field names to avoid fabricating. Fall back to table parsing.
            logger.info(
                "numberFire __NEXT_DATA__ found for %s but schema is unverified; "
                "using table parser",
                date,
            )

        rows = _parse_html_tables_for_winprob(html, date, url)
        if rows:
            all_rows.extend(rows)
            break  # first productive page wins

    if not all_rows:
        logger.warning(
            "No numberFire MLB predictions parsed for %s. This is expected for "
            "historical dates (JS-rendered / not exposed). Returning empty frame; "
            "use manual CSV or Wayback backfill. NOT fabricating data.",
            date,
        )
        return _empty_nf()

    df = pd.DataFrame(all_rows)
    for c in NF_COLUMNS:
        if c not in df.columns:
            df[c] = None
    df["date"] = date
    df["source_url"] = df["source_url"].fillna(primary_url)
    logger.info("Parsed %d numberFire rows for %s", len(df), date)
    return df[NF_COLUMNS]


# ---------------------------------------------------------------------------
# Manual fallback
# ---------------------------------------------------------------------------
def manual_predictions_path(date: str) -> Path:
    return config.MANUAL_DIR / f"numberfire_predictions_{date}.csv"


def load_manual_numberfire_predictions(date: str) -> pd.DataFrame:
    """Load a manual numberFire prediction CSV for a date, if present.

    Manual file columns (see data/manual/numberfire_predictions_EXAMPLE.csv):
        date, source_url, away_team, home_team, nf_away_win_prob,
        nf_home_win_prob, nf_pick_team, nf_pick_probability, data_quality, notes
    """
    path = manual_predictions_path(date)
    if not path.exists():
        return _empty_nf()
    raw = pd.read_csv(path)
    rows: list[dict] = []
    for r in raw.to_dict(orient="records"):
        row = _blank_row(date, r.get("source_url") or "manual")
        away = normalize_team_name(r.get("away_team"))
        home = normalize_team_name(r.get("home_team"))
        row.update(
            {
                "away_team": away,
                "home_team": home,
                "matchup_text": f"{away} @ {home}",
                "nf_away_win_prob": r.get("nf_away_win_prob"),
                "nf_home_win_prob": r.get("nf_home_win_prob"),
                "nf_pick_team": normalize_team_name(r["nf_pick_team"])
                if r.get("nf_pick_team")
                else None,
                "nf_pick_probability": r.get("nf_pick_probability"),
                "parse_status": "manual_import",
                "parse_notes": r.get("notes"),
                "data_quality": r.get("data_quality") or "manual_import",
            }
        )
        if row["nf_pick_team"] is None:
            _finalize_pick(row)
        rows.append(row)
    if not rows:
        return _empty_nf()
    df = pd.DataFrame(rows)
    for c in NF_COLUMNS:
        if c not in df.columns:
            df[c] = None
    logger.info("Loaded %d manual numberFire rows for %s", len(df), date)
    return df[NF_COLUMNS]


def get_numberfire_predictions(date: str) -> pd.DataFrame:
    """Preferred entry point: manual CSV first, then live scrape.

    Manual data is trusted over a live scrape because manual rows are
    explicitly labeled with their true data_quality (and, for past dates, a
    live scrape would be current-page data, not the historical prediction).
    """
    manual = load_manual_numberfire_predictions(date)
    if not manual.empty:
        return manual
    return scrape_numberfire_mlb_predictions(date)


# ---------------------------------------------------------------------------
# Wayback / CDX backfill (optional)
# ---------------------------------------------------------------------------
def _wayback_snapshot_url(original_url: str, date: str) -> str | None:
    """Query the Wayback CDX API for a snapshot near ``date``."""
    ts = date.replace("-", "")
    cdx = (
        "https://web.archive.org/cdx/search/cdx"
        f"?url={original_url}&output=json&limit=1&filter=statuscode:200"
        f"&from={ts}&to={ts}"
    )
    resp = _http_get(cdx)
    if resp is None or resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        return None
    if len(data) < 2:
        return None
    # rows: [ [urlkey, timestamp, original, mimetype, statuscode, digest, length] ]
    _, snap_ts, original, *_ = data[1]
    return f"https://web.archive.org/web/{snap_ts}/{original}"


def _wayback_backfill_date(date: str) -> pd.DataFrame:
    """Attempt to recover a historical numberFire page via Wayback."""
    for template in _CANDIDATE_URL_TEMPLATES:
        original = template.format(date=date)
        snap = _wayback_snapshot_url(original, date)
        if not snap:
            continue
        resp = _http_get(snap)
        if resp is None or resp.status_code != 200:
            continue
        _save_raw(date, "wayback_snapshot.html", resp.content)
        rows = _parse_html_tables_for_winprob(resp.text, date, snap)
        if rows:
            for row in rows:
                row["data_quality"] = "wayback_snapshot"
                row["prediction_timestamp_type"] = "wayback_snapshot"
                row["parse_notes"] = "recovered via Wayback CDX"
            df = pd.DataFrame(rows)
            for c in NF_COLUMNS:
                if c not in df.columns:
                    df[c] = None
            return df[NF_COLUMNS]
    return _empty_nf()


# ---------------------------------------------------------------------------
# Backfill driver
# ---------------------------------------------------------------------------
def _daterange(start_date: str, end_date: str):
    start = _dt.date.fromisoformat(start_date)
    end = _dt.date.fromisoformat(end_date)
    cur = start
    while cur <= end:
        yield cur.isoformat()
        cur += _dt.timedelta(days=1)


def backfill_numberfire_predictions(start_date: str, end_date: str) -> pd.DataFrame:
    """Backfill numberFire predictions across a date range.

    For each date:
      1. Prefer a manual CSV (labeled with its true data_quality).
      2. Try date-specific / index numberFire URLs (current-page data only).
      3. Optionally attempt Wayback recovery if ENABLE_WAYBACK_BACKFILL.
      4. Save raw responses; parse with the shared parser.
      5. Record failures to data/results/numberfire_backfill_failures.csv.

    Never fabricates: a date with no recoverable prediction produces no rows
    (and one failure record with data_quality='unavailable').
    """
    config.ensure_directories()
    frames: list[pd.DataFrame] = []
    failures: list[dict] = []

    for date in _daterange(start_date, end_date):
        logger.info("Backfilling numberFire for %s", date)

        manual = load_manual_numberfire_predictions(date)
        if not manual.empty:
            frames.append(manual)
            continue

        scraped = scrape_numberfire_mlb_predictions(date)
        if not scraped.empty:
            # Live scrape of a past date is current-page data, not the historical
            # prediction. Mark it honestly.
            scraped = scraped.copy()
            scraped["data_quality"] = scraped["data_quality"].fillna(
                "observed_current_page"
            )
            frames.append(scraped)
            continue

        if config.ENABLE_WAYBACK_BACKFILL:
            wb = _wayback_backfill_date(date)
            if not wb.empty:
                frames.append(wb)
                continue

        failures.append(
            {
                "date": date,
                "data_quality": "unavailable",
                "notes": (
                    "No manual CSV, live scrape empty, "
                    + (
                        "Wayback empty"
                        if config.ENABLE_WAYBACK_BACKFILL
                        else "Wayback disabled"
                    )
                ),
            }
        )
        logger.warning("numberFire unavailable for %s (logged, not fabricated)", date)

    if failures:
        fail_path = config.RESULTS_DIR / "numberfire_backfill_failures.csv"
        pd.DataFrame(failures).to_csv(fail_path, index=False)
        logger.warning("Wrote %d backfill failures to %s", len(failures), fail_path)

    if not frames:
        return _empty_nf()
    out = pd.concat(frames, ignore_index=True)
    return out[NF_COLUMNS]
