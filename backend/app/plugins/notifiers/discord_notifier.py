from __future__ import annotations

from typing import Any

import httpx

from app.core.contracts import MarketRegime, NotifierPlugin


class DiscordNotifierPlugin(NotifierPlugin):
    plugin_id = "discord_notifier"
    display_name = "Discord Webhook Alerts"
    description = "Posts daily swing screener top setups & channel touch alerts to a Discord webhook (optional)."

    def is_configured(self, settings: dict[str, Any]) -> bool:
        cfg = settings.get("notifiers", {})
        return bool(cfg.get("discord_enabled") and str(cfg.get("discord_webhook_url", "")).strip())

    def send_test(self, settings: dict[str, Any]) -> tuple[bool, str]:
        url = str(settings.get("notifiers", {}).get("discord_webhook_url", "")).strip()
        if not url:
            return False, "Discord webhook URL is required."
        return self._post(url, "✅ **Stock Screener Test Alert**: Discord webhook connected successfully.")

    def notify_scan_summary(
        self,
        settings: dict[str, Any],
        top_alerts: list[dict[str, Any]],
        regime: MarketRegime | None,
    ) -> tuple[bool, str]:
        if not self.is_configured(settings) or not top_alerts:
            return False, "Not configured or no alerts."
        url = str(settings.get("notifiers", {}).get("discord_webhook_url", "")).strip()
        lines = [f"**Daily Swing Screener Alert** (`{regime.regime if regime else 'N/A'}`)", ""]
        for item in top_alerts[:10]:
            sig = ", ".join(item.get("signals", [])[:2])
            lines.append(
                f"• **{item['symbol']}** (`${item['last_price']:.2f}`, `{item['change_pct']:+.1f}%`) — Score **{item['score']:.0f}** [{item['bias'].upper()}] {sig}"
            )
        return self._post(url, "\n".join(lines))

    def _post(self, url: str, content: str) -> tuple[bool, str]:
        try:
            with httpx.Client(timeout=8.0) as client:
                resp = client.post(url, json={"content": content})
                if 200 <= resp.status_code < 300:
                    return True, "Discord webhook sent."
                return False, f"Discord HTTP {resp.status_code}: {resp.text[:160]}"
        except Exception as exc:
            return False, f"Discord send failed: {exc}"
