import React from "react";
import { Building2 } from "lucide-react";
import { useScreenerStore } from "../store/useScreenerStore";

function fmtCap(val: unknown): string {
  if (typeof val !== "number" || !Number.isFinite(val)) return "N/A";
  if (val >= 1e12) return `$${(val / 1e12).toFixed(2)}T`;
  if (val >= 1e9) return `$${(val / 1e9).toFixed(1)}B`;
  if (val >= 1e6) return `$${(val / 1e6).toFixed(0)}M`;
  return `$${val.toFixed(0)}`;
}

function fmtNum(val: unknown, suffix = "", digits = 1): string {
  if (typeof val !== "number" || !Number.isFinite(val)) return "N/A";
  return `${val.toFixed(digits)}${suffix}`;
}

export const FundamentalsCard: React.FC = () => {
  const detail = useScreenerStore((s) => s.selectedDetail);
  if (!detail) return null;

  const f = detail.fundamentals || {};
  const health = detail.plugin_results?.fundamental_health;
  const grade = String(health?.metrics?.health_grade?.formatted || "—");

  const gradeColor = grade.startsWith("A")
    ? "border-emerald-500/40 bg-emerald-500/15 text-emerald-300"
    : grade.startsWith("B")
    ? "border-sky-500/40 bg-sky-500/15 text-sky-300"
    : grade === "C"
    ? "border-amber-500/40 bg-amber-500/15 text-amber-300"
    : "border-rose-500/40 bg-rose-500/15 text-rose-300";

  const rows = [
    { label: "Market Cap", value: fmtCap(f.market_cap) },
    { label: "Trailing P/E", value: fmtNum(f.pe_trailing) },
    { label: "Forward P/E", value: fmtNum(f.pe_forward) },
    { label: "PEG Ratio", value: fmtNum(f.peg_ratio, "", 2) },
    { label: "Op. Margin", value: fmtNum(f.operating_margin, "%") },
    { label: "Profit Margin", value: fmtNum(f.profit_margin, "%") },
    { label: "ROE", value: fmtNum(f.roe, "%") },
    { label: "Rev Growth YoY", value: fmtNum(f.revenue_growth, "%") },
    { label: "EPS Growth YoY", value: fmtNum(f.earnings_growth, "%") },
    { label: "Debt / Equity", value: fmtNum(f.debt_to_equity, "%", 0) },
    { label: "Current Ratio", value: fmtNum(f.current_ratio, "", 2) },
    { label: "FCF Yield", value: fmtNum(f.fcf_yield_pct, "%") },
  ];

  return (
    <section className="rounded-xl border border-border bg-panel p-4 flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Building2 className="h-4 w-4 text-sky-400" />
          <h2 className="text-sm font-bold text-white">Company Financial Health</h2>
        </div>
        <span className={`rounded border px-2 py-0.5 text-xs font-bold ${gradeColor}`}>
          Grade {grade}
        </span>
      </div>

      <div className="grid grid-cols-3 gap-2">
        {rows.map((r) => (
          <div key={r.label} className="rounded-lg bg-canvas/80 px-2.5 py-2">
            <div className="text-[10px] text-slate-400">{r.label}</div>
            <div className="mt-0.5 tabular-nums text-xs font-semibold text-slate-100">{r.value}</div>
          </div>
        ))}
      </div>
    </section>
  );
};
