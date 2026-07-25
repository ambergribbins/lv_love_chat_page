"""American-odds math for the numberFire value model.

Keeps the original chat workflow exact:

    market_implied_probability = american_to_implied_prob(odds)
    predictive_model_edge      = model_prob - market_implied_probability

Theoretical EV uses 1 unit risked:
    profit_if_win = odds/100          (positive American odds)
                  = 100/abs(odds)     (negative American odds)
    EV = model_prob * profit_if_win - (1 - model_prob) * 1
"""

from __future__ import annotations

import math


def _coerce_odds(odds) -> float:
    """Coerce American odds to float, rejecting invalid / zero values."""
    try:
        val = float(odds)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid American odds: {odds!r}") from exc
    if math.isnan(val) or val == 0:
        raise ValueError(f"American odds must be non-zero numeric, got {odds!r}")
    return val


def american_to_implied_prob(odds) -> float:
    """Convert American odds to the market implied (break-even) probability.

    Examples
    --------
    +100 -> 0.5
    -110 -> 0.5238...
    +150 -> 0.4
    -200 -> 0.6667...
    """
    val = _coerce_odds(odds)
    if val > 0:
        return 100.0 / (val + 100.0)
    return abs(val) / (abs(val) + 100.0)


def profit_if_win_per_unit(odds) -> float:
    """Profit for a 1-unit *risked* winning bet at the given American odds."""
    val = _coerce_odds(odds)
    if val > 0:
        return val / 100.0
    return 100.0 / abs(val)


def ev_from_prob_and_american_odds(model_prob: float, odds) -> float:
    """Theoretical EV per 1 unit risked, given the model probability.

    EV = model_prob * profit_if_win - (1 - model_prob) * 1
    """
    p = float(model_prob)
    profit_if_win = profit_if_win_per_unit(odds)
    return p * profit_if_win - (1.0 - p) * 1.0


def profit_1u_risked(odds, result: str) -> float:
    """Realized P/L for a 1-unit-risked bet.

    result is 'W'/'L'/'P' (case-insensitive; 'win'/'loss'/'push' also accepted).
    Win at +150 -> +1.50, win at -120 -> +0.8333, loss -> -1, push -> 0.
    """
    r = str(result).strip().upper()
    if r in {"P", "PUSH"}:
        return 0.0
    if r in {"W", "WIN"}:
        return profit_if_win_per_unit(odds)
    if r in {"L", "LOSS", "LOSE"}:
        return -1.0
    raise ValueError(f"Unknown result {result!r}; expected W/L/P")


def model_edge(model_prob: float, odds) -> float:
    """Predictive model edge = model_prob - market_implied_probability."""
    return float(model_prob) - american_to_implied_prob(odds)
