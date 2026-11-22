from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from scipy.signal import argrelextrema

from app.core.contracts import (
    AnalysisContext,
    AnalyzerPlugin,
    AnalyzerResult,
    ChartOverlay,
    MetricItem,
    Signal,
    TradeHints,
)


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(window=period, min_periods=1).mean()


class PriceActionSRAnalyzer(AnalyzerPlugin):
    plugin_id = "price_action_sr"
    display_name = "Price Action & S/R"
    description = "Detects clustered Support/Resistance zones, polarity flips, and swing candlestick triggers (Hammer, Engulfing, Inside Bar, Break & Retest)."
    category = "technical"
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
                summary="Insufficient price history for S/R analysis.",
            )

        work = df.tail(252).reset_index(drop=True)
        highs = work["high"].to_numpy(dtype=float)
        lows = work["low"].to_numpy(dtype=float)
        opens = work["open"].to_numpy(dtype=float)
        closes = work["close"].to_numpy(dtype=float)
        vols = work["volume"].to_numpy(dtype=float)
        timestamps = work["ts"].to_numpy(dtype=int)

        atr_series = compute_atr(work, 14)
        atr = float(atr_series.iloc[-1])
        last_close = float(closes[-1])
        if atr <= 0:
            atr = max(last_close * 0.015, 0.01)

        # 1. Detect swing pivots
        order = 5 if len(work) >= 40 else 3
        hi_idx = argrelextrema(highs, np.greater_equal, order=order)[0]
        lo_idx = argrelextrema(lows, np.less_equal, order=order)[0]

        pivots: list[dict[str, Any]] = []
        avg_vol = float(np.mean(vols[-60:])) if len(vols) >= 20 else float(np.mean(vols))
        avg_vol = max(avg_vol, 1.0)

        for idx in hi_idx:
            pivots.append({"price": float(highs[idx]), "idx": int(idx), "kind": "res", "vol_mult": float(vols[idx] / avg_vol)})
        for idx in lo_idx:
            pivots.append({"price": float(lows[idx]), "idx": int(idx), "kind": "sup", "vol_mult": float(vols[idx] / avg_vol)})

        # Cluster pivots into S/R zones within 0.75 * ATR
        tol = max(0.75 * atr, last_close * 0.012)
        pivots.sort(key=lambda p: p["price"])
        clusters: list[list[dict[str, Any]]] = []
        for p in pivots:
            if not clusters or (p["price"] - np.mean([x["price"] for x in clusters[-1]])) > tol:
                clusters.append([p])
            else:
                clusters[-1].append(p)

        zones: list[dict[str, Any]] = []
        n_bars = len(work)
        for grp in clusters:
            prices = [g["price"] for g in grp]
            mid = float(np.mean(prices))
            low_b = min(prices) - 0.18 * atr
            high_b = max(prices) + 0.18 * atr

            # Count historical bar touches near the zone
            touch_mask = (lows <= high_b + 0.2 * atr) & (highs >= low_b - 0.2 * atr)
            touches = int(np.sum(touch_mask))
            recency_bonus = max((g["idx"] / n_bars) for g in grp)
            vol_bonus = min(1.5, float(np.mean([g["vol_mult"] for g in grp])))
            kinds = {g["kind"] for g in grp}
            polarity_flip = len(kinds) > 1
            strength = len(grp) * 2.0 + touches * 0.35 + recency_bonus * 2.0 + vol_bonus + (2.0 if polarity_flip else 0.0)

            zones.append(
                {
                    "mid": round(mid, 2),
                    "low": round(low_b, 2),
                    "high": round(high_b, 2),
                    "touches": max(len(grp), touches),
                    "strength": round(strength, 2),
                    "polarity_flip": polarity_flip,
                    "role": "support" if mid <= last_close * 1.005 else "resistance",
                }
            )

        supports = sorted([z for z in zones if z["role"] == "support"], key=lambda z: last_close - z["mid"])
        resistances = sorted([z for z in zones if z["role"] == "resistance"], key=lambda z: z["mid"] - last_close)

        # Keep strongest nearby zones (top 3 each)
        top_supports = sorted(supports[:6], key=lambda z: z["strength"], reverse=True)[:3]
        top_supports.sort(key=lambda z: z["mid"], reverse=True)
        top_resistances = sorted(resistances[:6], key=lambda z: z["strength"], reverse=True)[:3]
        top_resistances.sort(key=lambda z: z["mid"])

        nearest_sup = top_supports[0] if top_supports else None
        nearest_res = top_resistances[0] if top_resistances else None

        dist_sup_atr = ((last_close - nearest_sup["mid"]) / atr) if nearest_sup else 99.0
        dist_res_atr = ((nearest_res["mid"] - last_close) / atr) if nearest_res else 99.0

        # 2. Candlestick & Price Action Triggers on last 2 bars
        signals: list[Signal] = []
        overlays: list[ChartOverlay] = []

        for i, z in enumerate(top_supports):
            overlays.append(
                ChartOverlay(
                    id=f"sr-sup-{i}",
                    layer="sr_zones",
                    kind="horizontal_zone",
                    label=f"Support ${z['mid']:.2f} ({z['touches']}x)",
                    color="#10b981",
                    data={"priceLow": z["low"], "priceHigh": z["high"], "mid": z["mid"], "role": "support"},
                )
            )
        for i, z in enumerate(top_resistances):
            overlays.append(
                ChartOverlay(
                    id=f"sr-res-{i}",
                    layer="sr_zones",
                    kind="horizontal_zone",
                    label=f"Resistance ${z['mid']:.2f} ({z['touches']}x)",
                    color="#f43f5e",
                    data={"priceLow": z["low"], "priceHigh": z["high"], "mid": z["mid"], "role": "resistance"},
                )
            )

        score = 50.0
        bullish_pts = 0.0
        bearish_pts = 0.0

        # Proximity to support/resistance
        if nearest_sup and dist_sup_atr <= 1.1:
            pts = 16.0 if dist_sup_atr <= 0.6 else 10.0
            bullish_pts += pts
            signals.append(
                Signal(
                    code="AT_SUPPORT_ZONE",
                    label=f"Testing Support ${nearest_sup['mid']:.2f}",
                    direction="bullish",
                    severity="high" if dist_sup_atr <= 0.6 else "medium",
                    score_impact=pts,
                    detail=f"Price is {dist_sup_atr:.2f} ATR above support zone (${nearest_sup['low']:.2f}-${nearest_sup['high']:.2f}).",
                )
            )

        if nearest_res and dist_res_atr <= 0.75:
            pts = 10.0
            bearish_pts += pts
            signals.append(
                Signal(
                    code="AT_RESISTANCE_ZONE",
                    label=f"Testing Resistance ${nearest_res['mid']:.2f}",
                    direction="bearish",
                    severity="medium",
                    score_impact=pts,
                    detail=f"Price is {dist_res_atr:.2f} ATR below resistance zone (${nearest_res['low']:.2f}-${nearest_res['high']:.2f}).",
                )
            )

        # Evaluate last bar candle patterns
        o1, h1, l1, c1 = float(opens[-1]), float(highs[-1]), float(lows[-1]), float(closes[-1])
        o0, h0, l0, c0 = float(opens[-2]), float(highs[-2]), float(lows[-2]), float(closes[-2])
        body1 = abs(c1 - o1)
        body0 = abs(c0 - o0)
        rng1 = max(h1 - l1, 1e-6)
        upper_wick1 = h1 - max(o1, c1)
        lower_wick1 = min(o1, c1) - l1
        ts_last = int(timestamps[-1])

        # Bullish Pin Bar / Hammer
        if rng1 >= 0.35 * atr and lower_wick1 >= max(body1 * 2.0, rng1 * 0.55) and upper_wick1 <= rng1 * 0.25:
            pts = 18.0 if dist_sup_atr <= 1.5 else 11.0
            bullish_pts += pts
            signals.append(
                Signal(
                    code="BULLISH_HAMMER",
                    label="Bullish Pin Bar / Hammer",
                    direction="bullish",
                    severity="high" if dist_sup_atr <= 1.5 else "medium",
                    score_impact=pts,
                    detail="Long lower rejection wick indicating buyers stepping in.",
                )
            )
            overlays.append(
                ChartOverlay(
                    id="pa-hammer",
                    layer="price_action",
                    kind="marker",
                    label="Hammer",
                    color="#10b981",
                    data={"time": ts_last, "position": "belowBar", "shape": "arrowUp", "text": "Hammer"},
                )
            )

        # Bearish Pin Bar / Shooting Star
        if rng1 >= 0.35 * atr and upper_wick1 >= max(body1 * 2.0, rng1 * 0.55) and lower_wick1 <= rng1 * 0.25:
            pts = 16.0 if dist_res_atr <= 1.5 else 10.0
            bearish_pts += pts
            signals.append(
                Signal(
                    code="BEARISH_PIN_BAR",
                    label="Bearish Shooting Star",
                    direction="bearish",
                    severity="high" if dist_res_atr <= 1.5 else "medium",
                    score_impact=pts,
                    detail="Long upper wick showing supply rejection.",
                )
            )
            overlays.append(
                ChartOverlay(
                    id="pa-star",
                    layer="price_action",
                    kind="marker",
                    label="Shooting Star",
                    color="#f43f5e",
                    data={"time": ts_last, "position": "aboveBar", "shape": "arrowDown", "text": "Pin"},
                )
            )

        # Bullish Engulfing
        if c0 < o0 and c1 > o1 and body1 > body0 * 1.05 and o1 <= c0 * 1.003 and c1 >= o0 * 0.997:
            pts = 18.0 if dist_sup_atr <= 1.8 else 12.0
            bullish_pts += pts
            signals.append(
                Signal(
                    code="BULLISH_ENGULFING",
                    label="Bullish Engulfing",
                    direction="bullish",
                    severity="high",
                    score_impact=pts,
                    detail="Current bullish candle engulfed prior bearish candle body.",
                )
            )
            overlays.append(
                ChartOverlay(
                    id="pa-bull-eng",
                    layer="price_action",
                    kind="marker",
                    label="Bull Engulf",
                    color="#10b981",
                    data={"time": ts_last, "position": "belowBar", "shape": "arrowUp", "text": "Engulf"},
                )
            )

        # Bearish Engulfing
        if c0 > o0 and c1 < o1 and body1 > body0 * 1.05 and o1 >= c0 * 0.997 and c1 <= o0 * 1.003:
            pts = 16.0 if dist_res_atr <= 1.8 else 10.0
            bearish_pts += pts
            signals.append(
                Signal(
                    code="BEARISH_ENGULFING",
                    label="Bearish Engulfing",
                    direction="bearish",
                    severity="high",
                    score_impact=pts,
                    detail="Current bearish candle engulfed prior bullish candle body.",
                )
            )
            overlays.append(
                ChartOverlay(
                    id="pa-bear-eng",
                    layer="price_action",
                    kind="marker",
                    label="Bear Engulf",
                    color="#f43f5e",
                    data={"time": ts_last, "position": "aboveBar", "shape": "arrowDown", "text": "Engulf"},
                )
            )

        # Inside bar compression
        if h1 <= h0 and l1 >= l0:
            signals.append(
                Signal(
                    code="INSIDE_BAR",
                    label="Inside Bar Compression",
                    direction="neutral",
                    severity="low",
                    score_impact=5.0,
                    detail="Tight range inside prior bar — watch for breakout expansion.",
                )
            )
            bullish_pts += 3.0

        # S/R Breakout (crossed above a resistance within last 2 bars)
        if nearest_sup and nearest_sup.get("polarity_flip") and dist_sup_atr <= 1.0 and c1 > c0:
            bullish_pts += 12.0
            signals.append(
                Signal(
                    code="SR_BREAK_RETEST",
                    label="S/R Break & Retest",
                    direction="bullish",
                    severity="high",
                    score_impact=12.0,
                    detail=f"Holding reclaimed polarity-flip support at ${nearest_sup['mid']:.2f}.",
                )
            )

        # Score favors clear actionable setups (either strong bullish OR strong bearish setups score high for screening)
        dominant_pts = max(bullish_pts, bearish_pts)
        score = min(98.0, max(20.0, 48.0 + dominant_pts * 1.35))

        if bullish_pts > bearish_pts + 3.0:
            bias = "bullish"
        elif bearish_pts > bullish_pts + 3.0:
            bias = "bearish"
        else:
            bias = "neutral"

        if signals:
            summary = " | ".join(s.label for s in signals[:2])
        elif nearest_sup and nearest_res:
            summary = f"Between S ${nearest_sup['mid']:.2f} and R ${nearest_res['mid']:.2f}"
        else:
            summary = "No immediate Price Action trigger"

        metrics = {
            "nearest_support": MetricItem(
                label="Nearest Support",
                value=nearest_sup["mid"] if nearest_sup else None,
                formatted=f"${nearest_sup['mid']:.2f}" if nearest_sup else "N/A",
                status="good" if dist_sup_atr <= 1.2 else "neutral",
            ),
            "nearest_resistance": MetricItem(
                label="Nearest Resistance",
                value=nearest_res["mid"] if nearest_res else None,
                formatted=f"${nearest_res['mid']:.2f}" if nearest_res else "N/A",
                status="warn" if dist_res_atr <= 0.75 else "neutral",
            ),
            "dist_to_support_atr": MetricItem(
                label="Dist. to Support (ATR)",
                value=round(dist_sup_atr, 2) if nearest_sup else None,
                formatted=f"{dist_sup_atr:.2f}x" if nearest_sup else "N/A",
                status="good" if dist_sup_atr <= 1.0 else "neutral",
            ),
            "atr_14": MetricItem(
                label="ATR (14)",
                value=round(atr, 2),
                formatted=f"${atr:.2f}",
                status="neutral",
            ),
        }

        hints = TradeHints(
            nearest_support=nearest_sup["mid"] if nearest_sup else None,
            nearest_resistance=nearest_res["mid"] if nearest_res else None,
            atr=round(atr, 4),
            setup_name=signals[0].label if signals else None,
            bias=bias,
            invalidation_level=(nearest_sup["low"] - 0.35 * atr) if (bias == "bullish" and nearest_sup) else None,
        )

        return AnalyzerResult(
            plugin_id=self.plugin_id,
            display_name=self.display_name,
            category=self.category,
            score=round(score, 1),
            bias=bias,
            confidence=0.85 if signals else 0.55,
            summary=summary,
            signals=signals,
            metrics=metrics,
            overlays=overlays,
            trade_hints=hints,
        )
