import React from "react";
import { GitCompare } from "lucide-react";
import { useScreenerStore } from "../store/useScreenerStore";

export const CorrelationCard: React.FC = () => {
  const detail = useScreenerStore((s) => s.selectedDetail);
  if (!detail) return null;

  const corrPlugin = detail.plugin_results?.correlation_rs;
  const volPlugin = detail.plugin_results?.volume_volatility;
  const chPlugin = detail.plugin_results?.trend_channels;

  const metrics = {
    ...(corrPlugin?.metrics || {}),
    ...(chPlugin?.metrics || {}),
    ...(volPlugin?.metrics || {}),
  };

  const keysOrder = [
    "benchmark_symbol",
    "corr_20d",
    "corr_60d",
    "beta_60d",
    "mansfield_rs",
    "alpha_20d_pct",
    "sector_etf",
    "sector_corr_60d",
    "sector_alpha_20d_pct",
    "channel_type",
    "channel_position_pct",
    "rvol",
    "ttm_squeeze",
    "vp_poc",
  ];

  return (
    <section className="rounded-xl border border-border bg-panel p-4 flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <GitCompare className="h-4 w-4 text-sky-400" />
          <h2 className="text-sm font-bold text-white">Correlation, RS & Structure</h2>
        </div>
        <span className="text-[11px] text-slate-400">
          vs {detail.primary_benchmark} / {detail.sector_etf}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        {keysOrder
          .filter((k) => metrics[k])
          .map((k) => {
            const m = metrics[k];
            const valColor =
              m.status === "good"
                ? "text-emerald-300"
                : m.status === "bad"
                ? "text-rose-300"
                : m.status === "warn"
                ? "text-amber-300"
                : "text-slate-100";
            return (
              <div key={k} className="rounded-lg bg-canvas/80 px-2.5 py-2">
                <div className="truncate text-[10px] text-slate-400">{m.label}</div>
                <div className={`mt-0.5 tabular-nums text-xs font-semibold ${valColor}`}>{m.formatted}</div>
              </div>
            );
          })}
      </div>
    </section>
  );
};
