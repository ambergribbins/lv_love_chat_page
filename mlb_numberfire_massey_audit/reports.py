"""Readable reports for the numberFire + Massey backtest.

Writes:
  * data/results/nf_massey_candidates_START_END.csv  (graded rows)
  * data/results/nf_massey_summary_START_END.md       (human report)
  * data/results/nf_massey_summary_START_END.xlsx      (if openpyxl available)
"""

from __future__ import annotations

import logging

import pandas as pd

import config
from backtest import summarize_backtest

logger = logging.getLogger(__name__)


def _fmt_record(rec: dict) -> str:
    return (
        f"{rec['bets']} bets | {rec['wins']}-{rec['losses']}-{rec['pushes']} "
        f"| P/L {rec['pnl_1u']:+.2f}u | ROI {rec['roi'] * 100:+.1f}% "
        f"| win% {rec['win_rate'] * 100:.1f}% "
        f"| avg edge {rec['avg_edge'] * 100:+.1f}pp | avg EV {rec['avg_ev']:+.3f}"
    )


def _verdict(alone: dict, agree: dict, conflict: dict) -> str:
    if alone["bets"] == 0:
        return "No graded numberFire value candidates -- cannot judge Massey's effect."
    base_roi = alone["roi"]
    agree_roi = agree["roi"] if agree["bets"] else None
    conflict_roi = conflict["roi"] if conflict["bets"] else None
    parts = []
    if agree_roi is not None:
        delta = agree_roi - base_roi
        parts.append(
            f"Massey-agree ROI {agree_roi * 100:+.1f}% vs baseline "
            f"{base_roi * 100:+.1f}% ({delta * 100:+.1f}pp)"
        )
    if conflict_roi is not None:
        delta = conflict_roi - base_roi
        parts.append(
            f"Massey-conflict ROI {conflict_roi * 100:+.1f}% "
            f"({delta * 100:+.1f}pp vs baseline)"
        )
    if not parts:
        return "Insufficient Massey-bucketed sample to judge the filter."
    improved = agree_roi is not None and agree_roi > base_roi
    tail = (
        " -> Massey agreement looks additive in this sample."
        if improved
        else " -> Massey agreement did NOT beat the baseline in this sample."
    )
    return "; ".join(parts) + tail


def create_backtest_report(
    backtest_df: pd.DataFrame, start_date: str, end_date: str
) -> dict:
    """Render markdown + csv + xlsx. Returns paths written."""
    config.ensure_directories()
    tag = f"{start_date}_{end_date}"
    csv_path = config.RESULTS_DIR / f"nf_massey_candidates_{tag}.csv"
    md_path = config.RESULTS_DIR / f"nf_massey_summary_{tag}.md"
    xlsx_path = config.RESULTS_DIR / f"nf_massey_summary_{tag}.xlsx"

    backtest_df.to_csv(csv_path, index=False)
    summary = summarize_backtest(backtest_df)

    md = _render_markdown(summary, backtest_df, start_date, end_date)
    md_path.write_text(md)

    written = {"csv": str(csv_path), "md": str(md_path)}

    try:
        _write_xlsx(xlsx_path, backtest_df, summary)
        written["xlsx"] = str(xlsx_path)
    except Exception as exc:  # noqa: BLE001 - openpyxl optional
        logger.warning("Could not write xlsx (%s); skipping", exc)

    logger.info("Report written: %s", md_path)
    return written


def _render_markdown(
    summary: dict, df: pd.DataFrame, start_date: str, end_date: str
) -> str:
    alone = summary.get("value_candidates", {})
    agree = summary.get("massey_agree", {})
    neutral = summary.get("massey_neutral", {})
    conflict = summary.get("massey_conflict", {})
    insuff = summary.get("massey_insufficient_data", {})

    lines: list[str] = []
    lines.append(f"# numberFire + Massey MLB Moneyline Audit ({start_date} .. {end_date})")
    lines.append("")
    lines.append(
        "Model: `predictive_model_edge = numberFire_prob - market_implied_prob`. "
        "Massey is an **independent filter** (not averaged into the probability). "
        "Flat 1 unit risked per play. MLB moneyline only."
    )
    lines.append("")

    # 1. Executive summary
    lines.append("## 1. Executive summary")
    lines.append("")
    lines.append(f"- **numberFire alone** (value candidates): {_fmt_record(alone)}")
    lines.append(f"- **numberFire + Massey agree**: {_fmt_record(agree)}")
    lines.append(f"- **numberFire + Massey conflict**: {_fmt_record(conflict)}")
    lines.append("")
    lines.append(f"**Verdict:** {_verdict(alone, agree, conflict)}")
    lines.append("")

    # 2. Data availability
    lines.append("## 2. Data availability")
    lines.append("")
    nf_missing = summary.get("unavailable_nf_dates", [])
    odds_missing = summary.get("unavailable_odds_dates", [])
    lines.append(f"- numberFire dates missing: {len(nf_missing)}")
    if nf_missing:
        lines.append(f"  - {', '.join(nf_missing[:40])}" + (" ..." if len(nf_missing) > 40 else ""))
    lines.append(f"- odds dates missing: {len(odds_missing)}")
    if odds_missing:
        lines.append(f"  - {', '.join(odds_missing[:40])}" + (" ..." if len(odds_missing) > 40 else ""))
    if not df.empty and "nf_data_quality" in df.columns:
        lines.append("- data_quality breakdown (graded rows):")
        for dq, cnt in df["nf_data_quality"].fillna("unknown").value_counts().items():
            lines.append(f"  - {dq}: {cnt}")
    lines.append("")

    # 3. numberFire alone
    lines.append("## 3. numberFire alone")
    lines.append("")
    lines.append(f"- {_fmt_record(alone)}")
    lines.append("")

    # 4. numberFire + Massey
    lines.append("## 4. numberFire + Massey")
    lines.append("")
    lines.append(f"- **agree**: {_fmt_record(agree)}")
    lines.append(f"- **neutral**: {_fmt_record(neutral)}")
    lines.append(f"- **conflict**: {_fmt_record(conflict)}")
    lines.append(f"- **insufficient_data**: {_fmt_record(insuff)}")
    lines.append("")

    # 5. Edge buckets
    lines.append("## 5. Edge buckets")
    lines.append("")
    lines.append(f"- 0-2 pp: {_fmt_record(summary.get('edge_0_2pp', {}))}")
    lines.append(f"- 2-3 pp: {_fmt_record(summary.get('edge_2_3pp', {}))}")
    lines.append(f"- 3-5 pp: {_fmt_record(summary.get('edge_3_5pp', {}))}")
    lines.append(f"- 5 pp+: {_fmt_record(summary.get('edge_ge_5pp', {}))}")
    lines.append("")

    # 6. Favorite/dog split
    lines.append("## 6. Favorite / dog split")
    lines.append("")
    lines.append(f"- plus-money dogs: {_fmt_record(summary.get('plus_money_dogs', {}))}")
    lines.append(f"- favorites: {_fmt_record(summary.get('favorites', {}))}")
    lines.append("")

    # 7. Worst losing positions
    lines.append("## 7. Worst losing positions")
    lines.append("")
    if not df.empty and "pnl_1u" in df.columns:
        graded = df[df["value_label"] == "value_candidate"].copy()
        if not graded.empty:
            worst = graded.nsmallest(10, "pnl_1u")[
                ["date", "away_team", "home_team", "pick_team", "moneyline",
                 "predictive_model_edge", "massey_agreement", "result", "pnl_1u"]
            ]
            lines.append("Largest single-position losses (value candidates):")
            lines.append("")
            lines.append("| date | matchup | pick | ML | edge | massey | result | P/L |")
            lines.append("|---|---|---|---|---|---|---|---|")
            for r in worst.to_dict(orient="records"):
                lines.append(
                    f"| {r['date']} | {r['away_team']}@{r['home_team']} | "
                    f"{r['pick_team']} | {r['moneyline']:+d} | "
                    f"{r['predictive_model_edge'] * 100:+.1f}pp | "
                    f"{r['massey_agreement']} | {r['result']} | {r['pnl_1u']:+.2f} |"
                )
            lines.append("")
            # Repeated losing teams.
            losers = graded[graded["result"] == "L"]
            if not losers.empty:
                top_losing_teams = losers["pick_team"].value_counts().head(5)
                lines.append("Most-repeated losing pick teams:")
                for team, cnt in top_losing_teams.items():
                    lines.append(f"- {team}: {cnt} losses")
                lines.append("")
    else:
        lines.append("_No graded positions._")
        lines.append("")

    # 8. Caveats
    lines.append("## 8. Caveats")
    lines.append("")
    lines.append(
        "- Historical numberFire predictions are not cleanly exposed; rows may be "
        "manual imports or current-page observations. Check `data_quality`."
    )
    lines.append("- Missing odds dates reduce the graded sample (see section 2).")
    lines.append("- No pitcher-adjusted model is applied yet (per project scope).")
    lines.append(
        "- Massey here is a **filter**, not a full projection system, and uses only "
        "games completed before each prediction date (no leakage)."
    )
    lines.append(
        "- Entry price is the observed/opening moneyline, not the closing line."
    )
    lines.append("")
    return "\n".join(lines)


def _write_xlsx(path, df: pd.DataFrame, summary: dict) -> None:
    import openpyxl  # noqa: F401  (import check)

    flat_rows = []
    for key, val in summary.items():
        if isinstance(val, dict) and "bets" in val:
            flat_rows.append({"group": key, **val})
    summary_df = pd.DataFrame(flat_rows)

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        (summary_df if not summary_df.empty else pd.DataFrame([{"note": "no data"}])).to_excel(
            writer, sheet_name="summary", index=False
        )
        (df if not df.empty else pd.DataFrame([{"note": "no graded rows"}])).to_excel(
            writer, sheet_name="candidates", index=False
        )
