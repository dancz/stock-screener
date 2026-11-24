from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from sqlalchemy.orm import Session

from app.api.serializers import (
    _normalize_trade_plan,
    get_latest_market_regime,
    get_watchlist_membership_map,
    latest_snapshot_subquery,
)
from app.db.models import AnalysisSnapshot, Ticker
from app.plugins.sources.market_data_ibkr import fetch_live_quotes_batch_ibkr

HorizonType = Literal["all", "short_term", "medium_term"]

_LIVE_QUOTE_CACHE_LOCK = threading.Lock()
_LIVE_QUOTE_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_LIVE_QUOTE_TTL_SECONDS = 30.0

_LIVE_STATUS_RANK: dict[str | None, int] = {
    "IN_ZONE": 3,
    "BELOW_ENTRY": 2,
    "ABOVE_ENTRY": 2,
    None: 2,
    "MISSED_ENTRY": 1,
    "TARGET_HIT": 0,
    "STOP_HIT": -1,
}

_PLUGIN_CATEGORY_MAP: dict[str, str] = {
    "smart_money_zones": "Smart Money",
    "trend_channels": "Trend Channel",
    "price_action_sr": "Price Action & S/R",
    "rr_index_trend": "R:R & Index",
    "correlation_rs": "Relative Strength",
    "volume_volatility": "Volume",
    "fundamental_health": "Fundamentals",
    "insider_activity": "Insiders",
    "events_earnings": "Events",
}

_DIRECTIONAL_PLUGINS = (
    "price_action_sr",
    "smart_money_zones",
    "trend_channels",
    "rr_index_trend",
    "correlation_rs",
)

_SUPPLEMENT_PLUGIN_ORDER = (
    "smart_money_zones",
    "trend_channels",
    "price_action_sr",
    "rr_index_trend",
    "correlation_rs",
    "fundamental_health",
    "insider_activity",
    "volume_volatility",
)

_STRUCTURAL_CONFLICT_CODES = frozenset(
    {
        "LOWER_CHANNEL_TOUCH",
        "UPPER_CHANNEL_TOUCH",
        "CHANNEL_BREAKOUT_UP",
        "CHANNEL_BREAKDOWN",
        "AT_SUPPORT_ZONE",
        "AT_RESISTANCE_ZONE",
        "BULLISH_HAMMER",
        "BEARISH_PIN_BAR",
        "BULLISH_ENGULFING",
        "BEARISH_ENGULFING",
        "SMC_AT_BULLISH_OB",
        "SMC_AT_BEARISH_OB",
        "SMC_BULLISH_CHOCH",
        "SMC_BEARISH_CHOCH",
        "SR_BREAK_RETEST",
    }
)


def _safe_json_loads(raw: str | None, fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, type(fallback)) else fallback
    except (json.JSONDecodeError, TypeError):
        return fallback


def _categorize_signal(code: str) -> str:
    upper = (code or "").upper()
    if any(tok in upper for tok in ("SMC", "ORDER_BLOCK", "FVG", "BOS", "CHOCH", "DISCOUNT", "PREMIUM", "LIQUIDITY")):
        return "Smart Money"
    if "CHANNEL" in upper:
        return "Trend Channel"
    if any(tok in upper for tok in ("SUPPORT", "RESISTANCE", "SR_", "HAMMER", "ENGULF", "INSIDE_BAR", "BREAKOUT", "BREAKDOWN")):
        return "Price Action & S/R"
    if any(tok in upper for tok in ("ASYMMETRIC_RR", "INDEX_")):
        return "R:R & Index"
    if any(tok in upper for tok in ("RS_", "CORR", "BETA")):
        return "Relative Strength"
    if any(tok in upper for tok in ("VOL", "SQUEEZE", "RVOL", "POC")):
        return "Volume"
    if any(tok in upper for tok in ("HEALTH", "FUNDAMENTAL", "VALUATION", "GROWTH", "PROFIT", "MARGIN")):
        return "Fundamentals"
    if "INSIDER" in upper:
        return "Insiders"
    if any(tok in upper for tok in ("EARNINGS", "ANALYST", "EVENT")):
        return "Events"
    return "Technical Signal"


def _conviction_tier(score: float) -> str:
    if score >= 82.0:
        return "A+ High Conviction"
    if score >= 72.0:
        return "Strong Setup"
    if score >= 60.0:
        return "Actionable Watch"
    return "Speculative"


def _safe_float_val(val: Any) -> float | None:
    if val is None or isinstance(val, bool):
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _extract_corr_60(plugin_results: dict[str, Any]) -> float | None:
    for pid in ("correlation_rs", "rr_index_trend"):
        pdata = plugin_results.get(pid)
        if isinstance(pdata, dict):
            val = _safe_float_val(pdata.get("metrics", {}).get("corr_60d", {}).get("value"))
            if val is not None:
                return val
    return None


def _extract_insider_buy_usd(plugin_results: dict[str, Any], events_insiders: dict[str, Any]) -> float:
    ins_data = plugin_results.get("insider_activity")
    if isinstance(ins_data, dict):
        net_val = _safe_float_val(ins_data.get("metrics", {}).get("net_buy_usd", {}).get("value"))
        if net_val is not None:
            return net_val
    trades = events_insiders.get("insiders") or []
    return sum(
        max(0.0, _safe_float_val(t.get("value_usd")) or 0.0)
        for t in trades
        if isinstance(t, dict)
    )


def get_cached_live_quotes_ibkr(symbols: list[str], ibkr_cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Fetch batch IBKR quotes with a 30s thread-safe TTL cache (no-op if IBKR disabled)."""
    if not ibkr_cfg.get("enabled", False) or not symbols:
        return {}
    unique = [s.strip().upper().replace(".", "-") for s in dict.fromkeys(symbols) if s.strip()][:40]
    if not unique:
        return {}

    now = time.monotonic()
    out: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    with _LIVE_QUOTE_CACHE_LOCK:
        for sym in unique:
            cached = _LIVE_QUOTE_CACHE.get(sym)
            if cached and (now - cached[0]) < _LIVE_QUOTE_TTL_SECONDS:
                out[sym] = dict(cached[1])
            else:
                missing.append(sym)

    if missing:
        fetched = fetch_live_quotes_batch_ibkr(
            missing,
            host=str(ibkr_cfg.get("host", "127.0.0.1")),
            port=int(ibkr_cfg.get("port", 4002)),
            client_id=int(ibkr_cfg.get("client_id", 17)),
        )
        if fetched:
            stamp = time.monotonic()
            with _LIVE_QUOTE_CACHE_LOCK:
                for sym, quote in fetched.items():
                    _LIVE_QUOTE_CACHE[sym] = (stamp, dict(quote))
                    out[sym] = dict(quote)

    return out


def _compute_conviction(
    *,
    snap: AnalysisSnapshot,
    direction: Literal["LONG", "SHORT"],
    horizon: HorizonType,
    preset_scores: dict[str, Any],
    plugin_results: dict[str, Any],
    rr: float,
    potential_loss_pct: float,
    index_aligned: bool,
    corr_60: float | None = None,
    days_to_earnings: int | None,
    change_pct: float,
    high_opposing_count: int,
    other_opposing_count: int,
    med_opposing_count: int,
    stop_distance_atr: float | None,
    spot_rr_tp1: float | None,
    spot_rr_tp2: float | None,
    target_crosses_barrier: bool,
    stretched_zone: bool,
    has_major_conflict: bool,
    short_pct_float: float | None = None,
    has_insider_buy: bool = False,
    has_rs_leader: bool = False,
    health_grade: str | None = None,
    debt_to_equity: float | None = None,
) -> float:
    short_score = float(preset_scores.get("short_term_high_rr", snap.composite_score) or snap.composite_score or 0.0)
    medium_score = float(preset_scores.get("medium_term_invest", snap.composite_score) or snap.composite_score or 0.0)
    comp_score = float(snap.composite_score or 0.0)

    if horizon == "short_term":
        base = short_score
    elif horizon == "medium_term":
        base = medium_score
    else:
        base = 0.5 * comp_score + 0.5 * short_score

    target_bias = "bullish" if direction == "LONG" else "bearish"
    opposing_bias = "bearish" if direction == "LONG" else "bullish"

    confirming = 0
    conflicting = 0
    for pid in _DIRECTIONAL_PLUGINS:
        pdata = plugin_results.get(pid)
        if not isinstance(pdata, dict):
            continue
        pbias = pdata.get("bias")
        if pbias == target_bias:
            confirming += 1
        elif pbias == opposing_bias:
            conflicting += 1

    consensus_adj = min(12.0, confirming * 3.0) - min(10.0, conflicting * 3.5)

    capped_rr = min(max(rr, 0.0), 4.5)
    rr_bonus = max(0.0, (capped_rr - 1.5) * 3.5)

    stop_penalty = 0.0
    if 0.0 < potential_loss_pct < 0.8:
        stop_penalty = -10.0
    elif potential_loss_pct > 12.0:
        stop_penalty = -6.0

    # Structural conflict & realism penalties
    opposing_sig_penalty = (
        -min(30.0, high_opposing_count * 15.0)
        - min(12.0, other_opposing_count * 6.0)
        - min(12.0, med_opposing_count * 6.0)
    )
    atr_stop_penalty = -10.0 if (stop_distance_atr is not None and stop_distance_atr < 0.95) else 0.0
    spot_rr_penalty = 0.0
    if spot_rr_tp2 is not None and spot_rr_tp2 < 1.5:
        spot_rr_penalty -= 10.0
    if spot_rr_tp1 is not None and spot_rr_tp1 < 1.0:
        spot_rr_penalty -= 8.0
    barrier_penalty = -10.0 if target_crosses_barrier else 0.0
    stretch_penalty = -6.0 if stretched_zone else 0.0
    shock_penalty = -10.0 if abs(change_pct) >= 12.0 else 0.0

    if index_aligned:
        index_adj = 6.0
    elif corr_60 is not None and corr_60 < 0.35:
        index_adj = 0.0
    else:
        index_adj = -3.0

    earnings_penalty = -8.0 if days_to_earnings is not None and 0 <= days_to_earnings <= 5 else 0.0

    safety_adj = 0.0
    is_active_squeeze = bool(
        direction == "SHORT"
        and short_pct_float is not None
        and short_pct_float >= 20.0
        and change_pct >= 5.0
    )
    if direction == "SHORT":
        if short_pct_float is not None:
            if short_pct_float >= 20.0:
                safety_adj -= 16.0
            elif short_pct_float >= 15.0:
                safety_adj -= 8.0
        if health_grade in ("A+", "A") and has_rs_leader:
            safety_adj -= 14.0
        elif has_rs_leader:
            safety_adj -= 8.0
        if has_insider_buy:
            safety_adj -= 10.0
    else:
        if health_grade in ("D", "F"):
            safety_adj -= 15.0 if horizon == "medium_term" else 10.0
        if debt_to_equity is not None and debt_to_equity >= 300.0:
            safety_adj -= 8.0

    fund_adj = 0.0
    if horizon == "medium_term":
        fund_data = plugin_results.get("fundamental_health")
        if isinstance(fund_data, dict) and fund_data.get("score") is not None:
            fund_score = float(fund_data["score"])
            if direction == "LONG":
                fund_adj = (fund_score - 50.0) * 0.15
            else:
                fund_adj = (50.0 - fund_score) * 0.08

    conviction = (
        base
        + consensus_adj
        + rr_bonus
        + stop_penalty
        + opposing_sig_penalty
        + atr_stop_penalty
        + spot_rr_penalty
        + barrier_penalty
        + stretch_penalty
        + shock_penalty
        + index_adj
        + earnings_penalty
        + safety_adj
        + fund_adj
    )
    if is_active_squeeze:
        conviction = min(conviction, 60.0)
    elif has_major_conflict:
        conviction = min(conviction, 65.0)

    return round(max(0.0, min(100.0, conviction)), 1)


def _build_reasons(
    *,
    direction: Literal["LONG", "SHORT"],
    last_price: float,
    target_bias: str,
    signals: list[dict[str, Any]],
    plugin_results: dict[str, Any],
    days_to_earnings: int | None,
    trade_plan: dict[str, Any],
) -> list[dict[str, str]]:
    reasons: list[dict[str, str]] = []
    seen_categories: set[str] = set()
    seen_titles: set[str] = set()

    entry_low = float(trade_plan.get("entry_low") or 0.0)
    entry_high = float(trade_plan.get("entry_high") or entry_low)
    mid_entry = (entry_low + entry_high) / 2.0
    tp1 = float(trade_plan.get("tp1") or 0.0)
    tp2 = float(trade_plan.get("tp2") or 0.0)
    rr = float(trade_plan.get("risk_reward") or 0.0)
    tp1_rr = float(trade_plan.get("tp1_risk_reward") or 0.0)
    potential_gain_pct = float(trade_plan.get("potential_gain_pct") or 0.0)
    tp1_gain_pct = float(trade_plan.get("tp1_gain_pct") or 0.0)
    potential_loss_pct = float(trade_plan.get("potential_loss_pct") or 0.0)
    spot_rr_tp1 = trade_plan.get("spot_rr_tp1")
    spot_rr_tp2 = trade_plan.get("spot_rr_tp2")
    spot_risk_pct = trade_plan.get("spot_risk_pct")

    confirming_signals = [
        s for s in signals if isinstance(s, dict) and s.get("direction") == target_bias
    ]
    confirming_signals.sort(key=lambda s: float(s.get("score_impact") or 0.0), reverse=True)

    for sig in confirming_signals:
        if len(reasons) >= 5:
            break
        code = str(sig.get("code") or "")
        title = str(sig.get("label") or "").strip()
        detail = str(sig.get("detail") or "").strip()

        # Harmonize R:R reason text with unified TradePlan figures (never show conflicting 3rd set of numbers)
        if code in ("HIGH_ASYMMETRIC_RR_SHORT", "HIGH_ASYMMETRIC_RR_LONG"):
            title = f"{direction} Risk/Reward {rr:.2f}x (TP2 +{potential_gain_pct:.1f}% / -{potential_loss_pct:.1f}%)"
            if spot_rr_tp2 is not None and spot_rr_tp1 is not None and spot_risk_pct is not None:
                detail = (
                    f"Entry mid ${mid_entry:.2f} -> TP1 ${tp1:.2f} ({tp1_rr:.2f}R) & "
                    f"TP2 ${tp2:.2f} ({rr:.2f}R); at current spot ${last_price:.2f}: "
                    f"{float(spot_rr_tp2):.2f}R (TP1 {float(spot_rr_tp1):.2f}R, risk -{float(spot_risk_pct):.1f}%)."
                )
            else:
                detail = (
                    f"Entry mid ${mid_entry:.2f} -> TP1 ${tp1:.2f} ({tp1_rr:.2f}R, +{tp1_gain_pct:.1f}%) & "
                    f"TP2 ${tp2:.2f} ({rr:.2f}R, +{potential_gain_pct:.1f}%), risk -{potential_loss_pct:.1f}%."
                )

        if not title or title.lower() in seen_titles:
            continue
        category = _categorize_signal(code)
        reasons.append({"category": category, "title": title, "detail": detail})
        seen_titles.add(title.lower())
        seen_categories.add(category)

    for pid in _SUPPLEMENT_PLUGIN_ORDER:
        if len(reasons) >= 5:
            break
        pdata = plugin_results.get(pid)
        if not isinstance(pdata, dict):
            continue
        category = _PLUGIN_CATEGORY_MAP.get(pid, pid)
        if category in seen_categories:
            continue
        pscore = float(pdata.get("score") or 0.0)
        pbias = pdata.get("bias")
        summary = str(pdata.get("summary") or "").strip()
        if pbias == target_bias and pscore >= 65.0 and summary:
            title = str(pdata.get("display_name") or category)
            if pid == "rr_index_trend":
                summary = (
                    f"Entry mid R:R {rr:.2f}x (+{potential_gain_pct:.1f}% / -{potential_loss_pct:.1f}%) | "
                    f"Benchmark trend: {str(trade_plan.get('index_trend') or 'neutral').title()}"
                )
            if title.lower() not in seen_titles:
                reasons.append({"category": category, "title": title, "detail": summary})
                seen_titles.add(title.lower())
                seen_categories.add(category)

    if len(reasons) < 4 and days_to_earnings is not None and days_to_earnings > 21 and "Events" not in seen_categories:
        reasons.append(
            {
                "category": "Events",
                "title": "Clean Event Runway",
                "detail": f"Next earnings in {days_to_earnings} days — no immediate gap risk.",
            }
        )

    if not reasons:
        rationale = str(trade_plan.get("rationale") or "Aligned S/R and technical structure.")
        reasons.append(
            {
                "category": "Trade Setup",
                "title": str(trade_plan.get("setup_name") or "Swing Technical Setup"),
                "detail": rationale,
            }
        )

    return reasons[:5]


def _build_watchouts(
    *,
    ticker: Ticker,
    snap: AnalysisSnapshot,
    direction: Literal["LONG", "SHORT"],
    trade_plan: dict[str, Any],
    opposing_bias: str,
    signals: list[dict[str, Any]],
    risk_flags: list[dict[str, Any]],
    index_aligned: bool,
    corr_60: float | None = None,
    days_to_earnings: int | None,
    potential_loss_pct: float,
    change_pct: float,
    stop_distance_atr: float | None,
    spot_rr_tp1: float | None,
    target_crosses_barrier: bool,
    zone_pos: float | None,
    short_pct_float: float | None = None,
    has_insider_buy: bool = False,
    insider_buy_usd: float = 0.0,
    has_rs_leader: bool = False,
    health_grade: str | None = None,
    debt_to_equity: float | None = None,
) -> list[str]:
    watchouts: list[str] = []
    seen: set[str] = set()

    def add_watchout(text: str) -> None:
        cleaned = text.strip()
        if cleaned and cleaned.lower() not in seen:
            watchouts.append(cleaned)
            seen.add(cleaned.lower())

    opposing_high = [
        s
        for s in signals
        if isinstance(s, dict)
        and s.get("direction") == opposing_bias
        and (s.get("severity") == "high" or (direction == "LONG" and s.get("code") == "UPPER_CHANNEL_TOUCH"))
    ]
    opposing_high.sort(
        key=lambda s: (1 if s.get("code") in _STRUCTURAL_CONFLICT_CODES else 0, float(s.get("score_impact") or 0.0)),
        reverse=True,
    )
    for opp in opposing_high[:2]:
        label = str(opp.get("label") or "Opposing Level").strip()
        detail = str(opp.get("detail") or "").strip()
        add_watchout(f"Conflicting signal — {label}: {detail}" if detail else f"Conflicting signal — {label}")

    if direction == "SHORT":
        if short_pct_float is not None and short_pct_float >= 20.0:
            if change_pct >= 5.0:
                add_watchout(
                    f"Active short-squeeze risk ({short_pct_float:.1f}% short float, {change_pct:+.1f}% 1D surge) — avoid shorting equity directly."
                )
            else:
                add_watchout(
                    f"High short interest ({short_pct_float:.1f}% of float) — elevated short-squeeze risk; avoid crowded equity short."
                )
        elif short_pct_float is not None and short_pct_float >= 15.0:
            add_watchout(f"Elevated short interest ({short_pct_float:.1f}% of float) — monitor squeeze risk.")

        if has_rs_leader:
            if health_grade in ("A+", "A"):
                add_watchout(
                    f"Shorting a Grade {health_grade} RS Leader outperforming its benchmark — fighting institutional accumulation."
                )
            else:
                add_watchout(
                    "Shorting an RS Leader outperforming its benchmark — confirm trend breakdown first."
                )

        if has_insider_buy:
            if insider_buy_usd > 0:
                add_watchout(f"Recent open-market insider buying (${insider_buy_usd:,.0f}) opposes SHORT thesis.")
            else:
                add_watchout("Recent open-market insider buying opposes SHORT thesis.")
    else:
        if health_grade in ("D", "F"):
            add_watchout(
                f"Weak fundamental health (Grade {health_grade}) — speculative profitability / balance-sheet profile."
            )
        if debt_to_equity is not None and debt_to_equity >= 300.0:
            add_watchout(
                f"Extreme balance-sheet leverage (Debt/Equity {debt_to_equity:.0f}%) — heightened financing/dilution risk."
            )

    if direction == "SHORT" and zone_pos is not None and zone_pos <= 20.0:
        add_watchout(
            f"Price is already in a deep Discount zone ({zone_pos:.0f}% of swing range) — downside may be stretched; prefer bounce into supply."
        )
    elif direction == "LONG" and zone_pos is not None and zone_pos >= 80.0:
        add_watchout(
            f"Price is already in a deep Premium zone ({zone_pos:.0f}% of swing range) — upside may be stretched; prefer pullback into demand."
        )

    if abs(change_pct) >= 10.0:
        add_watchout(
            f"Extreme 1D move ({change_pct:+.1f}%) — high gap/mean-reversion noise; verify news & catalysts before entering."
        )

    if target_crosses_barrier:
        add_watchout("TP1 lies beyond a major opposing channel/S&R boundary — confirm breakout momentum first.")

    if stop_distance_atr is not None and stop_distance_atr < 1.0:
        add_watchout(
            f"Stop distance from current price is {stop_distance_atr:.2f}x ATR (<1.0 ATR) — elevated risk of noise stop-out."
        )

    entry_low = float(trade_plan.get("entry_low") or 0.0)
    entry_high = float(trade_plan.get("entry_high") or entry_low)
    mid_entry = (entry_low + entry_high) / 2.0
    spot_price = float(snap.last_price or 0.0)
    tp1 = float(trade_plan.get("tp1") or 0.0)
    if spot_rr_tp1 is not None and spot_rr_tp1 < 1.0:
        at_or_better_than_mid = (direction == "LONG" and spot_price <= mid_entry) or (
            direction == "SHORT" and spot_price >= mid_entry
        )
        if at_or_better_than_mid:
            add_watchout(
                f"TP1 (${tp1:.2f}) sits close ({spot_rr_tp1:.2f}R at current price) — manage partial exit or confirm room to TP2."
            )
        else:
            add_watchout(
                f"Entering at current spot (${spot_price:.2f}) offers only {spot_rr_tp1:.2f}R to TP1 "
                f"(${tp1:.2f}) — wait for a limit fill inside ${entry_low:.2f}-${entry_high:.2f}."
            )

    if not index_aligned and (corr_60 is None or corr_60 >= 0.35):
        idx_trend = str(trade_plan.get("index_trend") or "neutral").title()
        add_watchout(
            f"Counter-trend vs {ticker.primary_benchmark} ({idx_trend}) — wait for trigger confirmation and size conservatively."
        )

    earnings_mentioned = False
    for rf in risk_flags:
        if not isinstance(rf, dict):
            continue
        label = str(rf.get("label") or "").strip()
        detail = str(rf.get("detail") or "").strip()
        if "earning" in label.lower() or "earning" in detail.lower():
            earnings_mentioned = True
        msg = f"{label}: {detail}" if label and detail else (label or detail)
        add_watchout(msg)

    if days_to_earnings is not None and 0 <= days_to_earnings <= 14 and not earnings_mentioned:
        add_watchout(
            f"Upcoming earnings in {days_to_earnings} day(s) — verify event risk or exit before announcement."
        )

    if 0.0 < potential_loss_pct < 1.0:
        add_watchout("Very tight calculated stop (<1%) — verify intraday ATR/noise before placing stop.")

    if not snap.is_deep_enriched:
        add_watchout(
            "Stage-1 technical snapshot — click 'Open Chart & Full Analysis' to refresh live quote, fundamentals & options."
        )

    return watchouts[:7]


def _extract_top_option(
    option_proposals: dict[str, Any],
    target_bias: str,
    *,
    direction: Literal["LONG", "SHORT"] = "LONG",
    spot_price: float = 0.0,
    tp1: float = 0.0,
    tp2: float = 0.0,
) -> dict[str, Any] | None:
    proposals = option_proposals.get("proposals")
    if not isinstance(proposals, list):
        return None
    for prop in proposals:
        if not isinstance(prop, dict) or prop.get("bias") != target_bias:
            continue

        raw_pop = prop.get("pop_estimate")
        pop_pct: float | None = None
        if raw_pop is not None:
            val = float(raw_pop)
            norm = val * 100.0 if 0.0 < val <= 1.0 else val
            pop_pct = round(norm, 1)
            # Filter out broken delta underflows (e.g., PoP 2.1% or 99.0%)
            if pop_pct < 10.0 or pop_pct >= 98.0:
                continue

        breakeven = [round(float(b), 2) for b in (prop.get("breakeven") or []) if b is not None]
        net_cd = float(prop.get("net_credit_debit") or 0.0)
        if net_cd < 0 and breakeven:
            be = breakeven[0]
            if direction == "LONG" and (
                (tp2 > 0 and be >= tp2 * 0.99) or (spot_price > 0 and be > spot_price * 1.15)
            ):
                continue
            if direction == "SHORT" and (
                (tp2 > 0 and be <= tp2 * 1.01) or (spot_price > 0 and be < spot_price * 0.85)
            ):
                continue

        legs_out: list[dict[str, Any]] = []
        for leg in prop.get("legs") or []:
            if not isinstance(leg, dict):
                continue
            legs_out.append(
                {
                    "action": str(leg.get("action") or ""),
                    "option_type": str(leg.get("option_type") or ""),
                    "strike": float(leg.get("strike") or 0.0),
                    "mid_price": float(leg.get("mid_price") or 0.0),
                    "delta": round(float(leg["delta"]), 3) if leg.get("delta") is not None else None,
                }
            )

        return {
            "strategy_name": str(prop.get("strategy_name") or "Option Strategy"),
            "expiry": str(prop.get("expiry") or ""),
            "dte": int(prop.get("dte") or 0),
            "legs": legs_out,
            "breakeven": breakeven,
            "net_credit_debit": net_cd,
            "max_profit": float(prop["max_profit"]) if prop.get("max_profit") is not None else None,
            "max_loss": float(prop["max_loss"]) if prop.get("max_loss") is not None else None,
            "pop_estimate": round(min(97.9, max(10.0, pop_pct)), 1) if pop_pct is not None else None,
            "iv_regime": str(prop.get("iv_regime") or "normal"),
            "rationale": str(prop.get("rationale") or ""),
            "earnings_warning": prop.get("earnings_warning"),
        }
    return None


def apply_live_quote_to_idea(idea: dict[str, Any], quote: dict[str, Any] | None) -> dict[str, Any]:
    """Overlay live quote drift, entry-zone status, and live R:R onto a TradeIdea dict."""
    base_live = {
        "live_price": None,
        "live_change_pct": None,
        "live_source": None,
        "price_drift_pct": None,
        "live_status": None,
        "live_risk_pct": None,
        "live_rr_tp1": None,
        "live_rr_tp2": None,
    }
    if not quote:
        idea.update(base_live)
        return idea

    live_price = _safe_float_val(quote.get("price"))
    if live_price is None or live_price <= 0:
        idea.update(base_live)
        return idea

    snap_price = float(idea.get("last_price") or 0.0)
    entry_low = float(idea.get("entry_low") or 0.0)
    entry_high = float(idea.get("entry_high") or entry_low)
    stop_loss = float(idea.get("stop_loss") or 0.0)
    tp1 = float(idea.get("tp1") or 0.0)
    tp2 = float(idea.get("tp2") or 0.0)
    direction = str(idea.get("direction") or "LONG")

    drift_pct = round(((live_price - snap_price) / snap_price) * 100.0, 2) if snap_price > 0 else 0.0
    live_chg = _safe_float_val(quote.get("change_pct"))

    if direction == "LONG":
        if stop_loss > 0 and live_price <= stop_loss:
            status = "STOP_HIT"
        elif tp1 > 0 and live_price >= tp1:
            status = "TARGET_HIT"
        elif live_price > entry_high * 1.005:
            status = "MISSED_ENTRY"
        elif live_price < entry_low * 0.995:
            status = "BELOW_ENTRY"
        else:
            status = "IN_ZONE"
        live_risk = live_price - stop_loss
        rew1 = max(0.0, tp1 - live_price)
        rew2 = max(0.0, tp2 - live_price)
    else:
        if stop_loss > 0 and live_price >= stop_loss:
            status = "STOP_HIT"
        elif tp1 > 0 and live_price <= tp1:
            status = "TARGET_HIT"
        elif live_price < entry_low * 0.995:
            status = "MISSED_ENTRY"
        elif live_price > entry_high * 1.005:
            status = "ABOVE_ENTRY"
        else:
            status = "IN_ZONE"
        live_risk = stop_loss - live_price
        rew1 = max(0.0, live_price - tp1)
        rew2 = max(0.0, live_price - tp2)

    if live_risk > 0:
        live_risk_pct: float | None = round((live_risk / live_price) * 100.0, 2)
        live_rr_tp1: float | None = round(rew1 / live_risk, 2)
        live_rr_tp2: float | None = round(rew2 / live_risk, 2)
    else:
        live_risk_pct = None
        live_rr_tp1 = None
        live_rr_tp2 = None

    watchouts = list(idea.get("watchouts") or [])
    conviction = float(idea.get("conviction_score") or 0.0)
    prepend_msg: str | None = None

    if status == "STOP_HIT":
        prepend_msg = f"Live price (${live_price:.2f}) already breached Stop-Loss (${stop_loss:.2f}) — setup invalidated."
        conviction = min(conviction, 40.0)
    elif status == "TARGET_HIT":
        prepend_msg = f"Live price (${live_price:.2f}) already reached/passed Target 1 (${tp1:.2f}) — setup is stale, do not chase."
        conviction = min(conviction, 45.0)
    elif status == "MISSED_ENTRY":
        rr_str = f"{live_rr_tp1:.2f}R" if live_rr_tp1 is not None else "reduced R:R"
        prepend_msg = (
            f"Live price (${live_price:.2f}, {drift_pct:+.1f}% vs snapshot) moved past Entry Zone — "
            f"live R:R to TP1 is {rr_str}; wait for pullback to limit zone."
        )
        if live_rr_tp1 is not None and live_rr_tp1 < 0.8:
            conviction = max(0.0, conviction - 8.0)
    elif status in ("BELOW_ENTRY", "ABOVE_ENTRY") and stop_loss > 0:
        entry_mid = (entry_low + entry_high) / 2.0
        entry_stop_dist = abs(entry_mid - stop_loss)
        if entry_stop_dist > 0 and 0 < live_risk <= 0.45 * entry_stop_dist:
            prepend_msg = (
                f"Live price (${live_price:.2f}, {drift_pct:+.1f}% vs snapshot) moved close to Stop-Loss "
                f"(${stop_loss:.2f}) — verify rejection/reversal before ordering."
            )

    if prepend_msg:
        watchouts = [prepend_msg] + [w for w in watchouts if w != prepend_msg]

    conviction = round(max(0.0, min(100.0, conviction)), 1)
    idea.update(
        {
            "live_price": round(live_price, 2),
            "live_change_pct": round(live_chg, 2) if live_chg is not None else None,
            "live_source": str(quote.get("source") or "IBKR Gateway"),
            "price_drift_pct": drift_pct,
            "live_status": status,
            "live_risk_pct": live_risk_pct,
            "live_rr_tp1": live_rr_tp1,
            "live_rr_tp2": live_rr_tp2,
            "conviction_score": conviction,
            "conviction_tier": _conviction_tier(conviction),
            "watchouts": watchouts[:7],
        }
    )
    return idea


def build_trade_ideas(
    session: Session,
    *,
    limit: int = 10,
    horizon: HorizonType = "all",
    min_rr: float = 1.5,
    exclude_earnings_days: int | None = None,
    index_aligned_only: bool = False,
    watchlist_only: bool = False,
    hide_conflicted: bool = True,
    hide_stale: bool = True,
    live_quotes: dict[str, dict[str, Any]] | None = None,
    live_quote_fetcher: Callable[[list[str]], dict[str, dict[str, Any]]] | None = None,
    min_price: float = 5.0,
) -> dict[str, Any]:
    """Build advisory Buy (LONG) and Sell (SHORT) trade ideas from stored SQLite snapshots."""
    subq = latest_snapshot_subquery(session)
    rows = (
        session.query(AnalysisSnapshot, Ticker)
        .join(subq, AnalysisSnapshot.id == subq.c.max_id)
        .join(Ticker, Ticker.symbol == AnalysisSnapshot.symbol)
        .all()
    )
    wl_map = get_watchlist_membership_map(session)

    buys: list[dict[str, Any]] = []
    sells: list[dict[str, Any]] = []
    filtered_conflicted_count = 0

    for snap, ticker in rows:
        wl_ids = wl_map.get(snap.symbol, [])
        if watchlist_only and not wl_ids:
            continue

        last_price = float(snap.last_price or 0.0)
        if not watchlist_only and last_price < min_price:
            continue

        raw_plan = _safe_json_loads(snap.trade_plan_json, {})
        trade_plan = _normalize_trade_plan(raw_plan, spot_price=last_price)
        if not trade_plan:
            continue

        direction = trade_plan.get("direction")
        entry_low = float(trade_plan.get("entry_low") or 0.0)
        entry_high = float(trade_plan.get("entry_high") or entry_low)
        stop_loss = float(trade_plan.get("stop_loss") or 0.0)
        tp1 = float(trade_plan.get("tp1") or 0.0)
        tp2 = float(trade_plan.get("tp2") or 0.0)
        rr = float(trade_plan.get("risk_reward") or 0.0)
        tp1_rr = float(trade_plan.get("tp1_risk_reward") or 0.0)

        if direction not in ("LONG", "SHORT") or entry_low <= 0 or stop_loss <= 0:
            continue
        if rr < min_rr:
            continue

        preset_scores = _safe_json_loads(snap.preset_scores_json, {})
        signals = _safe_json_loads(snap.signals_json, [])
        risk_flags = _safe_json_loads(snap.risk_flags_json, [])
        plugin_results = _safe_json_loads(snap.plugin_results_json, {})
        option_proposals = _safe_json_loads(snap.option_proposals_json, {})
        fundamentals = _safe_json_loads(snap.fundamentals_json, {})
        events_insiders = _safe_json_loads(snap.events_insiders_json, {})

        corr_60 = _extract_corr_60(plugin_results)
        index_aligned = bool(trade_plan.get("index_aligned", False))
        if corr_60 is not None and corr_60 < 0.35:
            index_aligned = False
        if index_aligned_only and not index_aligned:
            continue

        days_to_earnings_raw = events_insiders.get("days_to_earnings")
        days_to_earnings = int(days_to_earnings_raw) if isinstance(days_to_earnings_raw, (int, float)) else None
        if exclude_earnings_days is not None and days_to_earnings is not None:
            if 0 <= days_to_earnings <= exclude_earnings_days:
                continue

        potential_gain_pct = float(trade_plan.get("potential_gain_pct") or 0.0)
        tp1_gain_pct = float(trade_plan.get("tp1_gain_pct") or 0.0)
        potential_loss_pct = float(trade_plan.get("potential_loss_pct") or 0.0)
        spot_risk_pct = trade_plan.get("spot_risk_pct")
        spot_rr_tp1 = trade_plan.get("spot_rr_tp1")
        spot_rr_tp2 = trade_plan.get("spot_rr_tp2")
        stop_distance_atr = trade_plan.get("stop_distance_atr")
        target_crosses_barrier = bool(trade_plan.get("target_crosses_barrier", False))

        mid_entry = (entry_low + entry_high) / 2.0
        risk_per_share = float(
            trade_plan.get("risk_per_share") or round(max(abs(mid_entry - stop_loss), 0.01), 2)
        )
        change_pct = round(float(snap.change_pct or 0.0), 2)

        target_bias = "bullish" if direction == "LONG" else "bearish"
        opposing_bias = "bearish" if direction == "LONG" else "bullish"

        structural_high_opposing = [
            s
            for s in signals
            if isinstance(s, dict)
            and s.get("direction") == opposing_bias
            and (
                (s.get("severity") == "high" and s.get("code") in _STRUCTURAL_CONFLICT_CODES)
                or (direction == "LONG" and s.get("code") == "UPPER_CHANNEL_TOUCH")
            )
        ]
        other_high_opposing = [
            s
            for s in signals
            if isinstance(s, dict)
            and s.get("direction") == opposing_bias
            and s.get("severity") == "high"
            and s.get("code") not in _STRUCTURAL_CONFLICT_CODES
            and not (direction == "LONG" and s.get("code") == "UPPER_CHANNEL_TOUCH")
        ]
        med_opposing = [
            s
            for s in signals
            if isinstance(s, dict)
            and s.get("direction") == opposing_bias
            and s.get("severity") == "medium"
            and not (direction == "LONG" and s.get("code") == "UPPER_CHANNEL_TOUCH")
        ]

        smc_zone_raw = (
            plugin_results.get("smart_money_zones", {})
            .get("metrics", {})
            .get("zone_position", {})
            .get("value")
        )
        zone_pos = float(smc_zone_raw) if isinstance(smc_zone_raw, (int, float)) else None
        stretched_zone = bool(
            zone_pos is not None
            and ((direction == "SHORT" and zone_pos <= 20.0) or (direction == "LONG" and zone_pos >= 80.0))
        )

        short_pct_float = _safe_float_val(fundamentals.get("short_pct_float"))
        debt_to_equity = _safe_float_val(fundamentals.get("debt_to_equity"))
        health_grade = fundamentals.get("health_grade") or (
            plugin_results.get("fundamental_health", {})
            .get("metrics", {})
            .get("health_grade", {})
            .get("value")
            if isinstance(plugin_results.get("fundamental_health"), dict)
            else None
        )
        insider_buy_usd = _extract_insider_buy_usd(plugin_results, events_insiders)
        has_insider_buy = bool(
            any(
                isinstance(s, dict) and s.get("code") in ("INSIDER_CLUSTER_BUY", "CEO_CFO_BUY")
                for s in signals
            )
            or insider_buy_usd >= 100_000.0
        )
        has_rs_leader = any(isinstance(s, dict) and s.get("code") == "RS_LEADER" for s in signals)

        active_short_squeeze = bool(
            direction == "SHORT"
            and short_pct_float is not None
            and short_pct_float >= 20.0
            and change_pct >= 5.0
        )
        shorting_quality_leader = bool(direction == "SHORT" and health_grade in ("A+", "A") and has_rs_leader)
        extreme_move = abs(change_pct) >= 12.0
        has_major_conflict = bool(
            structural_high_opposing
            or extreme_move
            or target_crosses_barrier
            or active_short_squeeze
            or shorting_quality_leader
        )

        if hide_conflicted and has_major_conflict:
            filtered_conflicted_count += 1
            continue

        conviction = _compute_conviction(
            snap=snap,
            direction=direction,
            horizon=horizon,
            preset_scores=preset_scores,
            plugin_results=plugin_results,
            rr=rr,
            potential_loss_pct=potential_loss_pct,
            index_aligned=index_aligned,
            corr_60=corr_60,
            days_to_earnings=days_to_earnings,
            change_pct=change_pct,
            high_opposing_count=len(structural_high_opposing),
            other_opposing_count=len(other_high_opposing),
            med_opposing_count=len(med_opposing),
            stop_distance_atr=stop_distance_atr,
            spot_rr_tp1=spot_rr_tp1,
            spot_rr_tp2=spot_rr_tp2,
            target_crosses_barrier=target_crosses_barrier,
            stretched_zone=stretched_zone,
            has_major_conflict=has_major_conflict,
            short_pct_float=short_pct_float,
            has_insider_buy=has_insider_buy,
            has_rs_leader=has_rs_leader,
            health_grade=health_grade,
            debt_to_equity=debt_to_equity,
        )

        action_label = "Buy" if direction == "LONG" else "Short"
        setup_name = str(trade_plan.get("setup_name") or "Swing Technical Setup")

        reasons = _build_reasons(
            direction=direction,
            last_price=last_price,
            target_bias=target_bias,
            signals=signals,
            plugin_results=plugin_results,
            days_to_earnings=days_to_earnings,
            trade_plan=trade_plan,
        )
        watchouts = _build_watchouts(
            ticker=ticker,
            snap=snap,
            direction=direction,
            trade_plan=trade_plan,
            opposing_bias=opposing_bias,
            signals=signals,
            risk_flags=risk_flags,
            index_aligned=index_aligned,
            corr_60=corr_60,
            days_to_earnings=days_to_earnings,
            potential_loss_pct=potential_loss_pct,
            change_pct=change_pct,
            stop_distance_atr=stop_distance_atr,
            spot_rr_tp1=spot_rr_tp1,
            target_crosses_barrier=target_crosses_barrier,
            zone_pos=zone_pos,
            short_pct_float=short_pct_float,
            has_insider_buy=has_insider_buy,
            insider_buy_usd=insider_buy_usd,
            has_rs_leader=has_rs_leader,
            health_grade=health_grade,
            debt_to_equity=debt_to_equity,
        )
        top_option = _extract_top_option(
            option_proposals,
            target_bias,
            direction=direction,
            spot_price=last_price,
            tp1=tp1,
            tp2=tp2,
        )

        thesis = (
            f"{action_label} {snap.symbol} around ${entry_low:.2f}-${entry_high:.2f} "
            f"(stop ${stop_loss:.2f}) targeting ${tp1:.2f} / ${tp2:.2f} for {rr:.2f}R "
            f"via {setup_name}."
        )

        idea: dict[str, Any] = {
            "symbol": snap.symbol,
            "name": ticker.name or snap.symbol,
            "sector": ticker.sector or "",
            "primary_benchmark": ticker.primary_benchmark,
            "last_price": round(last_price, 2),
            "change_pct": change_pct,
            "analyzed_at": snap.analyzed_at.isoformat() if snap.analyzed_at else None,
            "is_deep_enriched": bool(snap.is_deep_enriched),
            "direction": direction,
            "action": "BUY" if direction == "LONG" else "SELL / SHORT",
            "conviction_score": conviction,
            "conviction_tier": _conviction_tier(conviction),
            "composite_score": round(float(snap.composite_score or 0.0), 1),
            "has_conflict": has_major_conflict,
            "setup_name": setup_name,
            "thesis": thesis,
            "entry_low": round(entry_low, 2),
            "entry_high": round(entry_high, 2),
            "stop_loss": round(stop_loss, 2),
            "tp1": round(tp1, 2),
            "tp2": round(tp2, 2),
            "risk_reward": round(rr, 2),
            "tp1_risk_reward": round(tp1_rr, 2),
            "risk_per_share": round(risk_per_share, 2),
            "potential_gain_pct": round(potential_gain_pct, 2),
            "tp1_gain_pct": round(tp1_gain_pct, 2),
            "potential_loss_pct": round(potential_loss_pct, 2),
            "spot_risk_pct": round(float(spot_risk_pct), 2) if spot_risk_pct is not None else None,
            "spot_rr_tp1": round(float(spot_rr_tp1), 2) if spot_rr_tp1 is not None else None,
            "spot_rr_tp2": round(float(spot_rr_tp2), 2) if spot_rr_tp2 is not None else None,
            "stop_distance_atr": round(float(stop_distance_atr), 2) if stop_distance_atr is not None else None,
            "dist_to_support_pct": trade_plan.get("dist_to_support_pct"),
            "dist_to_resistance_pct": trade_plan.get("dist_to_resistance_pct"),
            "index_aligned": index_aligned,
            "index_trend": str(trade_plan.get("index_trend") or "neutral"),
            "days_to_earnings": days_to_earnings,
            "fundamental_grade": health_grade,
            "live_price": None,
            "live_change_pct": None,
            "live_source": None,
            "price_drift_pct": None,
            "live_status": None,
            "live_risk_pct": None,
            "live_rr_tp1": None,
            "live_rr_tp2": None,
            "reasons": reasons,
            "watchouts": watchouts,
            "top_option": top_option,
            "watchlist_ids": wl_ids,
        }

        if direction == "LONG":
            buys.append(idea)
        else:
            sells.append(idea)

    base_sort_key = lambda x: (
        x["conviction_score"],
        bool(x["index_aligned"]),
        min(float(x["risk_reward"]), 4.5),
        x["composite_score"],
    )
    buys.sort(key=base_sort_key, reverse=True)
    sells.sort(key=base_sort_key, reverse=True)

    pool_cap = min(max(limit * 2, limit), 20)
    buy_pool = buys[:pool_cap]
    sell_pool = sells[:pool_cap]

    quotes_map = live_quotes
    if quotes_map is None and live_quote_fetcher is not None:
        interleaved: list[str] = []
        for b_item, s_item in zip(buy_pool, sell_pool):
            interleaved.append(b_item["symbol"])
            interleaved.append(s_item["symbol"])
        interleaved.extend(x["symbol"] for x in buy_pool[len(sell_pool) :])
        interleaved.extend(x["symbol"] for x in sell_pool[len(buy_pool) :])
        candidate_syms = list(dict.fromkeys(interleaved))
        quotes_map = live_quote_fetcher(candidate_syms) if candidate_syms else {}
    quotes_map = quotes_map or {}

    filtered_stale_count = 0
    if quotes_map:
        def _refine(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
            nonlocal filtered_stale_count
            refined: list[dict[str, Any]] = []
            for item in items:
                apply_live_quote_to_idea(item, quotes_map.get(item["symbol"]))
                if hide_stale and item.get("live_status") in ("STOP_HIT", "TARGET_HIT"):
                    filtered_stale_count += 1
                    continue
                refined.append(item)
            refined.sort(
                key=lambda x: (
                    _LIVE_STATUS_RANK.get(x.get("live_status"), 2),
                    x["conviction_score"],
                    bool(x["index_aligned"]),
                    min(float(x["risk_reward"]), 4.5),
                    x["composite_score"],
                ),
                reverse=True,
            )
            return refined

        buy_pool = _refine(buy_pool)
        sell_pool = _refine(sell_pool)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "disclaimer": (
            "Advisory trade ideas derived from the latest stored screener snapshots. "
            "Always verify live price, chart structure, liquidity, and news before placing any broker order."
        ),
        "horizon": horizon,
        "min_rr": min_rr,
        "hide_conflicted": hide_conflicted,
        "filtered_conflicted_count": filtered_conflicted_count,
        "hide_stale": hide_stale,
        "filtered_stale_count": filtered_stale_count,
        "live_quotes_checked": bool(quotes_map),
        "market_regime": get_latest_market_regime(session),
        "last_scan": None,
        "total_scanned": len(rows),
        "buys": buy_pool[:limit],
        "sells": sell_pool[:limit],
    }
