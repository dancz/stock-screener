from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT_DIR = Path(__file__).resolve().parent.parent
BACKEND_DIR = ROOT_DIR / "backend"
for p in (ROOT_DIR, BACKEND_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def make_ohlcv_from_closes(
    closes: np.ndarray,
    spread_pct: float = 0.012,
    base_volume: float = 1_500_000.0,
    start_ts: int = 1_700_000_000,
) -> pd.DataFrame:
    n = len(closes)
    ts = np.array([start_ts + i * 86400 for i in range(n)], dtype=int)
    opens = np.roll(closes, 1)
    opens[0] = closes[0] * 0.998
    highs = np.maximum(opens, closes) * (1.0 + spread_pct * 0.5)
    lows = np.minimum(opens, closes) * (1.0 - spread_pct * 0.5)
    vols = np.full(n, base_volume, dtype=float)
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
