import React, { useEffect, useState } from "react";
import { CheckCircle2, Plug, Save, X, XCircle } from "lucide-react";
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
  const updateSettings = useScreenerStore((s) => s.updateSettings);

  const [tab, setTab] = useState<"presets" | "ibkr" | "alerts">("presets");
  const [draft, setDraft] = useState<RuntimeSettings | null>(null);
  const [saving, setSaving] = useState(false);
  const [ibkrTestRes, setIbkrTestRes] = useState<{ connected: boolean; message: string } | null>(null);
  const [notifTestRes, setNotifTestRes] = useState<string | null>(null);

  useEffect(() => {
    if (open && settings) {
      setDraft(JSON.parse(JSON.stringify(settings)));
      setIbkrTestRes(null);
      setNotifTestRes(null);
    }
  }, [open, settings]);

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
