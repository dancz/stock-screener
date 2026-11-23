from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.api.serializers import (
    _normalize_trade_plan,
    get_latest_market_regime,
    get_watchlist_membership_map,
    latest_snapshot_subquery,
)
from app.db.models import AnalysisSnapshot, Ticker

HorizonType = Literal["all", "short_term", "medium_term"]

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
    days_to_earnings: int | None,
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

    index_adj = 6.0 if index_aligned else -3.0
    earnings_penalty = -8.0 if days_to_earnings is not None and 0 <= days_to_earnings <= 5 else 0.0

    fund_adj = 0.0
    if horizon == "medium_term":
        fund_data = plugin_results.get("fundamental_health")
        if isinstance(fund_data, dict) and fund_data.get("score") is not None:
            fund_score = float(fund_data["score"])
            if direction == "LONG":
                fund_adj = (fund_score - 50.0) * 0.15
            else:
                # Weak fundamentals slightly support medium-term short thesis; very strong fundamentals penalize it.
                fund_adj = (50.0 - fund_score) * 0.08

    conviction = base + consensus_adj + rr_bonus + stop_penalty + index_adj + earnings_penalty + fund_adj
    return round(max(0.0, min(100.0, conviction)), 1)


def _build_reasons(
    *,
    target_bias: str,
    signals: list[dict[str, Any]],
    plugin_results: dict[str, Any],
    days_to_earnings: int | None,
    trade_plan: dict[str, Any],
) -> list[dict[str, str]]:
    reasons: list[dict[str, str]] = []
    seen_categories: set[str] = set()
    seen_titles: set[str] = set()

    confirming_signals = [
        s for s in signals if isinstance(s, dict) and s.get("direction") == target_bias
    ]
    confirming_signals.sort(key=lambda s: float(s.get("score_impact") or 0.0), reverse=True)

    for sig in confirming_signals:
        if len(reasons) >= 5:
            break
        title = str(sig.get("label") or "").strip()
        detail = str(sig.get("detail") or "").strip()
        if not title or title.lower() in seen_titles:
            continue
        category = _categorize_signal(str(sig.get("code") or ""))
        reasons.append({"category": category, "title": title, "detail": detail or title})
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
    trade_plan: dict[str, Any],
    opposing_bias: str,
    signals: list[dict[str, Any]],
    risk_flags: list[dict[str, Any]],
    index_aligned: bool,
    days_to_earnings: int | None,
    potential_loss_pct: float,
) -> list[str]:
    watchouts: list[str] = []
    seen: set[str] = set()

    def add_watchout(text: str) -> None:
        cleaned = text.strip()
        if cleaned and cleaned.lower() not in seen:
            watchouts.append(cleaned)
            seen.add(cleaned.lower())

    if not index_aligned:
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

    opposing_high = [
        s
        for s in signals
        if isinstance(s, dict) and s.get("direction") == opposing_bias and s.get("severity") == "high"
    ]
    opposing_high.sort(key=lambda s: float(s.get("score_impact") or 0.0), reverse=True)
    if opposing_high:
        top_opp = opposing_high[0]
        label = str(top_opp.get("label") or "Opposing Level").strip()
        detail = str(top_opp.get("detail") or "").strip()
        add_watchout(f"Conflicting signal — {label}: {detail}" if detail else f"Conflicting signal — {label}")

    if 0.0 < potential_loss_pct < 1.0:
        add_watchout("Very tight calculated stop (<1%) — verify intraday ATR/noise before placing stop.")

    if not snap.is_deep_enriched:
        add_watchout(
            "Stage-1 technical snapshot — click 'Open Chart & Full Analysis' to refresh live quote, fundamentals & options."
        )

    return watchouts[:5]


def _extract_top_option(option_proposals: dict[str, Any], target_bias: str) -> dict[str, Any] | None:
    proposals = option_proposals.get("proposals")
    if not isinstance(proposals, list):
        return None
    for prop in proposals:
        if not isinstance(prop, dict):
            continue
        if prop.get("bias") == target_bias:
            return {
                "strategy_name": str(prop.get("strategy_name") or "Option Strategy"),
                "expiry": str(prop.get("expiry") or ""),
                "dte": int(prop.get("dte") or 0),
                "net_credit_debit": float(prop.get("net_credit_debit") or 0.0),
                "max_profit": float(prop["max_profit"]) if prop.get("max_profit") is not None else None,
                "max_loss": float(prop["max_loss"]) if prop.get("max_loss") is not None else None,
                "pop_estimate": float(prop["pop_estimate"]) if prop.get("pop_estimate") is not None else None,
                "iv_regime": str(prop.get("iv_regime") or "normal"),
                "rationale": str(prop.get("rationale") or ""),
                "earnings_warning": prop.get("earnings_warning"),
            }
    return None


def build_trade_ideas(
    session: Session,
    *,
    limit: int = 10,
    horizon: HorizonType = "all",
    min_rr: float = 1.5,
    exclude_earnings_days: int | None = None,
    index_aligned_only: bool = False,
    watchlist_only: bool = False,
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

    for snap, ticker in rows:
        wl_ids = wl_map.get(snap.symbol, [])
        if watchlist_only and not wl_ids:
            continue

        last_price = float(snap.last_price or 0.0)
        if not watchlist_only and last_price < min_price:
            continue

        raw_plan = _safe_json_loads(snap.trade_plan_json, {})
        trade_plan = _normalize_trade_plan(raw_plan)
        if not trade_plan:
            continue

        direction = trade_plan.get("direction")
        entry_low = float(trade_plan.get("entry_low") or 0.0)
        entry_high = float(trade_plan.get("entry_high") or entry_low)
        stop_loss = float(trade_plan.get("stop_loss") or 0.0)
        tp1 = float(trade_plan.get("tp1") or 0.0)
        tp2 = float(trade_plan.get("tp2") or 0.0)
        rr = float(trade_plan.get("risk_reward") or 0.0)

        if direction not in ("LONG", "SHORT") or entry_low <= 0 or stop_loss <= 0:
            continue
        if rr < min_rr:
            continue

        index_aligned = bool(trade_plan.get("index_aligned", False))
        if index_aligned_only and not index_aligned:
            continue

        events_insiders = _safe_json_loads(snap.events_insiders_json, {})
        days_to_earnings_raw = events_insiders.get("days_to_earnings")
        days_to_earnings = int(days_to_earnings_raw) if isinstance(days_to_earnings_raw, (int, float)) else None
        if exclude_earnings_days is not None and days_to_earnings is not None:
            if 0 <= days_to_earnings <= exclude_earnings_days:
                continue

        preset_scores = _safe_json_loads(snap.preset_scores_json, {})
        signals = _safe_json_loads(snap.signals_json, [])
        risk_flags = _safe_json_loads(snap.risk_flags_json, [])
        plugin_results = _safe_json_loads(snap.plugin_results_json, {})
        option_proposals = _safe_json_loads(snap.option_proposals_json, {})
        fundamentals = _safe_json_loads(snap.fundamentals_json, {})

        potential_gain_pct = float(trade_plan.get("potential_gain_pct") or 0.0)
        tp1_gain_pct = float(trade_plan.get("tp1_gain_pct") or 0.0)
        potential_loss_pct = float(trade_plan.get("potential_loss_pct") or 0.0)
        mid_entry = (entry_low + entry_high) / 2.0
        risk_per_share = float(
            trade_plan.get("risk_per_share") or round(max(abs(mid_entry - stop_loss), 0.01), 2)
        )

        conviction = _compute_conviction(
            snap=snap,
            direction=direction,
            horizon=horizon,
            preset_scores=preset_scores,
            plugin_results=plugin_results,
            rr=rr,
            potential_loss_pct=potential_loss_pct,
            index_aligned=index_aligned,
            days_to_earnings=days_to_earnings,
        )

        target_bias = "bullish" if direction == "LONG" else "bearish"
        opposing_bias = "bearish" if direction == "LONG" else "bullish"
        action_label = "Buy" if direction == "LONG" else "Short"
        setup_name = str(trade_plan.get("setup_name") or "Swing Technical Setup")

        reasons = _build_reasons(
            target_bias=target_bias,
            signals=signals,
            plugin_results=plugin_results,
            days_to_earnings=days_to_earnings,
            trade_plan=trade_plan,
        )
        watchouts = _build_watchouts(
            ticker=ticker,
            snap=snap,
            trade_plan=trade_plan,
            opposing_bias=opposing_bias,
            signals=signals,
            risk_flags=risk_flags,
            index_aligned=index_aligned,
            days_to_earnings=days_to_earnings,
            potential_loss_pct=potential_loss_pct,
        )
        top_option = _extract_top_option(option_proposals, target_bias)

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
            "change_pct": round(float(snap.change_pct or 0.0), 2),
            "analyzed_at": snap.analyzed_at.isoformat() if snap.analyzed_at else None,
            "is_deep_enriched": bool(snap.is_deep_enriched),
            "direction": direction,
            "action": "BUY" if direction == "LONG" else "SELL / SHORT",
            "conviction_score": conviction,
            "conviction_tier": _conviction_tier(conviction),
            "composite_score": round(float(snap.composite_score or 0.0), 1),
            "setup_name": setup_name,
            "thesis": thesis,
            "entry_low": round(entry_low, 2),
            "entry_high": round(entry_high, 2),
            "stop_loss": round(stop_loss, 2),
            "tp1": round(tp1, 2),
            "tp2": round(tp2, 2),
            "risk_reward": round(rr, 2),
            "risk_per_share": round(risk_per_share, 2),
            "potential_gain_pct": round(potential_gain_pct, 2),
            "tp1_gain_pct": round(tp1_gain_pct, 2),
            "potential_loss_pct": round(potential_loss_pct, 2),
            "dist_to_support_pct": trade_plan.get("dist_to_support_pct"),
            "dist_to_resistance_pct": trade_plan.get("dist_to_resistance_pct"),
            "index_aligned": index_aligned,
            "index_trend": str(trade_plan.get("index_trend") or "neutral"),
            "days_to_earnings": days_to_earnings,
            "fundamental_grade": fundamentals.get("health_grade"),
            "reasons": reasons,
            "watchouts": watchouts,
            "top_option": top_option,
            "watchlist_ids": wl_ids,
        }

        if direction == "LONG":
            buys.append(idea)
        else:
            sells.append(idea)

    sort_key = lambda x: (
        x["conviction_score"],
        bool(x["index_aligned"]),
        min(float(x["risk_reward"]), 4.5),
        x["composite_score"],
    )
    buys.sort(key=sort_key, reverse=True)
    sells.sort(key=sort_key, reverse=True)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "disclaimer": (
            "Advisory trade ideas derived from the latest stored screener snapshots. "
            "Always verify live price, chart structure, liquidity, and news before placing any broker order."
        ),
        "horizon": horizon,
        "min_rr": min_rr,
        "market_regime": get_latest_market_regime(session),
        "last_scan": None,
        "total_scanned": len(rows),
        "buys": buys[:limit],
        "sells": sells[:limit],
    }
