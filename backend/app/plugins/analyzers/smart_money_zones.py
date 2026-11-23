from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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


@dataclass
class _PivotState:
    current_level: float | None = None
    last_level: float | None = None
    crossed: bool = False
    bar_index: int = 0
    bar_time: int = 0


@dataclass
class _OrderBlock:
    bar_high: float
    bar_low: float
    bar_time: int
    bar_index: int
    bias: int  # +1 bullish (demand), -1 bearish (supply)
    internal: bool


@dataclass
class _FairValueGap:
    top: float
    bottom: float
    bar_time: int
    bar_index: int
    bias: int  # +1 bullish, -1 bearish


class SmartMoneyZonesAnalyzer(AnalyzerPlugin):
    plugin_id = "smart_money_zones"
    display_name = "Smart Money Zones (SMC)"
    description = (
        "Detects LuxAlgo-style Smart Money Concepts: Swing/Internal BOS & CHoCH, unmitigated Order Blocks "
        "(Demand/Supply), Fair Value Gaps (FVG), Equal Highs/Lows, and Premium/Discount zones."
    )
    category = "structure"
    default_weight = 0.20
    requires_deep_data = False

    def analyze(self, ctx: AnalysisContext) -> AnalyzerResult:
        df = ctx.daily_df
        if df is None or len(df) < 35:
            return AnalyzerResult(
                plugin_id=self.plugin_id,
                display_name=self.display_name,
                category=self.category,
                score=50.0,
                bias="neutral",
                confidence=0.1,
                summary="Insufficient bars for Smart Money Concepts analysis.",
            )

        work = df.tail(500).reset_index(drop=True)
        n = len(work)
        opens = work["open"].to_numpy(dtype=float)
        highs = work["high"].to_numpy(dtype=float)
        lows = work["low"].to_numpy(dtype=float)
        closes = work["close"].to_numpy(dtype=float)
        timestamps = work["ts"].to_numpy(dtype=int)

        # Volatility filter matching pine/smart money 1.pine: ta.atr(200)
        prev_c = np.roll(closes, 1)
        prev_c[0] = closes[0]
        tr = np.maximum(highs - lows, np.maximum(np.abs(highs - prev_c), np.abs(lows - prev_c)))
        atr200 = pd.Series(tr).rolling(window=min(200, n), min_periods=1).mean().to_numpy(dtype=float)
        atr14 = float(pd.Series(tr).rolling(window=14, min_periods=1).mean().iloc[-1])

        high_vol = (highs - lows) >= (2.0 * atr200)
        parsed_highs = np.where(high_vol, lows, highs)
        parsed_lows = np.where(high_vol, highs, lows)

        swing_len = min(35, max(12, n // 8))
        internal_len = 5
        eq_len = 3

        swing_high = _PivotState()
        swing_low = _PivotState()
        internal_high = _PivotState()
        internal_low = _PivotState()
        eq_high = _PivotState()
        eq_low = _PivotState()

        swing_leg = 0
        internal_leg = 0
        eq_leg = 0

        swing_trend = 0  # +1 bullish, -1 bearish
        internal_trend = 0

        swing_obs: list[_OrderBlock] = []
        internal_obs: list[_OrderBlock] = []
        fvgs: list[_FairValueGap] = []
        recent_breaks: list[dict[str, Any]] = []
        eq_events: list[dict[str, Any]] = []

        trailing_top = float(highs[0])
        trailing_bottom = float(lows[0])

        # Cumulative mean bar delta for FVG auto-threshold
        cum_abs_delta = 0.0

        for i in range(1, n):
            trailing_top = max(trailing_top, float(highs[i]))
            trailing_bottom = min(trailing_bottom, float(lows[i]))

            # 1. Update legs (mirroring pine leg(size))
            if i >= swing_len:
                p_idx = i - swing_len
                new_leg_high = highs[p_idx] > np.max(highs[p_idx + 1 : i + 1])
                new_leg_low = lows[p_idx] < np.min(lows[p_idx + 1 : i + 1])
                prev_leg = swing_leg
                if new_leg_high:
                    swing_leg = 0
                elif new_leg_low:
                    swing_leg = 1
                if swing_leg != prev_leg:
                    if swing_leg == 1:
                        swing_low.last_level = swing_low.current_level
                        swing_low.current_level = float(lows[p_idx])
                        swing_low.crossed = False
                        swing_low.bar_index = p_idx
                        swing_low.bar_time = int(timestamps[p_idx])
                        trailing_bottom = float(lows[p_idx])
                    else:
                        swing_high.last_level = swing_high.current_level
                        swing_high.current_level = float(highs[p_idx])
                        swing_high.crossed = False
                        swing_high.bar_index = p_idx
                        swing_high.bar_time = int(timestamps[p_idx])
                        trailing_top = float(highs[p_idx])

            if i >= internal_len:
                p_idx = i - internal_len
                new_leg_high = highs[p_idx] > np.max(highs[p_idx + 1 : i + 1])
                new_leg_low = lows[p_idx] < np.min(lows[p_idx + 1 : i + 1])
                prev_leg = internal_leg
                if new_leg_high:
                    internal_leg = 0
                elif new_leg_low:
                    internal_leg = 1
                if internal_leg != prev_leg:
                    if internal_leg == 1:
                        internal_low.last_level = internal_low.current_level
                        internal_low.current_level = float(lows[p_idx])
                        internal_low.crossed = False
                        internal_low.bar_index = p_idx
                        internal_low.bar_time = int(timestamps[p_idx])
                    else:
                        internal_high.last_level = internal_high.current_level
                        internal_high.current_level = float(highs[p_idx])
                        internal_high.crossed = False
                        internal_high.bar_index = p_idx
                        internal_high.bar_time = int(timestamps[p_idx])

            if i >= eq_len:
                p_idx = i - eq_len
                new_leg_high = highs[p_idx] > np.max(highs[p_idx + 1 : i + 1])
                new_leg_low = lows[p_idx] < np.min(lows[p_idx + 1 : i + 1])
                prev_leg = eq_leg
                if new_leg_high:
                    eq_leg = 0
                elif new_leg_low:
                    eq_leg = 1
                if eq_leg != prev_leg:
                    if eq_leg == 1:
                        if eq_low.current_level is not None and abs(eq_low.current_level - lows[p_idx]) < 0.15 * atr200[i]:
                            eq_events.append({"kind": "EQL", "price": float(lows[p_idx]), "idx": i, "ts": int(timestamps[i])})
                        eq_low.current_level = float(lows[p_idx])
                        eq_low.bar_index = p_idx
                        eq_low.bar_time = int(timestamps[p_idx])
                    else:
                        if eq_high.current_level is not None and abs(eq_high.current_level - highs[p_idx]) < 0.15 * atr200[i]:
                            eq_events.append({"kind": "EQH", "price": float(highs[p_idx]), "idx": i, "ts": int(timestamps[i])})
                        eq_high.current_level = float(highs[p_idx])
                        eq_high.bar_index = p_idx
                        eq_high.bar_time = int(timestamps[p_idx])

            # 2. Detect structure breaks (BOS / CHoCH) & store Order Blocks
            def _check_structure(p_hi: _PivotState, p_lo: _PivotState, is_internal: bool) -> None:
                nonlocal swing_trend, internal_trend
                t_val = internal_trend if is_internal else swing_trend
                obs = internal_obs if is_internal else swing_obs

                # Bullish crossover
                if (
                    p_hi.current_level is not None
                    and not p_hi.crossed
                    and closes[i] > p_hi.current_level
                    and closes[i - 1] <= p_hi.current_level
                    and (not is_internal or internal_high.current_level != swing_high.current_level)
                ):
                    tag = "CHoCH" if t_val == -1 else "BOS"
                    p_hi.crossed = True
                    if is_internal:
                        internal_trend = 1
                    else:
                        swing_trend = 1
                    recent_breaks.append(
                        {"scope": "internal" if is_internal else "swing", "dir": 1, "tag": tag, "idx": i, "ts": int(timestamps[i]), "level": p_hi.current_level}
                    )
                    start_i = max(0, min(p_hi.bar_index, i - 1))
                    seg = parsed_lows[start_i:i]
                    if len(seg) > 0:
                        rel_idx = int(np.argmin(seg))
                        ob_i = start_i + rel_idx
                        obs.insert(
                            0,
                            _OrderBlock(
                                bar_high=float(parsed_highs[ob_i]),
                                bar_low=float(parsed_lows[ob_i]),
                                bar_time=int(timestamps[ob_i]),
                                bar_index=ob_i,
                                bias=1,
                                internal=is_internal,
                            ),
                        )
                        if len(obs) > 40:
                            obs.pop()

                # Bearish crossunder
                if (
                    p_lo.current_level is not None
                    and not p_lo.crossed
                    and closes[i] < p_lo.current_level
                    and closes[i - 1] >= p_lo.current_level
                    and (not is_internal or internal_low.current_level != swing_low.current_level)
                ):
                    tag = "CHoCH" if t_val == 1 else "BOS"
                    p_lo.crossed = True
                    if is_internal:
                        internal_trend = -1
                    else:
                        swing_trend = -1
                    recent_breaks.append(
                        {"scope": "internal" if is_internal else "swing", "dir": -1, "tag": tag, "idx": i, "ts": int(timestamps[i]), "level": p_lo.current_level}
                    )
                    start_i = max(0, min(p_lo.bar_index, i - 1))
                    seg = parsed_highs[start_i:i]
                    if len(seg) > 0:
                        rel_idx = int(np.argmax(seg))
                        ob_i = start_i + rel_idx
                        obs.insert(
                            0,
                            _OrderBlock(
                                bar_high=float(parsed_highs[ob_i]),
                                bar_low=float(parsed_lows[ob_i]),
                                bar_time=int(timestamps[ob_i]),
                                bar_index=ob_i,
                                bias=-1,
                                internal=is_internal,
                            ),
                        )
                        if len(obs) > 40:
                            obs.pop()

            _check_structure(internal_high, internal_low, is_internal=True)
            _check_structure(swing_high, swing_low, is_internal=False)

            # 3. Mitigate Order Blocks
            internal_obs = [
                ob for ob in internal_obs
                if not ((ob.bias == -1 and highs[i] > ob.bar_high) or (ob.bias == 1 and lows[i] < ob.bar_low))
            ]
            swing_obs = [
                ob for ob in swing_obs
                if not ((ob.bias == -1 and highs[i] > ob.bar_high) or (ob.bias == 1 and lows[i] < ob.bar_low))
            ]

            # 4. Fair Value Gaps (FVG) + Mitigation
            fvgs = [
                gap for gap in fvgs
                if not ((gap.bias == 1 and lows[i] < gap.bottom) or (gap.bias == -1 and highs[i] > gap.top))
            ]
            if i >= 2 and opens[i - 1] > 0:
                delta_pct = (closes[i - 1] - opens[i - 1]) / opens[i - 1]
                cum_abs_delta += abs(delta_pct)
                thresh = (cum_abs_delta / i) * 1.5
                if lows[i] > highs[i - 2] and closes[i - 1] > highs[i - 2] and delta_pct > thresh:
                    fvgs.insert(
                        0,
                        _FairValueGap(top=float(lows[i]), bottom=float(highs[i - 2]), bar_time=int(timestamps[i - 1]), bar_index=i - 1, bias=1),
                    )
                elif highs[i] < lows[i - 2] and closes[i - 1] < lows[i - 2] and -delta_pct > thresh:
                    fvgs.insert(
                        0,
                        _FairValueGap(top=float(lows[i - 2]), bottom=float(highs[i]), bar_time=int(timestamps[i - 1]), bar_index=i - 1, bias=-1),
                    )
                if len(fvgs) > 25:
                    fvgs.pop()

        last_close = float(closes[-1])
        last_low = float(lows[-1])
        last_high = float(highs[-1])
        atr_safe = max(atr14, last_close * 0.015, 0.01)

        # Combine swing + internal OBs and keep strongest unmitigated Demand (below/at price) & Supply (above/at price)
        all_obs = swing_obs + internal_obs
        bull_obs = sorted(
            [ob for ob in all_obs if ob.bias == 1 and ob.bar_low <= last_close * 1.01],
            key=lambda ob: abs(last_close - ob.bar_high),
        )[:3]
        bear_obs = sorted(
            [ob for ob in all_obs if ob.bias == -1 and ob.bar_high >= last_close * 0.99],
            key=lambda ob: abs(ob.bar_low - last_close),
        )[:3]

        nearest_bull_ob = bull_obs[0] if bull_obs else None
        nearest_bear_ob = bear_obs[0] if bear_obs else None

        bull_fvgs = sorted([g for g in fvgs if g.bias == 1 and g.bottom <= last_close], key=lambda g: abs(last_close - g.top))[:2]
        bear_fvgs = sorted([g for g in fvgs if g.bias == -1 and g.top >= last_close], key=lambda g: abs(g.bottom - last_close))[:2]

        # Premium / Equilibrium / Discount calculation
        range_span = max(trailing_top - trailing_bottom, 1e-6)
        range_pos_pct = ((last_close - trailing_bottom) / range_span) * 100.0
        if range_pos_pct <= 33.0:
            zone_label = "Discount"
        elif range_pos_pct >= 67.0:
            zone_label = "Premium"
        else:
            zone_label = "Equilibrium"

        # Scoring & Signals
        signals: list[Signal] = []
        overlays: list[ChartOverlay] = []
        bull_pts = 0.0
        bear_pts = 0.0

        # Recent breaks within last 10 bars
        recent_10 = [b for b in recent_breaks if b["idx"] >= n - 10]
        latest_swing_break = next((b for b in reversed(recent_breaks) if b["scope"] == "swing"), None)

        for b in recent_10[-3:]:
            tag = b["tag"]
            if b["dir"] == 1:
                pts = 16.0 if tag == "CHoCH" else 12.0
                bull_pts += pts
                code = "SMC_BULLISH_CHOCH" if tag == "CHoCH" else "SMC_BULLISH_BOS"
                signals.append(
                    Signal(
                        code=code,
                        label=f"SMC {b['scope'].title()} Bullish {tag}",
                        direction="bullish",
                        severity="high" if tag == "CHoCH" else "medium",
                        score_impact=pts,
                        detail=f"Closed above structure level ${b['level']:.2f}.",
                    )
                )
            else:
                pts = 16.0 if tag == "CHoCH" else 12.0
                bear_pts += pts
                code = "SMC_BEARISH_CHOCH" if tag == "CHoCH" else "SMC_BEARISH_BOS"
                signals.append(
                    Signal(
                        code=code,
                        label=f"SMC {b['scope'].title()} Bearish {tag}",
                        direction="bearish",
                        severity="high" if tag == "CHoCH" else "medium",
                        score_impact=pts,
                        detail=f"Closed below structure level ${b['level']:.2f}.",
                    )
                )

        # Testing unmitigated Demand / Supply Order Blocks
        if nearest_bull_ob:
            dist_ob = (last_close - nearest_bull_ob.bar_high) / atr_safe
            if last_low <= nearest_bull_ob.bar_high + 0.4 * atr_safe and dist_ob <= 1.1:
                pts = 22.0 if dist_ob <= 0.5 else 14.0
                bull_pts += pts
                signals.append(
                    Signal(
                        code="SMC_AT_BULLISH_OB",
                        label=f"Testing Bullish Order Block (${nearest_bull_ob.bar_low:.2f}-${nearest_bull_ob.bar_high:.2f})",
                        direction="bullish",
                        severity="high",
                        score_impact=pts,
                        detail="Price is tapping an unmitigated institutional Demand Order Block.",
                    )
                )

        if nearest_bear_ob:
            dist_ob = (nearest_bear_ob.bar_low - last_close) / atr_safe
            if last_high >= nearest_bear_ob.bar_low - 0.4 * atr_safe and dist_ob <= 1.1:
                pts = 20.0 if dist_ob <= 0.5 else 13.0
                bear_pts += pts
                signals.append(
                    Signal(
                        code="SMC_AT_BEARISH_OB",
                        label=f"Testing Bearish Order Block (${nearest_bear_ob.bar_low:.2f}-${nearest_bear_ob.bar_high:.2f})",
                        direction="bearish",
                        severity="high",
                        score_impact=pts,
                        detail="Price is tapping an unmitigated institutional Supply Order Block.",
                    )
                )

        # Fair Value Gap proximity
        if bull_fvgs and (last_close - bull_fvgs[0].top) <= 0.8 * atr_safe:
            bull_pts += 10.0
            signals.append(
                Signal(
                    code="SMC_FVG_SUPPORT",
                    label=f"Bullish FVG Support (${bull_fvgs[0].bottom:.2f}-${bull_fvgs[0].top:.2f})",
                    direction="bullish",
                    severity="medium",
                    score_impact=10.0,
                )
            )
        if bear_fvgs and (bear_fvgs[0].bottom - last_close) <= 0.8 * atr_safe:
            bear_pts += 10.0
            signals.append(
                Signal(
                    code="SMC_FVG_RESISTANCE",
                    label=f"Bearish FVG Supply (${bear_fvgs[0].bottom:.2f}-${bear_fvgs[0].top:.2f})",
                    direction="bearish",
                    severity="medium",
                    score_impact=10.0,
                )
            )

        # Premium / Discount Alignment
        if zone_label == "Discount":
            bull_pts += 8.0
            signals.append(
                Signal(
                    code="SMC_DISCOUNT_ZONE",
                    label=f"Discount Zone ({range_pos_pct:.0f}% of swing range)",
                    direction="bullish",
                    severity="low",
                    score_impact=8.0,
                    detail="Trading in lower 33% discount territory of trailing swing range.",
                )
            )
        elif zone_label == "Premium":
            bear_pts += 8.0
            signals.append(
                Signal(
                    code="SMC_PREMIUM_ZONE",
                    label=f"Premium Zone ({range_pos_pct:.0f}% of swing range)",
                    direction="bearish",
                    severity="low",
                    score_impact=8.0,
                    detail="Trading in upper 33% premium territory of trailing swing range.",
                )
            )

        # EQH / EQL liquidity pools in last 15 bars
        for eq in [e for e in eq_events if e["idx"] >= n - 15][-2:]:
            signals.append(
                Signal(
                    code=f"SMC_{eq['kind']}",
                    label=f"{eq['kind']} Liquidity Pool (${eq['price']:.2f})",
                    direction="bullish" if eq["kind"] == "EQH" else "bearish",
                    severity="low",
                    score_impact=5.0,
                    detail="Equal highs/lows resting liquidity target.",
                )
            )

        # Overlays (all on layer="smart_money")
        for idx, ob in enumerate(bull_obs):
            overlays.append(
                ChartOverlay(
                    id=f"smc-ob-bull-{idx}",
                    layer="smart_money",
                    kind="horizontal_zone",
                    label=f"{'Swing ' if not ob.internal else ''}Bull OB ${ob.bar_low:.2f}-${ob.bar_high:.2f}",
                    color="#3179f5",
                    data={
                        "priceLow": round(ob.bar_low, 2),
                        "priceHigh": round(ob.bar_high, 2),
                        "mid": round((ob.bar_low + ob.bar_high) / 2.0, 2),
                        "startTs": ob.bar_time,
                        "role": "ob_bull",
                    },
                )
            )
        for idx, ob in enumerate(bear_obs):
            overlays.append(
                ChartOverlay(
                    id=f"smc-ob-bear-{idx}",
                    layer="smart_money",
                    kind="horizontal_zone",
                    label=f"{'Swing ' if not ob.internal else ''}Bear OB ${ob.bar_low:.2f}-${ob.bar_high:.2f}",
                    color="#f23645",
                    data={
                        "priceLow": round(ob.bar_low, 2),
                        "priceHigh": round(ob.bar_high, 2),
                        "mid": round((ob.bar_low + ob.bar_high) / 2.0, 2),
                        "startTs": ob.bar_time,
                        "role": "ob_bear",
                    },
                )
            )
        for idx, gap in enumerate(bull_fvgs):
            overlays.append(
                ChartOverlay(
                    id=f"smc-fvg-bull-{idx}",
                    layer="smart_money",
                    kind="horizontal_zone",
                    label=f"Bull FVG ${gap.bottom:.2f}-${gap.top:.2f}",
                    color="#00e676",
                    data={
                        "priceLow": round(gap.bottom, 2),
                        "priceHigh": round(gap.top, 2),
                        "mid": round((gap.bottom + gap.top) / 2.0, 2),
                        "startTs": gap.bar_time,
                        "role": "fvg_bull",
                    },
                )
            )
        for idx, gap in enumerate(bear_fvgs):
            overlays.append(
                ChartOverlay(
                    id=f"smc-fvg-bear-{idx}",
                    layer="smart_money",
                    kind="horizontal_zone",
                    label=f"Bear FVG ${gap.bottom:.2f}-${gap.top:.2f}",
                    color="#ff5252",
                    data={
                        "priceLow": round(gap.bottom, 2),
                        "priceHigh": round(gap.top, 2),
                        "mid": round((gap.bottom + gap.top) / 2.0, 2),
                        "startTs": gap.bar_time,
                        "role": "fvg_bear",
                    },
                )
            )

        # Premium / Discount markers
        overlays.append(
            ChartOverlay(
                id="smc-discount-zone",
                layer="smart_money",
                kind="horizontal_zone",
                label=f"Discount Zone (${trailing_bottom:.2f})",
                color="#089981",
                data={
                    "priceLow": round(trailing_bottom, 2),
                    "priceHigh": round(trailing_bottom + 0.12 * range_span, 2),
                    "mid": round(trailing_bottom, 2),
                    "role": "discount",
                },
            )
        )
        overlays.append(
            ChartOverlay(
                id="smc-premium-zone",
                layer="smart_money",
                kind="horizontal_zone",
                label=f"Premium Zone (${trailing_top:.2f})",
                color="#f23645",
                data={
                    "priceLow": round(trailing_top - 0.12 * range_span, 2),
                    "priceHigh": round(trailing_top, 2),
                    "mid": round(trailing_top, 2),
                    "role": "premium",
                },
            )
        )

        # Add recent BOS / CHoCH markers
        for idx, brk in enumerate(recent_breaks[-4:]):
            overlays.append(
                ChartOverlay(
                    id=f"smc-brk-{idx}",
                    layer="smart_money",
                    kind="marker",
                    label=brk["tag"],
                    color="#089981" if brk["dir"] == 1 else "#f23645",
                    data={
                        "time": brk["ts"],
                        "position": "belowBar" if brk["dir"] == 1 else "aboveBar",
                        "shape": "arrowUp" if brk["dir"] == 1 else "arrowDown",
                        "text": f"{brk['tag']}",
                    },
                )
            )

        if swing_trend == 1:
            bull_pts += 6.0
        elif swing_trend == -1:
            bear_pts += 6.0

        dominant_pts = max(bull_pts, bear_pts)
        score = min(98.0, max(22.0, 48.0 + dominant_pts * 1.2))
        if bull_pts > bear_pts + 3.0:
            bias = "bullish"
        elif bear_pts > bull_pts + 3.0:
            bias = "bearish"
        else:
            bias = "bullish" if swing_trend == 1 else ("bearish" if swing_trend == -1 else "neutral")

        swing_str = "Bullish" if swing_trend == 1 else ("Bearish" if swing_trend == -1 else "Neutral")
        internal_str = "Bullish" if internal_trend == 1 else ("Bearish" if internal_trend == -1 else "Neutral")

        if signals:
            summary = f"{signals[0].label} | {zone_label} ({range_pos_pct:.0f}%)"
        elif latest_swing_break:
            summary = f"Swing {swing_str} ({latest_swing_break['tag']}) | {zone_label} ({range_pos_pct:.0f}%)"
        else:
            summary = f"Swing {swing_str} | {zone_label} ({range_pos_pct:.0f}%)"

        metrics = {
            "swing_structure": MetricItem(
                label="SMC Swing Trend",
                value=swing_str,
                formatted=swing_str,
                status="good" if swing_trend == 1 else ("bad" if swing_trend == -1 else "neutral"),
            ),
            "internal_structure": MetricItem(
                label="SMC Internal Trend",
                value=internal_str,
                formatted=internal_str,
            ),
            "zone_position": MetricItem(
                label="Range Zone",
                value=round(range_pos_pct, 1),
                formatted=f"{zone_label} ({range_pos_pct:.0f}%)",
                status="good" if zone_label == "Discount" else ("warn" if zone_label == "Premium" else "neutral"),
            ),
            "nearest_demand_ob": MetricItem(
                label="Demand OB",
                value=round(nearest_bull_ob.bar_high, 2) if nearest_bull_ob else None,
                formatted=f"${nearest_bull_ob.bar_low:.2f}-${nearest_bull_ob.bar_high:.2f}" if nearest_bull_ob else "None",
                status="good" if nearest_bull_ob else "neutral",
            ),
            "nearest_supply_ob": MetricItem(
                label="Supply OB",
                value=round(nearest_bear_ob.bar_low, 2) if nearest_bear_ob else None,
                formatted=f"${nearest_bear_ob.bar_low:.2f}-${nearest_bear_ob.bar_high:.2f}" if nearest_bear_ob else "None",
                status="warn" if nearest_bear_ob else "neutral",
            ),
            "active_ob_count": MetricItem(
                label="Unmitigated OBs / FVGs",
                value=len(all_obs) + len(fvgs),
                formatted=f"{len(bull_obs)} Bull / {len(bear_obs)} Bear / {len(fvgs)} FVG",
            ),
        }

        hints = TradeHints(
            nearest_support=round(nearest_bull_ob.bar_high, 2) if nearest_bull_ob else None,
            nearest_resistance=round(nearest_bear_ob.bar_low, 2) if nearest_bear_ob else None,
            smc_demand_zone=(round(nearest_bull_ob.bar_low, 2), round(nearest_bull_ob.bar_high, 2))
            if nearest_bull_ob
            else None,
            smc_supply_zone=(round(nearest_bear_ob.bar_low, 2), round(nearest_bear_ob.bar_high, 2))
            if nearest_bear_ob
            else None,
            swing_trend="bullish" if swing_trend == 1 else ("bearish" if swing_trend == -1 else "neutral"),
            atr=round(atr14, 4),
            setup_name=signals[0].label if signals else f"SMC {swing_str} Structure",
            bias=bias,
        )

        return AnalyzerResult(
            plugin_id=self.plugin_id,
            display_name=self.display_name,
            category=self.category,
            score=round(score, 1),
            bias=bias,
            confidence=0.88 if signals else 0.65,
            summary=summary,
            signals=signals,
            metrics=metrics,
            overlays=overlays,
            trade_hints=hints,
        )
