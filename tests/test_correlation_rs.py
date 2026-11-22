from __future__ import annotations

import numpy as np

from app.core.contracts import AnalysisContext
from app.plugins.analyzers.correlation_rs import CorrelationRSAnalyzer
from tests.conftest import make_ohlcv_from_closes


def test_correlation_beta_and_mansfield_rs():
    rng = np.random.default_rng(42)
    n = 130
    bench_rets = rng.normal(0.0005, 0.01, size=n)
    bench_closes = 400.0 * np.cumprod(1.0 + bench_rets)

    # Stock has Beta ~1.4 plus strong positive outperformance alpha (+0.2% per day)
    stock_rets = 1.4 * bench_rets + 0.002 + rng.normal(0.0, 0.003, size=n)
    stock_closes = 100.0 * np.cumprod(1.0 + stock_rets)

    bench_df = make_ohlcv_from_closes(bench_closes)
    stock_df = make_ohlcv_from_closes(stock_closes)

    analyzer = CorrelationRSAnalyzer()
    res = analyzer.analyze(
        AnalysisContext(
            symbol="LEAD",
            daily_df=stock_df,
            benchmark_symbol="QQQ",
            benchmark_df=bench_df,
            sector_etf_symbol="XLK",
            sector_df=bench_df,
        )
    )

    corr_60 = float(res.metrics["corr_60d"].value)
    beta_60 = float(res.metrics["beta_60d"].value)
    mansfield = float(res.metrics["mansfield_rs"].value)

    assert corr_60 > 0.80
    assert 1.1 <= beta_60 <= 1.7
    assert mansfield > 3.0
    assert res.bias == "bullish"
    assert any(s.code == "RS_LEADER" for s in res.signals)
