from __future__ import annotations

import asyncio
import itertools
import logging
import queue
import threading
import time
from collections import deque
from concurrent.futures import Future
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)


class Priority(IntEnum):
    """Queue priority levels: lower value executes first."""

    HIGH = 10  # UI live quotes, on-demand refresh, benchmarks
    NORMAL = 20  # Active scan Stage 1 / Stage 2, watchlists
    LOW = 30  # Background history backfill


class SlidingWindowLimiter:
    """Thread-safe rate limiter enforcing min interval and max calls per sliding window."""

    def __init__(self, max_calls: int, window_seconds: float, min_interval: float = 0.0) -> None:
        self.max_calls = int(max_calls)
        self.window_seconds = float(window_seconds)
        self.min_interval = float(min_interval)
        self._lock = threading.Lock()
        self._timestamps: deque[float] = deque()
        self._last_ts: float | None = None
        self._waits = 0

    def acquire(self, max_wait: float | None = None) -> bool:
        """Block until a request slot is available, or return False if wait exceeds max_wait."""
        deadline = (time.monotonic() + float(max_wait)) if max_wait is not None else None
        while True:
            with self._lock:
                now = time.monotonic()
                while self._timestamps and (now - self._timestamps[0]) >= self.window_seconds:
                    self._timestamps.popleft()

                wait_interval = 0.0
                if self._last_ts is not None and self.min_interval > 0:
                    wait_interval = max(0.0, (self._last_ts + self.min_interval) - now)

                wait_window = 0.0
                if self.max_calls > 0 and len(self._timestamps) >= self.max_calls:
                    wait_window = max(0.0, (self._timestamps[0] + self.window_seconds) - now)

                wait_s = max(wait_interval, wait_window)
                if wait_s <= 0.0:
                    self._timestamps.append(now)
                    self._last_ts = now
                    return True

                if deadline is not None:
                    remaining = deadline - now
                    if remaining <= 0.0 or wait_s > remaining:
                        return False

                self._waits += 1

            sleep_s = min(wait_s, 0.25)
            time.sleep(sleep_s)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            while self._timestamps and (now - self._timestamps[0]) >= self.window_seconds:
                self._timestamps.popleft()
            return {
                "used_in_window": len(self._timestamps),
                "max_calls": self.max_calls,
                "window_seconds": self.window_seconds,
                "min_interval": self.min_interval,
                "waits": self._waits,
            }


@dataclass
class _QueueTask:
    fn: Callable[[], Any]
    future: Future[Any]
    dedup_key: str | None
    max_wait: float | None


class PriorityRequestQueue:
    """Single-worker prioritized request queue with rate limiting and in-flight deduplication."""

    def __init__(self, name: str, limiter: SlidingWindowLimiter) -> None:
        self.name = name
        self.limiter = limiter
        self._pq: queue.PriorityQueue[tuple[int, int, _QueueTask | None]] = queue.PriorityQueue()
        self._seq = itertools.count()
        self._lock = threading.Lock()
        self._inflight: dict[str, Future[Any]] = {}
        self._worker: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._in_flight_count = 0
        self._completed = 0
        self._dedup_hits = 0
        self._errors = 0

    def _ensure_worker(self) -> None:
        if self._worker is None or not self._worker.is_alive():
            self._stop_event.clear()
            self._worker = threading.Thread(
                target=self._run_loop,
                name=f"req-queue-{self.name}",
                daemon=True,
            )
            self._worker.start()

    def submit(
        self,
        fn: Callable[[], Any],
        *,
        priority: int = Priority.NORMAL,
        dedup_key: str | None = None,
        max_wait: float | None = None,
    ) -> Future[Any]:
        with self._lock:
            if dedup_key and dedup_key in self._inflight:
                self._dedup_hits += 1
                return self._inflight[dedup_key]

            fut: Future[Any] = Future()
            if dedup_key:
                self._inflight[dedup_key] = fut

            self._ensure_worker()
            task = _QueueTask(fn=fn, future=fut, dedup_key=dedup_key, max_wait=max_wait)
            self._pq.put((int(priority), next(self._seq), task))
            return fut

    def call(
        self,
        fn: Callable[[], Any],
        *,
        priority: int = Priority.NORMAL,
        dedup_key: str | None = None,
        timeout: float = 90.0,
        max_wait: float | None = None,
    ) -> Any:
        fut = self.submit(fn, priority=priority, dedup_key=dedup_key, max_wait=max_wait)
        return fut.result(timeout=timeout)

    def _cleanup_inflight(self, dedup_key: str | None, fut: Future[Any]) -> None:
        if dedup_key:
            with self._lock:
                if self._inflight.get(dedup_key) is fut:
                    self._inflight.pop(dedup_key, None)

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                _prio, _seq, task = self._pq.get(timeout=2.0)
            except queue.Empty:
                continue

            if task is None:
                break

            with self._lock:
                self._in_flight_count += 1

            try:
                allowed = self.limiter.acquire(max_wait=task.max_wait)
                if not allowed:
                    self._cleanup_inflight(task.dedup_key, task.future)
                    if not task.future.done():
                        task.future.set_exception(TimeoutError(f"{self.name} queue rate-limit wait exceeded"))
                    continue

                result = task.fn()
                self._cleanup_inflight(task.dedup_key, task.future)
                with self._lock:
                    self._completed += 1
                if not task.future.done():
                    task.future.set_result(result)
            except Exception as exc:
                self._cleanup_inflight(task.dedup_key, task.future)
                with self._lock:
                    self._errors += 1
                if not task.future.done():
                    task.future.set_exception(exc)
            finally:
                with self._lock:
                    self._in_flight_count = max(0, self._in_flight_count - 1)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "pending": self._pq.qsize(),
                "in_flight": self._in_flight_count,
                "completed": self._completed,
                "dedup_hits": self._dedup_hits,
                "errors": self._errors,
                "limiter": self.limiter.stats(),
            }

    def shutdown(self) -> None:
        with self._lock:
            worker = self._worker
            if worker is None:
                return
            self._stop_event.set()
            self._pq.put((int(Priority.LOW) + 100, next(self._seq), None))
        if worker.is_alive():
            worker.join(timeout=2.0)
        with self._lock:
            self._worker = None


@dataclass
class _IbkrJob:
    host: str
    port: int
    client_id: int
    is_hmds: bool
    coro_factory: Callable[[Any], Coroutine[Any, Any, Any]]
    future: Future[Any]
    dedup_key: str | None
    timeout: float
    max_wait: float | None


class IbkrSessionWorker:
    """Dedicated lazy daemon thread owning a single shared read-only IBKR Gateway session."""

    def __init__(
        self,
        *,
        client_id_offset: int = 5,
        idle_timeout_s: float = 120.0,
        hmds_cooldown_s: float = 300.0,
    ) -> None:
        self.client_id_offset = int(client_id_offset)
        self.idle_timeout_s = float(idle_timeout_s)
        self.hmds_cooldown_s = float(hmds_cooldown_s)
        self.hmds_limiter = SlidingWindowLimiter(max_calls=50, window_seconds=600.0, min_interval=1.2)
        self.live_limiter = SlidingWindowLimiter(max_calls=120, window_seconds=60.0, min_interval=0.15)
        self._pq: queue.PriorityQueue[tuple[int, int, _IbkrJob | None]] = queue.PriorityQueue()
        self._seq = itertools.count()
        self._lock = threading.Lock()
        self._inflight: dict[str, Future[Any]] = {}
        self._worker: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._connected = False
        self._in_flight_count = 0
        self._completed = 0
        self._dedup_hits = 0
        self._errors = 0
        self._hmds_budget_skips = 0
        self._hmds_consecutive_failures = 0
        self._hmds_cooldown_until = 0.0
        self._hmds_circuit_skips = 0
        self._hmds_farm_broken = False

    def is_hmds_circuit_open(self) -> bool:
        with self._lock:
            return time.monotonic() < self._hmds_cooldown_until

    def record_hmds_outcome(self, success: bool) -> None:
        with self._lock:
            if success:
                self._hmds_consecutive_failures = 0
                self._hmds_farm_broken = False
                self._hmds_cooldown_until = 0.0
                return
            self._hmds_consecutive_failures += 1
            threshold = 1 if self._hmds_farm_broken else 2
            if self._hmds_consecutive_failures >= threshold:
                self._hmds_cooldown_until = time.monotonic() + self.hmds_cooldown_s
                logger.warning(
                    "IBKR HMDS circuit breaker opened for %.0fs (failures=%d, farm_broken=%s); falling back to Yahoo/SQLite for historical bars.",
                    self.hmds_cooldown_s,
                    self._hmds_consecutive_failures,
                    self._hmds_farm_broken,
                )

    def handle_ib_error(
        self,
        req_id: int,
        error_code: int,
        error_string: str,
        contract: Any = None,
    ) -> None:
        msg = (error_string or "").lower()
        with self._lock:
            if error_code == 2105 and "ushmds" in msg:
                self._hmds_farm_broken = True
            elif error_code == 2106 and "ushmds" in msg:
                self._hmds_farm_broken = False
                self._hmds_consecutive_failures = 0
                self._hmds_cooldown_until = 0.0

    def _ensure_worker(self) -> None:
        if self._worker is None or not self._worker.is_alive():
            self._stop_event.clear()
            self._worker = threading.Thread(
                target=self._run_loop,
                name="ibkr-session-worker",
                daemon=True,
            )
            self._worker.start()

    def submit(
        self,
        coro_factory: Callable[[Any], Coroutine[Any, Any, Any]],
        *,
        host: str,
        port: int,
        client_id: int,
        priority: int = Priority.NORMAL,
        is_hmds: bool = False,
        dedup_key: str | None = None,
        timeout: float = 25.0,
        max_wait: float | None = 6.0,
    ) -> Future[Any]:
        with self._lock:
            if dedup_key and dedup_key in self._inflight:
                self._dedup_hits += 1
                return self._inflight[dedup_key]

            fut: Future[Any] = Future()
            if dedup_key:
                self._inflight[dedup_key] = fut

            self._ensure_worker()
            job = _IbkrJob(
                host=str(host),
                port=int(port),
                client_id=int(client_id),
                is_hmds=bool(is_hmds),
                coro_factory=coro_factory,
                future=fut,
                dedup_key=dedup_key,
                timeout=float(timeout),
                max_wait=max_wait,
            )
            self._pq.put((int(priority), next(self._seq), job))
            return fut

    def run(
        self,
        coro_factory: Callable[[Any], Coroutine[Any, Any, Any]],
        *,
        host: str,
        port: int,
        client_id: int,
        priority: int = Priority.NORMAL,
        is_hmds: bool = False,
        dedup_key: str | None = None,
        timeout: float = 25.0,
        max_wait: float | None = 6.0,
    ) -> Any:
        fut = self.submit(
            coro_factory,
            host=host,
            port=port,
            client_id=client_id,
            priority=priority,
            is_hmds=is_hmds,
            dedup_key=dedup_key,
            timeout=timeout,
            max_wait=max_wait,
        )
        try:
            return fut.result(timeout=float(timeout) + float(max_wait or 0.0) + 5.0)
        except Exception as exc:
            logger.debug("IBKR queued operation fallback (%s): %s", dedup_key or "unnamed", exc)
            return None

    def _cleanup_inflight(self, dedup_key: str | None, fut: Future[Any]) -> None:
        if dedup_key:
            with self._lock:
                if self._inflight.get(dedup_key) is fut:
                    self._inflight.pop(dedup_key, None)

    def _run_loop(self) -> None:
        from app.plugins.sources.market_data_ibkr import is_gateway_reachable

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        ib: Any = None
        active_endpoint: tuple[str, int, int] | None = None
        last_used_mono = 0.0

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
            with self._lock:
                self._connected = False

        try:
            while not self._stop_event.is_set():
                try:
                    _prio, _seq, job = self._pq.get(timeout=5.0)
                except queue.Empty:
                    if ib is not None and (time.monotonic() - last_used_mono) >= self.idle_timeout_s:
                        logger.info("Disconnecting idle shared IBKR Gateway session after %.0fs.", self.idle_timeout_s)
                        _disconnect_ib()
                    continue

                if job is None:
                    break

                with self._lock:
                    self._in_flight_count += 1

                try:
                    if job.is_hmds and self.is_hmds_circuit_open():
                        with self._lock:
                            self._hmds_circuit_skips += 1
                        self._cleanup_inflight(job.dedup_key, job.future)
                        if not job.future.done():
                            job.future.set_result(None)
                        continue

                    limiter = self.hmds_limiter if job.is_hmds else self.live_limiter
                    if not limiter.acquire(max_wait=job.max_wait):
                        if job.is_hmds:
                            with self._lock:
                                self._hmds_budget_skips += 1
                        self._cleanup_inflight(job.dedup_key, job.future)
                        if not job.future.done():
                            job.future.set_result(None)
                        continue

                    target_endpoint = (job.host, job.port, job.client_id + self.client_id_offset)
                    if ib is not None and (not ib.isConnected() or active_endpoint != target_endpoint):
                        _disconnect_ib()

                    if ib is None:
                        if not is_gateway_reachable(job.host, job.port, timeout=0.25):
                            self._cleanup_inflight(job.dedup_key, job.future)
                            if not job.future.done():
                                job.future.set_result(None)
                            continue

                        from ib_async import IB

                        candidate_ib = IB()
                        candidate_ib.errorEvent += self.handle_ib_error
                        try:
                            loop.run_until_complete(
                                candidate_ib.connectAsync(
                                    target_endpoint[0],
                                    target_endpoint[1],
                                    clientId=target_endpoint[2],
                                    timeout=3.0,
                                    readonly=True,
                                )
                            )
                            candidate_ib.reqMarketDataType(3)
                            ib = candidate_ib
                            active_endpoint = target_endpoint
                            with self._lock:
                                self._connected = True
                        except Exception as exc:
                            try:
                                if candidate_ib.isConnected():
                                    candidate_ib.disconnect()
                            except Exception:
                                pass
                            logger.debug("Shared IBKR session connect failed (%s): %s", target_endpoint, exc)
                            self._cleanup_inflight(job.dedup_key, job.future)
                            with self._lock:
                                self._errors += 1
                            if not job.future.done():
                                job.future.set_result(None)
                            continue

                    async def _exec() -> Any:
                        return await asyncio.wait_for(job.coro_factory(ib), timeout=job.timeout)

                    res = loop.run_until_complete(_exec())
                    if job.is_hmds:
                        ok = res is not None and (not hasattr(res, "empty") or not res.empty)
                        self.record_hmds_outcome(bool(ok))
                    last_used_mono = time.monotonic()
                    self._cleanup_inflight(job.dedup_key, job.future)
                    with self._lock:
                        self._completed += 1
                    if not job.future.done():
                        job.future.set_result(res)
                except Exception as exc:
                    if job.is_hmds:
                        self.record_hmds_outcome(False)
                    if isinstance(exc, (ConnectionError, OSError)) or (ib is not None and not ib.isConnected()):
                        _disconnect_ib()
                    self._cleanup_inflight(job.dedup_key, job.future)
                    with self._lock:
                        self._errors += 1
                    if not job.future.done():
                        job.future.set_exception(exc)
                finally:
                    with self._lock:
                        self._in_flight_count = max(0, self._in_flight_count - 1)
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
                "pending": self._pq.qsize(),
                "in_flight": self._in_flight_count,
                "completed": self._completed,
                "dedup_hits": self._dedup_hits,
                "errors": self._errors,
                "hmds_budget_skips": self._hmds_budget_skips,
                "hmds_circuit_open": time.monotonic() < self._hmds_cooldown_until,
                "hmds_farm_broken": self._hmds_farm_broken,
                "hmds_circuit_skips": self._hmds_circuit_skips,
                "hmds_limiter": self.hmds_limiter.stats(),
                "live_limiter": self.live_limiter.stats(),
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


yahoo_queue = PriorityRequestQueue(
    "yahoo",
    SlidingWindowLimiter(max_calls=45, window_seconds=60.0, min_interval=0.45),
)
ibkr_worker = IbkrSessionWorker()


def get_queue_stats() -> dict[str, Any]:
    return {
        "yahoo": yahoo_queue.stats(),
        "ibkr": ibkr_worker.stats(),
    }


def shutdown_queues() -> None:
    yahoo_queue.shutdown()
    ibkr_worker.shutdown()
