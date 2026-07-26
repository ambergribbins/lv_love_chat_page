"""FanDuel Research scraper for numberFire MLB win probabilities.

numberFire was absorbed into FanDuel; its MLB win-probability predictions now
publish on FanDuel Research at a regular dated URL:

    https://www.fanduel.com/research/mlb-betting-odds-MM-D-YYYY
    (month zero-padded, day NOT padded -> e.g. .../mlb-betting-odds-06-4-2026)

These are date-stamped article pages, so recovered rows are tagged
``data_quality='article_timestamp'`` -- an honest "the numberFire prediction as
published that day", not a live re-scrape of a current page.

Two capture paths (FanDuel has bot protection and may return HTTP 403 to raw
scripts):
  1. Live fetch  -> scrape_fanduel_mlb(date)          (works if your network allows)
  2. Saved file  -> import_fanduel_saved_file(path,..) (browser Save-As; 403-proof)
scrape_fanduel_mlb() automatically falls back to instructing the saved-file path
on a 403.

PARSER CALIBRATION
------------------
The win-probability extraction (`parse_fanduel_html`) is multi-strategy and
best-effort. It is meant to be *calibrated against one real saved page* -- run
``probe_fanduel_*`` , inspect ``parser_detected`` and the snippet, and tighten
``_extract_games_from_*`` accordingly. It never fabricates: if it cannot find
win probabilities it returns an empty frame and logs why.
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
from numberfire_scraper import (
    NF_COLUMNS,
    _blank_row,
    _empty_nf,
    _finalize_pick,
    _pct_to_prob,
)
from team_map import CANONICAL_TEAMS, normalize_team_name

logger = logging.getLogger(__name__)

# Browser-like headers reduce (do not guarantee) 403s from bot protection.
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_MONEYLINE_RE = re.compile(r"([+-]\d{3,4})")


def fanduel_date_url(date: str) -> str:
    """Build the FanDuel Research MLB odds URL for a date (day not zero-padded)."""
    d = _dt.date.fromisoformat(date)
    return (
        f"https://www.fanduel.com/research/"
        f"mlb-betting-odds-{d.month:02d}-{d.day}-{d.year}"
    )


def _raw_dir(date: str) -> Path:
    d = config.RAW_NUMBERFIRE_DIR / date
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save_raw(date: str, name: str, content) -> Path | None:
    if not config.SAVE_RAW_RESPONSES:
        return None
    path = _raw_dir(date) / name
    mode = "wb" if isinstance(content, bytes) else "w"
    with open(path, mode) as fh:
        fh.write(content)
    logger.info("Saved raw FanDuel response: %s", path)
    return path


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------
class FanDuelBlockedError(RuntimeError):
    """Raised when FanDuel returns 403 (bot protection) -- use the saved file."""


def _http_get(url: str) -> requests.Response | None:
    last_exc: Exception | None = None
    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=_BROWSER_HEADERS, timeout=30)
            if resp.status_code == 403:
                raise FanDuelBlockedError(
                    f"HTTP 403 from {url} (FanDuel bot protection). "
                    "Save the page in your browser and use "
                    "import_fanduel_saved_file()."
                )
            return resp
        except FanDuelBlockedError:
            raise
        except Exception as exc:  # noqa: BLE001 - network robustness
            last_exc = exc
            wait = min(2 ** attempt, 16)
            logger.warning(
                "FanDuel GET failed (%s) attempt %d/%d: %s; retry in %ss",
                url, attempt, config.MAX_RETRIES, exc, wait,
            )
            time.sleep(wait)
        finally:
            if config.SLEEP_SECONDS:
                time.sleep(config.SLEEP_SECONDS)
    logger.error("FanDuel GET permanently failed for %s: %s", url, last_exc)
    return None


# ---------------------------------------------------------------------------
# Structure characterization (for calibration)
# ---------------------------------------------------------------------------
def _characterize(html: str) -> dict:
    info = {
        "has_next_data": "__NEXT_DATA__" in (html or ""),
        "has_ld_json": "application/ld+json" in (html or ""),
        "table_count": (html or "").count("<table"),
        "percent_tokens": len(re.findall(r"\d{1,3}(?:\.\d+)?\s*%", html or "")),
        "moneyline_tokens": len(_MONEYLINE_RE.findall(html or "")),
        "mentions_numberfire": "numberfire" in (html or "").lower(),
        "length": len(html or ""),
    }
    if info["has_next_data"]:
        info["parser_detected"] = "nextjs_next_data"
    elif info["table_count"]:
        info["parser_detected"] = "html_table"
    elif info["percent_tokens"]:
        info["parser_detected"] = "visible_text_regex"
    else:
        info["parser_detected"] = "unknown"
    return info


def probe_fanduel_source(date: str) -> dict:
    """Live-probe the FanDuel page for a date; save raw HTML; report structure."""
    url = fanduel_date_url(date)
    result = {"date": date, "source_url": url, "status_code": None, "notes": []}
    try:
        resp = _http_get(url)
    except FanDuelBlockedError as exc:
        result["status_code"] = 403
        result["notes"].append(str(exc))
        return result
    if resp is None:
        result["notes"].append("request failed (network)")
        return result
    result["status_code"] = resp.status_code
    _save_raw(date, "fanduel_probe.html", resp.content)
    if resp.status_code == 200:
        result.update(_characterize(resp.text))
        result["snippet"] = _snippet(resp.text)
    else:
        result["notes"].append(f"HTTP {resp.status_code}")
    return result


def probe_fanduel_file(path: str, date: str | None = None) -> dict:
    """Probe a browser-saved HTML file; report structure for calibration."""
    html = Path(path).read_text(errors="ignore")
    info = {"file": str(path), "date": date}
    info.update(_characterize(html))
    info["snippet"] = _snippet(html)
    return info


def _snippet(html: str, n: int = 600) -> str:
    """A visible-text snippet around the first percentage, to aid calibration."""
    text = _visible_text(html)
    m = re.search(r"\d{1,3}(?:\.\d+)?\s*%", text)
    if not m:
        return text[:n]
    start = max(0, m.start() - n // 2)
    return text[start:start + n]


# ---------------------------------------------------------------------------
# Parsing (CALIBRATION-PENDING: tighten against a real saved page)
# ---------------------------------------------------------------------------
def _visible_text(html: str) -> str:
    if BeautifulSoup is not None and html:
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        return re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    # crude fallback
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html or ""))


def _extract_next_data(html: str) -> dict | None:
    if not html or "__NEXT_DATA__" not in html:
        return None
    m = re.search(
        r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL
    )
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        logger.warning("FanDuel __NEXT_DATA__ present but unparseable")
        return None


def _extract_games_from_text(text: str, date: str, source_url: str) -> list[dict]:
    """First-pass: find ``<Team> <pct>%`` adjacencies and pair into games.

    CALIBRATION-PENDING. This works when the page renders team names immediately
    followed by their win %. Real FanDuel markup should be confirmed via probe;
    tighten the windowing here once a sample is available.
    """
    # Find every "<words> <pct>%" occurrence; map the words to a team code.
    pairs: list[tuple[str, float, int]] = []  # (team, prob, char_pos)
    for m in re.finditer(r"([A-Za-z.'\- ]{3,40}?)\s+(\d{1,3}(?:\.\d+)?)\s*%", text):
        label = m.group(1).strip()
        # Use the last 1-4 words as the team candidate.
        words = label.split()
        team = None
        for take in (4, 3, 2, 1):
            if len(words) >= take:
                cand = " ".join(words[-take:])
                code = normalize_team_name(cand)
                if code in CANONICAL_TEAMS:
                    team = code
                    break
        if team is None:
            continue
        prob = _pct_to_prob(m.group(2) + "%")
        if prob is None:
            continue
        pairs.append((team, prob, m.start()))

    rows: list[dict] = []
    for i in range(0, len(pairs) - 1, 2):
        (away, ap, pos_a) = pairs[i]
        (home, hp, pos_h) = pairs[i + 1]
        if away == home:
            continue
        row = _blank_row(date, source_url)
        # Nearby moneylines (best-effort) for the snippet.
        window = text[pos_a: pos_h + 60]
        mls = _MONEYLINE_RE.findall(window)
        row.update(
            {
                "away_team": away,
                "home_team": home,
                "nf_away_win_prob": ap,
                "nf_home_win_prob": hp,
                "matchup_text": f"{away} @ {home}",
                "raw_text_snippet": window[:200],
                "prediction_timestamp_raw": date,
                "prediction_timestamp_type": "article_date",
                "parse_status": "parsed_fanduel_text",
                "parse_notes": (f"moneylines_seen={mls}" if mls else None),
                "data_quality": "article_timestamp",
            }
        )
        _finalize_pick(row)
        rows.append(row)
    return rows


def parse_fanduel_html(html: str, date: str, source_url: str = "") -> pd.DataFrame:
    """Parse a FanDuel Research page into numberFire prediction rows.

    Strategy order: __NEXT_DATA__ (best-effort, calibration-pending) then
    visible-text adjacency. Returns NF_COLUMNS; empty (never fabricated) if
    nothing parses.
    """
    source_url = source_url or fanduel_date_url(date)
    rows: list[dict] = []

    next_data = _extract_next_data(html)
    if next_data is not None:
        # numberFire/FanDuel JSON schema is not documented here; we deliberately
        # do NOT guess field names (that risks fabricating). Left as a calibration
        # hook: once a real payload is seen, map win-prob fields explicitly.
        logger.info(
            "FanDuel __NEXT_DATA__ found for %s; JSON mapping is calibration-"
            "pending, using visible-text parser meanwhile.",
            date,
        )

    text = _visible_text(html)
    rows = _extract_games_from_text(text, date, source_url)

    if not rows:
        logger.warning(
            "No numberFire win probabilities parsed from FanDuel page for %s. "
            "Run probe_fanduel_* and calibrate the parser; NOT fabricating.",
            date,
        )
        return _empty_nf()

    df = pd.DataFrame(rows)
    for c in NF_COLUMNS:
        if c not in df.columns:
            df[c] = None
    df["date"] = date
    logger.info("Parsed %d numberFire rows from FanDuel page for %s", len(df), date)
    return df[NF_COLUMNS]


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------
def import_fanduel_saved_file(path: str, date: str) -> pd.DataFrame:
    """Parse a browser-saved FanDuel HTML file into numberFire rows (403-proof)."""
    html = Path(path).read_text(errors="ignore")
    _save_raw(date, "fanduel_saved_import.html", html)
    return parse_fanduel_html(html, date, source_url=fanduel_date_url(date))


def scrape_fanduel_mlb(date: str) -> pd.DataFrame:
    """Live-fetch + parse the FanDuel page for a date.

    Raises FanDuelBlockedError on 403 so the caller can prompt for the saved
    file. Returns an empty NF frame (never fabricated) if the page yields no
    parseable predictions.
    """
    url = fanduel_date_url(date)
    resp = _http_get(url)  # may raise FanDuelBlockedError
    if resp is None or resp.status_code != 200:
        code = getattr(resp, "status_code", "no-response")
        logger.warning("FanDuel fetch for %s returned %s", date, code)
        return _empty_nf()
    _save_raw(date, "fanduel_scrape.html", resp.content)
    return parse_fanduel_html(resp.text, date, source_url=url)


def to_manual_rows(df: pd.DataFrame) -> list[dict]:
    """Map parsed NF-schema rows to the manual-CSV row format."""
    from manual_entry import MANUAL_COLUMNS

    rows: list[dict] = []
    for r in df.to_dict(orient="records"):
        rows.append({c: r.get(c, "") for c in MANUAL_COLUMNS})
    return rows


def save_to_manual(df: pd.DataFrame, date: str, append: bool = False):
    """Persist recovered FanDuel predictions to the manual CSV for the pipeline."""
    from manual_entry import write_manual_csv

    if df.empty:
        logger.warning("Nothing to save for %s (empty parse)", date)
        return None
    return write_manual_csv(date, to_manual_rows(df), append=append)


def get_fanduel_predictions(date: str, saved_file: str | None = None) -> pd.DataFrame:
    """Convenience: saved file if given, else live fetch with 403 guidance."""
    if saved_file:
        return import_fanduel_saved_file(saved_file, date)
    try:
        return scrape_fanduel_mlb(date)
    except FanDuelBlockedError as exc:
        logger.error("%s", exc)
        return _empty_nf()
