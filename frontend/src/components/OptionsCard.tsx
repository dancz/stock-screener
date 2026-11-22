import React from "react";
import { AlertCircle, ShieldCheck, Sigma } from "lucide-react";
import { useScreenerStore } from "../store/useScreenerStore";

export const OptionsCard: React.FC = () => {
  const detail = useScreenerStore((s) => s.selectedDetail);
  if (!detail) return null;

  const iv = detail.options?.iv_metrics;
  const proposals = detail.options?.proposals || [];

  const regimeBadge =
    iv?.iv_regime === "high"
      ? "bg-amber-500/15 text-amber-300 border-amber-500/40"
      : iv?.iv_regime === "low"
      ? "bg-sky-500/15 text-sky-300 border-sky-500/40"
      : "bg-slate-700/60 text-slate-300 border-slate-600";

  return (
    <section className="rounded-xl border border-border bg-panel p-4 flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Sigma className="h-4 w-4 text-sky-400" />
          <h2 className="text-sm font-bold text-white">Options & Strategy Proposals</h2>
        </div>
        <div className="flex items-center gap-1.5">
          {iv?.data_source && (
            <span className="rounded bg-card px-2 py-0.5 text-[10px] font-medium text-slate-300">
              {iv.data_source}
            </span>
          )}
          {iv?.iv_regime && (
            <span className={`rounded border px-2 py-0.5 text-[10px] font-bold uppercase ${regimeBadge}`}>
              {iv.iv_regime} IV
            </span>
          )}
        </div>
      </div>

      {/* IV vs HV Row */}
      {iv && (
        <div className="grid grid-cols-4 gap-2 rounded-lg bg-canvas p-2.5 text-center">
          <div>
            <div className="text-[10px] text-slate-400">ATM IV</div>
            <div className="tabular-nums text-xs font-bold text-white">
              {iv.atm_iv != null ? `${iv.atm_iv.toFixed(1)}%` : "N/A"}
            </div>
          </div>
          <div>
            <div className="text-[10px] text-slate-400">HV (20d)</div>
            <div className="tabular-nums text-xs font-semibold text-slate-200">
              {iv.hv20 != null ? `${iv.hv20.toFixed(1)}%` : "N/A"}
            </div>
          </div>
          <div>
            <div className="text-[10px] text-slate-400">IV / HV</div>
            <div className="tabular-nums text-xs font-bold text-sky-300">
              {iv.iv_hv_ratio != null ? `${iv.iv_hv_ratio.toFixed(2)}x` : "N/A"}
            </div>
          </div>
          <div>
            <div className="text-[10px] text-slate-400">Liquidity</div>
            <div className="flex items-center justify-center gap-1 text-xs font-semibold">
              {iv.liquidity_ok ? (
                <span className="inline-flex items-center gap-1 text-emerald-400">
                  <ShieldCheck className="h-3.5 w-3.5" /> OK
                </span>
              ) : (
                <span className="text-amber-400">Thin</span>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Proposals */}
      {proposals.length === 0 ? (
        <div className="rounded-lg bg-canvas/60 p-3 text-xs text-slate-400">
          No liquid option strategy proposals available for this ticker (run Deep Refresh or verify option chain).
        </div>
      ) : (
        <div className="space-y-2.5">
          {proposals.map((prop, idx) => (
            <div key={`${prop.strategy_name}-${idx}`} className="rounded-lg border border-border/80 bg-canvas/80 p-3 space-y-2">
              <div className="flex items-center justify-between gap-2">
                <div>
                  <div className="text-xs font-bold text-white">{prop.strategy_name}</div>
                  <div className="text-[11px] text-slate-400">{prop.rationale}</div>
                </div>
                <span className="shrink-0 rounded bg-card px-2 py-0.5 tabular-nums text-[11px] font-semibold text-sky-300">
                  {prop.expiry} ({prop.dte} DTE)
                </span>
              </div>

              {prop.earnings_warning && (
                <div className="flex items-center gap-1.5 rounded bg-amber-500/10 px-2 py-1 text-[11px] text-amber-300">
                  <AlertCircle className="h-3.5 w-3.5 shrink-0" />
                  <span>{prop.earnings_warning}</span>
                </div>
              )}

              <div className="space-y-1">
                {prop.legs.map((leg, lIdx) => (
                  <div
                    key={lIdx}
                    className="flex items-center justify-between rounded bg-panel px-2.5 py-1 text-[11px] tabular-nums"
                  >
                    <span className="flex items-center gap-2">
                      <span
                        className={`font-bold ${
                          leg.action === "BUY" ? "text-emerald-400" : "text-rose-400"
                        }`}
                      >
                        {leg.action}
                      </span>
                      <span className="font-semibold text-white">
                        ${leg.strike} {leg.option_type}
                      </span>
                    </span>
                    <span className="text-slate-400">
                      Mid ${leg.mid_price.toFixed(2)} • Δ {leg.delta != null ? leg.delta.toFixed(2) : "—"} • OI{" "}
                      {leg.oi ?? 0}
                    </span>
                  </div>
                ))}
              </div>

              <div className="grid grid-cols-4 gap-2 border-t border-border/60 pt-2 text-center text-[11px]">
                <div>
                  <div className="text-[10px] text-slate-400">
                    {prop.net_credit_debit >= 0 ? "Net Credit" : "Net Debit"}
                  </div>
                  <div className="tabular-nums font-bold text-white">
                    ${Math.abs(prop.net_credit_debit).toFixed(2)}
                  </div>
                </div>
                <div>
                  <div className="text-[10px] text-slate-400">Max Profit</div>
                  <div className="tabular-nums font-semibold text-emerald-400">
                    {prop.max_profit != null ? `$${prop.max_profit.toFixed(0)}` : "Unlimited"}
                  </div>
                </div>
                <div>
                  <div className="text-[10px] text-slate-400">Max Loss</div>
                  <div className="tabular-nums font-semibold text-rose-400">
                    {prop.max_loss != null ? `$${prop.max_loss.toFixed(0)}` : "N/A"}
                  </div>
                </div>
                <div>
                  <div className="text-[10px] text-slate-400">Est. PoP / BE</div>
                  <div className="tabular-nums font-semibold text-slate-200">
                    {prop.pop_estimate != null ? `${prop.pop_estimate.toFixed(0)}%` : "—"} / $
                    {prop.breakeven.map((b) => b.toFixed(1)).join("-")}
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
};
