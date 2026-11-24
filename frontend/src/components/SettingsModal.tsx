import React, { useEffect, useState } from "react";
import { CheckCircle2, Database, Plug, RefreshCw, Save, X, XCircle } from "lucide-react";
import { api } from "../api/client";
import { useScreenerStore } from "../store/useScreenerStore";
import type { RuntimeSettings } from "../types/screener";

interface SettingsModalProps {
  open: boolean;
  onClose: () => void;
}

export const SettingsModal: React.FC<SettingsModalProps> = ({ open, onClose }) => {
  const settings = useScreenerStore((s) => s.settings);
  const plugins = useScreenerStore((s) => s.plugins);
  const backfillStatus = useScreenerStore((s) => s.backfillStatus);
  const updateSettings = useScreenerStore((s) => s.updateSettings);
  const triggerBackfill = useScreenerStore((s) => s.triggerBackfill);
  const fetchBackfillStatus = useScreenerStore((s) => s.fetchBackfillStatus);

  const [tab, setTab] = useState<"presets" | "ibkr" | "alerts" | "paper">("presets");
  const [draft, setDraft] = useState<RuntimeSettings | null>(null);
  const [saving, setSaving] = useState(false);
  const [ibkrTestRes, setIbkrTestRes] = useState<{ connected: boolean; message: string } | null>(null);
  const [notifTestRes, setNotifTestRes] = useState<string | null>(null);

  useEffect(() => {
    if (open && settings) {
      setDraft(JSON.parse(JSON.stringify(settings)));
      setIbkrTestRes(null);
      setNotifTestRes(null);
      fetchBackfillStatus();
    }
  }, [open, settings, fetchBackfillStatus]);

  if (!open || !draft) return null;

  const handleSave = async () => {
    setSaving(true);
    try {
      await updateSettings(draft);
      onClose();
    } finally {
      setSaving(false);
    }
  };

  const handleTestIbkr = async () => {
    setIbkrTestRes({ connected: false, message: "Testing connection..." });
    const res = await api.testIbkr(draft.ibkr.host, draft.ibkr.port, draft.ibkr.client_id);
    setIbkrTestRes(res);
  };

  const handleTestNotifier = async (pluginId: string) => {
    await updateSettings({ notifiers: draft.notifiers });
    const res = await api.testNotifier(pluginId);
    setNotifTestRes(res.message);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm">
      <div className="flex max-h-[90vh] w-full max-w-3xl flex-col overflow-hidden rounded-2xl border border-border bg-panel shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-border px-5 py-3.5">
          <h2 className="text-base font-bold text-white">Screener Settings & Plugins</h2>
          <button type="button" onClick={onClose} className="rounded p-1 text-slate-400 hover:text-white">
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Tabs */}
        <div className="flex gap-2 border-b border-border bg-canvas/50 px-5 py-2">
          {[
            { id: "presets", label: "Plugins & Presets" },
            { id: "ibkr", label: "Account, IBKR & Data" },
            { id: "alerts", label: "Schedule & Alerts" },
            { id: "paper", label: "Paper Trading" },
          ].map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => setTab(t.id as typeof tab)}
              className={`rounded-md px-3 py-1.5 text-xs font-semibold ${
                tab === t.id ? "bg-sky-600 text-white" : "text-slate-400 hover:bg-card hover:text-white"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-5 space-y-5">
          {tab === "presets" && (
            <>
              <div>
                <h3 className="mb-2 text-xs font-bold uppercase tracking-wider text-slate-400">
                  Registered Plugins (Auto-Discovered)
                </h3>
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                  {plugins.map((p) => {
                    const enabled = draft.enabled_plugins[p.plugin_id] ?? p.enabled;
                    return (
                      <label
                        key={p.plugin_id}
                        className="flex cursor-pointer items-start justify-between gap-3 rounded-lg border border-border bg-canvas/80 p-3"
                      >
                        <div>
                          <div className="flex items-center gap-2">
                            <span className="text-xs font-semibold text-white">{p.display_name}</span>
                            <span className="rounded bg-card px-1.5 py-0.5 text-[10px] uppercase text-slate-400">
                              {p.kind}
                            </span>
                          </div>
                          <p className="mt-1 text-[11px] text-slate-400">{p.description}</p>
                        </div>
                        <input
                          type="checkbox"
                          checked={enabled}
                          onChange={(e) =>
                            setDraft({
                              ...draft,
                              enabled_plugins: { ...draft.enabled_plugins, [p.plugin_id]: e.target.checked },
                            })
                          }
                          className="mt-1 h-4 w-4 accent-sky-500"
                        />
                      </label>
                    );
                  })}
                </div>
              </div>

              <div>
                <h3 className="mb-2 text-xs font-bold uppercase tracking-wider text-slate-400">
                  Scoring Preset Weights (All-Around Swing)
                </h3>
                <div className="space-y-2 rounded-lg border border-border bg-canvas/80 p-3">
                  {Object.entries(draft.presets.all_around_swing?.weights || {}).map(([pid, w]) => (
                    <div key={pid} className="flex items-center gap-3">
                      <span className="w-40 text-xs font-medium text-slate-200">{pid}</span>
                      <input
                        type="range"
                        min={0}
                        max={0.5}
                        step={0.02}
                        value={w}
                        onChange={(e) =>
                          setDraft({
                            ...draft,
                            presets: {
                              ...draft.presets,
                              all_around_swing: {
                                ...draft.presets.all_around_swing,
                                weights: {
                                  ...draft.presets.all_around_swing.weights,
                                  [pid]: Number(e.target.value),
                                },
                              },
                            },
                          })
                        }
                        className="flex-1 accent-sky-500"
                      />
                      <span className="w-12 text-right tabular-nums text-xs text-slate-300">
                        {Math.round(w * 100)}%
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            </>
          )}

          {tab === "ibkr" && (
            <div className="space-y-4">
              <div className="rounded-lg border border-border bg-canvas/80 p-4 space-y-3">
                <h3 className="text-xs font-bold uppercase tracking-wider text-slate-400">
                  Default Position Sizing Account
                </h3>
                <div className="grid grid-cols-2 gap-3">
                  <label className="block">
                    <span className="text-xs text-slate-300">Account Equity ($)</span>
                    <input
                      type="number"
                      value={draft.account.equity}
                      onChange={(e) =>
                        setDraft({ ...draft, account: { ...draft.account, equity: Number(e.target.value) || 0 } })
                      }
                      className="mt-1 w-full rounded border border-border bg-panel px-2.5 py-1.5 text-xs text-white"
                    />
                  </label>
                  <label className="block">
                    <span className="text-xs text-slate-300">Default Risk per Trade (%)</span>
                    <input
                      type="number"
                      step={0.25}
                      value={draft.account.risk_pct}
                      onChange={(e) =>
                        setDraft({ ...draft, account: { ...draft.account, risk_pct: Number(e.target.value) || 0 } })
                      }
                      className="mt-1 w-full rounded border border-border bg-panel px-2.5 py-1.5 text-xs text-white"
                    />
                  </label>
                </div>
              </div>

              <div className="rounded-lg border border-border bg-canvas/80 p-4 space-y-3">
                <div className="flex items-center justify-between">
                  <div>
                    <h3 className="text-xs font-bold uppercase tracking-wider text-slate-400">
                      IBKR Gateway (Read-Only Options & Quotes)
                    </h3>
                    <p className="text-[11px] text-slate-400">
                      Paper port: 4002 | Live port: 4001. Automatically falls back to Yahoo + Black-Scholes if offline.
                    </p>
                  </div>
                  <input
                    type="checkbox"
                    checked={draft.ibkr.enabled}
                    onChange={(e) => setDraft({ ...draft, ibkr: { ...draft.ibkr, enabled: e.target.checked } })}
                    className="h-4 w-4 accent-sky-500"
                  />
                </div>

                <div className="grid grid-cols-3 gap-3">
                  <label className="block">
                    <span className="text-xs text-slate-300">Host (local or LAN IP)</span>
                    <input
                      type="text"
                      value={draft.ibkr.host}
                      onChange={(e) => setDraft({ ...draft, ibkr: { ...draft.ibkr, host: e.target.value } })}
                      className="mt-1 w-full rounded border border-border bg-panel px-2.5 py-1.5 text-xs text-white"
                    />
                  </label>
                  <label className="block">
                    <span className="text-xs text-slate-300">Port</span>
                    <input
                      type="number"
                      value={draft.ibkr.port}
                      onChange={(e) =>
                        setDraft({ ...draft, ibkr: { ...draft.ibkr, port: Number(e.target.value) || 4002 } })
                      }
                      className="mt-1 w-full rounded border border-border bg-panel px-2.5 py-1.5 text-xs text-white"
                    />
                  </label>
                  <label className="block">
                    <span className="text-xs text-slate-300">Client ID</span>
                    <input
                      type="number"
                      value={draft.ibkr.client_id}
                      onChange={(e) =>
                        setDraft({ ...draft, ibkr: { ...draft.ibkr, client_id: Number(e.target.value) || 17 } })
                      }
                      className="mt-1 w-full rounded border border-border bg-panel px-2.5 py-1.5 text-xs text-white"
                    />
                  </label>
                </div>

                <div className="flex items-center gap-3 pt-1">
                  <button
                    type="button"
                    onClick={handleTestIbkr}
                    className="inline-flex items-center gap-1.5 rounded border border-border bg-card px-3 py-1.5 text-xs font-semibold text-white hover:border-sky-500"
                  >
                    <Plug className="h-3.5 w-3.5 text-sky-400" />
                    Test IBKR Connection
                  </button>
                  {ibkrTestRes && (
                    <span
                      className={`inline-flex items-center gap-1.5 text-xs ${
                        ibkrTestRes.connected ? "text-emerald-400" : "text-amber-300"
                      }`}
                    >
                      {ibkrTestRes.connected ? (
                        <CheckCircle2 className="h-4 w-4" />
                      ) : (
                        <XCircle className="h-4 w-4" />
                      )}
                      {ibkrTestRes.message}
                    </span>
                  )}
                </div>
              </div>

              <div className="rounded-lg border border-border bg-canvas/80 p-4 space-y-3">
                <div className="flex items-center justify-between gap-2">
                  <div>
                    <h3 className="flex items-center gap-1.5 text-xs font-bold uppercase tracking-wider text-slate-400">
                      <Database className="h-3.5 w-3.5 text-sky-400" />
                      Historical Depth &amp; Background Backfill
                    </h3>
                    <p className="text-[11px] text-slate-400">
                      Dev keeps data light (2Y 1D / 120D 1H). Production (Raspberry Pi) stores 5Y 1D, 10Y 1W, 1Y 1H and continuously hydrates tickers in batches.
                    </p>
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-2">
                  {(["dev", "production"] as const).map((prof) => (
                    <button
                      key={prof}
                      type="button"
                      onClick={() =>
                        setDraft({
                          ...draft,
                          history: {
                            ...draft.history,
                            profile: prof,
                            backfill_enabled: prof === "production",
                          },
                        })
                      }
                      className={`rounded-lg border px-3 py-2 text-left text-xs transition-colors ${
                        draft.history.profile === prof
                          ? "border-sky-500 bg-sky-500/15 text-white"
                          : "border-border bg-panel text-slate-400 hover:text-slate-200"
                      }`}
                    >
                      <div className="font-bold">
                        {prof === "dev" ? "Development Mode (Light)" : "Production / Raspberry Pi (Deep)"}
                      </div>
                      <div className="text-[10px] text-slate-400">
                        {prof === "dev"
                          ? "2Y Daily • 120D 1H • Manual backfill"
                          : "5Y Daily • 10Y Weekly • 1Y 1H • Auto backfill"}
                      </div>
                    </button>
                  ))}
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <label className="block">
                    <span className="text-xs text-slate-300">Backfill Batch Size (tickers/run)</span>
                    <input
                      type="number"
                      min={1}
                      max={30}
                      value={draft.history.backfill_batch_size}
                      onChange={(e) =>
                        setDraft({
                          ...draft,
                          history: { ...draft.history, backfill_batch_size: Number(e.target.value) || 5 },
                        })
                      }
                      className="mt-1 w-full rounded border border-border bg-panel px-2.5 py-1.5 text-xs text-white"
                    />
                  </label>
                  <label className="block">
                    <span className="text-xs text-slate-300">Backfill Interval (minutes)</span>
                    <input
                      type="number"
                      min={5}
                      max={180}
                      value={draft.history.backfill_interval_minutes}
                      onChange={(e) =>
                        setDraft({
                          ...draft,
                          history: { ...draft.history, backfill_interval_minutes: Number(e.target.value) || 15 },
                        })
                      }
                      className="mt-1 w-full rounded border border-border bg-panel px-2.5 py-1.5 text-xs text-white"
                    />
                  </label>
                </div>

                <div className="flex flex-wrap items-center justify-between gap-3 pt-1">
                  <label className="flex items-center gap-2 text-xs text-slate-300">
                    <input
                      type="checkbox"
                      checked={draft.history.prefer_ibkr_history}
                      onChange={(e) =>
                        setDraft({
                          ...draft,
                          history: { ...draft.history, prefer_ibkr_history: e.target.checked },
                        })
                      }
                      className="h-4 w-4 accent-sky-500"
                    />
                    <span>Prefer IBKR Gateway for extended history (Yahoo fallback)</span>
                  </label>

                  <button
                    type="button"
                    onClick={() => triggerBackfill()}
                    className="inline-flex items-center gap-1.5 rounded border border-border bg-card px-3 py-1.5 text-xs font-semibold text-white hover:border-sky-500"
                  >
                    <RefreshCw className={`h-3.5 w-3.5 text-sky-400 ${backfillStatus?.running ? "animate-spin" : ""}`} />
                    Run Backfill Batch Now
                  </button>
                </div>

                {backfillStatus && (
                  <div className="rounded bg-panel px-3 py-1.5 text-[11px] text-slate-300 flex items-center justify-between gap-2">
                    <span>
                      Hydrated: <strong>{backfillStatus.hydrated_tickers}</strong> / {backfillStatus.total_tickers} tickers
                    </span>
                    <span className="truncate text-slate-400">{backfillStatus.message}</span>
                  </div>
                )}
              </div>

              <div className="rounded-lg border border-border bg-canvas/80 p-4 space-y-3">
                <h3 className="text-xs font-bold uppercase tracking-wider text-slate-400">
                  Optional External Keys
                </h3>
                <label className="block">
                  <span className="text-xs text-slate-300">Finnhub Free API Key (News & Analyst Consensus)</span>
                  <input
                    type="password"
                    value={draft.data_sources.finnhub_api_key}
                    onChange={(e) =>
                      setDraft({
                        ...draft,
                        data_sources: { ...draft.data_sources, finnhub_api_key: e.target.value },
                      })
                    }
                    placeholder="Optional..."
                    className="mt-1 w-full rounded border border-border bg-panel px-2.5 py-1.5 text-xs text-white"
                  />
                </label>
              </div>
            </div>
          )}

          {tab === "alerts" && (
            <div className="space-y-4">
              <div className="rounded-lg border border-border bg-canvas/80 p-4 space-y-3">
                <div className="flex items-center justify-between">
                  <h3 className="text-xs font-bold uppercase tracking-wider text-slate-400">
                    Background Daily Scan Schedule (Mon–Fri)
                  </h3>
                  <input
                    type="checkbox"
                    checked={draft.schedule.enabled}
                    onChange={(e) =>
                      setDraft({ ...draft, schedule: { ...draft.schedule, enabled: e.target.checked } })
                    }
                    className="h-4 w-4 accent-sky-500"
                  />
                </div>
                <div className="grid grid-cols-3 gap-3">
                  <label className="block">
                    <span className="text-xs text-slate-300">Timezone</span>
                    <input
                      type="text"
                      value={draft.schedule.timezone}
                      onChange={(e) =>
                        setDraft({ ...draft, schedule: { ...draft.schedule, timezone: e.target.value } })
                      }
                      className="mt-1 w-full rounded border border-border bg-panel px-2.5 py-1.5 text-xs text-white"
                    />
                  </label>
                  <label className="block">
                    <span className="text-xs text-slate-300">Pre-Market Scan (HH:MM)</span>
                    <input
                      type="text"
                      value={draft.schedule.pre_market_time}
                      onChange={(e) =>
                        setDraft({ ...draft, schedule: { ...draft.schedule, pre_market_time: e.target.value } })
                      }
                      className="mt-1 w-full rounded border border-border bg-panel px-2.5 py-1.5 text-xs text-white"
                    />
                  </label>
                  <label className="block">
                    <span className="text-xs text-slate-300">Post-Market Scan (HH:MM)</span>
                    <input
                      type="text"
                      value={draft.schedule.post_market_time}
                      onChange={(e) =>
                        setDraft({ ...draft, schedule: { ...draft.schedule, post_market_time: e.target.value } })
                      }
                      className="mt-1 w-full rounded border border-border bg-panel px-2.5 py-1.5 text-xs text-white"
                    />
                  </label>
                </div>
              </div>

              <div className="rounded-lg border border-border bg-canvas/80 p-4 space-y-3">
                <h3 className="text-xs font-bold uppercase tracking-wider text-slate-400">
                  Optional Alert Webhooks (Telegram / Discord)
                </h3>
                <label className="block">
                  <span className="text-xs text-slate-300">Minimum Composite Score for Alert</span>
                  <input
                    type="number"
                    min={50}
                    max={98}
                    value={draft.notifiers.min_alert_score}
                    onChange={(e) =>
                      setDraft({
                        ...draft,
                        notifiers: { ...draft.notifiers, min_alert_score: Number(e.target.value) || 78 },
                      })
                    }
                    className="mt-1 w-32 rounded border border-border bg-panel px-2.5 py-1.5 text-xs text-white"
                  />
                </label>

                <div className="border-t border-border/60 pt-3 space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-semibold text-white">Telegram Bot</span>
                    <input
                      type="checkbox"
                      checked={draft.notifiers.telegram_enabled}
                      onChange={(e) =>
                        setDraft({
                          ...draft,
                          notifiers: { ...draft.notifiers, telegram_enabled: e.target.checked },
                        })
                      }
                      className="h-4 w-4 accent-sky-500"
                    />
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <input
                      type="password"
                      placeholder="Bot Token"
                      value={draft.notifiers.telegram_bot_token}
                      onChange={(e) =>
                        setDraft({
                          ...draft,
                          notifiers: { ...draft.notifiers, telegram_bot_token: e.target.value },
                        })
                      }
                      className="rounded border border-border bg-panel px-2.5 py-1.5 text-xs text-white"
                    />
                    <input
                      type="text"
                      placeholder="Chat ID"
                      value={draft.notifiers.telegram_chat_id}
                      onChange={(e) =>
                        setDraft({
                          ...draft,
                          notifiers: { ...draft.notifiers, telegram_chat_id: e.target.value },
                        })
                      }
                      className="rounded border border-border bg-panel px-2.5 py-1.5 text-xs text-white"
                    />
                  </div>
                  <button
                    type="button"
                    onClick={() => handleTestNotifier("telegram_notifier")}
                    className="rounded border border-border bg-card px-2.5 py-1 text-[11px] text-slate-200 hover:border-sky-500"
                  >
                    Test Telegram
                  </button>
                </div>

                <div className="border-t border-border/60 pt-3 space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-semibold text-white">Discord Webhook</span>
                    <input
                      type="checkbox"
                      checked={draft.notifiers.discord_enabled}
                      onChange={(e) =>
                        setDraft({
                          ...draft,
                          notifiers: { ...draft.notifiers, discord_enabled: e.target.checked },
                        })
                      }
                      className="h-4 w-4 accent-sky-500"
                    />
                  </div>
                  <input
                    type="password"
                    placeholder="https://discord.com/api/webhooks/..."
                    value={draft.notifiers.discord_webhook_url}
                    onChange={(e) =>
                      setDraft({
                        ...draft,
                        notifiers: { ...draft.notifiers, discord_webhook_url: e.target.value },
                      })
                    }
                    className="w-full rounded border border-border bg-panel px-2.5 py-1.5 text-xs text-white"
                  />
                  <button
                    type="button"
                    onClick={() => handleTestNotifier("discord_notifier")}
                    className="rounded border border-border bg-card px-2.5 py-1 text-[11px] text-slate-200 hover:border-sky-500"
                  >
                    Test Discord
                  </button>
                </div>

                {notifTestRes && <div className="text-xs text-sky-300">{notifTestRes}</div>}
              </div>
            </div>
          )}

          {tab === "paper" && (() => {
            const p = draft.paper_trading ?? {
              enabled: false,
              mode: "virtual" as const,
              auto_execute_after_scan: true,
              min_conviction: 68.0,
              min_rr: 1.8,
              allow_short: false,
              risk_per_trade_pct: 1.0,
              max_position_pct_equity: 15.0,
              max_open_positions: 6,
              max_positions_per_sector: 2,
              max_daily_new_orders: 5,
              min_dollar_volume: 20000000,
              max_spread_pct: 0.4,
              min_days_to_earnings: 7,
              move_to_be_at_r: 1.0,
              time_stop_days: 15,
              daily_loss_limit_pct: -2.0,
              max_drawdown_pct: -6.0,
            };
            const updatePaper = (patch: Partial<typeof p>) =>
              setDraft({ ...draft, paper_trading: { ...p, ...patch } });

            return (
              <div className="space-y-4">
                <div className="rounded-lg border border-border bg-canvas/80 p-4 space-y-3">
                  <div className="flex items-center justify-between">
                    <div>
                      <h3 className="text-xs font-bold uppercase tracking-wider text-slate-400">
                        Autonomous Paper Trading Mode
                      </h3>
                      <p className="text-[11px] text-slate-400">
                        Phase 1 runs the local SQLite virtual simulator (read-only toward IBKR). IBKR Paper order routing arrives in Phase 2.
                      </p>
                    </div>
                    <input
                      type="checkbox"
                      checked={p.enabled}
                      onChange={(e) => updatePaper({ enabled: e.target.checked })}
                      className="h-4 w-4 accent-sky-500"
                    />
                  </div>
                  <div className="grid grid-cols-3 gap-3">
                    <label className="block">
                      <span className="text-xs text-slate-300">Execution Mode</span>
                      <select
                        value={p.mode}
                        onChange={(e) => updatePaper({ mode: e.target.value as "virtual" | "ibkr_paper" })}
                        className="mt-1 w-full rounded border border-border bg-panel px-2.5 py-1.5 text-xs text-white"
                      >
                        <option value="virtual">Virtual Simulator (Active)</option>
                        <option value="ibkr_paper" disabled>
                          IBKR Paper DU* (Phase 2)
                        </option>
                      </select>
                    </label>
                    <label className="flex items-center gap-2 pt-5 text-xs text-slate-300">
                      <input
                        type="checkbox"
                        checked={p.auto_execute_after_scan}
                        onChange={(e) => updatePaper({ auto_execute_after_scan: e.target.checked })}
                        className="h-4 w-4 accent-sky-500"
                      />
                      Auto-allocate after scans
                    </label>
                    <label className="flex items-center gap-2 pt-5 text-xs text-slate-300">
                      <input
                        type="checkbox"
                        checked={p.allow_short}
                        onChange={(e) => updatePaper({ allow_short: e.target.checked })}
                        className="h-4 w-4 accent-sky-500"
                      />
                      Allow Short setups
                    </label>
                  </div>
                </div>

                <div className="rounded-lg border border-border bg-canvas/80 p-4 space-y-3">
                  <h3 className="text-xs font-bold uppercase tracking-wider text-slate-400">
                    Entry Filters &amp; Position Sizing
                  </h3>
                  <div className="grid grid-cols-3 gap-3">
                    <label className="block">
                      <span className="text-xs text-slate-300">Min Conviction (0-100)</span>
                      <input
                        type="number"
                        step={1}
                        value={p.min_conviction}
                        onChange={(e) => updatePaper({ min_conviction: Number(e.target.value) || 0 })}
                        className="mt-1 w-full rounded border border-border bg-panel px-2.5 py-1.5 text-xs text-white"
                      />
                    </label>
                    <label className="block">
                      <span className="text-xs text-slate-300">Min Reward:Risk</span>
                      <input
                        type="number"
                        step={0.1}
                        value={p.min_rr}
                        onChange={(e) => updatePaper({ min_rr: Number(e.target.value) || 1.0 })}
                        className="mt-1 w-full rounded border border-border bg-panel px-2.5 py-1.5 text-xs text-white"
                      />
                    </label>
                    <label className="block">
                      <span className="text-xs text-slate-300">Risk per Trade (%)</span>
                      <input
                        type="number"
                        step={0.25}
                        value={p.risk_per_trade_pct}
                        onChange={(e) => updatePaper({ risk_per_trade_pct: Number(e.target.value) || 1.0 })}
                        className="mt-1 w-full rounded border border-border bg-panel px-2.5 py-1.5 text-xs text-white"
                      />
                    </label>
                    <label className="block">
                      <span className="text-xs text-slate-300">Max Position (% Equity)</span>
                      <input
                        type="number"
                        step={1}
                        value={p.max_position_pct_equity}
                        onChange={(e) => updatePaper({ max_position_pct_equity: Number(e.target.value) || 15 })}
                        className="mt-1 w-full rounded border border-border bg-panel px-2.5 py-1.5 text-xs text-white"
                      />
                    </label>
                    <label className="block">
                      <span className="text-xs text-slate-300">Max Open Positions</span>
                      <input
                        type="number"
                        step={1}
                        value={p.max_open_positions}
                        onChange={(e) => updatePaper({ max_open_positions: Number(e.target.value) || 6 })}
                        className="mt-1 w-full rounded border border-border bg-panel px-2.5 py-1.5 text-xs text-white"
                      />
                    </label>
                    <label className="block">
                      <span className="text-xs text-slate-300">Max per Sector</span>
                      <input
                        type="number"
                        step={1}
                        value={p.max_positions_per_sector}
                        onChange={(e) => updatePaper({ max_positions_per_sector: Number(e.target.value) || 2 })}
                        className="mt-1 w-full rounded border border-border bg-panel px-2.5 py-1.5 text-xs text-white"
                      />
                    </label>
                  </div>
                </div>

                <div className="rounded-lg border border-border bg-canvas/80 p-4 space-y-3">
                  <h3 className="text-xs font-bold uppercase tracking-wider text-slate-400">
                    Lifecycle &amp; Kill-Switch Safeguards
                  </h3>
                  <div className="grid grid-cols-3 gap-3">
                    <label className="block">
                      <span className="text-xs text-slate-300">Min Days to Earnings</span>
                      <input
                        type="number"
                        step={1}
                        value={p.min_days_to_earnings}
                        onChange={(e) => updatePaper({ min_days_to_earnings: Number(e.target.value) || 7 })}
                        className="mt-1 w-full rounded border border-border bg-panel px-2.5 py-1.5 text-xs text-white"
                      />
                    </label>
                    <label className="block">
                      <span className="text-xs text-slate-300">Move SL to BE at R</span>
                      <input
                        type="number"
                        step={0.1}
                        value={p.move_to_be_at_r}
                        onChange={(e) => updatePaper({ move_to_be_at_r: Number(e.target.value) || 1.0 })}
                        className="mt-1 w-full rounded border border-border bg-panel px-2.5 py-1.5 text-xs text-white"
                      />
                    </label>
                    <label className="block">
                      <span className="text-xs text-slate-300">Time Stop (Business Days)</span>
                      <input
                        type="number"
                        step={1}
                        value={p.time_stop_days}
                        onChange={(e) => updatePaper({ time_stop_days: Number(e.target.value) || 15 })}
                        className="mt-1 w-full rounded border border-border bg-panel px-2.5 py-1.5 text-xs text-white"
                      />
                    </label>
                    <label className="block">
                      <span className="text-xs text-slate-300">Daily Loss Limit (%)</span>
                      <input
                        type="number"
                        step={0.5}
                        value={p.daily_loss_limit_pct}
                        onChange={(e) => updatePaper({ daily_loss_limit_pct: Number(e.target.value) })}
                        className="mt-1 w-full rounded border border-border bg-panel px-2.5 py-1.5 text-xs text-white"
                      />
                    </label>
                    <label className="block">
                      <span className="text-xs text-slate-300">Max Drawdown Kill-Switch (%)</span>
                      <input
                        type="number"
                        step={0.5}
                        value={p.max_drawdown_pct}
                        onChange={(e) => updatePaper({ max_drawdown_pct: Number(e.target.value) })}
                        className="mt-1 w-full rounded border border-border bg-panel px-2.5 py-1.5 text-xs text-white"
                      />
                    </label>
                    <label className="block">
                      <span className="text-xs text-slate-300">Max Daily New Orders</span>
                      <input
                        type="number"
                        step={1}
                        value={p.max_daily_new_orders}
                        onChange={(e) => updatePaper({ max_daily_new_orders: Number(e.target.value) || 5 })}
                        className="mt-1 w-full rounded border border-border bg-panel px-2.5 py-1.5 text-xs text-white"
                      />
                    </label>
                  </div>
                </div>
              </div>
            );
          })()}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 border-t border-border px-5 py-3">
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-border bg-card px-4 py-1.5 text-xs font-semibold text-slate-300 hover:text-white"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSave}
            disabled={saving}
            className="inline-flex items-center gap-1.5 rounded-md bg-sky-600 px-4 py-1.5 text-xs font-semibold text-white hover:bg-sky-500 disabled:opacity-50"
          >
            <Save className="h-3.5 w-3.5" />
            <span>{saving ? "Saving..." : "Save Settings"}</span>
          </button>
        </div>
      </div>
    </div>
  );
};
