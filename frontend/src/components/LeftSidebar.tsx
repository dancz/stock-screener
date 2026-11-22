import React, { useEffect, useState } from "react";
import { AlertTriangle, Bookmark, Plus, Search, Trash2 } from "lucide-react";
import { useScreenerStore } from "../store/useScreenerStore";
import { WatchlistPopover } from "./WatchlistPopover";

const SIGNAL_CHIPS: Array<{ label: string; signal?: string; bias?: string; noEarn?: boolean }> = [
  { label: "All" },
  { label: "Lower Channel", signal: "LOWER_CHANNEL_TOUCH" },
  { label: "Breakout", signal: "CHANNEL_BREAKOUT_UP,SR_BREAK_RETEST" },
  { label: "Squeeze", signal: "SQUEEZE_ON,SQUEEZE_FIRED" },
  { label: "Insider Buy", signal: "INSIDER_CLUSTER_BUY,CEO_CFO_BUY" },
  { label: "No Earn <7d", noEarn: true },
  { label: "Long", bias: "bullish" },
  { label: "Short", bias: "bearish" },
];

export const LeftSidebar: React.FC = () => {
  const [newListName, setNewListName] = useState("");
  const [showNewList, setShowNewList] = useState(false);

  const sidebarTab = useScreenerStore((s) => s.sidebarTab);
  const setSidebarTab = useScreenerStore((s) => s.setSidebarTab);
  const activePreset = useScreenerStore((s) => s.activePreset);
  const presets = useScreenerStore((s) => s.presets);
  const setActivePreset = useScreenerStore((s) => s.setActivePreset);
  const watchlists = useScreenerStore((s) => s.watchlists);
  const activeWatchlistId = useScreenerStore((s) => s.activeWatchlistId);
  const setActiveWatchlistId = useScreenerStore((s) => s.setActiveWatchlistId);
  const createWatchlist = useScreenerStore((s) => s.createWatchlist);
  const deleteWatchlist = useScreenerStore((s) => s.deleteWatchlist);

  const signalFilter = useScreenerStore((s) => s.signalFilter);
  const biasFilter = useScreenerStore((s) => s.biasFilter);
  const excludeEarnings7d = useScreenerStore((s) => s.excludeEarnings7d);
  const searchFilter = useScreenerStore((s) => s.searchFilter);
  const setSignalFilter = useScreenerStore((s) => s.setSignalFilter);
  const setBiasFilter = useScreenerStore((s) => s.setBiasFilter);
  const setExcludeEarnings7d = useScreenerStore((s) => s.setExcludeEarnings7d);
  const setSearchFilter = useScreenerStore((s) => s.setSearchFilter);

  const items = useScreenerStore((s) => s.items);
  const selectedSymbol = useScreenerStore((s) => s.selectedSymbol);
  const selectStock = useScreenerStore((s) => s.selectStock);
  const loadingList = useScreenerStore((s) => s.loadingList);

  // Keyboard navigation ↑ / ↓ across ranked stocks
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT")) {
        return;
      }
      if (items.length === 0) return;
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const idx = items.findIndex((it) => it.symbol === selectedSymbol);
        const nextIdx =
          e.key === "ArrowDown"
            ? idx < 0
              ? 0
              : Math.min(items.length - 1, idx + 1)
            : idx <= 0
            ? 0
            : idx - 1;
        if (items[nextIdx] && items[nextIdx].symbol !== selectedSymbol) {
          selectStock(items[nextIdx].symbol);
        }
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [items, selectedSymbol, selectStock]);

  const handleCreateList = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newListName.trim()) return;
    await createWatchlist(newListName.trim());
    setNewListName("");
    setShowNewList(false);
  };

  const applyChip = async (chip: (typeof SIGNAL_CHIPS)[number]) => {
    if (!chip.signal && !chip.bias && !chip.noEarn) {
      useScreenerStore.setState({ signalFilter: null, biasFilter: null, excludeEarnings7d: false });
      await useScreenerStore.getState().fetchScreener();
      return;
    }
    if (chip.signal) {
      await setSignalFilter(signalFilter === chip.signal ? null : chip.signal);
    } else if (chip.bias) {
      await setBiasFilter(biasFilter === chip.bias ? null : chip.bias);
    } else if (chip.noEarn) {
      await setExcludeEarnings7d(!excludeEarnings7d);
    }
  };

  const scoreColor = (score: number) => {
    if (score >= 72) return "bg-emerald-500/20 text-emerald-300 border-emerald-500/40";
    if (score >= 58) return "bg-sky-500/20 text-sky-300 border-sky-500/40";
    if (score >= 45) return "bg-amber-500/20 text-amber-300 border-amber-500/40";
    return "bg-rose-500/20 text-rose-300 border-rose-500/40";
  };

  return (
    <aside className="flex w-[345px] shrink-0 flex-col border-r border-border bg-panel">
      {/* Mode Switch Tabs */}
      <div className="grid grid-cols-2 border-b border-border p-1.5 gap-1">
        <button
          type="button"
          onClick={() => setSidebarTab("screener")}
          className={`rounded-md py-1.5 text-xs font-semibold transition-colors ${
            sidebarTab === "screener"
              ? "bg-card text-white shadow-sm"
              : "text-slate-400 hover:text-slate-200"
          }`}
        >
          Daily Screener
        </button>
        <button
          type="button"
          onClick={() => setSidebarTab("watchlists")}
          className={`flex items-center justify-center gap-1.5 rounded-md py-1.5 text-xs font-semibold transition-colors ${
            sidebarTab === "watchlists"
              ? "bg-card text-white shadow-sm"
              : "text-slate-400 hover:text-slate-200"
          }`}
        >
          <Bookmark className="h-3.5 w-3.5" />
          <span>Watchlists ({watchlists.length})</span>
        </button>
      </div>

      {/* Watchlist Selector (when in Watchlists tab) */}
      {sidebarTab === "watchlists" && (
        <div className="border-b border-border bg-canvas/50 p-2.5 space-y-2">
          <div className="flex items-center justify-between gap-1.5">
            <div className="flex flex-wrap gap-1 flex-1">
              {watchlists.map((wl) => (
                <button
                  key={wl.id}
                  type="button"
                  onClick={() => setActiveWatchlistId(wl.id)}
                  className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-[11px] font-medium transition-colors ${
                    activeWatchlistId === wl.id
                      ? "border-sky-500/50 bg-sky-500/15 text-sky-200"
                      : "border-border bg-card text-slate-400 hover:text-slate-200"
                  }`}
                >
                  <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: wl.color }} />
                  <span>{wl.name}</span>
                  <span className="text-[10px] opacity-70">{wl.count}</span>
                </button>
              ))}
            </div>
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={() => setShowNewList((s) => !s)}
                className="rounded border border-border bg-card p-1 text-slate-300 hover:text-white"
                title="Create new watchlist"
              >
                <Plus className="h-3.5 w-3.5" />
              </button>
              {activeWatchlistId !== null && watchlists.length > 1 && (
                <button
                  type="button"
                  onClick={() => deleteWatchlist(activeWatchlistId)}
                  className="rounded border border-border bg-card p-1 text-slate-400 hover:border-rose-500/50 hover:text-rose-400"
                  title="Delete selected watchlist"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              )}
            </div>
          </div>

          {showNewList && (
            <form onSubmit={handleCreateList} className="flex gap-1.5">
              <input
                type="text"
                value={newListName}
                onChange={(e) => setNewListName(e.target.value)}
                placeholder="New watchlist name..."
                autoFocus
                className="flex-1 rounded border border-border bg-canvas px-2 py-1 text-xs text-slate-100 focus:border-sky-500 focus:outline-none"
              />
              <button
                type="submit"
                className="rounded bg-emerald-600 px-2.5 py-1 text-xs font-semibold text-white hover:bg-emerald-500"
              >
                Create
              </button>
            </form>
          )}
        </div>
      )}

      {/* Preset & Search & Quick Filters */}
      <div className="space-y-2 border-b border-border p-2.5">
        <div className="flex items-center gap-2">
          <select
            value={activePreset}
            onChange={(e) => setActivePreset(e.target.value)}
            className="w-full rounded-md border border-border bg-canvas px-2.5 py-1.5 text-xs font-medium text-slate-100 focus:border-sky-500 focus:outline-none"
          >
            {Object.entries(presets).map(([id, p]) => (
              <option key={id} value={id}>
                Preset: {p.name}
              </option>
            ))}
          </select>
        </div>

        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-500" />
          <input
            type="text"
            value={searchFilter}
            onChange={(e) => setSearchFilter(e.target.value)}
            placeholder="Filter list by ticker or name..."
            className="w-full rounded-md border border-border bg-canvas py-1.5 pl-8 pr-2.5 text-xs text-slate-100 placeholder-slate-500 focus:border-sky-500 focus:outline-none"
          />
        </div>

        <div className="flex flex-wrap gap-1">
          {SIGNAL_CHIPS.map((chip) => {
            const active =
              (!chip.signal && !chip.bias && !chip.noEarn && !signalFilter && !biasFilter && !excludeEarnings7d) ||
              (chip.signal && signalFilter === chip.signal) ||
              (chip.bias && biasFilter === chip.bias) ||
              (chip.noEarn && excludeEarnings7d);
            return (
              <button
                key={chip.label}
                type="button"
                onClick={() => applyChip(chip)}
                className={`rounded px-2 py-0.5 text-[11px] font-medium transition-colors ${
                  active
                    ? "bg-sky-500/25 text-sky-300 ring-1 ring-sky-500/50"
                    : "bg-card text-slate-400 hover:bg-slate-800 hover:text-slate-200"
                }`}
              >
                {chip.label}
              </button>
            );
          })}
        </div>
      </div>

      {/* Count bar */}
      <div className="flex items-center justify-between border-b border-border/60 px-3 py-1.5 text-[11px] text-slate-400">
        <span>{loadingList ? "Loading..." : `${items.length} stocks ranked`}</span>
        <span className="text-[10px] text-slate-500">Use ↑ / ↓ keys</span>
      </div>

      {/* Stock List */}
      <div className="flex-1 overflow-y-auto divide-y divide-border/50">
        {items.length === 0 && !loadingList && (
          <div className="p-6 text-center text-xs text-slate-400">
            No stocks match the active filter or no scan has run yet.
          </div>
        )}

        {items.map((item) => {
          const isSelected = item.symbol === selectedSymbol;
          const hasRisk = item.risk_flags.some((r) => r.severity === "high" || r.severity === "warning");
          return (
            <div
              key={item.symbol}
              onClick={() => selectStock(item.symbol)}
              className={`group cursor-pointer px-3 py-2.5 transition-colors ${
                isSelected ? "bg-sky-500/12 border-l-2 border-l-sky-400" : "hover:bg-card/70"
              }`}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    <span className="text-sm font-bold tracking-tight text-white">{item.symbol}</span>
                    <span
                      className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${
                        item.bias === "bullish"
                          ? "bg-emerald-500/15 text-emerald-400"
                          : item.bias === "bearish"
                          ? "bg-rose-500/15 text-rose-400"
                          : "bg-slate-700/60 text-slate-300"
                      }`}
                    >
                      {item.bias}
                    </span>
                    {hasRisk && (
                      <span title={item.risk_flags.map((r) => r.label).join(", ")}>
                        <AlertTriangle className="h-3.5 w-3.5 text-amber-400" />
                      </span>
                    )}
                  </div>
                  <div className="truncate text-[11px] text-slate-400">{item.name}</div>
                </div>

                <div className="flex items-center gap-2 shrink-0">
                  <div className="text-right">
                    <div className="tabular-nums text-xs font-semibold text-slate-100">
                      ${item.last_price.toFixed(2)}
                    </div>
                    <div
                      className={`tabular-nums text-[11px] font-medium ${
                        item.change_pct >= 0 ? "text-emerald-400" : "text-rose-400"
                      }`}
                    >
                      {item.change_pct >= 0 ? "+" : ""}
                      {item.change_pct.toFixed(2)}%
                    </div>
                  </div>

                  <div
                    className={`flex h-8 w-9 items-center justify-center rounded-md border tabular-nums text-xs font-bold ${scoreColor(
                      item.score
                    )}`}
                  >
                    {Math.round(item.score)}
                  </div>

                  <WatchlistPopover symbol={item.symbol} watchlistIds={item.watchlist_ids} compact />
                </div>
              </div>

              {item.top_signals.length > 0 && (
                <div className="mt-1.5 flex flex-wrap gap-1">
                  {item.top_signals.slice(0, 2).map((sig) => (
                    <span
                      key={sig.code}
                      className={`truncate max-w-[145px] rounded px-1.5 py-0.5 text-[10px] ${
                        sig.direction === "bullish"
                          ? "bg-emerald-500/10 text-emerald-300"
                          : sig.direction === "bearish"
                          ? "bg-rose-500/10 text-rose-300"
                          : "bg-slate-800 text-slate-300"
                      }`}
                    >
                      {sig.label}
                    </span>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </aside>
  );
};
