"""Robust MLB team aliasing / normalization.

Every team string in this project (numberFire, sportsbook odds, MLB Stats API)
should pass through :func:`normalize_team_name` so joins are reliable.

Canonical codes (30):
    ARI ATL BAL BOS CHC CHW CIN CLE COL DET HOU KC LAA LAD MIA
    MIL MIN NYM NYY ATH PHI PIT SD SEA SF STL TB TEX TOR WSH

Note: the Athletics use canonical ``ATH`` (Oakland ``OAK`` is an alias).
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Canonical set of 30 team codes.
CANONICAL_TEAMS: set[str] = {
    "ARI", "ATL", "BAL", "BOS", "CHC", "CHW", "CIN", "CLE", "COL", "DET",
    "HOU", "KC", "LAA", "LAD", "MIA", "MIL", "MIN", "NYM", "NYY", "ATH",
    "PHI", "PIT", "SD", "SEA", "SF", "STL", "TB", "TEX", "TOR", "WSH",
}

# Alias map. Keys are normalized (uppercased, punctuation/space-stripped) so we
# can match "L.A. Dodgers", "la dodgers", "LAD" all the same.
# The canonical code itself is always accepted.
_ALIAS_SOURCE: dict[str, str] = {
    # Athletics (canonical ATH)
    "OAK": "ATH",
    "ATH": "ATH",
    "OAKLAND": "ATH",
    "OAKLAND ATHLETICS": "ATH",
    "ATHLETICS": "ATH",
    "LAS VEGAS ATHLETICS": "ATH",
    "SACRAMENTO ATHLETICS": "ATH",
    "A'S": "ATH",
    "AS": "ATH",
    # White Sox
    "CWS": "CHW",
    "CHW": "CHW",
    "CHICAGO WHITE SOX": "CHW",
    "WHITE SOX": "CHW",
    # Nationals
    "WSN": "WSH",
    "WSH": "WSH",
    "WAS": "WSH",
    "WASHINGTON": "WSH",
    "WASHINGTON NATIONALS": "WSH",
    "NATIONALS": "WSH",
    "NATS": "WSH",
    # Diamondbacks
    "AZ": "ARI",
    "ARI": "ARI",
    "ARIZONA": "ARI",
    "ARIZONA DIAMONDBACKS": "ARI",
    "DIAMONDBACKS": "ARI",
    "DBACKS": "ARI",
    "D-BACKS": "ARI",
    # Padres
    "SDP": "SD",
    "SD": "SD",
    "SAN DIEGO": "SD",
    "SAN DIEGO PADRES": "SD",
    "PADRES": "SD",
    # Giants
    "SFG": "SF",
    "SF": "SF",
    "SAN FRANCISCO": "SF",
    "SAN FRANCISCO GIANTS": "SF",
    "GIANTS": "SF",
    # Rays
    "TBR": "TB",
    "TB": "TB",
    "TAMPA BAY": "TB",
    "TAMPA BAY RAYS": "TB",
    "RAYS": "TB",
    # Royals
    "KCR": "KC",
    "KC": "KC",
    "KANSAS CITY": "KC",
    "KANSAS CITY ROYALS": "KC",
    "ROYALS": "KC",
    # --- remaining full names / common codes ---
    "ARIZONA D-BACKS": "ARI",
    "ATL": "ATL",
    "ATLANTA": "ATL",
    "ATLANTA BRAVES": "ATL",
    "BRAVES": "ATL",
    "BAL": "BAL",
    "BALTIMORE": "BAL",
    "BALTIMORE ORIOLES": "BAL",
    "ORIOLES": "BAL",
    "BOS": "BOS",
    "BOSTON": "BOS",
    "BOSTON RED SOX": "BOS",
    "RED SOX": "BOS",
    "CHC": "CHC",
    "CHICAGO CUBS": "CHC",
    "CUBS": "CHC",
    "CIN": "CIN",
    "CINCINNATI": "CIN",
    "CINCINNATI REDS": "CIN",
    "REDS": "CIN",
    "CLE": "CLE",
    "CLEVELAND": "CLE",
    "CLEVELAND GUARDIANS": "CLE",
    "GUARDIANS": "CLE",
    "CLEVELAND INDIANS": "CLE",  # legacy name
    "INDIANS": "CLE",
    "COL": "COL",
    "COLORADO": "COL",
    "COLORADO ROCKIES": "COL",
    "ROCKIES": "COL",
    "DET": "DET",
    "DETROIT": "DET",
    "DETROIT TIGERS": "DET",
    "TIGERS": "DET",
    "HOU": "HOU",
    "HOUSTON": "HOU",
    "HOUSTON ASTROS": "HOU",
    "ASTROS": "HOU",
    "LAA": "LAA",
    "LA ANGELS": "LAA",
    "L.A. ANGELS": "LAA",
    "LOS ANGELES ANGELS": "LAA",
    "ANAHEIM": "LAA",
    "ANGELS": "LAA",
    "LAD": "LAD",
    "LA DODGERS": "LAD",
    "L.A. DODGERS": "LAD",
    "LOS ANGELES DODGERS": "LAD",
    "DODGERS": "LAD",
    "MIA": "MIA",
    "MIAMI": "MIA",
    "MIAMI MARLINS": "MIA",
    "MARLINS": "MIA",
    "FLA": "MIA",  # legacy Florida Marlins
    "MIL": "MIL",
    "MILWAUKEE": "MIL",
    "MILWAUKEE BREWERS": "MIL",
    "BREWERS": "MIL",
    "MIN": "MIN",
    "MINNESOTA": "MIN",
    "MINNESOTA TWINS": "MIN",
    "TWINS": "MIN",
    "NYM": "NYM",
    "NY METS": "NYM",
    "N.Y. METS": "NYM",
    "NEW YORK METS": "NYM",
    "METS": "NYM",
    "NYY": "NYY",
    "NY YANKEES": "NYY",
    "N.Y. YANKEES": "NYY",
    "NEW YORK YANKEES": "NYY",
    "YANKEES": "NYY",
    "PHI": "PHI",
    "PHILADELPHIA": "PHI",
    "PHILADELPHIA PHILLIES": "PHI",
    "PHILLIES": "PHI",
    "PIT": "PIT",
    "PITTSBURGH": "PIT",
    "PITTSBURGH PIRATES": "PIT",
    "PIRATES": "PIT",
    "SEA": "SEA",
    "SEATTLE": "SEA",
    "SEATTLE MARINERS": "SEA",
    "MARINERS": "SEA",
    "STL": "STL",
    "ST LOUIS": "STL",
    "ST. LOUIS": "STL",
    "SAINT LOUIS": "STL",
    "ST LOUIS CARDINALS": "STL",
    "ST. LOUIS CARDINALS": "STL",
    "CARDINALS": "STL",
    "TEX": "TEX",
    "TEXAS": "TEX",
    "TEXAS RANGERS": "TEX",
    "RANGERS": "TEX",
    "TOR": "TOR",
    "TORONTO": "TOR",
    "TORONTO BLUE JAYS": "TOR",
    "BLUE JAYS": "TOR",
}


def _norm_key(name: str) -> str:
    """Normalize a raw string to an alias-map key.

    Uppercases and collapses whitespace. Punctuation like periods and
    apostrophes is preserved where it helps disambiguate (e.g. ``A'S``) but
    also stripped in a fallback key.
    """
    s = str(name).strip().upper()
    s = re.sub(r"\s+", " ", s)
    return s


def _norm_key_stripped(name: str) -> str:
    """Aggressive key: remove all non-alphanumeric characters."""
    return re.sub(r"[^A-Z0-9]", "", str(name).strip().upper())


# Build a secondary lookup with punctuation stripped for fuzzy fallback.
_ALIAS_STRIPPED: dict[str, str] = {}
for _k, _v in _ALIAS_SOURCE.items():
    _ALIAS_STRIPPED.setdefault(_norm_key_stripped(_k), _v)


def normalize_team_name(name: str, strict: bool = False) -> str:
    """Return the canonical team code for ``name``.

    Parameters
    ----------
    name:
        Any team string (code, abbreviation, city, full name).
    strict:
        If True, raise ``ValueError`` on an unknown team. If False (default),
        log a warning and return the cleaned uppercase token unchanged.
    """
    if name is None:
        if strict:
            raise ValueError("Cannot normalize team name: got None")
        logger.warning("normalize_team_name received None")
        return ""

    key = _norm_key(name)
    if key in CANONICAL_TEAMS:
        return key
    if key in _ALIAS_SOURCE:
        return _ALIAS_SOURCE[key]

    stripped = _norm_key_stripped(name)
    if stripped in CANONICAL_TEAMS:
        return stripped
    if stripped in _ALIAS_STRIPPED:
        return _ALIAS_STRIPPED[stripped]

    msg = f"Unknown MLB team name: {name!r} (normalized key {key!r})"
    if strict:
        raise ValueError(msg)
    logger.warning(msg)
    return key


def normalize_matchup(
    home_team: str, away_team: str, strict: bool = False
) -> tuple[str, str]:
    """Normalize a (home, away) pair. Returns ``(home_code, away_code)``."""
    return (
        normalize_team_name(home_team, strict=strict),
        normalize_team_name(away_team, strict=strict),
    )


def is_canonical(code: str) -> bool:
    """True if ``code`` is already one of the 30 canonical team codes."""
    return str(code).strip().upper() in CANONICAL_TEAMS
