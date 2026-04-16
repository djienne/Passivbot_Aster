"""
Aster live user-stream probe.

This is a manual diagnostic tool for investigating Aster private websocket
behavior with the current V3 wallet-signed listen-key flow.

It is intentionally not part of the normal pytest suite.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from typing import Any

import aiohttp
import aiohttp.connector as _aiohttp_connector

if sys.platform == "win32":
    _aiohttp_connector.DefaultResolver = aiohttp.ThreadedResolver

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SRC_DIR = os.path.join(ROOT_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from config_utils import format_config, load_config
from exchanges.aster_rest import AsterConfiguration, AsterRestClient
from procedures import load_user_info

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("aster-live-user-stream-probe")


def _ws_root(ws_url: str) -> str:
    ws_url = ws_url.rstrip("/")
    if ws_url.endswith("/ws"):
        return ws_url[:-3]
    return ws_url


def _key_prefix(value: str, keep: int = 12) -> str:
    if not value:
        return ""
    return value[:keep]


async def _probe_stream(
    client: AsterRestClient,
    config: AsterConfiguration,
    *,
    duration: float,
    immediate_keepalive: bool,
    keepalive_every: float,
) -> dict[str, Any]:
    listen_key = await client.create_listen_key()
    second_key = await client.create_listen_key()
    same_key = second_key == listen_key
    log.info(
        "listen key created prefix=%s second_prefix=%s same_key=%s",
        _key_prefix(listen_key),
        _key_prefix(second_key),
        same_key,
    )

    if immediate_keepalive:
        await client.keepalive_listen_key(listen_key)
        log.info("sent immediate keepalive for prefix=%s", _key_prefix(listen_key))

    ws_url = f"{_ws_root(config.ws_url)}/ws/{listen_key}"
    log.info("connecting to %s", ws_url)

    seen_messages: list[dict[str, Any]] = []
    expired_payload: dict[str, Any] | None = None
    started = time.monotonic()
    next_keepalive = started + keepalive_every if keepalive_every > 0.0 else float("inf")
    ws = None
    try:
        await client.ensure_session()
        ws = await client.session.ws_connect(
            ws_url,
            heartbeat=20.0,
            autoping=True,
        )
        while time.monotonic() - started < duration:
            timeout = min(5.0, max(0.1, duration - (time.monotonic() - started)))
            try:
                msg = await asyncio.wait_for(ws.receive(), timeout=timeout)
            except asyncio.TimeoutError:
                if time.monotonic() >= next_keepalive:
                    await client.keepalive_listen_key(listen_key)
                    log.info(
                        "sent periodic keepalive for prefix=%s age=%.2fs",
                        _key_prefix(listen_key),
                        time.monotonic() - started,
                    )
                    next_keepalive = time.monotonic() + keepalive_every
                continue

            if msg.type == aiohttp.WSMsgType.TEXT:
                age = time.monotonic() - started
                try:
                    payload = json.loads(msg.data)
                except json.JSONDecodeError:
                    payload = {"_raw_text": msg.data}
                seen_messages.append({"age_s": age, "payload": payload})
                log.info("WS text age=%.2fs payload=%s", age, payload)
                if isinstance(payload, dict) and payload.get("e") == "listenKeyExpired":
                    expired_payload = payload
                    break
            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                log.warning("WS closed type=%s extra=%s", msg.type, getattr(msg, "data", None))
                break

            if time.monotonic() >= next_keepalive:
                await client.keepalive_listen_key(listen_key)
                log.info(
                    "sent periodic keepalive for prefix=%s age=%.2fs",
                    _key_prefix(listen_key),
                    time.monotonic() - started,
                )
                next_keepalive = time.monotonic() + keepalive_every
    finally:
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass
        try:
            await client.close_listen_key(listen_key)
        except Exception as exc:
            log.warning("close listen key failed prefix=%s error=%s", _key_prefix(listen_key), exc)

    summary = {
        "listen_key_prefix": _key_prefix(listen_key),
        "same_key_on_second_post": same_key,
        "message_count": len(seen_messages),
        "expired_payload": expired_payload,
        "messages": seen_messages,
    }
    return summary


async def run_probe(
    config_path: str,
    *,
    duration: float,
    immediate_keepalive: bool,
    keepalive_every: float,
) -> None:
    log.info("loading config %s", config_path)
    config = format_config(load_config(config_path, verbose=False), verbose=False)
    user = config["live"]["user"]
    user_info = load_user_info(user)
    credentials = AsterConfiguration.from_user_info(user_info)
    client = AsterRestClient(credentials)
    try:
        summary = await _probe_stream(
            client,
            credentials,
            duration=duration,
            immediate_keepalive=immediate_keepalive,
            keepalive_every=keepalive_every,
        )
    finally:
        await client.close()

    print("USER_STREAM_PROBE_SUMMARY")
    print(json.dumps(summary, indent=2, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser(description="Manual Aster live user-stream probe")
    parser.add_argument(
        "config_path",
        nargs="?",
        default="configs/config_hype_aster.json",
        help="Path to config file",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=20.0,
        help="How long to watch the user stream after connect",
    )
    parser.add_argument(
        "--keepalive-every",
        type=float,
        default=10.0,
        help="Send a periodic keepalive every N seconds during the probe",
    )
    parser.add_argument(
        "--no-immediate-keepalive",
        action="store_true",
        help="Do not send an immediate keepalive right after creating the listen key",
    )
    parser.add_argument(
        "--confirm-live",
        action="store_true",
        help="Required safety flag for live-account user-stream probing",
    )
    args = parser.parse_args()

    if not args.confirm_live:
        raise SystemExit("Refusing to run live user-stream probe without --confirm-live")

    asyncio.run(
        run_probe(
            args.config_path,
            duration=args.duration,
            immediate_keepalive=not args.no_immediate_keepalive,
            keepalive_every=args.keepalive_every,
        )
    )


if __name__ == "__main__":
    main()
