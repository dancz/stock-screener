import React, { useState } from "react";
import {
  Activity,
  BarChart3,
  ChevronDown,
  Lightbulb,
  Loader2,
  Play,
  Radio,
  Search,
  Settings,
  TrendingUp,
} from "lucide-react";
import { useScreenerStore } from "../store/useScreenerStore";

interface TopBarProps {
  onOpenSettings: () => void;
}

export const TopBar: React.FC<TopBarProps> = ({ onOpenSettings }) => {
  const [tickerInput, setTickerInput] = useState("");
  const [scanMenuOpen, setScanMenuOpen] = useState(false);

  const activeView = useScreenerStore((s) => s.activeView);
  const setActiveView = useScreenerStore((s) => s.setActiveView);
  const tradeIdeas = useScreenerStore((s) => s.tradeIdeas);
  const marketRegime = useScreenerStore((s) => s.marketRegime);
  const scanStatus = useScreenerStore((s) => s.scanStatus);
  const ibkrStatus = useScreenerStore((s) => s.ibkrStatus);
  const lastScan = useScreenerStore((s) => s.lastScan);
  const jumpToTicker = useScreenerStore((s) => s.jumpToTicker);
  const triggerScan = useScreenerStore((s) => s.triggerScan);

  const handleJump = async (e: React.FormEvent) => {
    e.preventDefault();
    const sym = tickerInput.trim().toUpperCase();
    if (!sym) return;
    setTickerInput("");
    await jumpToTicker(sym);
  };

  const regimeColor =
    marketRegime?.regime === "RISK_ON"
      ? "border-emerald-500/40 bg-emerald-500/15 text-emerald-300"
      : marketRegime?.regime === "RISK_OFF"
      ? "border-rose-500/40 bg-rose-500/15 text-rose-300"
      : "border-amber-500/40 bg-amber-500/15 text-amber-300";

  const isScanning = scanStatus?.running ?? false;
  const ideasCount = (tradeIdeas?.buys?.length ?? 0) + (tradeIdeas?.sells?.length ?? 0);
  const pct =
    scanStatus && scanStatus.total > 0
      ? Math.min(100, Math.round((scanStatus.processed / scanStatus.total) * 100))
      : 0;

  return (
    <header className="flex h-14 shrink-0 items-center justify-between gap-3 border-b border-border bg-panel px-4">
      {/* Left: Brand & Market Regime */}
      <div className="flex items-center gap-3 overflow-x-auto">
        <div className="flex items-center gap-2 pr-2">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-sky-500/20 text-sky-400">
            <TrendingUp className="h-4 w-4" />
          </div>
          <div className="leading-tight">
            <div className="text-sm font-bold tracking-tight text-white">SwingPulse</div>
            <div className="text-[10px] text-slate-400">Daily US Screener</div>
          </div>
        </div>

        <div className="h-6 w-px bg-border" />

        {/* View Switcher */}
        <nav
          aria-label="Primary view switcher"
          className="inline-flex shrink-0 items-center rounded-lg border border-border bg-canvas p-0.5"
        >
          <button
            type="button"
            onClick={() => setActiveView("screener")}
            className={`inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-semibold transition-colors ${
              activeView === "screener"
                ? "bg-sky-600 text-white shadow-sm"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            <BarChart3 className="h-3.5 w-3.5" />
            <span>Screener &amp; Chart</span>
          </button>
          <button
            type="button"
            onClick={() => setActiveView("trade_ideas")}
            className={`inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-semibold transition-colors ${
              activeView === "trade_ideas"
                ? "bg-sky-600 text-white shadow-sm"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            <Lightbulb className="h-3.5 w-3.5" />
            <span>Trade Ideas</span>
            {ideasCount > 0 && (
              <span
                className={`rounded-full px-1.5 py-0 text-[10px] font-bold tabular-nums ${
                  activeView === "trade_ideas"
                    ? "bg-white/20 text-white"
                    : "bg-sky-500/20 text-sky-300"
                }`}
              >
                {ideasCount}
              </span>
            )}
          </button>
        </nav>

        <div className="hidden h-6 w-px bg-border sm:block" />

        {/* Market Regime Strip */}
        <div className="flex items-center gap-2">
          <span
            className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-semibold ${regimeColor}`}
            title={marketRegime?.summary || "Run a scan to calculate market regime"}
          >
            <Activity className="h-3 w-3" />
            {marketRegime?.regime?.replace("_", "-") || "NO REGIME"}
          </span>

          {marketRegime?.benchmarks?.map((b) => (
            <div
              key={b.symbol}
              className="hidden items-center gap-1.5 rounded border border-border/70 bg-card px-2 py-0.5 text-xs lg:flex"
            >
              <span className="font-semibold text-slate-300">
                {b.symbol === "^VIX" ? "VIX" : b.symbol}
              </span>
              <span className="tabular-nums text-slate-400">{b.price.toFixed(2)}</span>
              <span
                className={`tabular-nums font-medium ${
                  b.symbol === "^VIX"
                    ? b.change_1d_pct > 0
                      ? "text-rose-400"
                      : "text-emerald-400"
                    : b.change_1d_pct >= 0
                    ? "text-emerald-400"
                    : "text-rose-400"
                }`}
              >
                {b.change_1d_pct >= 0 ? "+" : ""}
                {b.change_1d_pct.toFixed(2)}%
              </span>
            </div>
          ))}

          {marketRegime?.sectors && marketRegime.sectors.length > 0 && (
            <div className="hidden items-center gap-1.5 rounded border border-border/60 bg-canvas px-2 py-0.5 text-[11px] text-slate-400 xl:flex">
              <span>Lead:</span>
              <span className="font-semibold text-emerald-400">
                {marketRegime.sectors[0].symbol} ({marketRegime.sectors[0].change_5d_pct >= 0 ? "+" : ""}
                {marketRegime.sectors[0].change_5d_pct.toFixed(1)}%)
              </span>
            </div>
          )}
        </div>
      </div>

      {/* Right: Quick Jump, Scan Controls, IBKR, Settings */}
      <div className="flex items-center gap-2.5">
        <form onSubmit={handleJump} className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
          <input
            type="text"
            value={tickerInput}
            onChange={(e) => setTickerInput(e.target.value)}
            placeholder="Analyze ticker (e.g. PLTR)..."
            className="w-44 rounded-md border border-border bg-canvas py-1.5 pl-8 pr-2.5 text-xs text-slate-100 placeholder-slate-500 focus:w-52 focus:border-sky-500 focus:outline-none transition-all"
          />
        </form>

        {/* IBKR Status */}
        <div
          title={ibkrStatus?.message || "Checking IBKR Gateway..."}
          className={`hidden items-center gap-1.5 rounded-md border px-2.5 py-1 text-[11px] font-medium md:inline-flex ${
            ibkrStatus?.connected
              ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
              : "border-border bg-card text-slate-400"
          }`}
        >
          <Radio className={`h-3 w-3 ${ibkrStatus?.connected ? "text-emerald-400" : "text-slate-500"}`} />
          <span>{ibkrStatus?.connected ? "IBKR Live" : "Yahoo + BS"}</span>
        </div>

        {/* Scan Status / Trigger */}
        <div className="relative">
          {isScanning ? (
            <div className="flex items-center gap-2 rounded-md border border-sky-500/40 bg-sky-500/15 px-3 py-1.5 text-xs text-sky-200">
              <Loader2 className="h-3.5 w-3.5 animate-spin text-sky-400" />
              <span className="max-w-[160px] truncate">{scanStatus?.message}</span>
              {scanStatus && scanStatus.total > 0 && (
                <span className="tabular-nums font-semibold text-sky-300">{pct}%</span>
              )}
            </div>
          ) : (
            <>
              <button
                type="button"
                onClick={() => setScanMenuOpen((o) => !o)}
                className="inline-flex items-center gap-1.5 rounded-md bg-sky-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-sky-500 transition-colors"
                title={
                  lastScan?.finished_at
                    ? `Last scan: ${new Date(lastScan.finished_at).toLocaleString()}`
                    : "Run market scan"
                }
              >
                <Play className="h-3.5 w-3.5 fill-current" />
                <span>Run Scan</span>
                <ChevronDown className="h-3.5 w-3.5 opacity-80" />
              </button>

              {scanMenuOpen && (
                <>
                  <div className="fixed inset-0 z-40" onClick={() => setScanMenuOpen(false)} />
                  <div className="absolute right-0 z-50 mt-1.5 w-64 rounded-lg border border-border bg-[#121926] p-1.5 shadow-2xl">
                    <button
                      type="button"
                      onClick={() => {
                        setScanMenuOpen(false);
                        triggerScan("quick");
                      }}
                      className="flex w-full flex-col rounded-md px-3 py-2 text-left hover:bg-slate-800/80"
                    >
                      <span className="text-xs font-semibold text-white">Quick Scan (~45-70 tickers)</span>
                      <span className="text-[11px] text-slate-400">
                        Top leaders, movers & all your watchlists (~25s)
                      </span>
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setScanMenuOpen(false);
                        triggerScan("full");
                      }}
                      className="flex w-full flex-col rounded-md px-3 py-2 text-left hover:bg-slate-800/80"
                    >
                      <span className="text-xs font-semibold text-white">Full Universe Scan (~600 tickers)</span>
                      <span className="text-[11px] text-slate-400">
                        S&P 500 + Nasdaq 100 + Movers + Deep Top 90
                      </span>
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setScanMenuOpen(false);
                        triggerScan("watchlists");
                      }}
                      className="flex w-full flex-col rounded-md px-3 py-2 text-left hover:bg-slate-800/80"
                    >
                      <span className="text-xs font-semibold text-white">Refresh Watchlists Only</span>
                      <span className="text-[11px] text-slate-400">
                        Deep analyze only stocks in your custom lists
                      </span>
                    </button>
                  </div>
                </>
              )}
            </>
          )}
        </div>

        <button
          type="button"
          onClick={onOpenSettings}
          className="rounded-md border border-border bg-card p-1.5 text-slate-300 hover:border-slate-500 hover:text-white"
          title="Settings & Plugins"
        >
          <Settings className="h-4 w-4" />
        </button>
      </div>
    </header>
  );
};
