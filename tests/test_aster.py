"""Aster connector tests for foundation and phase-2 REST behavior."""

from __future__ import annotations

import copy
import asyncio
import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC_DIR = os.path.join(ROOT_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from exchanges.aster import AsterBot
from exchanges.aster_ws import AsterWebsocketConfig, _exponential_backoff_delay
from exchanges.aster_rest import (
    ASTER_DEFAULT_BASE_URL,
    ASTER_DEFAULT_WS_URL,
    AsterAPIError,
    AsterConfiguration,
    AsterRestClient,
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


def _fake_signing_dependencies():
    class FakeWeb3:
        @staticmethod
        def to_checksum_address(value):
            return value

        @staticmethod
        def keccak(value):
            return bytes.fromhex("12" * 32)

    class FakeAccount:
        @staticmethod
        def sign_message(message, private_key):
            return SimpleNamespace(signature=bytes.fromhex("34" * 65))

    def fake_encode_defunct(*, hexstr):
        return hexstr

    def fake_encode(types, values):
        return b"encoded"

    return FakeWeb3, FakeAccount, fake_encode_defunct, fake_encode


def test_aster_configuration_defaults():
    cfg = AsterConfiguration.from_user_info({"exchange": "aster"})
    assert cfg.base_url == ASTER_DEFAULT_BASE_URL
    assert cfg.ws_url == ASTER_DEFAULT_WS_URL
    assert cfg.balance_mode == "auto"


def test_sign_v3_params_nonce_and_timestamp_are_strictly_monotonic_when_clock_repeats():
    client = AsterRestClient(
        AsterConfiguration(
            api_user=ASTER_USER_INFO["api_user"],
            api_signer=ASTER_USER_INFO["api_signer"],
            api_private_key=ASTER_USER_INFO["api_private_key"],
        )
    )
    with patch("exchanges.aster_rest._load_signing_dependencies", return_value=_fake_signing_dependencies()), patch(
        "exchanges.aster_rest.time.time", return_value=1000.0
    ):
        first = client._sign_v3_params({})
        second = client._sign_v3_params({})
    assert int(second["nonce"]) == int(first["nonce"]) + 1
    assert int(second["timestamp"]) == int(first["timestamp"]) + 1


@pytest.mark.asyncio
async def test_private_request_json_serializes_signed_private_requests():
    client = AsterRestClient(
        AsterConfiguration(
            api_user=ASTER_USER_INFO["api_user"],
            api_signer=ASTER_USER_INFO["api_signer"],
            api_private_key=ASTER_USER_INFO["api_private_key"],
        )
    )
    active = 0
    max_active = 0
    seen_nonces = []

    async def fake_request_json(method, path, *, params=None, data=None, headers=None):
        nonlocal active, max_active
        payload = params if params is not None else data
        seen_nonces.append(int(payload["nonce"]))
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return {"code": 200}

    with patch("exchanges.aster_rest._load_signing_dependencies", return_value=_fake_signing_dependencies()), patch.object(
        client, "_request_json", side_effect=fake_request_json
    ):
        await asyncio.gather(
            client._private_request_json("GET", ("/fapi/v3/account",)),
            client._private_request_json("GET", ("/fapi/v3/positionRisk",)),
        )

    assert max_active == 1
    assert len(seen_nonces) == 2
    assert seen_nonces[1] > seen_nonces[0]


@pytest.mark.asyncio
async def test_private_request_resigns_per_retry_on_network_error():
    import aiohttp as _aiohttp

    client = AsterRestClient(
        AsterConfiguration(
            api_user=ASTER_USER_INFO["api_user"],
            api_signer=ASTER_USER_INFO["api_signer"],
            api_private_key=ASTER_USER_INFO["api_private_key"],
        )
    )
    seen_nonces = []
    calls = {"n": 0}

    async def fake_request_json(method, path, *, params=None, data=None, headers=None):
        payload = params if params is not None else data
        seen_nonces.append(int(payload["nonce"]))
        calls["n"] += 1
        if calls["n"] == 1:
            raise _aiohttp.ClientConnectorError(
                connection_key=MagicMock(),
                os_error=OSError("connect refused"),
            )
        return {"code": 200}

    with patch(
        "exchanges.aster_rest._load_signing_dependencies",
        return_value=_fake_signing_dependencies(),
    ), patch.object(client, "_request_json", side_effect=fake_request_json):
        await client._private_request_json(
            "GET", ("/fapi/v3/primary", "/fapi/v3/fallback")
        )
    assert len(seen_nonces) == 2
    assert seen_nonces[1] > seen_nonces[0]


@pytest.mark.asyncio
async def test_private_request_does_not_retry_on_aster_api_error():
    client = AsterRestClient(
        AsterConfiguration(
            api_user=ASTER_USER_INFO["api_user"],
            api_signer=ASTER_USER_INFO["api_signer"],
            api_private_key=ASTER_USER_INFO["api_private_key"],
        )
    )
    calls = {"n": 0}

    async def fake_request_json(method, path, *, params=None, data=None, headers=None):
        calls["n"] += 1
        raise AsterAPIError("insufficient margin", status=400, code=-2019, path=path)

    with patch(
        "exchanges.aster_rest._load_signing_dependencies",
        return_value=_fake_signing_dependencies(),
    ), patch.object(client, "_request_json", side_effect=fake_request_json):
        with pytest.raises(AsterAPIError) as excinfo:
            await client._private_request_json(
                "POST", ("/fapi/v3/order_primary", "/fapi/v3/order_fallback")
            )
    assert excinfo.value.code == -2019
    assert calls["n"] == 1  # the second path must not be tried


def test_websocket_config_defaults_follow_credentials():
    cfg = AsterWebsocketConfig.from_credentials(
        AsterConfiguration.from_user_info({"exchange": "aster"})
    )
    assert cfg.private_stream_mode == "v3"
    assert cfg.private_stale_seconds is None


def test_websocket_backoff_delay_exponentially_grows_and_caps(monkeypatch):
    monkeypatch.setattr("exchanges.aster_ws.random.uniform", lambda a, b: 0.0)
    assert _exponential_backoff_delay(0, base=1.0, cap=60.0, jitter_fraction=0.25) == 1.0
    assert _exponential_backoff_delay(1, base=1.0, cap=60.0, jitter_fraction=0.25) == 2.0
    assert _exponential_backoff_delay(2, base=1.0, cap=60.0, jitter_fraction=0.25) == 4.0
    assert _exponential_backoff_delay(8, base=1.0, cap=60.0, jitter_fraction=0.25) == 60.0


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
async def test_execute_order_quantizes_price_and_qty_before_submit():
    bot = _make_bot()
    bot.config["live"]["time_in_force"] = "post_only"
    bot.cca.create_order = AsyncMock(
        return_value={
            "symbol": "BTCUSDT",
            "orderId": 988,
            "clientOrderId": "cid-988",
            "side": "BUY",
            "positionSide": "BOTH",
            "origQty": "0.123",
            "price": "70000.1",
            "status": "NEW",
            "time": 1710002000001,
        }
    )
    await bot.execute_order(
        {
            "symbol": "BTC/USDT:USDT",
            "side": "buy",
            "position_side": "long",
            "qty": 0.1230000004,
            "price": 70000.1000000001,
            "type": "limit",
            "reduce_only": False,
            "custom_id": "cid-988",
        }
    )
    call_kwargs = bot.cca.create_order.await_args.kwargs
    assert call_kwargs["amount"] == pytest.approx(0.123)
    assert call_kwargs["price"] == pytest.approx(70000.1)
    assert call_kwargs["params"]["timeInForce"] == "GTX"


def test_did_create_order_returns_false_for_exception_objects():
    bot = _make_bot()
    assert bot.did_create_order(AsterAPIError("boom")) is False


@pytest.mark.asyncio
async def test_execute_order_rounds_qty_down_and_rejects_zero():
    bot = _make_bot()
    bot.cca.create_order = AsyncMock(return_value={})
    result = await bot.execute_order(
        {
            "symbol": "BTC/USDT:USDT",
            "side": "buy",
            "position_side": "long",
            "qty": 0.0004,
            "price": 70000.0,
            "type": "limit",
            "reduce_only": False,
            "custom_id": "cid-rnd-0",
        }
    )
    assert result == {}
    bot.cca.create_order.assert_not_called()


@pytest.mark.asyncio
async def test_execute_order_buy_price_rounds_down():
    bot = _make_bot()
    bot.cca.create_order = AsyncMock(
        return_value={
            "symbol": "BTCUSDT",
            "orderId": 100,
            "clientOrderId": "cid-rnd-1",
            "side": "BUY",
            "positionSide": "BOTH",
            "origQty": "0.001",
            "price": "70000.1",
            "status": "NEW",
            "time": 1710002000001,
        }
    )
    await bot.execute_order(
        {
            "symbol": "BTC/USDT:USDT",
            "side": "buy",
            "position_side": "long",
            "qty": 0.001,
            "price": 70000.15,
            "type": "limit",
            "reduce_only": False,
            "custom_id": "cid-rnd-1",
        }
    )
    call_kwargs = bot.cca.create_order.await_args.kwargs
    assert call_kwargs["price"] == pytest.approx(70000.1)


@pytest.mark.asyncio
async def test_execute_order_sell_price_rounds_up():
    bot = _make_bot()
    bot.cca.create_order = AsyncMock(
        return_value={
            "symbol": "BTCUSDT",
            "orderId": 101,
            "clientOrderId": "cid-rnd-2",
            "side": "SELL",
            "positionSide": "BOTH",
            "origQty": "0.001",
            "price": "70000.2",
            "status": "NEW",
            "time": 1710002000001,
        }
    )
    await bot.execute_order(
        {
            "symbol": "BTC/USDT:USDT",
            "side": "sell",
            "position_side": "long",
            "qty": 0.001,
            "price": 70000.15,
            "type": "limit",
            "reduce_only": True,
            "custom_id": "cid-rnd-2",
        }
    )
    call_kwargs = bot.cca.create_order.await_args.kwargs
    assert call_kwargs["price"] == pytest.approx(70000.2)


@pytest.mark.asyncio
async def test_execute_order_qty_truncates_not_rounds_up():
    bot = _make_bot()
    bot.cca.create_order = AsyncMock(
        return_value={
            "symbol": "BTCUSDT",
            "orderId": 102,
            "clientOrderId": "cid-rnd-3",
            "side": "BUY",
            "positionSide": "BOTH",
            "origQty": "0.009",
            "price": "70000.0",
            "status": "NEW",
            "time": 1710002000001,
        }
    )
    await bot.execute_order(
        {
            "symbol": "BTC/USDT:USDT",
            "side": "buy",
            "position_side": "long",
            "qty": 0.0099999,
            "price": 70000.0,
            "type": "limit",
            "reduce_only": False,
            "custom_id": "cid-rnd-3",
        }
    )
    call_kwargs = bot.cca.create_order.await_args.kwargs
    assert call_kwargs["amount"] == pytest.approx(0.009)


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
async def test_update_exchange_config_by_symbols_sleeps_between_symbols(monkeypatch):
    bot = _make_bot()
    bot.markets_dict["BTC/USDT:USDT"] = bot.markets_dict["BTC/USDT:USDT"]
    bot.max_leverage["BTC/USDT:USDT"] = 5
    bot.max_leverage["ETH/USDT:USDT"] = 5
    bot.cca.change_leverage = AsyncMock(return_value={"leverage": 5})
    bot.cca.set_margin_type = AsyncMock(return_value={"code": 200})
    sleep_durations: list[float] = []
    real_sleep = asyncio.sleep

    async def tracking_sleep(duration):
        sleep_durations.append(duration)
        await real_sleep(0)

    monkeypatch.setattr("exchanges.aster.asyncio.sleep", tracking_sleep)
    await bot.update_exchange_config_by_symbols(["BTC/USDT:USDT", "ETH/USDT:USDT"])
    gap_sleeps = [d for d in sleep_durations if d >= 0.2]
    assert len(gap_sleeps) >= 1


@pytest.mark.asyncio
async def test_update_exchange_config_by_symbols_treats_code_4046_as_already_set(caplog):
    import logging as _logging

    bot = _make_bot()
    bot.max_leverage["BTC/USDT:USDT"] = 5
    bot.cca.change_leverage = AsyncMock(return_value={"leverage": 5})
    bot.cca.set_margin_type = AsyncMock(
        side_effect=AsterAPIError("margin type unchanged", status=400, code=-4046)
    )
    with caplog.at_level(_logging.INFO):
        await bot.update_exchange_config_by_symbols(["BTC/USDT:USDT"])
    assert any("already set" in rec.message.lower() for rec in caplog.records)
    assert not any("error setting cross margin" in rec.message.lower() for rec in caplog.records)


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
async def test_terminal_order_events_drop_from_order_state_caches():
    bot = _make_bot()
    bot.handle_order_update = MagicMock()
    for order_id in range(3):
        await bot._handle_private_ws_message(
            {
                "e": "ORDER_TRADE_UPDATE",
                "E": 1710007000000 + order_id,
                "o": {
                    "s": "BTCUSDT",
                    "S": "BUY",
                    "X": "FILLED",
                    "i": order_id,
                    "q": "0.001",
                    "p": "70000.0",
                    "z": "0.001",
                    "l": "0.001",
                    "L": "70000.0",
                    "T": 1710007000000 + order_id,
                },
            },
            "v3",
        )
    assert bot._ws_order_event_times == {}
    assert bot._aster_order_detail_cache == {}


@pytest.mark.asyncio
async def test_order_state_caches_bounded_under_flood():
    bot = _make_bot()
    bot.handle_order_update = MagicMock()
    bot._ws_fill_events_max = 100  # tighten cap to keep test fast
    for order_id in range(2500):
        await bot._handle_private_ws_message(
            {
                "e": "ORDER_TRADE_UPDATE",
                "E": 1710008000000 + order_id,
                "o": {
                    "s": "BTCUSDT",
                    "S": "BUY",
                    "X": "NEW",
                    "i": order_id,
                    "q": "0.001",
                    "p": "70000.0",
                    "z": "0",
                    "l": "0",
                    "L": "0",
                    "T": 1710008000000 + order_id,
                },
            },
            "v3",
        )
    assert len(bot._ws_order_event_times) <= bot._ws_fill_events_max
    assert len(bot._aster_order_detail_cache) <= bot._ws_fill_events_max


@pytest.mark.asyncio
async def test_listen_key_expired_does_not_advance_last_message_ms(caplog):
    import logging as _logging

    bot = _make_bot()
    bot._ws_last_message_ms = 0
    secret_field = "DO_NOT_LOG_THIS_SECRET"
    with caplog.at_level(_logging.WARNING):
        await bot._handle_private_ws_message(
            {"e": "listenKeyExpired", "E": 1710006000000, "privateToken": secret_field},
            "v3",
        )
    assert bot._ws_last_message_ms == 0
    assert any("listen key expired" in rec.message.lower() for rec in caplog.records)
    assert secret_field not in caplog.text
    assert "privateToken" not in caplog.text


@pytest.mark.asyncio
async def test_account_update_partial_does_not_wipe_other_positions():
    bot = _make_bot()
    bot.balance = 0.0
    bot.handle_balance_update = AsyncMock()
    bot._ws_positions_cache = [
        {"symbol": "ETH/USDT:USDT", "position_side": "long", "size": 1.0, "price": 3000.0},
        {"symbol": "BTC/USDT:USDT", "position_side": "long", "size": 0.02, "price": 70000.0},
    ]
    bot._ws_positions_cache_ts = 1.0
    await bot._handle_private_ws_message(
        {
            "e": "ACCOUNT_UPDATE",
            "E": 1710004000100,
            "a": {
                "B": [{"a": "USDT", "wb": "42.5"}],
                "P": [{"s": "BTCUSDT", "pa": "0.03", "ep": "70100", "ps": "LONG"}],
            },
        },
        "v3",
    )
    symbols = {(p["symbol"], p["position_side"]): p for p in bot._ws_positions_cache}
    assert ("ETH/USDT:USDT", "long") in symbols
    assert symbols[("ETH/USDT:USDT", "long")]["size"] == 1.0
    assert symbols[("BTC/USDT:USDT", "long")]["size"] == 0.03


@pytest.mark.asyncio
async def test_account_update_close_removes_only_closed_symbol():
    bot = _make_bot()
    bot.balance = 0.0
    bot.handle_balance_update = AsyncMock()
    bot._ws_positions_cache = [
        {"symbol": "ETH/USDT:USDT", "position_side": "long", "size": 1.0, "price": 3000.0},
        {"symbol": "BTC/USDT:USDT", "position_side": "long", "size": 0.02, "price": 70000.0},
    ]
    bot._ws_positions_cache_ts = 1.0
    await bot._handle_private_ws_message(
        {
            "e": "ACCOUNT_UPDATE",
            "E": 1710004000200,
            "a": {
                "B": [{"a": "USDT", "wb": "42.5"}],
                "P": [{"s": "BTCUSDT", "pa": "0", "ep": "0", "ps": "LONG"}],
            },
        },
        "v3",
    )
    symbols = {(p["symbol"], p["position_side"]) for p in bot._ws_positions_cache}
    assert ("ETH/USDT:USDT", "long") in symbols
    assert ("BTC/USDT:USDT", "long") not in symbols


@pytest.mark.asyncio
async def test_handle_private_account_update_merges_partial_multi_asset_balances():
    bot = _make_bot()
    bot.balance = 0.0
    bot._aster_multi_assets_mode = True
    bot._ws_balance_assets_cache = {
        "USDF": {"asset": "USDF", "wb": "116.82105510"},
        "USDT": {"asset": "USDT", "wb": "0.02067084"},
        "USDC": {"asset": "USDC", "wb": "0.00000064"},
    }
    bot.handle_balance_update = AsyncMock()
    await bot._handle_private_ws_message(
        {
            "e": "ACCOUNT_UPDATE",
            "E": 1710004000000,
            "a": {
                "B": [{"a": "USDT", "wb": "0.12785969"}],
                "P": [{"s": "BTCUSDT", "pa": "0.02", "ep": "70100", "ps": "LONG", "up": "0.26642823"}],
            },
        },
        "v3",
    )
    expected_balance = 116.82105510 + 0.12785969 + 0.00000064 + 0.26642823
    assert bot._ws_balance_cache == pytest.approx(expected_balance)
    assert bot.balance == pytest.approx(expected_balance)
    assert bot._ws_balance_assets_cache["USDF"]["wb"] == "116.82105510"
    assert bot._ws_balance_assets_cache["USDT"]["wb"] == "0.12785969"
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


def test_next_timestamp_ms_is_strictly_monotonic_when_wall_clock_repeats():
    client = AsterRestClient(
        AsterConfiguration(
            api_user=ASTER_USER_INFO["api_user"],
            api_signer=ASTER_USER_INFO["api_signer"],
            api_private_key=ASTER_USER_INFO["api_private_key"],
        )
    )
    with patch("exchanges.aster_rest.time.time", return_value=1000.0):
        first = client._next_timestamp_ms()
        second = client._next_timestamp_ms()
        third = client._next_timestamp_ms()
    assert second == first + 1
    assert third == second + 1


@pytest.mark.asyncio
async def test_fetch_positions_filters_dust_with_position_side_both():
    bot = _make_bot()
    bot._ws_positions_cache = None
    bot._ws_positions_cache_ts = 0.0
    bot.cca = SimpleNamespace(
        fetch_position_risk=AsyncMock(
            return_value=[
                {"symbol": "BTCUSDT", "positionAmt": "0.0000000000001", "positionSide": "BOTH", "entryPrice": "0"},
                {"symbol": "ETHUSDT", "positionAmt": "0.5", "positionSide": "LONG", "entryPrice": "3000"},
            ]
        )
    )
    positions = await bot.fetch_positions()
    symbols = {(p["symbol"], p["position_side"]): p for p in positions}
    assert ("BTC/USDT:USDT", "long") not in symbols
    assert ("BTC/USDT:USDT", "short") not in symbols
    assert symbols[("ETH/USDT:USDT", "long")]["size"] == 0.5


def test_handle_public_ws_message_rejects_nan_bid():
    bot = _make_bot()
    bot._ws_tickers_cache = {}
    bot._ws_ticker_event_times = {}
    bot._ws_last_message_ms = 0
    payload = {
        "s": "BTCUSDT",
        "E": 1710007000000,
        "b": "nan",
        "a": "70000.5",
    }
    asyncio.run(bot._handle_public_ws_message(payload, "bookTicker"))
    assert "BTC/USDT:USDT" not in bot._ws_tickers_cache


def test_handle_public_ws_message_rejects_infinite_ask():
    bot = _make_bot()
    bot._ws_tickers_cache = {}
    bot._ws_ticker_event_times = {}
    bot._ws_last_message_ms = 0
    payload = {
        "s": "BTCUSDT",
        "E": 1710007000000,
        "b": "70000.0",
        "a": "inf",
    }
    asyncio.run(bot._handle_public_ws_message(payload, "bookTicker"))
    assert "BTC/USDT:USDT" not in bot._ws_tickers_cache


@pytest.mark.asyncio
async def test_cancel_all_open_orders_returns_list_response_unchanged():
    client = AsterRestClient(
        AsterConfiguration(
            api_user=ASTER_USER_INFO["api_user"],
            api_signer=ASTER_USER_INFO["api_signer"],
            api_private_key=ASTER_USER_INFO["api_private_key"],
        )
    )
    fake_list = [{"orderId": 1, "status": "CANCELED"}, {"orderId": 2, "status": "CANCELED"}]
    with patch.object(client, "_private_request_json", AsyncMock(return_value=fake_list)):
        result = await client.cancel_all_open_orders("BTC/USDT:USDT")
    assert result == fake_list


@pytest.mark.asyncio
async def test_account_config_update_invalidates_positions_cache_on_mode_flip(caplog):
    import logging as _logging

    bot = _make_bot()
    bot._aster_dual_side_position = False
    bot._ws_positions_cache = [
        {"symbol": "BTC/USDT:USDT", "position_side": "long", "size": 0.02, "price": 70000.0},
    ]
    bot._ws_positions_cache_ts = 5.0
    with caplog.at_level(_logging.WARNING):
        await bot._handle_private_ws_message(
            {
                "e": "ACCOUNT_CONFIG_UPDATE",
                "E": 1710008100000,
                "pm": True,
            },
            "v3",
        )
    assert bot._aster_dual_side_position is True
    assert bot._ws_positions_cache == []
    assert bot._ws_positions_cache_ts == 0.0
    assert any("position mode changed" in rec.message.lower() for rec in caplog.records)


@pytest.mark.asyncio
async def test_account_config_update_noop_when_mode_unchanged():
    bot = _make_bot()
    bot._aster_dual_side_position = True
    cached = [
        {"symbol": "BTC/USDT:USDT", "position_side": "long", "size": 0.02, "price": 70000.0},
    ]
    bot._ws_positions_cache = list(cached)
    bot._ws_positions_cache_ts = 5.0
    await bot._handle_private_ws_message(
        {"e": "ACCOUNT_CONFIG_UPDATE", "E": 1710008200000, "pm": True},
        "v3",
    )
    assert bot._aster_dual_side_position is True
    assert bot._ws_positions_cache == cached
    assert bot._ws_positions_cache_ts == 5.0


@pytest.mark.asyncio
async def test_keepalive_loop_survives_transient_failure(monkeypatch):
    from exchanges.aster_ws import AsterWebsocketManager

    config = AsterWebsocketConfig(keepalive_interval_seconds=1.0, forced_reconnect_seconds=86400.0)
    rest_client = SimpleNamespace(
        keepalive_listen_key=AsyncMock(side_effect=[RuntimeError("boom"), None, asyncio.CancelledError()])
    )
    mgr = AsterWebsocketManager(config, rest_client)
    mgr._listen_key = "fake-listen-key"

    sleep_calls: list[float] = []

    async def fake_sleep(seconds):
        sleep_calls.append(float(seconds))
        if len(sleep_calls) >= 4:
            mgr._stop.set()

    monkeypatch.setattr("exchanges.aster_ws.asyncio.sleep", fake_sleep)

    try:
        await mgr._keepalive_loop()
    except asyncio.CancelledError:
        pass

    assert rest_client.keepalive_listen_key.await_count >= 2
    assert any(abs(s - min(config.keepalive_interval_seconds, 300.0)) < 1e-6 for s in sleep_calls)
