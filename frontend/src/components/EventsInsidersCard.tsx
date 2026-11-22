import React from "react";
import { Calendar, Users } from "lucide-react";
import { useScreenerStore } from "../store/useScreenerStore";

export const EventsInsidersCard: React.FC = () => {
  const detail = useScreenerStore((s) => s.selectedDetail);
  if (!detail) return null;

  const ev = detail.events_insiders || {};
  const insiders = ev.insiders || [];
  const news = ev.news || [];

  return (
    <section className="rounded-xl border border-border bg-panel p-4 flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Calendar className="h-4 w-4 text-sky-400" />
          <h2 className="text-sm font-bold text-white">Events, Earnings & Insider Trades</h2>
        </div>
      </div>

      {/* Events Strip */}
      <div className="grid grid-cols-3 gap-2">
        <div className="rounded-lg bg-canvas/80 px-2.5 py-2">
          <div className="text-[10px] text-slate-400">Next Earnings</div>
          <div
            className={`mt-0.5 tabular-nums text-xs font-semibold ${
              ev.days_to_earnings != null && ev.days_to_earnings >= 0 && ev.days_to_earnings <= 7
                ? "text-rose-400"
                : "text-slate-100"
            }`}
          >
            {ev.next_earnings_date
              ? `${ev.next_earnings_date}${ev.days_to_earnings != null ? ` (${ev.days_to_earnings}d)` : ""}`
              : "N/A"}
          </div>
        </div>
        <div className="rounded-lg bg-canvas/80 px-2.5 py-2">
          <div className="text-[10px] text-slate-400">Ex-Dividend</div>
          <div className="mt-0.5 tabular-nums text-xs font-semibold text-slate-100">
            {ev.ex_dividend_date || "N/A"}
          </div>
        </div>
        <div className="rounded-lg bg-canvas/80 px-2.5 py-2">
          <div className="text-[10px] text-slate-400">Analyst Target</div>
          <div className="mt-0.5 tabular-nums text-xs font-semibold text-slate-100">
            {ev.analyst_target ? `$${Number(ev.analyst_target).toFixed(2)}` : "N/A"}
          </div>
        </div>
      </div>

      {/* Insiders */}
      <div>
        <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
          <Users className="h-3.5 w-3.5" />
          <span>Insider Activity ({insiders.length})</span>
        </div>
        {insiders.length === 0 ? (
          <div className="rounded bg-canvas/60 px-3 py-2 text-xs text-slate-400">
            No recent OpenInsider cluster or C-suite buy records found.
          </div>
        ) : (
          <div className="max-h-36 space-y-1 overflow-y-auto">
            {insiders.map((t, i) => (
              <div
                key={i}
                className="flex items-center justify-between gap-2 rounded bg-canvas/80 px-2.5 py-1.5 text-xs"
              >
                <div className="min-w-0">
                  <div className="truncate font-medium text-slate-200">
                    {t.insider_name} <span className="text-slate-400">({t.title})</span>
                  </div>
                  <div className="text-[10px] text-slate-500">
                    {t.trade_date} • {t.source}
                  </div>
                </div>
                <span className="shrink-0 tabular-nums font-semibold text-emerald-400">{t.value_raw}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Optional Finnhub Headlines */}
      {news.length > 0 && (
        <div className="border-t border-border/60 pt-2">
          <div className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
            Recent Headlines
          </div>
          <div className="space-y-1">
            {news.slice(0, 3).map((n, idx) => (
              <a
                key={idx}
                href={n.url}
                target="_blank"
                rel="noreferrer"
                className="block truncate text-xs text-sky-300 hover:underline"
              >
                • {n.headline}
              </a>
            ))}
          </div>
        </div>
      )}
    </section>
  );
};
