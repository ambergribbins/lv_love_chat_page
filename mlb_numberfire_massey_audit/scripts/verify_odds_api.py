#!/usr/bin/env python3
"""Standalone verifier for The Odds API (the-odds-api.com).

Run this on a machine with open outbound HTTPS and your key set. It confirms:
  1. Auth works and prints your remaining request quota.
  2. Current MLB moneyline (h2h) odds can be fetched.
  3. (Optional) A historical pre-game snapshot for a date can be fetched --
     this is the paid-plan endpoint the backtest uses.

It is intentionally self-contained: only the `requests` package is required
(`pip install requests`). It reads the key from --api-key, then the
ODDS_API_KEY environment variable, then a sibling .env file. Nothing is written
to disk and the key is never printed.

Examples
--------
  python scripts/verify_odds_api.py
  python scripts/verify_odds_api.py --date 2026-07-25
  python scripts/verify_odds_api.py --date 2026-07-25 --historical
  ODDS_API_KEY=xxxx python scripts/verify_odds_api.py --regions us --bookmakers draftkings,fanduel
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import sys
from pathlib import Path

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("ERROR: the 'requests' package is required -> pip install requests")

try:
    from zoneinfo import ZoneInfo

    _ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    _ET = _dt.timezone(_dt.timedelta(hours=-4))  # EDT fallback

DEFAULT_BASE = "https://api.the-odds-api.com"
DEFAULT_SPORT = "baseball_mlb"

# ANSI helpers (degrade gracefully if not a TTY).
_TTY = sys.stdout.isatty()


def _c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _TTY else text


def ok(msg: str) -> None:
    print(_c("  OK  ", "42;30") + " " + msg)


def warn(msg: str) -> None:
    print(_c(" WARN ", "43;30") + " " + msg)


def fail(msg: str) -> None:
    print(_c(" FAIL ", "41;37") + " " + msg)


def info(msg: str) -> None:
    print("       " + msg)


# ---------------------------------------------------------------------------
# Key resolution
# ---------------------------------------------------------------------------
def _load_env_file(path: Path) -> dict:
    values: dict = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        values[k.strip()] = v.strip().strip('"').strip("'")
    return values


def resolve_api_key(cli_key: str | None) -> str | None:
    if cli_key:
        return cli_key.strip()
    env_key = os.getenv("ODDS_API_KEY")
    if env_key and env_key.strip():
        return env_key.strip()
    # Look for a .env next to the project (script is in scripts/).
    here = Path(__file__).resolve().parent
    for candidate in (here.parent / ".env", here / ".env", Path.cwd() / ".env"):
        vals = _load_env_file(candidate)
        if vals.get("ODDS_API_KEY"):
            info(f"Loaded ODDS_API_KEY from {candidate}")
            return vals["ODDS_API_KEY"].strip()
    return None


def _quota_from_headers(headers) -> str:
    remaining = headers.get("x-requests-remaining")
    used = headers.get("x-requests-used")
    last = headers.get("x-requests-last")
    bits = []
    if remaining is not None:
        bits.append(f"remaining={remaining}")
    if used is not None:
        bits.append(f"used={used}")
    if last is not None:
        bits.append(f"last_cost={last}")
    return ", ".join(bits) if bits else "(no quota headers returned)"


def _snapshot_timestamp(date: str, hour_et: int = 12) -> str:
    d = _dt.date.fromisoformat(date)
    local = _dt.datetime(d.year, d.month, d.day, hour_et, 0, 0, tzinfo=_ET)
    return local.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------
def check_auth(base: str, api_key: str) -> bool:
    print(_c("\n[1] Auth + quota", "1"))
    url = f"{base}/v4/sports/"
    try:
        resp = requests.get(url, params={"apiKey": api_key}, timeout=30)
    except Exception as exc:  # noqa: BLE001
        fail(f"Network error contacting {url}: {exc}")
        info("If this is a sandbox/CI box, the host may be firewalled. Run locally.")
        return False

    if resp.status_code == 401:
        fail("401 Unauthorized -- the API key is invalid or expired.")
        return False
    if resp.status_code != 200:
        fail(f"Unexpected HTTP {resp.status_code}: {resp.text[:200]}")
        return False

    ok(f"Authenticated. Quota: {_quota_from_headers(resp.headers)}")
    try:
        sports = resp.json()
        mlb = [s for s in sports if s.get("key") == DEFAULT_SPORT]
        if mlb:
            active = mlb[0].get("active")
            ok(f"'{DEFAULT_SPORT}' is listed (active={active}).")
        else:
            warn(f"'{DEFAULT_SPORT}' not in the sports list right now.")
    except Exception:  # noqa: BLE001
        warn("Could not parse the sports list JSON (auth still OK).")
    return True


def check_current(base: str, api_key: str, sport: str, regions: str,
                  bookmakers: str, date: str | None) -> bool:
    print(_c("\n[2] Current MLB h2h odds", "1"))
    url = f"{base}/v4/sports/{sport}/odds"
    params = {
        "apiKey": api_key,
        "regions": regions,
        "markets": "h2h",
        "oddsFormat": "american",
        "dateFormat": "iso",
    }
    if bookmakers:
        params["bookmakers"] = bookmakers
    try:
        resp = requests.get(url, params=params, timeout=30)
    except Exception as exc:  # noqa: BLE001
        fail(f"Network error: {exc}")
        return False
    if resp.status_code != 200:
        fail(f"HTTP {resp.status_code}: {resp.text[:200]}")
        return False

    games = resp.json()
    ok(f"Fetched {len(games)} upcoming game(s). Quota: "
       f"{_quota_from_headers(resp.headers)}")
    _preview_games(games, date, limit=3)
    return True


def check_historical(base: str, api_key: str, sport: str, regions: str,
                     bookmakers: str, date: str, hour_et: int) -> bool:
    print(_c("\n[3] Historical snapshot (pre-game line)", "1"))
    snap = _snapshot_timestamp(date, hour_et)
    info(f"Requesting snapshot at {snap} (~{hour_et}:00 ET on {date}).")
    url = f"{base}/v4/historical/sports/{sport}/odds"
    params = {
        "apiKey": api_key,
        "regions": regions,
        "markets": "h2h",
        "oddsFormat": "american",
        "dateFormat": "iso",
        "date": snap,
    }
    if bookmakers:
        params["bookmakers"] = bookmakers
    try:
        resp = requests.get(url, params=params, timeout=30)
    except Exception as exc:  # noqa: BLE001
        fail(f"Network error: {exc}")
        return False
    if resp.status_code == 422:
        warn("HTTP 422 -- historical odds are not available on your plan "
             "(or the params/date are invalid). This endpoint requires a paid "
             "Odds API tier. 'current' mode still works for today/upcoming.")
        return False
    if resp.status_code != 200:
        fail(f"HTTP {resp.status_code}: {resp.text[:200]}")
        return False

    payload = resp.json()
    games = payload.get("data") if isinstance(payload, dict) else payload
    ts = payload.get("timestamp") if isinstance(payload, dict) else snap
    ok(f"Historical snapshot returned {len(games or [])} game(s) at {ts}. "
       f"Quota: {_quota_from_headers(resp.headers)}")
    _preview_games(games or [], date, limit=3)
    return True


def _eastern_date_of(commence_iso: str) -> str | None:
    if not commence_iso:
        return None
    try:
        ts = _dt.datetime.fromisoformat(commence_iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=_dt.timezone.utc)
    return ts.astimezone(_ET).date().isoformat()


def _preview_games(games: list, date: str | None, limit: int = 3) -> None:
    shown = 0
    for g in games or []:
        if date and _eastern_date_of(g.get("commence_time")) != date:
            continue
        home = g.get("home_team")
        away = g.get("away_team")
        books = g.get("bookmakers", []) or []
        line = ""
        if books:
            h2h = next((m for m in books[0].get("markets", [])
                        if m.get("key") == "h2h"), None)
            if h2h:
                prices = {o.get("name"): o.get("price")
                          for o in h2h.get("outcomes", [])}
                line = f" | {books[0].get('title')}: {prices}"
        info(f"{away} @ {home}  ({_eastern_date_of(g.get('commence_time'))}){line}")
        shown += 1
        if shown >= limit:
            break
    if date and shown == 0:
        warn(f"No games matched ET date {date} in this response.")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Verify The Odds API key + endpoints.")
    p.add_argument("--api-key", default=None, help="Override ODDS_API_KEY.")
    p.add_argument("--base", default=os.getenv("ODDS_API_BASE", DEFAULT_BASE))
    p.add_argument("--sport", default=os.getenv("ODDS_API_SPORT", DEFAULT_SPORT))
    p.add_argument("--regions", default=os.getenv("ODDS_API_REGIONS", "us"))
    p.add_argument("--bookmakers", default=os.getenv("ODDS_API_BOOKMAKERS", ""))
    p.add_argument("--date", default=None,
                   help="Game date YYYY-MM-DD to filter/preview and snapshot.")
    p.add_argument("--historical", action="store_true",
                   help="Also test the historical snapshot endpoint (paid plan).")
    p.add_argument("--current", action="store_true",
                   help="Only test auth + current odds (skip historical).")
    p.add_argument("--snapshot-hour-et", type=int,
                   default=int(os.getenv("ODDS_API_SNAPSHOT_HOUR_ET", "12")))
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    print(_c("The Odds API verifier", "1;4"))

    api_key = resolve_api_key(args.api_key)
    if not api_key:
        fail("No API key found. Pass --api-key, set ODDS_API_KEY, or add it to .env")
        return 2
    info(f"Using key ending in ...{api_key[-4:]}  (base: {args.base})")

    results = {"auth": check_auth(args.base, api_key)}
    if not results["auth"]:
        print(_c("\nStopping: auth failed.", "1;31"))
        return 1

    results["current"] = check_current(
        args.base, api_key, args.sport, args.regions, args.bookmakers, args.date
    )

    if args.historical and not args.current:
        if not args.date:
            warn("--historical needs --date YYYY-MM-DD; skipping historical check.")
        else:
            results["historical"] = check_historical(
                args.base, api_key, args.sport, args.regions, args.bookmakers,
                args.date, args.snapshot_hour_et,
            )

    print(_c("\nSummary", "1"))
    for name, passed in results.items():
        (ok if passed else warn)(f"{name}: {'passed' if passed else 'not available'}")

    # Exit 0 if auth + current both worked; historical is informational.
    return 0 if results.get("current") else 1


if __name__ == "__main__":
    raise SystemExit(main())
