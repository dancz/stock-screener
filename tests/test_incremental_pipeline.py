from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from app.core import pipeline
from app.db.database import configure_database, session_scope
from app.db.models import AnalysisSnapshot, Candle, ScanRun, Ticker, init_db


def _make_bars(
    count: int,
    *,
    end_ts: int,
    step_sec: int,
    price: float = 100.0,
) -> pd.DataFrame:
    start_ts = end_ts - (count - 1) * step_sec
    ts = np.array([start_ts + i * step_sec for i in range(count)], dtype=int)
    closes = np.full(count, price, dtype=float)
    opens = closes * 0.995
    highs = closes * 1.01
    lows = closes * 0.99
    vols = np.full(count, 1_000_000.0, dtype=float)
    return pd.DataFrame(
        {
            "ts": ts,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": vols,
        }
    )


def test_fetch_daily_incremental_buckets_and_preserves_history(tmp_path: Path, monkeypatch):
    db_file = tmp_path / "inc_buckets.db"
    configure_database(db_file)
    init_db()

    now_ts = 1_800_000_000
    aapl_seed = _make_bars(250, end_ts=now_ts - 86400, step_sec=86400, price=190.0)
    msft_seed = _make_bars(250, end_ts=now_ts - 10 * 86400, step_sec=86400, price=420.0)
    nvda_seed = _make_bars(20, end_ts=now_ts - 86400, step_sec=86400, price=130.0)

    with session_scope() as session:
        for sym, seed_df in [("AAPL", aapl_seed), ("MSFT", msft_seed), ("NVDA", nvda_seed)]:
            pipeline._ensure_ticker_row(session, sym, name=sym)
            pipeline.upsert_candles(session, sym, "1d", seed_df)
        session.commit()

        calls: list[tuple[list[str], str]] = []

        def fake_fetch_daily_batch(symbols: list[str], period: str = "1y", chunk_size: int = 60):
            calls.append((list(symbols), period))
            out: dict[str, pd.DataFrame] = {}
            for sym in symbols:
                if sym == "AAPL" and period == "5d":
                    # Last 2 overlapping historical bars + 1 brand-new bar at now_ts
                    out["AAPL"] = _make_bars(3, end_ts=now_ts, step_sec=86400, price=190.0)
                elif sym == "MSFT" and period == "1mo":
                    out["MSFT"] = _make_bars(12, end_ts=now_ts, step_sec=86400, price=420.0)
                elif sym == "NVDA" and period == "5y":
                    out["NVDA"] = _make_bars(260, end_ts=now_ts, step_sec=86400, price=135.0)
            return out

        monkeypatch.setattr(pipeline, "fetch_daily_batch", fake_fetch_daily_batch)
        frames, stats = pipeline.fetch_daily_incremental(
            session,
            ["AAPL", "MSFT", "NVDA"],
            full_period="5y",
            min_bars_for_delta=220,
            now_ts=now_ts,
        )
        session.commit()

        assert calls == [
            (["NVDA"], "5y"),
            (["AAPL"], "5d"),
            (["MSFT"], "1mo"),
        ]
        assert stats == {
            "delta_5d": 1,
            "delta_1mo": 1,
            "full": 1,
            "ibkr_fetched": 0,
            "resynced": 0,
            "cache_fallback": 0,
        }
        assert len(frames["AAPL"]) == 251
        assert int(frames["AAPL"]["ts"].iloc[-1]) == now_ts
        assert len(frames["MSFT"]) == 260
        assert int(frames["MSFT"]["ts"].iloc[-1]) == now_ts
        assert len(frames["NVDA"]) == 260

        cov = pipeline.get_candle_coverage(session, ["AAPL", "MSFT", "NVDA"], "1d")
        assert cov["AAPL"][0] == 251
        assert cov["MSFT"][0] == 260
        assert cov["NVDA"][0] == 260


def test_fetch_daily_incremental_detects_split_and_resyncs(tmp_path: Path, monkeypatch):
    db_file = tmp_path / "inc_split.db"
    configure_database(db_file)
    init_db()

    now_ts = 1_800_000_000
    pre_split = _make_bars(250, end_ts=now_ts - 86400, step_sec=86400, price=200.0)

    with session_scope() as session:
        pipeline._ensure_ticker_row(session, "SPLITCO", name="Split Co")
        pipeline.upsert_candles(session, "SPLITCO", "1d", pre_split)
        session.commit()

        calls: list[tuple[list[str], str]] = []

        def fake_fetch_daily_batch(symbols: list[str], period: str = "1y", chunk_size: int = 60):
            calls.append((list(symbols), period))
            if period == "5d":
                # Overlapping bars show 2:1 split adjustment (200 -> 100)
                return {"SPLITCO": _make_bars(5, end_ts=now_ts, step_sec=86400, price=100.0)}
            if period == "5y":
                return {"SPLITCO": _make_bars(300, end_ts=now_ts, step_sec=86400, price=100.0)}
            return {}

        monkeypatch.setattr(pipeline, "fetch_daily_batch", fake_fetch_daily_batch)
        frames, stats = pipeline.fetch_daily_incremental(
            session,
            ["SPLITCO"],
            full_period="5y",
            min_bars_for_delta=220,
            now_ts=now_ts,
        )
        session.commit()

        assert calls == [(["SPLITCO"], "5d"), (["SPLITCO"], "5y")]
        assert stats["resynced"] == 1
        df_res = frames["SPLITCO"]
        assert len(df_res) == 300
        assert float(df_res["close"].max()) == 100.0
        assert float(df_res["close"].min()) == 100.0

        db_df = pipeline.load_candles_df(session, "SPLITCO", "1d")
        assert len(db_df) == 300
        assert float(db_df["close"].max()) == 100.0


def test_fetch_daily_incremental_falls_back_to_db_on_empty_fetch(tmp_path: Path, monkeypatch):
    db_file = tmp_path / "inc_fallback.db"
    configure_database(db_file)
    init_db()

    now_ts = 1_800_000_000
    seed_df = _make_bars(250, end_ts=now_ts - 86400, step_sec=86400, price=75.0)

    with session_scope() as session:
        pipeline._ensure_ticker_row(session, "FALLBACK", name="Fallback Corp")
        pipeline.upsert_candles(session, "FALLBACK", "1d", seed_df)
        session.commit()

        monkeypatch.setattr(pipeline, "fetch_daily_batch", lambda *args, **kwargs: {})
        frames, stats = pipeline.fetch_daily_incremental(
            session,
            ["FALLBACK"],
            full_period="5y",
            min_bars_for_delta=220,
            now_ts=now_ts,
        )

    assert len(frames["FALLBACK"]) == 250
    assert stats["delta_5d"] == 1
    assert stats["cache_fallback"] == 1


def test_fetch_intraday_1h_incremental(tmp_path: Path, monkeypatch):
    db_file = tmp_path / "inc_1h.db"
    configure_database(db_file)
    init_db()

    now_ts = 1_800_000_000
    aapl_1h = _make_bars(100, end_ts=now_ts - 3600, step_sec=3600, price=210.0)

    with session_scope() as session:
        pipeline._ensure_ticker_row(session, "AAPL", name="Apple")
        pipeline.upsert_candles(session, "AAPL", "1h", aapl_1h)
        session.commit()

        calls: list[tuple[str, str]] = []

        def fake_fetch_1h(symbol: str, period: str = "120d"):
            calls.append((symbol, period))
            if symbol == "AAPL" and period == "5d":
                return _make_bars(4, end_ts=now_ts, step_sec=3600, price=210.0)
            if symbol == "NEWCO" and period == "365d":
                return _make_bars(120, end_ts=now_ts, step_sec=3600, price=55.0)
            return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])

        monkeypatch.setattr(pipeline, "fetch_intraday_1h", fake_fetch_1h)

        df_aapl, mode_aapl = pipeline.fetch_intraday_1h_incremental(
            session, "AAPL", full_period="365d", min_bars_for_delta=80, now_ts=now_ts
        )
        df_newco, mode_newco = pipeline.fetch_intraday_1h_incremental(
            session,
            "NEWCO",
            full_period="365d",
            min_bars_for_delta=80,
            now_ts=now_ts,
            ensure_ticker_fn=lambda s: pipeline._ensure_ticker_row(session, s, is_custom=True),
        )
        assert calls == [("AAPL", "5d"), ("NEWCO", "365d")]
        assert mode_aapl == "5d"
        assert len(df_aapl) == 101
        assert int(df_aapl["ts"].iloc[-1]) == now_ts
        assert mode_newco == "365d"
        assert len(df_newco) == 120

        # Empty fetch fallback returns stored DB 1H bars
        monkeypatch.setattr(
            pipeline,
            "fetch_intraday_1h",
            lambda *args, **kwargs: pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"]),
        )
        df_fb, mode_fb = pipeline.fetch_intraday_1h_incremental(
            session, "AAPL", full_period="365d", min_bars_for_delta=80, now_ts=now_ts
        )
        assert mode_fb == "cache_fallback"
        assert len(df_fb) == 101


def test_cached_fundamentals_ttl_and_earnings_recompute(tmp_path: Path):
    db_file = tmp_path / "inc_fund.db"
    configure_database(db_file)
    init_db()

    now_dt = datetime(2026, 11, 24, 20, 0, tzinfo=timezone.utc)

    with session_scope() as session:
        run = ScanRun(mode="quick", status="completed", stage="completed")
        session.add(run)
        session.flush()

        def _seed_case(
            sym: str,
            enriched_at: datetime | None,
            fundamentals: dict,
            next_earnings_date: str | None,
        ) -> Ticker:
            tk = Ticker(symbol=sym, name=sym, last_enriched_at=enriched_at)
            session.add(tk)
            session.flush()
            session.add(
                AnalysisSnapshot(
                    scan_run_id=run.id,
                    symbol=sym,
                    fundamentals_json=json.dumps(fundamentals),
                    events_insiders_json=json.dumps(
                        {
                            "next_earnings_date": next_earnings_date,
                            "days_to_earnings": 99,
                            "insiders": [{"owner": "CEO"}],
                        }
                    ),
                    is_deep_enriched=True,
                )
            )
            session.flush()
            return tk

        # 1. Fresh cache (4h old, naive UTC as SQLite reads back), earnings 10d ahead
        tk_hit = _seed_case(
            "HITCO",
            (now_dt - timedelta(hours=4)).replace(tzinfo=None),
            {"market_cap": 1e11, "pe_trailing": 22.5},
            "2026-12-04",
        )
        # 2. Stale (>12h)
        tk_stale = _seed_case(
            "STALECO",
            now_dt - timedelta(hours=13),
            {"market_cap": 1e11},
            "2026-12-10",
        )
        # 3. Imminent earnings (<=1d) bypasses cache
        tk_earn = _seed_case(
            "EARNCO",
            now_dt - timedelta(hours=2),
            {"market_cap": 1e11},
            "2026-11-25",
        )
        # 4. Empty fundamentals values bypass cache
        tk_empty = _seed_case(
            "EMPTYCO",
            now_dt - timedelta(hours=2),
            {"market_cap": None, "pe_trailing": None},
            "2026-12-10",
        )
        session.commit()

        hit = pipeline._load_cached_fundamentals_events(session, tk_hit, max_age_hours=12.0, now_dt=now_dt)
        assert hit is not None
        fund_hit, ev_hit = hit
        assert fund_hit["pe_trailing"] == 22.5
        assert ev_hit["days_to_earnings"] == 10
        assert "insiders" not in ev_hit

        assert pipeline._load_cached_fundamentals_events(session, tk_stale, max_age_hours=12.0, now_dt=now_dt) is None
        assert pipeline._load_cached_fundamentals_events(session, tk_earn, max_age_hours=12.0, now_dt=now_dt) is None
        assert pipeline._load_cached_fundamentals_events(session, tk_empty, max_age_hours=12.0, now_dt=now_dt) is None


def test_prune_old_snapshots(tmp_path: Path):
    db_file = tmp_path / "inc_prune.db"
    configure_database(db_file)
    init_db()

    with session_scope() as session:
        run = ScanRun(mode="quick", status="completed", stage="completed")
        session.add(run)
        session.flush()

        for sym in ("AAPL", "MSFT"):
            pipeline._ensure_ticker_row(session, sym, name=sym)
        session.flush()

        aapl_ids: list[int] = []
        for i in range(8):
            snap = AnalysisSnapshot(scan_run_id=run.id, symbol="AAPL", composite_score=50.0 + i)
            session.add(snap)
            session.flush()
            aapl_ids.append(snap.id)

        for i in range(3):
            session.add(AnalysisSnapshot(scan_run_id=run.id, symbol="MSFT", composite_score=60.0 + i))
        session.commit()

        pruned = pipeline.prune_old_snapshots(session, keep_per_symbol=5)
        session.commit()

        assert pruned == 3
        rem_aapl = [
            row[0]
            for row in session.query(AnalysisSnapshot.id)
            .filter(AnalysisSnapshot.symbol == "AAPL")
            .order_by(AnalysisSnapshot.id.asc())
            .all()
        ]
        rem_msft = session.query(AnalysisSnapshot).filter(AnalysisSnapshot.symbol == "MSFT").count()
        assert rem_aapl == aapl_ids[-5:]
        assert rem_msft == 3


def test_history_backfill_handles_naive_sqlite_datetime(tmp_path: Path, monkeypatch):
    from app.core import history_backfill
    from app.db.models import load_runtime_settings, save_runtime_settings

    db_file = tmp_path / "backfill_tz.db"
    configure_database(db_file)
    init_db()

    now_ts = 1_800_000_000
    with session_scope() as session:
        rt = load_runtime_settings(session)
        rt["history"]["backfill_enabled"] = True
        save_runtime_settings(session, rt)
        tk = session.get(Ticker, "AAPL")
        assert tk is not None
        # Store naive UTC datetime (as SQLite does on readback) and mark other starter tickers fresh
        tk.history_backfilled_at = (datetime.now(timezone.utc) - timedelta(hours=24)).replace(tzinfo=None)
        for other in session.query(Ticker).filter(Ticker.symbol != "AAPL").all():
            other.history_backfilled_at = datetime.now(timezone.utc).replace(tzinfo=None)
        session.commit()

    monkeypatch.setattr(history_backfill, "is_gateway_reachable", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        history_backfill,
        "fetch_daily_batch",
        lambda syms, **kwargs: {s: _make_bars(5, end_ts=now_ts, step_sec=86400, price=200.0) for s in syms},
    )
    monkeypatch.setattr(
        history_backfill,
        "fetch_weekly_bars",
        lambda sym, **kwargs: _make_bars(5, end_ts=now_ts, step_sec=7 * 86400, price=200.0),
    )
    monkeypatch.setattr(
        history_backfill,
        "fetch_intraday_1h",
        lambda sym, **kwargs: _make_bars(5, end_ts=now_ts, step_sec=3600, price=200.0),
    )

    res = history_backfill.run_history_backfill_batch(max_symbols=1, force=False)
    assert "error" not in res["message"].lower()
    assert res["last_batch_symbols"] == ["AAPL"]
