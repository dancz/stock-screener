import React from "react";
import { Layers } from "lucide-react";
import { useScreenerStore } from "../store/useScreenerStore";

export const ScoreBreakdownCard: React.FC = () => {
  const detail = useScreenerStore((s) => s.selectedDetail);
  if (!detail) return null;

  const plugins = Object.values(detail.plugin_results || {}).sort((a, b) => b.score - a.score);

  return (
    <section className="rounded-xl border border-border bg-panel p-4 flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Layers className="h-4 w-4 text-sky-400" />
          <h2 className="text-sm font-bold text-white">Score Breakdown & "Why Today?"</h2>
        </div>
        <span className="text-xs text-slate-400">{plugins.length} active analyzers</span>
      </div>

      {/* Per-Plugin Score Bars */}
      <div className="space-y-2">
        {plugins.map((p) => {
          const barColor =
            p.score >= 70
              ? "bg-emerald-500"
              : p.score >= 55
              ? "bg-sky-500"
              : p.score >= 42
              ? "bg-amber-500"
              : "bg-rose-500";
          return (
            <div key={p.plugin_id} className="space-y-1">
              <div className="flex items-center justify-between text-xs">
                <span className="font-medium text-slate-200">{p.display_name}</span>
                <div className="flex items-center gap-2">
                  <span
                    className={`text-[10px] uppercase font-semibold ${
                      p.bias === "bullish"
                        ? "text-emerald-400"
                        : p.bias === "bearish"
                        ? "text-rose-400"
                        : "text-slate-400"
                    }`}
                  >
                    {p.bias}
                  </span>
                  <span className="tabular-nums font-bold text-white w-8 text-right">
                    {Math.round(p.score)}
                  </span>
                </div>
              </div>
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-canvas">
                <div className={`h-full rounded-full ${barColor}`} style={{ width: `${Math.min(100, p.score)}%` }} />
              </div>
              <div className="text-[11px] text-slate-400">{p.summary}</div>
            </div>
          );
        })}
      </div>

      {/* Triggered Signals */}
      {detail.signals.length > 0 && (
        <div className="border-t border-border/70 pt-2.5">
          <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
            Triggered Signals
          </div>
          <div className="space-y-1.5 max-h-44 overflow-y-auto pr-1">
            {detail.signals.map((sig, idx) => (
              <div
                key={`${sig.code}-${idx}`}
                className="flex items-start justify-between gap-2 rounded bg-canvas/70 px-2.5 py-1.5 text-xs"
              >
                <div>
                  <span
                    className={`font-semibold ${
                      sig.direction === "bullish"
                        ? "text-emerald-300"
                        : sig.direction === "bearish"
                        ? "text-rose-300"
                        : "text-sky-300"
                    }`}
                  >
                    {sig.label}
                  </span>
                  {sig.detail && <div className="text-[11px] text-slate-400">{sig.detail}</div>}
                </div>
                {sig.score_impact !== 0 && (
                  <span className="shrink-0 tabular-nums text-[11px] font-semibold text-slate-300">
                    +{sig.score_impact.toFixed(0)}
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  );
};
