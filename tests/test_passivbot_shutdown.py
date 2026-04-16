from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC_DIR = os.path.join(ROOT_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

import passivbot


class _DummyTask:
    def done(self):
        return False


def test_signal_handler_uses_active_bot_shutdown_task():
    dummy_bot = SimpleNamespace(
        stop_signal_received=False,
        _shutdown_task=None,
    )

    async def _shutdown():
        return None

    dummy_bot.shutdown_gracefully = _shutdown

    created = []

    def create_task(coro):
        created.append(coro)
        coro.close()
        return _DummyTask()

    loop = MagicMock()
    loop.create_task.side_effect = create_task

    original_bot = passivbot._ACTIVE_BOT
    try:
        passivbot._ACTIVE_BOT = dummy_bot
        original_get_event_loop = passivbot.asyncio.get_event_loop
        passivbot.asyncio.get_event_loop = lambda: loop
        passivbot.signal_handler(None, None)
    finally:
        passivbot._ACTIVE_BOT = original_bot
        passivbot.asyncio.get_event_loop = original_get_event_loop

    assert dummy_bot.stop_signal_received is True
    assert len(created) == 1
    loop.stop.assert_not_called()


def test_signal_handler_without_active_bot_stops_loop():
    loop = MagicMock()

    original_bot = passivbot._ACTIVE_BOT
    try:
        passivbot._ACTIVE_BOT = None
        original_get_event_loop = passivbot.asyncio.get_event_loop
        passivbot.asyncio.get_event_loop = lambda: loop
        passivbot.signal_handler(None, None)
    finally:
        passivbot._ACTIVE_BOT = original_bot
        passivbot.asyncio.get_event_loop = original_get_event_loop

    loop.call_soon_threadsafe.assert_called_once()
