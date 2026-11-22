export type Bias = "bullish" | "bearish" | "neutral";
export type Severity = "high" | "medium" | "low" | "warning" | "info";

export interface Signal {
  code: string;
  label: string;
  direction: Bias;
  severity: "high" | "medium" | "low";
  score_impact: number;
  detail: string;
}

export interface RiskFlag {
  code: string;
  label: string;
  severity: "high" | "warning" | "info";
  detail: string;
}

export interface MetricItem {
  label: string;
  value: number | string | boolean | null;
  formatted: string;
  status: "good" | "warn" | "bad" | "neutral";
}

export type OverlayLayer =
  | "channels"
  | "sr_zones"
  | "price_action"
  | "trade_plan"
  | "emas"
  | "volume_profile"
  | "volume";

export type OverlayKind =
  | "horizontal_zone"
  | "channel"
  | "trend_line"
  | "marker"
  | "line_series"
  | "price_box";

export interface ChartOverlay {
  id: string;
  layer: OverlayLayer;
  kind: OverlayKind;
  label: string;
  color: string;
  data: Record<string, unknown>;
}

export interface TradePlan {
  direction: "LONG" | "SHORT" | "NEUTRAL";
  setup_name: string;
  rationale: string;
  entry_low: number;
  entry_high: number;
  stop_loss: number;
  tp1: number;
  tp2: number;
  risk_reward: number;
  atr: number;
  risk_per_share: number;
}

export interface OptionLeg {
  action: "BUY" | "SELL";
  option_type: "CALL" | "PUT";
  strike: number;
  expiry: string;
  dte: number;
  mid_price: number;
  delta: number | null;
  iv: number | null;
  oi: number | null;
  bid: number | null;
  ask: number | null;
}

export interface OptionStrategyProposal {
  strategy_name: string;
  bias: Bias;
  rationale: string;
  expiry: string;
  dte: number;
  legs: OptionLeg[];
  net_credit_debit: number;
  max_profit: number | null;
  max_loss: number | null;
  breakeven: number[];
  pop_estimate: number | null;
  iv_regime: "high" | "normal" | "low";
  data_source: string;
  earnings_warning: string | null;
}

export interface OptionsPayload {
  iv_metrics?: {
    hv20?: number;
    hv60?: number;
    atm_iv?: number | null;
    iv_hv_ratio?: number | null;
    iv_regime?: "high" | "normal" | "low";
    liquidity_ok?: boolean;
    median_oi?: number;
    data_source?: string;
  };
  proposals?: OptionStrategyProposal[];
}

export interface AnalyzerResultDTO {
  plugin_id: string;
  display_name: string;
  category: string;
  score: number;
  bias: Bias;
  confidence: number;
  summary: string;
  signals: Signal[];
  risk_flags: RiskFlag[];
  metrics: Record<string, MetricItem>;
  overlays: ChartOverlay[];
}

export interface ScreenerItem {
  symbol: string;
  name: string;
  sector: string;
  primary_benchmark: string;
  sector_etf: string;
  last_price: number;
  change_pct: number;
  score: number;
  composite_score: number;
  preset_scores: Record<string, number>;
  bias: Bias;
  top_signals: Signal[];
  signal_codes: string[];
  risk_flags: RiskFlag[];
  days_to_earnings: number | null;
  watchlist_ids: number[];
  is_deep_enriched: boolean;
  analyzed_at: string | null;
}

export interface BenchmarkSnapshot {
  symbol: string;
  name: string;
  price: number;
  change_1d_pct: number;
  change_5d_pct: number;
  above_ema20: boolean;
  above_ema50: boolean;
}

export interface MarketRegime {
  regime: "RISK_ON" | "NEUTRAL" | "RISK_OFF";
  summary: string;
  benchmarks: BenchmarkSnapshot[];
  sectors: BenchmarkSnapshot[];
  updated_at: string;
}

export interface ScreenerResponse {
  preset: string;
  presets: Record<string, { name: string; description: string }>;
  total: number;
  items: ScreenerItem[];
  market_regime: MarketRegime | null;
  last_scan: {
    id: number;
    mode: string;
    status: string;
    stage: string;
    total_tickers: number;
    processed_tickers: number;
    started_at: string | null;
    finished_at: string | null;
  } | null;
}

export interface StockDetail {
  symbol: string;
  name: string;
  sector: string;
  industry: string;
  primary_benchmark: string;
  sector_etf: string;
  discovery_sources: string[];
  last_price: number;
  change_pct: number;
  composite_score: number;
  preset_scores: Record<string, number>;
  bias: Bias;
  signals: Signal[];
  risk_flags: RiskFlag[];
  overlays: ChartOverlay[];
  trade_plan: TradePlan;
  options: OptionsPayload;
  fundamentals: Record<string, number | string | null>;
  events_insiders: {
    next_earnings_date?: string | null;
    days_to_earnings?: number | null;
    ex_dividend_date?: string | null;
    analyst_target?: number | null;
    analyst_count?: number | null;
    recommendation?: string | null;
    news?: Array<{ headline: string; source: string; url: string; datetime?: number }>;
    analyst_breakdown?: Record<string, number | string>;
    insiders?: Array<{
      source: string;
      tag: string;
      filing_date: string;
      trade_date: string;
      insider_name: string;
      title: string;
      trade_type: string;
      value_raw: string;
      value_usd: number;
    }>;
  };
  plugin_results: Record<string, AnalyzerResultDTO>;
  note_text: string;
  tags: string[];
  watchlist_ids: number[];
  is_deep_enriched: boolean;
  analyzed_at: string | null;
}

export interface CandleBar {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface Watchlist {
  id: number;
  name: string;
  color: string;
  symbols: string[];
  count: number;
}

export interface ScanStatus {
  running: boolean;
  scan_run_id: number | null;
  mode: string | null;
  stage: string;
  processed: number;
  total: number;
  message: string;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
}

export interface PluginDescriptor {
  plugin_id: string;
  display_name: string;
  description: string;
  kind: "analyzer" | "source" | "options" | "notifier";
  category?: string;
  default_weight?: number;
  requires_deep_data?: boolean;
  enabled: boolean;
}

export interface RuntimeSettings {
  presets: Record<string, { name: string; description: string; weights: Record<string, number> }>;
  enabled_plugins: Record<string, boolean>;
  account: { equity: number; risk_pct: number };
  ibkr: { enabled: boolean; host: string; port: number; client_id: number };
  data_sources: { finnhub_api_key: string; sec_user_agent: string };
  schedule: { enabled: boolean; timezone: string; post_market_time: string; pre_market_time: string };
  notifiers: {
    telegram_enabled: boolean;
    telegram_bot_token: string;
    telegram_chat_id: string;
    discord_enabled: boolean;
    discord_webhook_url: string;
    min_alert_score: number;
  };
}
