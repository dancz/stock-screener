from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.contracts import MarketRegime, NotifierPlugin

logger = logging.getLogger(__name__)


class TelegramNotifierPlugin(NotifierPlugin):
    plugin_id = "telegram_notifier"
    display_name = "Telegram Alert Bot"
    description = "Sends daily scan summaries & high-conviction channel/signal alerts via Telegram Bot API (optional)."

    def is_configured(self, settings: dict[str, Any]) -> bool:
        cfg = settings.get("notifiers", {})
        return bool(
            cfg.get("telegram_enabled")
            and str(cfg.get("telegram_bot_token", "")).strip()
            and str(cfg.get("telegram_chat_id", "")).strip()
        )

    def send_test(self, settings: dict[str, Any]) -> tuple[bool, str]:
        cfg = settings.get("notifiers", {})
        token = str(cfg.get("telegram_bot_token", "")).strip()
        chat_id = str(cfg.get("telegram_chat_id", "")).strip()
        if not token or not chat_id:
            return False, "Telegram bot token and chat ID are required."
        return self._send_message(token, chat_id, "Stock Screener test alert: Telegram integration is working.")

    def notify_scan_summary(
        self,
        settings: dict[str, Any],
        top_alerts: list[dict[str, Any]],
        regime: MarketRegime | None,
    ) -> tuple[bool, str]:
        if not self.is_configured(settings) or not top_alerts:
            return False, "Not configured or no alerts."
        cfg = settings.get("notifiers", {})
        lines = [
            f"*Daily Swing Screener Alert* ({regime.regime if regime else 'N/A'})",
            "",
        ]
        for item in top_alerts[:8]:
            sig = ", ".join(item.get("signals", [])[:2])
            lines.append(
                f"• *{item['symbol']}* (${item['last_price']:.2f}, {item['change_pct']:+.1f}%) — Score *{item['score']:.0f}* [{item['bias'].upper()}] {sig}"
            )
        return self._send_message(
            str(cfg["telegram_bot_token"]).strip(),
            str(cfg["telegram_chat_id"]).strip(),
            "\n".join(lines),
            parse_mode="Markdown",
        )

    def _send_message(
        self, token: str, chat_id: str, text: str, parse_mode: str | None = None
    ) -> tuple[bool, str]:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        try:
            with httpx.Client(timeout=8.0) as client:
                resp = client.post(url, json=payload)
                if resp.status_code == 200:
                    return True, "Telegram message sent."
                return False, f"Telegram API HTTP {resp.status_code}: {resp.text[:160]}"
        except Exception as exc:
            return False, f"Telegram send failed: {exc}"
