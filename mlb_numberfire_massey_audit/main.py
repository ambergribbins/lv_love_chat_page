"""Command-line entry point for the MLB numberFire + Massey audit.

Examples
--------
  python main.py --probe-numberfire --date 2026-07-25
  python main.py --scrape-numberfire --date 2026-07-25
  python main.py --enter-numberfire --date 2026-07-25            # interactive
  python main.py --enter-numberfire --date 2026-07-25 --input slate.txt \
      --data-quality exact_historical_prediction
  python main.py --backfill-numberfire --start-date 2026-03-26 --end-date 2026-07-25
  python main.py --build-candidates --date 2026-07-25
  python main.py --backtest --start-date 2026-03-26 --end-date 2026-07-25
  python main.py --season --year 2026
"""

from __future__ import annotations

import argparse
import json
import logging

import config


def _setup_logging() -> None:
    config.ensure_directories()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(config.LOGS_DIR / "audit.log"),
        ],
    )


def cmd_probe(date: str) -> None:
    from numberfire_scraper import probe_numberfire_sources

    result = probe_numberfire_sources(date)
    print(json.dumps(result, indent=2, default=str))


def cmd_scrape(date: str) -> None:
    from numberfire_scraper import get_numberfire_predictions

    df = get_numberfire_predictions(date)
    out = config.PROCESSED_DIR / f"numberfire_{date}.csv"
    df.to_csv(out, index=False)
    print(f"numberFire rows for {date}: {len(df)} -> {out}")
    if df.empty:
        print(
            "No predictions recovered. This is expected for historical dates. "
            f"Add a manual CSV at data/manual/numberfire_predictions_{date}.csv"
        )
    else:
        print(df[["away_team", "home_team", "nf_pick_team", "nf_pick_probability",
                  "data_quality"]].to_string(index=False))


def cmd_backfill(start_date: str, end_date: str) -> None:
    from numberfire_scraper import backfill_numberfire_predictions

    df = backfill_numberfire_predictions(start_date, end_date)
    out = config.PROCESSED_DIR / f"numberfire_backfill_{start_date}_{end_date}.csv"
    df.to_csv(out, index=False)
    print(f"Backfill rows: {len(df)} -> {out}")
    print(
        "Any unavailable dates are logged to "
        "data/results/numberfire_backfill_failures.csv (not fabricated)."
    )


def cmd_enter_numberfire(args) -> None:
    """Manual-entry helper -> data/manual/numberfire_predictions_DATE.csv."""
    import sys

    import manual_entry as me

    date = args.date
    dq = args.data_quality
    if dq and dq not in me.VALID_DATA_QUALITY:
        raise SystemExit(
            f"--data-quality must be one of {sorted(me.VALID_DATA_QUALITY)}"
        )
    source_url = args.source_url or "manual"

    # Gather input text from --input file, stdin (piped), or interactive.
    text = None
    if args.input:
        text = open(args.input).read()
    elif not sys.stdin.isatty():
        piped = sys.stdin.read()
        text = piped if piped.strip() else None

    try:
        if text is not None:
            dq = dq or "manual_import"
            if args.loose:
                rows = me.parse_loose(
                    text, date, data_quality=dq, source_url=source_url,
                    home_first=args.home_first,
                )
            else:
                rows = me.parse_structured(
                    text, date, data_quality=dq, source_url=source_url
                )
        else:
            rows = me.interactive_entry(date, data_quality=dq, source_url=source_url)
    except me.ManualEntryError as exc:
        raise SystemExit(f"Manual entry failed: {exc}")

    if not rows:
        print("No rows entered; nothing written.")
        return

    me.preview(rows)
    path = me.write_manual_csv(date, rows, append=args.append)
    print(f"\nWrote {len(rows)} game(s) to {path} (append={args.append}).")
    print("Next: python main.py --build-candidates --date " + date)


def cmd_fanduel(args) -> None:
    """Recover numberFire predictions from FanDuel Research (live or saved file)."""
    import json as _json

    import fanduel_scraper as fd

    date = args.date

    # Probe mode: characterize page structure for parser calibration.
    if args.probe_fanduel:
        if args.input:
            info = fd.probe_fanduel_file(args.input, date)
        else:
            _require(date, "--date")
            info = fd.probe_fanduel_source(date)
        print(_json.dumps(info, indent=2, default=str))
        return

    _require(date, "--date")
    if args.input:
        df = fd.import_fanduel_saved_file(args.input, date)
    else:
        try:
            df = fd.scrape_fanduel_mlb(date)
        except fd.FanDuelBlockedError as exc:
            print(f"FanDuel blocked the live fetch: {exc}")
            print(
                "Save the page in your browser (Ctrl+S -> 'Webpage, HTML Only') "
                "and re-run with:  --import-fanduel-file --input <file.html> "
                f"--date {date}"
            )
            return

    if df.empty:
        print(
            f"No numberFire predictions parsed for {date}. "
            "Run with --probe-fanduel to inspect the page structure, then the "
            "parser can be calibrated."
        )
        return

    fd.save_to_manual(df, date, append=args.append)
    cols = ["away_team", "home_team", "nf_pick_team", "nf_pick_probability",
            "data_quality"]
    print(df[cols].to_string(index=False))
    print(f"\nSaved to data/manual/numberfire_predictions_{date}.csv")
    print(f"Next: python main.py --build-candidates --date {date}")


def cmd_build_candidates(date: str) -> None:
    from candidate_builder import (
        add_massey_to_candidates,
        build_numberfire_candidates,
    )
    from massey import get_games_before_date
    from mlb_results import get_mlb_completed_games

    candidates = build_numberfire_candidates(date)
    if candidates.empty:
        print(f"No candidates for {date} (missing numberFire and/or odds).")
        return

    season_start = f"{date[:4]}-01-01"
    all_completed = get_mlb_completed_games(season_start, date)
    prior = get_games_before_date(all_completed, date)
    candidates = add_massey_to_candidates(candidates, prior)

    out = config.PROCESSED_DIR / f"candidates_{date}.csv"
    candidates.to_csv(out, index=False)
    print(f"Candidates for {date}: {len(candidates)} -> {out}")
    cols = ["pick_team", "moneyline", "nf_probability",
            "market_implied_probability", "predictive_model_edge",
            "theoretical_ev", "value_label", "massey_agreement"]
    print(candidates[cols].to_string(index=False))


def cmd_backtest(start_date: str, end_date: str) -> None:
    from backtest import backtest_numberfire_massey
    from reports import create_backtest_report

    df = backtest_numberfire_massey(start_date, end_date)
    written = create_backtest_report(df, start_date, end_date)
    print(f"Graded rows: {len(df)}")
    for kind, path in written.items():
        print(f"  {kind}: {path}")
    if df.empty:
        print(
            "No graded candidates. Add manual numberFire predictions and odds "
            "CSVs for these dates, then re-run."
        )


def cmd_season(year: int) -> None:
    start = f"{year}-{config.SEASON_START_MMDD}"
    end = f"{year}-{config.SEASON_END_MMDD}"
    print(f"Running season {year}: {start} .. {end}")
    cmd_backtest(start, end)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="MLB numberFire + Massey audit (moneyline only)."
    )
    p.add_argument("--probe-numberfire", action="store_true")
    p.add_argument("--scrape-numberfire", action="store_true")
    p.add_argument("--enter-numberfire", action="store_true",
                   help="Manually enter/import a numberFire slate for --date.")
    p.add_argument("--scrape-fanduel", action="store_true",
                   help="Live-fetch FanDuel Research (numberFire) for --date.")
    p.add_argument("--import-fanduel-file", action="store_true",
                   help="Parse a browser-saved FanDuel HTML file (--input) for --date.")
    p.add_argument("--probe-fanduel", action="store_true",
                   help="Inspect a FanDuel page's structure (--date live, or --input file).")
    p.add_argument("--backfill-numberfire", action="store_true")
    p.add_argument("--build-candidates", action="store_true")
    p.add_argument("--backtest", action="store_true")
    p.add_argument("--season", action="store_true")
    p.add_argument("--date", type=str, default=None)
    p.add_argument("--start-date", type=str, default=None)
    p.add_argument("--end-date", type=str, default=None)
    p.add_argument("--year", type=int, default=config.SEASON_YEAR)
    # Manual-entry options (used with --enter-numberfire).
    p.add_argument("--input", type=str, default=None,
                   help="Read slate text from this file instead of stdin/interactive.")
    p.add_argument("--loose", action="store_true",
                   help="Parse --input as loose pasted text (default: structured).")
    p.add_argument("--home-first", action="store_true",
                   help="Loose mode: teams are listed home-first (default away-first).")
    p.add_argument("--append", action="store_true",
                   help="Append to an existing manual CSV instead of overwriting.")
    p.add_argument("--data-quality", type=str, default=None,
                   help="Tag rows: exact_historical_prediction | observed_current_page "
                        "| article_timestamp | wayback_snapshot | manual_import.")
    p.add_argument("--source-url", type=str, default=None,
                   help="Source URL to record on manual rows.")
    return p


def main(argv=None) -> int:
    _setup_logging()
    args = build_parser().parse_args(argv)

    if args.probe_numberfire:
        _require(args.date, "--date")
        cmd_probe(args.date)
    elif args.scrape_numberfire:
        _require(args.date, "--date")
        cmd_scrape(args.date)
    elif args.enter_numberfire:
        _require(args.date, "--date")
        cmd_enter_numberfire(args)
    elif args.scrape_fanduel or args.import_fanduel_file or args.probe_fanduel:
        cmd_fanduel(args)
    elif args.backfill_numberfire:
        _require(args.start_date, "--start-date")
        _require(args.end_date, "--end-date")
        cmd_backfill(args.start_date, args.end_date)
    elif args.build_candidates:
        _require(args.date, "--date")
        cmd_build_candidates(args.date)
    elif args.backtest:
        _require(args.start_date, "--start-date")
        _require(args.end_date, "--end-date")
        cmd_backtest(args.start_date, args.end_date)
    elif args.season:
        cmd_season(args.year)
    else:
        build_parser().print_help()
        return 1
    return 0


def _require(value, name: str) -> None:
    if not value:
        raise SystemExit(f"Missing required argument: {name}")


if __name__ == "__main__":
    raise SystemExit(main())
