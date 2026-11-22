from __future__ import annotations

import numpy as np

from app.config import get_default_runtime_settings
from app.core.contracts import AnalysisContext
from app.core.registry import PluginRegistry
from app.core.scoring import compute_scores_and_summary
from app.core.trade_planner import build_trade_plan
from tests.conftest import make_ohlcv_from_closes


def test_plugin_autodiscovery_and_scoring_and_trade_plan():
    reg = PluginRegistry()
    reg.discover_all(force=True)

    assert "price_action_sr" in reg.analyzers
    assert "trend_channels" in reg.analyzers
    assert "correlation_rs" in reg.analyzers
    assert "volume_volatility" in reg.analyzers
    assert "fundamental_health" in reg.analyzers
    assert "events_earnings" in reg.analyzers
    assert "insider_activity" in reg.analyzers
    assert "swing_options" in reg.options

    n = 100
    t = np.arange(n, dtype=float)
    bench_closes = 400.0 + 0.4 * t
    stock_closes = 100.0 + 0.3 * t + 2.5 * np.sin(2.0 * np.pi * t / 18.0)

    ctx = AnalysisContext(
        symbol="AAPL",
        daily_df=make_ohlcv_from_closes(stock_closes),
        benchmark_symbol="QQQ",
        benchmark_df=make_ohlcv_from_closes(bench_closes),
        sector_etf_symbol="XLK",
        sector_df=make_ohlcv_from_closes(bench_closes),
        fundamentals={"operating_margin": 28.0, "roe": 45.0, "revenue_growth": 16.0, "pe_forward": 24.0, "free_cash_flow": 5e10, "market_cap": 1e12},
        events={"days_to_earnings": 30, "next_earnings_date": "2026-12-22"},
        insiders=[{"tag": "cluster_buy", "value_usd": 450000}],
    )

    results = {pid: plugin.analyze(ctx) for pid, plugin in reg.analyzers.items()}
    runtime = get_default_runtime_settings()
    agg = compute_scores_and_summary(results, runtime)

    assert 0.0 <= agg["composite_score"] <= 100.0
    assert set(runtime["presets"].keys()).issubset(set(agg["preset_scores"].keys()))

    spot = float(ctx.daily_df["close"].iloc[-1])
    plan, overlay = build_trade_plan(spot, results, agg["bias"])
    assert plan.entry_low <= plan.entry_high
    assert plan.risk_reward > 0
    if plan.direction != "SHORT":
        assert plan.stop_loss < plan.entry_low
        assert plan.tp1 > plan.entry_high
        assert plan.tp2 > plan.tp1
    assert overlay.layer == "trade_plan"
    assert overlay.kind == "price_box"
