from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, Field


class DiscoveredTicker(BaseModel):
    symbol: str
    name: str = ""
    sector: str = ""
    industry: str = ""
    source_tag: str = "universe"


class Signal(BaseModel):
    code: str
    label: str
    direction: Literal["bullish", "bearish", "neutral"] = "neutral"
    severity: Literal["high", "medium", "low"] = "medium"
    score_impact: float = 0.0
    detail: str = ""


class RiskFlag(BaseModel):
    code: str
    label: str
    severity: Literal["high", "warning", "info"] = "warning"
    detail: str = ""


class MetricItem(BaseModel):
    label: str
    value: float | str | bool | None = None
    formatted: str = ""
    status: Literal["good", "warn", "bad", "neutral"] = "neutral"


class ChartOverlay(BaseModel):
    id: str
    layer: Literal[
        "channels",
        "sr_zones",
        "smart_money",
        "price_action",
        "trade_plan",
        "emas",
        "volume_profile",
        "volume",
    ]
    kind: Literal[
        "horizontal_zone",
        "channel",
        "trend_line",
        "marker",
        "line_series",
        "price_box",
    ]
    label: str = ""
    color: str = "#38bdf8"
    data: dict[str, Any] = Field(default_factory=dict)


class TradeHints(BaseModel):
    nearest_support: float | None = None
    nearest_resistance: float | None = None
    support_zone_low: float | None = None
    support_zone_high: float | None = None
    resistance_zone_low: float | None = None
    resistance_zone_high: float | None = None
    channel_lower: float | None = None
    channel_upper: float | None = None
    channel_mid: float | None = None
    smc_demand_zone: tuple[float, float] | None = None
    smc_supply_zone: tuple[float, float] | None = None
    swing_trend: Literal["bullish", "bearish", "neutral"] | None = None
    atr: float | None = None
    setup_name: str | None = None
    bias: Literal["bullish", "bearish", "neutral"] | None = None
    invalidation_level: float | None = None


class AnalyzerResult(BaseModel):
    plugin_id: str
    display_name: str
    category: Literal["technical", "structure", "relative", "fundamental", "catalyst", "sentiment"]
    score: float = Field(ge=0.0, le=100.0)
    bias: Literal["bullish", "bearish", "neutral"] = "neutral"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    summary: str = ""
    signals: list[Signal] = Field(default_factory=list)
    risk_flags: list[RiskFlag] = Field(default_factory=list)
    metrics: dict[str, MetricItem] = Field(default_factory=dict)
    overlays: list[ChartOverlay] = Field(default_factory=list)
    trade_hints: TradeHints | None = None


class OptionLeg(BaseModel):
    action: Literal["BUY", "SELL"]
    option_type: Literal["CALL", "PUT"]
    strike: float
    expiry: str
    dte: int
    mid_price: float
    delta: float | None = None
    iv: float | None = None
    oi: int | None = None
    bid: float | None = None
    ask: float | None = None


class OptionStrategyProposal(BaseModel):
    strategy_name: str
    bias: Literal["bullish", "bearish", "neutral"]
    rationale: str
    expiry: str
    dte: int
    legs: list[OptionLeg]
    net_credit_debit: float  # positive = credit received, negative = debit paid (per share)
    max_profit: float | None = None  # per contract ($100 multiplier)
    max_loss: float | None = None  # per contract ($100 multiplier), positive number representing max risk
    breakeven: list[float] = Field(default_factory=list)
    pop_estimate: float | None = None  # 0..100
    iv_regime: Literal["high", "normal", "low"] = "normal"
    data_source: str = "Yahoo + BS"
    earnings_warning: str | None = None


class TradePlan(BaseModel):
    direction: Literal["LONG", "SHORT", "NEUTRAL"]
    setup_name: str
    rationale: str
    entry_low: float
    entry_high: float
    stop_loss: float
    tp1: float
    tp2: float
    risk_reward: float
    tp1_risk_reward: float = 0.0
    atr: float
    risk_per_share: float
    potential_gain_pct: float = 0.0
    tp1_gain_pct: float = 0.0
    potential_loss_pct: float = 0.0
    spot_risk_pct: float | None = None
    spot_rr_tp1: float | None = None
    spot_rr_tp2: float | None = None
    stop_distance_atr: float | None = None
    target_crosses_barrier: bool = False
    dist_to_support_pct: float | None = None
    dist_to_resistance_pct: float | None = None
    index_aligned: bool = False
    index_trend: Literal["bullish", "bearish", "neutral"] = "neutral"


class BenchmarkSnapshot(BaseModel):
    symbol: str
    name: str
    price: float
    change_1d_pct: float
    change_5d_pct: float
    above_ema20: bool
    above_ema50: bool


class MarketRegime(BaseModel):
    regime: Literal["RISK_ON", "NEUTRAL", "RISK_OFF"] = "NEUTRAL"
    summary: str = "Neutral / Mixed Breadth"
    benchmarks: list[BenchmarkSnapshot] = Field(default_factory=list)
    sectors: list[BenchmarkSnapshot] = Field(default_factory=list)
    updated_at: str = ""


@dataclass(frozen=True)
class AnalysisContext:
    symbol: str
    daily_df: pd.DataFrame
    intraday_df: pd.DataFrame | None = None
    benchmark_symbol: str = "SPY"
    benchmark_df: pd.DataFrame | None = None
    sector_etf_symbol: str = "SPY"
    sector_df: pd.DataFrame | None = None
    fundamentals: dict[str, Any] = field(default_factory=dict)
    events: dict[str, Any] = field(default_factory=dict)
    insiders: list[dict[str, Any]] = field(default_factory=list)
    market_regime: MarketRegime | None = None
    settings: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OptionAnalysisContext:
    symbol: str
    spot_price: float
    daily_df: pd.DataFrame
    chain_df: pd.DataFrame | None
    bias: Literal["bullish", "bearish", "neutral"]
    days_to_earnings: int | None = None
    nearest_support: float | None = None
    nearest_resistance: float | None = None
    channel_lower: float | None = None
    channel_upper: float | None = None
    data_source: str = "Yahoo + BS"


class SourcePlugin(ABC):
    plugin_id: str = ""
    display_name: str = ""
    description: str = ""

    def discover(self, settings: dict[str, Any]) -> list[DiscoveredTicker]:
        return []


class AnalyzerPlugin(ABC):
    plugin_id: str = ""
    display_name: str = ""
    description: str = ""
    category: Literal["technical", "structure", "relative", "fundamental", "catalyst", "sentiment"] = "technical"
    default_weight: float = 0.1
    requires_deep_data: bool = False

    @abstractmethod
    def analyze(self, ctx: AnalysisContext) -> AnalyzerResult:
        raise NotImplementedError


class OptionStrategyPlugin(ABC):
    plugin_id: str = ""
    display_name: str = ""
    description: str = ""

    @abstractmethod
    def propose(self, ctx: OptionAnalysisContext) -> dict[str, Any]:
        """Returns {'iv_metrics': {...}, 'risk_flags': [...], 'proposals': [...]}."""
        raise NotImplementedError


class NotifierPlugin(ABC):
    plugin_id: str = ""
    display_name: str = ""
    description: str = ""

    @abstractmethod
    def is_configured(self, settings: dict[str, Any]) -> bool:
        raise NotImplementedError

    @abstractmethod
    def send_test(self, settings: dict[str, Any]) -> tuple[bool, str]:
        raise NotImplementedError

    @abstractmethod
    def notify_scan_summary(
        self,
        settings: dict[str, Any],
        top_alerts: list[dict[str, Any]],
        regime: MarketRegime | None,
    ) -> tuple[bool, str]:
        raise NotImplementedError

    def notify_paper_event(
        self,
        settings: dict[str, Any],
        event_type: str,
        title: str,
        lines: list[str],
    ) -> tuple[bool, str]:
        return False, "Not implemented"
