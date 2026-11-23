import React, { useEffect, useState } from "react";
import { Calculator, Crosshair, TrendingUp } from "lucide-react";
import { useScreenerStore } from "../store/useScreenerStore";

export const TradePlanCard: React.FC = () => {
  const detail = useScreenerStore((s) => s.selectedDetail);
  const settings = useScreenerStore((s) => s.settings);

  const [equity, setEquity] = useState<number>(25000);
  const [riskPct, setRiskPct] = useState<number>(1.0);

  useEffect(() => {
    if (settings?.account) {
      setEquity(settings.account.equity ?? 25000);
      setRiskPct(settings.account.risk_pct ?? 1.0);
    }
  }, [settings?.account]);

  if (!detail || !detail.trade_plan || detail.trade_plan.entry_low == null) return null;

  const plan = detail.trade_plan;
  const entryLow = Number(plan.entry_low ?? 0);
  const entryHigh = Number(plan.entry_high ?? entryLow);
  const stopLoss = Number(plan.stop_loss ?? 0);
  const tp1 = Number(plan.tp1 ?? 0);
  const tp2 = Number(plan.tp2 ?? 0);
  const rr = Number(plan.risk_reward ?? 0);
  const atr = Number(plan.atr ?? 0);

  const midEntry = (entryLow + entryHigh) / 2;
  const riskPerShare = Math.max(Number(plan.risk_per_share ?? 0) || Math.abs(midEntry - stopLoss), 0.01);
  const maxDollarRisk = Math.max(0, equity * (riskPct / 100));
  const shares = Math.max(0, Math.floor(maxDollarRisk / riskPerShare));
  const positionValue = shares * midEntry;
  const tp2Reward = Math.abs(tp2 - midEntry) * shares;

  const lossPct =
    plan.potential_loss_pct != null
      ? Number(plan.potential_loss_pct)
      : midEntry > 0
      ? (Math.abs(midEntry - stopLoss) / midEntry) * 100
      : 0;
  const tp1GainPct =
    plan.tp1_gain_pct != null
      ? Number(plan.tp1_gain_pct)
      : midEntry > 0
      ? (Math.abs(tp1 - midEntry) / midEntry) * 100
      : 0;
  const tp2GainPct =
    plan.potential_gain_pct != null
      ? Number(plan.potential_gain_pct)
      : midEntry > 0
      ? (Math.abs(tp2 - midEntry) / midEntry) * 100
      : 0;
  const indexTrend = plan.index_trend ?? "neutral";

  const dirColor =
    plan.direction === "LONG"
      ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/40"
      : plan.direction === "SHORT"
      ? "bg-rose-500/15 text-rose-300 border-rose-500/40"
      : "bg-sky-500/15 text-sky-300 border-sky-500/40";

  return (
    <section className="rounded-xl border border-border bg-panel p-4 flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Crosshair className="h-4 w-4 text-sky-400" />
          <h2 className="text-sm font-bold text-white">Trade Plan & Position Sizing</h2>
        </div>
        <div className="flex items-center gap-1.5">
          <span
            className={`inline-flex items-center gap-1 rounded border px-2 py-0.5 text-[10px] font-semibold ${
              plan.index_aligned
                ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
                : "border-slate-600 bg-card text-slate-400"
            }`}
          >
            <TrendingUp className="h-3 w-3" />
            {plan.index_aligned
              ? `${detail.primary_benchmark} Aligned`
              : `Index: ${indexTrend}`}
          </span>
          <span className={`rounded border px-2 py-0.5 text-[11px] font-bold tabular-nums ${dirColor}`}>
            {plan.direction} • {rr.toFixed(2)}R
          </span>
        </div>
      </div>

      <div className="rounded-lg bg-card/80 px-3 py-2">
        <div className="text-xs font-semibold text-slate-200">{plan.setup_name}</div>
        <div className="mt-0.5 text-[11px] text-slate-400">{plan.rationale}</div>
      </div>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <div className="rounded-lg border border-border/70 bg-canvas p-2.5">
          <div className="text-[10px] uppercase tracking-wider text-slate-400">Entry Zone</div>
          <div className="mt-0.5 tabular-nums text-xs font-bold text-sky-300">
            ${entryLow.toFixed(2)} – ${entryHigh.toFixed(2)}
          </div>
          {plan.dist_to_support_pct != null && (
            <div className="mt-0.5 text-[10px] text-slate-400 tabular-nums">
              Sup {Number(plan.dist_to_support_pct).toFixed(1)}% / Res{" "}
              {plan.dist_to_resistance_pct != null ? Number(plan.dist_to_resistance_pct).toFixed(1) : "—"}%
            </div>
          )}
        </div>
        <div className="rounded-lg border border-rose-500/30 bg-rose-500/5 p-2.5">
          <div className="text-[10px] uppercase tracking-wider text-rose-300/80">Stop-Loss</div>
          <div className="mt-0.5 tabular-nums text-xs font-bold text-rose-300">
            ${stopLoss.toFixed(2)}
          </div>
          <div className="mt-0.5 tabular-nums text-[10px] font-medium text-rose-400">
            -{lossPct.toFixed(1)}% Risk
          </div>
        </div>
        <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-2.5">
          <div className="text-[10px] uppercase tracking-wider text-emerald-300/80">Target 1</div>
          <div className="mt-0.5 tabular-nums text-xs font-bold text-emerald-300">
            ${tp1.toFixed(2)}
          </div>
          <div className="mt-0.5 tabular-nums text-[10px] font-medium text-emerald-400">
            +{tp1GainPct.toFixed(1)}% Gain
          </div>
        </div>
        <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-2.5">
          <div className="text-[10px] uppercase tracking-wider text-emerald-300/80">Target 2</div>
          <div className="mt-0.5 tabular-nums text-xs font-bold text-emerald-300">
            ${tp2.toFixed(2)}
          </div>
          <div className="mt-0.5 tabular-nums text-[10px] font-medium text-emerald-400">
            +{tp2GainPct.toFixed(1)}% Gain
          </div>
        </div>
      </div>

      {/* Interactive Position Sizing Calculator */}
      <div className="rounded-lg border border-border/70 bg-canvas/80 p-3">
        <div className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold text-slate-300">
          <Calculator className="h-3.5 w-3.5 text-sky-400" />
          <span>Position Sizing Calculator (ATR: ${atr.toFixed(2)})</span>
        </div>

        <div className="grid grid-cols-2 gap-2.5 mb-2.5">
          <label className="block">
            <span className="text-[10px] text-slate-400">Account Equity ($)</span>
            <input
              type="number"
              min={500}
              step={500}
              value={equity}
              onChange={(e) => setEquity(Number(e.target.value) || 0)}
              className="mt-0.5 w-full rounded border border-border bg-panel px-2 py-1 tabular-nums text-xs text-white focus:border-sky-500 focus:outline-none"
            />
          </label>
          <label className="block">
            <span className="text-[10px] text-slate-400">Risk per Trade (%)</span>
            <input
              type="number"
              min={0.1}
              max={10}
              step={0.25}
              value={riskPct}
              onChange={(e) => setRiskPct(Number(e.target.value) || 0)}
              className="mt-0.5 w-full rounded border border-border bg-panel px-2 py-1 tabular-nums text-xs text-white focus:border-sky-500 focus:outline-none"
            />
          </label>
        </div>

        <div className="grid grid-cols-4 gap-2 text-center border-t border-border/60 pt-2">
          <div>
            <div className="text-[10px] text-slate-400">Shares</div>
            <div className="tabular-nums text-xs font-bold text-white">{shares}</div>
          </div>
          <div>
            <div className="text-[10px] text-slate-400">Position</div>
            <div className="tabular-nums text-xs font-semibold text-slate-200">
              ${positionValue.toLocaleString(undefined, { maximumFractionDigits: 0 })}
            </div>
          </div>
          <div>
            <div className="text-[10px] text-slate-400">Max Risk</div>
            <div className="tabular-nums text-xs font-semibold text-rose-400">
              -${maxDollarRisk.toFixed(0)}
            </div>
          </div>
          <div>
            <div className="text-[10px] text-slate-400">TP2 Gain</div>
            <div className="tabular-nums text-xs font-semibold text-emerald-400">
              +${tp2Reward.toFixed(0)}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
};
