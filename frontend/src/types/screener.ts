export type Bias = "bullish" | "bearish" | "neutral";
export type ActiveView = "screener" | "trade_ideas" | "paper_trading";
export type Severity = "high" | "medium" | "low" | "warning" | "info";
export type SortBy = "score" | "risk_reward" | "potential_gain" | "tight_entry";
export type WatchlistHorizon = "short_term" | "medium_term" | "general";
export type TradeIdeaHorizon = "all" | "short_term" | "medium_term";
export type TradeIdeaLiveStatus =
  | "IN_ZONE"
  | "MISSED_ENTRY"
  | "BELOW_ENTRY"
  | "ABOVE_ENTRY"
  | "TARGET_HIT"
  | "STOP_HIT";

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
  | "smart_money"
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
  tp1_risk_reward?: number;
  atr: number;
  risk_per_share: number;
  potential_gain_pct: number;
  tp1_gain_pct: number;
  potential_loss_pct: number;
  spot_risk_pct?: number | null;
  spot_rr_tp1?: number | null;
  spot_rr_tp2?: number | null;
  stop_distance_atr?: number | null;
  target_crosses_barrier?: boolean;
  dist_to_support_pct: number | null;
  dist_to_resistance_pct: number | null;
  index_aligned: boolean;
  index_trend: Bias;
}

export interface LiveQuote {
  symbol: string;
  price: number;
  previous_close: number | null;
  change_pct: number;
  day_high: number | null;
  day_low: number | null;
  bid: number | null;
  ask: number | null;
  source: string;
  timestamp: string;
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
  trade_direction: "LONG" | "SHORT" | "NEUTRAL";
  risk_reward: number;
  potential_gain_pct: number;
  potential_loss_pct: number;
  dist_to_support_pct: number | null;
  dist_to_resistance_pct: number | null;
  index_aligned: boolean;
  index_trend: Bias;
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
  sort_by?: SortBy;
  presets: Record<string, { name: string; description: string; weights: Record<string, number> }>;
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
  horizon: WatchlistHorizon;
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

export interface BackfillStatus {
  running: boolean;
  profile: string;
  total_tickers: number;
  hydrated_tickers: number;
  last_batch_symbols: string[];
  last_run_at: string | null;
  last_source: string | null;
  message: string;
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

export interface PaperTradingSettings {
  enabled: boolean;
  mode: "virtual" | "ibkr_paper";
  auto_execute_after_scan: boolean;
  min_conviction: number;
  min_rr: number;
  allow_short: boolean;
  risk_per_trade_pct: number;
  max_position_pct_equity: number;
  max_open_positions: number;
  max_positions_per_sector: number;
  max_daily_new_orders: number;
  min_dollar_volume: number;
  max_spread_pct: number;
  min_days_to_earnings: number;
  move_to_be_at_r: number;
  time_stop_days: number;
  daily_loss_limit_pct: number;
  max_drawdown_pct: number;
}

export interface RuntimeSettings {
  presets: Record<string, { name: string; description: string; weights: Record<string, number> }>;
  enabled_plugins: Record<string, boolean>;
  account: { equity: number; risk_pct: number };
  ibkr: { enabled: boolean; host: string; port: number; client_id: number };
  history: {
    profile: "dev" | "production";
    dev_daily_period: string;
    dev_intraday_period: string;
    dev_weekly_period: string;
    prod_daily_period: string;
    prod_intraday_period: string;
    prod_weekly_period: string;
    backfill_enabled: boolean;
    backfill_interval_minutes: number;
    backfill_batch_size: number;
    prefer_ibkr_history: boolean;
  };
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
  paper_trading?: PaperTradingSettings;
}

export interface PaperAccountRun {
  id: number;
  name: string;
  mode: "virtual" | "ibkr_paper" | string;
  ibkr_account?: string;
  initial_capital: number;
  current_equity: number;
  cash_balance?: number;
  high_water_mark?: number;
  is_active: boolean;
  kill_switch_triggered: boolean;
  kill_switch_reason?: string;
  started_at: string | null;
  ended_at: string | null;
}

export interface PaperKpis {
  total_return_usd: number;
  total_return_pct: number;
  spy_return_pct: number | null;
  alpha_vs_spy_pct: number | null;
  realized_pnl_usd: number;
  unrealized_pnl_usd: number;
  open_count: number;
  pending_count: number;
  closed_count: number;
  wins_count: number;
  losses_count: number;
  win_rate_pct: number;
  profit_factor: number | null;
  expectancy_r: number;
  expectancy_usd: number;
  avg_win_usd: number;
  avg_loss_usd: number;
  avg_win_r: number;
  avg_loss_r: number;
  largest_win_usd: number;
  largest_loss_usd: number;
  max_drawdown_pct: number;
  current_drawdown_pct: number;
  sharpe_ratio: number | null;
  avg_hold_days: number;
  avg_slippage_bps: number | null;
  fill_rate_pct: number | null;
}

export interface PaperOverview {
  run: PaperAccountRun;
  paper_settings: PaperTradingSettings;
  kpis: PaperKpis;
}

export interface PaperEquityPoint {
  date: string;
  equity: number;
  cash: number;
  drawdown_pct: number;
  open_positions_count: number;
  realized_pnl_cum: number;
  unrealized_pnl: number;
  spy_equity: number | null;
}

export interface PaperTrade {
  id: number;
  run_id: number;
  symbol: string;
  scan_run_id: number | null;
  direction: "LONG" | "SHORT";
  setup_name: string;
  status: string;
  created_at: string | null;
  submitted_at: string | null;
  filled_at: string | null;
  closed_at: string | null;
  planned_entry_low: number;
  planned_entry_high: number;
  planned_limit_price: number;
  planned_stop_loss: number;
  current_stop_loss: number;
  planned_tp1: number;
  planned_tp2: number;
  planned_rr: number;
  be_moved: boolean;
  quantity_planned: number;
  quantity_filled: number;
  quantity_remaining: number;
  entry_fill_price: number | null;
  exit_fill_price: number | null;
  last_mark_price: number | null;
  entry_slippage_bps: number | null;
  commission_usd: number;
  risk_amount_usd: number;
  realized_pnl_usd: number;
  realized_pnl_pct: number;
  unrealized_pnl_usd: number;
  r_multiple: number;
  exit_reason: string;
  conviction_at_entry: number;
  composite_score_at_entry: number;
  market_regime_at_entry: string;
  sector_at_entry: string;
  signals_at_entry: string[];
}

export interface PaperOrderEvent {
  id: number;
  run_id: number;
  trade_id: number | null;
  symbol: string;
  event_type: string;
  message: string;
  created_at: string | null;
}

export interface PaperAttributionBucket {
  key: string;
  trades: number;
  wins: number;
  win_rate_pct: number;
  avg_r: number;
  total_pnl_usd: number;
  profit_factor: number | null;
}

export interface PaperAttribution {
  run_id: number;
  closed_trades: number;
  by_signal: PaperAttributionBucket[];
  by_conviction_band: PaperAttributionBucket[];
  by_direction: PaperAttributionBucket[];
  by_regime: PaperAttributionBucket[];
  by_sector: PaperAttributionBucket[];
  by_exit_reason: PaperAttributionBucket[];
}

export interface TradeIdeaReason {
  category: string;
  title: string;
  detail: string;
}

export interface TradeIdeaOptionLeg {
  action: "BUY" | "SELL" | string;
  option_type: "CALL" | "PUT" | string;
  strike: number;
  mid_price: number;
  delta: number | null;
}

export interface TradeIdeaOptionHint {
  strategy_name: string;
  expiry: string;
  dte: number;
  legs: TradeIdeaOptionLeg[];
  breakeven: number[];
  net_credit_debit: number;
  max_profit: number | null;
  max_loss: number | null;
  pop_estimate: number | null;
  iv_regime: string;
  rationale: string;
  earnings_warning: string | null;
}

export interface TradeIdea {
  symbol: string;
  name: string;
  sector: string;
  primary_benchmark: string;
  last_price: number;
  change_pct: number;
  analyzed_at: string | null;
  scan_run_id?: number | null;
  is_latest_scan?: boolean;
  snapshot_age_hours?: number | null;
  is_deep_enriched: boolean;
  direction: "LONG" | "SHORT";
  action: "BUY" | "SELL / SHORT";
  conviction_score: number;
  conviction_tier: string;
  composite_score: number;
  has_conflict: boolean;
  setup_name: string;
  thesis: string;
  entry_low: number;
  entry_high: number;
  stop_loss: number;
  tp1: number;
  tp2: number;
  risk_reward: number;
  tp1_risk_reward: number;
  risk_per_share: number;
  potential_gain_pct: number;
  tp1_gain_pct: number;
  potential_loss_pct: number;
  spot_risk_pct: number | null;
  spot_rr_tp1: number | null;
  spot_rr_tp2: number | null;
  stop_distance_atr: number | null;
  dist_to_support_pct: number | null;
  dist_to_resistance_pct: number | null;
  index_aligned: boolean;
  index_trend: Bias;
  days_to_earnings: number | null;
  fundamental_grade: string | null;
  live_price?: number | null;
  live_change_pct?: number | null;
  live_source?: string | null;
  price_drift_pct?: number | null;
  live_status?: TradeIdeaLiveStatus | null;
  live_risk_pct?: number | null;
  live_rr_tp1?: number | null;
  live_rr_tp2?: number | null;
  reasons: TradeIdeaReason[];
  watchouts: string[];
  top_option: TradeIdeaOptionHint | null;
  watchlist_ids: number[];
}

export interface TradeIdeasResponse {
  generated_at: string;
  disclaimer: string;
  horizon: TradeIdeaHorizon;
  min_rr: number;
  min_conviction?: number;
  hide_conflicted?: boolean;
  filtered_conflicted_count?: number;
  filtered_low_conviction_count?: number;
  hide_stale?: boolean;
  filtered_stale_count?: number;
  live_quotes_checked?: boolean;
  market_regime: MarketRegime | null;
  last_scan: ScreenerResponse["last_scan"];
  total_scanned: number;
  buys: TradeIdea[];
  sells: TradeIdea[];
}
