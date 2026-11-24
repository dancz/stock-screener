import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  Activity,
  AlertOctagon,
  Award,
  BarChart2,
  Download,
  ExternalLink,
  History,
  ListChecks,
  Play,
  PlusCircle,
  Power,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  TrendingDown,
  TrendingUp,
  XCircle,
} from "lucide-react";
import {
  ColorType,
  CrosshairMode,
  IChartApi,
  ISeriesApi,
  LineStyle,
  Time,
  createChart,
} from "lightweight-charts";
import { api } from "../api/client";
import { useScreenerStore } from "../store/useScreenerStore";
import type {
  PaperAccountRun,
  PaperAttribution,
  PaperAttributionBucket,
  PaperEquityPoint,
  PaperOrderEvent,
  PaperOverview,
  PaperTrade,
} from "../types/screener";

type SubTab = "open" | "journal" | "attribution" | "events";

function fmtUsd(val: number | null | undefined, signed = false): string {
  if (val === null || val === undefined || Number.isNaN(val)) return "—";
  const prefix = signed && val > 0 ? "+" : val < 0 ? "-" : "";
  return `${prefix}$${Math.abs(val).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

function fmtPct(val: number | null | undefined, signed = true): string {
  if (val === null || val === undefined || Number.isNaN(val)) return "—";
  const prefix = signed && val > 0 ? "+" : "";
  return `${prefix}${val.toFixed(2)}%`;
}

function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function AttributionTable({ title, rows }: { title: string; rows: PaperAttributionBucket[] }) {
  return (
    <div className="overflow-hidden rounded-xl border border-border bg-panel">
      <div className="border-b border-border bg-card/60 px-3.5 py-2 text-xs font-bold uppercase tracking-wider text-slate-300">
        {title}
      </div>
      {rows.length === 0 ? (
        <div className="px-3.5 py-4 text-xs text-slate-500">No closed trades in this category yet.</div>
      ) : (
        <table className="w-full text-left text-xs">
          <thead className="border-b border-border/70 bg-canvas/50 text-[10px] uppercase text-slate-400">
            <tr>
              <th className="px-3 py-2">Bucket</th>
              <th className="px-2 py-2 text-right">Trades</th>
              <th className="px-2 py-2 text-right">Win Rate</th>
              <th className="px-2 py-2 text-right">Avg R</th>
              <th className="px-2 py-2 text-right">PF</th>
              <th className="px-3 py-2 text-right">PnL</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border/50">
            {rows.map((r) => (
              <tr key={r.key} className="hover:bg-card/40">
                <td className="px-3 py-2 font-medium text-slate-200">{r.key}</td>
                <td className="px-2 py-2 text-right tabular-nums text-slate-300">{r.trades}</td>
                <td className="px-2 py-2 text-right tabular-nums text-slate-300">{r.win_rate_pct.toFixed(1)}%</td>
                <td
                  className={`px-2 py-2 text-right tabular-nums font-semibold ${
                    r.avg_r > 0 ? "text-emerald-400" : r.avg_r < 0 ? "text-rose-400" : "text-slate-400"
                  }`}
                >
                  {r.avg_r > 0 ? "+" : ""}
                  {r.avg_r.toFixed(2)}R
                </td>
                <td className="px-2 py-2 text-right tabular-nums text-slate-300">
                  {r.profit_factor !== null ? r.profit_factor.toFixed(2) : "—"}
                </td>
                <td
                  className={`px-3 py-2 text-right tabular-nums font-semibold ${
                    r.total_pnl_usd > 0
                      ? "text-emerald-400"
                      : r.total_pnl_usd < 0
                      ? "text-rose-400"
                      : "text-slate-400"
                  }`}
                >
                  {fmtUsd(r.total_pnl_usd, true)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export const PaperTradingView: React.FC = () => {
  const openIdeaInScreener = useScreenerStore((s) => s.openIdeaInScreener);
  const refreshPaperSummary = useScreenerStore((s) => s.refreshPaperSummary);
  const updateSettings = useScreenerStore((s) => s.updateSettings);
  const settings = useScreenerStore((s) => s.settings);

  const [runs, setRuns] = useState<PaperAccountRun[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
  const [overview, setOverview] = useState<PaperOverview | null>(null);
  const [curve, setCurve] = useState<PaperEquityPoint[]>([]);
  const [openTrades, setOpenTrades] = useState<PaperTrade[]>([]);
  const [journalTrades, setJournalTrades] = useState<PaperTrade[]>([]);
  const [attribution, setAttribution] = useState<PaperAttribution | null>(null);
  const [events, setEvents] = useState<PaperOrderEvent[]>([]);
  const [subTab, setSubTab] = useState<SubTab>("open");
  const [loading, setLoading] = useState(true);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [bannerMsg, setBannerMsg] = useState<string | null>(null);

  const chartElRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const eqSeriesRef = useRef<ISeriesApi<"Area"> | null>(null);
  const spySeriesRef = useRef<ISeriesApi<"Line"> | null>(null);

  const loadAll = useCallback(async (runIdParam?: number | null) => {
    setLoading(true);
    try {
      const targetRunId = runIdParam !== undefined ? runIdParam : selectedRunId;
      const [runsRes, ov, eqRes, activeRes, closedRes, attrRes, evRes] = await Promise.all([
        api.getPaperRuns(),
        api.getPaperOverview(targetRunId),
        api.getPaperEquityCurve(targetRunId),
        api.getPaperTrades(targetRunId, "PENDING_ENTRY,OPEN,PARTIAL_TP1"),
        api.getPaperTrades(targetRunId, "CLOSED,CANCELLED,REJECTED"),
        api.getPaperAttribution(targetRunId),
        api.getPaperEvents(targetRunId),
      ]);
      setRuns(runsRes.runs);
      setOverview(ov);
      if (targetRunId === null) {
        setSelectedRunId(ov.run.id);
      }
      setCurve(eqRes.points);
      setOpenTrades(activeRes.trades);
      setJournalTrades(closedRes.trades);
      setAttribution(attrRes);
      setEvents(evRes.events);
    } finally {
      setLoading(false);
    }
  }, [selectedRunId]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  // Initialize Equity Curve chart
  useEffect(() => {
    const el = chartElRef.current;
    if (!el) return;

    const chart = createChart(el, {
      layout: {
        background: { type: ColorType.Solid, color: "#0d131f" },
        textColor: "#94a3b8",
        fontFamily: "Inter, system-ui, sans-serif",
      },
      grid: {
        vertLines: { color: "rgba(37, 48, 71, 0.35)" },
        horzLines: { color: "rgba(37, 48, 71, 0.35)" },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: "#253047" },
      timeScale: { borderColor: "#253047", timeVisible: false, secondsVisible: false },
      width: el.clientWidth,
      height: 220,
    });

    const eqSeries = chart.addAreaSeries({
      lineColor: "#38bdf8",
      topColor: "rgba(56, 189, 248, 0.28)",
      bottomColor: "rgba(56, 189, 248, 0.02)",
      lineWidth: 2,
      title: "Portfolio Equity",
    });

    const spySeries = chart.addLineSeries({
      color: "#94a3b8",
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      title: "SPY Benchmark",
    });

    chartRef.current = chart;
    eqSeriesRef.current = eqSeries;
    spySeriesRef.current = spySeries;

    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        if (entry.contentRect.width > 0) {
          chart.applyOptions({ width: Math.floor(entry.contentRect.width) });
        }
      }
    });
    ro.observe(el);

    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
      eqSeriesRef.current = null;
      spySeriesRef.current = null;
    };
  }, []);

  // Sync equity curve data cleanly
  useEffect(() => {
    const eqSeries = eqSeriesRef.current;
    const spySeries = spySeriesRef.current;
    const chart = chartRef.current;
    if (!eqSeries || !spySeries || !chart) return;

    const uniqueByDate = new Map<string, PaperEquityPoint>();
    for (const pt of curve) {
      uniqueByDate.set(pt.date, pt);
    }
    const sorted = Array.from(uniqueByDate.values()).sort((a, b) => a.date.localeCompare(b.date));

    eqSeries.setData(
      sorted.map((pt) => ({
        time: pt.date as Time,
        value: pt.equity,
      }))
    );
    spySeries.setData(
      sorted
        .filter((pt) => pt.spy_equity !== null && pt.spy_equity > 0)
        .map((pt) => ({
          time: pt.date as Time,
          value: pt.spy_equity as number,
        }))
    );
    if (sorted.length > 0) {
      chart.timeScale().fitContent();
    }
  }, [curve]);

  const handleEvaluateNow = async () => {
    setBusyAction("eval");
    setBannerMsg(null);
    try {
      const res = await api.evaluatePaperNow();
      if (!res.executed) {
        setBannerMsg(res.reason || "Allocation cycle did not run.");
      } else {
        const openedCnt = res.opened?.length ?? 0;
        const rejCnt = res.rejected?.length ?? 0;
        const sampleRej = res.rejected && res.rejected.length > 0 ? ` First filter: ${res.rejected[0].symbol} (${res.rejected[0].reason})` : "";
        setBannerMsg(`Allocation cycle complete: ${openedCnt} opened, ${rejCnt} filtered.${sampleRej}`);
      }
      await Promise.all([loadAll(), refreshPaperSummary()]);
    } catch (err) {
      setBannerMsg((err as Error).message);
    } finally {
      setBusyAction(null);
    }
  };

  const handleStepLifecycle = async () => {
    setBusyAction("step");
    setBannerMsg(null);
    try {
      const res = await api.stepPaperLifecycle();
      const s = res.step as Record<string, number>;
      setBannerMsg(
        `Lifecycle step complete: filled=${s.filled ?? 0}, closed=${s.closed ?? 0}, BE moved=${s.be_moved ?? 0}, expired=${s.expired ?? 0}.`
      );
      await Promise.all([loadAll(), refreshPaperSummary()]);
    } catch (err) {
      setBannerMsg((err as Error).message);
    } finally {
      setBusyAction(null);
    }
  };

  const handleToggleAuto = async () => {
    if (!overview?.paper_settings) return;
    setBusyAction("toggle_auto");
    try {
      const nextEnabled = !overview.paper_settings.enabled;
      const basePaper = settings?.paper_trading ?? overview.paper_settings;
      await updateSettings({
        paper_trading: { ...basePaper, enabled: nextEnabled },
      });
      await loadAll();
    } finally {
      setBusyAction(null);
    }
  };

  const handleCloseTrade = async (tradeId: number, symbol: string) => {
    if (!window.confirm(`Close virtual paper position for ${symbol} at current mark price?`)) return;
    setBusyAction(`close_${tradeId}`);
    try {
      await api.closePaperTrade(tradeId);
      setBannerMsg(`Closed trade #${tradeId} (${symbol}).`);
      await Promise.all([loadAll(), refreshPaperSummary()]);
    } finally {
      setBusyAction(null);
    }
  };

  const handleKillSwitch = async () => {
    if (!overview) return;
    const isHalted = overview.run.kill_switch_triggered;
    if (!isHalted) {
      const closeAll = window.confirm(
        "Trigger Emergency Kill-Switch? Click OK to also close all open/pending positions, or Cancel to abort."
      );
      if (!closeAll) return;
      setBusyAction("kill");
      try {
        await api.togglePaperKillSwitch({
          halt: true,
          reason: "Manual Emergency Stop from Paper Trading view",
          close_all: true,
        });
        setBannerMsg("Kill-switch activated and all open positions closed.");
        await Promise.all([loadAll(), refreshPaperSummary()]);
      } finally {
        setBusyAction(null);
      }
    } else {
      setBusyAction("kill");
      try {
        await api.togglePaperKillSwitch({ halt: false });
        setBannerMsg("Paper trading resumed.");
        await loadAll();
      } finally {
        setBusyAction(null);
      }
    }
  };

  const handleNewExperiment = async () => {
    const capStr = window.prompt(
      "Start a clean new Paper Experiment? Enter initial capital (USD):",
      String(overview?.run.initial_capital ?? 25000)
    );
    if (!capStr) return;
    const cap = Number(capStr);
    if (!Number.isFinite(cap) || cap < 100) return;
    const name = window.prompt("Experiment label (optional):", "") || undefined;
    setBusyAction("reset");
    try {
      const ov = await api.resetPaperRun({ name, initial_capital: cap });
      setSelectedRunId(ov.run.id);
      setBannerMsg(`Started new experiment: ${ov.run.name} (${fmtUsd(ov.run.initial_capital)}).`);
      await Promise.all([loadAll(ov.run.id), refreshPaperSummary()]);
    } finally {
      setBusyAction(null);
    }
  };

  const k = overview?.kpis;
  const r = overview?.run;
  const pSettings = overview?.paper_settings;
  const isViewingActive = r?.is_active ?? true;

  return (
    <div className="mx-auto max-w-[1440px] space-y-4 pb-8">
      {/* Header & Controls */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-panel px-4 py-3">
        <div className="flex flex-wrap items-center gap-3">
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-base font-bold text-white">Autonomous Paper Trading</h1>
              <span className="rounded-full border border-sky-500/40 bg-sky-500/15 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider text-sky-300">
                {r?.mode === "ibkr_paper" ? "IBKR PAPER DU*" : "VIRTUAL SIMULATOR (READ-ONLY)"}
              </span>
              {r?.kill_switch_triggered ? (
                <span className="inline-flex items-center gap-1 rounded-full border border-rose-500/50 bg-rose-500/15 px-2 py-0.5 text-[10px] font-bold text-rose-300">
                  <AlertOctagon className="h-3 w-3" />
                  HALTED
                </span>
              ) : pSettings?.enabled ? (
                <span className="inline-flex items-center gap-1 rounded-full border border-emerald-500/40 bg-emerald-500/15 px-2 py-0.5 text-[10px] font-bold text-emerald-300">
                  <ShieldCheck className="h-3 w-3" />
                  AUTO-SCAN ACTIVE
                </span>
              ) : (
                <span className="rounded-full border border-slate-600/60 bg-slate-700/30 px-2 py-0.5 text-[10px] font-semibold text-slate-300">
                  MANUAL / STANDBY
                </span>
              )}
            </div>
            <p className="mt-0.5 text-xs text-slate-400">
              Evaluates top Trade Ideas after scans (Conviction ≥ {pSettings?.min_conviction ?? 68}, R:R ≥{" "}
              {pSettings?.min_rr ?? 1.8}x, {pSettings?.risk_per_trade_pct ?? 1}% risk/trade) and tracks live performance.
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {runs.length > 0 && (
            <select
              value={selectedRunId ?? r?.id ?? ""}
              onChange={(e) => {
                const nextId = Number(e.target.value);
                setSelectedRunId(nextId);
                loadAll(nextId);
              }}
              className="rounded-lg border border-border bg-canvas px-2.5 py-1.5 text-xs text-slate-200"
              title="Select experiment run"
            >
              {runs.map((runItem) => (
                <option key={runItem.id} value={runItem.id}>
                  #{runItem.id} {runItem.name} {runItem.is_active ? "(Active)" : "(Archived)"}
                </option>
              ))}
            </select>
          )}

          {isViewingActive && (
            <>
              <button
                type="button"
                onClick={handleToggleAuto}
                disabled={busyAction !== null}
                className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-semibold transition-colors ${
                  pSettings?.enabled
                    ? "border-emerald-500/50 bg-emerald-500/15 text-emerald-300 hover:bg-emerald-500/25"
                    : "border-border bg-card text-slate-300 hover:text-white"
                }`}
                title="Toggle automatic execution after each scan"
              >
                <Power className="h-3.5 w-3.5" />
                <span>{pSettings?.enabled ? "Auto: ON" : "Auto: OFF"}</span>
              </button>

              <button
                type="button"
                onClick={handleEvaluateNow}
                disabled={busyAction !== null}
                className="inline-flex items-center gap-1.5 rounded-lg bg-sky-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-sky-500 disabled:opacity-50"
              >
                <Play className="h-3.5 w-3.5 fill-current" />
                <span>{busyAction === "eval" ? "Allocating..." : "Run Auto-Allocation"}</span>
              </button>

              <button
                type="button"
                onClick={handleStepLifecycle}
                disabled={busyAction !== null}
                className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-card px-2.5 py-1.5 text-xs font-semibold text-slate-200 hover:border-sky-500/60"
              >
                <RefreshCw className={`h-3.5 w-3.5 ${busyAction === "step" ? "animate-spin" : ""}`} />
                <span>Update Marks</span>
              </button>

              <button
                type="button"
                onClick={handleNewExperiment}
                disabled={busyAction !== null}
                className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-card px-2.5 py-1.5 text-xs font-semibold text-slate-200 hover:border-amber-500/60"
              >
                <PlusCircle className="h-3.5 w-3.5" />
                <span>New Experiment</span>
              </button>

              <button
                type="button"
                onClick={handleKillSwitch}
                disabled={busyAction !== null}
                className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-semibold ${
                  r?.kill_switch_triggered
                    ? "border-emerald-500/50 bg-emerald-500/15 text-emerald-300 hover:bg-emerald-500/25"
                    : "border-rose-500/50 bg-rose-500/15 text-rose-300 hover:bg-rose-500/25"
                }`}
              >
                <ShieldAlert className="h-3.5 w-3.5" />
                <span>{r?.kill_switch_triggered ? "Resume Trading" : "Emergency Stop"}</span>
              </button>
            </>
          )}
        </div>
      </div>

      {bannerMsg && (
        <div className="flex items-center justify-between rounded-lg border border-sky-500/40 bg-sky-500/10 px-3.5 py-2 text-xs text-sky-200">
          <span>{bannerMsg}</span>
          <button type="button" onClick={() => setBannerMsg(null)} className="text-sky-400 hover:text-white">
            Dismiss
          </button>
        </div>
      )}

      {r?.kill_switch_triggered && r.kill_switch_reason && (
        <div className="rounded-lg border border-rose-500/50 bg-rose-500/10 px-3.5 py-2 text-xs text-rose-200">
          <strong>Kill-Switch Active:</strong> {r.kill_switch_reason}
        </div>
      )}

      {/* 6 KPI Cards */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <div className="rounded-xl border border-border bg-panel p-3">
          <div className="text-[11px] font-medium text-slate-400">Account Equity</div>
          <div className="mt-1 text-lg font-bold tabular-nums text-white">{fmtUsd(r?.current_equity)}</div>
          <div
            className={`mt-0.5 text-xs font-semibold tabular-nums ${
              (k?.total_return_usd ?? 0) >= 0 ? "text-emerald-400" : "text-rose-400"
            }`}
          >
            {fmtUsd(k?.total_return_usd, true)} ({fmtPct(k?.total_return_pct)})
          </div>
        </div>

        <div className="rounded-xl border border-border bg-panel p-3">
          <div className="text-[11px] font-medium text-slate-400">Alpha vs SPY</div>
          <div
            className={`mt-1 text-lg font-bold tabular-nums ${
              (k?.alpha_vs_spy_pct ?? 0) >= 0 ? "text-emerald-400" : "text-rose-400"
            }`}
          >
            {k?.alpha_vs_spy_pct !== null && k?.alpha_vs_spy_pct !== undefined
              ? fmtPct(k.alpha_vs_spy_pct)
              : "—"}
          </div>
          <div className="mt-0.5 text-xs text-slate-400">
            SPY: {k?.spy_return_pct !== null && k?.spy_return_pct !== undefined ? fmtPct(k.spy_return_pct) : "N/A"} · Cash{" "}
            {fmtUsd(r?.cash_balance)}
          </div>
        </div>

        <div className="rounded-xl border border-border bg-panel p-3">
          <div className="text-[11px] font-medium text-slate-400">Win Rate &amp; Closed</div>
          <div className="mt-1 text-lg font-bold tabular-nums text-white">
            {k ? `${k.win_rate_pct.toFixed(1)}%` : "—"}
          </div>
          <div className="mt-0.5 text-xs text-slate-400">
            {k?.wins_count ?? 0}W / {k?.losses_count ?? 0}L · {k?.open_count ?? 0} Open, {k?.pending_count ?? 0} Pending
          </div>
        </div>

        <div className="rounded-xl border border-border bg-panel p-3">
          <div className="text-[11px] font-medium text-slate-400">Expectancy &amp; PF</div>
          <div
            className={`mt-1 text-lg font-bold tabular-nums ${
              (k?.expectancy_r ?? 0) >= 0 ? "text-emerald-400" : "text-rose-400"
            }`}
          >
            {k ? `${k.expectancy_r >= 0 ? "+" : ""}${k.expectancy_r.toFixed(2)}R` : "—"}
          </div>
          <div className="mt-0.5 text-xs text-slate-400">
            PF: {k?.profit_factor !== null && k?.profit_factor !== undefined ? k.profit_factor.toFixed(2) : "—"} · Avg{" "}
            {fmtUsd(k?.expectancy_usd, true)}
          </div>
        </div>

        <div className="rounded-xl border border-border bg-panel p-3">
          <div className="text-[11px] font-medium text-slate-400">Drawdown &amp; Sharpe</div>
          <div className="mt-1 text-lg font-bold tabular-nums text-rose-400">
            {k ? fmtPct(k.max_drawdown_pct, false) : "—"}
          </div>
          <div className="mt-0.5 text-xs text-slate-400">
            Current: {k ? fmtPct(k.current_drawdown_pct, false) : "—"} · Sharpe{" "}
            {k?.sharpe_ratio !== null && k?.sharpe_ratio !== undefined ? k.sharpe_ratio.toFixed(2) : "—"}
          </div>
        </div>

        <div className="rounded-xl border border-border bg-panel p-3">
          <div className="text-[11px] font-medium text-slate-400">Execution &amp; Hold</div>
          <div className="mt-1 text-lg font-bold tabular-nums text-white">
            {k?.avg_slippage_bps !== null && k?.avg_slippage_bps !== undefined
              ? `${k.avg_slippage_bps.toFixed(1)} bps`
              : "0.0 bps"}
          </div>
          <div className="mt-0.5 text-xs text-slate-400">
            Fill Rate: {k?.fill_rate_pct !== null && k?.fill_rate_pct !== undefined ? `${k.fill_rate_pct}%` : "—"} ·{" "}
            {k?.avg_hold_days ?? 0}d hold
          </div>
        </div>
      </div>

      {/* Equity Curve Chart */}
      <div className="rounded-xl border border-border bg-panel p-3">
        <div className="mb-2 flex items-center justify-between">
          <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-wider text-slate-300">
            <Activity className="h-3.5 w-3.5 text-sky-400" />
            <span>Equity Curve vs Normalized SPY Benchmark</span>
          </div>
          <div className="flex items-center gap-4 text-[11px] text-slate-400">
            <span className="inline-flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-full bg-sky-400" />
              Portfolio Equity
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="h-0.5 w-3 border-t border-dashed border-slate-400" />
              SPY Benchmark
            </span>
          </div>
        </div>
        <div ref={chartElRef} className="w-full" />
      </div>

      {/* Sub-Tabs */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="inline-flex items-center gap-1 rounded-lg border border-border bg-panel p-1">
          {[
            { id: "open" as SubTab, label: `Open & Pending (${openTrades.length})`, icon: ListChecks },
            { id: "journal" as SubTab, label: `Trade Journal (${journalTrades.length})`, icon: History },
            { id: "attribution" as SubTab, label: "Signal Attribution", icon: Award },
            { id: "events" as SubTab, label: `Activity Log (${events.length})`, icon: BarChart2 },
          ].map((t) => {
            const Icon = t.icon;
            return (
              <button
                key={t.id}
                type="button"
                onClick={() => setSubTab(t.id)}
                className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-semibold transition-colors ${
                  subTab === t.id ? "bg-sky-600 text-white" : "text-slate-400 hover:text-white"
                }`}
              >
                <Icon className="h-3.5 w-3.5" />
                <span>{t.label}</span>
              </button>
            );
          })}
        </div>

        {subTab === "journal" && (
          <a
            href={api.getPaperExportCsvUrl(selectedRunId)}
            className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-card px-3 py-1.5 text-xs font-semibold text-slate-200 hover:border-sky-500"
          >
            <Download className="h-3.5 w-3.5" />
            <span>Export CSV</span>
          </a>
        )}
      </div>

      {/* Sub-Tab Content */}
      {loading && !overview ? (
        <div className="rounded-xl border border-border bg-panel p-8 text-center text-xs text-slate-400">
          Loading Paper Trading telemetry...
        </div>
      ) : subTab === "open" ? (
        <div className="overflow-x-auto rounded-xl border border-border bg-panel">
          {openTrades.length === 0 ? (
            <div className="p-8 text-center text-xs text-slate-400">
              No open or pending virtual paper positions in this experiment. Run a scan or click{" "}
              <strong className="text-slate-200">Run Auto-Allocation</strong> to evaluate current Trade Ideas.
            </div>
          ) : (
            <table className="w-full text-left text-xs">
              <thead className="border-b border-border bg-card/60 text-[10px] uppercase text-slate-400">
                <tr>
                  <th className="px-3 py-2.5">Symbol</th>
                  <th className="px-2 py-2.5">Dir / Status</th>
                  <th className="px-2 py-2.5 text-right">Qty</th>
                  <th className="px-2 py-2.5 text-right">Entry / Limit</th>
                  <th className="px-2 py-2.5 text-right">Mark</th>
                  <th className="px-2 py-2.5 text-right">Stop Loss</th>
                  <th className="px-2 py-2.5 text-right">TP1 / TP2</th>
                  <th className="px-2 py-2.5 text-right">Unrealized PnL</th>
                  <th className="px-2 py-2.5 text-right">Conv / Setup</th>
                  <th className="px-3 py-2.5 text-right">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/50">
                {openTrades.map((t) => {
                  const refEntry = t.entry_fill_price ?? t.planned_limit_price;
                  const mark = t.last_mark_price ?? refEntry;
                  const curR = t.risk_amount_usd > 0 ? t.unrealized_pnl_usd / t.risk_amount_usd : 0;
                  return (
                    <tr key={t.id} className="hover:bg-card/40">
                      <td className="px-3 py-2.5">
                        <button
                          type="button"
                          onClick={() => openIdeaInScreener(t.symbol)}
                          className="inline-flex items-center gap-1 font-bold text-sky-400 hover:underline"
                        >
                          <span>{t.symbol}</span>
                          <ExternalLink className="h-3 w-3" />
                        </button>
                        <div className="text-[10px] text-slate-500">{t.sector_at_entry || "US Equity"}</div>
                      </td>
                      <td className="px-2 py-2.5">
                        <span
                          className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-bold ${
                            t.direction === "LONG"
                              ? "bg-emerald-500/15 text-emerald-300"
                              : "bg-rose-500/15 text-rose-300"
                          }`}
                        >
                          {t.direction === "LONG" ? (
                            <TrendingUp className="h-3 w-3" />
                          ) : (
                            <TrendingDown className="h-3 w-3" />
                          )}
                          {t.direction}
                        </span>
                        <div className="mt-0.5 text-[10px] font-medium text-slate-400">
                          {t.status}
                          {t.be_moved ? " · BE Protected" : ""}
                        </div>
                      </td>
                      <td className="px-2 py-2.5 text-right tabular-nums text-slate-200">{t.quantity_planned}</td>
                      <td className="px-2 py-2.5 text-right tabular-nums text-slate-200">
                        ${refEntry.toFixed(2)}
                        <div className="text-[10px] text-slate-500">Risk ${t.risk_amount_usd.toFixed(0)}</div>
                      </td>
                      <td className="px-2 py-2.5 text-right tabular-nums font-medium text-white">
                        ${mark.toFixed(2)}
                      </td>
                      <td className="px-2 py-2.5 text-right tabular-nums text-rose-300">
                        ${t.current_stop_loss.toFixed(2)}
                      </td>
                      <td className="px-2 py-2.5 text-right tabular-nums text-emerald-300">
                        ${t.planned_tp1.toFixed(2)}
                        <div className="text-[10px] text-slate-500">TP2 ${t.planned_tp2.toFixed(2)}</div>
                      </td>
                      <td
                        className={`px-2 py-2.5 text-right tabular-nums font-semibold ${
                          t.unrealized_pnl_usd >= 0 ? "text-emerald-400" : "text-rose-400"
                        }`}
                      >
                        {fmtUsd(t.unrealized_pnl_usd, true)}
                        <div className="text-[10px]">
                          {curR >= 0 ? "+" : ""}
                          {curR.toFixed(2)}R
                        </div>
                      </td>
                      <td className="px-2 py-2.5 text-right">
                        <span className="font-semibold text-slate-200">{t.conviction_at_entry.toFixed(0)}</span>
                        <div className="max-w-[140px] truncate text-[10px] text-slate-500" title={t.setup_name}>
                          {t.setup_name}
                        </div>
                      </td>
                      <td className="px-3 py-2.5 text-right">
                        {isViewingActive && (
                          <button
                            type="button"
                            onClick={() => handleCloseTrade(t.id, t.symbol)}
                            disabled={busyAction !== null}
                            className="inline-flex items-center gap-1 rounded border border-rose-500/40 bg-rose-500/10 px-2 py-1 text-[11px] font-semibold text-rose-300 hover:bg-rose-500/20"
                          >
                            <XCircle className="h-3 w-3" />
                            <span>{t.status === "PENDING_ENTRY" ? "Cancel" : "Close"}</span>
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      ) : subTab === "journal" ? (
        <div className="overflow-x-auto rounded-xl border border-border bg-panel">
          {journalTrades.length === 0 ? (
            <div className="p-8 text-center text-xs text-slate-400">
              No closed or cancelled paper trades recorded yet.
            </div>
          ) : (
            <table className="w-full text-left text-xs">
              <thead className="border-b border-border bg-card/60 text-[10px] uppercase text-slate-400">
                <tr>
                  <th className="px-3 py-2.5">Symbol</th>
                  <th className="px-2 py-2.5">Direction</th>
                  <th className="px-2 py-2.5 text-right">Qty</th>
                  <th className="px-2 py-2.5 text-right">Entry</th>
                  <th className="px-2 py-2.5 text-right">Exit</th>
                  <th className="px-2 py-2.5 text-right">Slippage</th>
                  <th className="px-2 py-2.5 text-right">Realized PnL</th>
                  <th className="px-2 py-2.5 text-right">R-Mult</th>
                  <th className="px-2 py-2.5">Exit Reason</th>
                  <th className="px-3 py-2.5 text-right">Closed</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/50">
                {journalTrades.map((t) => (
                  <tr key={t.id} className="hover:bg-card/40">
                    <td className="px-3 py-2">
                      <button
                        type="button"
                        onClick={() => openIdeaInScreener(t.symbol)}
                        className="font-bold text-sky-400 hover:underline"
                      >
                        {t.symbol}
                      </button>
                      <div className="text-[10px] text-slate-500">Conv {t.conviction_at_entry.toFixed(0)}</div>
                    </td>
                    <td className="px-2 py-2">
                      <span
                        className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${
                          t.direction === "LONG"
                            ? "bg-emerald-500/15 text-emerald-300"
                            : "bg-rose-500/15 text-rose-300"
                        }`}
                      >
                        {t.direction}
                      </span>
                    </td>
                    <td className="px-2 py-2 text-right tabular-nums text-slate-300">
                      {t.quantity_filled || t.quantity_planned}
                    </td>
                    <td className="px-2 py-2 text-right tabular-nums text-slate-300">
                      {t.entry_fill_price !== null ? `$${t.entry_fill_price.toFixed(2)}` : `$${t.planned_limit_price.toFixed(2)}`}
                    </td>
                    <td className="px-2 py-2 text-right tabular-nums text-slate-300">
                      {t.exit_fill_price !== null ? `$${t.exit_fill_price.toFixed(2)}` : "—"}
                    </td>
                    <td className="px-2 py-2 text-right tabular-nums text-slate-400">
                      {t.entry_slippage_bps !== null ? `${t.entry_slippage_bps.toFixed(1)} bps` : "—"}
                    </td>
                    <td
                      className={`px-2 py-2 text-right tabular-nums font-semibold ${
                        t.realized_pnl_usd > 0
                          ? "text-emerald-400"
                          : t.realized_pnl_usd < 0
                          ? "text-rose-400"
                          : "text-slate-400"
                      }`}
                    >
                      {t.status === "CLOSED" ? fmtUsd(t.realized_pnl_usd, true) : "—"}
                    </td>
                    <td
                      className={`px-2 py-2 text-right tabular-nums font-semibold ${
                        t.r_multiple > 0
                          ? "text-emerald-400"
                          : t.r_multiple < 0
                          ? "text-rose-400"
                          : "text-slate-400"
                      }`}
                    >
                      {t.status === "CLOSED" ? `${t.r_multiple > 0 ? "+" : ""}${t.r_multiple.toFixed(2)}R` : "—"}
                    </td>
                    <td className="px-2 py-2 text-slate-300">{t.exit_reason || t.status}</td>
                    <td className="px-3 py-2 text-right text-[11px] text-slate-400">{fmtDateTime(t.closed_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      ) : subTab === "attribution" ? (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <AttributionTable title="By Entry Signal Code" rows={attribution?.by_signal ?? []} />
          <AttributionTable title="By Conviction Band" rows={attribution?.by_conviction_band ?? []} />
          <AttributionTable title="By Direction & Market Regime" rows={[...(attribution?.by_direction ?? []), ...(attribution?.by_regime ?? [])]} />
          <AttributionTable title="By Exit Reason & Sector" rows={[...(attribution?.by_exit_reason ?? []), ...(attribution?.by_sector ?? [])]} />
        </div>
      ) : (
        <div className="overflow-hidden rounded-xl border border-border bg-panel">
          {events.length === 0 ? (
            <div className="p-8 text-center text-xs text-slate-400">No activity events recorded yet.</div>
          ) : (
            <div className="divide-y divide-border/50">
              {events.map((ev) => (
                <div key={ev.id} className="flex items-start justify-between gap-3 px-4 py-2.5 text-xs hover:bg-card/40">
                  <div>
                    <span className="mr-2 rounded border border-border bg-canvas px-1.5 py-0.5 text-[10px] font-bold uppercase text-sky-300">
                      {ev.event_type}
                    </span>
                    {ev.symbol && <span className="mr-2 font-bold text-white">{ev.symbol}</span>}
                    <span className="text-slate-300">{ev.message}</span>
                  </div>
                  <span className="shrink-0 text-[11px] text-slate-500">{fmtDateTime(ev.created_at)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
};
