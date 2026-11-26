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

    # Determine direction before building setup_name so conflicting plugin labels don't mix into it
    effective_bias = overall_bias
    if effective_bias == "neutral" and rr_mod and rr_mod.bias == "bearish":
        rr_val = float(rr_mod.metrics.get("asymmetric_rr").value or 0.0) if "asymmetric_rr" in rr_mod.metrics else 0.0
        if rr_val >= 1.8:
            effective_bias = "bearish"

    if effective_bias == "bearish":
        direction: Literal["LONG", "SHORT", "NEUTRAL"] = "SHORT"
        target_bias = "bearish"
    elif overall_bias == "bullish":
        direction = "LONG"
        target_bias = "bullish"
    else:
        direction = "NEUTRAL"
        target_bias = "bullish"

    setup_parts = []
    for mod in (smc, ch, pa):
        if mod and mod.trade_hints and mod.trade_hints.setup_name and mod.bias == target_bias:
            lbl = mod.trade_hints.setup_name
            lower_lbl = lbl.lower()
            if target_bias == "bullish" and ("bearish" in lower_lbl or "premium zone" in lower_lbl):
                continue
            if target_bias == "bearish" and ("bullish" in lower_lbl or "discount zone" in lower_lbl):
                continue
            setup_parts.append(lbl)
    setup_name = " + ".join(dict.fromkeys(setup_parts[:2])) or (
        "Bearish Supply Setup" if direction == "SHORT" else "Swing Technical Setup"
    )
    if direction == "NEUTRAL":
        setup_name = f"Support / Demand Bounce ({setup_name})"

    if direction == "SHORT":
        ceilings = [lvl for lvl in (nearest_res, ch_high) if lvl is not None and lvl >= spot_price * 0.985]
        anchor_res = min(ceilings) if ceilings else spot_price + 1.2 * atr
        if smc_hints and smc_hints.smc_supply_zone and smc_hints.smc_supply_zone[1] >= spot_price:
            anchor_res = min(anchor_res, smc_hints.smc_supply_zone[1])

        outer_res_cands = [
            lvl
            for lvl in (
                pa_hints.resistance_zone_high if pa_hints else None,
                smc_hints.smc_supply_zone[1] if (smc_hints and smc_hints.smc_supply_zone) else None,
                nearest_res,
            )
            if lvl is not None and lvl >= spot_price * 0.985 and lvl <= spot_price + 2.8 * atr
        ]
        outer_res_ceiling = max(outer_res_cands) if outer_res_cands else anchor_res
        effective_res_ceiling = min(outer_res_ceiling, spot_price + 2.2 * atr)

        entry_high = round(max(spot_price, min(anchor_res * 0.998, spot_price + 0.55 * atr)), 2)
        entry_low = round(min(entry_high, max(0.01, spot_price - 0.15 * atr)), 2)
        stop_loss = round(
            max(entry_high + 0.60 * atr, spot_price + 1.0 * atr, effective_res_ceiling + 0.25 * atr),
            2,
        )

        mid_entry = (entry_low + entry_high) / 2.0
        risk = max(stop_loss - mid_entry, 0.25 * atr)

        # Respect real structural floors below entry rather than forcing TP1 through support/channel floor
        # Exclude soft channel midline unless it offers >= 1.1R from both mid_entry and spot_price
        floors_list = [
            lvl
            for lvl in (nearest_sup, ch_low)
            if lvl is not None and lvl < entry_low - 0.55 * atr
        ]
        spot_risk_prelim = max(0.0, stop_loss - spot_price)
        mid_floor_cap = min(mid_entry - 1.2 * risk, spot_price - 1.1 * spot_risk_prelim)
        if ch_mid is not None and ch_mid <= mid_floor_cap and ch_mid < entry_low - 0.55 * atr:
            floors_list.append(ch_mid)
        floors = sorted(floors_list, reverse=True)
        if floors:
            tp1 = round(max(0.01, floors[0]), 2)
            tp2_cand = (
                floors[1]
                if len(floors) > 1 and floors[1] < tp1 - 0.35 * atr
                else min(tp1 - 0.8 * risk, mid_entry - 2.0 * risk)
            )
        else:
            tp1 = round(max(0.01, mid_entry - 1.5 * risk), 2)
            tp2_cand = mid_entry - 2.5 * risk

        tp2 = round(max(0.01, min(tp2_cand, tp1 - 0.8 * risk)), 2)

        barrier_crossing = bool(
            ch_low is not None and ch_low < entry_low - 0.15 * atr and tp1 < ch_low - 0.15 * atr
        )

        tp1_rr = round(max(0.0, mid_entry - tp1) / max(stop_loss - mid_entry, 1e-4), 2)
        rr = round(max(0.0, mid_entry - tp2) / max(stop_loss - mid_entry, 1e-4), 2)
        potential_gain_pct = round(max(0.0, (mid_entry - tp2) / max(mid_entry, 1e-4) * 100.0), 2)
        tp1_gain_pct = round(max(0.0, (mid_entry - tp1) / max(mid_entry, 1e-4) * 100.0), 2)
        potential_loss_pct = round(max(0.0, (stop_loss - mid_entry) / max(mid_entry, 1e-4) * 100.0), 2)

        spot_risk = stop_loss - spot_price
        spot_reward_1 = max(0.0, spot_price - tp1)
        spot_reward_2 = max(0.0, spot_price - tp2)
        rationale = f"Short setup anchored against Supply/Resistance (${anchor_res:.2f}) targeting lower S/R (${tp2:.2f})."
    else:
        floors = [lvl for lvl in (nearest_sup, ch_low) if lvl is not None and lvl <= spot_price * 1.015]
        anchor_sup = max(floors) if floors else max(0.01, spot_price - 1.2 * atr)
        if smc_hints and smc_hints.smc_demand_zone and smc_hints.smc_demand_zone[0] <= spot_price:
            anchor_sup = max(anchor_sup, smc_hints.smc_demand_zone[0])

        outer_sup_cands = [
            lvl
            for lvl in (
                pa_hints.support_zone_low if pa_hints else None,
                smc_hints.smc_demand_zone[0] if (smc_hints and smc_hints.smc_demand_zone) else None,
                nearest_sup,
            )
            if lvl is not None and lvl <= spot_price * 1.015 and lvl >= spot_price - 2.8 * atr
        ]
        outer_sup_floor = min(outer_sup_cands) if outer_sup_cands else anchor_sup
        effective_sup_floor = max(outer_sup_floor, spot_price - 2.2 * atr)

        entry_low = round(max(0.01, min(spot_price, max(anchor_sup * 1.002, spot_price - 0.55 * atr))), 2)
        entry_high = round(max(entry_low, spot_price + 0.15 * atr), 2)
        stop_loss = round(
            max(0.01, min(entry_low - 0.60 * atr, spot_price - 1.0 * atr, effective_sup_floor - 0.25 * atr)),
            2,
        )

        mid_entry = (entry_low + entry_high) / 2.0
        risk = max(mid_entry - stop_loss, 0.25 * atr)

        # Respect real structural ceilings above entry rather than forcing TP1 through resistance
        # Exclude soft channel midline unless it offers >= 1.1R from both mid_entry and spot_price
        ceilings_list = [
            lvl
            for lvl in (nearest_res, ch_high)
            if lvl is not None and lvl > entry_high + 0.55 * atr
        ]
        spot_risk_prelim = max(0.0, spot_price - stop_loss)
        mid_ceil_floor = max(mid_entry + 1.2 * risk, spot_price + 1.1 * spot_risk_prelim)
        if ch_mid is not None and ch_mid >= mid_ceil_floor and ch_mid > entry_high + 0.55 * atr:
            ceilings_list.append(ch_mid)
        ceilings = sorted(ceilings_list)
        if ceilings:
            tp1 = round(ceilings[0], 2)
            tp2_cand = (
                ceilings[1]
                if len(ceilings) > 1 and ceilings[1] > tp1 + 0.35 * atr
                else max(tp1 + 0.8 * risk, mid_entry + 2.0 * risk)
            )
        else:
            tp1 = round(mid_entry + 1.5 * risk, 2)
            tp2_cand = mid_entry + 2.5 * risk

        tp2 = round(max(tp2_cand, tp1 + 0.8 * risk), 2)

        barrier_crossing = bool(
            ch_high is not None and ch_high > entry_high + 0.15 * atr and tp1 > ch_high + 0.15 * atr
        )

        tp1_rr = round(max(0.0, tp1 - mid_entry) / max(mid_entry - stop_loss, 1e-4), 2)
        rr = round(max(0.0, tp2 - mid_entry) / max(mid_entry - stop_loss, 1e-4), 2)
        potential_gain_pct = round(max(0.0, (tp2 - mid_entry) / max(mid_entry, 1e-4) * 100.0), 2)
        tp1_gain_pct = round(max(0.0, (tp1 - mid_entry) / max(mid_entry, 1e-4) * 100.0), 2)
        potential_loss_pct = round(max(0.0, (mid_entry - stop_loss) / max(mid_entry, 1e-4) * 100.0), 2)

        spot_risk = spot_price - stop_loss
        spot_reward_1 = max(0.0, tp1 - spot_price)
        spot_reward_2 = max(0.0, tp2 - spot_price)
        rationale = f"Long entry near ${entry_low:.2f}-${entry_high:.2f} anchored by Demand/Support (${anchor_sup:.2f})."

    if spot_risk > 0 and spot_price > 0:
        spot_risk_pct: float | None = round((spot_risk / spot_price) * 100.0, 2)
        spot_rr_tp1: float | None = round(spot_reward_1 / spot_risk, 2)
        spot_rr_tp2: float | None = round(spot_reward_2 / spot_risk, 2)
        stop_distance_atr: float | None = round(spot_risk / max(atr, 1e-4), 2)
    else:
        spot_risk_pct = None
        spot_rr_tp1 = None
        spot_rr_tp2 = None
        stop_distance_atr = None

    index_trend = _compute_index_trend(benchmark_df)
    corr_60: float | None = None
    for pid in ("correlation_rs", "rr_index_trend"):
        mod = results.get(pid)
        if mod and "corr_60d" in mod.metrics and mod.metrics["corr_60d"].value is not None:
            try:
                corr_60 = float(mod.metrics["corr_60d"].value)
                break
            except (TypeError, ValueError):
                pass
    index_coupled = True if corr_60 is None else (corr_60 >= 0.35)
    index_aligned = index_coupled and (
        (direction == "LONG" and index_trend == "bullish")
        or (direction == "SHORT" and index_trend == "bearish")
    )
    below_sup = [lvl for lvl in sup_candidates if lvl <= spot_price]
    above_res = [lvl for lvl in res_candidates if lvl >= spot_price]
    ref_sup = max(below_sup) if below_sup else None
    ref_res = min(above_res) if above_res else None
    dist_sup_pct = round(max(0.0, ((spot_price - ref_sup) / spot_price) * 100.0), 2) if ref_sup and spot_price > 0 else None
    dist_res_pct = round(max(0.0, ((ref_res - spot_price) / spot_price) * 100.0), 2) if ref_res and spot_price > 0 else None

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
        tp1_risk_reward=max(0.0, tp1_rr),
        atr=round(atr, 2),
        risk_per_share=round(max(abs(((entry_low + entry_high) / 2.0) - stop_loss), 0.01), 2),
        potential_gain_pct=potential_gain_pct,
        tp1_gain_pct=tp1_gain_pct,
        potential_loss_pct=potential_loss_pct,
        spot_risk_pct=spot_risk_pct,
        spot_rr_tp1=spot_rr_tp1,
        spot_rr_tp2=spot_rr_tp2,
        stop_distance_atr=stop_distance_atr,
        target_crosses_barrier=barrier_crossing,
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
