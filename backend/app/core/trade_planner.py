from __future__ import annotations

from typing import Literal

from app.core.contracts import AnalyzerResult, ChartOverlay, TradePlan


def build_trade_plan(
    spot_price: float,
    results: dict[str, AnalyzerResult],
    overall_bias: Literal["bullish", "bearish", "neutral"],
) -> tuple[TradePlan, ChartOverlay]:
    pa = results.get("price_action_sr")
    ch = results.get("trend_channels")
    vv = results.get("volume_volatility")

    pa_hints = pa.trade_hints if pa else None
    ch_hints = ch.trade_hints if ch else None
    vv_hints = vv.trade_hints if vv else None

    atr = (
        (pa_hints.atr if pa_hints and pa_hints.atr else None)
        or (ch_hints.atr if ch_hints and ch_hints.atr else None)
        or (vv_hints.atr if vv_hints and vv_hints.atr else None)
        or max(spot_price * 0.02, 0.25)
    )
    atr = max(float(atr), 0.05)

    nearest_sup = pa_hints.nearest_support if pa_hints else None
    nearest_res = pa_hints.nearest_resistance if pa_hints else None
    ch_low = ch_hints.channel_lower if ch_hints else None
    ch_mid = ch_hints.channel_mid if ch_hints else None
    ch_high = ch_hints.channel_upper if ch_hints else None

    setup_parts = []
    if ch_hints and ch_hints.setup_name:
        setup_parts.append(ch_hints.setup_name)
    if pa_hints and pa_hints.setup_name:
        setup_parts.append(pa_hints.setup_name)
    setup_name = " + ".join(dict.fromkeys(setup_parts)) or "Swing Technical Setup"

    if overall_bias == "bearish":
        direction: Literal["LONG", "SHORT", "NEUTRAL"] = "SHORT"
        ceilings = [lvl for lvl in (nearest_res, ch_high) if lvl is not None and lvl >= spot_price * 0.985]
        anchor_res = min(ceilings) if ceilings else spot_price + 1.2 * atr

        entry_high = round(max(spot_price, min(anchor_res * 0.998, spot_price + 0.55 * atr)), 2)
        entry_low = round(min(entry_high, max(0.01, spot_price - 0.15 * atr)), 2)
        stop_loss = round(max(entry_high + 0.6 * atr, anchor_res + 0.5 * atr), 2)

        mid_entry = (entry_low + entry_high) / 2.0
        risk = max(stop_loss - mid_entry, 0.25 * atr)

        floors = sorted([lvl for lvl in (ch_mid, nearest_sup, ch_low) if lvl is not None and lvl < entry_low], reverse=True)
        tp1_raw = floors[0] if floors else (mid_entry - 1.5 * risk)
        tp1 = round(max(0.01, min(tp1_raw, entry_low - 1.15 * risk)), 2)

        tp2_cand = floors[1] if len(floors) > 1 else (ch_low if ch_low and ch_low < tp1 else mid_entry - 2.5 * risk)
        tp2 = round(max(0.01, min(tp2_cand, tp1 - 0.8 * risk)), 2)

        rr = round((mid_entry - tp2) / max(stop_loss - mid_entry, 1e-4), 2)
        rationale = f"Short setup anchored against resistance/upper rail (${anchor_res:.2f}) with ATR-buffered stop."
    else:
        direction = "LONG" if overall_bias == "bullish" else "LONG"
        if overall_bias == "neutral":
            # Still build an actionable long bounce plan anchored to support/lower channel
            setup_name = f"Support / Range Bounce ({setup_name})"
        floors = [lvl for lvl in (nearest_sup, ch_low) if lvl is not None and lvl <= spot_price * 1.015]
        anchor_sup = max(floors) if floors else max(0.01, spot_price - 1.2 * atr)

        entry_low = round(max(0.01, min(spot_price, max(anchor_sup * 1.002, spot_price - 0.55 * atr))), 2)
        entry_high = round(max(entry_low, spot_price + 0.15 * atr), 2)
        stop_loss = round(max(0.01, min(entry_low - 0.6 * atr, anchor_sup - 0.5 * atr)), 2)

        mid_entry = (entry_low + entry_high) / 2.0
        risk = max(mid_entry - stop_loss, 0.25 * atr)

        ceilings = sorted([lvl for lvl in (ch_mid, nearest_res, ch_high) if lvl is not None and lvl > entry_high])
        tp1_raw = ceilings[0] if ceilings else (mid_entry + 1.5 * risk)
        tp1 = round(max(tp1_raw, entry_high + 1.15 * risk), 2)

        tp2_cand = ceilings[1] if len(ceilings) > 1 else (ch_high if ch_high and ch_high > tp1 else mid_entry + 2.5 * risk)
        tp2 = round(max(tp2_cand, tp1 + 0.8 * risk), 2)

        rr = round((tp2 - mid_entry) / max(mid_entry - stop_loss, 1e-4), 2)
        rationale = f"Entry zone near ${entry_low:.2f}-${entry_high:.2f} with invalidation below structural floor (${anchor_sup:.2f})."

    plan = TradePlan(
        direction=direction if overall_bias != "neutral" else "NEUTRAL",
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
