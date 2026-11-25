import { useEffect, useState } from "react";
import { Play, Sparkles } from "lucide-react";
import { CorrelationCard } from "./components/CorrelationCard";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { EventsInsidersCard } from "./components/EventsInsidersCard";
import { FundamentalsCard } from "./components/FundamentalsCard";
import { LeftSidebar } from "./components/LeftSidebar";
import { NotesTagsCard } from "./components/NotesTagsCard";
import { OptionsCard } from "./components/OptionsCard";
import { PaperTradingView } from "./components/PaperTradingView";
import { ScoreBreakdownCard } from "./components/ScoreBreakdownCard";
import { SettingsModal } from "./components/SettingsModal";
import { StockChart } from "./components/StockChart";
import { TickerHeader } from "./components/TickerHeader";
import { TopBar } from "./components/TopBar";
import { TradeIdeasView } from "./components/TradeIdeasView";
import { TradePlanCard } from "./components/TradePlanCard";
import { useScreenerStore } from "./store/useScreenerStore";

export function App() {
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsTab, setSettingsTab] = useState<"presets" | "ibkr" | "alerts" | "paper">("presets");

  const openSettings = (tab: "presets" | "ibkr" | "alerts" | "paper" = "presets") => {
    setSettingsTab(tab);
    setSettingsOpen(true);
  };

  const init = useScreenerStore((s) => s.init);
  const activeView = useScreenerStore((s) => s.activeView);
  const pollScanStatus = useScreenerStore((s) => s.pollScanStatus);
  const scanRunning = useScreenerStore((s) => s.scanStatus?.running ?? false);
  const selectedSymbol = useScreenerStore((s) => s.selectedSymbol);
  const selectedDetail = useScreenerStore((s) => s.selectedDetail);
  const items = useScreenerStore((s) => s.items);
  const loadingDetail = useScreenerStore((s) => s.loadingDetail);
  const error = useScreenerStore((s) => s.error);
  const triggerScan = useScreenerStore((s) => s.triggerScan);

  useEffect(() => {
    init();
  }, [init]);

  useEffect(() => {
    if (!scanRunning) return;
    const timer = window.setInterval(() => {
      pollScanStatus();
    }, 1500);
    return () => window.clearInterval(timer);
  }, [scanRunning, pollScanStatus]);

  return (
    <div className="flex h-screen w-screen flex-col bg-canvas text-slate-100">
      <TopBar onOpenSettings={() => openSettings("presets")} />

      {activeView === "trade_ideas" ? (
        <div className="flex min-h-0 flex-1 overflow-hidden">
          <main className="flex-1 overflow-y-auto p-4">
            {error && (
              <div className="mb-3 rounded-lg border border-rose-500/40 bg-rose-500/10 px-4 py-2 text-xs text-rose-300">
                {error}
              </div>
            )}
            <ErrorBoundary resetKey="trade_ideas">
              <TradeIdeasView />
            </ErrorBoundary>
          </main>
        </div>
      ) : activeView === "paper_trading" ? (
        <div className="flex min-h-0 flex-1 overflow-hidden">
          <main className="flex-1 overflow-y-auto p-4">
            {error && (
              <div className="mb-3 rounded-lg border border-rose-500/40 bg-rose-500/10 px-4 py-2 text-xs text-rose-300">
                {error}
              </div>
            )}
            <ErrorBoundary resetKey="paper_trading">
              <PaperTradingView onOpenTelegramSetup={() => openSettings("alerts")} />
            </ErrorBoundary>
          </main>
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 overflow-hidden">
          <LeftSidebar />

          <main className="flex-1 overflow-y-auto p-4">
            {error && (
              <div className="mb-3 rounded-lg border border-rose-500/40 bg-rose-500/10 px-4 py-2 text-xs text-rose-300">
                {error}
              </div>
            )}

            {!selectedDetail && items.length === 0 && !loadingDetail ? (
              <div className="flex h-full flex-col items-center justify-center rounded-2xl border border-dashed border-border bg-panel/40 p-10 text-center">
                <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-xl bg-sky-500/15 text-sky-400">
                  <Sparkles className="h-6 w-6" />
                </div>
                <h2 className="text-lg font-bold text-white">Welcome to SwingPulse Screener</h2>
                <p className="mt-1.5 max-w-md text-xs leading-relaxed text-slate-400">
                  No daily scan snapshots are stored yet. Run a Quick Scan (~45-70 liquid leaders &amp; your
                  watchlists) or a Full S&amp;P 500 + Nasdaq 100 Universe Scan to rank today&apos;s best setups.
                </p>
                <div className="mt-5 flex flex-wrap justify-center gap-3">
                  <button
                    type="button"
                    onClick={() => triggerScan("quick")}
                    className="inline-flex items-center gap-2 rounded-lg bg-sky-600 px-4 py-2 text-xs font-semibold text-white hover:bg-sky-500"
                  >
                    <Play className="h-3.5 w-3.5 fill-current" />
                    Run Quick Scan (~25s)
                  </button>
                  <button
                    type="button"
                    onClick={() => triggerScan("full")}
                    className="inline-flex items-center gap-2 rounded-lg border border-border bg-card px-4 py-2 text-xs font-semibold text-slate-200 hover:border-slate-500 hover:text-white"
                  >
                    Run Full Universe Scan (~600 tickers)
                  </button>
                </div>
              </div>
            ) : (
              <ErrorBoundary resetKey={selectedSymbol ?? ""}>
                <div className="mx-auto max-w-[1440px] space-y-4 pb-8">
                  <TickerHeader />
                  <StockChart />

                  <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
                    <TradePlanCard />
                    <ScoreBreakdownCard />
                    <OptionsCard />
                    <CorrelationCard />
                    <FundamentalsCard />
                    <EventsInsidersCard />
                  </div>

                  <NotesTagsCard />
                </div>
              </ErrorBoundary>
            )}
          </main>
        </div>
      )}

      <SettingsModal open={settingsOpen} initialTab={settingsTab} onClose={() => setSettingsOpen(false)} />
    </div>
  );
}

export default App;
