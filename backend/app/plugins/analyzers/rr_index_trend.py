from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.signal import argrelextrema

from app.core.contracts import (
    AnalysisContext,
    AnalyzerPlugin,
    AnalyzerResult,
    MetricItem,
    Signal,
    TradeHints,
)
from app.plugins.analyzers.price_action_sr import compute_atr


def _trend_state(df: pd.DataFrame | None) -> str:
    if df is None or len(df) < 30:
        return "neutral"
    closes = df["close"].astype(float)
    last = float(closes.iloc[-1])
    ema20 = float(closes.ewm(span=20, adjust=False).mean().iloc[-1])
    ema50 = float(closes.ewm(span=50, adjust=False).mean().iloc[-1])
    ret20 = (last / float(closes.iloc[-20]) - 1.0) if len(closes) >= 20 else 0.0
    if last > ema20 and ema20 >= ema50 * 0.995 and ret20 > 0:
        return "bullish"
    if last < ema20 and ema20 <= ema50 * 1.005 and ret20 < 0:
        return "bearish"
    return "neutral"


class RRIndexTrendAnalyzer(AnalyzerPlugin):
    plugin_id = "rr_index_trend"
    display_name = "S/R Risk/Reward & Index Trend"
    description = (
        "Evaluates asymmetric Long and Short Reward-to-Risk from Support/Resistance distances "
        "and alignment with the benchmark index trend."
    )
    category = "technical"
    default_weight = 0.16
    requires_deep_data = False

    def analyze(self, ctx: AnalysisContext) -> AnalyzerResult:
        df = ctx.daily_df
        if df is None or len(df) < 30:
            return AnalyzerResult(
                plugin_id=self.plugin_id,
                display_name=self.display_name,
                category=self.category,
                score=50.0,
                bias="neutral",
                confidence=0.1,
                summary="Insufficient bars for S/R Reward/Risk analysis.",
            )

        work = df.tail(400).reset_index(drop=True)
        highs = work["high"].to_numpy(dtype=float)
        lows = work["low"].to_numpy(dtype=float)
        closes = work["close"].to_numpy(dtype=float)

        last_close = float(closes[-1])
        atr = float(compute_atr(work, 14).iloc[-1])
        atr = max(atr, last_close * 0.012, 0.05)

        order = 5 if len(work) >= 40 else 3
        hi_idx = argrelextrema(highs, np.greater_equal, order=order)[0]
        lo_idx = argrelextrema(lows, np.less_equal, order=order)[0]

        sup_levels = [float(lows[i]) for i in lo_idx if float(lows[i]) <= last_close * 0.995]
        res_levels = [float(highs[i]) for i in hi_idx if float(highs[i]) >= last_close * 1.005]

        # Nearest meaningful support & major resistance (at least ~0.8 ATR away for target)
        sup_candidates = sorted([s for s in sup_levels if last_close - s >= 0.25 * atr], reverse=True)
        res_candidates = sorted([r for r in res_levels if r - last_close >= 0.25 * atr])

        nearest_sup = sup_candidates[0] if sup_candidates else max(0.01, float(np.min(lows[-30:])))
        nearest_res = res_candidates[0] if res_candidates else float(np.max(highs[-30:]))

        # Major swing targets (further structural zone when immediate one is tiny)
        tp_long = next((r for r in res_candidates if r >= last_close + 1.4 * atr), nearest_res)
        tp_short = next((s for s in sup_candidates if s <= last_close - 1.4 * atr), nearest_sup)

        # Evaluate LONG vs SHORT Reward/Risk
        long_stop = max(0.01, nearest_sup - 0.35 * atr)
        long_risk = max(last_close - long_stop, 0.45 * atr)
        long_reward = max(tp_long - last_close, 0.0)
        long_rr = long_reward / long_risk
        long_gain_pct = (tp_long / last_close - 1.0) * 100.0 if last_close > 0 else 0.0
        long_loss_pct = (1.0 - long_stop / last_close) * 100.0 if last_close > 0 else 0.0

        short_stop = nearest_res + 0.35 * atr
        short_risk = max(short_stop - last_close, 0.45 * atr)
        short_reward = max(last_close - tp_short, 0.0)
        short_rr = short_reward / short_risk
        short_gain_pct = (1.0 - tp_short / last_close) * 100.0 if last_close > 0 else 0.0
        short_loss_pct = (short_stop / last_close - 1.0) * 100.0 if last_close > 0 else 0.0

        stock_trend = _trend_state(work)
        index_trend = _trend_state(ctx.benchmark_df)

        # Index correlation factor
        corr_60 = 0.0
        if ctx.benchmark_df is not None and len(ctx.benchmark_df) >= 30:
            merged = pd.merge(
                work[["ts", "close"]].rename(columns={"close": "s"}),
                ctx.benchmark_df[["ts", "close"]].rename(columns={"close": "b"}),
                on="ts",
                how="inner",
            )
            if len(merged) >= 25:
                c_val = merged["s"].pct_change().tail(60).corr(merged["b"].pct_change().tail(60))
                if np.isfinite(c_val):
                    corr_60 = float(c_val)

        # Score both sides fairly (supporting Longs in uptrends AND Shorts in downtrends!)
        long_edge = long_rr + (0.6 if stock_trend == "bullish" else 0.0) + (0.6 if index_trend == "bullish" else 0.0)
        short_edge = short_rr + (0.6 if stock_trend == "bearish" else 0.0) + (0.6 if index_trend == "bearish" else 0.0)

        if short_edge > long_edge + 0.15 and short_rr >= 1.3:
            best_dir = "SHORT"
            bias = "bearish"
            best_rr = short_rr
            gain_pct = short_gain_pct
            loss_pct = short_loss_pct
            index_match = index_trend == "bearish"
            trend_match = stock_trend == "bearish"
        else:
            best_dir = "LONG"
            bias = "bullish" if long_rr >= 1.3 or stock_trend == "bullish" else "neutral"
            best_rr = long_rr
            gain_pct = long_gain_pct
            loss_pct = long_loss_pct
            index_match = index_trend == "bullish"
            trend_match = stock_trend == "bullish"

        signals: list[Signal] = []
        score = 44.0

        # Reward-to-Risk asymmetry bonus
        if best_rr >= 3.0:
            score += 26.0
        elif best_rr >= 2.0:
            score += 20.0
        elif best_rr >= 1.5:
            score += 12.0
        elif best_rr < 1.0:
            score -= 10.0

        if best_rr >= 2.0:
            sig_code = "HIGH_ASYMMETRIC_RR_SHORT" if best_dir == "SHORT" else "HIGH_ASYMMETRIC_RR_LONG"
            signals.append(
                Signal(
                    code=sig_code,
                    label=f"{best_dir} High R:R {best_rr:.1f}x (+{gain_pct:.1f}% / -{loss_pct:.1f}%)",
                    direction="bearish" if best_dir == "SHORT" else "bullish",
                    severity="high",
                    score_impact=22.0,
                    detail=f"Target distance +{gain_pct:.1f}% vs S/R invalidation risk -{loss_pct:.1f}%.",
                )
            )

        if loss_pct <= 4.5 and best_rr >= 1.6:
            score += 8.0
            signals.append(
                Signal(
                    code="TIGHT_SR_ENTRY",
                    label=f"Tight S/R Invalidation (-{loss_pct:.1f}%)",
                    direction="bearish" if best_dir == "SHORT" else "bullish",
                    severity="medium",
                    score_impact=8.0,
                    detail="Close to structural invalidation zone with asymmetric upside/downside.",
                )
            )

        if trend_match:
            score += 8.0
        if index_match:
            score += 10.0
            if corr_60 >= 0.5:
                score += 4.0
            signals.append(
                Signal(
                    code="INDEX_TREND_ALIGNED",
                    label=f"Aligned w/ {ctx.benchmark_symbol} {index_trend.title()} Trend",
                    direction="bearish" if best_dir == "SHORT" else "bullish",
                    severity="medium",
                    score_impact=10.0,
                    detail=f"{best_dir} direction aligns with {ctx.benchmark_symbol} trend (60d corr {corr_60:.2f}).",
                )
            )

        score = min(98.0, max(15.0, score))
        dist_sup_pct = ((last_close - nearest_sup) / last_close) * 100.0 if last_close > 0 else 0.0
        dist_res_pct = ((nearest_res - last_close) / last_close) * 100.0 if last_close > 0 else 0.0

        summary = (
            f"{best_dir} R:R {best_rr:.2f}x (+{gain_pct:.1f}% / -{loss_pct:.1f}%) | "
            f"{ctx.benchmark_symbol}: {index_trend.title()}"
        )

        metrics = {
            "best_direction": MetricItem(
                label="Best Asymmetric Side",
                value=best_dir,
                formatted=best_dir,
                status="good" if best_rr >= 2.0 else "neutral",
            ),
            "asymmetric_rr": MetricItem(
                label="S/R Reward : Risk",
                value=round(best_rr, 2),
                formatted=f"{best_rr:.2f}x",
                status="good" if best_rr >= 2.0 else ("bad" if best_rr < 1.1 else "neutral"),
            ),
            "potential_gain_pct": MetricItem(
                label="Potential Gain",
                value=round(gain_pct, 2),
                formatted=f"+{gain_pct:.1f}%",
                status="good" if gain_pct >= 8.0 else "neutral",
            ),
            "potential_loss_pct": MetricItem(
                label="Risk to S/R Stop",
                value=round(loss_pct, 2),
                formatted=f"-{loss_pct:.1f}%",
                status="good" if loss_pct <= 4.5 else "neutral",
            ),
            "index_trend_match": MetricItem(
                label=f"{ctx.benchmark_symbol} Trend Match",
                value=index_match,
                formatted="Aligned" if index_match else f"Counter ({index_trend})",
                status="good" if index_match else "warn",
            ),
            "dist_to_support_pct": MetricItem(
                label="Dist. to Support",
                value=round(dist_sup_pct, 2),
                formatted=f"{dist_sup_pct:.1f}%",
            ),
            "dist_to_resistance_pct": MetricItem(
                label="Dist. to Resistance",
                value=round(dist_res_pct, 2),
                formatted=f"{dist_res_pct:.1f}%",
            ),
        }

        hints = TradeHints(
            nearest_support=round(nearest_sup, 2),
            nearest_resistance=round(nearest_res, 2),
            atr=round(atr, 4),
            setup_name=signals[0].label if signals else f"{best_dir} S/R Asymmetry",
            bias=bias,
        )

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
            trade_hints=hints,
        )
