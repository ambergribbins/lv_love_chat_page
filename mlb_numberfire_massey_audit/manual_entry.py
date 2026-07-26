"""Manual-entry helper: turn a pasted numberFire MLB slate into the correct
``data/manual/numberfire_predictions_YYYY-MM-DD.csv``.

Why this exists
---------------
numberFire does not expose clean historical predictions, so real backtests of
past dates depend on manually captured slates. This helper makes that capture
fast and *honest*: every batch is stamped with a ``data_quality`` you choose, so
a row is never silently mistaken for an exact historical prediction when it is
really a current-page observation.

Three input modes
-----------------
1. Structured (recommended, robust). One game per line::

       AWAY, HOME, AWAY_PROB[, HOME_PROB][, NOTES]

   Probabilities accept ``52.5%``, ``52.5``, or ``0.525``. Leave HOME_PROB blank
   to auto-fill ``1 - AWAY_PROB`` (only valid for a two-way win probability)::

       NYY, BOS, 52.5%, , copied from numberFire game page
       LAD, SF,  0.610, 0.390

2. Loose paste. Free text copied from a numberFire page; the parser pulls
   team+percent pairs in order and groups them into games (away team first).
   Always preview before trusting it.

3. Interactive. Prompted game-by-game entry.

Output columns (match numberfire_scraper.load_manual_numberfire_predictions):
    date, source_url, away_team, home_team, nf_away_win_prob, nf_home_win_prob,
    nf_pick_team, nf_pick_probability, data_quality, notes
"""

from __future__ import annotations

import csv
import logging
import re
from pathlib import Path

import config
from team_map import CANONICAL_TEAMS, normalize_team_name

logger = logging.getLogger(__name__)

MANUAL_COLUMNS = [
    "date",
    "source_url",
    "away_team",
    "home_team",
    "nf_away_win_prob",
    "nf_home_win_prob",
    "nf_pick_team",
    "nf_pick_probability",
    "data_quality",
    "notes",
]

# Honest data_quality choices, most-truthful first.
DATA_QUALITY_CHOICES = [
    ("exact_historical_prediction",
     "You recorded this from numberFire at/near game time (true history)."),
    ("wayback_snapshot",
     "Recovered from a Wayback Machine snapshot dated on/near the game date."),
    ("article_timestamp",
     "From a timestamped numberFire article/preview for that date."),
    ("observed_current_page",
     "Copied numberFire's CURRENT page for a past date -- NOT the true "
     "historical prediction. Use with caution in backtests."),
    ("manual_import",
     "Generic manual import (source/timing unspecified)."),
]
VALID_DATA_QUALITY = {c for c, _ in DATA_QUALITY_CHOICES}

_PCT_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")
_NUM_RE = re.compile(r"^\s*(\d{1,3}(?:\.\d+)?|0?\.\d+)\s*%?\s*$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ManualEntryError(ValueError):
    """Raised for unrecoverable parse/validation problems."""


# ---------------------------------------------------------------------------
# Probability parsing
# ---------------------------------------------------------------------------
def parse_prob(text) -> float:
    """Parse a probability from ``'52.5%'``, ``'52.5'``, ``'0.525'`` -> 0.525.

    Values in (1, 100] are treated as percentages. Raises ManualEntryError on
    anything outside [0, 1] after normalization.
    """
    if text is None:
        raise ManualEntryError("empty probability")
    s = str(text).strip()
    if not s:
        raise ManualEntryError("empty probability")
    had_pct = s.endswith("%")
    m = _PCT_RE.search(s) if had_pct else _NUM_RE.match(s)
    if not m:
        raise ManualEntryError(f"could not parse probability from {text!r}")
    val = float(m.group(1))
    if had_pct or val > 1.0:
        val = val / 100.0
    val = round(val, 4)
    if not (0.0 <= val <= 1.0):
        raise ManualEntryError(f"probability {val} out of range [0,1] from {text!r}")
    return val


def _looks_like_prob(token: str) -> bool:
    return bool(_NUM_RE.match(str(token).strip()))


# ---------------------------------------------------------------------------
# Row assembly
# ---------------------------------------------------------------------------
def _compute_pick(away_team, home_team, away_prob, home_prob):
    """Return (pick_team, pick_probability) -- home wins ties (has field edge)."""
    if home_prob >= away_prob:
        return home_team, home_prob
    return away_team, away_prob


def make_row(
    date: str,
    away_team: str,
    home_team: str,
    away_prob,
    home_prob=None,
    data_quality: str = "manual_import",
    source_url: str = "manual",
    notes: str = "",
    prob_sum_tolerance: float = 0.02,
    strict_teams: bool = False,
) -> dict:
    """Build one validated, normalized manual row.

    If ``home_prob`` is None/blank it is inferred as ``1 - away_prob`` (valid
    only for a genuine two-way win probability). If both are given and they do
    not sum to ~1.0, a warning is logged (numberFire's two-way probs should
    sum to 1; a large gap usually means a copy/paste error).
    """
    away = normalize_team_name(away_team, strict=strict_teams)
    home = normalize_team_name(home_team, strict=strict_teams)
    if not away or not home:
        raise ManualEntryError(f"missing team(s): away={away_team!r} home={home_team!r}")
    if away == home:
        raise ManualEntryError(f"away and home are the same team: {away}")
    if away not in CANONICAL_TEAMS:
        logger.warning("away team %r did not normalize to a canonical code", away_team)
    if home not in CANONICAL_TEAMS:
        logger.warning("home team %r did not normalize to a canonical code", home_team)

    ap = parse_prob(away_prob)
    if home_prob is None or str(home_prob).strip() == "":
        hp = round(1.0 - ap, 4)
        inferred = True
    else:
        hp = parse_prob(home_prob)
        inferred = False

    total = round(ap + hp, 4)
    if not inferred and abs(total - 1.0) > prob_sum_tolerance:
        logger.warning(
            "%s @ %s: probs %.3f + %.3f = %.3f (expected ~1.0); "
            "check the paste",
            away, home, ap, hp, total,
        )
        notes = (notes + " " if notes else "") + f"[prob_sum={total}]"

    if data_quality not in VALID_DATA_QUALITY:
        raise ManualEntryError(
            f"invalid data_quality {data_quality!r}; choose one of "
            f"{sorted(VALID_DATA_QUALITY)}"
        )

    pick_team, pick_prob = _compute_pick(away, home, ap, hp)
    return {
        "date": date,
        "source_url": source_url or "manual",
        "away_team": away,
        "home_team": home,
        "nf_away_win_prob": ap,
        "nf_home_win_prob": hp,
        "nf_pick_team": pick_team,
        "nf_pick_probability": pick_prob,
        "data_quality": data_quality,
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# Structured parsing
# ---------------------------------------------------------------------------
def parse_structured(
    text: str,
    date: str,
    data_quality: str = "manual_import",
    source_url: str = "manual",
) -> list[dict]:
    """Parse structured lines: ``AWAY, HOME, AWAY_PROB[, HOME_PROB][, NOTES]``.

    Blank lines and ``#`` comments are ignored. Raises ManualEntryError with the
    offending line number on malformed input.
    """
    rows: list[dict] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            raise ManualEntryError(
                f"line {lineno}: need at least AWAY, HOME, AWAY_PROB -> {raw!r}"
            )
        away, home, away_prob = parts[0], parts[1], parts[2]
        home_prob = parts[3] if len(parts) >= 4 else None
        notes = parts[4] if len(parts) >= 5 else ""
        try:
            rows.append(
                make_row(
                    date, away, home, away_prob, home_prob,
                    data_quality=data_quality, source_url=source_url, notes=notes,
                )
            )
        except ManualEntryError as exc:
            raise ManualEntryError(f"line {lineno}: {exc}") from exc
    return rows


# ---------------------------------------------------------------------------
# Loose paste parsing
# ---------------------------------------------------------------------------
def _normalize_team_line(line: str) -> str | None:
    """If a line (minus any percentage) is a recognizable team, return its code."""
    cleaned = _PCT_RE.sub("", line)
    cleaned = re.sub(r"[^A-Za-z0-9 .'\-]", " ", cleaned).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        return None
    code = normalize_team_name(cleaned)
    return code if code in CANONICAL_TEAMS else None


def parse_loose(
    text: str,
    date: str,
    data_quality: str = "manual_import",
    source_url: str = "manual",
    home_first: bool = False,
) -> list[dict]:
    """Best-effort parse of free-form pasted text into games.

    Walks the paste extracting an ordered stream of (team, probability) pairs,
    then groups every two consecutive teams into a game. numberFire lists the
    **away** team first by default; pass ``home_first=True`` to flip.

    This mode is heuristic -- always preview the result. Raises ManualEntryError
    if the number of recovered team/prob items is odd or inconsistent.
    """
    events: list[tuple[str, str | None]] = []  # (team_code, prob_or_None)
    pending_team: str | None = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        team = _normalize_team_line(line)
        pct_match = _PCT_RE.search(line)
        prob = pct_match.group(0) if pct_match else None

        if team and prob:
            events.append((team, prob))
            continue
        if team and not prob:
            if pending_team is not None:
                # Two teams in a row with no prob between -> can't align safely.
                raise ManualEntryError(
                    f"loose parse: two teams without a probability between "
                    f"({pending_team} then {team}); use structured mode"
                )
            pending_team = team
            continue
        if prob and not team:
            if pending_team is None:
                # A stray probability with no owning team; skip with a warning.
                logger.warning("loose parse: probability %r with no team; skipped", prob)
                continue
            events.append((pending_team, prob))
            pending_team = None

    if pending_team is not None:
        raise ManualEntryError(
            f"loose parse: team {pending_team} had no probability"
        )
    if len(events) < 2:
        raise ManualEntryError("loose parse: fewer than two team/prob pairs found")
    if len(events) % 2 != 0:
        raise ManualEntryError(
            f"loose parse: recovered {len(events)} team/prob items (odd count); "
            "a game is missing a side. Use structured mode."
        )

    rows: list[dict] = []
    for i in range(0, len(events), 2):
        (t1, p1), (t2, p2) = events[i], events[i + 1]
        if home_first:
            home, hp, away, ap = t1, p1, t2, p2
        else:
            away, ap, home, hp = t1, p1, t2, p2
        rows.append(
            make_row(
                date, away, home, ap, hp,
                data_quality=data_quality, source_url=source_url,
                notes="loose_paste",
            )
        )
    return rows


# ---------------------------------------------------------------------------
# Interactive entry
# ---------------------------------------------------------------------------
def choose_data_quality(input_fn=input, print_fn=print) -> str:
    print_fn("\nHow was this slate captured? (honest tagging matters)")
    for i, (code, desc) in enumerate(DATA_QUALITY_CHOICES, start=1):
        print_fn(f"  {i}. {code}\n       {desc}")
    while True:
        raw = input_fn("Choose 1-5 [default 5=manual_import]: ").strip()
        if not raw:
            return "manual_import"
        if raw.isdigit() and 1 <= int(raw) <= len(DATA_QUALITY_CHOICES):
            return DATA_QUALITY_CHOICES[int(raw) - 1][0]
        if raw in VALID_DATA_QUALITY:
            return raw
        print_fn("  invalid choice; try again.")


def interactive_entry(
    date: str,
    data_quality: str | None = None,
    source_url: str = "manual",
    input_fn=input,
    print_fn=print,
) -> list[dict]:
    """Prompt game-by-game. Blank away-team ends entry."""
    if data_quality is None:
        data_quality = choose_data_quality(input_fn, print_fn)
    print_fn(
        f"\nEntering games for {date} (data_quality={data_quality}). "
        "Blank AWAY team to finish.\n"
        "Away team is listed first on numberFire. Leave home prob blank to "
        "auto-fill 1 - away_prob."
    )
    rows: list[dict] = []
    while True:
        away = input_fn("AWAY team (blank to finish): ").strip()
        if not away:
            break
        home = input_fn("HOME team: ").strip()
        away_prob = input_fn("AWAY win prob (e.g. 52.5% or 0.525): ").strip()
        home_prob = input_fn("HOME win prob (blank = 1 - away): ").strip() or None
        notes = input_fn("notes (optional): ").strip()
        try:
            row = make_row(
                date, away, home, away_prob, home_prob,
                data_quality=data_quality, source_url=source_url, notes=notes,
            )
        except ManualEntryError as exc:
            print_fn(f"  !! {exc}  -- skipped, re-enter this game.")
            continue
        rows.append(row)
        print_fn(
            f"  added: {row['away_team']}@{row['home_team']} "
            f"pick {row['nf_pick_team']} {row['nf_pick_probability']:.3f}"
        )
    return rows


# ---------------------------------------------------------------------------
# CSV writing
# ---------------------------------------------------------------------------
def manual_predictions_path(date: str) -> Path:
    return config.MANUAL_DIR / f"numberfire_predictions_{date}.csv"


def _dedupe_key(row: dict) -> tuple:
    return (row["date"], row["away_team"], row["home_team"])


def write_manual_csv(
    date: str,
    rows: list[dict],
    append: bool = False,
) -> Path:
    """Write (or append) rows to the manual CSV for ``date``.

    On append, existing rows are preserved and any incoming row that duplicates
    an existing (date, away, home) is skipped (existing wins) with a warning.
    Returns the file path.
    """
    if not _DATE_RE.match(date):
        raise ManualEntryError(f"date must be YYYY-MM-DD, got {date!r}")
    config.ensure_directories()
    path = manual_predictions_path(date)

    existing: list[dict] = []
    if append and path.exists():
        with open(path, newline="") as fh:
            existing = list(csv.DictReader(fh))

    seen = {_dedupe_key(r) for r in existing}
    merged = list(existing)
    added = 0
    for r in rows:
        key = _dedupe_key(r)
        if key in seen:
            logger.warning(
                "skip duplicate %s @ %s on %s (already in file)",
                r["away_team"], r["home_team"], date,
            )
            continue
        seen.add(key)
        merged.append({c: r.get(c, "") for c in MANUAL_COLUMNS})
        added += 1

    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=MANUAL_COLUMNS)
        writer.writeheader()
        for r in merged:
            writer.writerow({c: r.get(c, "") for c in MANUAL_COLUMNS})

    logger.info("Wrote %d new row(s) (%d total) to %s", added, len(merged), path)
    return path


def preview(rows: list[dict], print_fn=print) -> None:
    if not rows:
        print_fn("(no rows)")
        return
    print_fn(f"\n{'AWAY':>5} @ {'HOME':<5} {'a_prob':>7} {'h_prob':>7}  "
             f"{'PICK':<5} {'p':>6}  data_quality")
    for r in rows:
        print_fn(
            f"{r['away_team']:>5} @ {r['home_team']:<5} "
            f"{float(r['nf_away_win_prob']):>7.3f} {float(r['nf_home_win_prob']):>7.3f}  "
            f"{r['nf_pick_team']:<5} {float(r['nf_pick_probability']):>6.3f}  "
            f"{r['data_quality']}"
        )
