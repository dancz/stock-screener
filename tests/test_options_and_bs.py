from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.black_scholes import bs_greeks, bs_price, implied_volatility
from app.core.contracts import OptionAnalysisContext
from app.plugins.options.swing_options import SwingOptionsStrategyPlugin
from tests.conftest import make_ohlcv_from_closes


def test_black_scholes_greeks_and_iv_roundtrip():
    S, K, T, r, sigma = 100.0, 100.0, 30 / 365.0, 0.0425, 0.30
    call_g = bs_greeks(S, K, T, r=r, sigma=sigma, option_type="CALL")
    put_g = bs_greeks(S, K, T, r=r, sigma=sigma, option_type="PUT")

    # ATM call delta should be slightly > 0.5, put delta slightly > -0.5, put-call delta diff ~= 1.0
    assert 0.50 < call_g["delta"] < 0.58
    assert -0.50 < put_g["delta"] < -0.42
    assert abs((call_g["delta"] - put_g["delta"]) - 1.0) < 0.02
    assert call_g["vega"] > 0 and call_g["theta"] < 0

    price = bs_price(S, K, T, r, sigma, "CALL")
    solved_iv = implied_volatility(price, S, K, T, r=r, option_type="CALL")
    assert solved_iv is not None
    assert abs(solved_iv - sigma) < 0.002


def _make_synthetic_chain(spot: float, iv: float, dte: int = 35) -> pd.DataFrame:
    T = dte / 365.0
    rows = []
    for k in [85.0, 90.0, 95.0, 100.0, 105.0, 110.0, 115.0]:
        for opt_type in ("CALL", "PUT"):
            mid = max(0.20, bs_price(spot, k, T, 0.0425, iv, opt_type))
            rows.append(
                {
                    "option_type": opt_type,
                    "strike": k,
                    "expiry": "2026-12-27",
                    "dte": dte,
                    "bid": round(mid * 0.96, 2),
                    "ask": round(mid * 1.04, 2),
                    "mid_price": round(mid, 2),
                    "iv": iv,
                    "oi": 500,
                    "volume": 120,
                    "delta": None,  # force Black-Scholes delta computation
                }
            )
    return pd.DataFrame(rows)


def test_swing_options_high_iv_bullish_and_earnings_warning():
    # Low HV (~15%) vs High IV (45%) -> Bull Put Credit Spread & Cash-Secured Put
    closes = np.full(80, 100.0) + np.sin(np.arange(80)) * 0.5
    df = make_ohlcv_from_closes(closes)
    chain = _make_synthetic_chain(spot=100.0, iv=0.45, dte=35)

    plugin = SwingOptionsStrategyPlugin()
    out = plugin.propose(
        OptionAnalysisContext(
            symbol="OPT",
            spot_price=100.0,
            daily_df=df,
            chain_df=chain,
            bias="bullish",
            days_to_earnings=10,  # crosses 35 DTE!
            nearest_support=95.0,
            nearest_resistance=108.0,
        )
    )

    assert out["iv_metrics"]["iv_regime"] == "high"
    assert out["iv_metrics"]["liquidity_ok"] is True
    names = [p.strategy_name for p in out["proposals"]]
    assert "Bull Put Credit Spread" in names
    assert all(p.earnings_warning is not None for p in out["proposals"])
    assert all(p.legs[0].delta is not None for p in out["proposals"])


def test_swing_options_neutral_iron_condor():
    closes = np.full(80, 100.0) + np.sin(np.arange(80)) * 0.5
    df = make_ohlcv_from_closes(closes)
    chain = _make_synthetic_chain(spot=100.0, iv=0.40, dte=35)

    plugin = SwingOptionsStrategyPlugin()
    out = plugin.propose(
        OptionAnalysisContext(
            symbol="RNG",
            spot_price=100.0,
            daily_df=df,
            chain_df=chain,
            bias="neutral",
            channel_lower=95.0,
            channel_upper=105.0,
        )
    )

    names = [p.strategy_name for p in out["proposals"]]
    assert any("Iron Condor" in n for n in names)


def test_swing_options_filters_stale_quotes_and_collapsed_delta():
    closes = 100.0 + 3.0 * np.sin(np.arange(80) * 0.6)  # hv20 ~ 20.2%
    df = make_ohlcv_from_closes(closes)
    chain = _make_synthetic_chain(spot=100.0, iv=0.20, dte=30)  # iv/hv ~ 1.0 -> normal regime (Long Call / Debit Spread)

    # Corrupt upstream delta to 0.0 (collapsed) across liquid rows -> must fall back to BS analytical delta
    chain["delta"] = 0.0
    # Inject a stale one-sided ITM call quote ($90 strike mid=$30.10 -> +$20.10 fake extrinsic when bid=0)
    stale_row = pd.DataFrame(
        [
            {
                "option_type": "CALL",
                "strike": 90.0,
                "expiry": "2026-12-27",
                "dte": 30,
                "bid": 0.0,
                "ask": 35.0,
                "mid_price": 30.10,
                "iv": 0.20,
                "oi": 10,
                "volume": 0,
                "delta": 0.0,
            }
        ]
    )
    dirty_chain = pd.concat(
        [chain[~((chain["strike"] == 90.0) & (chain["option_type"] == "CALL"))], stale_row],
        ignore_index=True,
    )

    out = SwingOptionsStrategyPlugin().propose(
        OptionAnalysisContext(
            symbol="CHRD",
            spot_price=100.0,
            daily_df=df,
            chain_df=dirty_chain,
            bias="bullish",
            nearest_support=95.0,
            nearest_resistance=108.0,
        )
    )

    assert len(out["proposals"]) >= 1
    for prop in out["proposals"]:
        assert all((leg.option_type, leg.strike) != ("CALL", 90.0) for leg in prop.legs)
        assert prop.pop_estimate is None or (15.0 <= prop.pop_estimate <= 88.0)
        assert all(leg.delta is not None and abs(leg.delta) > 0.05 for leg in prop.legs)
