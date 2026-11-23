from __future__ import annotations

from typing import Any

from app.core.contracts import AnalyzerResult, ChartOverlay, RiskFlag, Signal

SEVERITY_ORDER = {"high": 0, "warning": 1, "medium": 1, "low": 2, "info": 2}


def compute_scores_and_summary(
    results: dict[str, AnalyzerResult],
    runtime_settings: dict[str, Any],
) -> dict[str, Any]:
    presets_cfg: dict[str, dict[str, Any]] = runtime_settings.get("presets", {})
    preset_scores: dict[str, float] = {}

    for preset_id, cfg in presets_cfg.items():
        weights: dict[str, float] = cfg.get("weights", {})
        total_w = 0.0
        weighted_sum = 0.0
        for pid, res in results.items():
            w = float(weights.get(pid, 0.05))
            if w <= 0:
                continue
            weighted_sum += res.score * w
            total_w += w
        preset_scores[preset_id] = round(weighted_sum / total_w, 1) if total_w > 0 else 50.0

    composite_score = preset_scores.get("all_around_swing")
    if composite_score is None:
        composite_score = round(sum(r.score for r in results.values()) / max(len(results), 1), 1)

    # Bias aggregation weighted by structural importance & confidence
    bias_weight_map = {
        "price_action_sr": 1.4,
        "smart_money_zones": 1.6,
        "rr_index_trend": 1.4,
        "trend_channels": 1.5,
        "volume_volatility": 1.1,
        "correlation_rs": 1.0,
        "fundamental_health": 0.7,
        "events_earnings": 0.4,
        "insider_activity": 0.7,
    }
    net_vote = 0.0
    total_vote_w = 0.0
    for pid, res in results.items():
        if res.bias == "neutral":
            continue
        sign = 1.0 if res.bias == "bullish" else -1.0
        w = bias_weight_map.get(pid, 1.0) * max(res.confidence, 0.25) * (res.score / 50.0)
        net_vote += sign * w
        total_vote_w += w

    if total_vote_w == 0:
        overall_bias = "neutral"
    else:
        ratio = net_vote / total_vote_w
        if ratio >= 0.18:
            overall_bias = "bullish"
        elif ratio <= -0.18:
            overall_bias = "bearish"
        else:
            overall_bias = "neutral"

    # Collect signals and risk flags
    all_signals: list[Signal] = []
    all_risks: list[RiskFlag] = []
    all_overlays: list[ChartOverlay] = []
    seen_risks: set[str] = set()

    for res in results.values():
        all_signals.extend(res.signals)
        all_overlays.extend(res.overlays)
        for rf in res.risk_flags:
            if rf.code not in seen_risks:
                seen_risks.add(rf.code)
                all_risks.append(rf)

    all_signals.sort(key=lambda s: (SEVERITY_ORDER.get(s.severity, 2), -s.score_impact))
    all_risks.sort(key=lambda r: SEVERITY_ORDER.get(r.severity, 2))

    return {
        "composite_score": round(composite_score, 1),
        "preset_scores": preset_scores,
        "bias": overall_bias,
        "signals": all_signals,
        "risk_flags": all_risks,
        "overlays": all_overlays,
    }
