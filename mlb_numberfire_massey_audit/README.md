# mlb_numberfire_massey_audit

A **research / audit** project (not a betting bot) that replicates the original
chat MLB value model and tests one new question: **would layering a pre-game
Massey score-differential filter on top of numberFire have improved the
moneyline results?**

## What this is

- **MLB only.**
- **numberFire only** as the predictive model. (No Dimers, no WNBA, no Queen
  Kitty, no Colley, no pitcher-adjusted model — those are explicitly out of
  scope for this pass.)
- **Primary market: moneyline only.** In the original audit the numberFire
  bucket that performed well was MLB moneylines, so this project tests exactly
  that area first.

## The model (faithful to the original chat workflow)

1. Take numberFire's MLB win probability for a side.
2. Take the sportsbook moneyline and convert it to a market implied probability.
3. **Predictive model edge = numberFire probability − market implied probability.**
4. Theoretical EV from the numberFire probability and the American odds
   (1 unit risked).
5. Add line-movement / pitcher context notes when available (columns exist;
   not required).
6. Grade flat, **1 unit risked** per play.

A side is labeled:
- `value_candidate` — edge ≥ `EDGE_THRESHOLD_NF` (default 3 pp) **and** EV > `MIN_EV_THRESHOLD`.
- `lean_only` — positive edge but below threshold.
- `pass` — edge ≤ 0.

## Massey is a *filter*, not an average

Massey ratings are computed **only from completed games before each prediction
date** and produce a projected home run-margin. That margin is compared to the
numberFire pick to label agreement — it is **never averaged into the numberFire
probability**. numberFire alone is the baseline; numberFire + Massey
agree/neutral/conflict is the research test.

Agreement (thresholds in `config.py`, runs of projected home margin):
- `agree` — Massey leans the same way as the pick, by ≥ `MASSEY_AGREE_MARGIN_RUNS`.
- `neutral` — within the neutral band.
- `conflict` — Massey leans against the pick, by ≥ `|MASSEY_CONFLICT_MARGIN_RUNS|`.
- `insufficient_data` — not enough prior games to rate both teams.

### No future leakage

For a prediction on date *D*, Massey uses only games completed **strictly before
*D***. Target-date final scores are used **only to grade results**, never to
build that date's ratings. This is enforced by
`massey.get_games_before_date` and covered by `tests/test_massey_no_leakage.py`.

## Data ethics

- Respects robots.txt / site terms; polite rate limiting (`SLEEP_SECONDS`).
- Does **not** bypass paywalls.
- **Never fabricates historical numberFire predictions.** If a page does not
  expose a real historical prediction, the row is logged as `unavailable`
  (see `data/results/numberfire_backfill_failures.csv`) instead of being
  invented.
- Raw HTML/JSON responses are saved under `data/raw/` when
  `SAVE_RAW_RESPONSES=True`.
- Every historical row carries a `data_quality` note:
  `exact_historical_prediction`, `observed_current_page`, `article_timestamp`,
  `wayback_snapshot`, `manual_import`, or `unavailable`.

## Important reality check on numberFire history

numberFire does **not** publish a clean historical-prediction API, and its live
pages are JavaScript-rendered. The scraper probes candidate URLs, saves what it
finds, and parses defensively — but for **past dates it will often recover
nothing**, and a live scrape of a past page would be *current-page* data, not
the historical prediction. **The project is designed to run fully from manual
CSVs** so the audit still works even when scraping cannot recover history.

## Install

```bash
cd mlb_numberfire_massey_audit
python -m venv .venv && source .venv/bin/activate   # optional
pip install -r requirements.txt
cp .env.example .env    # optional; all values have defaults
```

Python 3.12 is the target (works on 3.11+).

## Manual CSV fallback (the reliable path)

Drop per-date files in `data/manual/` (see the `*_EXAMPLE.csv` files):

- `data/manual/numberfire_predictions_YYYY-MM-DD.csv`
  columns: `date, source_url, away_team, home_team, nf_away_win_prob,
  nf_home_win_prob, nf_pick_team, nf_pick_probability, data_quality, notes`
- `data/manual/mlb_moneyline_odds_YYYY-MM-DD.csv`
  columns: `odds_observed_at_utc, date, sportsbook, away_team, home_team,
  away_moneyline, home_moneyline, [opening/closing_*_moneyline optional],
  source_notes`

Teams can be any alias (e.g. `OAK`, `Athletics`, `CWS`) — everything is
normalized by `team_map.py`. Final scores come from the free MLB Stats API, so
you only supply predictions and odds.

## Live odds via The Odds API (optional)

Instead of manual odds CSVs you can pull MLB moneylines automatically from
[The Odds API](https://the-odds-api.com). Set in `.env`:

```bash
ODDS_SOURCE=api
ODDS_API_KEY=your_key_here
ODDS_API_REGIONS=us
ODDS_API_MODE=auto          # historical for past dates, current for today/future
# ODDS_API_BOOKMAKERS=draftkings,fanduel   # optional; blank = all books
```

Behavior:
- `ODDS_API_MODE=historical` pulls a snapshot near **noon ET** of each game date
  (the pre-game line) from the `/v4/historical/...` endpoint — this is what a
  real past-date backtest needs, and requires a **paid** Odds API plan.
- `ODDS_API_MODE=current` uses live/upcoming odds only (works on the free tier
  but can't price past dates).
- `ODDS_API_MODE=auto` picks historical for past dates and current for today.
- Games are matched to a date by the **US/Eastern** date of `commence_time`, so
  late West-coast games land on the correct MLB day.
- One row is produced per (game, sportsbook); raw JSON is saved under
  `data/raw/odds/<date>/`. On any API error the loader logs and returns empty,
  so `load_moneyline_odds` **falls back to the manual CSV** for that date.

> Note: The Odds API host may be blocked by restrictive network policies (e.g.
> some CI/sandbox environments). Run the API path from an environment with open
> outbound HTTPS, or use the manual CSV fallback.

**Verify your key first** with the standalone checker (only needs `requests`):

```bash
python scripts/verify_odds_api.py                       # auth + quota + current odds
python scripts/verify_odds_api.py --date 2026-07-25 --historical   # also test paid snapshot
```

It confirms auth, prints your remaining request quota, previews a few MLB
moneylines, and tells you whether the historical (paid) endpoint is available on
your plan. It reads the key from `--api-key`, then `ODDS_API_KEY`, then `.env`,
and never prints the key or writes to disk.

## Commands

```bash
# Probe likely numberFire source pages for a date and save raw responses
python main.py --probe-numberfire --date 2026-07-25

# Scrape one day of numberFire predictions (or load manual CSV if present)
python main.py --scrape-numberfire --date 2026-07-25

# Backfill a date range (manual > live scrape > optional Wayback; logs failures)
python main.py --backfill-numberfire --start-date 2026-03-26 --end-date 2026-07-25

# Build numberFire candidates + Massey for one date
python main.py --build-candidates --date 2026-07-25

# Full numberFire vs numberFire+Massey backtest over a range
python main.py --backtest --start-date 2026-03-26 --end-date 2026-07-25

# Full season-to-date (uses SEASON_START/END from config)
python main.py --season --year 2026
```

## Outputs and how to read them

The backtest writes to `data/results/`:
- `nf_massey_candidates_START_END.csv` — every graded pick with edge, EV,
  Massey agreement, result, and 1u P/L.
- `nf_massey_summary_START_END.md` — the readable report.
- `nf_massey_summary_START_END.xlsx` — same data in Excel (if `openpyxl`).

The markdown report sections:
1. **Executive summary** — numberFire alone vs Massey-agree vs Massey-conflict,
   and a verdict on whether Massey helped.
2. **Data availability** — which dates were missing numberFire / odds, and the
   `data_quality` mix of graded rows. **Read this first** — a small or skewed
   sample makes every other number fragile.
3. **numberFire alone** — the baseline record / ROI.
4. **numberFire + Massey** — agree / neutral / conflict / insufficient_data.
5. **Edge buckets** — 0–2, 2–3, 3–5, 5 pp+.
6. **Favorite / dog split.**
7. **Worst losing positions** — largest single losses and repeated losing teams.
8. **Caveats** — historical availability, missing odds, no pitcher model, Massey
   is a filter not a projection system, entry price is opening/observed (not
   closing).

Interpreting the verdict: compare the **ROI** of `Massey agree` and
`Massey conflict` against the `numberFire alone` baseline. If `agree` ROI beats
baseline and `conflict` ROI is worse, the filter is additive **in this sample** —
but always weigh it against the sample size in section 2 before trusting it.

## Project layout

```
mlb_numberfire_massey_audit/
    config.py               # env-driven config + thresholds
    team_map.py             # MLB alias normalization (OAK->ATH, CWS->CHW, ...)
    odds_math.py            # implied prob, EV, 1u P/L, model edge
    mlb_results.py          # MLB Stats API scores + moneyline grading
    massey.py               # pre-game Massey ratings (no leakage)
    numberfire_scraper.py   # probe / scrape / manual load / backfill
    odds_collector.py       # manual CSV loader + The Odds API collector
    candidate_builder.py    # numberFire candidates + Massey join
    backtest.py             # backtest + comparison-group summaries
    reports.py              # markdown / csv / xlsx reports
    main.py                 # argparse CLI
    tests/                  # pytest
    data/                   # raw / manual / processed / results / reports / logs
```

## Scope reminders

Not used in this pass: Dimers, WNBA, Queen Kitty, Colley, and any new
pitcher-adjusted model. Massey is included solely as an independent filter to
answer the research question above.
