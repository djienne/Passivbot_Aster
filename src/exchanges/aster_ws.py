from __future__ import annotations

import asyncio
import json
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


@dataclass(frozen=True)
class AsterWebsocketConfig:
    ws_url: str = ASTER_DEFAULT_WS_URL
    keepalive_interval_seconds: float = 1800.0
    forced_reconnect_seconds: float = 23 * 60 * 60
    private_stale_seconds: float = 30.0
    public_stale_seconds: float = 10.0

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
        return listen_key, "v3"

    async def _keepalive_loop(self) -> None:
        while not self._stop.is_set() and self._listen_key:
            await asyncio.sleep(self.config.keepalive_interval_seconds)
            if self._stop.is_set() or not self._listen_key:
                return
            await self.rest_client.keepalive_listen_key(self._listen_key)

    async def _run_private_loop(
        self,
        private_message_callback,
        private_reconnect_callback,
    ) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            keepalive_task: Optional[asyncio.Task] = None
            start_s = time.monotonic()
            try:
                listen_key, mode = await self._obtain_listen_key()
                self._listen_key = listen_key
                self._listen_key_mode = mode
                self._private_ws = await self.rest_client.session.ws_connect(
                    f"{self._ws_root()}/ws/{listen_key}",
                    heartbeat=20.0,
                    autoping=True,
                )
                backoff = 1.0
                self._last_private_message_s = time.monotonic()
                keepalive_task = asyncio.create_task(self._keepalive_loop())
                while not self._stop.is_set():
                    if time.monotonic() - start_s >= self.config.forced_reconnect_seconds:
                        raise TimeoutError("aster private websocket forced reconnect")
                    try:
                        msg = await asyncio.wait_for(self._private_ws.receive(), timeout=5.0)
                    except asyncio.TimeoutError:
                        if (
                            time.monotonic() - self._last_private_message_s
                            >= self.config.private_stale_seconds
                        ):
                            raise TimeoutError("aster private websocket stale")
                        continue
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        self._last_private_message_s = time.monotonic()
                        payload = json.loads(msg.data)
                        await self._call_callback(private_message_callback, payload, mode)
                        if payload.get("e") == "listenKeyExpired":
                            raise AsterListenKeyExpired("listen key expired")
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        raise ConnectionError("aster private websocket closed")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self._call_callback(private_reconnect_callback, exc, self._listen_key_mode)
                await asyncio.sleep(backoff + random.uniform(0.0, backoff * 0.25))
                backoff = min(backoff * 2.0, 30.0)
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
                if self._stop.is_set():
                    return

    async def _run_public_loop(
        self,
        public_message_callback,
        public_reconnect_callback,
        symbols_provider: Callable[[], list[str]],
    ) -> None:
        backoff = 1.0
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
                self._public_ws = await self.rest_client.session.ws_connect(
                    ws_url,
                    heartbeat=20.0,
                    autoping=True,
                )
                backoff = 1.0
                self._last_public_message_s = time.monotonic()
                while not self._stop.is_set():
                    if time.monotonic() - start_s >= self.config.forced_reconnect_seconds:
                        raise TimeoutError("aster public websocket forced reconnect")
                    try:
                        msg = await asyncio.wait_for(self._public_ws.receive(), timeout=5.0)
                    except asyncio.TimeoutError:
                        if (
                            time.monotonic() - self._last_public_message_s
                            >= self.config.public_stale_seconds
                        ):
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
                await self._call_callback(public_reconnect_callback, exc)
                await asyncio.sleep(backoff + random.uniform(0.0, backoff * 0.25))
                backoff = min(backoff * 2.0, 30.0)
            finally:
                if self._public_ws is not None:
                    try:
                        await self._public_ws.close()
                    except Exception:
                        pass
                    self._public_ws = None
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
