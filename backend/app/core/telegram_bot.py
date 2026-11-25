from __future__ import annotations

import html
import logging
import threading
from typing import Any, Literal

import httpx

from app.core.registry import registry
from app.db.database import session_scope
from app.db.models import PaperTrade, load_runtime_settings

logger = logging.getLogger(__name__)

_poll_lock = threading.Lock()
_last_update_id: int | None = None
_active_token: str = ""


def _tg_category_allowed(notifiers_cfg: dict[str, Any], category: Literal["orders", "lifecycle", "kill_switch"]) -> bool:
    if category == "orders":
        return bool(notifiers_cfg.get("telegram_notify_paper_orders", True))
    if category == "lifecycle":
        return bool(notifiers_cfg.get("telegram_notify_paper_lifecycle", True))
    if category == "kill_switch":
        return bool(notifiers_cfg.get("telegram_notify_kill_switch", True))
    return True


def dispatch_paper_notification(
    runtime_settings: dict[str, Any],
    category: Literal["orders", "lifecycle", "kill_switch"],
    title: str,
    lines: list[str],
) -> None:
    """Safely broadcast a Paper Trading notification to configured Telegram/Discord plugins."""
    cfg = runtime_settings.get("notifiers", {})
    for notifier in registry.get_notifiers():
        if not notifier.is_configured(runtime_settings):
            continue
        if notifier.plugin_id == "telegram_notifier" and not _tg_category_allowed(cfg, category):
            continue
        try:
            notifier.notify_paper_event(runtime_settings, category, title, lines)
        except Exception as exc:
            logger.debug("Paper notification error (%s): %s", notifier.plugin_id, exc)


def _send_telegram_html(token: str, chat_id: str, text_html: str) -> tuple[bool, str]:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        with httpx.Client(timeout=8.0) as client:
            resp = client.post(
                url,
                json={"chat_id": chat_id, "text": text_html, "parse_mode": "HTML", "disable_web_page_preview": True},
            )
            if resp.status_code == 200:
                return True, "Sent"
            return False, f"HTTP {resp.status_code}: {resp.text[:160]}"
    except Exception as exc:
        return False, str(exc)


def detect_telegram_chats(bot_token: str) -> dict[str, Any]:
    """Validate a Telegram bot token via getMe and discover recent chat IDs via getUpdates."""
    token = str(bot_token or "").strip()
    if not token:
        return {"ok": False, "chats": [], "message": "Enter your Telegram Bot Token first."}

    try:
        with httpx.Client(timeout=8.0) as client:
            me_resp = client.get(f"https://api.telegram.org/bot{token}/getMe")
            if me_resp.status_code != 200:
                return {
                    "ok": False,
                    "chats": [],
                    "message": f"Invalid bot token (HTTP {me_resp.status_code}). Check token from @BotFather.",
                }
            me_data = me_resp.json().get("result") or {}
            bot_username = str(me_data.get("username") or "")
            bot_name = str(me_data.get("first_name") or "SwingPulse Bot")

            upd_resp = client.get(f"https://api.telegram.org/bot{token}/getUpdates", params={"limit": 25})
            if upd_resp.status_code != 200:
                return {
                    "ok": True,
                    "bot_username": bot_username,
                    "bot_name": bot_name,
                    "suggested_chat_id": "",
                    "chats": [],
                    "message": f"Verified @{bot_username}, but getUpdates returned HTTP {upd_resp.status_code}.",
                }
            updates = upd_resp.json().get("result") or []
    except Exception as exc:
        return {"ok": False, "chats": [], "message": f"Telegram API error: {exc}"}

    seen: dict[str, dict[str, Any]] = {}
    for upd in reversed(updates):
        msg = upd.get("message") or upd.get("edited_message") or upd.get("channel_post") or {}
        chat = msg.get("chat") or (upd.get("my_chat_member") or {}).get("chat") or {}
        cid = chat.get("id")
        if cid is None:
            continue
        cid_str = str(cid)
        if cid_str in seen:
            continue
        username = chat.get("username") or ""
        first_name = chat.get("first_name") or ""
        last_name = chat.get("last_name") or ""
        title = chat.get("title") or ""
        label = title or " ".join(p for p in (first_name, last_name) if p).strip() or (f"@{username}" if username else cid_str)
        seen[cid_str] = {
            "chat_id": cid_str,
            "type": str(chat.get("type") or "private"),
            "label": label,
            "username": username,
            "last_text": str(msg.get("text") or "")[:80],
        }

    chats = list(seen.values())
    if chats:
        suggested = chats[0]["chat_id"]
        msg = f"Verified @{bot_username} and detected {len(chats)} chat(s). Selected Chat ID {suggested}."
    else:
        suggested = ""
        msg = f"Verified @{bot_username}, but no messages were found yet. Open Telegram, send /start to @{bot_username}, and click Verify again."

    return {
        "ok": True,
        "bot_username": bot_username,
        "bot_name": bot_name,
        "suggested_chat_id": suggested,
        "chats": chats,
        "message": msg,
    }


def _handle_command(cmd_text: str) -> str:
    raw = cmd_text.strip().split()[0].lower()
    cmd = raw.split("@", 1)[0]

    if cmd in ("/start", "/help"):
        return (
            "<b>SwingPulse Bot Commands</b>\n"
            "/status — Paper account equity, return, win rate &amp; kill-switch\n"
            "/positions — List currently open &amp; pending positions\n"
            "/trades — Last 5 closed paper trades\n"
            "/scan — Trigger a background Quick Scan\n"
            "/halt — Activate Emergency Kill-Switch (pause new entries)"
        )

    if cmd in ("/status", "/overview"):
        from app.core.paper_analytics import build_paper_overview

        with session_scope() as session:
            ov = build_paper_overview(session)
            r = ov["run"]
            k = ov["kpis"]
            mode_lbl = "IBKR PAPER" if r["mode"] == "ibkr_paper" else "VIRTUAL"
            acct_part = f" ({r['ibkr_account']})" if r.get("ibkr_account") else ""
            ks = f"HALTED ({r['kill_switch_reason']})" if r["kill_switch_triggered"] else "ACTIVE"
            return (
                f"<b>{html.escape(r['name'])}</b>\n"
                f"Mode: <code>{mode_lbl}{html.escape(acct_part)}</code> · State: <b>{html.escape(ks)}</b>\n"
                f"Equity: <b>${r['current_equity']:,.2f}</b> ({k['total_return_pct']:+.2f}% / ${k['total_return_usd']:+,.2f})\n"
                f"Cash: ${r['cash_balance']:,.2f} · Max DD: {k['max_drawdown_pct']:.2f}%\n"
                f"Positions: {k['open_count']} Open, {k['pending_count']} Pending\n"
                f"Closed: {k['closed_count']} ({k['wins_count']}W/{k['losses_count']}L · Win {k['win_rate_pct']:.1f}% · Exp {k['expectancy_r']:+.2f}R)"
            )

    if cmd in ("/positions", "/open"):
        from app.core.paper_risk import ensure_active_paper_run

        with session_scope() as session:
            run = ensure_active_paper_run(session)
            rows = (
                session.query(PaperTrade)
                .filter(
                    PaperTrade.run_id == run.id,
                    PaperTrade.status.in_(("PENDING_ENTRY", "OPEN", "PARTIAL_TP1")),
                )
                .order_by(PaperTrade.id.asc())
                .all()
            )
            if not rows:
                return "<b>Open &amp; Pending Positions</b>\nNo active or pending paper positions."
            lines = [f"<b>Active Positions ({len(rows)})</b>"]
            for t in rows[:15]:
                entry_px = t.entry_fill_price or t.planned_limit_price
                mark_px = t.last_mark_price or entry_px
                cur_r = (float(t.unrealized_pnl_usd) / float(t.risk_amount_usd)) if t.risk_amount_usd > 0 else 0.0
                flags = []
                if t.be_moved:
                    flags.append("BE")
                if getattr(t, "trailing_active", False):
                    flags.append("TRAIL")
                flag_str = f" [{'+'.join(flags)}]" if flags else ""
                lines.append(
                    f"• <b>{html.escape(t.symbol)}</b> {t.direction} x{t.quantity_planned} [{t.status}]{flag_str}\n"
                    f"  Entry ${entry_px:.2f} → Mark ${mark_px:.2f} | SL ${t.current_stop_loss:.2f} | TP ${t.planned_tp1:.2f} | PnL ${t.unrealized_pnl_usd:+.2f} ({cur_r:+.2f}R)"
                )
            return "\n".join(lines)

    if cmd == "/trades":
        from app.core.paper_risk import ensure_active_paper_run

        with session_scope() as session:
            run = ensure_active_paper_run(session)
            rows = (
                session.query(PaperTrade)
                .filter(PaperTrade.run_id == run.id, PaperTrade.status == "CLOSED")
                .order_by(PaperTrade.closed_at.desc(), PaperTrade.id.desc())
                .limit(5)
                .all()
            )
            if not rows:
                return "<b>Recent Closed Trades</b>\nNo closed paper trades yet."
            lines = ["<b>Last 5 Closed Trades</b>"]
            for t in rows:
                lines.append(
                    f"• <b>{html.escape(t.symbol)}</b> {t.direction} ({html.escape(t.exit_reason or 'CLOSED')}): "
                    f"${t.realized_pnl_usd:+.2f} ({t.r_multiple:+.2f}R)"
                )
            return "\n".join(lines)

    if cmd == "/scan":
        from app.core.pipeline import get_scan_status, run_scan

        status = get_scan_status()
        if status.get("running"):
            return f"A scan is already running ({html.escape(str(status.get('message') or 'in progress'))})."
        threading.Thread(target=run_scan, kwargs={"mode": "quick"}, daemon=True).start()
        return "<b>Quick Scan Started</b>\nBackground quick scan launched; summary and paper allocation will follow on completion."

    if cmd == "/halt":
        from app.core.paper_virtual import set_paper_kill_switch

        with session_scope() as session:
            set_paper_kill_switch(session, halt=True, reason="Halted via Telegram /halt command", close_all=False)
        return (
            "<b>Kill-Switch Activated</b>\n"
            "New autonomous entries are paused. Existing protective Stop-Loss orders remain intact."
        )

    return "Unknown command. Send /help for available commands."


def poll_telegram_commands_once() -> dict[str, Any]:
    """Poll Telegram getUpdates once and answer authorized commands from the configured chat_id."""
    global _last_update_id, _active_token

    if not _poll_lock.acquire(blocking=False):
        return {"polled": False, "reason": "busy"}
    try:
        with session_scope() as session:
            runtime = load_runtime_settings(session)
        cfg = runtime.get("notifiers", {})
        if not (
            cfg.get("telegram_enabled")
            and cfg.get("telegram_commands_enabled", True)
            and str(cfg.get("telegram_bot_token", "")).strip()
            and str(cfg.get("telegram_chat_id", "")).strip()
        ):
            return {"polled": False, "reason": "disabled"}

        token = str(cfg["telegram_bot_token"]).strip()
        auth_chat_id = str(cfg["telegram_chat_id"]).strip()

        if token != _active_token:
            _active_token = token
            _last_update_id = None

        params: dict[str, Any] = {"timeout": 1, "limit": 20}
        if _last_update_id is not None:
            params["offset"] = _last_update_id

        with httpx.Client(timeout=8.0) as client:
            resp = client.get(f"https://api.telegram.org/bot{token}/getUpdates", params=params)
            if resp.status_code != 200:
                return {"polled": False, "reason": f"HTTP {resp.status_code}"}
            updates = resp.json().get("result") or []

        if not updates:
            return {"polled": True, "processed": 0}

        max_id = max(int(u["update_id"]) for u in updates)
        if _last_update_id is None:
            # Prime offset past historical messages on first poll so restart never replays old commands
            _last_update_id = max_id + 1
            return {"polled": True, "primed": True, "processed": 0}

        _last_update_id = max_id + 1
        processed = 0
        for upd in updates:
            msg = upd.get("message") or {}
            chat_id = str((msg.get("chat") or {}).get("id", "")).strip()
            text = str(msg.get("text") or "").strip()
            if not text.startswith("/") or chat_id != auth_chat_id:
                continue
            reply = _handle_command(text)
            _send_telegram_html(token, auth_chat_id, reply)
            processed += 1

        return {"polled": True, "processed": processed}
    except Exception as exc:
        logger.debug("Telegram command poll error: %s", exc)
        return {"polled": False, "reason": str(exc)}
    finally:
        _poll_lock.release()
