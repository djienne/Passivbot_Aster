from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

import aiohttp

from exchanges.aster_rest import ASTER_DEFAULT_WS_URL, AsterConfiguration


class AsterListenKeyExpired(Exception):
    pass


def _is_awaitable(value: Any) -> bool:
    return hasattr(value, "__await__")


def _exponential_backoff_delay(
    attempt: int,
    *,
    base: float,
    cap: float,
    jitter_fraction: float,
) -> float:
    capped = min(float(cap), float(base) * (2 ** max(int(attempt), 0)))
    if jitter_fraction <= 0.0:
        return capped
    return capped + random.uniform(0.0, capped * jitter_fraction)


@dataclass(frozen=True)
class AsterWebsocketConfig:
    ws_url: str = ASTER_DEFAULT_WS_URL
    keepalive_interval_seconds: float = 1800.0
    immediate_keepalive_on_connect: bool = True
    forced_reconnect_seconds: float = 23 * 60 * 60
    private_stale_seconds: Optional[float] = None
    public_stale_seconds: float = 60.0
    receive_timeout_seconds: float = 5.0
    heartbeat_seconds: float = 20.0
    backoff_initial_seconds: float = 1.0
    backoff_max_seconds: float = 60.0
    backoff_jitter_fraction: float = 0.25
    backoff_reset_after_seconds: float = 60.0

    @classmethod
    def from_credentials(cls, credentials: AsterConfiguration) -> "AsterWebsocketConfig":
        return cls(
            ws_url=credentials.ws_url,
        )

    @property
    def private_stream_mode(self) -> str:
        return "v3"


class AsterWebsocketManager:
    """Aster private/public websocket supervisor with reconnect and keepalive."""

    def __init__(self, config: AsterWebsocketConfig, rest_client) -> None:
        self.config = config
        self.rest_client = rest_client
        self.started = False
        self._stop = asyncio.Event()
        self._private_ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._public_ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._listen_key: str = ""
        self._listen_key_mode: str = self.config.private_stream_mode
        self._last_private_message_s = 0.0
        self._last_public_message_s = 0.0
        self._private_failures = 0
        self._public_failures = 0
        self._private_connected_at_s = 0.0
        self._public_connected_at_s = 0.0

    def describe_private_stream_plan(self) -> dict[str, Any]:
        return {
            "mode": "v3",
            "ws_url": self.config.ws_url,
        }

    def _ws_root(self) -> str:
        ws_url = self.config.ws_url.rstrip("/")
        if ws_url.endswith("/ws"):
            return ws_url[: -3]
        return ws_url

    async def _call_callback(self, callback, *args) -> None:
        if callback is None:
            return
        result = callback(*args)
        if _is_awaitable(result):
            await result

    async def _obtain_listen_key(self) -> tuple[str, str]:
        listen_key = await self.rest_client.create_listen_key()
        if self.config.immediate_keepalive_on_connect and listen_key:
            await self.rest_client.keepalive_listen_key(listen_key)
            logging.info(
                "Aster private WS immediate keepalive sent for listen key prefix=%s",
                listen_key[:12],
            )
        return listen_key, "v3"

    async def _keepalive_loop(self) -> None:
        while not self._stop.is_set() and self._listen_key:
            await asyncio.sleep(self.config.keepalive_interval_seconds)
            if self._stop.is_set() or not self._listen_key:
                return
            await self.rest_client.keepalive_listen_key(self._listen_key)

    def _private_stream_is_stale(self) -> bool:
        stale_seconds = self.config.private_stale_seconds
        if stale_seconds is None or stale_seconds <= 0.0:
            return False
        return time.monotonic() - self._last_private_message_s >= stale_seconds

    def _public_stream_is_stale(self) -> bool:
        stale_seconds = self.config.public_stale_seconds
        if stale_seconds is None or stale_seconds <= 0.0:
            return False
        return time.monotonic() - self._last_public_message_s >= stale_seconds

    @staticmethod
    def _raise_if_task_failed(task: Optional[asyncio.Task], label: str) -> None:
        if task is None or not task.done() or task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            raise ConnectionError(f"{label} failed: {exc}") from exc

    async def _run_private_loop(
        self,
        private_message_callback,
        private_reconnect_callback,
    ) -> None:
        while not self._stop.is_set():
            keepalive_task: Optional[asyncio.Task] = None
            start_s = time.monotonic()
            try:
                listen_key, mode = await self._obtain_listen_key()
                self._listen_key = listen_key
                self._listen_key_mode = mode
                logging.info("Aster private WS connecting: %s", f"{self._ws_root()}/ws/{listen_key}")
                self._private_ws = await self.rest_client.session.ws_connect(
                    f"{self._ws_root()}/ws/{listen_key}",
                    heartbeat=self.config.heartbeat_seconds,
                    autoping=True,
                )
                self._last_private_message_s = time.monotonic()
                self._private_connected_at_s = self._last_private_message_s
                keepalive_task = asyncio.create_task(self._keepalive_loop())
                logging.info("Aster private WS connected")
                while not self._stop.is_set():
                    if (
                        self._private_failures > 0
                        and time.monotonic() - self._private_connected_at_s
                        >= self.config.backoff_reset_after_seconds
                    ):
                        logging.info(
                            "Aster private WS backoff reset after %.1fs of stable connection",
                            time.monotonic() - self._private_connected_at_s,
                        )
                        self._private_failures = 0
                    self._raise_if_task_failed(keepalive_task, "aster private websocket keepalive")
                    if time.monotonic() - start_s >= self.config.forced_reconnect_seconds:
                        raise TimeoutError("aster private websocket forced reconnect")
                    try:
                        msg = await asyncio.wait_for(
                            self._private_ws.receive(),
                            timeout=self.config.receive_timeout_seconds,
                        )
                    except asyncio.TimeoutError:
                        self._raise_if_task_failed(
                            keepalive_task, "aster private websocket keepalive"
                        )
                        if self._private_stream_is_stale():
                            raise TimeoutError("aster private websocket stale")
                        continue
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        self._last_private_message_s = time.monotonic()
                        payload = json.loads(msg.data)
                        await self._call_callback(private_message_callback, payload, mode)
                        if payload.get("e") == "listenKeyExpired":
                            age_s = max(0.0, time.monotonic() - self._private_connected_at_s)
                            logging.warning(
                                "Aster private WS received listenKeyExpired after %.2fs | payload=%s",
                                age_s,
                                payload,
                            )
                            raise AsterListenKeyExpired(
                                f"listen key expired after {age_s:.2f}s"
                            )
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        raise ConnectionError("aster private websocket closed")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._private_failures += 1
                delay = _exponential_backoff_delay(
                    self._private_failures - 1,
                    base=self.config.backoff_initial_seconds,
                    cap=self.config.backoff_max_seconds,
                    jitter_fraction=self.config.backoff_jitter_fraction,
                )
                logging.warning(
                    "Aster private WS reconnect scheduled in %.1fs after %s consecutive failure(s): %s",
                    delay,
                    self._private_failures,
                    exc,
                )
                await self._call_callback(private_reconnect_callback, exc, self._listen_key_mode)
                await asyncio.sleep(delay)
            finally:
                if keepalive_task is not None:
                    keepalive_task.cancel()
                    try:
                        await keepalive_task
                    except BaseException:
                        pass
                if self._private_ws is not None:
                    try:
                        await self._private_ws.close()
                    except Exception:
                        pass
                    self._private_ws = None
                if self._listen_key:
                    try:
                        await self.rest_client.close_listen_key(self._listen_key)
                    except Exception:
                        pass
                    self._listen_key = ""
                self._private_connected_at_s = 0.0
                if self._stop.is_set():
                    return

    async def _run_public_loop(
        self,
        public_message_callback,
        public_reconnect_callback,
        symbols_provider: Callable[[], list[str]],
    ) -> None:
        while not self._stop.is_set():
            start_s = time.monotonic()
            try:
                symbols = [s for s in symbols_provider() if s]
                if not symbols:
                    await asyncio.sleep(5.0)
                    continue
                streams = "/".join(
                    f"{symbol.split('/', 1)[0].lower()}{symbol.split('/', 1)[1].split(':', 1)[0].replace('/', '').lower()}@bookTicker"
                    for symbol in symbols
                )
                ws_url = f"{self._ws_root()}/stream?streams={streams}"
                logging.info("Aster public WS connecting: %s", ws_url)
                self._public_ws = await self.rest_client.session.ws_connect(
                    ws_url,
                    heartbeat=self.config.heartbeat_seconds,
                    autoping=True,
                )
                self._last_public_message_s = time.monotonic()
                self._public_connected_at_s = self._last_public_message_s
                logging.info("Aster public WS connected for %s stream(s)", len(symbols))
                while not self._stop.is_set():
                    if (
                        self._public_failures > 0
                        and time.monotonic() - self._public_connected_at_s
                        >= self.config.backoff_reset_after_seconds
                    ):
                        logging.info(
                            "Aster public WS backoff reset after %.1fs of stable connection",
                            time.monotonic() - self._public_connected_at_s,
                        )
                        self._public_failures = 0
                    if time.monotonic() - start_s >= self.config.forced_reconnect_seconds:
                        raise TimeoutError("aster public websocket forced reconnect")
                    try:
                        msg = await asyncio.wait_for(
                            self._public_ws.receive(),
                            timeout=self.config.receive_timeout_seconds,
                        )
                    except asyncio.TimeoutError:
                        if self._public_stream_is_stale():
                            raise TimeoutError("aster public websocket stale")
                        continue
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        self._last_public_message_s = time.monotonic()
                        payload = json.loads(msg.data)
                        if isinstance(payload, dict) and "data" in payload:
                            await self._call_callback(
                                public_message_callback,
                                payload.get("data"),
                                payload.get("stream"),
                            )
                        else:
                            await self._call_callback(public_message_callback, payload, None)
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        raise ConnectionError("aster public websocket closed")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._public_failures += 1
                delay = _exponential_backoff_delay(
                    self._public_failures - 1,
                    base=self.config.backoff_initial_seconds,
                    cap=self.config.backoff_max_seconds,
                    jitter_fraction=self.config.backoff_jitter_fraction,
                )
                logging.warning(
                    "Aster public WS reconnect scheduled in %.1fs after %s consecutive failure(s): %s",
                    delay,
                    self._public_failures,
                    exc,
                )
                await self._call_callback(public_reconnect_callback, exc)
                await asyncio.sleep(delay)
            finally:
                if self._public_ws is not None:
                    try:
                        await self._public_ws.close()
                    except Exception:
                        pass
                    self._public_ws = None
                self._public_connected_at_s = 0.0
                if self._stop.is_set():
                    return

    async def run(
        self,
        *,
        private_message_callback,
        public_message_callback,
        symbols_provider: Callable[[], list[str]],
        private_reconnect_callback=None,
        public_reconnect_callback=None,
    ) -> None:
        self.started = True
        self._stop.clear()
        await self.rest_client.ensure_session()
        tasks = [
            asyncio.create_task(
                self._run_private_loop(private_message_callback, private_reconnect_callback)
            ),
            asyncio.create_task(
                self._run_public_loop(
                    public_message_callback,
                    public_reconnect_callback,
                    symbols_provider,
                )
            ),
        ]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                try:
                    await task
                except BaseException:
                    pass
            self.started = False

    async def close(self) -> None:
        self._stop.set()
        if self._private_ws is not None:
            try:
                await self._private_ws.close()
            except Exception:
                pass
        if self._public_ws is not None:
            try:
                await self._public_ws.close()
            except Exception:
                pass
        if self._listen_key:
            await self.rest_client.close_listen_key(self._listen_key)
            self._listen_key = ""
        self.started = False
