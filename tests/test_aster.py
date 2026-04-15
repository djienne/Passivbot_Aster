"""Aster connector tests for foundation and phase-2 REST behavior."""

from __future__ import annotations

import copy
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC_DIR = os.path.join(ROOT_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from exchanges.aster import AsterBot
from exchanges.aster_ws import AsterWebsocketConfig
from exchanges.aster_rest import (
    ASTER_DEFAULT_BASE_URL,
    ASTER_DEFAULT_WS_URL,
    AsterConfiguration,
    translate_aster_exchange_info,
)
from fill_events_manager import _build_fetcher_for_bot
from utils import get_quote, load_markets


SAMPLE_EXCHANGE_INFO = {
    "timezone": "UTC",
    "serverTime": 1710000000000,
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
            "maxLeverage": "50",
            "filters": [
                {"filterType": "PRICE_FILTER", "minPrice": "0.1", "maxPrice": "1000000", "tickSize": "0.1"},
                {"filterType": "LOT_SIZE", "minQty": "0.001", "maxQty": "1000", "stepSize": "0.001"},
                {"filterType": "MIN_NOTIONAL", "notional": "5"},
            ],
        },
        {
            "symbol": "ETHUSDT",
            "status": "TRADING",
            "contractType": "PERPETUAL",
            "baseAsset": "ETH",
            "quoteAsset": "USDT",
            "marginAsset": "USDT",
            "pricePrecision": 2,
            "quantityPrecision": 2,
            "maxLeverage": "30",
            "filters": [
                {"filterType": "PRICE_FILTER", "minPrice": "0.01", "maxPrice": "1000000", "tickSize": "0.01"},
                {"filterType": "LOT_SIZE", "minQty": "0.01", "maxQty": "1000", "stepSize": "0.01"},
                {"filterType": "MIN_NOTIONAL", "notional": "10"},
            ],
        },
    ],
}

TEST_CONFIG = {
    "live": {
        "user": "aster_test",
        "approved_coins": {"long": ["BTC", "ETH"], "short": []},
        "ignored_coins": {"long": [], "short": []},
        "empty_means_all_approved": False,
        "minimum_coin_age_days": 0,
        "max_memory_candles_per_symbol": 1000,
        "max_disk_candles_per_symbol_per_tf": 1000,
        "inactive_coin_candle_ttl_minutes": 10,
        "memory_snapshot_interval_minutes": 30,
        "auto_gs": True,
        "leverage": 10,
        "time_in_force": "good_till_cancelled",
        "pnls_max_lookback_days": 30,
        "dry_run": False,
        "dry_run_wallet": 10000.0,
        "execution_delay_seconds": 2,
        "filter_by_min_effective_cost": True,
        "forced_mode_long": "",
        "forced_mode_short": "",
        "market_orders_allowed": True,
        "max_n_cancellations_per_batch": 5,
        "max_n_creations_per_batch": 3,
        "max_n_restarts_per_day": 10,
        "max_warmup_minutes": 0,
        "price_distance_threshold": 0.002,
        "warmup_ratio": 0.2,
    },
    "bot": {
        "long": {
            "n_positions": 1,
            "total_wallet_exposure_limit": 1.0,
            "ema_span_0": 60,
            "ema_span_1": 60,
            "entry_initial_qty_pct": 0.1,
            "entry_initial_ema_dist": 0.01,
            "entry_grid_spacing_pct": 0.01,
            "entry_grid_spacing_we_weight": 0,
            "entry_grid_spacing_log_weight": 0,
            "entry_grid_spacing_log_span_hours": 24,
            "entry_grid_double_down_factor": 0.1,
            "entry_trailing_grid_ratio": -1,
            "entry_trailing_retracement_pct": 0,
            "entry_trailing_threshold_pct": -0.1,
            "entry_trailing_double_down_factor": 0.1,
            "close_grid_markup_start": 0.01,
            "close_grid_markup_end": 0.01,
            "close_grid_qty_pct": 0.1,
            "close_trailing_grid_ratio": -1,
            "close_trailing_qty_pct": 0.1,
            "close_trailing_retracement_pct": 0,
            "close_trailing_threshold_pct": -0.1,
            "unstuck_close_pct": 0.005,
            "unstuck_ema_dist": -0.1,
            "unstuck_loss_allowance_pct": 0.01,
            "unstuck_threshold": 0.7,
            "enforce_exposure_limit": True,
            "filter_log_range_ema_span": 60,
            "filter_volume_drop_pct": 0,
            "filter_volume_ema_span": 10,
        },
        "short": {
            "n_positions": 0,
            "total_wallet_exposure_limit": 0,
            "ema_span_0": 200,
            "ema_span_1": 200,
            "entry_initial_qty_pct": 0.025,
            "entry_initial_ema_dist": -0.1,
            "entry_grid_spacing_pct": 0.01,
            "entry_grid_spacing_we_weight": 0,
            "entry_grid_spacing_log_weight": 0,
            "entry_grid_spacing_log_span_hours": 24,
            "entry_grid_double_down_factor": 0.1,
            "entry_trailing_grid_ratio": -1,
            "entry_trailing_retracement_pct": 0,
            "entry_trailing_threshold_pct": -0.1,
            "entry_trailing_double_down_factor": 0.1,
            "close_grid_markup_start": 0.01,
            "close_grid_markup_end": 0.01,
            "close_grid_qty_pct": 0.1,
            "close_trailing_grid_ratio": -1,
            "close_trailing_qty_pct": 0.1,
            "close_trailing_retracement_pct": 0,
            "close_trailing_threshold_pct": -0.1,
            "unstuck_close_pct": 0.005,
            "unstuck_ema_dist": -0.1,
            "unstuck_loss_allowance_pct": 0.01,
            "unstuck_threshold": 0.7,
            "enforce_exposure_limit": True,
            "filter_log_range_ema_span": 10,
            "filter_volume_drop_pct": 0,
            "filter_volume_ema_span": 10,
        },
    },
    "logging": {"level": 0},
}

ASTER_USER_INFO = {
    "exchange": "aster",
    "api_user": "0x1111111111111111111111111111111111111111",
    "api_signer": "0x2222222222222222222222222222222222222222",
    "api_private_key": "0xabc123",
}


def _make_bot() -> AsterBot:
    config = copy.deepcopy(TEST_CONFIG)
    with patch("passivbot.load_user_info", return_value=ASTER_USER_INFO), patch(
        "procedures.load_user_info", return_value=ASTER_USER_INFO
    ), patch("passivbot.load_broker_code", return_value=""), patch(
        "passivbot.resolve_custom_endpoint_override", return_value=None
    ), patch("passivbot.CandlestickManager"):
        bot = AsterBot(config)
    bot.markets_dict = translate_aster_exchange_info(SAMPLE_EXCHANGE_INFO)
    bot.set_market_specific_settings()
    bot.active_symbols = ["BTC/USDT:USDT"]
    bot.open_orders = {}
    bot.positions = {}
    bot.coin_overrides = {}
    bot.approved_coins_minus_ignored_coins = {
        "long": {"BTC/USDT:USDT", "ETH/USDT:USDT"},
        "short": set(),
    }
    return bot


def test_aster_configuration_defaults():
    cfg = AsterConfiguration.from_user_info({"exchange": "aster"})
    assert cfg.base_url == ASTER_DEFAULT_BASE_URL
    assert cfg.ws_url == ASTER_DEFAULT_WS_URL
    assert cfg.balance_mode == "auto"


def test_websocket_config_defaults_follow_credentials():
    cfg = AsterWebsocketConfig.from_credentials(
        AsterConfiguration.from_user_info({"exchange": "aster"})
    )
    assert cfg.private_stream_mode == "v3"


def test_translate_exchange_info_normalizes_symbols_and_filters():
    markets = translate_aster_exchange_info(SAMPLE_EXCHANGE_INFO)
    btc = markets["BTC/USDT:USDT"]
    assert btc["id"] == "BTCUSDT"
    assert btc["precision"]["price"] == 0.1
    assert btc["precision"]["amount"] == 0.001
    assert btc["limits"]["amount"]["min"] == 0.001
    assert btc["limits"]["cost"]["min"] == 5.0
    assert btc["info"]["maxLeverage"] == 50


@pytest.mark.asyncio
async def test_load_markets_aster_uses_custom_loader_and_builds_symbol_maps(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with patch(
        "exchanges.aster_rest.AsterRestClient.fetch_exchange_info",
        new=AsyncMock(return_value=SAMPLE_EXCHANGE_INFO),
    ):
        markets = await load_markets("aster", max_age_ms=0, verbose=False)
    assert "BTC/USDT:USDT" in markets
    assert os.path.exists(tmp_path / "caches" / "aster" / "markets.json")
    assert os.path.exists(tmp_path / "caches" / "aster" / "coin_to_symbol_map.json")
    assert os.path.exists(tmp_path / "caches" / "symbol_to_coin_map.json")
    with open(tmp_path / "caches" / "aster" / "markets.json", "r") as f:
        cached = json.load(f)
    assert "BTC/USDT:USDT" in cached


def test_get_quote_aster_defaults_to_usdt():
    assert get_quote("aster") == "USDT"


@pytest.mark.asyncio
async def test_fetch_balance_single_asset_uses_account_available_balance():
    bot = _make_bot()
    bot._aster_multi_assets_mode = False
    bot.cca.fetch_account = AsyncMock(
        return_value={
            "totalMarginBalance": "91.25",
            "totalCrossWalletBalance": "88.8",
            "availableBalance": "123.45",
            "assets": [{"asset": "USDT", "availableBalance": "90.0", "crossWalletBalance": "88.8", "marginBalance": "91.25", "marginAvailable": True}],
        }
    )
    assert await bot.fetch_balance() == 91.25


@pytest.mark.asyncio
async def test_fetch_balance_multi_asset_sums_margin_balances_of_margin_stables():
    bot = _make_bot()
    bot._aster_multi_assets_mode = True
    bot.cca.fetch_account = AsyncMock(
        return_value={
            "availableBalance": "99.4",
            "assets": [
                {"asset": "USDT", "availableBalance": "99.4", "walletBalance": "10", "crossWalletBalance": "10", "marginBalance": "11.5", "marginAvailable": True},
                {"asset": "USDC", "availableBalance": "99.5", "walletBalance": "5", "crossWalletBalance": "5", "marginBalance": "5.75", "marginAvailable": True},
                {"asset": "USDF", "availableBalance": "99.4", "walletBalance": "7", "crossWalletBalance": "7", "marginBalance": "7.1", "marginAvailable": False},
                {"asset": "BNB", "availableBalance": "99", "walletBalance": "99", "crossWalletBalance": "99", "marginBalance": "99", "marginAvailable": True},
            ]
        }
    )
    assert await bot.fetch_balance() == 17.25


def test_extract_ws_balance_total_multi_asset_sums_wallet_balances_and_upnl():
    bot = _make_bot()
    bot._aster_multi_assets_mode = True
    total = bot._extract_ws_balance_total(
        [
            {"a": "USDT", "wb": "17.60042047"},
            {"a": "USDC", "wb": "99.00681264"},
            {"a": "USDF", "wb": "0.09105510"},
        ],
        [{"up": "0.93171179"}],
    )
    assert total == pytest.approx(117.62999999999998)


@pytest.mark.asyncio
async def test_fetch_positions_parses_one_way_long_and_short():
    bot = _make_bot()
    bot.cca.fetch_position_risk = AsyncMock(
        return_value=[
            {"symbol": "BTCUSDT", "positionSide": "BOTH", "positionAmt": "0.5", "entryPrice": "70000"},
            {"symbol": "ETHUSDT", "positionSide": "BOTH", "positionAmt": "-1.25", "entryPrice": "3500"},
            {"symbol": "BTCUSDT", "positionSide": "BOTH", "positionAmt": "0", "entryPrice": "0"},
        ]
    )
    positions = await bot.fetch_positions()
    assert positions == [
        {"symbol": "BTC/USDT:USDT", "position_side": "long", "size": 0.5, "price": 70000.0},
        {"symbol": "ETH/USDT:USDT", "position_side": "short", "size": 1.25, "price": 3500.0},
    ]


@pytest.mark.asyncio
async def test_fetch_open_orders_normalizes_one_way_reduce_only():
    bot = _make_bot()
    bot.cca.fetch_open_orders = AsyncMock(
        return_value=[
            {
                "symbol": "BTCUSDT",
                "orderId": 123,
                "clientOrderId": "cid-1",
                "side": "SELL",
                "positionSide": "BOTH",
                "reduceOnly": True,
                "origQty": "0.25",
                "price": "71000",
                "status": "NEW",
                "time": 1710001000000,
            }
        ]
    )
    orders = await bot.fetch_open_orders()
    assert orders[0]["id"] == "123"
    assert orders[0]["symbol"] == "BTC/USDT:USDT"
    assert orders[0]["position_side"] == "long"
    assert orders[0]["reduceOnly"] is True


def test_get_order_execution_params_maps_one_way_orders_to_both():
    bot = _make_bot()
    params = bot.get_order_execution_params(
        {
            "symbol": "BTC/USDT:USDT",
            "side": "sell",
            "position_side": "long",
            "reduce_only": True,
            "type": "limit",
            "custom_id": "abcdef",
        }
    )
    assert params["positionSide"] == "BOTH"
    assert params["reduceOnly"] is True
    assert params["timeInForce"] == "GTC"
    assert params["newClientOrderId"] == "abcdef"


@pytest.mark.asyncio
async def test_execute_order_uses_post_only_gtx_and_normalizes_response():
    bot = _make_bot()
    bot.config["live"]["time_in_force"] = "post_only"
    bot.cca.create_order = AsyncMock(
        return_value={
            "symbol": "BTCUSDT",
            "orderId": 987,
            "clientOrderId": "cid-987",
            "side": "BUY",
            "positionSide": "BOTH",
            "origQty": "0.01",
            "price": "70000",
            "status": "NEW",
            "time": 1710002000000,
        }
    )
    result = await bot.execute_order(
        {
            "symbol": "BTC/USDT:USDT",
            "side": "buy",
            "position_side": "long",
            "qty": 0.01,
            "price": 70000.0,
            "type": "limit",
            "reduce_only": False,
            "custom_id": "cid-987",
        }
    )
    assert bot.cca.create_order.await_args.kwargs["params"]["timeInForce"] == "GTX"
    assert bot.cca.create_order.await_args.kwargs["params"]["positionSide"] == "BOTH"
    assert result["id"] == "987"
    assert result["symbol"] == "BTC/USDT:USDT"


@pytest.mark.asyncio
async def test_fetch_pnls_fans_out_per_symbol_and_sorts():
    bot = _make_bot()
    bot.positions = {
        "ETH/USDT:USDT": {
            "long": {"size": 1.0, "price": 3000.0},
            "short": {"size": 0.0, "price": 0.0},
        }
    }
    bot.cca.fetch_user_trades = AsyncMock(
        side_effect=[
            [
                {
                    "symbol": "BTCUSDT",
                    "id": 1,
                    "time": 1710003000000,
                    "side": "BUY",
                    "positionSide": "BOTH",
                    "qty": "0.01",
                    "price": "70000",
                    "realizedPnl": "0",
                    "commission": "0.1",
                }
            ],
            [
                {
                    "symbol": "ETHUSDT",
                    "id": 2,
                    "time": 1710002000000,
                    "side": "SELL",
                    "positionSide": "SHORT",
                    "qty": "0.2",
                    "price": "3500",
                    "realizedPnl": "12.5",
                    "commission": "0.2",
                }
            ],
        ]
    )
    pnls = await bot.fetch_pnls()
    assert [x["symbol"] for x in pnls] == ["ETH/USDT:USDT", "BTC/USDT:USDT"]
    assert pnls[0]["position_side"] == "short"
    assert pnls[1]["position_side"] == "long"
    assert pnls[0]["pnl"] == 12.5


@pytest.mark.asyncio
async def test_update_exchange_config_enforces_one_way_and_balance_mode():
    config = copy.deepcopy(TEST_CONFIG)
    config["live"]["hedge_mode"] = True
    user_info = dict(ASTER_USER_INFO)
    user_info["balance_mode"] = "multi_asset_stables"
    with patch("passivbot.load_user_info", return_value=user_info), patch(
        "procedures.load_user_info", return_value=user_info
    ), patch("passivbot.load_broker_code", return_value=""), patch(
        "passivbot.resolve_custom_endpoint_override", return_value=None
    ), patch("passivbot.CandlestickManager"):
        bot = AsterBot(config)
    bot.cca.get_position_mode = AsyncMock(return_value=True)
    bot.cca.set_position_mode = AsyncMock(return_value={"code": 200})
    bot.cca.get_multi_assets_mode = AsyncMock(return_value=False)
    bot.cca.set_multi_assets_mode = AsyncMock(return_value={"code": 200})
    await bot.update_exchange_config()
    bot.cca.set_position_mode.assert_awaited_once_with(False)
    bot.cca.set_multi_assets_mode.assert_awaited_once_with(True)


@pytest.mark.asyncio
async def test_update_exchange_config_by_symbols_clamps_leverage_to_market_max():
    bot = _make_bot()
    bot.max_leverage["BTC/USDT:USDT"] = 5
    bot.cca.change_leverage = AsyncMock(return_value={"leverage": 5})
    bot.cca.set_margin_type = AsyncMock(return_value={"code": 200})
    await bot.update_exchange_config_by_symbols(["BTC/USDT:USDT"])
    bot.cca.change_leverage.assert_awaited_once_with("BTC/USDT:USDT", 5)
    bot.cca.set_margin_type.assert_awaited_once_with("BTC/USDT:USDT", "CROSSED")


@pytest.mark.asyncio
async def test_handle_private_account_update_refreshes_ws_balance_and_positions():
    bot = _make_bot()
    bot.balance = 0.0
    bot.handle_balance_update = AsyncMock()
    await bot._handle_private_ws_message(
        {
            "e": "ACCOUNT_UPDATE",
            "E": 1710004000000,
            "a": {
                "B": [{"a": "USDT", "wb": "42.5"}],
                "P": [{"s": "BTCUSDT", "pa": "0.02", "ep": "70100", "ps": "LONG"}],
            },
        },
        "v3",
    )
    assert bot._ws_balance_cache == 42.5
    assert bot.balance == 42.5
    assert bot._ws_positions_cache == [
        {
            "symbol": "BTC/USDT:USDT",
            "position_side": "long",
            "size": 0.02,
            "price": 70100.0,
        }
    ]
    bot.handle_balance_update.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_private_order_trade_update_updates_order_cache_and_ws_fill_cache():
    bot = _make_bot()
    bot.handle_order_update = MagicMock()
    await bot._handle_private_ws_message(
        {
            "e": "ORDER_TRADE_UPDATE",
            "E": 1710005000000,
            "o": {
                "s": "BTCUSDT",
                "S": "BUY",
                "X": "PARTIALLY_FILLED",
                "x": "TRADE",
                "i": 111,
                "c": "0x0001abcd",
                "ps": "LONG",
                "R": False,
                "q": "0.03",
                "l": "0.01",
                "L": "70200",
                "rp": "1.5",
                "n": "0.02",
                "N": "USDT",
                "t": 222,
                "T": 1710005000000,
                "p": "70200",
            },
        },
        "v3",
    )
    assert bot._ws_open_orders_cache is not None
    assert bot._ws_open_orders_cache[0]["id"] == "111"
    assert "BTCUSDT:222" in bot._ws_fill_events_cache
    assert bot._ws_fill_events_cache["BTCUSDT:222"]["client_order_id"] == "0x0001abcd"
    bot.handle_order_update.assert_called_once()


@pytest.mark.asyncio
async def test_handle_public_ws_message_updates_ticker_cache_and_fetch_tickers_uses_it():
    bot = _make_bot()
    await bot._handle_public_ws_message(
        {"s": "BTCUSDT", "b": "70000.1", "a": "70000.5", "E": 1710006000000},
        "btcusdt@bookTicker",
    )
    bot.cca.fetch_book_ticker = AsyncMock(side_effect=AssertionError("REST should not be used"))
    tickers = await bot.fetch_tickers()
    assert tickers["BTC/USDT:USDT"]["bid"] == 70000.1
    assert tickers["BTC/USDT:USDT"]["ask"] == 70000.5


@pytest.mark.asyncio
async def test_fetch_fill_events_merges_rest_trades_and_ws_fill_cache():
    bot = _make_bot()
    bot.active_symbols = ["BTC/USDT:USDT"]
    bot.approved_coins_minus_ignored_coins = {"long": {"BTC/USDT:USDT"}, "short": set()}
    bot._ws_fill_events_cache = {
        "BTCUSDT:99": {
            "id": "BTCUSDT:99",
            "timestamp": 1710006500000,
            "datetime": "2024-03-09T00:00:00+00:00",
            "symbol": "BTC/USDT:USDT",
            "side": "buy",
            "qty": 0.01,
            "price": 70300.0,
            "pnl": 0.0,
            "fees": {"currency": "USDT", "cost": 0.01},
            "pb_order_type": "unknown",
            "position_side": "long",
            "client_order_id": "",
            "raw": [{"source": "ws", "data": {}}],
        }
    }
    bot.cca.fetch_user_trades = AsyncMock(
        return_value=[
            {
                "symbol": "BTCUSDT",
                "id": 1,
                "orderId": 555,
                "time": 1710006400000,
                "side": "SELL",
                "positionSide": "LONG",
                "qty": "0.02",
                "price": "70400",
                "realizedPnl": "2.5",
                "commission": "0.03",
                "commissionAsset": "USDT",
            }
        ]
    )
    bot.cca.fetch_all_orders = AsyncMock(
        return_value=[{"clientOrderId": "0x0002beef", "orderId": 555}]
    )
    events = await bot.fetch_fill_events()
    assert [x["id"] for x in events] == ["BTCUSDT:1", "BTCUSDT:99"]
    assert events[0]["client_order_id"] == "0x0002beef"
    assert events[0]["pb_order_type"] == "entry_trailing_normal_long"


def test_build_fetcher_for_bot_supports_aster():
    bot = _make_bot()
    fetcher = _build_fetcher_for_bot(bot, ["BTC/USDT:USDT"])
    assert fetcher.__class__.__name__ == "AsterFetcher"
