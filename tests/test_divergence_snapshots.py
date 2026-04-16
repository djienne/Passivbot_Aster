import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from passivbot import Passivbot


def _mk_bot() -> Passivbot:
    bot = Passivbot.__new__(Passivbot)
    bot.exchange = "aster"
    bot.user = "aster_01"
    bot.config = {
        "live": {"max_n_creations_per_batch": 3},
        "bot": {
            "long": {"wallet_exposure_limit": 1.0, "total_wallet_exposure_limit": 1.0, "n_positions": 1},
            "short": {"wallet_exposure_limit": 0.0, "total_wallet_exposure_limit": 0.0, "n_positions": 0},
        },
    }
    bot.balance = 100.0
    bot._previous_balance = 100.0
    bot.active_symbols = ["HYPE/USDT:USDT"]
    bot.positions = {
        "HYPE/USDT:USDT": {
            "long": {"size": 0.5, "price": 44.5},
            "short": {"size": 0.0, "price": 0.0},
        }
    }
    bot.fetched_positions = [
        {"symbol": "HYPE/USDT:USDT", "position_side": "long", "size": 0.5, "price": 44.5}
    ]
    bot.open_orders = {"HYPE/USDT:USDT": []}
    bot.fetched_open_orders = []
    bot.trailing_prices = {"HYPE/USDT:USDT": {"long": {}, "short": {}}}
    bot.PB_modes = {"long": {"HYPE/USDT:USDT": "normal"}, "short": {"HYPE/USDT:USDT": "manual"}}
    bot.approved_coins = {"long": {"HYPE/USDT:USDT"}, "short": set()}
    bot.ignored_coins = {"long": set(), "short": set()}
    bot.approved_coins_minus_ignored_coins = {"long": {"HYPE/USDT:USDT"}, "short": set()}
    bot.effective_min_cost = {"HYPE/USDT:USDT": 5.0}
    bot.recent_order_executions = []
    bot.recent_order_cancellations = []
    bot.state_change_detected_by_symbol = set()
    bot.qty_steps = {"HYPE/USDT:USDT": 0.01}
    bot.price_steps = {"HYPE/USDT:USDT": 0.001}
    bot.min_qtys = {"HYPE/USDT:USDT": 0.01}
    bot.min_costs = {"HYPE/USDT:USDT": 5.0}
    bot.c_mults = {"HYPE/USDT:USDT": 1.0}
    bot.cm = SimpleNamespace(_cache={})
    bot.tickers = {"HYPE/USDT:USDT": {"bid": 44.6, "ask": 44.7, "last": 44.65}}
    bot._ws_tickers_cache = {"HYPE/USDT:USDT": {"bid": 44.6, "ask": 44.7, "last": 44.65}}
    bot._ws_fill_events_cache = {}
    bot._health_ws_reconnects = 0
    bot._ws_watchdog_backoff = 120_000
    bot._health_errors = 0
    bot._last_orchestrator_snapshot = {"orchestrator_output": {"orders": []}}
    bot._last_replay_snapshot = {"symbols": ["HYPE/USDT:USDT"]}
    bot._last_orchestrator_snapshot_ts = 1
    bot._debug_snapshot_seq = 0
    bot._debug_snapshot_last_ms = {}
    bot._debug_snapshot_lock = asyncio.Lock()
    bot.debug_mode = False
    bot.quote = "USDT"
    return bot


@pytest.mark.asyncio
async def test_maybe_capture_divergence_snapshot_writes_file(tmp_path: Path):
    bot = _mk_bot()
    bot._debug_snapshot_dir = str(tmp_path) + "/"

    path = await bot.maybe_capture_divergence_snapshot(
        "fill_event",
        extra={"foo": "bar"},
        force=True,
    )

    assert path is not None
    saved = Path(path)
    assert saved.exists()
    payload = json.loads(saved.read_text())
    assert payload["meta"]["trigger"] == "fill_event"
    assert payload["extra"] == {"foo": "bar"}
    assert payload["last_orchestrator_snapshot"]["orchestrator_output"]["orders"] == []


@pytest.mark.asyncio
async def test_handle_balance_update_captures_suspicious_ws_change():
    bot = _mk_bot()
    bot.balance = 0.2
    bot._previous_balance = 117.0
    bot.calc_upnl_sum = AsyncMock(return_value=0.0)
    bot.maybe_capture_divergence_snapshot = AsyncMock()

    await bot.handle_balance_update(source="WS:v3")

    bot.maybe_capture_divergence_snapshot.assert_awaited_once()
    trigger = bot.maybe_capture_divergence_snapshot.await_args.args[0]
    assert trigger == "suspicious_balance_update"


@pytest.mark.asyncio
async def test_execute_orders_parent_captures_order_post_burst():
    bot = _mk_bot()
    bot.add_to_recent_order_executions = lambda order: bot.recent_order_executions.append(order)
    bot.log_order_action = lambda *args, **kwargs: None
    bot._log_order_action_summary = lambda *args, **kwargs: None
    bot.execute_orders = AsyncMock(return_value=[{"id": "abc"}])
    bot.did_create_order = lambda executed: True
    bot.add_new_order = lambda *args, **kwargs: None
    bot._health_orders_placed = 0
    bot.maybe_capture_divergence_snapshot = AsyncMock()

    orders = [
        {
            "symbol": "HYPE/USDT:USDT",
            "side": "buy",
            "position_side": "long",
            "qty": 0.55,
            "price": 43.684,
            "type": "limit",
        }
    ]
    out = await bot.execute_orders_parent(orders)

    assert out == [{"id": "abc", **orders[0]}]
    bot.maybe_capture_divergence_snapshot.assert_awaited_once()
    trigger = bot.maybe_capture_divergence_snapshot.await_args.args[0]
    assert trigger == "order_post_burst"
