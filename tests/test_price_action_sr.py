from __future__ import annotations

import numpy as np

from app.core.contracts import AnalysisContext
from app.plugins.analyzers.price_action_sr import PriceActionSRAnalyzer
from tests.conftest import make_ohlcv_from_closes


def test_detects_support_resistance_and_hammer_at_support():
    # Build oscillating price action bouncing repeatedly between 100 and 120
    x = np.linspace(0, 8 * np.pi, 140)
    closes = 110.0 + 9.5 * np.sin(x)
    df = make_ohlcv_from_closes(closes, spread_pct=0.01)

    # Craft a strong Bullish Hammer on the last bar touching the 100.5 support zone
    idx = len(df) - 1
    df.loc[idx, "open"] = 101.8
    df.loc[idx, "close"] = 102.2
    df.loc[idx, "high"] = 102.3
    df.loc[idx, "low"] = 99.6  # long lower rejection wick

    analyzer = PriceActionSRAnalyzer()
    res = analyzer.analyze(AnalysisContext(symbol="TEST", daily_df=df))

    codes = {s.code for s in res.signals}
    assert "BULLISH_HAMMER" in codes
    assert res.bias == "bullish"
    assert res.score > 60.0
    assert any(ov.layer == "sr_zones" and ov.kind == "horizontal_zone" for ov in res.overlays)
    assert any(ov.layer == "price_action" and ov.kind == "marker" for ov in res.overlays)
    assert res.trade_hints is not None and res.trade_hints.nearest_support is not None


def test_detects_bullish_engulfing():
    x = np.linspace(0, 6 * np.pi, 100)
    closes = 50.0 + 4.0 * np.sin(x)
    df = make_ohlcv_from_closes(closes, spread_pct=0.01)

    i0 = len(df) - 2
    i1 = len(df) - 1
    # Bearish prior bar
    df.loc[i0, "open"] = 47.4
    df.loc[i0, "close"] = 46.6
    df.loc[i0, "high"] = 47.6
    df.loc[i0, "low"] = 46.4
    # Bullish engulfing bar
    df.loc[i1, "open"] = 46.5
    df.loc[i1, "close"] = 47.9
    df.loc[i1, "high"] = 48.0
    df.loc[i1, "low"] = 46.4

    analyzer = PriceActionSRAnalyzer()
    res = analyzer.analyze(AnalysisContext(symbol="ENG", daily_df=df))
    codes = {s.code for s in res.signals}
    assert "BULLISH_ENGULFING" in codes
    assert res.bias == "bullish"
