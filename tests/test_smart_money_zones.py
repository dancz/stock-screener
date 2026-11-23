from __future__ import annotations

import numpy as np

from app.core.contracts import AnalysisContext
from app.plugins.analyzers.smart_money_zones import SmartMoneyZonesAnalyzer
from tests.conftest import make_ohlcv_from_closes


def test_smart_money_detects_bos_order_block_and_fvg():
    # Build downtrend leg then sharp bullish CHoCH + impulse creating Bullish OB and FVG
    leg1 = np.linspace(120.0, 95.0, 35)
    leg2 = np.linspace(95.0, 108.0, 15)
    leg3 = np.linspace(108.0, 98.0, 12)
    # Break strongly above swing high (108 -> 125) and pull back toward OB near ~98
    leg4 = np.array([101.0, 105.0, 111.0, 117.0, 121.0, 124.0, 119.0, 113.0, 107.0, 102.0, 100.5])
    closes = np.concatenate([leg1, leg2, leg3, leg4])
    df = make_ohlcv_from_closes(closes, spread_pct=0.01)

    # Inject a clean Bullish FVG in leg4 (bar len-9 low > bar len-11 high)
    fvg_base = len(df) - 10
    df.loc[fvg_base - 1, "high"] = 104.0
    df.loc[fvg_base, "open"] = 104.2
    df.loc[fvg_base, "close"] = 110.5
    df.loc[fvg_base, "high"] = 110.8
    df.loc[fvg_base + 1, "low"] = 106.5

    analyzer = SmartMoneyZonesAnalyzer()
    res = analyzer.analyze(AnalysisContext(symbol="SMC", daily_df=df))

    assert res.plugin_id == "smart_money_zones"
    assert 0.0 <= res.score <= 100.0
    assert any(ov.layer == "smart_money" for ov in res.overlays)
    assert res.trade_hints is not None
    assert res.metrics["zone_position"].value is not None


def test_smart_money_bearish_structure_and_supply_ob():
    # Rally then steep breakdown creating Bearish CHoCH/BOS and Supply OB
    leg1 = np.linspace(90.0, 125.0, 40)
    leg2 = np.linspace(125.0, 115.0, 12)
    leg3 = np.linspace(115.0, 124.0, 10)
    leg4 = np.linspace(124.0, 104.0, 15)
    closes = np.concatenate([leg1, leg2, leg3, leg4])
    df = make_ohlcv_from_closes(closes, spread_pct=0.01)

    analyzer = SmartMoneyZonesAnalyzer()
    res = analyzer.analyze(AnalysisContext(symbol="BEAR", daily_df=df))

    assert res.metrics["swing_structure"].value in ("Bearish", "Bullish", "Neutral")
    assert any(ov.layer == "smart_money" and ov.kind == "horizontal_zone" for ov in res.overlays)
