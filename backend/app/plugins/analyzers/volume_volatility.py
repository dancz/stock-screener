from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.contracts import (
    AnalysisContext,
    AnalyzerPlugin,
    AnalyzerResult,
    ChartOverlay,
    MetricItem,
    Signal,
    TradeHints,
)
from app.plugins.analyzers.price_action_sr import compute_atr


class VolumeVolatilityAnalyzer(AnalyzerPlugin):
    plugin_id = "volume_volatility"
    display_name = "Volume, Volatility & TTM Squeeze"
    description = "Computes RVOL, ATR%, EMA 20/50/200 stack, TTM Squeeze compression/fire, and Volume Profile POC."
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
                summary="Insufficient bars for volume/volatility analysis.",
            )

        work = df.tail(252).reset_index(drop=True)
        closes = work["close"].astype(float)
        highs = work["high"].astype(float)
        lows = work["low"].astype(float)
        vols = work["volume"].astype(float)
        ts_arr = work["ts"].to_numpy(dtype=int)

        last_close = float(closes.iloc[-1])
        atr_series = compute_atr(work, 14)
        atr = float(atr_series.iloc[-1])
        atr_pct = (atr / last_close) * 100.0 if last_close > 0 else 0.0

        # EMAs
        ema20 = closes.ewm(span=20, adjust=False).mean()
        ema50 = closes.ewm(span=50, adjust=False).mean()
        ema200 = closes.ewm(span=200, adjust=False).mean()

        e20, e50, e200 = float(ema20.iloc[-1]), float(ema50.iloc[-1]), float(ema200.iloc[-1])
        has_ema200 = len(work) >= 120
        bull_stack = last_close > e20 > e50 and (not has_ema200 or e50 > e200)
        bear_stack = last_close < e20 < e50 and (not has_ema200 or e50 < e200)

        # Relative Volume (RVOL)
        avg_vol_20 = float(vols.iloc[-21:-1].mean()) if len(vols) >= 21 else float(vols.mean())
        rvol = float(vols.iloc[-1]) / max(avg_vol_20, 1.0)

        # TTM Squeeze: BB(20, 2.0) inside KC(20, 1.5 * ATR20)
        sma20 = closes.rolling(20, min_periods=10).mean()
        std20 = closes.rolling(20, min_periods=10).std(ddof=0)
        atr20 = compute_atr(work, 20)
        bb_up = sma20 + 2.0 * std20
        bb_lo = sma20 - 2.0 * std20
        kc_up = sma20 + 1.5 * atr20
        kc_lo = sma20 - 1.5 * atr20
        squeeze_series = (bb_up < kc_up) & (bb_lo > kc_lo)

        squeeze_on = bool(squeeze_series.iloc[-1])
        was_squeezed = bool(squeeze_series.iloc[-4:-1].any()) if len(squeeze_series) >= 5 else False
        mom_val = float(closes.iloc[-1] - sma20.iloc[-1])
        squeeze_fired = (not squeeze_on) and was_squeezed

        # 24-bin Volume Profile over last 90 bars
        vp_df = work.tail(90)
        vp_low = float(vp_df["low"].min())
        vp_high = float(vp_df["high"].max())
        poc_price = last_close
        if vp_high > vp_low:
            bins = np.linspace(vp_low, vp_high, 25)
            bin_vols = np.zeros(24, dtype=float)
            for row_l, row_h, row_v in zip(vp_df["low"], vp_df["high"], vp_df["volume"]):
                mask = (bins[:-1] <= float(row_h)) & (bins[1:] >= float(row_l))
                cnt = int(np.sum(mask))
                if cnt > 0:
                    bin_vols[mask] += float(row_v) / cnt
            poc_idx = int(np.argmax(bin_vols))
            poc_price = float((bins[poc_idx] + bins[poc_idx + 1]) / 2.0)

        signals: list[Signal] = []
        score = 50.0
        bias = "neutral"

        if bull_stack:
            score += 14.0
            bias = "bullish"
            stack_label = "Bullish EMA Stack (20>50>200)" if has_ema200 else "Bullish EMA Stack (20>50)"
            stack_detail = (
                f"Price ${last_close:.2f} > EMA20 (${e20:.2f}) > EMA50 (${e50:.2f}) > EMA200 (${e200:.2f})."
                if has_ema200
                else f"Price ${last_close:.2f} > EMA20 (${e20:.2f}) > EMA50 (${e50:.2f})."
            )
            signals.append(
                Signal(
                    code="BULLISH_EMA_STACK",
                    label=stack_label,
                    direction="bullish",
                    severity="medium",
                    score_impact=14.0,
                    detail=stack_detail,
                )
            )
        elif bear_stack:
            score += 10.0
            bias = "bearish"
            stack_label = "Bearish EMA Stack (20<50<200)" if has_ema200 else "Bearish EMA Stack (20<50)"
            stack_detail = (
                f"Price ${last_close:.2f} < EMA20 (${e20:.2f}) < EMA50 (${e50:.2f}) < EMA200 (${e200:.2f})."
                if has_ema200
                else f"Price ${last_close:.2f} < EMA20 (${e20:.2f}) < EMA50 (${e50:.2f})."
            )
            signals.append(
                Signal(
                    code="BEARISH_EMA_STACK",
                    label=stack_label,
                    direction="bearish",
                    severity="medium",
                    score_impact=10.0,
                    detail=stack_detail,
                )
            )

        if rvol >= 1.5:
            pts = 16.0 if rvol >= 2.0 else 10.0
            prev_close = float(closes.iloc[-2]) if len(closes) >= 2 else float(work["open"].iloc[-1])
            day_chg_pct = ((last_close / prev_close) - 1.0) * 100.0 if prev_close > 0 else 0.0
            bar_open = float(work["open"].iloc[-1])
            bar_low = float(lows.iloc[-1])
            bar_high = float(highs.iloc[-1])
            close_pos = (last_close - bar_low) / max(bar_high - bar_low, 1e-6)

            if (day_chg_pct >= 1.0 and close_pos >= 0.45) or (last_close > bar_open and day_chg_pct >= 0.0):
                sig_dir = "bullish"
                vol_kind = "Buying Volume Spike"
            elif (day_chg_pct <= -1.0 and close_pos <= 0.55) or (last_close < bar_open and day_chg_pct <= 0.0):
                sig_dir = "bearish"
                vol_kind = "Selling Volume Spike"
            else:
                sig_dir = "neutral"
                vol_kind = "Mixed / Climax Volume Spike"

            if sig_dir != "neutral":
                score += pts
            else:
                score += 4.0
            if bias == "neutral" and sig_dir != "neutral":
                bias = sig_dir
            signals.append(
                Signal(
                    code="HIGH_RVOL",
                    label=f"{vol_kind} ({rvol:.1f}x)",
                    direction=sig_dir,
                    severity="high" if rvol >= 2.0 and sig_dir != "neutral" else "medium",
                    score_impact=pts if sig_dir != "neutral" else 4.0,
                    detail=(
                        f"Volume is {rvol:.2f}x the 20d avg on a {day_chg_pct:+.1f}% 1D move "
                        f"(closed at {close_pos * 100:.0f}% of daily range)."
                    ),
                )
            )

        if squeeze_fired:
            s_dir = "bullish" if mom_val > 0 else "bearish"
            score += 20.0
            bias = s_dir
            signals.append(
                Signal(
                    code="SQUEEZE_FIRED",
                    label=f"TTM Squeeze Fired ({'Bull' if s_dir == 'bullish' else 'Bear'})",
                    direction=s_dir,
                    severity="high",
                    score_impact=20.0,
                    detail="Volatility expansion triggered out of Bollinger/Keltner squeeze.",
                )
            )
        elif squeeze_on:
            score += 12.0
            signals.append(
                Signal(
                    code="SQUEEZE_ON",
                    label="TTM Squeeze On (Coiling)",
                    direction="neutral",
                    severity="medium",
                    score_impact=12.0,
                    detail="Bollinger Bands compressed inside Keltner Channels — breakout watch.",
                )
            )

        score = min(98.0, max(15.0, score))

        overlays: list[ChartOverlay] = [
            ChartOverlay(
                id="ema-20",
                layer="emas",
                kind="line_series",
                label="EMA 20",
                color="#38bdf8",
                data={
                    "lineWidth": 1,
                    "points": [{"time": int(t), "value": round(float(v), 2)} for t, v in zip(ts_arr, ema20)],
                },
            ),
            ChartOverlay(
                id="ema-50",
                layer="emas",
                kind="line_series",
                label="EMA 50",
                color="#f59e0b",
                data={
                    "lineWidth": 1,
                    "points": [{"time": int(t), "value": round(float(v), 2)} for t, v in zip(ts_arr, ema50)],
                },
            ),
            ChartOverlay(
                id="ema-200",
                layer="emas",
                kind="line_series",
                label="EMA 200",
                color="#a855f7",
                data={
                    "lineWidth": 2,
                    "points": [{"time": int(t), "value": round(float(v), 2)} for t, v in zip(ts_arr, ema200)],
                },
            ),
            ChartOverlay(
                id="vp-poc",
                layer="volume_profile",
                kind="horizontal_zone",
                label=f"VP POC ${poc_price:.2f}",
                color="#eab308",
                data={
                    "priceLow": round(poc_price - 0.1 * atr, 2),
                    "priceHigh": round(poc_price + 0.1 * atr, 2),
                    "mid": round(poc_price, 2),
                    "role": "poc",
                },
            ),
        ]

        summary = f"RVOL {rvol:.2f}x | ATR {atr_pct:.1f}% | POC ${poc_price:.2f}"
        if squeeze_fired:
            summary += " | Squeeze Fired"
        elif squeeze_on:
            summary += " | Squeeze Coiling"

        metrics = {
            "rvol": MetricItem(
                label="Relative Volume",
                value=round(rvol, 2),
                formatted=f"{rvol:.2f}x",
                status="good" if rvol >= 1.5 else "neutral",
            ),
            "atr_pct": MetricItem(
                label="ATR %",
                value=round(atr_pct, 2),
                formatted=f"{atr_pct:.2f}%",
                status="warn" if atr_pct >= 6.0 else "neutral",
            ),
            "ttm_squeeze": MetricItem(
                label="TTM Squeeze",
                value="FIRED" if squeeze_fired else ("ON" if squeeze_on else "OFF"),
                formatted="FIRED" if squeeze_fired else ("ON (Coiling)" if squeeze_on else "OFF"),
                status="good" if (squeeze_on or squeeze_fired) else "neutral",
            ),
            "vp_poc": MetricItem(
                label="Volume POC (90d)",
                value=round(poc_price, 2),
                formatted=f"${poc_price:.2f}",
                status="neutral",
            ),
            "ema20": MetricItem(label="EMA 20", value=round(e20, 2), formatted=f"${e20:.2f}"),
            "ema50": MetricItem(label="EMA 50", value=round(e50, 2), formatted=f"${e50:.2f}"),
            "ema200": MetricItem(label="EMA 200", value=round(e200, 2), formatted=f"${e200:.2f}"),
        }

        return AnalyzerResult(
            plugin_id=self.plugin_id,
            display_name=self.display_name,
            category=self.category,
            score=round(score, 1),
            bias=bias,
            confidence=0.8,
            summary=summary,
            signals=signals,
            metrics=metrics,
            overlays=overlays,
            trade_hints=TradeHints(atr=round(atr, 4), bias=bias),
        )
