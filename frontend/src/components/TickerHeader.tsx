import React from "react";
import { AlertTriangle, HelpCircle, Radio, RefreshCw } from "lucide-react";
import { useScreenerStore } from "../store/useScreenerStore";
import { WatchlistPopover } from "./WatchlistPopover";

export const TickerHeader: React.FC = () => {
  const detail = useScreenerStore((s) => s.selectedDetail);
  const liveQuote = useScreenerStore((s) => s.liveQuote);
  const activePreset = useScreenerStore((s) => s.activePreset);
  const presets = useScreenerStore((s) => s.presets);
  const loadingDetail = useScreenerStore((s) => s.loadingDetail);
  const refreshStock = useScreenerStore((s) => s.refreshStock);

  if (!detail) return null;

  const presetScore = Number(detail.preset_scores?.[activePreset] ?? detail.composite_score ?? 0);
  const scoreClass =
    presetScore >= 72
      ? "border-emerald-500/40 bg-emerald-500/15 text-emerald-300"
      : presetScore >= 58
      ? "border-sky-500/40 bg-sky-500/15 text-sky-300"
      : "border-amber-500/40 bg-amber-500/15 text-amber-300";

  const scoreTier =
    presetScore >= 80
      ? "A+ Setup"
      : presetScore >= 68
      ? "Strong"
      : presetScore >= 55
      ? "Watch"
      : "Weak";

  const displayPrice = Number(liveQuote ? liveQuote.price : detail.last_price ?? 0);
  const displayChg = Number(liveQuote ? liveQuote.change_pct : detail.change_pct ?? 0);
  const presetName = presets[activePreset]?.name || activePreset;

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-panel p-4">
      <div className="space-y-1">
        <div className="flex flex-wrap items-center gap-2.5">
          <h1 className="text-xl font-bold tracking-tight text-white">{detail.symbol}</h1>
          <span className="text-sm font-medium text-slate-300">{detail.name}</span>
          {detail.sector && (
            <span className="rounded bg-card px-2 py-0.5 text-[11px] text-slate-400">
              {detail.sector} ({detail.sector_etf}) • Bench: {detail.primary_benchmark}
            </span>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2 pt-0.5">
          <span className="tabular-nums text-lg font-bold text-white">${displayPrice.toFixed(2)}</span>
          <span
            className={`tabular-nums text-sm font-semibold ${
              displayChg >= 0 ? "text-emerald-400" : "text-rose-400"
            }`}
          >
            {displayChg >= 0 ? "+" : ""}
            {displayChg.toFixed(2)}%
          </span>

          {liveQuote ? (
            <span
              title={`Live quote fetched at ${new Date(liveQuote.timestamp).toLocaleTimeString()}${
                liveQuote.bid && liveQuote.ask ? ` (Bid ${liveQuote.bid} / Ask ${liveQuote.ask})` : ""
              }`}
              className="inline-flex items-center gap-1 rounded border border-emerald-500/40 bg-emerald-500/10 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-300"
            >
              <Radio className="h-2.5 w-2.5 animate-pulse" />
              LIVE • {liveQuote.source}
            </span>
          ) : (
            <span className="rounded bg-card px-1.5 py-0.5 text-[10px] text-slate-400">
              Snapshot price
            </span>
          )}

          {liveQuote && Math.abs(liveQuote.price - detail.last_price) >= 0.02 && (
            <span className="text-[11px] text-slate-400 tabular-nums">
              (Scan: ${detail.last_price.toFixed(2)})
            </span>
          )}

          <span
            title={`Preset "${presetName}" weighted score (0–100): 80+ A+ Setup | 68–79 Strong | 55–67 Watch | <55 Weak. See breakdown card below for exact formula.`}
            className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-xs font-bold tabular-nums ${scoreClass}`}
          >
            <span>
              Score {presetScore.toFixed(1)} • {scoreTier}
            </span>
            <HelpCircle className="h-3 w-3 opacity-75" />
          </span>

          <span
            className={`rounded-md px-2 py-0.5 text-xs font-semibold uppercase ${
              detail.bias === "bullish"
                ? "bg-emerald-500/15 text-emerald-300"
                : detail.bias === "bearish"
                ? "bg-rose-500/15 text-rose-300"
                : "bg-slate-700/60 text-slate-300"
            }`}
          >
            {detail.bias}
          </span>

          {detail.risk_flags.map((rf) => (
            <span
              key={rf.code}
              title={rf.detail}
              className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-[11px] font-medium ${
                rf.severity === "high"
                  ? "border-rose-500/40 bg-rose-500/15 text-rose-300"
                  : rf.severity === "warning"
                  ? "border-amber-500/40 bg-amber-500/15 text-amber-300"
                  : "border-slate-600 bg-card text-slate-300"
              }`}
            >
              <AlertTriangle className="h-3 w-3" />
              {rf.label}
            </span>
          ))}
        </div>
      </div>

      <div className="flex items-center gap-2">
        <WatchlistPopover symbol={detail.symbol} watchlistIds={detail.watchlist_ids} enableHotkey />
        <button
          type="button"
          onClick={() => refreshStock(detail.symbol)}
          disabled={loadingDetail}
          className="inline-flex items-center gap-1.5 rounded-md border border-border bg-card px-2.5 py-1.5 text-xs font-medium text-slate-200 hover:border-slate-500 hover:text-white disabled:opacity-50"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${loadingDetail ? "animate-spin" : ""}`} />
          <span>Refresh</span>
        </button>
      </div>
    </div>
  );
};
