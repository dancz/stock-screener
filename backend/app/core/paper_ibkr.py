from __future__ import annotations

import asyncio
import itertools
import json
import logging
import queue
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Coroutine

from sqlalchemy.orm import Session

from app.core.paper_risk import as_utc, check_kill_switch, ensure_active_paper_run
from app.core.request_queue import Priority, SlidingWindowLimiter
from app.core.telegram_bot import dispatch_paper_notification
from app.db.models import PaperAccountRun, PaperTrade, load_runtime_settings, utcnow
from app.plugins.sources.market_data_ibkr import _make_ibkr_contract, is_gateway_reachable

logger = logging.getLogger(__name__)

ALLOWED_PAPER_PORTS = (4002, 7497)


class LiveAccountBlockedError(RuntimeError):
    """Raised whenever IBKR configuration or session points to a non-paper port or live account."""


class GatewayReadOnlyError(RuntimeError):
    """Raised when IBKR Gateway itself is configured with 'Read-Only API' (Error 321)."""

    def __init__(self, message: str, account: str = "") -> None:
        super().__init__(message)
        self.account = account


def assert_paper_account(accounts: list[str] | tuple[str, ...], port: int, expected_account: str = "") -> str:
    """Enforce strict Paper-Only safety lock on IBKR port and managed account prefix (DU* / DF*)."""
    p = int(port)
    if p not in ALLOWED_PAPER_PORTS:
        raise LiveAccountBlockedError(
            f"Blocked IBKR port {p}: only paper ports {ALLOWED_PAPER_PORTS} are allowed for autonomous order routing."
        )

    clean = [str(a).strip() for a in (accounts or []) if str(a).strip()]
    if not clean:
        raise LiveAccountBlockedError("IBKR returned no managed accounts; refusing order execution.")

    for acct in clean:
        upper = acct.upper()
        if not ((upper.startswith("DU") or upper.startswith("DF")) and len(upper) >= 5):
            raise LiveAccountBlockedError(
                f"Blocked non-paper IBKR account '{acct}'. Autonomous orders are restricted exclusively to DU*/DF* paper accounts."
            )

    exp = expected_account.strip()
    if exp:
        if exp not in clean:
            raise LiveAccountBlockedError(
                f"Configured paper account '{exp}' not found in managed accounts {clean}."
            )
        return exp
    return clean[0]


@dataclass
class _PaperIbkrJob:
    host: str
    port: int
    client_id: int
    is_order: bool
    expected_account: str
    coro_factory: Callable[[Any, str], Coroutine[Any, Any, Any]]
    future: Future[Any]
    timeout: float
    max_wait: float | None


class IbkrPaperExecutionWorker:
    """Dedicated lazy daemon thread owning an isolated read-write IBKR Paper session (clientId + 10)."""

    def __init__(self, *, client_id_offset: int = 10, idle_timeout_s: float = 180.0) -> None:
        self.client_id_offset = int(client_id_offset)
        self.idle_timeout_s = float(idle_timeout_s)
        self.order_limiter = SlidingWindowLimiter(max_calls=8, window_seconds=60.0, min_interval=1.5)
        self.reconcile_limiter = SlidingWindowLimiter(max_calls=20, window_seconds=60.0, min_interval=0.5)
        self._pq: queue.PriorityQueue[tuple[int, int, _PaperIbkrJob | None]] = queue.PriorityQueue()
        self._seq = itertools.count()
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._connected = False
        self._verified_account = ""

    def _ensure_worker(self) -> None:
        if self._worker is None or not self._worker.is_alive():
            self._stop_event.clear()
            self._worker = threading.Thread(
                target=self._run_loop,
                name="ibkr-paper-exec-worker",
                daemon=True,
            )
            self._worker.start()

    def run(
        self,
        coro_factory: Callable[[Any, str], Coroutine[Any, Any, Any]],
        *,
        host: str,
        port: int,
        client_id: int,
        is_order: bool = False,
        expected_account: str = "",
        priority: int = Priority.NORMAL,
        timeout: float = 20.0,
        max_wait: float | None = 15.0,
    ) -> Any:
        # Fail fast before enqueuing if port is non-paper
        if int(port) not in ALLOWED_PAPER_PORTS:
            raise LiveAccountBlockedError(
                f"Blocked IBKR port {port}: only paper ports {ALLOWED_PAPER_PORTS} are allowed."
            )
        fut: Future[Any] = Future()
        with self._lock:
            self._ensure_worker()
            job = _PaperIbkrJob(
                host=str(host),
                port=int(port),
                client_id=int(client_id),
                is_order=bool(is_order),
                expected_account=str(expected_account or ""),
                coro_factory=coro_factory,
                future=fut,
                timeout=float(timeout),
                max_wait=max_wait,
            )
            self._pq.put((int(priority), next(self._seq), job))
        return fut.result(timeout=float(timeout) + float(max_wait or 0.0) + 5.0)

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        ib: Any = None
        active_endpoint: tuple[str, int, int] | None = None
        last_used_mono = 0.0
        readonly_flag = {"hit": False}

        def _on_ib_error(req_id: int, error_code: int, error_string: str, contract: Any = None) -> None:
            if int(error_code) == 321 and "read-only" in (error_string or "").lower():
                readonly_flag["hit"] = True

        def _disconnect_ib() -> None:
            nonlocal ib, active_endpoint
            if ib is not None:
                try:
                    if ib.isConnected():
                        ib.disconnect()
                except Exception:
                    pass
                ib = None
            active_endpoint = None
            readonly_flag["hit"] = False
            with self._lock:
                self._connected = False
                self._verified_account = ""

        try:
            while not self._stop_event.is_set():
                try:
                    _prio, _seq, job = self._pq.get(timeout=5.0)
                except queue.Empty:
                    if ib is not None and (time.monotonic() - last_used_mono) >= self.idle_timeout_s:
                        logger.info("Disconnecting idle IBKR Paper execution session after %.0fs.", self.idle_timeout_s)
                        _disconnect_ib()
                    continue

                if job is None:
                    break

                try:
                    if int(job.port) not in ALLOWED_PAPER_PORTS:
                        raise LiveAccountBlockedError(f"Blocked non-paper IBKR port {job.port}.")

                    limiter = self.order_limiter if job.is_order else self.reconcile_limiter
                    if not limiter.acquire(max_wait=job.max_wait):
                        raise TimeoutError("IBKR Paper execution rate-limit wait exceeded")

                    target_endpoint = (job.host, job.port, job.client_id + self.client_id_offset)
                    if ib is not None and (not ib.isConnected() or active_endpoint != target_endpoint):
                        _disconnect_ib()

                    if ib is None:
                        if not is_gateway_reachable(job.host, job.port, timeout=0.5):
                            raise ConnectionError(f"IBKR Gateway {job.host}:{job.port} is not reachable.")
                        from ib_async import IB

                        cand = IB()
                        cand.errorEvent += _on_ib_error
                        readonly_flag["hit"] = False
                        try:
                            loop.run_until_complete(
                                cand.connectAsync(
                                    target_endpoint[0],
                                    target_endpoint[1],
                                    clientId=target_endpoint[2],
                                    timeout=3.5,
                                    readonly=False,
                                )
                            )
                            du_account = assert_paper_account(
                                cand.managedAccounts(),
                                job.port,
                                expected_account=job.expected_account,
                            )
                            if readonly_flag["hit"]:
                                raise GatewayReadOnlyError(
                                    f"Connected to Paper account {du_account} on {job.host}:{job.port}, but IBKR Gateway has 'Read-Only API' enabled (Error 321). Uncheck Configure -> Settings -> API -> Read-Only API in IB Gateway.",
                                    account=du_account,
                                )
                            ib = cand
                            active_endpoint = target_endpoint
                            with self._lock:
                                self._connected = True
                                self._verified_account = du_account
                        except Exception:
                            try:
                                if cand.isConnected():
                                    cand.disconnect()
                            except Exception:
                                pass
                            raise
                    else:
                        du_account = assert_paper_account(
                            ib.managedAccounts(),
                            job.port,
                            expected_account=job.expected_account,
                        )
                        if readonly_flag["hit"]:
                            raise GatewayReadOnlyError(
                                f"IBKR Gateway ({du_account}) is in 'Read-Only API' mode (Error 321).",
                                account=du_account,
                            )

                    async def _exec() -> Any:
                        return await asyncio.wait_for(job.coro_factory(ib, du_account), timeout=job.timeout)

                    res = loop.run_until_complete(_exec())
                    if readonly_flag["hit"]:
                        raise GatewayReadOnlyError(
                            f"IBKR Gateway ({du_account}) rejected write operation because 'Read-Only API' mode is active (Error 321).",
                            account=du_account,
                        )
                    last_used_mono = time.monotonic()
                    if not job.future.done():
                        job.future.set_result(res)
                except LiveAccountBlockedError as exc:
                    _disconnect_ib()
                    logger.error("SAFETY LOCK TRIGGERED: %s", exc)
                    if not job.future.done():
                        job.future.set_exception(exc)
                except GatewayReadOnlyError as exc:
                    _disconnect_ib()
                    logger.warning("IBKR Gateway Read-Only mode: %s", exc)
                    if not job.future.done():
                        job.future.set_exception(exc)
                except Exception as exc:
                    if isinstance(exc, (ConnectionError, OSError)) or (ib is not None and not ib.isConnected()):
                        _disconnect_ib()
                    if not job.future.done():
                        job.future.set_exception(exc)
        finally:
            _disconnect_ib()
            try:
                loop.close()
            finally:
                asyncio.set_event_loop(None)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "connected": self._connected,
                "verified_account": self._verified_account,
                "pending": self._pq.qsize(),
                "order_limiter": self.order_limiter.stats(),
                "reconcile_limiter": self.reconcile_limiter.stats(),
            }

    def shutdown(self) -> None:
        with self._lock:
            worker = self._worker
            if worker is None:
                return
            self._stop_event.set()
            self._pq.put((int(Priority.LOW) + 100, next(self._seq), None))
        if worker.is_alive():
            worker.join(timeout=3.0)
        with self._lock:
            self._worker = None


ibkr_paper_worker = IbkrPaperExecutionWorker()


def probe_ibkr_paper_account(host: str, port: int, client_id: int) -> dict[str, Any]:
    """Verify IBKR Paper Gateway connection, DU* account prefix, NetLiquidation & open counts."""
    if int(port) not in ALLOWED_PAPER_PORTS:
        return {
            "connected": False,
            "paper_verified": False,
            "account": "",
            "open_orders_count": 0,
            "positions_count": 0,
            "net_liquidation": None,
            "currency": "USD",
            "message": f"Blocked port {port}: only paper ports 4002 / 7497 are permitted.",
        }
    if not is_gateway_reachable(host, port, timeout=0.6):
        return {
            "connected": False,
            "paper_verified": False,
            "account": "",
            "open_orders_count": 0,
            "positions_count": 0,
            "net_liquidation": None,
            "currency": "USD",
            "message": f"IBKR Gateway {host}:{port} is not reachable.",
        }

    async def _probe(ib: Any, du_acct: str) -> dict[str, Any]:
        try:
            await asyncio.wait_for(ib.reqOpenOrdersAsync(), timeout=3.0)
        except Exception:
            pass
        open_orders = [
            t for t in ib.openTrades() if (getattr(t.order, "account", "") or du_acct) == du_acct
        ]
        positions = [
            p for p in ib.positions() if (getattr(p, "account", "") or du_acct) == du_acct and float(p.position or 0.0) != 0.0
        ]
        net_liq: float | None = None
        currency = "USD"
        vals = ib.accountValues(du_acct) if hasattr(ib, "accountValues") else []
        for pref_curr in ("USD", "BASE"):
            match = next(
                (v for v in vals if getattr(v, "tag", "") == "NetLiquidation" and getattr(v, "currency", "") == pref_curr),
                None,
            )
            if match is not None:
                try:
                    net_liq = round(float(match.value), 2)
                    currency = pref_curr
                    break
                except Exception:
                    pass
        if net_liq is None:
            any_nl = next((v for v in vals if getattr(v, "tag", "") == "NetLiquidation"), None)
            if any_nl is not None:
                try:
                    net_liq = round(float(any_nl.value), 2)
                    currency = str(getattr(any_nl, "currency", "USD") or "USD")
                except Exception:
                    pass
        return {
            "connected": True,
            "paper_verified": True,
            "account": du_acct,
            "open_orders_count": len(open_orders),
            "positions_count": len(positions),
            "net_liquidation": net_liq,
            "currency": currency,
            "message": f"Verified Paper account {du_acct} on {host}:{port}",
        }

    try:
        return ibkr_paper_worker.run(
            _probe,
            host=host,
            port=port,
            client_id=client_id,
            is_order=False,
            priority=Priority.HIGH,
            timeout=10.0,
            max_wait=4.0,
        )
    except GatewayReadOnlyError as exc:
        return {
            "connected": True,
            "paper_verified": False,
            "account": exc.account,
            "open_orders_count": 0,
            "positions_count": 0,
            "net_liquidation": None,
            "currency": "USD",
            "message": str(exc),
        }
    except Exception as exc:
        err_detail = str(exc).strip() or type(exc).__name__
        return {
            "connected": False,
            "paper_verified": False,
            "account": "",
            "open_orders_count": 0,
            "positions_count": 0,
            "net_liquidation": None,
            "currency": "USD",
            "message": f"Paper account verification failed: {err_detail}",
        }


def submit_ibkr_bracket_order(
    *,
    symbol: str,
    direction: str,
    quantity: int,
    limit_price: float,
    stop_loss: float,
    tp_price: float,
    ibkr_cfg: dict[str, Any],
    expected_account: str = "",
) -> dict[str, Any]:
    """Place an atomic IBKR Bracket order (DAY Limit parent + GTC TP + GTC SL) strictly on DU* paper account."""
    host = str(ibkr_cfg.get("host", "127.0.0.1"))
    port = int(ibkr_cfg.get("port", 4002))
    client_id = int(ibkr_cfg.get("client_id", 17))
    sym = symbol.strip().upper().replace(".", "-")
    dir_up = direction.strip().upper()
    action = "BUY" if dir_up == "LONG" else "SELL"
    rev_action = "SELL" if dir_up == "LONG" else "BUY"
    qty = int(quantity)

    async def _submit(ib: Any, du_acct: str) -> dict[str, Any]:
        from ib_async import LimitOrder, StopOrder

        contract = _make_ibkr_contract(sym)
        qualified = await ib.qualifyContractsAsync(contract)
        if not qualified:
            return {"ok": False, "error": f"Failed to qualify IBKR contract for {sym}."}
        qc = qualified[0]

        parent_id = ib.client.getReqId()
        tp_id = ib.client.getReqId()
        sl_id = ib.client.getReqId()

        parent = LimitOrder(
            action,
            qty,
            round(float(limit_price), 2),
            orderId=parent_id,
            tif="DAY",
            transmit=False,
            outsideRth=False,
            account=du_acct,
        )
        tp_order = LimitOrder(
            rev_action,
            qty,
            round(float(tp_price), 2),
            orderId=tp_id,
            parentId=parent_id,
            tif="GTC",
            transmit=False,
            outsideRth=False,
            account=du_acct,
        )
        sl_order = StopOrder(
            rev_action,
            qty,
            round(float(stop_loss), 2),
            orderId=sl_id,
            parentId=parent_id,
            tif="GTC",
            transmit=True,
            outsideRth=False,
            account=du_acct,
        )

        p_trade = ib.placeOrder(qc, parent)
        tp_trade = ib.placeOrder(qc, tp_order)
        sl_trade = ib.placeOrder(qc, sl_order)
        await asyncio.sleep(0.8)

        p_status = str(getattr(p_trade.orderStatus, "status", "") or "Submitted")
        if p_status in ("Cancelled", "ApiCancelled", "Inactive"):
            for tr in (sl_trade, tp_trade):
                try:
                    ib.cancelOrder(tr.order)
                except Exception:
                    pass
            reason = ""
            if getattr(p_trade, "log", None):
                last_log = p_trade.log[-1]
                reason = str(getattr(last_log, "message", "") or "")
            return {"ok": False, "error": f"IBKR rejected bracket order ({p_status}): {reason or 'inactive'}"}

        filled_qty = int(round(float(getattr(p_trade.orderStatus, "filled", 0.0) or 0.0)))
        avg_px = float(getattr(p_trade.orderStatus, "avgFillPrice", 0.0) or 0.0)
        perm_ids = [
            int(getattr(t.order, "permId", 0) or 0)
            for t in (p_trade, sl_trade, tp_trade)
            if int(getattr(t.order, "permId", 0) or 0) > 0
        ]
        return {
            "ok": True,
            "account": du_acct,
            "parent_order_id": int(parent_id),
            "stop_order_id": int(sl_id),
            "tp1_order_id": int(tp_id),
            "perm_ids": perm_ids,
            "ibkr_status": p_status,
            "filled_qty": filled_qty,
            "avg_fill_price": round(avg_px, 2) if avg_px > 0 else None,
        }

    return ibkr_paper_worker.run(
        _submit,
        host=host,
        port=port,
        client_id=client_id,
        is_order=True,
        expected_account=expected_account,
        priority=Priority.HIGH,
        timeout=20.0,
        max_wait=15.0,
    )


def modify_ibkr_stop_loss(
    *,
    symbol: str,
    direction: str,
    quantity: int,
    stop_order_id: int | None,
    new_stop_price: float,
    ibkr_cfg: dict[str, Any],
    expected_account: str = "",
) -> tuple[bool, int | None, str]:
    """Update existing protective StopOrder auxPrice in-place on IBKR Paper."""
    host = str(ibkr_cfg.get("host", "127.0.0.1"))
    port = int(ibkr_cfg.get("port", 4002))
    client_id = int(ibkr_cfg.get("client_id", 17))
    sym = symbol.strip().upper().replace(".", "-")
    dir_up = direction.strip().upper()
    rev_action = "SELL" if dir_up == "LONG" else "BUY"
    target_px = round(float(new_stop_price), 2)

    async def _modify(ib: Any, du_acct: str) -> tuple[bool, int | None, str]:
        try:
            await ib.reqOpenOrdersAsync()
        except Exception:
            pass
        open_trades = list(ib.openTrades())
        match = None
        if stop_order_id:
            match = next(
                (t for t in open_trades if int(getattr(t.order, "orderId", 0) or 0) == int(stop_order_id)),
                None,
            )
        if match is None:
            ib_sym = sym.replace("-", " ")
            match = next(
                (
                    t
                    for t in open_trades
                    if getattr(t.contract, "symbol", "") == ib_sym
                    and getattr(t.order, "action", "") == rev_action
                    and getattr(t.order, "orderType", "") == "STP"
                    and (getattr(t.order, "account", "") or du_acct) == du_acct
                ),
                None,
            )
        if match is None:
            from ib_async import StopOrder

            contract = _make_ibkr_contract(sym)
            qualified = await ib.qualifyContractsAsync(contract)
            if not qualified:
                return False, stop_order_id, f"Contract {sym} could not be qualified"
            new_id = ib.client.getReqId()
            sl = StopOrder(
                rev_action,
                int(quantity),
                target_px,
                orderId=new_id,
                tif="GTC",
                transmit=True,
                outsideRth=False,
                account=du_acct,
            )
            ib.placeOrder(qualified[0], sl)
            await asyncio.sleep(0.4)
            return True, int(new_id), f"Placed replacement stop order @ ${target_px:.2f}"

        match.order.auxPrice = target_px
        match.order.account = du_acct
        match.order.transmit = True
        ib.placeOrder(match.contract, match.order)
        await asyncio.sleep(0.4)
        return True, int(match.order.orderId), f"Modified IBKR SL #{match.order.orderId} -> ${target_px:.2f}"

    try:
        return ibkr_paper_worker.run(
            _modify,
            host=host,
            port=port,
            client_id=client_id,
            is_order=True,
            expected_account=expected_account,
            priority=Priority.HIGH,
            timeout=15.0,
            max_wait=12.0,
        )
    except Exception as exc:
        logger.warning("IBKR stop modify failed for %s: %s", sym, exc)
        return False, stop_order_id, str(exc)


def close_ibkr_trade(
    *,
    symbol: str,
    direction: str,
    quantity: int,
    parent_order_id: int | None,
    stop_order_id: int | None,
    tp1_order_id: int | None,
    is_pending: bool,
    ibkr_cfg: dict[str, Any],
    expected_account: str = "",
) -> dict[str, Any]:
    """Cancel open bracket legs for symbol and flatten any active IBKR paper position at market."""
    host = str(ibkr_cfg.get("host", "127.0.0.1"))
    port = int(ibkr_cfg.get("port", 4002))
    client_id = int(ibkr_cfg.get("client_id", 17))
    sym = symbol.strip().upper().replace(".", "-")
    ib_sym = sym.replace("-", " ")
    dir_up = direction.strip().upper()
    rev_action = "SELL" if dir_up == "LONG" else "BUY"
    tracked_ids = {int(oid) for oid in (parent_order_id, stop_order_id, tp1_order_id) if oid}

    async def _close(ib: Any, du_acct: str) -> dict[str, Any]:
        from ib_async import MarketOrder

        try:
            await ib.reqOpenOrdersAsync()
        except Exception:
            pass
        cancelled_cnt = 0
        for tr in list(ib.openTrades()):
            oid = int(getattr(tr.order, "orderId", 0) or 0)
            c_sym = str(getattr(tr.contract, "symbol", "") or "").upper()
            acct = str(getattr(tr.order, "account", "") or du_acct)
            if acct == du_acct and (oid in tracked_ids or c_sym == ib_sym):
                try:
                    ib.cancelOrder(tr.order)
                    cancelled_cnt += 1
                except Exception:
                    pass
        if cancelled_cnt:
            await asyncio.sleep(0.4)

        fill_price: float | None = None
        if not is_pending:
            try:
                positions = await ib.reqPositionsAsync()
            except Exception:
                positions = ib.positions()
            pos_qty = 0.0
            for p in positions:
                if (
                    str(getattr(p, "account", "") or du_acct) == du_acct
                    and str(getattr(p.contract, "symbol", "") or "").upper() == ib_sym
                ):
                    pos_qty += float(getattr(p, "position", 0.0) or 0.0)

            needed = (pos_qty > 0 and dir_up == "LONG") or (pos_qty < 0 and dir_up == "SHORT")
            if needed:
                close_qty = min(int(quantity or abs(int(pos_qty))), abs(int(round(pos_qty))))
                if close_qty > 0:
                    qc = await ib.qualifyContractsAsync(_make_ibkr_contract(sym))
                    if qc:
                        mkt = MarketOrder(rev_action, close_qty, tif="DAY", account=du_acct)
                        mkt_trade = ib.placeOrder(qc[0], mkt)
                        for _ in range(15):
                            await asyncio.sleep(0.2)
                            st = str(getattr(mkt_trade.orderStatus, "status", "") or "")
                            if st == "Filled":
                                px = float(getattr(mkt_trade.orderStatus, "avgFillPrice", 0.0) or 0.0)
                                if px > 0:
                                    fill_price = round(px, 2)
                                break
        return {"ok": True, "cancelled": cancelled_cnt, "fill_price": fill_price}

    return ibkr_paper_worker.run(
        _close,
        host=host,
        port=port,
        client_id=client_id,
        is_order=True,
        expected_account=expected_account,
        priority=Priority.HIGH,
        timeout=20.0,
        max_wait=15.0,
    )


def fetch_ibkr_reconcile_snapshot(
    ibkr_cfg: dict[str, Any],
    expected_account: str = "",
) -> dict[str, Any]:
    """Fetch open orders, positions, and recent executions from IBKR Paper account."""
    host = str(ibkr_cfg.get("host", "127.0.0.1"))
    port = int(ibkr_cfg.get("port", 4002))
    client_id = int(ibkr_cfg.get("client_id", 17))

    async def _snap(ib: Any, du_acct: str) -> dict[str, Any]:
        from ib_async import ExecutionFilter

        try:
            await ib.reqOpenOrdersAsync()
        except Exception:
            pass
        try:
            positions_raw = await ib.reqPositionsAsync()
        except Exception:
            positions_raw = ib.positions()
        try:
            fills_raw = await ib.reqExecutionsAsync(ExecutionFilter(acctCode=du_acct))
        except Exception:
            fills_raw = ib.fills() if hasattr(ib, "fills") else []

        open_orders: list[dict[str, Any]] = []
        for tr in ib.openTrades():
            acct = str(getattr(tr.order, "account", "") or du_acct)
            if acct != du_acct:
                continue
            raw_sym = str(getattr(tr.contract, "symbol", "") or "").upper().replace(" ", "-")
            open_orders.append(
                {
                    "order_id": int(getattr(tr.order, "orderId", 0) or 0),
                    "perm_id": int(getattr(tr.order, "permId", 0) or 0),
                    "symbol": raw_sym,
                    "action": str(getattr(tr.order, "action", "") or "").upper(),
                    "order_type": str(getattr(tr.order, "orderType", "") or "").upper(),
                    "quantity": int(round(float(getattr(tr.order, "totalQuantity", 0.0) or 0.0))),
                    "lmt_price": float(getattr(tr.order, "lmtPrice", 0.0) or 0.0),
                    "aux_price": float(getattr(tr.order, "auxPrice", 0.0) or 0.0),
                    "status": str(getattr(tr.orderStatus, "status", "") or ""),
                }
            )

        positions: dict[str, dict[str, Any]] = {}
        for p in positions_raw:
            if str(getattr(p, "account", "") or du_acct) != du_acct:
                continue
            sym = str(getattr(p.contract, "symbol", "") or "").upper().replace(" ", "-")
            qty = float(getattr(p, "position", 0.0) or 0.0)
            if sym and qty != 0.0:
                positions[sym] = {
                    "quantity": qty,
                    "avg_cost": float(getattr(p, "avgCost", 0.0) or 0.0),
                }

        executions: list[dict[str, Any]] = []
        for f in fills_raw:
            ex = getattr(f, "execution", f)
            cr = getattr(f, "commissionReport", None)
            c = getattr(f, "contract", None)
            if str(getattr(ex, "acctNumber", du_acct) or du_acct) != du_acct:
                continue
            sym = str(getattr(c, "symbol", "") or "").upper().replace(" ", "-")
            executions.append(
                {
                    "order_id": int(getattr(ex, "orderId", 0) or 0),
                    "perm_id": int(getattr(ex, "permId", 0) or 0),
                    "symbol": sym,
                    "side": str(getattr(ex, "side", "") or "").upper(),  # BOT / SLD
                    "shares": float(getattr(ex, "shares", 0.0) or 0.0),
                    "price": float(getattr(ex, "avgPrice", 0.0) or getattr(ex, "price", 0.0) or 0.0),
                    "commission": float(getattr(cr, "commission", 0.0) or 0.0) if cr else 0.0,
                }
            )

        return {
            "account": du_acct,
            "open_orders": open_orders,
            "positions": positions,
            "executions": executions,
        }

    return ibkr_paper_worker.run(
        _snap,
        host=host,
        port=port,
        client_id=client_id,
        is_order=False,
        expected_account=expected_account,
        priority=Priority.NORMAL,
        timeout=15.0,
        max_wait=10.0,
    )


def _restore_protective_stop(
    *,
    symbol: str,
    direction: str,
    quantity: int,
    stop_loss: float,
    ibkr_cfg: dict[str, Any],
    expected_account: str = "",
) -> int | None:
    ok, oid, _msg = modify_ibkr_stop_loss(
        symbol=symbol,
        direction=direction,
        quantity=quantity,
        stop_order_id=None,
        new_stop_price=stop_loss,
        ibkr_cfg=ibkr_cfg,
        expected_account=expected_account,
    )
    return oid if ok else None


def reconcile_ibkr_state(
    session: Session,
    run: PaperAccountRun | None = None,
    runtime_settings: dict[str, Any] | None = None,
    *,
    now_utc: datetime | None = None,
    snapshot_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Synchronize active IBKR Paper trades with IBKR Gateway open orders, positions, and executions."""
    from app.core.paper_virtual import (
        _close_trade,
        _compute_slippage_bps,
        _latest_mark_price,
        _log_event,
        recompute_run_equity,
    )

    cur_dt = as_utc(now_utc) or utcnow()
    runtime = runtime_settings or load_runtime_settings(session)
    target_run = run or ensure_active_paper_run(session, runtime, now_utc=cur_dt)
    if target_run.mode != "ibkr_paper":
        return {"run_id": target_run.id, "reconciled": False, "reason": "Active run is not ibkr_paper"}

    ibkr_cfg = runtime.get("ibkr", {})
    if snapshot_override is not None:
        snap = snapshot_override
    else:
        if not ibkr_cfg.get("enabled", False):
            return {"run_id": target_run.id, "reconciled": False, "reason": "IBKR disabled in settings"}
        snap = fetch_ibkr_reconcile_snapshot(ibkr_cfg, expected_account=target_run.ibkr_account)

    du_acct = str(snap.get("account") or target_run.ibkr_account or "")
    if du_acct and target_run.ibkr_account != du_acct:
        target_run.ibkr_account = du_acct

    open_orders_by_id = {int(o["order_id"]): o for o in snap.get("open_orders", []) if o.get("order_id")}
    positions_map: dict[str, dict[str, Any]] = snap.get("positions", {})
    exec_by_order: dict[int, dict[str, Any]] = {}
    exec_by_sym_side: dict[tuple[str, str], dict[str, Any]] = {}
    for ex in snap.get("executions", []):
        oid = int(ex.get("order_id") or 0)
        shares = float(ex.get("shares") or 0.0)
        px = float(ex.get("price") or 0.0)
        comm = float(ex.get("commission") or 0.0)
        if oid and shares > 0:
            prev = exec_by_order.setdefault(oid, {"shares": 0.0, "val": 0.0, "comm": 0.0, "symbol": ex.get("symbol", "")})
            prev["shares"] += shares
            prev["val"] += shares * px
            prev["comm"] += comm
        sym_k = (str(ex.get("symbol") or "").upper(), str(ex.get("side") or "").upper())
        if sym_k[0] and shares > 0:
            prev_s = exec_by_sym_side.setdefault(sym_k, {"shares": 0.0, "val": 0.0, "comm": 0.0})
            prev_s["shares"] += shares
            prev_s["val"] += shares * px
            prev_s["comm"] += comm

    active = (
        session.query(PaperTrade)
        .filter(
            PaperTrade.run_id == target_run.id,
            PaperTrade.status.in_(("PENDING_ENTRY", "OPEN", "PARTIAL_TP1")),
        )
        .order_by(PaperTrade.id.asc())
        .all()
    )

    filled_count = 0
    expired_count = 0
    closed_count = 0
    restored_sl_count = 0

    for trade in active:
        pos_info = positions_map.get(trade.symbol)
        pos_qty = float(pos_info["quantity"]) if pos_info else 0.0
        matches_dir_pos = (pos_qty > 0 and trade.direction == "LONG") or (pos_qty < 0 and trade.direction == "SHORT")

        if trade.status == "PENDING_ENTRY":
            p_oid = int(trade.ibkr_parent_order_id or 0)
            p_exec = exec_by_order.get(p_oid)
            if p_exec and p_exec["shares"] > 0:
                fill_px = round(p_exec["val"] / p_exec["shares"], 2)
                filled_shares = int(round(p_exec["shares"]))
                comm = round(p_exec["comm"], 2)
            elif matches_dir_pos:
                fill_px = round(float(pos_info["avg_cost"] or trade.planned_limit_price), 2)
                filled_shares = min(int(trade.quantity_planned), abs(int(round(pos_qty))))
                comm = 0.0
            else:
                fill_px = None
                filled_shares = 0
                comm = 0.0

            if fill_px is not None and filled_shares > 0:
                old_reserved = round(float(trade.planned_limit_price) * int(trade.quantity_planned), 2)
                actual_notional = round(fill_px * filled_shares, 2)
                target_run.cash_balance = round(float(target_run.cash_balance) + old_reserved - actual_notional - comm, 2)
                trade.status = "OPEN"
                trade.entry_fill_price = fill_px
                trade.last_mark_price = fill_px
                trade.quantity_filled = filled_shares
                trade.quantity_remaining = filled_shares
                trade.commission_usd = round(float(trade.commission_usd or 0.0) + comm, 2)
                trade.entry_slippage_bps = _compute_slippage_bps(trade.direction, trade.planned_limit_price, fill_px)
                trade.risk_amount_usd = round(abs(fill_px - float(trade.current_stop_loss)) * filled_shares, 2)
                trade.filled_at = cur_dt
                trade.last_evaluated_bar_ts = int(cur_dt.timestamp())
                trade.unrealized_pnl_usd = 0.0
                filled_count += 1
                _log_event(
                    session,
                    target_run.id,
                    trade.id,
                    trade.symbol,
                    "ORDER_FILLED",
                    f"[IBKR {du_acct}] Filled {trade.direction} {filled_shares} {trade.symbol} @ ${fill_px:.2f} (slip {trade.entry_slippage_bps:+.1f} bps)",
                    {"fill_price": fill_px, "quantity": filled_shares, "commission_usd": comm},
                    created_at=cur_dt,
                )
                dispatch_paper_notification(
                    runtime,
                    "orders",
                    f"Order Filled: {trade.direction} {trade.symbol}",
                    [
                        f"Account: {du_acct} · Qty: {filled_shares} @ ${fill_px:.2f}",
                        f"Stop: ${trade.current_stop_loss:.2f} · TP1: ${trade.planned_tp1:.2f} · Slippage: {trade.entry_slippage_bps:+.1f} bps",
                    ],
                )
            else:
                sub_dt = as_utc(trade.submitted_at or trade.created_at) or cur_dt
                still_working = p_oid in open_orders_by_id
                if not still_working and (cur_dt - sub_dt) > timedelta(minutes=5):
                    reserved = round(float(trade.planned_limit_price) * int(trade.quantity_planned), 2)
                    target_run.cash_balance = round(float(target_run.cash_balance) + reserved, 2)
                    trade.status = "CANCELLED"
                    trade.exit_reason = "ENTRY_EXPIRED"
                    trade.closed_at = cur_dt
                    expired_count += 1
                    _log_event(
                        session,
                        target_run.id,
                        trade.id,
                        trade.symbol,
                        "ORDER_CANCELLED",
                        f"[IBKR {du_acct}] Pending limit order #{p_oid} for {trade.symbol} expired/cancelled on IBKR.",
                        {"exit_reason": "ENTRY_EXPIRED"},
                        created_at=cur_dt,
                    )
            continue

        if trade.status in ("OPEN", "PARTIAL_TP1"):
            if not matches_dir_pos:
                sl_oid = int(trade.ibkr_stop_order_id or 0)
                tp_oid = int(trade.ibkr_tp1_order_id or 0)
                sl_exec = exec_by_order.get(sl_oid)
                tp_exec = exec_by_order.get(tp_oid)
                rev_side = "SLD" if trade.direction == "LONG" else "BOT"
                fallback_exec = exec_by_sym_side.get((trade.symbol, rev_side))

                if tp_exec and tp_exec["shares"] > 0:
                    exit_px = round(tp_exec["val"] / tp_exec["shares"], 2)
                    exit_comm = round(tp_exec["comm"], 2)
                    exit_reason = "TP1_HIT"
                elif sl_exec and sl_exec["shares"] > 0:
                    exit_px = round(sl_exec["val"] / sl_exec["shares"], 2)
                    exit_comm = round(sl_exec["comm"], 2)
                    exit_reason = (
                        "TRAILING_STOP"
                        if getattr(trade, "trailing_active", False)
                        else ("BREAK_EVEN" if trade.be_moved else "STOP_LOSS")
                    )
                elif fallback_exec and fallback_exec["shares"] > 0:
                    exit_px = round(fallback_exec["val"] / fallback_exec["shares"], 2)
                    exit_comm = round(fallback_exec["comm"], 2)
                    exit_reason = "EXTERNAL_SYNC"
                else:
                    exit_px = _latest_mark_price(session, trade.symbol) or float(
                        trade.last_mark_price or trade.entry_fill_price or trade.planned_limit_price
                    )
                    exit_comm = 0.0
                    exit_reason = "EXTERNAL_SYNC"

                if exit_comm:
                    trade.commission_usd = round(float(trade.commission_usd or 0.0) + exit_comm, 2)
                _close_trade(session, target_run, trade, exit_px, exit_reason, cur_dt)
                closed_count += 1
                dispatch_paper_notification(
                    runtime,
                    "orders",
                    f"Trade Closed: {trade.symbol} ({exit_reason})",
                    [
                        f"Account: {du_acct} · Exit: ${exit_px:.2f}",
                        f"Realized PnL: ${trade.realized_pnl_usd:+.2f} ({trade.r_multiple:+.2f}R)",
                    ],
                )
            else:
                # Ensure protective Stop Loss order still exists on IBKR!
                sl_oid = int(trade.ibkr_stop_order_id or 0)
                rev_action = "SELL" if trade.direction == "LONG" else "BUY"
                has_sl = (sl_oid in open_orders_by_id) or any(
                    o["symbol"] == trade.symbol and o["action"] == rev_action and o["order_type"] == "STP"
                    for o in snap.get("open_orders", [])
                )
                if not has_sl and snapshot_override is None and ibkr_cfg.get("enabled", False):
                    new_sl_oid = _restore_protective_stop(
                        symbol=trade.symbol,
                        direction=trade.direction,
                        quantity=int(trade.quantity_remaining or trade.quantity_filled or abs(int(round(pos_qty)))),
                        stop_loss=float(trade.current_stop_loss),
                        ibkr_cfg=ibkr_cfg,
                        expected_account=du_acct,
                    )
                    if new_sl_oid:
                        trade.ibkr_stop_order_id = new_sl_oid
                        restored_sl_count += 1
                        _log_event(
                            session,
                            target_run.id,
                            trade.id,
                            trade.symbol,
                            "SL_RESTORED",
                            f"[IBKR {du_acct}] Missing protective SL detected; restored STP order #{new_sl_oid} @ ${trade.current_stop_loss:.2f}",
                            {"stop_order_id": new_sl_oid},
                            created_at=cur_dt,
                        )
                        dispatch_paper_notification(
                            runtime,
                            "kill_switch",
                            f"Protective Stop Restored: {trade.symbol}",
                            [f"Re-submitted missing SL order #{new_sl_oid} @ ${trade.current_stop_loss:.2f} on {du_acct}."],
                        )

    recompute_run_equity(session, target_run, now_utc=cur_dt)
    session.commit()
    return {
        "run_id": target_run.id,
        "account": du_acct,
        "reconciled": True,
        "filled": filled_count,
        "expired": expired_count,
        "closed": closed_count,
        "restored_stops": restored_sl_count,
        "current_equity": target_run.current_equity,
    }


def step_ibkr_paper_lifecycle(
    session: Session,
    run: PaperAccountRun | None = None,
    runtime_settings: dict[str, Any] | None = None,
    *,
    now_utc: datetime | None = None,
    snapshot_override: dict[str, Any] | None = None,
    price_overrides: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Reconcile IBKR Paper fills/exits, update live marks, and manage BE / Trailing / Earnings / Time stops."""
    from app.core.paper_virtual import (
        _close_trade,
        _compute_current_r,
        _compute_trailing_stop_candidate,
        _compute_unrealized_pnl,
        _current_days_to_earnings,
        _latest_mark_price,
        _log_event,
        get_live_quotes_with_fallback,
        recompute_run_equity,
    )
    import numpy as np

    cur_dt = as_utc(now_utc) or utcnow()
    runtime = runtime_settings or load_runtime_settings(session)
    paper_cfg = runtime.get("paper_trading", {})
    ibkr_cfg = runtime.get("ibkr", {})
    target_run = run or ensure_active_paper_run(session, runtime, now_utc=cur_dt)

    rec = reconcile_ibkr_state(
        session,
        run=target_run,
        runtime_settings=runtime,
        now_utc=cur_dt,
        snapshot_override=snapshot_override,
    )
    if not rec.get("reconciled", False) and snapshot_override is None:
        return rec

    open_trades = (
        session.query(PaperTrade)
        .filter(PaperTrade.run_id == target_run.id, PaperTrade.status.in_(("OPEN", "PARTIAL_TP1")))
        .order_by(PaperTrade.id.asc())
        .all()
    )
    quotes_map: dict[str, dict[str, Any]] = {}
    if open_trades and price_overrides is None:
        syms = [t.symbol for t in open_trades]
        quotes_map = get_live_quotes_with_fallback(syms, ibkr_cfg)

    be_moved_count = 0
    trailing_moved_count = 0
    closed_lifecycle = 0
    was_halted = bool(target_run.kill_switch_triggered)

    for trade in open_trades:
        mark_px = _latest_mark_price(session, trade.symbol, price_overrides=price_overrides, quotes_map=quotes_map)
        if mark_px is None:
            mark_px = float(trade.last_mark_price or trade.entry_fill_price or trade.planned_limit_price)
        trade.last_mark_price = round(mark_px, 2)
        trade.unrealized_pnl_usd = _compute_unrealized_pnl(trade, mark_px)
        cur_r = _compute_current_r(trade, mark_px)

        # 1. Earnings Exit Guard (close when earnings are <= 1 day away)
        dte = _current_days_to_earnings(session, trade.symbol, cur_dt)
        if dte is not None and 0 <= dte <= 1:
            close_res = close_ibkr_trade(
                symbol=trade.symbol,
                direction=trade.direction,
                quantity=int(trade.quantity_remaining or trade.quantity_filled),
                parent_order_id=trade.ibkr_parent_order_id,
                stop_order_id=trade.ibkr_stop_order_id,
                tp1_order_id=trade.ibkr_tp1_order_id,
                is_pending=False,
                ibkr_cfg=ibkr_cfg,
                expected_account=target_run.ibkr_account,
            )
            exit_px = float(close_res.get("fill_price") or mark_px)
            _close_trade(session, target_run, trade, exit_px, "EARNINGS_EXIT", cur_dt)
            closed_lifecycle += 1
            dispatch_paper_notification(
                runtime,
                "lifecycle",
                f"Earnings Exit: {trade.symbol}",
                [f"Closed before earnings ({dte}d away) @ ${exit_px:.2f} | PnL ${trade.realized_pnl_usd:+.2f}"],
            )
            continue

        # 2. Time Stop (release stagnant position after N business days below +0.5R)
        filled_dt = as_utc(trade.filled_at or trade.created_at) or cur_dt
        time_stop_days = int(paper_cfg.get("time_stop_days", 15))
        if time_stop_days > 0:
            held_bdays = int(np.busday_count(filled_dt.date(), cur_dt.date()))
            if held_bdays >= time_stop_days and cur_r < 0.5:
                close_res = close_ibkr_trade(
                    symbol=trade.symbol,
                    direction=trade.direction,
                    quantity=int(trade.quantity_remaining or trade.quantity_filled),
                    parent_order_id=trade.ibkr_parent_order_id,
                    stop_order_id=trade.ibkr_stop_order_id,
                    tp1_order_id=trade.ibkr_tp1_order_id,
                    is_pending=False,
                    ibkr_cfg=ibkr_cfg,
                    expected_account=target_run.ibkr_account,
                )
                exit_px = float(close_res.get("fill_price") or mark_px)
                _close_trade(session, target_run, trade, exit_px, "TIME_STOP", cur_dt)
                closed_lifecycle += 1
                dispatch_paper_notification(
                    runtime,
                    "lifecycle",
                    f"Time Stop Exit: {trade.symbol}",
                    [f"Held {held_bdays} business days < +0.5R; closed @ ${exit_px:.2f} ({trade.r_multiple:+.2f}R)"],
                )
                continue

        # 3. Move SL to Break-Even at +move_to_be_at_r
        if not trade.be_moved and cur_r >= float(paper_cfg.get("move_to_be_at_r", 1.0)):
            entry_px = float(trade.entry_fill_price or trade.planned_limit_price)
            offset = max(0.1 * float(trade.atr_at_entry or 0.0), entry_px * 0.001)
            if trade.direction == "LONG":
                new_sl = round(min(mark_px - 0.02, max(trade.current_stop_loss, entry_px + offset)), 2)
                valid = new_sl > trade.current_stop_loss and new_sl < mark_px
            else:
                new_sl = round(max(mark_px + 0.02, min(trade.current_stop_loss, entry_px - offset)), 2)
                valid = new_sl < trade.current_stop_loss and new_sl > mark_px

            if valid and snapshot_override is None:
                ok, new_oid, msg = modify_ibkr_stop_loss(
                    symbol=trade.symbol,
                    direction=trade.direction,
                    quantity=int(trade.quantity_remaining or trade.quantity_filled),
                    stop_order_id=trade.ibkr_stop_order_id,
                    new_stop_price=new_sl,
                    ibkr_cfg=ibkr_cfg,
                    expected_account=target_run.ibkr_account,
                )
                if ok:
                    trade.current_stop_loss = new_sl
                    trade.be_moved = True
                    if new_oid:
                        trade.ibkr_stop_order_id = new_oid
                    be_moved_count += 1
                    _log_event(
                        session,
                        target_run.id,
                        trade.id,
                        trade.symbol,
                        "SL_MOVED_BE",
                        f"[IBKR] Moved {trade.symbol} SL to Break-Even @ ${new_sl:.2f} ({msg})",
                        {"new_stop_loss": new_sl},
                        created_at=cur_dt,
                    )
                    dispatch_paper_notification(
                        runtime,
                        "lifecycle",
                        f"Break-Even Stop: {trade.symbol}",
                        [f"Moved SL on {target_run.ibkr_account} to ${new_sl:.2f} ({cur_r:+.2f}R)"],
                    )

        # 4. Dynamic ATR Trailing Stop at +trailing_activation_r
        cand_trail = _compute_trailing_stop_candidate(trade, mark_px, paper_cfg)
        if cand_trail is not None and snapshot_override is None:
            ok, new_oid, msg = modify_ibkr_stop_loss(
                symbol=trade.symbol,
                direction=trade.direction,
                quantity=int(trade.quantity_remaining or trade.quantity_filled),
                stop_order_id=trade.ibkr_stop_order_id,
                new_stop_price=cand_trail,
                ibkr_cfg=ibkr_cfg,
                expected_account=target_run.ibkr_account,
            )
            if ok:
                trade.current_stop_loss = cand_trail
                trade.be_moved = True
                trade.trailing_active = True
                if new_oid:
                    trade.ibkr_stop_order_id = new_oid
                trailing_moved_count += 1
                _log_event(
                    session,
                    target_run.id,
                    trade.id,
                    trade.symbol,
                    "SL_TRAILING",
                    f"[IBKR] Trailed {trade.symbol} stop loss to ${cand_trail:.2f} ({cur_r:+.2f}R)",
                    {"new_stop_loss": cand_trail, "cur_r": round(cur_r, 2)},
                    created_at=cur_dt,
                )
                dispatch_paper_notification(
                    runtime,
                    "lifecycle",
                    f"Trailing Stop Raised: {trade.symbol}",
                    [f"Updated SL on {target_run.ibkr_account} to ${cand_trail:.2f} ({cur_r:+.2f}R)"],
                )

    recompute_run_equity(session, target_run, now_utc=cur_dt)
    halted, halt_reason = check_kill_switch(session, target_run, paper_cfg, now_utc=cur_dt)
    session.commit()
    if halted and not was_halted and target_run.kill_switch_triggered:
        dispatch_paper_notification(
            runtime,
            "kill_switch",
            "Kill-Switch Triggered",
            [f"Run: {target_run.name}", f"Reason: {halt_reason}"],
        )

    return {
        **rec,
        "closed": int(rec.get("closed", 0)) + closed_lifecycle,
        "be_moved": be_moved_count,
        "trailing_moved": trailing_moved_count,
        "current_equity": target_run.current_equity,
    }
