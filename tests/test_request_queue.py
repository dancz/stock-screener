from __future__ import annotations

import threading
from pathlib import Path

import numpy as np
import pandas as pd

from app.core import pipeline
from app.core.request_queue import IbkrSessionWorker, Priority, PriorityRequestQueue, SlidingWindowLimiter
from app.db.database import configure_database, session_scope
from app.db.models import init_db


def _make_bars(count: int, *, end_ts: int, step_sec: int = 86400, price: float = 100.0) -> pd.DataFrame:
    start_ts = end_ts - (count - 1) * step_sec
    ts = np.array([start_ts + i * step_sec for i in range(count)], dtype=int)
    closes = np.full(count, price, dtype=float)
    return pd.DataFrame(
        {
            "ts": ts,
            "open": closes * 0.995,
            "high": closes * 1.01,
            "low": closes * 0.99,
            "close": closes,
            "volume": np.full(count, 1_000_000.0, dtype=float),
        }
    )


def test_sliding_window_limiter_enforces_min_interval_and_window_cap():
    limiter = SlidingWindowLimiter(max_calls=2, window_seconds=0.35, min_interval=0.08)

    assert limiter.acquire(max_wait=0.5) is True
    # Blocked by min_interval=0.08s when max_wait=0.01s
    assert limiter.acquire(max_wait=0.01) is False
    # Allowed once min_interval passes
    assert limiter.acquire(max_wait=0.5) is True
    # Blocked by 2/2 window cap when max_wait is tiny
    assert limiter.acquire(max_wait=0.03) is False
    # Succeeds once the sliding window advances
    assert limiter.acquire(max_wait=0.8) is True

    stats = limiter.stats()
    assert stats["max_calls"] == 2
    assert stats["waits"] >= 2


def test_priority_queue_orders_high_before_low_and_deduplicates():
    q = PriorityRequestQueue(
        "test-prio",
        SlidingWindowLimiter(max_calls=100, window_seconds=10.0, min_interval=0.0),
    )
    started = threading.Event()
    gate = threading.Event()
    order: list[str] = []
    dedup_calls: list[int] = []

    try:
        # Hold worker while enqueuing mixed priorities + duplicate dedup_key
        blocker = q.submit(lambda: (started.set(), gate.wait(2.0)), priority=Priority.HIGH)
        assert started.wait(1.5)

        f_low = q.submit(lambda: order.append("LOW"), priority=Priority.LOW)
        f_norm_a = q.submit(
            lambda: (dedup_calls.append(1), order.append("NORM"), "dup-ok")[-1],
            priority=Priority.NORMAL,
            dedup_key="same-op",
        )
        f_norm_b = q.submit(
            lambda: (dedup_calls.append(2), order.append("NORM2"), "should-not-run")[-1],
            priority=Priority.NORMAL,
            dedup_key="same-op",
        )
        f_high = q.submit(lambda: order.append("HIGH"), priority=Priority.HIGH)

        assert f_norm_a is f_norm_b
        gate.set()

        blocker.result(timeout=2.0)
        f_high.result(timeout=2.0)
        assert f_norm_a.result(timeout=2.0) == "dup-ok"
        assert f_norm_b.result(timeout=2.0) == "dup-ok"
        f_low.result(timeout=2.0)

        assert order == ["HIGH", "NORM", "LOW"]
        assert dedup_calls == [1]
        assert q.stats()["dedup_hits"] == 1
        assert q.stats()["completed"] == 4
    finally:
        q.shutdown()


def test_fetch_daily_incremental_ibkr_primary_and_fallback(tmp_path: Path, monkeypatch):
    db_file = tmp_path / "ibkr_routing.db"
    configure_database(db_file)
    init_db()

    now_ts = 1_800_000_000
    seed_spy = _make_bars(250, end_ts=now_ts - 86400, price=500.0)
    seed_qqq = _make_bars(250, end_ts=now_ts - 86400, price=450.0)
    seed_aapl = _make_bars(250, end_ts=now_ts - 86400, price=200.0)
    seed_msft = _make_bars(250, end_ts=now_ts - 86400, price=400.0)

    with session_scope() as session:
        for sym, df in [("SPY", seed_spy), ("QQQ", seed_qqq), ("AAPL", seed_aapl), ("MSFT", seed_msft)]:
            pipeline._ensure_ticker_row(session, sym, name=sym)
            pipeline.upsert_candles(session, sym, "1d", df)
        session.commit()

        ibkr_cfg = {"host": "127.0.0.1", "port": 4002, "client_id": 17}

        # 1) prefer_ibkr=True: IBKR serves SPY, returns None for QQQ -> Yahoo only called for QQQ
        yahoo_calls_1: list[tuple[list[str], str]] = []

        def fake_ibkr_primary(symbol, timeframe, duration, host, port, client_id, **kwargs):
            if symbol == "SPY":
                return _make_bars(3, end_ts=now_ts, price=500.0)
            return None

        def fake_yahoo_primary(symbols, period="1y", chunk_size=15, **kwargs):
            yahoo_calls_1.append((list(symbols), period))
            return {sym: _make_bars(3, end_ts=now_ts, price=450.0) for sym in symbols}

        monkeypatch.setattr(pipeline, "fetch_historical_bars_ibkr", fake_ibkr_primary)
        monkeypatch.setattr(pipeline, "fetch_daily_batch", fake_yahoo_primary)

        frames_1, stats_1 = pipeline.fetch_daily_incremental(
            session,
            ["SPY", "QQQ"],
            full_period="5y",
            min_bars_for_delta=220,
            now_ts=now_ts,
            ibkr_cfg=ibkr_cfg,
            prefer_ibkr=True,
            ibkr_fallback=True,
        )
        assert yahoo_calls_1 == [(["QQQ"], "5d")]
        assert len(frames_1["SPY"]) == 251
        assert len(frames_1["QQQ"]) == 251
        assert stats_1["ibkr_fetched"] == 1

        # 2) prefer_ibkr=False, ibkr_fallback=True: Yahoo fails MSFT -> IBKR rescues MSFT
        ibkr_fb_calls: list[str] = []

        def fake_yahoo_partial(symbols, period="1y", chunk_size=15, **kwargs):
            return {s: _make_bars(3, end_ts=now_ts, price=200.0) for s in symbols if s == "AAPL"}

        def fake_ibkr_fb(symbol, timeframe, duration, host, port, client_id, **kwargs):
            ibkr_fb_calls.append(symbol)
            if symbol == "MSFT":
                return _make_bars(3, end_ts=now_ts, price=400.0)
            return None

        monkeypatch.setattr(pipeline, "fetch_daily_batch", fake_yahoo_partial)
        monkeypatch.setattr(pipeline, "fetch_historical_bars_ibkr", fake_ibkr_fb)

        frames_2, stats_2 = pipeline.fetch_daily_incremental(
            session,
            ["AAPL", "MSFT"],
            full_period="5y",
            min_bars_for_delta=220,
            now_ts=now_ts,
            ibkr_cfg=ibkr_cfg,
            prefer_ibkr=False,
            ibkr_fallback=True,
        )
        assert ibkr_fb_calls == ["MSFT"]
        assert len(frames_2["AAPL"]) == 251
        assert len(frames_2["MSFT"]) == 251
        assert stats_2["ibkr_fetched"] == 1
        assert stats_2["cache_fallback"] == 0


def test_queue_stats_exposed_in_scan_status():
    status = pipeline.get_scan_status()
    assert "queues" in status
    queues = status["queues"]
    assert "yahoo" in queues and "ibkr" in queues
    assert "limiter" in queues["yahoo"]
    assert "hmds_limiter" in queues["ibkr"]
    assert "live_limiter" in queues["ibkr"]
    assert "hmds_circuit_open" in queues["ibkr"]


def test_ibkr_hmds_circuit_breaker_opens_and_skips_fast():
    import time

    worker = IbkrSessionWorker(hmds_cooldown_s=60.0)
    try:
        assert worker.is_hmds_circuit_open() is False
        worker.handle_ib_error(-1, 2105, "HMDS data farm connection is broken:ushmds")
        assert worker.stats()["hmds_farm_broken"] is True

        # Single HMDS failure when ushmds is broken trips the circuit breaker immediately
        worker.record_hmds_outcome(False)
        assert worker.is_hmds_circuit_open() is True

        async def _never_runs(_ib):  # pragma: no cover
            raise AssertionError("Should skip without connecting while circuit is open")

        t0 = time.monotonic()
        res = worker.run(
            _never_runs,
            host="127.0.0.1",
            port=4002,
            client_id=17,
            is_hmds=True,
            dedup_key="cb-test",
        )
        elapsed = time.monotonic() - t0
        assert res is None
        assert elapsed < 0.5
        st = worker.stats()
        assert st["hmds_circuit_skips"] == 1
        assert st["hmds_limiter"]["used_in_window"] == 0

        # Gateway recovery (2106) resets circuit cleanly
        worker.handle_ib_error(-1, 2106, "HMDS data farm connection is OK:ushmds")
        assert worker.is_hmds_circuit_open() is False
        assert worker.stats()["hmds_farm_broken"] is False
    finally:
        worker.shutdown()
