import { create } from "zustand";
import { api } from "../api/client";
import type {
  BackfillStatus,
  CandleBar,
  LiveQuote,
  MarketRegime,
  OverlayLayer,
  PluginDescriptor,
  RuntimeSettings,
  ScanStatus,
  ScreenerItem,
  SortBy,
  StockDetail,
  TradeIdea,
  TradeIdeaHorizon,
  TradeIdeasResponse,
  Watchlist,
  WatchlistHorizon,
} from "../types/screener";

export interface LayerVisibility {
  channels: boolean;
  sr_zones: boolean;
  smart_money: boolean;
  price_action: boolean;
  trade_plan: boolean;
  emas: boolean;
  volume_profile: boolean;
  volume: boolean;
}

interface ScreenerState {
  // Navigation & Filters
  activeView: "screener" | "trade_ideas";
  sidebarTab: "screener" | "watchlists";
  activePreset: string;
  sortBy: SortBy;
  presets: Record<string, { name: string; description: string; weights: Record<string, number> }>;
  activeWatchlistId: number | null;
  signalFilter: string | null;
  biasFilter: string | null;
  excludeEarnings7d: boolean;
  indexAlignedOnly: boolean;
  searchFilter: string;

  // Trade Ideas filters & data
  tradeIdeas: TradeIdeasResponse | null;
  loadingTradeIdeas: boolean;
  ideaHorizon: TradeIdeaHorizon;
  ideaMinRr: number;
  ideaExcludeEarnings7d: boolean;
  ideaIndexAlignedOnly: boolean;
  ideaWatchlistOnly: boolean;

  // Data
  items: ScreenerItem[];
  marketRegime: MarketRegime | null;
  lastScan: { id: number; finished_at: string | null; status: string } | null;
  watchlists: Watchlist[];
  selectedSymbol: string | null;
  selectedDetail: StockDetail | null;
  liveQuote: LiveQuote | null;
  timeframe: "1w" | "1d" | "1h";
  candles: CandleBar[];
  layers: LayerVisibility;

  // Scan, Backfill & Settings
  scanStatus: ScanStatus | null;
  backfillStatus: BackfillStatus | null;
  settings: RuntimeSettings | null;
  plugins: PluginDescriptor[];
  ibkrStatus: { connected: boolean; message: string } | null;

  // Loading & Errors
  loadingList: boolean;
  loadingDetail: boolean;
  loadingCandles: boolean;
  error: string | null;

  // Actions
  init: () => Promise<void>;
  setActiveView: (view: "screener" | "trade_ideas") => void;
  fetchTradeIdeas: () => Promise<void>;
  setIdeaFilters: (
    partial: Partial<{
      horizon: TradeIdeaHorizon;
      minRr: number;
      excludeEarnings7d: boolean;
      indexAlignedOnly: boolean;
      watchlistOnly: boolean;
    }>
  ) => Promise<void>;
  openIdeaInScreener: (symbol: string) => Promise<void>;
  fetchScreener: () => Promise<void>;
  fetchWatchlists: () => Promise<void>;
  selectStock: (symbol: string) => Promise<void>;
  refreshStock: (symbol: string) => Promise<void>;
  jumpToTicker: (symbol: string) => Promise<void>;
  setTimeframe: (tf: "1w" | "1d" | "1h") => Promise<void>;
  toggleLayer: (layer: OverlayLayer) => void;
  setSidebarTab: (tab: "screener" | "watchlists") => void;
  setActivePreset: (preset: string) => Promise<void>;
  setSortBy: (sortBy: SortBy) => Promise<void>;
  setActiveWatchlistId: (id: number | null) => Promise<void>;
  setSignalFilter: (sig: string | null) => Promise<void>;
  setBiasFilter: (bias: string | null) => Promise<void>;
  setExcludeEarnings7d: (exclude: boolean) => Promise<void>;
  setIndexAlignedOnly: (aligned: boolean) => Promise<void>;
  setSearchFilter: (q: string) => Promise<void>;
  toggleWatchlistMembership: (watchlistId: number, symbol: string) => Promise<void>;
  createWatchlist: (name: string, horizon?: WatchlistHorizon) => Promise<Watchlist>;
  deleteWatchlist: (id: number) => Promise<void>;
  renameWatchlist: (id: number, name: string) => Promise<void>;
  saveNotes: (symbol: string, noteText: string, tags: string[]) => Promise<void>;
  triggerScan: (mode: "quick" | "full" | "watchlists") => Promise<void>;
  pollScanStatus: () => Promise<void>;
  fetchBackfillStatus: () => Promise<void>;
  triggerBackfill: () => Promise<void>;
  loadSettings: () => Promise<void>;
  updateSettings: (payload: Partial<RuntimeSettings>) => Promise<void>;
  checkIbkrStatus: () => Promise<void>;
}

export const useScreenerStore = create<ScreenerState>((set, get) => ({
  activeView: "screener",
  sidebarTab: "screener",
  activePreset: "all_around_swing",
  sortBy: "score",
  presets: {},
  activeWatchlistId: null,
  signalFilter: null,
  biasFilter: null,
  excludeEarnings7d: false,
  indexAlignedOnly: false,
  searchFilter: "",

  tradeIdeas: null,
  loadingTradeIdeas: false,
  ideaHorizon: "all",
  ideaMinRr: 1.5,
  ideaExcludeEarnings7d: false,
  ideaIndexAlignedOnly: false,
  ideaWatchlistOnly: false,

  items: [],
  marketRegime: null,
  lastScan: null,
  watchlists: [],
  selectedSymbol: null,
  selectedDetail: null,
  liveQuote: null,
  timeframe: "1d",
  candles: [],
  layers: {
    channels: true,
    sr_zones: true,
    smart_money: true,
    price_action: true,
    trade_plan: true,
    emas: true,
    volume_profile: true,
    volume: true,
  },

  scanStatus: null,
  backfillStatus: null,
  settings: null,
  plugins: [],
  ibkrStatus: null,

  loadingList: false,
  loadingDetail: false,
  loadingCandles: false,
  error: null,

  async init() {
    await Promise.all([
      get().loadSettings(),
      get().fetchWatchlists(),
      get().fetchScreener(),
      get().fetchTradeIdeas(),
      get().pollScanStatus(),
      get().fetchBackfillStatus(),
    ]);
    get().checkIbkrStatus();
  },

  setActiveView(view) {
    set({ activeView: view });
    if (view === "trade_ideas") {
      get().fetchTradeIdeas();
    }
  },

  async fetchTradeIdeas() {
    const state = get();
    set({ loadingTradeIdeas: true });
    try {
      const res = await api.getTradeIdeas({
        limit: 10,
        horizon: state.ideaHorizon,
        minRr: state.ideaMinRr,
        excludeEarningsDays: state.ideaExcludeEarnings7d ? 7 : null,
        indexAlignedOnly: state.ideaIndexAlignedOnly,
        watchlistOnly: state.ideaWatchlistOnly,
      });
      set({ tradeIdeas: res, loadingTradeIdeas: false });
    } catch (err) {
      set({ loadingTradeIdeas: false, error: (err as Error).message });
    }
  },

  async setIdeaFilters(partial) {
    set({
      ...(partial.horizon !== undefined ? { ideaHorizon: partial.horizon } : {}),
      ...(partial.minRr !== undefined ? { ideaMinRr: partial.minRr } : {}),
      ...(partial.excludeEarnings7d !== undefined ? { ideaExcludeEarnings7d: partial.excludeEarnings7d } : {}),
      ...(partial.indexAlignedOnly !== undefined ? { ideaIndexAlignedOnly: partial.indexAlignedOnly } : {}),
      ...(partial.watchlistOnly !== undefined ? { ideaWatchlistOnly: partial.watchlistOnly } : {}),
    });
    await get().fetchTradeIdeas();
  },

  async openIdeaInScreener(symbol) {
    set({ activeView: "screener" });
    await get().selectStock(symbol);
  },

  async fetchScreener() {
    const state = get();
    set({ loadingList: true, error: null });
    try {
      const res = await api.getScreener({
        preset: state.activePreset,
        sortBy: state.sortBy,
        watchlistId: state.sidebarTab === "watchlists" ? state.activeWatchlistId : null,
        signal: state.signalFilter,
        bias: state.biasFilter,
        excludeEarningsDays: state.excludeEarnings7d ? 7 : null,
        indexAlignedOnly: state.indexAlignedOnly,
        search: state.searchFilter,
      });
      set({
        items: res.items,
        presets: res.presets,
        marketRegime: res.market_regime,
        lastScan: res.last_scan,
        loadingList: false,
      });
      const currentSym = get().selectedSymbol;
      if (res.items.length > 0 && (!currentSym || !res.items.some((i) => i.symbol === currentSym))) {
        await get().selectStock(res.items[0].symbol);
      }
    } catch (err) {
      set({ loadingList: false, error: (err as Error).message });
    }
  },

  async fetchWatchlists() {
    try {
      const res = await api.getWatchlists();
      set({ watchlists: res.watchlists });
      if (get().activeWatchlistId === null && res.watchlists.length > 0) {
        set({ activeWatchlistId: res.watchlists[0].id });
      }
    } catch {
      // ignore
    }
  },

  async selectStock(symbol) {
    const sym = symbol.toUpperCase();
    set({ selectedSymbol: sym, loadingDetail: true, loadingCandles: true, liveQuote: null, error: null });
    try {
      const tf = get().timeframe;
      const [detail, candlesRes, quote] = await Promise.all([
        api.getStockDetail(sym),
        api.getCandles(sym, tf),
        api.getLiveQuote(sym).catch(() => null),
      ]);
      // Ignore stale resolution if user clicked another stock quickly
      if (get().selectedSymbol !== sym) return;
      set((s) => ({
        selectedDetail: detail,
        candles: candlesRes.candles,
        liveQuote: quote,
        loadingDetail: false,
        loadingCandles: false,
        items: quote
          ? s.items.map((it) =>
              it.symbol === sym ? { ...it, last_price: quote.price, change_pct: quote.change_pct } : it
            )
          : s.items,
      }));
    } catch (err) {
      set({ loadingDetail: false, loadingCandles: false, error: (err as Error).message });
    }
  },

  async refreshStock(symbol) {
    const sym = symbol.toUpperCase();
    set({ loadingDetail: true, error: null });
    try {
      const [detail, candlesRes, quote] = await Promise.all([
        api.refreshStock(sym),
        api.getCandles(sym, get().timeframe),
        api.getLiveQuote(sym).catch(() => null),
      ]);
      set({ selectedDetail: detail, candles: candlesRes.candles, liveQuote: quote, loadingDetail: false });
      await get().fetchScreener();
    } catch (err) {
      set({ loadingDetail: false, error: (err as Error).message });
    }
  },

  async jumpToTicker(symbol) {
    const sym = symbol.trim().toUpperCase();
    if (!sym) return;
    set({
      activeView: "screener",
      selectedSymbol: sym,
      loadingDetail: true,
      loadingCandles: true,
      liveQuote: null,
      error: null,
    });
    try {
      const [detail, candlesRes, quote] = await Promise.all([
        api.refreshStock(sym),
        api.getCandles(sym, get().timeframe),
        api.getLiveQuote(sym).catch(() => null),
      ]);
      set({
        selectedDetail: detail,
        candles: candlesRes.candles,
        liveQuote: quote,
        loadingDetail: false,
        loadingCandles: false,
      });
      await get().fetchScreener();
    } catch (err) {
      set({ loadingDetail: false, loadingCandles: false, error: (err as Error).message });
    }
  },

  async setTimeframe(tf) {
    set({ timeframe: tf, loadingCandles: true });
    const sym = get().selectedSymbol;
    if (!sym) {
      set({ loadingCandles: false });
      return;
    }
    try {
      const res = await api.getCandles(sym, tf);
      set({ candles: res.candles, loadingCandles: false });
    } catch {
      set({ loadingCandles: false });
    }
  },

  toggleLayer(layer) {
    set((s) => ({ layers: { ...s.layers, [layer]: !s.layers[layer] } }));
  },

  setSidebarTab(tab) {
    set({ sidebarTab: tab });
    get().fetchScreener();
  },

  async setActivePreset(preset) {
    set({ activePreset: preset });
    await get().fetchScreener();
  },

  async setSortBy(sortBy) {
    set({ sortBy });
    await get().fetchScreener();
  },

  async setActiveWatchlistId(id) {
    const wl = get().watchlists.find((w) => w.id === id);
    if (wl?.horizon === "short_term") {
      set({ activeWatchlistId: id, activePreset: "short_term_high_rr", sortBy: "risk_reward" });
    } else if (wl?.horizon === "medium_term") {
      set({ activeWatchlistId: id, activePreset: "medium_term_invest", sortBy: "score" });
    } else {
      set({ activeWatchlistId: id });
    }
    await get().fetchScreener();
  },

  async setSignalFilter(sig) {
    set({ signalFilter: sig });
    await get().fetchScreener();
  },

  async setBiasFilter(bias) {
    set({ biasFilter: bias });
    await get().fetchScreener();
  },

  async setExcludeEarnings7d(exclude) {
    set({ excludeEarnings7d: exclude });
    await get().fetchScreener();
  },

  async setIndexAlignedOnly(aligned) {
    set({ indexAlignedOnly: aligned });
    await get().fetchScreener();
  },

  async setSearchFilter(q) {
    set({ searchFilter: q });
    await get().fetchScreener();
  },

  async toggleWatchlistMembership(watchlistId, symbol) {
    const sym = symbol.toUpperCase();
    const wl = get().watchlists.find((w) => w.id === watchlistId);
    const isMember = wl?.symbols.includes(sym) ?? false;
    if (isMember) {
      await api.removeFromWatchlist(watchlistId, sym);
    } else {
      await api.addToWatchlist(watchlistId, sym);
    }
    await get().fetchWatchlists();
    const detail = get().selectedDetail;
    if (detail && detail.symbol === sym) {
      const nextIds = isMember
        ? detail.watchlist_ids.filter((id) => id !== watchlistId)
        : [...detail.watchlist_ids, watchlistId];
      set({ selectedDetail: { ...detail, watchlist_ids: nextIds } });
    }
    set((s) => {
      const updateIds = (ids: number[]) =>
        isMember ? ids.filter((id) => id !== watchlistId) : [...ids, watchlistId];
      const patchIdea = (idea: TradeIdea): TradeIdea =>
        idea.symbol === sym ? { ...idea, watchlist_ids: updateIds(idea.watchlist_ids) } : idea;

      return {
        items: s.items.map((it) =>
          it.symbol === sym ? { ...it, watchlist_ids: updateIds(it.watchlist_ids) } : it
        ),
        tradeIdeas: s.tradeIdeas
          ? {
              ...s.tradeIdeas,
              buys: s.tradeIdeas.buys.map(patchIdea),
              sells: s.tradeIdeas.sells.map(patchIdea),
            }
          : null,
      };
    });
    if (get().sidebarTab === "watchlists") {
      await get().fetchScreener();
    }
  },

  async createWatchlist(name, horizon = "general") {
    const colorMap: Record<WatchlistHorizon, string> = {
      short_term: "#f59e0b",
      medium_term: "#38bdf8",
      general: "#10b981",
    };
    const wl = await api.createWatchlist(name, colorMap[horizon], horizon);
    await get().fetchWatchlists();
    set({ activeWatchlistId: wl.id });
    if (get().sidebarTab === "watchlists") {
      await get().fetchScreener();
    }
    return wl;
  },

  async deleteWatchlist(id) {
    await api.deleteWatchlist(id);
    await get().fetchWatchlists();
    const lists = get().watchlists;
    set({ activeWatchlistId: lists.length > 0 ? lists[0].id : null });
    if (get().sidebarTab === "watchlists") {
      await get().fetchScreener();
    }
  },

  async renameWatchlist(id, name) {
    await api.renameWatchlist(id, name);
    await get().fetchWatchlists();
  },

  async saveNotes(symbol, noteText, tags) {
    const res = await api.saveNotes(symbol, noteText, tags);
    const detail = get().selectedDetail;
    if (detail && detail.symbol === symbol) {
      set({ selectedDetail: { ...detail, note_text: res.note_text, tags: res.tags } });
    }
  },

  async triggerScan(mode) {
    const res = await api.triggerScan(mode);
    set({ scanStatus: res.status });
  },

  async pollScanStatus() {
    try {
      const prevRunning = get().scanStatus?.running ?? false;
      const status = await api.getScanStatus();
      set({ scanStatus: status });
      if (prevRunning && !status.running && status.stage === "completed") {
        await Promise.all([get().fetchScreener(), get().fetchTradeIdeas()]);
        const sel = get().selectedSymbol;
        if (sel) await get().selectStock(sel);
      }
    } catch {
      // ignore
    }
  },

  async fetchBackfillStatus() {
    try {
      const status = await api.getBackfillStatus();
      set({ backfillStatus: status });
    } catch {
      // ignore
    }
  },

  async triggerBackfill() {
    const res = await api.triggerBackfill();
    set({ backfillStatus: res.status });
  },

  async loadSettings() {
    try {
      const res = await api.getSettings();
      set({ settings: res.settings, plugins: res.plugins });
    } catch {
      // ignore
    }
  },

  async updateSettings(payload) {
    const res = await api.updateSettings(payload);
    set({ settings: res.settings, plugins: res.plugins });
    await Promise.all([get().fetchScreener(), get().fetchBackfillStatus()]);
    get().checkIbkrStatus();
  },

  async checkIbkrStatus() {
    const s = get().settings;
    if (!s || !s.ibkr.enabled) {
      set({ ibkrStatus: { connected: false, message: "IBKR disabled (Yahoo + BS)" } });
      return;
    }
    try {
      const res = await api.testIbkr(s.ibkr.host, s.ibkr.port, s.ibkr.client_id);
      set({ ibkrStatus: res });
    } catch {
      set({ ibkrStatus: { connected: false, message: "Yahoo Fallback" } });
    }
  },
}));
