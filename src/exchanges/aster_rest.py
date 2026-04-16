from __future__ import annotations

import asyncio
import importlib
import json
import math
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

import aiohttp


ASTER_DEFAULT_BASE_URL = "https://fapi.asterdex.com"
ASTER_DEFAULT_WS_URL = "wss://fstream.asterdex.com/ws"
ASTER_RECV_WINDOW = 50_000
ASTER_STABLE_ASSETS = {"USDT", "USDC", "USDF"}

ASTER_EXCHANGE_INFO_PATHS = ("/fapi/v3/exchangeInfo",)
ASTER_KLINES_PATHS = ("/fapi/v3/klines",)
ASTER_TIME_PATHS = ("/fapi/v3/time",)
ASTER_BOOK_TICKER_PATHS = ("/fapi/v3/ticker/bookTicker",)
ASTER_ACCOUNT_PATHS = ("/fapi/v3/account",)
ASTER_BALANCE_PATHS = ("/fapi/v3/balance",)
ASTER_POSITION_RISK_PATHS = ("/fapi/v3/positionRisk",)
ASTER_ORDER_PATHS = ("/fapi/v3/order",)
ASTER_OPEN_ORDER_PATHS = ("/fapi/v3/openOrder",)
ASTER_OPEN_ORDERS_PATHS = ("/fapi/v3/openOrders",)
ASTER_ALL_ORDERS_PATHS = ("/fapi/v3/allOrders",)
ASTER_ALL_OPEN_ORDERS_PATHS = ("/fapi/v3/allOpenOrders",)
ASTER_BATCH_ORDERS_PATHS = ("/fapi/v3/batchOrders",)
ASTER_USER_TRADES_PATHS = ("/fapi/v3/userTrades",)
ASTER_LEVERAGE_PATHS = ("/fapi/v3/leverage",)
ASTER_MARGIN_TYPE_PATHS = ("/fapi/v3/marginType",)
ASTER_POSITION_MODE_PATHS = ("/fapi/v3/positionSide/dual",)
ASTER_MULTI_ASSETS_MODE_PATHS = ("/fapi/v3/multiAssetsMargin",)
ASTER_LISTEN_KEY_PATHS = ("/fapi/v3/listenKey",)

TIMEFRAME_TO_ASTER_INTERVAL = {
    "1m": "1m",
    "3m": "3m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "2h": "2h",
    "4h": "4h",
    "6h": "6h",
    "8h": "8h",
    "12h": "12h",
    "1d": "1d",
    "3d": "3d",
    "1w": "1w",
    "1M": "1M",
}


class AsterAPIError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status: Optional[int] = None,
        code: Optional[int] = None,
        path: str = "",
        payload: Any = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.path = path
        self.payload = payload


def _safe_float(value: Any, default: float = 0.0) -> float:
    if value in (None, ""):
        return float(default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _safe_int(value: Any, default: int = 0) -> int:
    if value in (None, ""):
        return int(default)
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


def _safe_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _stringify_number(value: Any) -> str:
    text = format(Decimal(str(value)), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _step_from_precision_digits(value: Any) -> float:
    digits = _safe_int(value, default=-1)
    if digits < 0:
        return 0.0
    return float(Decimal("1").scaleb(-digits))


def _normalize_step(value: Any, fallback_digits: Any = None) -> float:
    if value not in (None, ""):
        try:
            step = float(Decimal(str(value)))
            if step > 0.0:
                return step
        except (InvalidOperation, TypeError, ValueError):
            pass
    return _step_from_precision_digits(fallback_digits)


def _extract_payload(payload: Any) -> Any:
    if isinstance(payload, dict) and "data" in payload and isinstance(payload["data"], (dict, list)):
        return payload["data"]
    return payload


def _extract_filter(filters: list[dict], filter_type: str) -> dict[str, Any]:
    for entry in filters or []:
        if entry.get("filterType") == filter_type:
            return entry
    return {}


def _normalize_for_signature(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _normalize_for_signature(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        normalized = []
        for item in value:
            if isinstance(item, dict):
                normalized.append(_normalize_for_signature(item))
            elif isinstance(item, bool):
                normalized.append("true" if item else "false")
            elif isinstance(item, (int, float, Decimal)) and not isinstance(item, bool):
                normalized.append(_stringify_number(item))
            else:
                normalized.append(str(item))
        return normalized
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return _stringify_number(value)
    return str(value)


def _trim_dict(my_dict: dict[str, Any]) -> dict[str, str]:
    trimmed: dict[str, str] = {}
    for key, value in my_dict.items():
        if isinstance(value, list):
            trimmed[key] = json.dumps(_normalize_for_signature(value), separators=(",", ":"))
        elif isinstance(value, dict):
            trimmed[key] = json.dumps(
                _normalize_for_signature(value),
                sort_keys=True,
                separators=(",", ":"),
            )
        else:
            normalized = _normalize_for_signature(value)
            trimmed[key] = normalized if isinstance(normalized, str) else str(normalized)
    return trimmed


def _extract_error_details(payload: Any, fallback_text: str = "") -> tuple[Optional[int], str]:
    if isinstance(payload, dict):
        code = None
        for field in ("code", "retCode"):
            if field in payload:
                code = _safe_int(payload.get(field), default=None)
                break
        for field in ("msg", "message", "retMsg", "error"):
            if field in payload and payload.get(field) not in (None, ""):
                return code, str(payload.get(field))
        return code, fallback_text
    return None, fallback_text


def passivbot_symbol_to_aster_symbol(symbol: str) -> str:
    if "/" not in symbol:
        return symbol.upper()
    base, rest = symbol.split("/", 1)
    quote = rest.split(":", 1)[0]
    return f"{base}{quote}".upper()


def aster_symbol_to_passivbot_symbol(
    raw_symbol: str,
    *,
    base_asset: str = "",
    quote_asset: str = "",
    margin_asset: str = "",
) -> str:
    raw_symbol = str(raw_symbol or "").upper()
    base_asset = str(base_asset or "").upper()
    quote_asset = str(quote_asset or "").upper()
    settle_asset = str(margin_asset or quote_asset or "").upper()
    if "/" in raw_symbol:
        return raw_symbol
    if not raw_symbol:
        return ""
    if not quote_asset:
        for candidate in sorted(ASTER_STABLE_ASSETS, key=len, reverse=True):
            if raw_symbol.endswith(candidate):
                quote_asset = candidate
                if not settle_asset:
                    settle_asset = candidate
                break
    if not base_asset and quote_asset and raw_symbol.endswith(quote_asset):
        base_asset = raw_symbol[: -len(quote_asset)]
    if not base_asset:
        return raw_symbol
    if not settle_asset:
        settle_asset = quote_asset
    return f"{base_asset}/{quote_asset}:{settle_asset}"


def translate_aster_symbol_info(symbol_info: dict[str, Any]) -> Optional[dict[str, Any]]:
    raw_symbol = str(symbol_info.get("symbol") or "").upper()
    if not raw_symbol:
        return None
    base_asset = str(symbol_info.get("baseAsset") or "").upper()
    quote_asset = str(symbol_info.get("quoteAsset") or symbol_info.get("marginAsset") or "").upper()
    margin_asset = str(symbol_info.get("marginAsset") or quote_asset).upper()
    normalized_symbol = aster_symbol_to_passivbot_symbol(
        raw_symbol,
        base_asset=base_asset,
        quote_asset=quote_asset,
        margin_asset=margin_asset,
    )
    filters = symbol_info.get("filters", [])
    price_filter = _extract_filter(filters, "PRICE_FILTER")
    lot_size_filter = _extract_filter(filters, "LOT_SIZE")
    market_lot_size_filter = _extract_filter(filters, "MARKET_LOT_SIZE")
    min_notional_filter = _extract_filter(filters, "MIN_NOTIONAL") or _extract_filter(
        filters, "NOTIONAL"
    )
    price_step = _normalize_step(
        price_filter.get("tickSize"),
        fallback_digits=symbol_info.get("pricePrecision"),
    )
    qty_step = _normalize_step(
        lot_size_filter.get("stepSize") or market_lot_size_filter.get("stepSize"),
        fallback_digits=symbol_info.get("quantityPrecision"),
    )
    min_qty = _safe_float(
        lot_size_filter.get("minQty") or market_lot_size_filter.get("minQty") or qty_step
    )
    min_cost = _safe_float(
        min_notional_filter.get("notional") or min_notional_filter.get("minNotional")
    )
    maker_fee = _safe_float(
        symbol_info.get("makerCommissionRate")
        or symbol_info.get("makerFeeRate")
        or symbol_info.get("maker")
        or 0.0002
    )
    taker_fee = _safe_float(
        symbol_info.get("takerCommissionRate")
        or symbol_info.get("takerFeeRate")
        or symbol_info.get("taker")
        or 0.0004
    )
    max_leverage = _safe_int(
        symbol_info.get("maxLeverage")
        or symbol_info.get("max_leverage")
        or symbol_info.get("initialLeverage")
    )
    min_price = _safe_float(price_filter.get("minPrice"))
    max_price = _safe_float(price_filter.get("maxPrice"))
    status = str(symbol_info.get("status") or "").upper()
    contract_type = str(symbol_info.get("contractType") or symbol_info.get("type") or "").upper()
    is_swap = "PERPETUAL" in contract_type or contract_type in {"SWAP", ""}
    is_linear = quote_asset in ASTER_STABLE_ASSETS and margin_asset in ASTER_STABLE_ASSETS
    info = dict(symbol_info)
    if max_leverage > 0:
        info["maxLeverage"] = max_leverage
    return {
        "id": raw_symbol,
        "symbol": normalized_symbol,
        "base": base_asset,
        "baseId": base_asset,
        "baseName": base_asset,
        "quote": quote_asset,
        "quoteId": quote_asset,
        "settle": margin_asset,
        "settleId": margin_asset,
        "type": "swap",
        "swap": bool(is_swap),
        "future": False,
        "spot": False,
        "option": False,
        "active": status in {"TRADING", "ENABLED", "LISTED"},
        "linear": bool(is_linear),
        "inverse": False,
        "contract": True,
        "contractSize": _safe_float(symbol_info.get("contractSize"), default=1.0) or 1.0,
        "maker": maker_fee,
        "taker": taker_fee,
        "precision": {"amount": qty_step, "price": price_step},
        "limits": {
            "amount": {"min": min_qty, "max": None},
            "price": {"min": min_price, "max": max_price},
            "cost": {"min": min_cost, "max": None},
        },
        "info": info,
    }


def translate_aster_exchange_info(exchange_info: dict[str, Any]) -> dict[str, dict[str, Any]]:
    payload = _extract_payload(exchange_info)
    symbols = payload.get("symbols", []) if isinstance(payload, dict) else []
    markets: dict[str, dict[str, Any]] = {}
    for symbol_info in symbols:
        market = translate_aster_symbol_info(symbol_info)
        if market is not None:
            markets[market["symbol"]] = market
    return markets


def _load_signing_dependencies():
    try:
        Web3 = importlib.import_module("web3").Web3
        Account = importlib.import_module("eth_account").Account
        encode_defunct = importlib.import_module("eth_account.messages").encode_defunct
        encode = importlib.import_module("eth_abi").encode
    except Exception as exc:
        raise RuntimeError(
            "Aster private REST requires the 'web3', 'eth-account', and 'eth-abi' packages."
        ) from exc
    return Web3, Account, encode_defunct, encode


@dataclass(frozen=True)
class AsterConfiguration:
    api_user: str = ""
    api_signer: str = ""
    api_private_key: str = ""
    base_url: str = ASTER_DEFAULT_BASE_URL
    ws_url: str = ASTER_DEFAULT_WS_URL
    balance_mode: str = "auto"

    @classmethod
    def from_user_info(cls, user_info: Optional[dict[str, Any]]) -> "AsterConfiguration":
        user_info = user_info or {}
        balance_mode = str(user_info.get("balance_mode") or "auto").strip().lower() or "auto"
        if balance_mode not in {"auto", "single_asset", "multi_asset_stables"}:
            balance_mode = "auto"
        return cls(
            api_user=str(user_info.get("api_user") or "").strip(),
            api_signer=str(user_info.get("api_signer") or "").strip(),
            api_private_key=str(user_info.get("api_private_key") or "").strip(),
            base_url=str(user_info.get("base_url") or ASTER_DEFAULT_BASE_URL).rstrip("/"),
            ws_url=str(user_info.get("ws_url") or ASTER_DEFAULT_WS_URL).rstrip("/"),
            balance_mode=balance_mode,
        )


class AsterRestClient:
    id = "aster"

    def __init__(
        self,
        config: Optional[AsterConfiguration] = None,
        *,
        session: Optional[aiohttp.ClientSession] = None,
        timeout_ms: int = 30_000,
    ) -> None:
        self.config = config or AsterConfiguration()
        self.base_url = self.config.base_url
        self.ws_url = self.config.ws_url
        self.timeout_ms = int(timeout_ms)
        self.session = session
        self._owns_session = session is None
        self.markets: dict[str, dict[str, Any]] = {}
        self.options: dict[str, Any] = {"defaultType": "swap"}
        self.has: dict[str, bool] = {
            "fetchOHLCV": True,
            "watchOrders": False,
            "setLeverage": True,
            "setMarginMode": True,
            "setPositionMode": True,
        }

    async def ensure_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=self.timeout_ms / 1000)
            self.session = aiohttp.ClientSession(timeout=timeout)
            self._owns_session = True
        return self.session

    async def close(self) -> None:
        if self.session is not None and self._owns_session and not self.session.closed:
            await self.session.close()

    def set_markets(self, markets: dict[str, dict[str, Any]]) -> None:
        self.markets = dict(markets or {})

    def _require_private_credentials(self) -> None:
        if not (
            self.config.api_user
            and self.config.api_signer
            and self.config.api_private_key
        ):
            raise RuntimeError(
                "Aster private endpoints require api_user, api_signer, and api_private_key."
            )

    def _sign_v3_params(self, params: Optional[dict[str, Any]] = None) -> dict[str, str]:
        self._require_private_credentials()
        Web3, Account, encode_defunct, encode = _load_signing_dependencies()
        nonce = math.trunc(time.time() * 1_000_000)
        payload = {k: v for k, v in (params or {}).items() if v is not None}
        payload.setdefault("recvWindow", ASTER_RECV_WINDOW)
        payload.setdefault("timestamp", int(round(time.time() * 1000)))
        trimmed = _trim_dict(payload)
        json_str = json.dumps(trimmed, sort_keys=True, separators=(",", ":"))
        encoded = encode(
            ["string", "address", "address", "uint256"],
            [
                json_str,
                Web3.to_checksum_address(self.config.api_user),
                Web3.to_checksum_address(self.config.api_signer),
                nonce,
            ],
        )
        keccak_hex = Web3.keccak(encoded).hex()
        signed_message = Account.sign_message(
            encode_defunct(hexstr=keccak_hex),
            private_key=self.config.api_private_key,
        )
        signature = signed_message.signature.hex()
        if not signature.startswith("0x"):
            signature = "0x" + signature
        trimmed["nonce"] = str(nonce)
        trimmed["user"] = self.config.api_user
        trimmed["signer"] = self.config.api_signer
        trimmed["signature"] = signature
        return trimmed

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        data: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> Any:
        session = await self.ensure_session()
        url = f"{self.base_url}{path}"
        async with session.request(method, url, params=params, data=data, headers=headers) as response:
            text = await response.text()
            payload: Any
            if text:
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    payload = text
            else:
                payload = None
            code, message = _extract_error_details(payload, fallback_text=text)
            if response.status >= 400:
                raise AsterAPIError(
                    message or f"Aster request failed with status {response.status}",
                    status=response.status,
                    code=code,
                    path=path,
                    payload=payload,
                )
            if code is not None and code < 0:
                raise AsterAPIError(
                    message or f"Aster returned error code {code}",
                    status=response.status,
                    code=code,
                    path=path,
                    payload=payload,
                )
            return payload

    async def _request_json_with_fallback(
        self,
        method: str,
        paths: tuple[str, ...],
        *,
        params: Optional[dict[str, Any]] = None,
        data: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> Any:
        last_error: Optional[Exception] = None
        for path in paths:
            try:
                return await self._request_json(
                    method,
                    path,
                    params=params,
                    data=data,
                    headers=headers,
                )
            except Exception as exc:
                last_error = exc
                await asyncio.sleep(0)
        if last_error is None:
            raise RuntimeError("Aster request failed before attempting any path")
        raise last_error

    async def _public_request_json(
        self,
        method: str,
        paths: tuple[str, ...],
        *,
        params: Optional[dict[str, Any]] = None,
    ) -> Any:
        return await self._request_json_with_fallback(method, paths, params=params)

    async def _private_request_json(
        self,
        method: str,
        paths: tuple[str, ...],
        *,
        params: Optional[dict[str, Any]] = None,
    ) -> Any:
        signed_params = self._sign_v3_params(params)
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "Passivbot/aster",
        }
        if method.upper() == "GET":
            return await self._request_json_with_fallback(
                method,
                paths,
                params=signed_params,
                headers=headers,
            )
        return await self._request_json_with_fallback(
            method,
            paths,
            data=signed_params,
            headers=headers,
        )

    async def fetch_exchange_info(self) -> dict[str, Any]:
        payload = await self._public_request_json("GET", ASTER_EXCHANGE_INFO_PATHS)
        extracted = _extract_payload(payload)
        if not isinstance(extracted, dict) or "symbols" not in extracted:
            raise RuntimeError("Aster exchangeInfo payload missing symbols")
        return extracted

    async def load_markets(self, reload: bool = False) -> dict[str, dict[str, Any]]:
        if self.markets and not reload:
            return self.markets
        exchange_info = await self.fetch_exchange_info()
        self.markets = translate_aster_exchange_info(exchange_info)
        return self.markets

    async def fetch_time(self) -> Optional[int]:
        payload = await self._public_request_json("GET", ASTER_TIME_PATHS)
        extracted = _extract_payload(payload)
        if isinstance(extracted, dict) and "serverTime" in extracted:
            return _safe_int(extracted.get("serverTime"))
        if isinstance(payload, dict) and "serverTime" in payload:
            return _safe_int(payload.get("serverTime"))
        return None

    async def fetch_account(self) -> dict[str, Any]:
        payload = await self._private_request_json("GET", ASTER_ACCOUNT_PATHS)
        extracted = _extract_payload(payload)
        return extracted if isinstance(extracted, dict) else {}

    async def fetch_balance(self) -> list[dict[str, Any]]:
        payload = await self._private_request_json("GET", ASTER_BALANCE_PATHS)
        extracted = _extract_payload(payload)
        return extracted if isinstance(extracted, list) else []

    async def fetch_position_risk(self, symbol: Optional[str] = None) -> list[dict[str, Any]]:
        params = {}
        if symbol:
            params["symbol"] = passivbot_symbol_to_aster_symbol(symbol)
        payload = await self._private_request_json("GET", ASTER_POSITION_RISK_PATHS, params=params)
        extracted = _extract_payload(payload)
        return extracted if isinstance(extracted, list) else []

    async def fetch_open_orders(self, symbol: Optional[str] = None) -> list[dict[str, Any]]:
        params = {}
        if symbol:
            params["symbol"] = passivbot_symbol_to_aster_symbol(symbol)
        payload = await self._private_request_json("GET", ASTER_OPEN_ORDERS_PATHS, params=params)
        extracted = _extract_payload(payload)
        if isinstance(extracted, dict):
            return [extracted]
        return extracted if isinstance(extracted, list) else []

    async def fetch_order(
        self,
        symbol: str,
        order_id: Optional[int] = None,
        client_order_id: Optional[str] = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"symbol": passivbot_symbol_to_aster_symbol(symbol)}
        if order_id is not None:
            params["orderId"] = int(order_id)
        if client_order_id:
            params["origClientOrderId"] = client_order_id
        payload = await self._private_request_json("GET", ASTER_OPEN_ORDER_PATHS, params=params)
        extracted = _extract_payload(payload)
        return extracted if isinstance(extracted, dict) else {}

    async def fetch_all_orders(
        self,
        symbol: str,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: Optional[int] = None,
        order_id: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"symbol": passivbot_symbol_to_aster_symbol(symbol)}
        if start_time is not None:
            params["startTime"] = int(start_time)
        if end_time is not None:
            params["endTime"] = int(end_time)
        if limit is not None:
            params["limit"] = int(limit)
        if order_id is not None:
            params["orderId"] = int(order_id)
        payload = await self._private_request_json("GET", ASTER_ALL_ORDERS_PATHS, params=params)
        extracted = _extract_payload(payload)
        return extracted if isinstance(extracted, list) else []

    async def fetch_user_trades(
        self,
        symbol: str,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        from_id: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"symbol": passivbot_symbol_to_aster_symbol(symbol)}
        if start_time is not None:
            params["startTime"] = int(start_time)
        if end_time is not None:
            params["endTime"] = int(end_time)
        if from_id is not None:
            params["fromId"] = int(from_id)
        if limit is not None:
            params["limit"] = int(limit)
        payload = await self._private_request_json("GET", ASTER_USER_TRADES_PATHS, params=params)
        extracted = _extract_payload(payload)
        return extracted if isinstance(extracted, list) else []

    async def fetch_book_ticker(self, symbol: Optional[str] = None) -> list[dict[str, Any]]:
        params = {}
        if symbol:
            params["symbol"] = passivbot_symbol_to_aster_symbol(symbol)
        payload = await self._public_request_json("GET", ASTER_BOOK_TICKER_PATHS, params=params)
        extracted = _extract_payload(payload)
        if isinstance(extracted, dict):
            return [extracted]
        return extracted if isinstance(extracted, list) else []

    async def fetch_klines(
        self,
        symbol: str,
        interval: str,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> list[Any]:
        params: dict[str, Any] = {
            "symbol": passivbot_symbol_to_aster_symbol(symbol),
            "interval": interval,
            "limit": int(limit or 1500),
        }
        if start_time is not None:
            params["startTime"] = int(start_time)
        if end_time is not None:
            params["endTime"] = int(end_time)
        payload = await self._public_request_json("GET", ASTER_KLINES_PATHS, params=params)
        extracted = _extract_payload(payload)
        return extracted if isinstance(extracted, list) else []

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1m",
        since: Optional[int] = None,
        limit: Optional[int] = None,
        params: Optional[dict[str, Any]] = None,
    ) -> list[list[float]]:
        interval = TIMEFRAME_TO_ASTER_INTERVAL.get(timeframe, timeframe)
        params = dict(params or {})
        end_time = params.pop("endTime", None)
        if end_time is None:
            end_time = params.pop("until", None)
        extracted = await self.fetch_klines(
            symbol,
            interval,
            start_time=since,
            end_time=int(end_time) if end_time is not None else None,
            limit=limit or 1500,
        )
        candles: list[list[float]] = []
        for candle in extracted:
            if isinstance(candle, list) and len(candle) >= 6:
                candles.append(
                    [
                        int(candle[0]),
                        float(candle[1]),
                        float(candle[2]),
                        float(candle[3]),
                        float(candle[4]),
                        float(candle[5]),
                    ]
                )
            elif isinstance(candle, dict):
                candles.append(
                    [
                        _safe_int(candle.get("openTime") or candle.get("t")),
                        _safe_float(candle.get("open") or candle.get("o")),
                        _safe_float(candle.get("high") or candle.get("h")),
                        _safe_float(candle.get("low") or candle.get("l")),
                        _safe_float(candle.get("close") or candle.get("c")),
                        _safe_float(candle.get("volume") or candle.get("v")),
                    ]
                )
        return candles

    async def create_order(
        self,
        symbol: str,
        type: str,
        side: str,
        amount: float,
        price: Optional[float] = None,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        request_params: dict[str, Any] = {
            "symbol": passivbot_symbol_to_aster_symbol(symbol),
            "type": str(type).upper(),
            "side": str(side).upper(),
            "quantity": amount,
        }
        if price is not None and str(type).lower() != "market":
            request_params["price"] = price
        if params:
            request_params.update(params)
        payload = await self._private_request_json("POST", ASTER_ORDER_PATHS, params=request_params)
        extracted = _extract_payload(payload)
        return extracted if isinstance(extracted, dict) else {}

    async def cancel_order(
        self,
        order_id: Any = None,
        *,
        symbol: Optional[str] = None,
        client_order_id: Optional[str] = None,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        request_params = dict(params or {})
        if symbol:
            request_params["symbol"] = passivbot_symbol_to_aster_symbol(symbol)
        if order_id not in (None, ""):
            try:
                request_params["orderId"] = int(order_id)
            except (TypeError, ValueError):
                request_params["origClientOrderId"] = str(order_id)
        if client_order_id:
            request_params["origClientOrderId"] = client_order_id
        payload = await self._private_request_json("DELETE", ASTER_ORDER_PATHS, params=request_params)
        extracted = _extract_payload(payload)
        return extracted if isinstance(extracted, dict) else {}

    async def cancel_all_open_orders(self, symbol: str) -> dict[str, Any]:
        params = {"symbol": passivbot_symbol_to_aster_symbol(symbol)}
        payload = await self._private_request_json("DELETE", ASTER_ALL_OPEN_ORDERS_PATHS, params=params)
        extracted = _extract_payload(payload)
        return extracted if isinstance(extracted, dict) else {}

    async def cancel_batch_orders(
        self,
        symbol: str,
        order_ids: Optional[list[Any]] = None,
        client_order_ids: Optional[list[str]] = None,
    ) -> Any:
        params: dict[str, Any] = {"symbol": passivbot_symbol_to_aster_symbol(symbol)}
        if order_ids:
            params["orderIdList"] = [int(x) for x in order_ids]
        if client_order_ids:
            params["origClientOrderIdList"] = [str(x) for x in client_order_ids]
        return await self._private_request_json("DELETE", ASTER_BATCH_ORDERS_PATHS, params=params)

    async def change_leverage(self, symbol: str, leverage: int) -> dict[str, Any]:
        params = {
            "symbol": passivbot_symbol_to_aster_symbol(symbol),
            "leverage": int(leverage),
        }
        payload = await self._private_request_json("POST", ASTER_LEVERAGE_PATHS, params=params)
        extracted = _extract_payload(payload)
        return extracted if isinstance(extracted, dict) else {}

    async def set_margin_type(self, symbol: str, margin_type: str = "CROSSED") -> dict[str, Any]:
        params = {
            "symbol": passivbot_symbol_to_aster_symbol(symbol),
            "marginType": str(margin_type).upper(),
        }
        payload = await self._private_request_json("POST", ASTER_MARGIN_TYPE_PATHS, params=params)
        extracted = _extract_payload(payload)
        return extracted if isinstance(extracted, dict) else {}

    async def set_margin_mode(self, margin_mode: str, symbol: Optional[str] = None, params=None):
        if symbol is None:
            raise ValueError("Aster margin mode changes require a symbol")
        margin_type = "CROSSED" if str(margin_mode).lower().startswith("cross") else str(margin_mode)
        return await self.set_margin_type(symbol, margin_type=margin_type)

    async def set_leverage(self, leverage: int, symbol: Optional[str] = None):
        if symbol is None:
            raise ValueError("Aster leverage changes require a symbol")
        return await self.change_leverage(symbol, leverage)

    async def set_position_mode(self, dual_side: bool) -> dict[str, Any]:
        params = {"dualSidePosition": "true" if dual_side else "false"}
        payload = await self._private_request_json("POST", ASTER_POSITION_MODE_PATHS, params=params)
        extracted = _extract_payload(payload)
        return extracted if isinstance(extracted, dict) else {}

    async def get_position_mode(self) -> bool:
        payload = await self._private_request_json("GET", ASTER_POSITION_MODE_PATHS)
        extracted = _extract_payload(payload)
        if isinstance(extracted, dict):
            return _safe_bool(extracted.get("dualSidePosition"), default=False)
        return _safe_bool(payload, default=False)

    async def set_multi_assets_mode(self, enabled: bool) -> dict[str, Any]:
        params = {"multiAssetsMargin": "true" if enabled else "false"}
        payload = await self._private_request_json("POST", ASTER_MULTI_ASSETS_MODE_PATHS, params=params)
        extracted = _extract_payload(payload)
        return extracted if isinstance(extracted, dict) else {}

    async def get_multi_assets_mode(self) -> bool:
        payload = await self._private_request_json("GET", ASTER_MULTI_ASSETS_MODE_PATHS)
        extracted = _extract_payload(payload)
        if isinstance(extracted, dict):
            return _safe_bool(extracted.get("multiAssetsMargin"), default=False)
        return _safe_bool(payload, default=False)

    async def create_listen_key(self) -> str:
        payload = await self._private_request_json("POST", ASTER_LISTEN_KEY_PATHS)
        extracted = _extract_payload(payload)
        listen_key = ""
        if isinstance(extracted, dict):
            listen_key = str(extracted.get("listenKey") or "")
        elif isinstance(payload, dict):
            listen_key = str(payload.get("listenKey") or "")
        if not listen_key:
            raise RuntimeError("Aster listen key response missing listenKey")
        return listen_key

    async def keepalive_listen_key(self, listen_key: str) -> None:
        # V3 docs say "Parameters: None" - do not pass the listenKey in params
        await self._private_request_json("PUT", ASTER_LISTEN_KEY_PATHS)

    async def close_listen_key(self, listen_key: str) -> None:
        # V3 docs say "Parameters: None" - do not pass the listenKey in params
        try:
            await self._private_request_json("DELETE", ASTER_LISTEN_KEY_PATHS)
        except Exception:
            return
