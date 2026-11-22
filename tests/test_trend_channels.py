from __future__ import annotations

import numpy as np

from app.core.contracts import AnalysisContext
from app.plugins.analyzers.trend_channels import TrendChannelsAnalyzer
from tests.conftest import make_ohlcv_from_closes


def test_ascending_channel_lower_rail_touch():
    # Clean ascending channel: base trend 100 -> 130 over 80 bars + crisp wave +/- 3
    n = 80
    t = np.arange(n, dtype=float)
    trend = 100.0 + 0.35 * t
    wave = 3.0 * np.sin(2.0 * np.pi * t / 18.0)
    closes = trend + wave
    df = make_ohlcv_from_closes(closes, spread_pct=0.008)

    # Pull last bar down to touch lower rail and bounce slightly
    expected_lower_end = (100.0 + 0.35 * (n - 1)) - 3.2
    df.loc[n - 1, "low"] = expected_lower_end - 0.1
    df.loc[n - 1, "open"] = expected_lower_end + 0.3
    df.loc[n - 1, "close"] = expected_lower_end + 0.8
    df.loc[n - 1, "high"] = expected_lower_end + 1.1

    analyzer = TrendChannelsAnalyzer()
    res = analyzer.analyze(AnalysisContext(symbol="CHAN", daily_df=df))

    codes = {s.code for s in res.signals}
    assert "LOWER_CHANNEL_TOUCH" in codes
    assert res.bias == "bullish"
    assert res.score >= 70.0
    assert res.metrics["channel_type"].value == "ascending"
    assert any(ov.kind == "channel" and ov.layer == "channels" for ov in res.overlays)


def test_channel_breakout_up():
    n = 80
    t = np.arange(n, dtype=float)
    closes = 100.0 + 2.5 * np.sin(2.0 * np.pi * t / 16.0)
    df = make_ohlcv_from_closes(closes, spread_pct=0.006)

    # Keep penultimate bar inside channel, break out strongly above upper rail on last bar
    df.loc[n - 2, "close"] = 102.0
    df.loc[n - 1, "open"] = 102.1
    df.loc[n - 1, "close"] = 106.5
    df.loc[n - 1, "high"] = 106.8
    df.loc[n - 1, "low"] = 102.0

    analyzer = TrendChannelsAnalyzer()
    res = analyzer.analyze(AnalysisContext(symbol="BRK", daily_df=df))

    codes = {s.code for s in res.signals}
    assert "CHANNEL_BREAKOUT_UP" in codes
    assert res.bias == "bullish"
    assert res.score >= 75.0
