from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Optional

from config_utils import require_live_value
from exchanges.aster_rest import (
    AsterAPIError,
    AsterConfiguration,
    AsterRestClient,
    passivbot_symbol_to_aster_symbol,
    _safe_bool,
    _safe_float,
    _safe_int,
    aster_symbol_to_passivbot_symbol,
)
from exchanges.aster_ws import AsterWebsocketConfig, AsterWebsocketManager
from passivbot import Passivbot, custom_id_to_snake, logging
from utils import utc_ms


class AsterPhaseNotReadyError(RuntimeError):
    pass


class AsterBot(Passivbot):
    def __init__(self, config: dict):
        super().__init__(config)
        self.id = "aster"
        self.quote = "USDT"
        self.hedge_mode = False
        self.live_trading_ready = True
        self._aster_multi_assets_mode: Optional[bool] = None
        self._aster_dual_side_position: Optional[bool] = None
        self._ws_private_cache_max_age = 30.0
        self._ws_public_cache_max_age = 10.0
        self._ws_balance_cache: Optional[float] = None
        self._ws_balance_cache_ts = 0.0
        self._ws_positions_cache: Optional[list[dict]] = None
        self._ws_positions_cache_ts = 0.0
        self._ws_open_orders_cache: Optional[list[dict]] = None
        self._ws_open_orders_cache_ts = 0.0
        self._ws_tickers_cache: dict[str, dict[str, float]] = {}
        self._ws_tickers_cache_ts = 0.0
        self._ws_private_event_times: dict[str, int] = {}
        self._ws_order_event_times: dict[str, int] = {}
        self._ws_ticker_event_times: dict[str, int] = {}
        self._ws_fill_events_cache: dict[str, dict] = {}
        self._ws_fill_events_max = 2000
        self._aster_order_detail_cache: dict[tuple[str, str], tuple[str, str]] = {}

    def create_ccxt_sessions(self):
        self.aster_config = AsterConfiguration.from_user_info(self.user_info)
        self.cca = AsterRestClient(self.aster_config)
        self.ccp = None
        self.ws_manager = AsterWebsocketManager(
            AsterWebsocketConfig.from_credentials(self.aster_config),
            self.cca,
        )

    async def start_bot(self):
        await super().start_bot()

    async def init_markets(self, verbose: bool = True):
        return await super().init_markets(verbose)

    async def determine_utc_offset(self, verbose: bool = True):
        server_time = await self.cca.fetch_time()
        if server_time is None:
            self.utc_offset = 0
        else:
            self.utc_offset = round((server_time - utc_ms()) / (1000 * 60 * 60)) * (
                1000 * 60 * 60
            )
        if verbose:
            logging.info(f"Exchange time offset is {self.utc_offset}ms compared to UTC")

    def symbol_is_eligible(self, symbol: str) -> bool:
        return symbol in self.markets_dict and bool(self.markets_dict[symbol].get("active", True))

    def set_market_specific_settings(self):
        super().set_market_specific_settings()
        for symbol, market in self.markets_dict.items():
            limits = market.get("limits", {})
            precision = market.get("precision", {})
            info = market.get("info", {})
            amount_limits = limits.get("amount", {})
            cost_limits = limits.get("cost", {})
            qty_step = float(precision.get("amount") or 0.0)
            min_qty = float(
                amount_limits.get("min")
                if amount_limits.get("min") not in (None, "")
                else qty_step
            )
            self.min_costs[symbol] = float(cost_limits.get("min") or 0.0)
            self.min_qtys[symbol] = min_qty
            self.qty_steps[symbol] = qty_step
            self.price_steps[symbol] = float(precision.get("price") or 0.0)
            self.c_mults[symbol] = float(market.get("contractSize", 1.0) or 1.0)
            self.max_leverage[symbol] = int(
                float(info.get("maxLeverage") or info.get("max_leverage") or 0.0)
            )

    def _private_cache_is_fresh(self, ts_value: float) -> bool:
        return ts_value > 0.0 and (time.monotonic() - ts_value) <= self._ws_private_cache_max_age

    def _public_cache_is_fresh(self, ts_value: float) -> bool:
        return ts_value > 0.0 and (time.monotonic() - ts_value) <= self._ws_public_cache_max_age

    def _public_ws_symbols(self) -> list[str]:
        symbols = set(getattr(self, "active_symbols", []) or [])
        if not symbols:
            approved = getattr(self, "approved_coins_minus_ignored_coins", {})
            for group in approved.values():
                symbols.update(group)
        return sorted(sym for sym in symbols if sym in self.markets_dict)

    def _order_cache_key(self, symbol: str, order_id: Any) -> tuple[str, str]:
        return symbol, str(order_id)

    def _cache_order_details(self, symbol: str, order_id: Any, client_order_id: str = "", pb_order_type: str = "") -> None:
        if not symbol or order_id in (None, ""):
            return
        client_order_id = str(client_order_id or "")
        pb_order_type = str(pb_order_type or (custom_id_to_snake(client_order_id) if client_order_id else "unknown"))
        self._aster_order_detail_cache[self._order_cache_key(symbol, order_id)] = (
            client_order_id,
            pb_order_type,
        )

    def _get_cached_order_details(self, symbol: str, order_id: Any) -> tuple[str, str]:
        return self._aster_order_detail_cache.get(self._order_cache_key(symbol, order_id), ("", "unknown"))

    def _prune_ws_fill_events(self) -> None:
        if len(self._ws_fill_events_cache) <= self._ws_fill_events_max:
            return
        ordered_ids = sorted(
            self._ws_fill_events_cache,
            key=lambda key: self._ws_fill_events_cache[key].get("timestamp", 0),
        )
        trim_count = max(0, len(ordered_ids) - self._ws_fill_events_max)
        for event_id in ordered_ids[:trim_count]:
            self._ws_fill_events_cache.pop(event_id, None)

    def _effective_hedge_mode(self) -> bool:
        return bool(self._config_hedge_mode and self.hedge_mode)

    def _raw_symbol_to_symbol(self, raw_symbol: str, raw: Optional[dict[str, Any]] = None) -> str:
        raw_symbol = str(raw_symbol or "").upper()
        if not raw_symbol:
            return ""
        try:
            return self.get_symbol_id_inv(raw_symbol)
        except Exception:
            raw = raw or {}
            return aster_symbol_to_passivbot_symbol(
                raw_symbol,
                base_asset=str(raw.get("baseAsset") or raw.get("baseAssetName") or "").upper(),
                quote_asset=str(raw.get("quoteAsset") or raw.get("marginAsset") or "").upper(),
                margin_asset=str(raw.get("marginAsset") or raw.get("quoteAsset") or "").upper(),
            )

    def _normalize_order_position_side(self, raw_order: dict[str, Any]) -> str:
        raw_ps = str(
            raw_order.get("positionSide")
            or raw_order.get("position_side")
            or raw_order.get("ps")
            or ""
        ).lower()
        if raw_ps in {"long", "short"}:
            return raw_ps
        side = str(raw_order.get("side") or "").lower()
        reduce_only = _safe_bool(
            raw_order.get("reduceOnly", raw_order.get("reduce_only")),
            default=False,
        )
        if reduce_only:
            return "long" if side == "sell" else "short"
        return "long" if side == "buy" else "short"

    def _normalize_trade_position_side(self, raw_trade: dict[str, Any], pnl: float) -> str:
        raw_ps = str(raw_trade.get("positionSide") or "").lower()
        if raw_ps in {"long", "short"}:
            return raw_ps
        side = str(raw_trade.get("side") or "").lower()
        if not side:
            side = "buy" if _safe_bool(raw_trade.get("buyer"), default=False) else "sell"
        if side == "buy":
            return "long" if pnl == 0.0 else "short"
        return "short" if pnl == 0.0 else "long"

    def _target_multi_assets_mode(self) -> Optional[bool]:
        if self.aster_config.balance_mode == "single_asset":
            return False
        if self.aster_config.balance_mode == "multi_asset_stables":
            return True
        return None

    def _extract_account_collateral_balance(
        self,
        account: dict[str, Any],
        *,
        multi_assets_mode: bool,
    ) -> float:
        assets = account.get("assets", []) if isinstance(account, dict) else []
        if not multi_assets_mode:
            if account.get("totalMarginBalance") not in (None, ""):
                return _safe_float(account.get("totalMarginBalance"))
            if account.get("totalCrossWalletBalance") not in (None, ""):
                return _safe_float(account.get("totalCrossWalletBalance"))
            if account.get("totalWalletBalance") not in (None, ""):
                return _safe_float(account.get("totalWalletBalance"))
            for asset in assets:
                if str(asset.get("asset") or "").upper() == self.quote:
                    return _safe_float(
                        asset.get("marginBalance")
                        or asset.get("crossWalletBalance")
                        or asset.get("walletBalance")
                        or asset.get("availableBalance")
                    )
            return 0.0

        # In Aster multi-assets mode, collateral comes from the stable asset margin balances.
        # Per-asset `availableBalance` may be duplicated across stables, so summing it can
        # overcount badly. We sum margin/wallet balances for eligible stable collateral instead.
        total = 0.0
        for asset in assets:
            asset_name = str(asset.get("asset") or "").upper()
            if asset_name not in {"USDT", "USDC", "USDF"}:
                continue
            if not _safe_bool(asset.get("marginAvailable"), default=True):
                continue
            total += _safe_float(
                asset.get("marginBalance")
                or asset.get("crossWalletBalance")
                or asset.get("walletBalance")
                or asset.get("availableBalance")
            )
        return total

    async def _get_multi_assets_mode(self) -> bool:
        if self._aster_multi_assets_mode is not None:
            return self._aster_multi_assets_mode
        target = self._target_multi_assets_mode()
        if target is not None:
            self._aster_multi_assets_mode = target
            return target
        self._aster_multi_assets_mode = await self.cca.get_multi_assets_mode()
        return self._aster_multi_assets_mode

    def _normalize_order_response(self, executed: dict[str, Any], order: Optional[dict[str, Any]] = None) -> dict:
        if not isinstance(executed, dict):
            return {}
        raw_symbol = executed.get("symbol") or (order or {}).get("symbol")
        symbol = (
            raw_symbol
            if isinstance(raw_symbol, str) and "/" in raw_symbol
            else self._raw_symbol_to_symbol(str(raw_symbol or ""), executed)
        )
        side = str(executed.get("side") or (order or {}).get("side") or "").lower()
        normalized = dict(executed)
        normalized["id"] = str(executed.get("orderId") or executed.get("id") or "")
        normalized["symbol"] = symbol
        normalized["side"] = side
        normalized["position_side"] = self._normalize_order_position_side(
            {
                "positionSide": executed.get("positionSide") or (order or {}).get("position_side"),
                "side": side or (order or {}).get("side"),
                "reduceOnly": executed.get("reduceOnly", (order or {}).get("reduce_only")),
            }
        )
        normalized["qty"] = _safe_float(
            executed.get("origQty") or executed.get("quantity") or executed.get("executedQty") or (order or {}).get("qty")
        )
        normalized["price"] = _safe_float(executed.get("price") or (order or {}).get("price"))
        normalized["clientOrderId"] = executed.get("clientOrderId") or (order or {}).get("custom_id")
        normalized["reduceOnly"] = _safe_bool(
            executed.get("reduceOnly", (order or {}).get("reduce_only")),
            default=False,
        )
        normalized["status"] = str(executed.get("status") or "").lower()
        normalized["timestamp"] = _safe_int(executed.get("time") or executed.get("updateTime"))
        self._cache_order_details(
            normalized["symbol"],
            normalized["id"],
            normalized.get("clientOrderId") or "",
            custom_id_to_snake(str(normalized.get("clientOrderId") or "")) if normalized.get("clientOrderId") else "unknown",
        )
        return normalized

    def _validate_order(self, order: dict) -> Optional[str]:
        symbol = order["symbol"]
        qty = abs(float(order["qty"]))
        order_type = str(order.get("type", "limit")).lower()
        if order_type == "market" and not self.live_value("market_orders_allowed"):
            return "market orders are disabled by config"
        min_qty = float(self.min_qtys.get(symbol, 0.0) or 0.0)
        if min_qty and qty < min_qty:
            return f"qty {qty} below min_qty {min_qty}"
        if order_type != "market":
            price = float(order.get("price") or 0.0)
            min_cost = float(self.min_costs.get(symbol, 0.0) or 0.0)
            cost = qty * price * float(self.c_mults.get(symbol, 1.0) or 1.0)
            if min_cost and cost + 1e-12 < min_cost:
                return f"cost {cost} below min_cost {min_cost}"
        return None

    async def fetch_balance(self) -> float:
        if self._private_cache_is_fresh(self._ws_balance_cache_ts) and self._ws_balance_cache is not None:
            return float(self._ws_balance_cache)
        account = await self.cca.fetch_account()
        multi_assets_mode = await self._get_multi_assets_mode()
        balance = self._extract_account_collateral_balance(
            account,
            multi_assets_mode=multi_assets_mode,
        )
        self._ws_balance_cache = balance
        self._ws_balance_cache_ts = time.monotonic()
        return balance

    async def fetch_positions(self):
        if self._private_cache_is_fresh(self._ws_positions_cache_ts) and self._ws_positions_cache is not None:
            return [dict(x) for x in self._ws_positions_cache]
        fetched = await self.cca.fetch_position_risk()
        positions = []
        for elm in fetched:
            raw_symbol = str(elm.get("symbol") or "").upper()
            position_amt = _safe_float(elm.get("positionAmt"))
            if position_amt == 0.0:
                continue
            raw_ps = str(elm.get("positionSide") or "BOTH").upper()
            if raw_ps == "LONG":
                position_side = "long"
            elif raw_ps == "SHORT":
                position_side = "short"
            else:
                position_side = "long" if position_amt > 0.0 else "short"
            positions.append(
                {
                    "symbol": self._raw_symbol_to_symbol(raw_symbol, elm),
                    "position_side": position_side,
                    "size": abs(position_amt),
                    "price": _safe_float(elm.get("entryPrice")),
                }
            )
        self._ws_positions_cache = [dict(x) for x in positions]
        self._ws_positions_cache_ts = time.monotonic()
        return positions

    async def fetch_open_orders(self, symbol: Optional[str] = None):
        if symbol is None and self._private_cache_is_fresh(self._ws_open_orders_cache_ts) and self._ws_open_orders_cache is not None:
            return [dict(x) for x in self._ws_open_orders_cache]
        fetched = await self.cca.fetch_open_orders(symbol=symbol)
        normalized: dict[str, dict] = {}
        for elm in fetched:
            order = self._normalize_order_response(elm)
            if order["id"]:
                normalized[order["id"]] = order
        result = sorted(normalized.values(), key=lambda x: x.get("timestamp", 0))
        if symbol is None:
            self._ws_open_orders_cache = [dict(x) for x in result]
            self._ws_open_orders_cache_ts = time.monotonic()
        return result

    async def fetch_tickers(self):
        if self._public_cache_is_fresh(self._ws_tickers_cache_ts) and self._ws_tickers_cache:
            return {k: dict(v) for k, v in self._ws_tickers_cache.items()}
        fetched = await self.cca.fetch_book_ticker()
        tickers = {}
        for elm in fetched:
            symbol = self._raw_symbol_to_symbol(str(elm.get("symbol") or ""), elm)
            if symbol not in self.markets_dict:
                continue
            bid = _safe_float(elm.get("bidPrice") or elm.get("bid"))
            ask = _safe_float(elm.get("askPrice") or elm.get("ask"))
            tickers[symbol] = {
                "bid": bid,
                "ask": ask,
                "last": (bid + ask) / 2 if bid and ask else bid or ask,
            }
        self._ws_tickers_cache = {k: dict(v) for k, v in tickers.items()}
        self._ws_tickers_cache_ts = time.monotonic()
        return tickers

    async def fetch_ohlcv(self, symbol: str, timeframe: str = "1m"):
        return await self.cca.fetch_ohlcv(symbol, timeframe=timeframe, limit=1000)

    async def fetch_ohlcvs_1m(
        self, symbol: str, since: Optional[float] = None, limit: Optional[int] = None
    ):
        if since is None:
            return await self.cca.fetch_ohlcv(symbol, timeframe="1m", limit=min(limit or 1500, 1500))
        remaining = int(limit or 1500)
        cursor = int(since // 60_000 * 60_000)
        all_candles: dict[int, list[float]] = {}
        while remaining > 0:
            batch_limit = min(remaining, 1500)
            batch = await self.cca.fetch_ohlcv(
                symbol,
                timeframe="1m",
                since=cursor,
                limit=batch_limit,
            )
            if not batch:
                break
            for candle in batch:
                all_candles[int(candle[0])] = candle
            if len(batch) < batch_limit:
                break
            next_cursor = int(batch[-1][0]) + 60_000
            if next_cursor <= cursor:
                break
            cursor = next_cursor
            remaining -= len(batch)
        return sorted(all_candles.values(), key=lambda x: x[0])

    def _set_ws_positions_cache(self, positions: list[dict]) -> None:
        self._ws_positions_cache = [dict(x) for x in positions]
        self._ws_positions_cache_ts = time.monotonic()

    def _set_ws_open_orders_cache(self, orders: list[dict]) -> None:
        self._ws_open_orders_cache = [dict(x) for x in orders]
        self._ws_open_orders_cache_ts = time.monotonic()

    def _replace_ws_open_order(self, order: dict) -> None:
        existing = self._ws_open_orders_cache or []
        filtered = [elm for elm in existing if str(elm.get("id")) != str(order.get("id"))]
        filtered.append(dict(order))
        self._set_ws_open_orders_cache(sorted(filtered, key=lambda x: x.get("timestamp", 0)))

    def _remove_ws_open_order(self, order_id: Any) -> None:
        if self._ws_open_orders_cache is None:
            self._set_ws_open_orders_cache([])
            return
        filtered = [
            elm for elm in self._ws_open_orders_cache if str(elm.get("id")) != str(order_id)
        ]
        self._set_ws_open_orders_cache(filtered)

    def _build_fill_event_from_ws_order(self, order_data: dict[str, Any], event_ts: int) -> Optional[dict]:
        trade_id = order_data.get("t")
        last_qty = _safe_float(order_data.get("l"))
        if trade_id in (None, "", "0") or last_qty <= 0.0:
            return None
        raw_symbol = str(order_data.get("s") or "").upper()
        symbol = self._raw_symbol_to_symbol(raw_symbol, order_data)
        side = str(order_data.get("S") or "").lower()
        client_order_id = str(order_data.get("c") or "")
        position_side = str(order_data.get("ps") or "").lower()
        if position_side not in {"long", "short"}:
            position_side = "long" if side == "buy" else "short"
        fee_cost = abs(_safe_float(order_data.get("n")))
        fee_currency = str(order_data.get("N") or self.quote or "")
        event_id = f"{raw_symbol}:{trade_id}"
        event = {
            "id": event_id,
            "timestamp": _safe_int(order_data.get("T") or event_ts),
            "datetime": datetime.fromtimestamp(
                _safe_int(order_data.get("T") or event_ts) / 1000,
                tz=timezone.utc,
            ).isoformat(),
            "symbol": symbol,
            "side": side,
            "qty": last_qty,
            "price": _safe_float(order_data.get("L") or order_data.get("ap") or order_data.get("p")),
            "pnl": _safe_float(order_data.get("rp")),
            "fees": {"currency": fee_currency, "cost": fee_cost} if fee_currency or fee_cost else None,
            "pb_order_type": custom_id_to_snake(client_order_id) if client_order_id else "unknown",
            "position_side": position_side,
            "client_order_id": client_order_id,
            "raw": [{"source": "aster_ws_order_trade_update", "data": dict(order_data)}],
        }
        self._ws_fill_events_cache[event_id] = dict(event)
        self._cache_order_details(symbol, order_data.get("i"), client_order_id, event["pb_order_type"])
        self._prune_ws_fill_events()
        return event

    def _normalize_trade_to_fill_event(self, raw_trade: dict[str, Any], client_order_id: str = "", pb_order_type: str = "unknown") -> dict:
        raw_symbol = str(raw_trade.get("symbol") or "").upper()
        symbol = self._raw_symbol_to_symbol(raw_symbol, raw_trade)
        trade_id = raw_trade.get("id") or raw_trade.get("tradeId")
        side = str(raw_trade.get("side") or "").lower()
        if not side:
            side = "buy" if _safe_bool(raw_trade.get("buyer"), default=False) else "sell"
        pnl = _safe_float(
            raw_trade.get("realizedPnl")
            or raw_trade.get("realizedPNL")
            or raw_trade.get("pnl")
            or raw_trade.get("profit")
        )
        if not client_order_id:
            client_order_id = str(raw_trade.get("clientOrderId") or "")
        if not pb_order_type or pb_order_type == "unknown":
            pb_order_type = custom_id_to_snake(client_order_id) if client_order_id else "unknown"
        fee_currency = str(raw_trade.get("commissionAsset") or raw_trade.get("feeAsset") or self.quote or "")
        fee_cost = abs(_safe_float(raw_trade.get("commission") or raw_trade.get("fee")))
        return {
            "id": f"{raw_symbol}:{trade_id}",
            "timestamp": _safe_int(raw_trade.get("time") or raw_trade.get("timestamp")),
            "datetime": datetime.fromtimestamp(
                _safe_int(raw_trade.get("time") or raw_trade.get("timestamp")) / 1000,
                tz=timezone.utc,
            ).isoformat(),
            "symbol": symbol,
            "side": side,
            "qty": _safe_float(raw_trade.get("qty") or raw_trade.get("origQty") or raw_trade.get("amount")),
            "price": _safe_float(raw_trade.get("price")),
            "pnl": pnl,
            "fees": {"currency": fee_currency, "cost": fee_cost} if fee_currency or fee_cost else None,
            "pb_order_type": pb_order_type,
            "position_side": self._normalize_trade_position_side(raw_trade, pnl),
            "client_order_id": client_order_id,
            "raw": [{"source": "aster_user_trades", "data": dict(raw_trade)}],
        }

    async def _reconcile_private_state(self) -> None:
        try:
            account = await self.cca.fetch_account()
            positions = await self.cca.fetch_position_risk()
            open_orders = await self.cca.fetch_open_orders()
        except Exception as exc:
            logging.warning("Aster REST reconciliation failed after WS reconnect: %s", exc)
            return

        fresh_positions = []
        for elm in positions:
            raw_symbol = str(elm.get("symbol") or "").upper()
            position_amt = _safe_float(elm.get("positionAmt"))
            if position_amt == 0.0:
                continue
            raw_ps = str(elm.get("positionSide") or "BOTH").upper()
            position_side = "long" if raw_ps == "LONG" or (raw_ps == "BOTH" and position_amt > 0.0) else "short"
            fresh_positions.append(
                {
                    "symbol": self._raw_symbol_to_symbol(raw_symbol, elm),
                    "position_side": position_side,
                    "size": abs(position_amt),
                    "price": _safe_float(elm.get("entryPrice")),
                }
            )
        self._set_ws_positions_cache(fresh_positions)

        normalized_orders = []
        for elm in open_orders:
            normalized = self._normalize_order_response(elm)
            if normalized.get("id"):
                normalized_orders.append(normalized)
        self._set_ws_open_orders_cache(normalized_orders)

        previous_balance = self.balance
        self._ws_balance_cache_ts = 0.0
        self._ws_balance_cache = None
        balance = await self.fetch_balance()
        self._ws_balance_cache = balance
        self._ws_balance_cache_ts = time.monotonic()
        if previous_balance != balance:
            self.balance = balance
            await self.handle_balance_update(source="WS/REST reconcile")
        self.execution_scheduled = True

    async def _handle_private_reconnect(self, exc: Exception, mode: str) -> None:
        self._health_ws_reconnects += 1
        self._ws_balance_cache_ts = 0.0
        self._ws_positions_cache_ts = 0.0
        self._ws_open_orders_cache_ts = 0.0
        logging.warning("Aster private WS reconnect (%s): %s", mode, exc)
        await self._reconcile_private_state()

    async def _handle_public_reconnect(self, exc: Exception) -> None:
        self._health_ws_reconnects += 1
        self._ws_tickers_cache_ts = 0.0
        logging.warning("Aster public WS reconnect: %s", exc)

    def _build_trade_symbol_pool(self) -> list[str]:
        symbols = set(getattr(self, "active_symbols", []) or [])
        for symbol, orders in getattr(self, "open_orders", {}).items():
            if orders:
                symbols.add(symbol)
        for symbol, sides in getattr(self, "positions", {}).items():
            if sides.get("long", {}).get("size") or sides.get("short", {}).get("size"):
                symbols.add(symbol)
        approved = getattr(self, "approved_coins_minus_ignored_coins", {})
        for symbols_set in approved.values():
            symbols.update(symbols_set)
        return sorted(sym for sym in symbols if sym in self.markets_dict)

    def _extract_ws_balance_total(
        self,
        balances: list[dict[str, Any]],
        positions: Optional[list[dict[str, Any]]] = None,
    ) -> Optional[float]:
        if not balances:
            return None
        positions = positions or []
        upnl_total = sum(
            _safe_float(pos.get("up") or pos.get("unrealizedPnl"))
            for pos in positions
        )
        if self._aster_multi_assets_mode:
            total = 0.0
            seen = False
            for balance in balances:
                asset = str(balance.get("a") or balance.get("asset") or "").upper()
                if asset not in {"USDT", "USDC", "USDF"}:
                    continue
                total += _safe_float(
                    balance.get("wb")
                    or balance.get("walletBalance")
                    or balance.get("cw")
                    or balance.get("crossWalletBalance")
                    or balance.get("availableBalance")
                )
                seen = True
            return (total + upnl_total) if seen else None
        for balance in balances:
            asset = str(balance.get("a") or balance.get("asset") or "").upper()
            if asset != self.quote:
                continue
            return _safe_float(
                balance.get("wb")
                or balance.get("walletBalance")
                or balance.get("cw")
                or balance.get("crossWalletBalance")
                or balance.get("availableBalance")
            ) + upnl_total
        return None

    async def _handle_private_ws_message(self, payload: dict[str, Any], mode: str) -> None:
        event_type = str(payload.get("e") or payload.get("eventType") or "")
        event_time = _safe_int(payload.get("E") or payload.get("eventTime") or payload.get("T"))
        if event_type:
            last_seen = self._ws_private_event_times.get(event_type, 0)
            if event_time and event_time < last_seen:
                return
            if event_time:
                self._ws_private_event_times[event_type] = event_time
        self._ws_last_message_ms = utc_ms()
        if event_type == "ACCOUNT_UPDATE":
            account_data = payload.get("a", {}) if isinstance(payload.get("a"), dict) else {}
            balances = account_data.get("B", []) if isinstance(account_data.get("B"), list) else []
            positions = account_data.get("P", []) if isinstance(account_data.get("P"), list) else []
            balance_total = self._extract_ws_balance_total(balances, positions)
            if balance_total is not None and balance_total >= 0.0:
                previous_balance = self.balance
                self._ws_balance_cache = balance_total
                self._ws_balance_cache_ts = time.monotonic()
                self.balance = balance_total
                if previous_balance != balance_total:
                    await self.handle_balance_update(source=f"WS:{mode}")
            normalized_positions = []
            for pos in positions:
                size = abs(_safe_float(pos.get("pa") or pos.get("positionAmt")))
                if size == 0.0:
                    continue
                raw_symbol = str(pos.get("s") or pos.get("symbol") or "").upper()
                position_side = str(pos.get("ps") or pos.get("positionSide") or "").lower()
                if position_side not in {"long", "short"}:
                    position_side = "long" if _safe_float(pos.get("pa") or pos.get("positionAmt")) > 0 else "short"
                normalized_positions.append(
                    {
                        "symbol": self._raw_symbol_to_symbol(raw_symbol, pos),
                        "position_side": position_side,
                        "size": size,
                        "price": _safe_float(pos.get("ep") or pos.get("entryPrice")),
                    }
                )
            if positions:
                self._set_ws_positions_cache(normalized_positions)
                self.execution_scheduled = True
        elif event_type == "ORDER_TRADE_UPDATE":
            order_data = payload.get("o", {}) if isinstance(payload.get("o"), dict) else {}
            order_id = str(order_data.get("i") or "")
            raw_symbol = str(order_data.get("s") or "").upper()
            symbol = self._raw_symbol_to_symbol(raw_symbol, order_data)
            if order_id:
                last_seen = self._ws_order_event_times.get(order_id, 0)
                if event_time and event_time < last_seen:
                    return
                if event_time:
                    self._ws_order_event_times[order_id] = event_time
            normalized_order = self._normalize_order_response(
                {
                    "symbol": raw_symbol,
                    "orderId": order_data.get("i"),
                    "clientOrderId": order_data.get("c"),
                    "side": order_data.get("S"),
                    "positionSide": order_data.get("ps"),
                    "reduceOnly": order_data.get("R"),
                    "origQty": order_data.get("q") or order_data.get("z"),
                    "price": order_data.get("p") or order_data.get("ap"),
                    "status": order_data.get("X"),
                    "time": order_data.get("T") or event_time,
                },
                order={"symbol": symbol},
            )
            status = str(order_data.get("X") or "").upper()
            if status in {"NEW", "PARTIALLY_FILLED"}:
                self._replace_ws_open_order(normalized_order)
            elif order_id:
                self._remove_ws_open_order(order_id)
            fill_event = self._build_fill_event_from_ws_order(order_data, event_time)
            if fill_event is not None:
                self.execution_scheduled = True
            self.handle_order_update([normalized_order])
        elif event_type == "ACCOUNT_CONFIG_UPDATE":
            ac = payload.get("ac", {}) if isinstance(payload.get("ac"), dict) else {}
            if "s" in ac and "l" in ac:
                symbol = self._raw_symbol_to_symbol(str(ac.get("s") or "").upper(), ac)
                self.max_leverage[symbol] = max(
                    int(ac.get("l") or 0),
                    int(self.max_leverage.get(symbol, 0) or 0),
                )
            if "pm" in payload:
                self._aster_dual_side_position = _safe_bool(payload.get("pm"), default=False)
        elif event_type == "listenKeyExpired":
            logging.warning(
                "Aster private WS listen key expired; reconnect requested. payload=%s",
                payload,
            )

    async def _handle_public_ws_message(self, payload: dict[str, Any], stream: Optional[str]) -> None:
        if not isinstance(payload, dict):
            return
        raw_symbol = str(payload.get("s") or "").upper()
        symbol = self._raw_symbol_to_symbol(raw_symbol, payload)
        if not symbol or symbol not in self.markets_dict:
            return
        event_time = _safe_int(payload.get("E") or payload.get("u") or payload.get("T"))
        if event_time and event_time < self._ws_ticker_event_times.get(symbol, 0):
            return
        if event_time:
            self._ws_ticker_event_times[symbol] = event_time
        bid = _safe_float(payload.get("b") or payload.get("bidPrice") or payload.get("bid"))
        ask = _safe_float(payload.get("a") or payload.get("askPrice") or payload.get("ask"))
        if bid < 0.0 or ask < 0.0:
            return
        self._ws_tickers_cache[symbol] = {
            "bid": bid,
            "ask": ask,
            "last": (bid + ask) / 2 if bid and ask else bid or ask,
        }
        self._ws_tickers_cache_ts = time.monotonic()

    async def fetch_pnls(self, start_time=None, end_time=None, limit=None):
        symbol_pool = self._build_trade_symbol_pool()
        if not symbol_pool:
            return []
        per_symbol_limit = min(int(limit or 1000), 1000)
        tasks = {
            symbol: asyncio.create_task(
                self.cca.fetch_user_trades(
                    symbol,
                    start_time=int(start_time) if start_time is not None else None,
                    end_time=int(end_time) if end_time is not None else None,
                    limit=per_symbol_limit,
                )
            )
            for symbol in symbol_pool
        }
        trades: dict[str, dict] = {}
        for symbol, task in tasks.items():
            fetched = await task
            for elm in fetched:
                raw_symbol = str(elm.get("symbol") or self.symbol_ids.get(symbol) or "")
                trade_id = elm.get("id") or elm.get("tradeId")
                if trade_id in (None, ""):
                    continue
                pnl = _safe_float(
                    elm.get("realizedPnl")
                    or elm.get("realizedPNL")
                    or elm.get("pnl")
                    or elm.get("profit")
                )
                side = str(elm.get("side") or "").lower()
                if not side:
                    side = "buy" if _safe_bool(elm.get("buyer"), default=False) else "sell"
                normalized = {
                    "id": f"{raw_symbol}:{trade_id}",
                    "tradeId": trade_id,
                    "symbol": self._raw_symbol_to_symbol(raw_symbol, elm),
                    "timestamp": _safe_int(elm.get("time") or elm.get("timestamp")),
                    "side": side,
                    "position_side": self._normalize_trade_position_side(elm, pnl),
                    "qty": _safe_float(elm.get("qty") or elm.get("origQty") or elm.get("amount")),
                    "amount": _safe_float(elm.get("qty") or elm.get("origQty") or elm.get("amount")),
                    "price": _safe_float(elm.get("price")),
                    "pnl": pnl,
                    "fee": _safe_float(elm.get("commission") or elm.get("fee")),
                    "info": elm,
                }
                trades[normalized["id"]] = normalized
        return sorted(trades.values(), key=lambda x: x["timestamp"])

    async def _get_order_details_for_trade(self, symbol: str, order_id: Any) -> tuple[str, str]:
        cached = self._get_cached_order_details(symbol, order_id)
        if cached[0] or cached[1] != "unknown":
            return cached
        client_order_id = ""
        pb_order_type = "unknown"
        try:
            records = await self.cca.fetch_all_orders(symbol, order_id=int(order_id), limit=1)
        except Exception:
            records = []
        if records:
            info = records[0]
            client_order_id = str(
                info.get("clientOrderId") or info.get("origClientOrderId") or ""
            )
            pb_order_type = custom_id_to_snake(client_order_id) if client_order_id else "unknown"
        self._cache_order_details(symbol, order_id, client_order_id, pb_order_type)
        return client_order_id, pb_order_type

    async def fetch_fill_events(self, start_time=None, end_time=None, limit=None):
        symbol_pool = self._build_trade_symbol_pool()
        if not symbol_pool:
            return []
        per_symbol_limit = min(int(limit or 1000), 1000)
        tasks = {
            symbol: asyncio.create_task(
                self.cca.fetch_user_trades(
                    symbol,
                    start_time=int(start_time) if start_time is not None else None,
                    end_time=int(end_time) if end_time is not None else None,
                    limit=per_symbol_limit,
                )
            )
            for symbol in symbol_pool
        }
        merged_events: dict[str, dict] = {}
        for symbol, task in tasks.items():
            fetched = await task
            for raw_trade in fetched:
                order_id = raw_trade.get("orderId") or raw_trade.get("orderID")
                client_order_id = str(raw_trade.get("clientOrderId") or "")
                pb_order_type = custom_id_to_snake(client_order_id) if client_order_id else "unknown"
                if order_id not in (None, "") and (not client_order_id or pb_order_type == "unknown"):
                    client_order_id, pb_order_type = await self._get_order_details_for_trade(symbol, order_id)
                event = self._normalize_trade_to_fill_event(
                    raw_trade,
                    client_order_id=client_order_id,
                    pb_order_type=pb_order_type,
                )
                merged_events[event["id"]] = event
        for event_id, event in self._ws_fill_events_cache.items():
            ts = _safe_int(event.get("timestamp"))
            if start_time is not None and ts < start_time:
                continue
            if end_time is not None and ts > end_time:
                continue
            merged_events[event_id] = dict(event)
        return sorted(merged_events.values(), key=lambda x: x["timestamp"])

    async def gather_fill_events(self, start_time=None, end_time=None, limit=None):
        return await self.fetch_fill_events(start_time=start_time, end_time=end_time, limit=limit)

    async def execute_order(self, order: dict) -> dict:
        validation_error = self._validate_order(order)
        if validation_error:
            logging.warning("Aster order skipped: %s | %s", validation_error, order)
            return {}
        created = await self.cca.create_order(
            symbol=order["symbol"],
            type=order.get("type", "limit"),
            side=order["side"],
            amount=abs(float(order["qty"])),
            price=float(order["price"]) if order.get("price") is not None else None,
            params=self.get_order_execution_params(order),
        )
        return self._normalize_order_response(created, order=order)

    async def execute_cancellation(self, order: dict) -> dict:
        try:
            executed = await self.cca.cancel_order(
                order.get("id"),
                symbol=order.get("symbol"),
                client_order_id=order.get("clientOrderId") or order.get("client_order_id"),
            )
            return self._normalize_order_response(executed, order=order)
        except AsterAPIError as exc:
            if exc.code == -2011 or "unknown order" in str(exc).lower():
                logging.info(
                    "[order] cancel skipped: %s %s - order likely already gone",
                    order.get("symbol", "?"),
                    str(order.get("id", "?"))[:12],
                )
                return {}
            raise

    def did_create_order(self, executed) -> bool:
        return bool(
            executed
            and (
                executed.get("id")
                or executed.get("orderId")
                or str(executed.get("status") or "").lower() in {"new", "partially_filled", "filled"}
            )
        )

    def did_cancel_order(self, executed, order=None) -> bool:
        if isinstance(executed, list) and len(executed) == 1:
            return self.did_cancel_order(executed[0], order)
        if not executed:
            return False
        status = str(executed.get("status") or "").lower()
        return bool(executed.get("id") or executed.get("orderId") or status in {"canceled", "cancelled"})

    def get_order_execution_params(self, order: dict) -> dict:
        params: dict[str, Any] = {}
        effective_hedge = self._effective_hedge_mode()
        if effective_hedge:
            position_side = str(order.get("position_side") or "").upper()
            if position_side not in {"LONG", "SHORT"}:
                raise ValueError(f"unsupported hedge-mode position_side: {order.get('position_side')}")
            params["positionSide"] = position_side
            if order.get("reduce_only"):
                side = str(order.get("side") or "").lower()
                if position_side == "LONG" and side != "sell":
                    raise ValueError("hedge-mode reduce-only long close must be SELL + LONG")
                if position_side == "SHORT" and side != "buy":
                    raise ValueError("hedge-mode reduce-only short close must be BUY + SHORT")
        else:
            params["positionSide"] = "BOTH"
            if order.get("reduce_only"):
                params["reduceOnly"] = True
        custom_id = order.get("custom_id")
        if custom_id:
            params["newClientOrderId"] = str(custom_id)[: self.custom_id_max_length]
        if str(order.get("type", "limit")).lower() == "limit":
            tif = require_live_value(self.config, "time_in_force")
            params["timeInForce"] = "GTX" if tif == "post_only" else "GTC"
        return params

    async def update_exchange_config(self):
        target_dual_side = self._effective_hedge_mode()
        if self._config_hedge_mode and not self.hedge_mode:
            logging.info("Aster connector currently forces one-way mode; ignoring live.hedge_mode=true.")
        current_dual_side = await self.cca.get_position_mode()
        if current_dual_side != target_dual_side:
            await self.cca.set_position_mode(target_dual_side)
            logging.info("Aster position mode updated: dualSidePosition=%s", target_dual_side)
        self._aster_dual_side_position = target_dual_side

        current_multi_assets = await self.cca.get_multi_assets_mode()
        target_multi_assets = self._target_multi_assets_mode()
        if target_multi_assets is not None and current_multi_assets != target_multi_assets:
            await self.cca.set_multi_assets_mode(target_multi_assets)
            current_multi_assets = target_multi_assets
            logging.info("Aster multi-assets mode updated: %s", target_multi_assets)
        self._aster_multi_assets_mode = current_multi_assets

    async def update_exchange_config_by_symbols(self, symbols: list):
        for symbol in symbols:
            if symbol not in self.markets_dict:
                continue
            leverage = int(self.config_get(["live", "leverage"], symbol=symbol))
            max_leverage = int(self.max_leverage.get(symbol, 0) or 0)
            if max_leverage > 0:
                leverage = min(leverage, max_leverage)
            try:
                await self.cca.change_leverage(symbol, leverage)
                logging.info("%s: set leverage to %sx", symbol, leverage)
            except AsterAPIError as exc:
                logging.error("%s: error setting leverage %s", symbol, exc)
            try:
                await self.cca.set_margin_type(symbol, "CROSSED")
                logging.info("%s: set cross margin mode", symbol)
            except AsterAPIError as exc:
                msg = str(exc).lower()
                if "no need to change" in msg or "same margin type" in msg:
                    logging.info("%s: cross margin mode already set", symbol)
                else:
                    logging.error("%s: error setting cross margin mode %s", symbol, exc)

    async def close(self):
        await self.ws_manager.close()
        await super().close()

    async def watch_orders(self):
        await self.ws_manager.run(
            private_message_callback=self._handle_private_ws_message,
            public_message_callback=self._handle_public_ws_message,
            symbols_provider=self._public_ws_symbols,
            private_reconnect_callback=self._handle_private_reconnect,
            public_reconnect_callback=self._handle_public_reconnect,
        )
