from __future__ import annotations

from typing import Any

import numpy as np

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


class TrendChannelsAnalyzer(AnalyzerPlugin):
    plugin_id = "trend_channels"
    display_name = "Trend Channels & Rail Signals"
    description = "Fits linear trend channels over 40/75/120d windows and alerts on lower/upper rail touches and breakouts."
    category = "structure"
    default_weight = 0.22
    requires_deep_data = False

    def analyze(self, ctx: AnalysisContext) -> AnalyzerResult:
        df = ctx.daily_df
        if df is None or len(df) < 25:
            return AnalyzerResult(
                plugin_id=self.plugin_id,
                display_name=self.display_name,
                category=self.category,
                score=50.0,
                bias="neutral",
                confidence=0.1,
                summary="Insufficient bars for channel fitting.",
            )

        work = df.tail(180).reset_index(drop=True)
        atr = float(compute_atr(work, 14).iloc[-1])
        last_close = float(work["close"].iloc[-1])
        if atr <= 0:
            atr = max(last_close * 0.015, 0.01)

        candidate_windows = [w for w in (40, 75, 120) if len(work) >= w]
        if not candidate_windows:
            candidate_windows = [len(work)]

        best: dict[str, Any] | None = None
        for win in candidate_windows:
            sub = work.tail(win).reset_index(drop=True)
            cand = self._fit_channel(sub, atr)
            if cand is not None:
                if best is None or cand["quality"] > best["quality"]:
                    best = cand

        if best is None:
            return AnalyzerResult(
                plugin_id=self.plugin_id,
                display_name=self.display_name,
                category=self.category,
                score=45.0,
                bias="neutral",
                confidence=0.3,
                summary="No clean trend channel detected.",
            )

        slope_type = best["slope_type"]
        upper_now = best["upper_end"]
        mid_now = best["mid_end"]
        lower_now = best["lower_end"]
        upper_prev = best["upper_prev"]
        lower_prev = best["lower_prev"]
        width = max(upper_now - lower_now, 1e-6)
        pos_pct = ((last_close - lower_now) / width) * 100.0

        last_low = float(work["low"].iloc[-1])
        last_high = float(work["high"].iloc[-1])
        prev_close = float(work["close"].iloc[-2])

        signals: list[Signal] = []
        score = 50.0
        bias = "neutral"

        # Determine directional context from slope
        if slope_type == "ascending":
            bias = "bullish"
            score += 8.0
        elif slope_type == "descending":
            bias = "bearish"
            score += 4.0

        # 1. Lower Channel Touch (key requirement!)
        near_lower = last_low <= lower_now + 0.55 * atr and last_close >= lower_now - 0.35 * atr
        bounced_lower = near_lower and last_close > lower_now and (last_close >= prev_close or pos_pct <= 28.0)

        # 2. Upper Channel Touch
        near_upper = last_high >= upper_now - 0.55 * atr and last_close <= upper_now + 0.35 * atr

        # 3. Channel Breakout Up / Breakdown
        breakout_up = last_close > upper_now + 0.15 * atr and prev_close <= upper_prev + 0.25 * atr
        breakdown = last_close < lower_now - 0.15 * atr and prev_close >= lower_prev - 0.25 * atr

        if breakout_up:
            score += 32.0
            bias = "bullish"
            signals.append(
                Signal(
                    code="CHANNEL_BREAKOUT_UP",
                    label=f"Upper Channel Breakout ({best['window']}d)",
                    direction="bullish",
                    severity="high",
                    score_impact=28.0,
                    detail=f"Closed above {best['window']}d {slope_type} channel upper rail (${upper_now:.2f}).",
                )
            )
        elif breakdown:
            score += 24.0
            bias = "bearish"
            signals.append(
                Signal(
                    code="CHANNEL_BREAKDOWN",
                    label=f"Lower Channel Breakdown ({best['window']}d)",
                    direction="bearish",
                    severity="high",
                    score_impact=22.0,
                    detail=f"Closed below {best['window']}d {slope_type} channel lower rail (${lower_now:.2f}).",
                )
            )
        elif near_lower:
            pts = 30.0 if slope_type in ("ascending", "horizontal") else 18.0
            score += pts
            if slope_type in ("ascending", "horizontal"):
                bias = "bullish"
            signals.append(
                Signal(
                    code="LOWER_CHANNEL_TOUCH",
                    label="Lower Channel Touch",
                    direction="bullish",
                    severity="high",
                    score_impact=pts,
                    detail=(
                        f"Touched lower rail (${lower_now:.2f}) of {best['window']}d {slope_type} channel"
                        + (" with active bounce." if bounced_lower else ".")
                    ),
                )
            )
        elif near_upper:
            pts = 20.0 if slope_type == "descending" else 14.0
            score += pts
            is_bearish_cap = (slope_type == "descending") or (pos_pct >= 92.0)
            if slope_type == "descending":
                bias = "bearish"
            signals.append(
                Signal(
                    code="UPPER_CHANNEL_TOUCH",
                    label="Upper Channel Touch",
                    direction="bearish" if is_bearish_cap else "neutral",
                    severity="high" if is_bearish_cap else "medium",
                    score_impact=pts,
                    detail=f"Testing upper rail (${upper_now:.2f}) of {best['window']}d {slope_type} channel.",
                )
            )
        elif slope_type == "ascending" and 15.0 <= pos_pct <= 45.0:
            score += 15.0
            signals.append(
                Signal(
                    code="ASCENDING_CHANNEL_PULLBACK",
                    label="Ascending Channel Lower Half",
                    direction="bullish",
                    severity="medium",
                    score_impact=14.0,
                    detail=f"Sitting in lower half ({pos_pct:.0f}%) of {best['window']}d ascending channel.",
                )
            )

        # Quality adjustment
        score += min(10.0, best["quality"] * 4.0)
        score = min(99.0, max(15.0, score))

        channel_color = (
            "#10b981" if slope_type == "ascending" else ("#f43f5e" if slope_type == "descending" else "#38bdf8")
        )
        overlay = ChartOverlay(
            id=f"channel-{best['window']}d",
            layer="channels",
            kind="channel",
            label=f"{slope_type.title()} Channel ({best['window']}d)",
            color=channel_color,
            data={
                "window": best["window"],
                "slopeType": slope_type,
                "startTs": best["start_ts"],
                "endTs": best["end_ts"],
                "upperStart": round(best["upper_start"], 2),
                "upperEnd": round(upper_now, 2),
                "midStart": round(best["mid_start"], 2),
                "midEnd": round(mid_now, 2),
                "lowerStart": round(best["lower_start"], 2),
                "lowerEnd": round(lower_now, 2),
            },
        )

        if signals:
            summary = f"{signals[0].label} — {slope_type.title()} ({pos_pct:.0f}% pos)"
        else:
            summary = f"{best['window']}d {slope_type.title()} channel ({pos_pct:.0f}% rail position)"

        metrics = {
            "channel_type": MetricItem(
                label="Channel Type",
                value=slope_type,
                formatted=f"{slope_type.title()} ({best['window']}d)",
                status="good" if slope_type == "ascending" else ("bad" if slope_type == "descending" else "neutral"),
            ),
            "channel_position_pct": MetricItem(
                label="Channel Position",
                value=round(pos_pct, 1),
                formatted=f"{pos_pct:.0f}%",
                status="good" if pos_pct <= 25 else ("warn" if pos_pct >= 85 else "neutral"),
            ),
            "lower_rail": MetricItem(
                label="Lower Rail",
                value=round(lower_now, 2),
                formatted=f"${lower_now:.2f}",
                status="neutral",
            ),
            "upper_rail": MetricItem(
                label="Upper Rail",
                value=round(upper_now, 2),
                formatted=f"${upper_now:.2f}",
                status="neutral",
            ),
            "rail_touches": MetricItem(
                label="Rail Touches",
                value=best["touches"],
                formatted=str(best["touches"]),
                status="good" if best["touches"] >= 4 else "neutral",
            ),
        }

        hints = TradeHints(
            channel_lower=round(lower_now, 2),
            channel_upper=round(upper_now, 2),
            channel_mid=round(mid_now, 2),
            atr=round(atr, 4),
            setup_name=signals[0].label if signals else f"{slope_type.title()} Channel",
            bias=bias,
            invalidation_level=round(lower_now - 0.5 * atr, 2) if bias == "bullish" else round(upper_now + 0.5 * atr, 2),
        )

        return AnalyzerResult(
            plugin_id=self.plugin_id,
            display_name=self.display_name,
            category=self.category,
            score=round(score, 1),
            bias=bias,
            confidence=min(0.95, 0.5 + best["containment"] * 0.4),
            summary=summary,
            signals=signals,
            metrics=metrics,
            overlays=[overlay],
            trade_hints=hints,
        )

    def _fit_channel(self, sub: pd.DataFrame, atr: float) -> dict[str, Any] | None:
        n = len(sub)
        if n < 20:
            return None
        # Fit channel over bars [0 .. n-2] so a fresh breakout on the latest bar is clearly visible outside the rails
        fit_n = n - 1
        x_fit = np.arange(fit_n, dtype=float)
        closes_fit = sub["close"].to_numpy(dtype=float)[:fit_n]
        highs_fit = sub["high"].to_numpy(dtype=float)[:fit_n]
        lows_fit = sub["low"].to_numpy(dtype=float)[:fit_n]

        slope, intercept = np.polyfit(x_fit, closes_fit, 1)
        mid_fit = slope * x_fit + intercept

        hi_res = highs_fit - mid_fit
        lo_res = lows_fit - mid_fit

        upper_off = float(np.percentile(hi_res, 96))
        lower_off = float(np.percentile(lo_res, 4))

        min_half = max(0.9 * atr, float(closes_fit[-1]) * 0.01)
        upper_off = max(upper_off, min_half)
        lower_off = min(lower_off, -min_half)

        width = upper_off - lower_off
        if width <= 0:
            return None

        # Containment & rail touches
        upper_fit = mid_fit + upper_off
        lower_fit = mid_fit + lower_off
        inside = (lows_fit >= lower_fit - 0.35 * atr) & (highs_fit <= upper_fit + 0.35 * atr)
        containment = float(np.mean(inside))

        tol = max(0.4 * atr, width * 0.1)
        upper_touches = int(np.sum(np.abs(highs_fit - upper_fit) <= tol))
        lower_touches = int(np.sum(np.abs(lows_fit - lower_fit) <= tol))
        touches = upper_touches + lower_touches

        # Dispersion ratio: tighter normalized residuals relative to price mean cleaner channel
        res_std = float(np.std(closes_fit - mid_fit))
        tightness = max(0.0, 1.0 - (res_std / max(width * 0.45, 1e-6)))

        # Normalized slope per bar
        last_fit_close = max(float(closes_fit[-1]), 1e-6)
        norm_slope = slope / last_fit_close
        if norm_slope > 0.0008:
            slope_type = "ascending"
        elif norm_slope < -0.0008:
            slope_type = "descending"
        else:
            slope_type = "horizontal"

        # Bonus for balanced touches on both rails + longer clean structures
        balance_bonus = min(upper_touches, lower_touches) * 0.35
        quality = containment * 2.0 + min(touches, 12) * 0.18 + tightness * 0.8 + balance_bonus

        mid_start = float(intercept)
        mid_prev = float(slope * (n - 2) + intercept)
        mid_end = float(slope * (n - 1) + intercept)

        return {
            "window": n,
            "slope": float(slope),
            "slope_type": slope_type,
            "start_ts": int(sub["ts"].iloc[0]),
            "end_ts": int(sub["ts"].iloc[-1]),
            "upper_start": mid_start + upper_off,
            "upper_prev": mid_prev + upper_off,
            "upper_end": mid_end + upper_off,
            "mid_start": mid_start,
            "mid_end": mid_end,
            "lower_start": mid_start + lower_off,
            "lower_prev": mid_prev + lower_off,
            "lower_end": mid_end + lower_off,
            "containment": containment,
            "touches": touches,
            "quality": quality,
        }
