from __future__ import annotations

from typing import Literal

import pandas as pd

from app.core.contracts import AnalyzerResult, ChartOverlay, TradePlan


def _compute_index_trend(benchmark_df: pd.DataFrame | None) -> Literal["bullish", "bearish", "neutral"]:
    if benchmark_df is None or len(benchmark_df) < 30:
        return "neutral"
    closes = benchmark_df["close"].astype(float)
    last = float(closes.iloc[-1])
    ema20 = float(closes.ewm(span=20, adjust=False).mean().iloc[-1])
    ema50 = float(closes.ewm(span=50, adjust=False).mean().iloc[-1])
    if last > ema20 and ema20 >= ema50 * 0.995:
        return "bullish"
    if last < ema20 and ema20 <= ema50 * 1.005:
        return "bearish"
    return "neutral"


def build_trade_plan(
    spot_price: float,
    results: dict[str, AnalyzerResult],
    overall_bias: Literal["bullish", "bearish", "neutral"],
    benchmark_df: pd.DataFrame | None = None,
) -> tuple[TradePlan, ChartOverlay]:
    pa = results.get("price_action_sr")
    smc = results.get("smart_money_zones")
    ch = results.get("trend_channels")
    vv = results.get("volume_volatility")
    rr_mod = results.get("rr_index_trend")

    pa_hints = pa.trade_hints if pa else None
    smc_hints = smc.trade_hints if smc else None
    ch_hints = ch.trade_hints if ch else None
    vv_hints = vv.trade_hints if vv else None
    rr_hints = rr_mod.trade_hints if rr_mod else None

    atr = (
        (pa_hints.atr if pa_hints and pa_hints.atr else None)
        or (smc_hints.atr if smc_hints and smc_hints.atr else None)
        or (ch_hints.atr if ch_hints and ch_hints.atr else None)
        or (vv_hints.atr if vv_hints and vv_hints.atr else None)
        or max(spot_price * 0.02, 0.25)
    )
    atr = max(float(atr), 0.05)

    sup_candidates = [
        lvl
        for lvl in (
            smc_hints.nearest_support if smc_hints else None,
            pa_hints.nearest_support if pa_hints else None,
            rr_hints.nearest_support if rr_hints else None,
        )
        if lvl is not None and lvl <= spot_price * 1.015
    ]
    res_candidates = [
        lvl
        for lvl in (
            smc_hints.nearest_resistance if smc_hints else None,
            pa_hints.nearest_resistance if pa_hints else None,
            rr_hints.nearest_resistance if rr_hints else None,
        )
        if lvl is not None and lvl >= spot_price * 0.985
    ]

    nearest_sup = max(sup_candidates) if sup_candidates else None
    nearest_res = min(res_candidates) if res_candidates else None

    ch_low = ch_hints.channel_lower if ch_hints else None
    ch_mid = ch_hints.channel_mid if ch_hints else None
    ch_high = ch_hints.channel_upper if ch_hints else None

    setup_parts = []
    if smc_hints and smc_hints.setup_name:
        setup_parts.append(smc_hints.setup_name)
    if ch_hints and ch_hints.setup_name:
        setup_parts.append(ch_hints.setup_name)
    if pa_hints and pa_hints.setup_name:
        setup_parts.append(pa_hints.setup_name)
    setup_name = " + ".join(dict.fromkeys(setup_parts[:2])) or "Swing Technical Setup"

    # When overall bias is neutral, check if asymmetric S/R strongly favors SHORT
    effective_bias = overall_bias
    if effective_bias == "neutral" and rr_mod and rr_mod.bias == "bearish":
        rr_val = float(rr_mod.metrics.get("asymmetric_rr").value or 0.0) if "asymmetric_rr" in rr_mod.metrics else 0.0
        if rr_val >= 1.8:
            effective_bias = "bearish"

    if effective_bias == "bearish":
        direction: Literal["LONG", "SHORT", "NEUTRAL"] = "SHORT"
        ceilings = [lvl for lvl in (nearest_res, ch_high) if lvl is not None and lvl >= spot_price * 0.985]
        anchor_res = min(ceilings) if ceilings else spot_price + 1.2 * atr
        if smc_hints and smc_hints.smc_supply_zone and smc_hints.smc_supply_zone[1] >= spot_price:
            anchor_res = min(anchor_res, smc_hints.smc_supply_zone[1])

        entry_high = round(max(spot_price, min(anchor_res * 0.998, spot_price + 0.55 * atr)), 2)
        entry_low = round(min(entry_high, max(0.01, spot_price - 0.15 * atr)), 2)
        stop_loss = round(max(entry_high + 0.55 * atr, anchor_res + 0.45 * atr), 2)

        mid_entry = (entry_low + entry_high) / 2.0
        risk = max(stop_loss - mid_entry, 0.25 * atr)

        floors = sorted([lvl for lvl in (ch_mid, nearest_sup, ch_low) if lvl is not None and lvl < entry_low], reverse=True)
        tp1_raw = floors[0] if floors else (mid_entry - 1.5 * risk)
        tp1 = round(max(0.01, min(tp1_raw, entry_low - 1.15 * risk)), 2)

        tp2_cand = floors[1] if len(floors) > 1 else (ch_low if ch_low and ch_low < tp1 else mid_entry - 2.5 * risk)
        tp2 = round(max(0.01, min(tp2_cand, tp1 - 0.8 * risk)), 2)

        rr = round((mid_entry - tp2) / max(stop_loss - mid_entry, 1e-4), 2)
        potential_gain_pct = round(max(0.0, (mid_entry - tp2) / max(mid_entry, 1e-4) * 100.0), 2)
        tp1_gain_pct = round(max(0.0, (mid_entry - tp1) / max(mid_entry, 1e-4) * 100.0), 2)
        potential_loss_pct = round(max(0.0, (stop_loss - mid_entry) / max(mid_entry, 1e-4) * 100.0), 2)
        rationale = f"Short setup anchored against Supply/Resistance (${anchor_res:.2f}) targeting lower S/R (${tp2:.2f})."
    else:
        direction = "LONG" if overall_bias == "bullish" else "NEUTRAL"
        if overall_bias == "neutral":
            setup_name = f"Support / Demand Bounce ({setup_name})"
        floors = [lvl for lvl in (nearest_sup, ch_low) if lvl is not None and lvl <= spot_price * 1.015]
        anchor_sup = max(floors) if floors else max(0.01, spot_price - 1.2 * atr)
        if smc_hints and smc_hints.smc_demand_zone and smc_hints.smc_demand_zone[0] <= spot_price:
            anchor_sup = max(anchor_sup, smc_hints.smc_demand_zone[0])

        entry_low = round(max(0.01, min(spot_price, max(anchor_sup * 1.002, spot_price - 0.55 * atr))), 2)
        entry_high = round(max(entry_low, spot_price + 0.15 * atr), 2)
        stop_loss = round(max(0.01, min(entry_low - 0.55 * atr, anchor_sup - 0.45 * atr)), 2)

        mid_entry = (entry_low + entry_high) / 2.0
        risk = max(mid_entry - stop_loss, 0.25 * atr)

        ceilings = sorted([lvl for lvl in (ch_mid, nearest_res, ch_high) if lvl is not None and lvl > entry_high])
        tp1_raw = ceilings[0] if ceilings else (mid_entry + 1.5 * risk)
        tp1 = round(max(tp1_raw, entry_high + 1.15 * risk), 2)

        tp2_cand = ceilings[1] if len(ceilings) > 1 else (ch_high if ch_high and ch_high > tp1 else mid_entry + 2.5 * risk)
        tp2 = round(max(tp2_cand, tp1 + 0.8 * risk), 2)

        rr = round((tp2 - mid_entry) / max(mid_entry - stop_loss, 1e-4), 2)
        potential_gain_pct = round(max(0.0, (tp2 - mid_entry) / max(mid_entry, 1e-4) * 100.0), 2)
        tp1_gain_pct = round(max(0.0, (tp1 - mid_entry) / max(mid_entry, 1e-4) * 100.0), 2)
        potential_loss_pct = round(max(0.0, (mid_entry - stop_loss) / max(mid_entry, 1e-4) * 100.0), 2)
        rationale = f"Long entry near ${entry_low:.2f}-${entry_high:.2f} anchored by Demand/Support (${anchor_sup:.2f})."

    index_trend = _compute_index_trend(benchmark_df)
    index_aligned = (direction == "LONG" and index_trend == "bullish") or (
        direction == "SHORT" and index_trend == "bearish"
    )
    dist_sup_pct = round(((spot_price - nearest_sup) / spot_price) * 100.0, 2) if nearest_sup and spot_price > 0 else None
    dist_res_pct = round(((nearest_res - spot_price) / spot_price) * 100.0, 2) if nearest_res and spot_price > 0 else None

    plan = TradePlan(
        direction=direction,
        setup_name=setup_name,
        rationale=rationale,
        entry_low=entry_low,
        entry_high=entry_high,
        stop_loss=stop_loss,
        tp1=tp1,
        tp2=tp2,
        risk_reward=max(0.1, rr),
        atr=round(atr, 2),
        risk_per_share=round(max(abs(((entry_low + entry_high) / 2.0) - stop_loss), 0.01), 2),
        potential_gain_pct=potential_gain_pct,
        tp1_gain_pct=tp1_gain_pct,
        potential_loss_pct=potential_loss_pct,
        dist_to_support_pct=dist_sup_pct,
        dist_to_resistance_pct=dist_res_pct,
        index_aligned=index_aligned,
        index_trend=index_trend,
    )

    overlay = ChartOverlay(
        id="trade-plan-box",
        layer="trade_plan",
        kind="price_box",
        label=f"Trade Plan ({plan.direction} {plan.risk_reward:.1f}R)",
        color="#10b981" if plan.direction != "SHORT" else "#f43f5e",
        data={
            "direction": plan.direction,
            "entryLow": plan.entry_low,
            "entryHigh": plan.entry_high,
            "stopLoss": plan.stop_loss,
            "tp1": plan.tp1,
            "tp2": plan.tp2,
        },
    )
    return plan, overlay
