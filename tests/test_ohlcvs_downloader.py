import os
import json
import types
import asyncio
import numpy as np
import pandas as pd
import pytest
from unittest.mock import AsyncMock, MagicMock

import downloader
from exchanges.aster_rest import translate_aster_exchange_info


def test_normalize_exchange_name(monkeypatch):
    # Make ccxt.exchanges predictable
    monkeypatch.setattr(
        downloader.ccxt,
        "exchanges",
        ["binance", "binanceusdm", "kucoin", "kucoinfutures", "bybit"],
        raising=False,
    )

    assert downloader.normalize_exchange_name("binance") == "binanceusdm"
    assert downloader.normalize_exchange_name("kucoin") == "kucoinfutures"
    # Unchanged when no matching futures suffix exists
    assert downloader.normalize_exchange_name("bybit") == "bybit"
    # Already normalized stays as-is
    assert downloader.normalize_exchange_name("binanceusdm") == "binanceusdm"


def test_deduplicate_rows():
    arr = np.array([[1.0, 2.0], [1.0, 2.0], [3.0, 4.0], [3.0, 4.0], [5.0, 6.0]])
    out = downloader.deduplicate_rows(arr)
    assert out.shape == (3, 2)
    assert (out[0] == np.array([1.0, 2.0])).all()
    assert (out[1] == np.array([3.0, 4.0])).all()
    assert (out[2] == np.array([5.0, 6.0])).all()


def test_ensure_millis_seconds_to_ms():
    df = pd.DataFrame(
        {
            "timestamp": [1_600_000_000],
            "open": [1],
            "high": [1],
            "low": [1],
            "close": [1],
            "volume": [1],
        }
    )
    out = downloader.ensure_millis_df(df.copy())
    assert out["timestamp"].iloc[0] == 1_600_000_000_000


def test_ensure_millis_micros_to_ms():
    df = pd.DataFrame(
        {
            "timestamp": [1_600_000_000_000_000],
            "open": [1],
            "high": [1],
            "low": [1],
            "close": [1],
            "volume": [1],
        }
    )
    out = downloader.ensure_millis_df(df.copy())
    assert out["timestamp"].iloc[0] == 1_600_000_000_000


def test_ensure_millis_ms_unchanged():
    df = pd.DataFrame(
        {
            "timestamp": [1_600_000_000_000],
            "open": [1],
            "high": [1],
            "low": [1],
            "close": [1],
            "volume": [1],
        }
    )
    out = downloader.ensure_millis_df(df.copy())
    assert out["timestamp"].iloc[0] == 1_600_000_000_000


def _make_df_with_gap():
    # timestamps: 0, 60000, 180000 (missing 120000)
    ts = [0, 60_000, 180_000]
    base = pd.DataFrame(
        {
            "timestamp": ts,
            "open": [1.0, 2.0, 4.0],
            "high": [1.5, 2.5, 4.5],
            "low": [0.5, 1.5, 3.5],
            "close": [1.1, 2.1, 4.1],
            "volume": [10.0, 20.0, 40.0],
        }
    )
    return base


def test_fill_gaps_in_ohlcvs():
    df = _make_df_with_gap()
    filled = downloader.fill_gaps_in_ohlcvs(df)
    # Should now have 4 rows: 0, 60000, 120000, 180000
    assert len(filled) == 4
    assert filled["timestamp"].tolist() == [0, 60_000, 120_000, 180_000]
    # The inserted row's O/H/L should be filled with previous close
    assert filled.loc[2, "open"] == filled.loc[1, "close"]


def test_attempt_gap_fix_ohlcvs_small_gap():
    df = _make_df_with_gap()
    fixed = downloader.attempt_gap_fix_ohlcvs(df, symbol="TEST", verbose=False)
    assert len(fixed) == 4
    assert fixed["timestamp"].tolist() == [0, 60_000, 120_000, 180_000]


def test_attempt_gap_fix_ohlcvs_raises_on_huge_gap():
    # create a huge gap of 13 hours
    gap_ms = 13 * 60 * 60 * 1000
    df = pd.DataFrame(
        {
            "timestamp": [0, gap_ms],
            "open": [1.0, 2.0],
            "high": [1.0, 2.0],
            "low": [1.0, 2.0],
            "close": [1.0, 2.0],
            "volume": [0.0, 0.0],
        }
    )
    with pytest.raises(Exception):
        downloader.attempt_gap_fix_ohlcvs(df, symbol="HUGE", verbose=False)


def test_dump_and_load_ohlcvs_roundtrip(tmp_path):
    fp = tmp_path / "ohlcv.npy"
    df = pd.DataFrame(
        {
            "timestamp": [0, 60_000, 120_000, 120_000],  # duplicate last row
            "open": [1.0, 2.0, 3.0, 3.0],
            "high": [1.0, 2.0, 3.0, 3.0],
            "low": [1.0, 2.0, 3.0, 3.0],
            "close": [1.0, 2.0, 3.0, 3.0],
            "volume": [10.0, 20.0, 30.0, 30.0],
        }
    )
    downloader.dump_ohlcv_data(df, str(fp))
    loaded = downloader.load_ohlcv_data(str(fp))
    # Deduplicated so should be 3 rows
    assert loaded.shape[0] == 3
    assert loaded["timestamp"].tolist() == [0, 60_000, 120_000]


@pytest.mark.asyncio
async def test_load_markets_fetch_and_cache(tmp_path, monkeypatch):
    # Make ccxt.exchanges predictable
    monkeypatch.setattr(downloader.ccxt, "exchanges", ["binance", "binanceusdm"], raising=False)

    # Dummy exchange class to inject
    class DummyCCXT:
        def __init__(self, config=None):
            self.options = {}

        async def load_markets(self, reload=False, params=None):
            return {"BTC/USDT:USDT": {"id": "BTCUSDT", "swap": True}}

        async def close(self):
            return

    # Point ccxt.binanceusdm to our dummy
    monkeypatch.setattr(downloader.ccxt, "binanceusdm", DummyCCXT, raising=False)

    # Control time so cache-aging checks are predictable
    monkeypatch.setattr(downloader, "utc_ms", lambda: 1_000_000_000_000, raising=False)
    # First call: force cache miss by reporting old mod time
    monkeypatch.setattr(downloader, "get_file_mod_utc", lambda p: 0, raising=False)

    # Run inside temporary directory so "caches/..." is isolated
    monkeypatch.chdir(tmp_path)

    markets = await downloader.load_markets("binance")
    assert "BTC/USDT:USDT" in markets

    # Ensure cache file exists (uses non-normalized exchange name for cache path)
    cache_fp = tmp_path / "caches" / "binance" / "markets.json"
    assert cache_fp.exists()

    # Second call: make cache fresh to test cache path
    monkeypatch.setattr(
        downloader, "get_file_mod_utc", lambda p: downloader.utc_ms() - 1000, raising=False
    )
    markets2 = await downloader.load_markets("binance")
    assert markets2 == markets


@pytest.mark.asyncio
async def test_ohlcvmanager_load_markets_calls_set_markets(monkeypatch):
    om = downloader.OHLCVManager("binance")

    # Provide a stub cc with set_markets
    class StubCC:
        def __init__(self):
            self.set_markets_called = False

        def set_markets(self, markets):
            self.set_markets_called = True

    om.cc = StubCC()

    # Monkeypatch top-level load_markets to return a dummy dict
    async def fake_load_markets(ex, verbose=False, cc=None):
        return {
            "BTC/USDT:USDT": {
                "swap": True,
                "precision": {"price": 0.01, "amount": 0.001},
                "limits": {"cost": {"min": 5.0}, "amount": {"min": 0.0}},
                "maker": 0.0002,
                "taker": 0.0005,
                "contractSize": 1.0,
            }
        }

    monkeypatch.setattr(downloader, "load_markets", fake_load_markets, raising=True)

    await om.load_markets()
    assert isinstance(om.markets, dict)
    assert om.cc.set_markets_called is True


@pytest.mark.asyncio
async def test_ohlcvmanager_load_cc_aster_uses_custom_loader(monkeypatch):
    om = downloader.OHLCVManager("aster")

    class StubAster:
        def __init__(self):
            self.options = {}
            self.id = "aster"

    stub = StubAster()
    monkeypatch.setattr(downloader, "load_ccxt_instance", lambda *args, **kwargs: stub, raising=True)

    om.load_cc()
    assert om.cc is stub


@pytest.mark.asyncio
async def test_ohlcvmanager_get_first_timestamp_aster(tmp_path, monkeypatch):
    om = downloader.OHLCVManager("aster")
    om.markets = translate_aster_exchange_info(
        {
            "symbols": [
                {
                    "symbol": "BTCUSDT",
                    "status": "TRADING",
                    "contractType": "PERPETUAL",
                    "baseAsset": "BTC",
                    "quoteAsset": "USDT",
                    "marginAsset": "USDT",
                    "pricePrecision": 1,
                    "quantityPrecision": 3,
                    "filters": [
                        {"filterType": "PRICE_FILTER", "tickSize": "0.1"},
                        {"filterType": "LOT_SIZE", "minQty": "0.001", "stepSize": "0.001"},
                        {"filterType": "MIN_NOTIONAL", "notional": "5"},
                    ],
                }
            ]
        }
    )

    class StubAster:
        def __init__(self):
            self.options = {"enableRateLimit": True}
            self.id = "aster"
            self.fetch_ohlcv = AsyncMock(return_value=[[1704067200000, 1, 1, 1, 1, 1]])

    monkeypatch.chdir(tmp_path)
    om.cache_filepaths["first_timestamps"] = str(tmp_path / "caches" / "aster" / "first_timestamps.json")
    om.cc = StubAster()
    fts = await om.get_first_timestamp("BTC")
    assert fts == 1704067200000
    om.cc.fetch_ohlcv.assert_awaited_once()


@pytest.mark.asyncio
async def test_fetch_and_save_day_aster_writes_full_day(tmp_path):
    om = downloader.OHLCVManager("aster")
    om.cc = MagicMock()
    start_ts = downloader.date_to_ts("2024-01-01")
    candles = []
    for i in range(1440):
        ts = start_ts + i * 60_000
        candles.append([ts, 100.0, 101.0, 99.0, 100.5, 5.0])
    om.cc.fetch_ohlcv = AsyncMock(return_value=candles)
    dirpath = tmp_path / "BTC"
    dirpath.mkdir(parents=True, exist_ok=True)

    await om.fetch_and_save_day_aster("BTC/USDT:USDT", "2024-01-01", str(dirpath))

    dumped = dirpath / "2024-01-01.npy"
    assert dumped.exists()
    loaded = np.load(dumped, allow_pickle=False)
    assert loaded.shape == (1440, 6)


@pytest.mark.asyncio
async def test_compute_exchange_volume_ratios_simple(monkeypatch):
    # Build stub OMs for two exchanges
    class StubOM:
        def __init__(self, df):
            self._df = df

        def has_coin(self, coin):
            return True

        def update_date_range(self, start, end):
            pass

        async def get_ohlcvs(self, coin):
            return self._df.copy()

    # Day 0 in ms is 0.. so use two bars
    df_a = pd.DataFrame(
        {
            "timestamp": [0, 60_000],
            "open": [1, 1],
            "high": [1, 1],
            "low": [1, 1],
            "close": [1, 1],
            "volume": [10.0, 20.0],  # sum = 30
        }
    )
    df_b = pd.DataFrame(
        {
            "timestamp": [0, 60_000],
            "open": [1, 1],
            "high": [1, 1],
            "low": [1, 1],
            "close": [1, 1],
            "volume": [30.0, 30.0],  # sum = 60
        }
    )

    om_dict = {"exA": StubOM(df_a), "exB": StubOM(df_b)}
    ratios = await downloader.compute_exchange_volume_ratios(
        exchanges=["exA", "exB"],
        coins=["BTC"],
        start_date="1970-01-01",
        end_date="1970-01-02",
        om_dict=om_dict,
    )
    # Pair order follows (ex0, ex1) as constructed; exA < exB
    assert ("exA", "exB") in ratios
    assert ratios[("exA", "exB")] == pytest.approx(0.5)
