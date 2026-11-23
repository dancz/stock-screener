import React, { useState } from "react";
import {
  AlertTriangle,
  ArrowDownRight,
  ArrowUpRight,
  CheckCircle2,
  ExternalLink,
  Layers,
  Lightbulb,
  Loader2,
  Play,
  RefreshCw,
  ShieldAlert,
  Sparkles,
  TrendingDown,
  TrendingUp,
} from "lucide-react";
import { useScreenerStore } from "../store/useScreenerStore";
import type { TradeIdea, TradeIdeaHorizon } from "../types/screener";
import { WatchlistPopover } from "./WatchlistPopover";

type SideFilter = "all" | "buys" | "sells";

const CATEGORY_COLORS: Record<string, string> = {
  "Smart Money": "border-violet-500/40 bg-violet-500/15 text-violet-300",
  "Trend Channel": "border-sky-500/40 bg-sky-500/15 text-sky-300",
  "Price Action & S/R": "border-emerald-500/40 bg-emerald-500/15 text-emerald-300",
  "R:R & Index": "border-amber-500/40 bg-amber-500/15 text-amber-300",
  "Relative Strength": "border-cyan-500/40 bg-cyan-500/15 text-cyan-300",
  Volume: "border-indigo-500/40 bg-indigo-500/15 text-indigo-300",
  Fundamentals: "border-teal-500/40 bg-teal-500/15 text-teal-300",
  Insiders: "border-fuchsia-500/40 bg-fuchsia-500/15 text-fuchsia-300",
  Events: "border-slate-500/40 bg-slate-500/15 text-slate-300",
};

interface TradeIdeaCardProps {
  idea: TradeIdea;
  riskDollars: number;
  onOpenChart: (symbol: string) => void;
}

const TradeIdeaCard: React.FC<TradeIdeaCardProps> = ({ idea, riskDollars, onOpenChart }) => {
  const isLong = idea.direction === "LONG";
  const entryLow = Number(idea.entry_low ?? 0);
  const entryHigh = Number(idea.entry_high ?? entryLow);
  const stopLoss = Number(idea.stop_loss ?? 0);
  const tp1 = Number(idea.tp1 ?? 0);
  const tp2 = Number(idea.tp2 ?? 0);
  const rr = Number(idea.risk_reward ?? 0);
  const tp1Rr = Number(idea.tp1_risk_reward ?? 0);
  const lastPrice = Number(idea.last_price ?? 0);
  const changePct = Number(idea.change_pct ?? 0);
  const tp1GainPct = Number(idea.tp1_gain_pct ?? 0);
  const tp2GainPct = Number(idea.potential_gain_pct ?? 0);
  const lossPct = Number(idea.potential_loss_pct ?? 0);
  const midEntry = (entryLow + entryHigh) / 2;
  const riskPerShare = Math.max(Number(idea.risk_per_share ?? 0) || Math.abs(midEntry - stopLoss), 0.01);
  const estShares = riskDollars > 0 ? Math.max(0, Math.floor(riskDollars / riskPerShare)) : 0;
  const estPosValue = estShares * midEntry;

  const accentBorder = idea.has_conflict
    ? "border-amber-500/40 hover:border-amber-500/70"
    : isLong
    ? "border-emerald-500/30 hover:border-emerald-500/60"
    : "border-rose-500/30 hover:border-rose-500/60";
  const badgeStyle = isLong
    ? "border-emerald-500/40 bg-emerald-500/15 text-emerald-300"
    : "border-rose-500/40 bg-rose-500/15 text-rose-300";

  const convictionColor =
    idea.conviction_score >= 82
      ? "border-emerald-500/40 bg-emerald-500/15 text-emerald-300"
      : idea.conviction_score >= 72
      ? "border-sky-500/40 bg-sky-500/15 text-sky-300"
      : "border-amber-500/40 bg-amber-500/15 text-amber-300";

  const snapshotTooltip = idea.analyzed_at
    ? `Snapshot close at ${new Date(idea.analyzed_at).toLocaleString()}`
    : "Latest stored snapshot";

  return (
    <article className={`flex flex-col gap-3 rounded-xl border bg-panel p-4 transition-colors ${accentBorder}`}>
      {/* Header Row */}
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className={`inline-flex items-center gap-1 rounded border px-2 py-0.5 text-[11px] font-bold ${badgeStyle}`}>
              {isLong ? <ArrowUpRight className="h-3.5 w-3.5" /> : <ArrowDownRight className="h-3.5 w-3.5" />}
              {idea.action}
            </span>
            <button
              type="button"
              onClick={() => onOpenChart(idea.symbol)}
              className="text-base font-extrabold tracking-tight text-white hover:text-sky-400 transition-colors"
              title={`Open ${idea.symbol} chart and full breakdown`}
            >
              {idea.symbol}
            </button>
            <span className="truncate text-xs text-slate-300">{idea.name}</span>
            {idea.sector && (
              <span className="rounded bg-card px-1.5 py-0.5 text-[10px] text-slate-400">{idea.sector}</span>
            )}
            {idea.has_conflict && (
              <span className="rounded border border-amber-500/40 bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-semibold text-amber-300">
                Conflicted Setup
              </span>
            )}
          </div>

          <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[10px]">
            <span
              className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 font-semibold ${
                idea.index_aligned
                  ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
                  : "border-slate-700 bg-card text-slate-400"
              }`}
            >
              {idea.index_aligned ? <TrendingUp className="h-3 w-3" /> : <TrendingDown className="h-3 w-3" />}
              {idea.index_aligned
                ? `${idea.primary_benchmark} Aligned (${idea.index_trend})`
                : `Index ${idea.primary_benchmark}: ${idea.index_trend}`}
            </span>

            <span className="rounded border border-border bg-card px-1.5 py-0.5 text-slate-300">
              {idea.setup_name}
            </span>

            {idea.fundamental_grade && (
              <span className="rounded border border-teal-500/30 bg-teal-500/10 px-1.5 py-0.5 font-semibold text-teal-300">
                Health {idea.fundamental_grade}
              </span>
            )}

            {idea.days_to_earnings != null && (
              <span
                className={`rounded border px-1.5 py-0.5 font-medium ${
                  idea.days_to_earnings >= 0 && idea.days_to_earnings <= 10
                    ? "border-amber-500/40 bg-amber-500/15 text-amber-300"
                    : "border-border bg-card text-slate-400"
                }`}
              >
                {idea.days_to_earnings < 0
                  ? `Reported ${Math.abs(idea.days_to_earnings)}d ago`
                  : idea.days_to_earnings === 0
                  ? "Earnings Today"
                  : `Earnings in ${idea.days_to_earnings}d`}
              </span>
            )}

            {!idea.is_deep_enriched && (
              <span className="rounded border border-slate-700 bg-card px-1.5 py-0.5 text-slate-400">
                Stage-1 Snapshot
              </span>
            )}
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-2.5 text-right" title={snapshotTooltip}>
          <div>
            <div className="tabular-nums text-sm font-bold text-white">${lastPrice.toFixed(2)}</div>
            <div
              className={`tabular-nums text-[11px] font-semibold ${
                changePct >= 0 ? "text-emerald-400" : "text-rose-400"
              }`}
            >
              {changePct >= 0 ? "+" : ""}
              {changePct.toFixed(2)}% <span className="text-[10px] font-normal text-slate-400">(1D)</span>
            </div>
          </div>
          <div className={`rounded-lg border px-2.5 py-1 text-center ${convictionColor}`}>
            <div className="tabular-nums text-sm font-extrabold leading-none">{idea.conviction_score.toFixed(1)}</div>
            <div className="mt-0.5 text-[9px] font-semibold uppercase tracking-wide opacity-90">
              {idea.conviction_tier}
            </div>
          </div>
        </div>
      </div>

      {/* Thesis */}
      <div className="rounded-lg border border-border/70 bg-card/60 px-3 py-2 text-xs leading-relaxed text-slate-200">
        {idea.thesis}
      </div>

      {/* Concrete Trade Levels */}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
        <div className="rounded-lg border border-border/70 bg-canvas p-2">
          <div className="text-[10px] uppercase tracking-wider text-slate-400">Entry Zone</div>
          <div className="mt-0.5 tabular-nums text-xs font-bold text-sky-300">
            ${entryLow.toFixed(2)}–${entryHigh.toFixed(2)}
          </div>
          {idea.dist_to_support_pct != null && (
            <div className="mt-0.5 tabular-nums text-[10px] text-slate-400">
              Sup {Number(idea.dist_to_support_pct).toFixed(1)}% / Res{" "}
              {idea.dist_to_resistance_pct != null ? Number(idea.dist_to_resistance_pct).toFixed(1) : "—"}%
            </div>
          )}
        </div>

        <div className="rounded-lg border border-rose-500/30 bg-rose-500/5 p-2">
          <div className="text-[10px] uppercase tracking-wider text-rose-300/80">Stop-Loss</div>
          <div className="mt-0.5 tabular-nums text-xs font-bold text-rose-300">${stopLoss.toFixed(2)}</div>
          <div className="mt-0.5 tabular-nums text-[10px] font-medium text-rose-400">
            -{lossPct.toFixed(1)}% Entry
            {idea.stop_distance_atr != null ? ` • ${Number(idea.stop_distance_atr).toFixed(1)}x ATR` : ""}
          </div>
        </div>

        <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-2">
          <div className="text-[10px] uppercase tracking-wider text-emerald-300/80">Target 1</div>
          <div className="mt-0.5 tabular-nums text-xs font-bold text-emerald-300">${tp1.toFixed(2)}</div>
          <div className="mt-0.5 tabular-nums text-[10px] font-medium text-emerald-400">
            +{tp1GainPct.toFixed(1)}% {tp1Rr > 0 ? `(${tp1Rr.toFixed(2)}R)` : ""}
          </div>
        </div>

        <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-2">
          <div className="text-[10px] uppercase tracking-wider text-emerald-300/80">Target 2</div>
          <div className="mt-0.5 tabular-nums text-xs font-bold text-emerald-300">${tp2.toFixed(2)}</div>
          <div className="mt-0.5 tabular-nums text-[10px] font-medium text-emerald-400">
            +{tp2GainPct.toFixed(1)}% ({rr.toFixed(2)}R)
          </div>
        </div>

        <div className="col-span-2 rounded-lg border border-border/70 bg-canvas p-2 sm:col-span-1">
          <div className="text-[10px] uppercase tracking-wider text-slate-400">R:R / Size</div>
          <div className="mt-0.5 tabular-nums text-xs font-bold text-white">{rr.toFixed(2)}x Entry</div>
          {idea.spot_rr_tp2 != null && (
            <div className="mt-0.5 tabular-nums text-[10px] text-slate-300">
              At Spot: {Number(idea.spot_rr_tp2).toFixed(2)}x (TP1{" "}
              {idea.spot_rr_tp1 != null ? Number(idea.spot_rr_tp1).toFixed(2) : "—"}x)
            </div>
          )}
          <div className="mt-0.5 tabular-nums text-[10px] text-slate-400">
            {estShares > 0
              ? `~${estShares} sh ($${estPosValue.toLocaleString(undefined, { maximumFractionDigits: 0 })})`
              : `Risk $${riskPerShare.toFixed(2)}/sh`}
          </div>
        </div>
      </div>

      {/* Explanation: Why Analysis Recommends This Trade */}
      <div className="space-y-1.5">
        <div className="flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wider text-slate-300">
          <CheckCircle2 className={`h-3.5 w-3.5 ${isLong ? "text-emerald-400" : "text-rose-400"}`} />
          <span>Why Analysis Recommends This Trade</span>
        </div>
        <ul className="space-y-1.5">
          {idea.reasons.map((r, idx) => (
            <li key={`${r.category}-${idx}`} className="flex items-start gap-2 text-xs leading-snug text-slate-300">
              <span
                className={`mt-0.5 shrink-0 rounded border px-1.5 py-0.5 text-[10px] font-semibold ${
                  CATEGORY_COLORS[r.category] ?? "border-border bg-card text-slate-300"
                }`}
              >
                {r.category}
              </span>
              <span className="min-w-0">
                {r.detail && r.detail !== r.title ? (
                  <>
                    <span className="font-semibold text-white">{r.title}: </span>
                    <span className="text-slate-300">{r.detail}</span>
                  </>
                ) : (
                  <span className="font-semibold text-white">{r.title}</span>
                )}
              </span>
            </li>
          ))}
        </ul>
      </div>

      {/* Watchouts / Verify Before Ordering */}
      {idea.watchouts.length > 0 && (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-2.5">
          <div className="mb-1 flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wider text-amber-300">
            <AlertTriangle className="h-3.5 w-3.5" />
            <span>Watchouts — Verify Before Ordering</span>
          </div>
          <ul className="list-disc space-y-0.5 pl-4 text-[11px] leading-snug text-amber-100/90">
            {idea.watchouts.map((w, idx) => (
              <li key={idx}>{w}</li>
            ))}
          </ul>
        </div>
      )}

      {/* Optional Matching Option Strategy with Strikes & Breakeven */}
      {idea.top_option && (
        <div className="rounded-lg border border-border/80 bg-canvas/80 px-3 py-2 text-[11px]">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="inline-flex items-center gap-1.5 font-semibold text-sky-300">
              <Layers className="h-3.5 w-3.5" />
              Option Idea: {idea.top_option.strategy_name}
            </span>
            <span className="tabular-nums text-slate-400">
              {idea.top_option.expiry} ({idea.top_option.dte} DTE) • IV {idea.top_option.iv_regime.toUpperCase()}
              {idea.top_option.pop_estimate != null && ` • PoP ~${Math.round(idea.top_option.pop_estimate)}%`}
            </span>
          </div>

          {idea.top_option.legs && idea.top_option.legs.length > 0 && (
            <div className="mt-1 flex flex-wrap items-center gap-1.5 tabular-nums">
              {idea.top_option.legs.map((leg, lIdx) => (
                <span
                  key={lIdx}
                  className={`rounded border px-1.5 py-0.5 text-[10px] font-semibold ${
                    leg.action === "BUY"
                      ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
                      : "border-rose-500/30 bg-rose-500/10 text-rose-300"
                  }`}
                >
                  {leg.action} ${leg.strike} {leg.option_type} (@ ${leg.mid_price.toFixed(2)})
                </span>
              ))}
              {idea.top_option.breakeven && idea.top_option.breakeven.length > 0 && (
                <span className="text-[10px] font-medium text-slate-300">
                  • BE: ${idea.top_option.breakeven.map((b) => b.toFixed(2)).join(" / $")}
                </span>
              )}
            </div>
          )}

          <div className="mt-1 flex flex-wrap gap-3 text-slate-300 tabular-nums">
            <span>
              {idea.top_option.net_credit_debit >= 0 ? "Net Credit" : "Net Debit"}: $
              {Math.abs(idea.top_option.net_credit_debit).toFixed(2)}
            </span>
            {idea.top_option.max_profit != null && <span>Max Profit: ${idea.top_option.max_profit.toFixed(0)}</span>}
            {idea.top_option.max_loss != null && <span>Max Risk: ${idea.top_option.max_loss.toFixed(0)}</span>}
          </div>
        </div>
      )}

      {/* Card Footer */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border/60 pt-2.5">
        <button
          type="button"
          onClick={() => onOpenChart(idea.symbol)}
          className="inline-flex items-center gap-1.5 rounded-md bg-sky-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-sky-500 transition-colors"
        >
          <ExternalLink className="h-3.5 w-3.5" />
          <span>Open Chart &amp; Full Analysis</span>
        </button>

        <WatchlistPopover symbol={idea.symbol} watchlistIds={idea.watchlist_ids} />
      </div>
    </article>
  );
};

export const TradeIdeasView: React.FC = () => {
  const [sideFilter, setSideFilter] = useState<SideFilter>("all");

  const tradeIdeas = useScreenerStore((s) => s.tradeIdeas);
  const loadingTradeIdeas = useScreenerStore((s) => s.loadingTradeIdeas);
  const horizon = useScreenerStore((s) => s.ideaHorizon);
  const minRr = useScreenerStore((s) => s.ideaMinRr);
  const excludeEarnings7d = useScreenerStore((s) => s.ideaExcludeEarnings7d);
  const indexAlignedOnly = useScreenerStore((s) => s.ideaIndexAlignedOnly);
  const watchlistOnly = useScreenerStore((s) => s.ideaWatchlistOnly);
  const hideConflicted = useScreenerStore((s) => s.ideaHideConflicted);
  const settings = useScreenerStore((s) => s.settings);
  const setIdeaFilters = useScreenerStore((s) => s.setIdeaFilters);
  const fetchTradeIdeas = useScreenerStore((s) => s.fetchTradeIdeas);
  const openIdeaInScreener = useScreenerStore((s) => s.openIdeaInScreener);
  const triggerScan = useScreenerStore((s) => s.triggerScan);

  const equity = Number(settings?.account?.equity ?? 25000);
  const riskPct = Number(settings?.account?.risk_pct ?? 1.0);
  const riskDollars = Math.max(0, equity * (riskPct / 100));

  const buys = tradeIdeas?.buys ?? [];
  const sells = tradeIdeas?.sells ?? [];
  const totalScanned = tradeIdeas?.total_scanned ?? 0;
  const filteredConflicted = tradeIdeas?.filtered_conflicted_count ?? 0;
  const regime = tradeIdeas?.market_regime;

  const regimeGuidance =
    regime?.regime === "RISK_OFF"
      ? "Market regime is RISK-OFF — Short setups align with the tape; demand extra trigger confirmation on Long buys."
      : regime?.regime === "RISK_ON"
      ? "Market regime is RISK-ON — Favor index-aligned Long buys and keep Short setups tactical."
      : "Market regime is NEUTRAL — Prioritize clean S/R zones, asymmetric R:R, and catalyst-free setups.";

  if (!tradeIdeas && loadingTradeIdeas) {
    return (
      <div className="flex h-64 items-center justify-center text-sm text-slate-400">
        <Loader2 className="mr-2 h-5 w-5 animate-spin text-sky-400" />
        Generating Buy &amp; Sell Trade Ideas from stored snapshots...
      </div>
    );
  }

  if (totalScanned === 0 && !loadingTradeIdeas) {
    return (
      <div className="mx-auto flex max-w-2xl flex-col items-center justify-center rounded-2xl border border-dashed border-border bg-panel/40 p-10 text-center">
        <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-xl bg-sky-500/15 text-sky-400">
          <Sparkles className="h-6 w-6" />
        </div>
        <h2 className="text-lg font-bold text-white">No Screener Snapshots Stored Yet</h2>
        <p className="mt-1.5 text-xs leading-relaxed text-slate-400">
          Run a Quick Scan or Full Universe Scan first so SwingPulse can synthesize top Buy &amp; Sell trade ideas with
          Entry, Stop-Loss, and Target levels.
        </p>
        <div className="mt-5 flex flex-wrap justify-center gap-3">
          <button
            type="button"
            onClick={() => triggerScan("quick")}
            className="inline-flex items-center gap-2 rounded-lg bg-sky-600 px-4 py-2 text-xs font-semibold text-white hover:bg-sky-500"
          >
            <Play className="h-3.5 w-3.5 fill-current" />
            Run Quick Scan (~25s)
          </button>
          <button
            type="button"
            onClick={() => triggerScan("full")}
            className="inline-flex items-center gap-2 rounded-lg border border-border bg-card px-4 py-2 text-xs font-semibold text-slate-200 hover:border-slate-500 hover:text-white"
          >
            Run Full Universe Scan
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-[1600px] space-y-4 pb-8">
      {/* Advisory Disclaimer Header */}
      <section className="rounded-xl border border-sky-500/30 bg-gradient-to-r from-sky-500/10 via-panel to-panel p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-1">
            <div className="flex flex-wrap items-center gap-2">
              <Lightbulb className="h-4 w-4 text-sky-400" />
              <h1 className="text-base font-bold text-white">Actionable Trade Ideas — Best Buys &amp; Sells</h1>
              <span className="rounded-full border border-amber-500/40 bg-amber-500/15 px-2 py-0.5 text-[10px] font-semibold text-amber-300">
                Advisory Only — Verify Manually Before Ordering
              </span>
            </div>
            <p className="text-xs leading-relaxed text-slate-300">
              {tradeIdeas?.disclaimer ||
                "Advisory trade ideas derived from the latest stored screener snapshots. Always verify live price, chart structure, liquidity, and news before placing any broker order."}
            </p>
            <p className="flex items-center gap-1.5 text-[11px] text-sky-300">
              <ShieldAlert className="h-3.5 w-3.5 shrink-0" />
              <span>{regimeGuidance}</span>
            </p>
          </div>

          <div className="flex flex-col items-end gap-1 text-right text-[11px] text-slate-400">
            <span>Evaluated from {totalScanned} tickers in DB</span>
            {tradeIdeas?.last_scan?.finished_at && (
              <span>Last scan: {new Date(tradeIdeas.last_scan.finished_at).toLocaleString()}</span>
            )}
            <span className="tabular-nums text-slate-300">
              Sizing: ${riskDollars.toFixed(0)} risk ({riskPct.toFixed(1)}% of ${equity.toLocaleString()})
            </span>
          </div>
        </div>
      </section>

      {/* Filter Toolbar */}
      <section className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-panel p-3">
        <div className="flex flex-wrap items-center gap-2">
          {/* Side Filter */}
          <div className="inline-flex rounded-lg border border-border bg-canvas p-0.5 text-xs">
            {(
              [
                { id: "all", label: `All (${buys.length + sells.length})` },
                { id: "buys", label: `Best Buys (${buys.length})` },
                { id: "sells", label: `Best Sells (${sells.length})` },
              ] as const
            ).map((opt) => (
              <button
                key={opt.id}
                type="button"
                onClick={() => setSideFilter(opt.id)}
                className={`rounded-md px-2.5 py-1 font-semibold transition-colors ${
                  sideFilter === opt.id ? "bg-sky-600 text-white" : "text-slate-400 hover:text-slate-200"
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>

          {/* Horizon Selector */}
          <div className="inline-flex rounded-lg border border-border bg-canvas p-0.5 text-xs">
            {(
              [
                { id: "all", label: "All Horizons" },
                { id: "short_term", label: "Short-Term" },
                { id: "medium_term", label: "Medium-Term" },
              ] as { id: TradeIdeaHorizon; label: string }[]
            ).map((h) => (
              <button
                key={h.id}
                type="button"
                onClick={() => setIdeaFilters({ horizon: h.id })}
                className={`rounded-md px-2.5 py-1 font-medium transition-colors ${
                  horizon === h.id ? "bg-slate-700 text-white" : "text-slate-400 hover:text-slate-200"
                }`}
              >
                {h.label}
              </button>
            ))}
          </div>

          {/* Min R:R */}
          <label className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-canvas px-2.5 py-1 text-xs text-slate-300">
            <span className="text-slate-400">Min R:R</span>
            <select
              value={minRr}
              onChange={(e) => setIdeaFilters({ minRr: Number(e.target.value) })}
              className="bg-transparent font-semibold text-white focus:outline-none"
            >
              <option value={1.5} className="bg-panel">
                1.5x+
              </option>
              <option value={2.0} className="bg-panel">
                2.0x+
              </option>
              <option value={2.5} className="bg-panel">
                2.5x+
              </option>
              <option value={3.0} className="bg-panel">
                3.0x+
              </option>
            </select>
          </label>
        </div>

        {/* Toggle Filters & Refresh */}
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => setIdeaFilters({ hideConflicted: !hideConflicted })}
            title="Filter out setups with high-severity conflicting signals, targets crossing major barriers, or >=12% 1D gap shocks"
            className={`rounded-lg border px-2.5 py-1 text-xs font-medium transition-colors ${
              hideConflicted
                ? "border-emerald-500/50 bg-emerald-500/15 text-emerald-300"
                : "border-amber-500/50 bg-amber-500/15 text-amber-300"
            }`}
          >
            Hide Conflicted{hideConflicted && filteredConflicted > 0 ? ` (${filteredConflicted})` : ""}
          </button>

          <button
            type="button"
            onClick={() => setIdeaFilters({ excludeEarnings7d: !excludeEarnings7d })}
            className={`rounded-lg border px-2.5 py-1 text-xs font-medium transition-colors ${
              excludeEarnings7d
                ? "border-amber-500/50 bg-amber-500/15 text-amber-300"
                : "border-border bg-canvas text-slate-400 hover:text-slate-200"
            }`}
          >
            Exclude Earnings &lt;7d
          </button>

          <button
            type="button"
            onClick={() => setIdeaFilters({ indexAlignedOnly: !indexAlignedOnly })}
            className={`rounded-lg border px-2.5 py-1 text-xs font-medium transition-colors ${
              indexAlignedOnly
                ? "border-emerald-500/50 bg-emerald-500/15 text-emerald-300"
                : "border-border bg-canvas text-slate-400 hover:text-slate-200"
            }`}
          >
            Index Aligned Only
          </button>

          <button
            type="button"
            onClick={() => setIdeaFilters({ watchlistOnly: !watchlistOnly })}
            className={`rounded-lg border px-2.5 py-1 text-xs font-medium transition-colors ${
              watchlistOnly
                ? "border-sky-500/50 bg-sky-500/15 text-sky-300"
                : "border-border bg-canvas text-slate-400 hover:text-slate-200"
            }`}
          >
            Watchlists Only
          </button>

          <button
            type="button"
            onClick={() => fetchTradeIdeas()}
            disabled={loadingTradeIdeas}
            className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-card px-2.5 py-1 text-xs font-semibold text-slate-200 hover:border-slate-500 hover:text-white disabled:opacity-50"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loadingTradeIdeas ? "animate-spin text-sky-400" : ""}`} />
            <span>Refresh</span>
          </button>
        </div>
      </section>

      {/* Ideas Columns / Grid */}
      {sideFilter === "all" ? (
        <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
          {/* Best Buy Ideas */}
          <div className="space-y-3">
            <div className="flex items-center justify-between rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3.5 py-2">
              <div className="flex items-center gap-2">
                <TrendingUp className="h-4 w-4 text-emerald-400" />
                <h2 className="text-sm font-bold text-emerald-200">Best Buy Ideas (Long Setups)</h2>
              </div>
              <span className="rounded-full bg-emerald-500/20 px-2 py-0.5 text-xs font-bold tabular-nums text-emerald-300">
                {buys.length}
              </span>
            </div>

            {buys.length === 0 ? (
              <div className="rounded-xl border border-dashed border-border bg-panel/40 p-6 text-center text-xs text-slate-400">
                No Long Buy setups match the current filters. Try lowering Min R:R or turning off Index Aligned Only.
              </div>
            ) : (
              buys.map((idea) => (
                <TradeIdeaCard
                  key={`buy-${idea.symbol}`}
                  idea={idea}
                  riskDollars={riskDollars}
                  onOpenChart={openIdeaInScreener}
                />
              ))
            )}
          </div>

          {/* Best Sell / Short Ideas */}
          <div className="space-y-3">
            <div className="flex items-center justify-between rounded-lg border border-rose-500/30 bg-rose-500/10 px-3.5 py-2">
              <div className="flex items-center gap-2">
                <TrendingDown className="h-4 w-4 text-rose-400" />
                <h2 className="text-sm font-bold text-rose-200">Best Sell / Short Ideas (Short Setups)</h2>
              </div>
              <span className="rounded-full bg-rose-500/20 px-2 py-0.5 text-xs font-bold tabular-nums text-rose-300">
                {sells.length}
              </span>
            </div>

            {sells.length === 0 ? (
              <div className="rounded-xl border border-dashed border-border bg-panel/40 p-6 text-center text-xs text-slate-400">
                No Short Sell setups match the current filters. Try lowering Min R:R or relaxing active toggles.
              </div>
            ) : (
              sells.map((idea) => (
                <TradeIdeaCard
                  key={`sell-${idea.symbol}`}
                  idea={idea}
                  riskDollars={riskDollars}
                  onOpenChart={openIdeaInScreener}
                />
              ))
            )}
          </div>
        </div>
      ) : (
        <div className="space-y-3">
          {(() => {
            const list = sideFilter === "buys" ? buys : sells;
            if (list.length === 0) {
              return (
                <div className="rounded-xl border border-dashed border-border bg-panel/40 p-8 text-center text-xs text-slate-400">
                  No {sideFilter === "buys" ? "Buy" : "Short"} setups match the current filters. Try relaxing Min R:R
                  or filter toggles.
                </div>
              );
            }
            return (
              <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                {list.map((idea) => (
                  <TradeIdeaCard
                    key={`${sideFilter}-${idea.symbol}`}
                    idea={idea}
                    riskDollars={riskDollars}
                    onOpenChart={openIdeaInScreener}
                  />
                ))}
              </div>
            );
          })()}
        </div>
      )}
    </div>
  );
};
