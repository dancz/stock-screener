import React from "react";
import { Info, Layers } from "lucide-react";
import { useScreenerStore } from "../store/useScreenerStore";

export const ScoreBreakdownCard: React.FC = () => {
  const detail = useScreenerStore((s) => s.selectedDetail);
  const activePreset = useScreenerStore((s) => s.activePreset);
  const presets = useScreenerStore((s) => s.presets);
  if (!detail) return null;

  const presetMeta = presets[activePreset];
  const rawWeights = presetMeta?.weights || {};
  const plugins = Object.values(detail.plugin_results || {});

  // Compute normalized weight & point contribution exactly matching backend scoring.py
  const activeWeights = plugins.map((p) => {
    const w = Number(rawWeights[p.plugin_id] ?? 0.05);
    return { plugin: p, rawWeight: w > 0 ? w : 0 };
  });
  const totalWeight = activeWeights.reduce((acc, item) => acc + item.rawWeight, 0);

  const enrichedPlugins = activeWeights
    .map(({ plugin, rawWeight }) => {
      const normWeight = totalWeight > 0 ? rawWeight / totalWeight : 0;
      const contrib = plugin.score * normWeight;
      return { plugin, normWeightPct: normWeight * 100, contrib };
    })
    .sort((a, b) => b.contrib - a.contrib || b.plugin.score - a.plugin.score);

  const presetTotal = detail.preset_scores?.[activePreset] ?? detail.composite_score;

  return (
    <section className="rounded-xl border border-border bg-panel p-4 flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Layers className="h-4 w-4 text-sky-400" />
          <h2 className="text-sm font-bold text-white">Score Breakdown & &quot;Why Today?&quot;</h2>
        </div>
        <span className="tabular-nums text-xs font-bold text-sky-300">
          Total: {presetTotal.toFixed(1)} / 100
        </span>
      </div>

      {/* Clear Score Explanation Box */}
      <div className="rounded-lg border border-sky-500/25 bg-sky-500/5 px-3 py-2 text-[11px] text-slate-300 space-y-1">
        <div className="flex items-center gap-1.5 font-semibold text-sky-300">
          <Info className="h-3.5 w-3.5 shrink-0" />
          <span>
            How Score Works — Preset: {presetMeta?.name || activePreset}
          </span>
        </div>
        <p className="text-slate-400 leading-relaxed">
          {presetMeta?.description || "Weighted average (0–100) across active analyzer modules."}
        </p>
        <div className="flex flex-wrap gap-2 pt-0.5 text-[10px]">
          <span className="rounded bg-emerald-500/15 px-1.5 py-0.5 text-emerald-300">80–100: A+ High Conviction</span>
          <span className="rounded bg-sky-500/15 px-1.5 py-0.5 text-sky-300">68–79: Strong Setup</span>
          <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-amber-300">55–67: Watchlist</span>
          <span className="rounded bg-rose-500/15 px-1.5 py-0.5 text-rose-300">&lt;55: Neutral / Weak</span>
        </div>
      </div>

      {/* Per-Plugin Score Bars with Weight & Contribution */}
      <div className="space-y-2">
        {enrichedPlugins.map(({ plugin: p, normWeightPct, contrib }) => {
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
              <div className="flex items-center justify-between text-xs gap-2">
                <span className="font-medium text-slate-200 truncate">{p.display_name}</span>
                <div className="flex items-center gap-2 shrink-0">
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
                  <span className="tabular-nums text-[10px] text-slate-400">
                    w {normWeightPct.toFixed(0)}% (+{contrib.toFixed(1)})
                  </span>
                  <span className="tabular-nums font-bold text-white w-7 text-right">
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
