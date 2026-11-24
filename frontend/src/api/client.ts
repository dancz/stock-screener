import type {
  BackfillStatus,
  CandleBar,
  LiveQuote,
  PluginDescriptor,
  RuntimeSettings,
  ScanStatus,
  ScreenerResponse,
  SortBy,
  StockDetail,
  TradeIdeaHorizon,
  TradeIdeasResponse,
  Watchlist,
  WatchlistHorizon,
} from "../types/screener";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!resp.ok) {
    let msg = `HTTP ${resp.status}`;
    try {
      const data = await resp.json();
      msg = data.detail || data.message || msg;
    } catch {
      // ignore json parse failure
    }
    throw new Error(msg);
  }
  return resp.json() as Promise<T>;
}

export const api = {
  getScreener(params: {
    preset?: string;
    sortBy?: SortBy;
    watchlistId?: number | null;
    signal?: string | null;
    bias?: string | null;
    excludeEarningsDays?: number | null;
    indexAlignedOnly?: boolean;
    search?: string;
  }): Promise<ScreenerResponse> {
    const qs = new URLSearchParams();
    if (params.preset) qs.set("preset", params.preset);
    if (params.sortBy) qs.set("sort_by", params.sortBy);
    if (params.watchlistId !== undefined && params.watchlistId !== null) {
      qs.set("watchlist_id", String(params.watchlistId));
    }
    if (params.signal) qs.set("signal", params.signal);
    if (params.bias) qs.set("bias", params.bias);
    if (params.excludeEarningsDays !== undefined && params.excludeEarningsDays !== null) {
      qs.set("exclude_earnings_days", String(params.excludeEarningsDays));
    }
    if (params.indexAlignedOnly) qs.set("index_aligned_only", "true");
    if (params.search) qs.set("search", params.search);
    return request<ScreenerResponse>(`/api/screener?${qs.toString()}`);
  },

  getTradeIdeas(params?: {
    limit?: number;
    horizon?: TradeIdeaHorizon;
    minRr?: number;
    excludeEarningsDays?: number | null;
    indexAlignedOnly?: boolean;
    watchlistOnly?: boolean;
    hideConflicted?: boolean;
    hideStale?: boolean;
  }): Promise<TradeIdeasResponse> {
    const qs = new URLSearchParams();
    if (params?.limit) qs.set("limit", String(params.limit));
    if (params?.horizon) qs.set("horizon", params.horizon);
    if (params?.minRr !== undefined) qs.set("min_rr", String(params.minRr));
    if (params?.excludeEarningsDays !== undefined && params?.excludeEarningsDays !== null) {
      qs.set("exclude_earnings_days", String(params.excludeEarningsDays));
    }
    if (params?.indexAlignedOnly) qs.set("index_aligned_only", "true");
    if (params?.watchlistOnly) qs.set("watchlist_only", "true");
    if (params?.hideConflicted !== undefined) {
      qs.set("hide_conflicted", params.hideConflicted ? "true" : "false");
    }
    if (params?.hideStale !== undefined) {
      qs.set("hide_stale", params.hideStale ? "true" : "false");
    }
    const suffix = qs.toString();
    return request<TradeIdeasResponse>(suffix ? `/api/trade-ideas?${suffix}` : "/api/trade-ideas");
  },

  getStockDetail(symbol: string): Promise<StockDetail> {
    return request<StockDetail>(`/api/stocks/${encodeURIComponent(symbol)}`);
  },

  getLiveQuote(symbol: string): Promise<LiveQuote> {
    return request<LiveQuote>(`/api/stocks/${encodeURIComponent(symbol)}/quote`);
  },

  getCandles(
    symbol: string,
    timeframe: "1w" | "1d" | "1h"
  ): Promise<{ symbol: string; timeframe: string; bar_count: number; candles: CandleBar[] }> {
    return request(`/api/stocks/${encodeURIComponent(symbol)}/candles?timeframe=${timeframe}`);
  },

  refreshStock(symbol: string): Promise<StockDetail> {
    return request<StockDetail>(`/api/stocks/${encodeURIComponent(symbol)}/refresh`, { method: "POST" });
  },

  saveNotes(symbol: string, noteText: string, tags: string[]): Promise<{ symbol: string; note_text: string; tags: string[] }> {
    return request(`/api/stocks/${encodeURIComponent(symbol)}/notes`, {
      method: "PUT",
      body: JSON.stringify({ note_text: noteText, tags }),
    });
  },

  getWatchlists(): Promise<{ watchlists: Watchlist[] }> {
    return request("/api/watchlists");
  },

  createWatchlist(name: string, color = "#10b981", horizon: WatchlistHorizon = "general"): Promise<Watchlist> {
    return request("/api/watchlists", {
      method: "POST",
      body: JSON.stringify({ name, color, horizon }),
    });
  },

  renameWatchlist(id: number, name: string): Promise<Watchlist> {
    return request(`/api/watchlists/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ name }),
    });
  },

  deleteWatchlist(id: number): Promise<{ deleted: boolean }> {
    return request(`/api/watchlists/${id}`, { method: "DELETE" });
  },

  addToWatchlist(watchlistId: number, symbol: string): Promise<{ id: number; symbols: string[]; count: number }> {
    return request(`/api/watchlists/${watchlistId}/items/${encodeURIComponent(symbol)}`, { method: "POST" });
  },

  removeFromWatchlist(watchlistId: number, symbol: string): Promise<{ id: number; symbols: string[]; count: number }> {
    return request(`/api/watchlists/${watchlistId}/items/${encodeURIComponent(symbol)}`, { method: "DELETE" });
  },

  triggerScan(mode: "quick" | "full" | "watchlists"): Promise<{ started: boolean; status: ScanStatus }> {
    return request("/api/scan/run", {
      method: "POST",
      body: JSON.stringify({ mode }),
    });
  },

  getScanStatus(): Promise<ScanStatus> {
    return request("/api/scan/status");
  },

  getBackfillStatus(): Promise<BackfillStatus> {
    return request("/api/scan/backfill/status");
  },

  triggerBackfill(): Promise<{ started: boolean; status: BackfillStatus }> {
    return request("/api/scan/backfill", { method: "POST" });
  },

  getSettings(): Promise<{ settings: RuntimeSettings; plugins: PluginDescriptor[] }> {
    return request("/api/settings");
  },

  updateSettings(payload: Partial<RuntimeSettings>): Promise<{ settings: RuntimeSettings; plugins: PluginDescriptor[] }> {
    return request("/api/settings", {
      method: "PUT",
      body: JSON.stringify(payload),
    });
  },

  testIbkr(host: string, port: number, clientId: number): Promise<{ connected: boolean; message: string }> {
    return request("/api/settings/test-ibkr", {
      method: "POST",
      body: JSON.stringify({ host, port, client_id: clientId }),
    });
  },

  testNotifier(pluginId: string): Promise<{ ok: boolean; message: string }> {
    return request("/api/settings/test-notifier", {
      method: "POST",
      body: JSON.stringify({ plugin_id: pluginId }),
    });
  },
};
