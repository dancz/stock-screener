from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.contracts import (
    AnalysisContext,
    AnalyzerPlugin,
    AnalyzerResult,
    MetricItem,
    Signal,
)


class CorrelationRSAnalyzer(AnalyzerPlugin):
    plugin_id = "correlation_rs"
    display_name = "Index Correlation & Relative Strength"
    description = "Measures 20d/60d correlation, Beta, and Mansfield Relative Strength against the stock's benchmark index and sector ETF."
    category = "relative"
    default_weight = 0.16
    requires_deep_data = False

    def analyze(self, ctx: AnalysisContext) -> AnalyzerResult:
        df = ctx.daily_df
        bench_df = ctx.benchmark_df
        if df is None or bench_df is None or len(df) < 30 or len(bench_df) < 30:
            return AnalyzerResult(
                plugin_id=self.plugin_id,
                display_name=self.display_name,
                category=self.category,
                score=50.0,
                bias="neutral",
                confidence=0.2,
                summary=f"Benchmark ({ctx.benchmark_symbol}) series unavailable.",
            )

        merged = pd.merge(
            df[["ts", "close"]].rename(columns={"close": "s_close"}),
            bench_df[["ts", "close"]].rename(columns={"close": "b_close"}),
            on="ts",
            how="inner",
        ).sort_values("ts")

        if len(merged) < 25:
            return AnalyzerResult(
                plugin_id=self.plugin_id,
                display_name=self.display_name,
                category=self.category,
                score=50.0,
                bias="neutral",
                confidence=0.2,
                summary="Insufficient overlapping bars with benchmark.",
            )

        merged["s_ret"] = merged["s_close"].pct_change()
        merged["b_ret"] = merged["b_close"].pct_change()
        rets = merged.dropna()

        corr_20 = float(rets["s_ret"].tail(20).corr(rets["b_ret"].tail(20))) if len(rets) >= 20 else 0.0
        corr_60 = float(rets["s_ret"].tail(60).corr(rets["b_ret"].tail(60))) if len(rets) >= 30 else corr_20
        corr_20 = 0.0 if not np.isfinite(corr_20) else corr_20
        corr_60 = 0.0 if not np.isfinite(corr_60) else corr_60

        tail_60 = rets.tail(60)
        b_var = float(tail_60["b_ret"].var(ddof=1))
        beta = float(tail_60["s_ret"].cov(tail_60["b_ret"]) / b_var) if b_var > 1e-12 else 1.0
        beta = 1.0 if not np.isfinite(beta) else beta

        # Mansfield Relative Strength vs Benchmark
        rs_line = merged["s_close"] / merged["b_close"]
        sma_len = min(120, len(rs_line))
        rs_sma = float(rs_line.tail(sma_len).mean())
        mansfield_rs = ((float(rs_line.iloc[-1]) / rs_sma) - 1.0) * 100.0 if rs_sma > 0 else 0.0

        look_20 = min(20, len(merged) - 1)
        s_ret_20 = (float(merged["s_close"].iloc[-1]) / float(merged["s_close"].iloc[-1 - look_20]) - 1.0) * 100.0
        b_ret_20 = (float(merged["b_close"].iloc[-1]) / float(merged["b_close"].iloc[-1 - look_20]) - 1.0) * 100.0
        alpha_20d = s_ret_20 - b_ret_20

        rs_60_high = float(rs_line.tail(min(60, len(rs_line))).max())
        at_rs_high = float(rs_line.iloc[-1]) >= rs_60_high * 0.992

        # Sector ETF correlation & RS
        sector_corr = None
        sector_alpha_20d = None
        if ctx.sector_df is not None and len(ctx.sector_df) >= 25 and ctx.sector_etf_symbol != ctx.benchmark_symbol:
            sec_m = pd.merge(
                df[["ts", "close"]].rename(columns={"close": "s_close"}),
                ctx.sector_df[["ts", "close"]].rename(columns={"close": "e_close"}),
                on="ts",
                how="inner",
            ).sort_values("ts")
            if len(sec_m) >= 25:
                sec_rets = sec_m.assign(s=sec_m["s_close"].pct_change(), e=sec_m["e_close"].pct_change()).dropna()
                sc = float(sec_rets["s"].tail(60).corr(sec_rets["e"].tail(60)))
                if np.isfinite(sc):
                    sector_corr = sc
                l20 = min(20, len(sec_m) - 1)
                e_ret_20 = (float(sec_m["e_close"].iloc[-1]) / float(sec_m["e_close"].iloc[-1 - l20]) - 1.0) * 100.0
                sector_alpha_20d = s_ret_20 - e_ret_20

        signals: list[Signal] = []
        score = 52.0
        bias = "neutral"

        if mansfield_rs >= 3.0 and alpha_20d >= 2.0:
            score += 22.0
            bias = "bullish"
            signals.append(
                Signal(
                    code="RS_LEADER",
                    label=f"RS Leader vs {ctx.benchmark_symbol}",
                    direction="bullish",
                    severity="high",
                    score_impact=20.0,
                    detail=f"Mansfield RS +{mansfield_rs:.1f}%, outperforming {ctx.benchmark_symbol} by +{alpha_20d:.1f}% (20d).",
                )
            )
        elif mansfield_rs <= -4.0 and alpha_20d <= -3.0:
            score += 6.0
            bias = "bearish"
            signals.append(
                Signal(
                    code="RS_LAGGARD",
                    label=f"Lagging {ctx.benchmark_symbol}",
                    direction="bearish",
                    severity="medium",
                    score_impact=4.0,
                    detail=f"Underperforming {ctx.benchmark_symbol} by {alpha_20d:.1f}% over 20d (RS {mansfield_rs:.1f}%).",
                )
            )

        if at_rs_high and mansfield_rs > 0:
            score += 12.0
            if bias == "neutral":
                bias = "bullish"
            signals.append(
                Signal(
                    code="RS_BREAKOUT",
                    label="60d Relative Strength High",
                    direction="bullish",
                    severity="medium",
                    score_impact=12.0,
                    detail=f"Stock/Index ratio at 60-day peak vs {ctx.benchmark_symbol}.",
                )
            )

        if corr_60 >= 0.72:
            score += 6.0
            signals.append(
                Signal(
                    code="HIGH_INDEX_CORR",
                    label=f"High {ctx.benchmark_symbol} Correlation ({corr_60:.2f})",
                    direction="neutral",
                    severity="low",
                    score_impact=6.0,
                    detail=f"Moves closely with {ctx.benchmark_symbol} (60d r={corr_60:.2f}, Beta={beta:.2f}).",
                )
            )
        elif abs(corr_20) < 0.25 and abs(alpha_20d) >= 4.0:
            signals.append(
                Signal(
                    code="IDIOSYNCRATIC_MOVER",
                    label="Low Index Correlation / Alpha",
                    direction="bullish" if alpha_20d > 0 else "bearish",
                    severity="low",
                    score_impact=5.0,
                    detail=f"20d correlation with {ctx.benchmark_symbol} dropped to {corr_20:.2f} (alpha {alpha_20d:+.1f}%).",
                )
            )

        if sector_alpha_20d is not None and sector_alpha_20d >= 3.0:
            score += 6.0

        score = min(98.0, max(18.0, score))
        summary = (
            f"Corr({ctx.benchmark_symbol}) {corr_60:.2f} | Beta {beta:.2f} | Mansfield RS {mansfield_rs:+.1f}% (20d {alpha_20d:+.1f}%)"
        )

        metrics = {
            "benchmark_symbol": MetricItem(label="Benchmark Index", value=ctx.benchmark_symbol, formatted=ctx.benchmark_symbol),
            "sector_etf": MetricItem(label="Sector ETF", value=ctx.sector_etf_symbol, formatted=ctx.sector_etf_symbol),
            "corr_20d": MetricItem(label="Corr (20d)", value=round(corr_20, 2), formatted=f"{corr_20:.2f}"),
            "corr_60d": MetricItem(label="Corr (60d)", value=round(corr_60, 2), formatted=f"{corr_60:.2f}"),
            "beta_60d": MetricItem(label="Beta (60d)", value=round(beta, 2), formatted=f"{beta:.2f}"),
            "mansfield_rs": MetricItem(
                label="Mansfield RS",
                value=round(mansfield_rs, 2),
                formatted=f"{mansfield_rs:+.1f}%",
                status="good" if mansfield_rs > 2 else ("bad" if mansfield_rs < -3 else "neutral"),
            ),
            "alpha_20d_pct": MetricItem(
                label="20d Outperf. vs Index",
                value=round(alpha_20d, 2),
                formatted=f"{alpha_20d:+.1f}%",
                status="good" if alpha_20d > 1.5 else ("bad" if alpha_20d < -2 else "neutral"),
            ),
            "sector_corr_60d": MetricItem(
                label=f"Corr ({ctx.sector_etf_symbol})",
                value=round(sector_corr, 2) if sector_corr is not None else None,
                formatted=f"{sector_corr:.2f}" if sector_corr is not None else "N/A",
            ),
            "sector_alpha_20d_pct": MetricItem(
                label=f"20d Outperf. vs {ctx.sector_etf_symbol}",
                value=round(sector_alpha_20d, 2) if sector_alpha_20d is not None else None,
                formatted=f"{sector_alpha_20d:+.1f}%" if sector_alpha_20d is not None else "N/A",
                status="good" if (sector_alpha_20d or 0) > 1.5 else "neutral",
            ),
        }

        return AnalyzerResult(
            plugin_id=self.plugin_id,
            display_name=self.display_name,
            category=self.category,
            score=round(score, 1),
            bias=bias,
            confidence=0.85,
            summary=summary,
            signals=signals,
            metrics=metrics,
        )
