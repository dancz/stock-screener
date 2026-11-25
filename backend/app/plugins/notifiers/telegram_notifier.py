from __future__ import annotations

import html
import logging
from typing import Any

import httpx

from app.core.contracts import MarketRegime, NotifierPlugin

logger = logging.getLogger(__name__)


class TelegramNotifierPlugin(NotifierPlugin):
    plugin_id = "telegram_notifier"
    display_name = "Telegram Alert Bot"
    description = "Sends scan summaries, autonomous paper trade alerts, and handles interactive bot commands."

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
        msg = (
            "<b>SwingPulse Telegram Bot Connected</b>\n"
            "Scan summaries, Paper Trading execution alerts, and Kill-Switch notifications are active.\n"
            "Send /help to view interactive portfolio commands."
        )
        return self._send_message(token, chat_id, msg, parse_mode="HTML")

    def notify_scan_summary(
        self,
        settings: dict[str, Any],
        top_alerts: list[dict[str, Any]],
        regime: MarketRegime | None,
    ) -> tuple[bool, str]:
        cfg = settings.get("notifiers", {})
        if not self.is_configured(settings) or not top_alerts or not cfg.get("telegram_notify_scan", True):
            return False, "Not configured, disabled, or no alerts."
        regime_txt = html.escape(regime.regime if regime else "N/A")
        lines = [
            f"<b>Daily Swing Screener Alert</b> ({regime_txt})",
            "",
        ]
        for item in top_alerts[:8]:
            sig = html.escape(", ".join(item.get("signals", [])[:2]))
            sym = html.escape(str(item["symbol"]))
            bias = html.escape(str(item["bias"]).upper())
            lines.append(
                f"• <b>{sym}</b> (${item['last_price']:.2f}, {item['change_pct']:+.1f}%) — Score <b>{item['score']:.0f}</b> [{bias}] {sig}"
            )
        return self._send_message(
            str(cfg["telegram_bot_token"]).strip(),
            str(cfg["telegram_chat_id"]).strip(),
            "\n".join(lines),
            parse_mode="HTML",
        )

    def notify_paper_event(
        self,
        settings: dict[str, Any],
        event_type: str,
        title: str,
        lines: list[str],
    ) -> tuple[bool, str]:
        if not self.is_configured(settings):
            return False, "Telegram not configured."
        cfg = settings.get("notifiers", {})
        body_lines = [f"<b>{html.escape(title)}</b>"]
        for line in lines:
            if line:
                body_lines.append(html.escape(line))
        return self._send_message(
            str(cfg["telegram_bot_token"]).strip(),
            str(cfg["telegram_chat_id"]).strip(),
            "\n".join(body_lines),
            parse_mode="HTML",
        )

    def _send_message(
        self, token: str, chat_id: str, text: str, parse_mode: str | None = None
    ) -> tuple[bool, str]:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
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
