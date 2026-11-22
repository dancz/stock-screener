import React, { useEffect, useState } from "react";
import { Bookmark, Check, Plus } from "lucide-react";
import { useScreenerStore } from "../store/useScreenerStore";

interface WatchlistPopoverProps {
  symbol: string;
  watchlistIds: number[];
  compact?: boolean;
  enableHotkey?: boolean;
}

export const WatchlistPopover: React.FC<WatchlistPopoverProps> = ({
  symbol,
  watchlistIds,
  compact = false,
  enableHotkey = false,
}) => {
  const [open, setOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    if (!enableHotkey) return;
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT")) {
        return;
      }
      if (e.key.toLowerCase() === "w" && !e.ctrlKey && !e.metaKey && !e.altKey) {
        e.preventDefault();
        setOpen((prev) => !prev);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [enableHotkey]);

  const watchlists = useScreenerStore((s) => s.watchlists);
  const toggleMembership = useScreenerStore((s) => s.toggleWatchlistMembership);
  const createWatchlist = useScreenerStore((s) => s.createWatchlist);

  const inAny = watchlistIds.length > 0;

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = newName.trim();
    if (!trimmed || creating) return;
    setCreating(true);
    try {
      const wl = await createWatchlist(trimmed);
      await toggleMembership(wl.id, symbol);
      setNewName("");
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="relative" onClick={(e) => e.stopPropagation()}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        title="Add / Remove from Watchlists"
        className={`inline-flex items-center gap-1.5 rounded-md border transition-colors ${
          compact ? "p-1.5" : "px-2.5 py-1.5 text-xs font-medium"
        } ${
          inAny
            ? "border-emerald-500/40 bg-emerald-500/15 text-emerald-300 hover:bg-emerald-500/25"
            : "border-border bg-card text-slate-300 hover:border-slate-500 hover:text-white"
        }`}
      >
        <Bookmark className={`h-3.5 w-3.5 ${inAny ? "fill-emerald-400 text-emerald-400" : ""}`} />
        {!compact && <span>{inAny ? `Listed (${watchlistIds.length})` : "Add to List"}</span>}
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="absolute right-0 z-50 mt-1.5 w-60 rounded-lg border border-border bg-[#121926] p-2.5 shadow-2xl">
            <div className="mb-2 flex items-center justify-between text-[11px] font-semibold uppercase tracking-wider text-slate-400">
              <span>Watchlists for {symbol}</span>
            </div>

            <div className="max-h-48 space-y-1 overflow-y-auto">
              {watchlists.length === 0 && (
                <div className="py-2 text-xs text-slate-500">No watchlists yet. Create one below.</div>
              )}
              {watchlists.map((wl) => {
                const checked = watchlistIds.includes(wl.id) || wl.symbols.includes(symbol);
                return (
                  <button
                    key={wl.id}
                    type="button"
                    onClick={() => toggleMembership(wl.id, symbol)}
                    className="flex w-full items-center justify-between rounded px-2 py-1.5 text-left text-xs text-slate-200 hover:bg-slate-800/80"
                  >
                    <span className="flex items-center gap-2 truncate">
                      <span
                        className="h-2 w-2 rounded-full"
                        style={{ backgroundColor: wl.color || "#10b981" }}
                      />
                      <span className="truncate">{wl.name}</span>
                    </span>
                    <span
                      className={`flex h-4 w-4 items-center justify-center rounded border ${
                        checked
                          ? "border-emerald-500 bg-emerald-500/20 text-emerald-400"
                          : "border-slate-600"
                      }`}
                    >
                      {checked && <Check className="h-3 w-3" />}
                    </span>
                  </button>
                );
              })}
            </div>

            <form onSubmit={handleCreate} className="mt-2 flex items-center gap-1 border-t border-border pt-2">
              <input
                type="text"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="New list name..."
                className="w-full rounded border border-border bg-canvas px-2 py-1 text-xs text-slate-100 placeholder-slate-500 focus:border-sky-500 focus:outline-none"
              />
              <button
                type="submit"
                disabled={!newName.trim() || creating}
                className="inline-flex items-center justify-center rounded bg-sky-600 p-1 text-white hover:bg-sky-500 disabled:opacity-40"
                title="Create & add ticker"
              >
                <Plus className="h-3.5 w-3.5" />
              </button>
            </form>
          </div>
        </>
      )}
    </div>
  );
};
