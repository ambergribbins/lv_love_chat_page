import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from odds_math import (  # noqa: E402
    american_to_implied_prob,
    ev_from_prob_and_american_odds,
    model_edge,
    profit_1u_risked,
)


def test_plus_100_implied():
    assert math.isclose(american_to_implied_prob(100), 0.5, rel_tol=1e-9)


def test_minus_110_implied():
    assert math.isclose(american_to_implied_prob(-110), 110 / 210, rel_tol=1e-9)


def test_plus_150_and_minus_200():
    assert math.isclose(american_to_implied_prob(150), 0.4, rel_tol=1e-9)
    assert math.isclose(american_to_implied_prob(-200), 200 / 300, rel_tol=1e-9)


def test_ev_positive_odds():
    # +150, model prob 0.5 -> 0.5*1.5 - 0.5*1 = 0.25
    assert math.isclose(ev_from_prob_and_american_odds(0.5, 150), 0.25, rel_tol=1e-9)


def test_ev_negative_odds():
    # -120, model prob 0.6 -> 0.6*(100/120) - 0.4 = 0.5 - 0.4 = 0.1
    assert math.isclose(
        ev_from_prob_and_american_odds(0.6, -120), 0.1, rel_tol=1e-9
    )


def test_profit_win_plus_150():
    assert math.isclose(profit_1u_risked(150, "W"), 1.5, rel_tol=1e-9)


def test_profit_win_minus_120():
    assert math.isclose(profit_1u_risked(-120, "W"), 100 / 120, rel_tol=1e-9)


def test_profit_loss_and_push():
    assert profit_1u_risked(150, "L") == -1.0
    assert profit_1u_risked(-120, "P") == 0.0


def test_model_edge():
    # nf prob 0.55 at +100 (implied 0.5) -> edge 0.05
    assert math.isclose(model_edge(0.55, 100), 0.05, rel_tol=1e-9)
