# Aster Connector Plan: Phase 2 - Live Trading Core

Date prepared: 2026-04-15

This phase covers the first implementation milestone for live trading on Aster Perpetuals. The aim is to reach a controlled, testable, REST-first Aster connector that can:

- load markets
- normalize symbols
- fetch balance, positions, open orders, and tickers
- place and cancel orders
- set leverage and exchange-side account configuration
- support Passivbot's live execution loop

This phase should not yet depend on production WebSocket behavior for correctness. WebSockets are phase 3.

## 1. Scope of Phase 2

Deliver a live-trading-capable Aster connector with REST as the source of truth.

Specifically:

- implement `AsterBot`
- implement Aster signing and REST wrappers
- map Aster market metadata to Passivbot market settings
- support order creation/cancellation
- support 10x default leverage through existing Passivbot config behavior
- support ticker/ohlcv/trade history fetches required by the live engine
- provide safe fallbacks where Aster semantics differ from other exchanges

Non-goals for phase 2:

- full private WebSocket parity
- fill-events manager parity
- historical downloader/backtesting parity
- performance tuning beyond basic rate-limit safety

## 2. Design Principles

### 2.1 Keep the connector thin

`AsterBot` should own only the exchange-specific behavior. Shared order orchestration, sizing, and trading logic should remain in `Passivbot`.

### 2.2 REST first, WS later

The connector must work correctly using:

- REST snapshots for positions/open orders/balance
- REST order placement/cancel
- REST ticker and candle fetches

Phase 3 can then replace the freshness path with WS caches.

### 2.3 Never hardcode 10x leverage in connector logic

Passivbot already carries a leverage configuration path. The Aster connector should:

- read `live.leverage`
- let the example config default to 10
- use the same semantics as other exchanges

## 3. Runtime Modules to Implement

### 3.1 `src/exchanges/aster.py`

Main runtime adapter.

Recommended responsibilities:

- `class AsterBot(Passivbot)`
- initialize auth/session state before `super().__init__()` if needed
- patch `self.cm.exchange = self` if `CandlestickManager` needs the exchange object directly
- expose a stable `id = "aster"`
- expose `quote = "USDT"`
- own runtime caches:
  - raw symbol to normalized symbol maps
  - price/qty step maps
  - REST caches for tickers, positions, open orders
  - optional WS state placeholders for phase 3

### 3.2 `src/exchanges/aster_rest.py`

Recommended responsibilities:

- V3 request signing
- persistent HTTP session helper
- endpoint wrapper methods
- Aster error classification

Recommended wrapper methods:

- `fetch_exchange_info()`
- `fetch_account()`
- `fetch_balance()`
- `fetch_position_risk(symbol=None)`
- `fetch_open_orders(symbol=None)`
- `fetch_order(symbol, order_id=None, client_order_id=None)`
- `fetch_all_orders(symbol, start_time=None, end_time=None, limit=None, order_id=None)`
- `fetch_user_trades(symbol, start_time=None, end_time=None, from_id=None, limit=None)`
- `fetch_book_ticker(symbol=None)`
- `fetch_klines(symbol, interval, start_time=None, end_time=None, limit=None)`
- `create_order(...)`
- `cancel_order(...)`
- `cancel_all_open_orders(symbol)`
- `cancel_batch_orders(...)`
- `change_leverage(symbol, leverage)`
- `set_position_mode(dual_side: bool)`
- `get_position_mode()`
- `set_multi_assets_mode(enabled: bool)`
- `get_multi_assets_mode()`

### 3.3 `src/exchanges/aster_ws.py`

Phase 2 should define the module and its interfaces even if most logic lands in phase 3.

At minimum:

- declare the public/private client interfaces
- define cache event models
- document reconnect and keepalive responsibilities

## 4. Endpoint Mapping

Use the official V3 docs as the primary mapping source.

### 4.1 Market metadata

- `GET /fapi/v3/exchangeInfo`

Needed for:

- symbol discovery
- active/inactive filters
- price step
- quantity step
- min notional
- precision and trigger protect info

### 4.2 Prices and order book

- `GET /fapi/v3/ticker/bookTicker`
- `GET /fapi/v3/ticker/price`
- `GET /fapi/v3/depth`

### 4.3 Candles

- `GET /fapi/v3/klines`

Use cases:

- `fetch_ohlcv()`
- `fetch_ohlcvs_1m()`
- later historical data integration

### 4.4 Orders

- `POST /fapi/v3/order`
- `GET /fapi/v3/openOrder`
- `GET /fapi/v3/openOrders`
- `GET /fapi/v3/allOrders`
- `DELETE /fapi/v3/order`
- `DELETE /fapi/v3/allOpenOrders`
- `DELETE /fapi/v3/batchOrders`

### 4.5 Account and positions

- `GET /fapi/v3/balance`
- `GET /fapi/v3/account`
- `GET /fapi/v3/positionRisk`
- `POST /fapi/v3/leverage`
- `POST /fapi/v3/marginType`
- `POST /fapi/v3/positionSide/dual`
- `GET /fapi/v3/positionSide/dual`
- `POST /fapi/v3/multiAssetsMargin`
- `GET /fapi/v3/multiAssetsMargin`

### 4.6 Trade history / realized PnL

- `GET /fapi/v3/userTrades`
- optionally `GET /fapi/v3/income`
- optionally `GET /fapi/v3/commissionRate`

## 5. Market Metadata Translation

### 5.1 What Aster returns

From the official docs and local example, `exchangeInfo` exposes symbol-level filters like:

- `PRICE_FILTER`
- `LOT_SIZE`
- `MIN_NOTIONAL`

The local helper in `ASTER_code_example/api_client.py:88` already parses:

- price precision
- tick size
- quantity precision
- step size
- min notional

### 5.2 What Passivbot needs

For each normalized symbol we need:

- `symbol_ids[symbol]`
- `min_costs[symbol]`
- `min_qtys[symbol]`
- `qty_steps[symbol]`
- `price_steps[symbol]`
- `c_mults[symbol]`
- `max_leverage[symbol]`

### 5.3 Translation rules

Recommended translation:

- market id: raw Aster id like `BTCUSDT`
- normalized symbol: `BTC/USDT:USDT`
- `price_step`: `PRICE_FILTER.tickSize`
- `qty_step`: `LOT_SIZE.stepSize`
- `min_qty`: `LOT_SIZE.minQty`
- `min_cost`: `MIN_NOTIONAL.notional`
- `c_mult`: `1.0` unless Aster exposes contract size for a special market
- `max_leverage`: fill from leverage-bracket endpoint when available, otherwise use a conservative fallback and update later per symbol

### 5.4 Cache outputs

Market translation should be cached to:

- `caches/aster/markets.json`
- `caches/aster/coin_to_symbol_map.json`

Use the existing cache helpers where possible. If `load_markets()` cannot use CCXT for Aster, add a custom branch similar in shape to the existing `lighter` branch but powered by the new Aster REST module.

## 6. Symbol, Coin, and Quote Rules

### 6.1 Quote behavior

Aster perps should remain `USDT`-quoted for market metadata.

Do not add Aster to the special `USDC`-quote list in `src/utils.py:672`.

### 6.2 Canonical symbol form

Use:

- `BTC/USDT:USDT`
- `ETH/USDT:USDT`
- `BNB/USDT:USDT`

### 6.3 Boundary translation

Every API boundary should translate:

- Passivbot symbol -> Aster raw symbol before requests
- Aster raw symbol -> Passivbot symbol after responses

## 7. Balance, Position, and Open Order Fetching

### 7.1 `fetch_positions()`

Primary endpoint:

- `GET /fapi/v3/positionRisk`

Recommended output shape:

```python
{
    "symbol": "BTC/USDT:USDT",
    "position_side": "long" | "short",
    "size": "...",
    "price": "...",
}
```

Implementation notes:

- in one-way mode:
  - map `positionSide=BOTH`
  - derive long/short from sign of `positionAmt`
- in hedge mode:
  - map `LONG` and `SHORT` directly
- discard zero-size entries unless Passivbot expects explicit zero placeholders

### 7.2 Balance source

Primary endpoints:

- `GET /fapi/v3/account`
- `GET /fapi/v3/balance`
- `GET /fapi/v3/multiAssetsMargin`

Recommended logic:

- query multi-assets mode once at startup and periodically on config refresh
- if single-asset mode:
  - use account summary `availableBalance`
  - cross-check against `USDT` asset row
- if multi-assets mode:
  - sum stable assets with `marginAvailable=true`
  - use `availableBalance`
  - initial included assets:
    - `USDT`
    - `USDC`
    - `USDF`

Use `availableBalance` for sizing, not `walletBalance` and not `marginBalance`.

### 7.3 `fetch_open_orders(symbol=None)`

Primary endpoint:

- `GET /fapi/v3/openOrders`

Normalization should include:

- `id`
- `symbol`
- `side`
- `qty`
- `price`
- `position_side`
- `timestamp`
- `clientOrderId`
- `reduceOnly`
- `status`

## 8. Order Execution and Cancellation

### 8.1 `execute_order()`

Primary endpoint:

- `POST /fapi/v3/order`

Needed order fields from Passivbot:

- symbol
- side
- qty
- price
- type
- position_side
- reduce_only
- custom_id

### 8.2 Time-in-force mapping

Recommended mapping:

- Passivbot `post_only` -> Aster `GTX`
- normal limit -> `GTC`
- emergency or aggressive fallback -> `IOC` only if explicitly needed

### 8.3 Market order handling

Phase 2 recommendation:

- support market orders only if they are already a normal Passivbot path for the active strategy
- otherwise keep market-order behavior conservative

### 8.4 `newClientOrderId`

Aster allows client ids up to 36 characters. This matches Passivbot's existing `custom_id_max_length = 36` in `src/passivbot.py:350`.

### 8.5 `reduceOnly` and hedge mode

This is the single biggest order-path risk.

Official docs say:

- `reduceOnly` cannot be sent in hedge mode

Implementation plan:

- if one-way mode:
  - use `positionSide=BOTH`
  - allow `reduceOnly` when closing
- if hedge mode:
  - omit `reduceOnly`
  - require correct `positionSide`
  - add a safety validation layer before order submission

Safety validation in hedge mode should check:

- close-long orders are `SELL + LONG`
- close-short orders are `BUY + SHORT`
- open-long orders are `BUY + LONG`
- open-short orders are `SELL + SHORT`

If this cannot be proven safe in test/live verification, hedge mode should stay disabled for Aster in initial rollout.

### 8.6 `execute_cancellation()`

Primary endpoint:

- `DELETE /fapi/v3/order`

Fallback helpers:

- `DELETE /fapi/v3/allOpenOrders`
- `DELETE /fapi/v3/batchOrders`

### 8.7 `did_create_order()` / `did_cancel_order()`

Implement Aster-specific success detection based on:

- HTTP success
- response shape
- terminal status values

## 9. Exchange Configuration on Startup

### 9.1 `update_exchange_config()`

Aster supports:

- position mode
- multi-assets mode

Recommended startup flow:

1. query current position mode
2. query current multi-assets mode
3. compare with config target
4. set only if needed
5. log any deviation

### 9.2 `update_exchange_config_by_symbols(symbols)`

Aster supports:

- per-symbol leverage changes
- margin-type changes

Recommended per-symbol flow:

1. set leverage using `POST /fapi/v3/leverage`
2. set margin type
3. log success/failure per symbol

### 9.3 Default leverage behavior

Do not hardcode `10` in the connector.

Instead:

- read `live.leverage`
- ensure Aster example configs default to `10`
- validate requested leverage against Aster brackets if available

## 10. `fetch_tickers()`, `fetch_ohlcv()`, and `fetch_ohlcvs_1m()`

### 10.1 `fetch_tickers()`

Primary endpoint:

- `GET /fapi/v3/ticker/bookTicker`

Output should match Passivbot's expected format:

```python
{
    "BTC/USDT:USDT": {
        "bid": 0.0,
        "ask": 0.0,
        "last": 0.0
    }
}
```

### 10.2 `fetch_ohlcv(symbol, timeframe="1m")`

Primary endpoint:

- `GET /fapi/v3/klines`

Requirements:

- translate Passivbot timeframes to Aster intervals
- honor `since` / `endTime` / `limit`
- return `[timestamp, open, high, low, close, volume]`

### 10.3 `fetch_ohlcvs_1m()`

Requirements:

- use `1m` endpoint directly
- support pagination up to the requested limit
- keep the output sorted by timestamp
- deduplicate on timestamp

## 11. `fetch_pnls()` Design

### 11.1 Constraint from Aster API

`GET /fapi/v3/userTrades` requires a symbol. That means Aster cannot use a simple `fetch_my_trades(symbol=None)` style implementation.

### 11.2 Recommended implementation

Implement Aster-specific `fetch_pnls(start_time=None, end_time=None, limit=None)` that:

1. builds a symbol pool from:
   - `active_symbols`
   - open-order symbols
   - position symbols
   - approved coins minus ignored coins
2. fetches `userTrades` per symbol
3. normalizes the results into Passivbot's expected PnL/trade shape
4. merges and sorts by timestamp
5. de-duplicates by trade id

## 12. Error Handling and Retry Rules

### 12.1 Errors to classify explicitly

HTTP/status layer:

- `429` too many requests
- `418` IP ban
- `503` execution unknown
- `5xx` transient server failures

Aster error-code layer:

- `-1021` invalid timestamp
- `-1022` invalid signature
- `-2011` cancel rejected / unknown order
- `-2021` order would immediately trigger
- `-2022` reduce-only rejected
- `-2024` position not sufficient
- `-2025` max open order exceeded
- `-4004` quantity less than min qty
- `-4014` price not increased by tick size
- `-4023` quantity not increased by step size
- `-4164` min notional

### 12.2 Retry policy

Recommended:

- retry network timeouts with exponential backoff
- retry 429 with backoff and global pacing
- treat 503 as unknown execution state and reconcile via REST order lookup
- do not blindly retry invalid signature, precision, or rule errors

## 13. Proposed Tests for Phase 2

Create `tests/test_aster.py` with at least these groups.

### 13.1 Market metadata

- translation of `exchangeInfo` to normalized markets
- symbol normalization
- precision/step/min-notional mapping
- active/inactive symbol filtering

### 13.2 Balance and position parsing

- single-asset mode balance
- multi-asset stable aggregation
- one-way position parsing
- hedge position parsing
- zero-position filtering

### 13.3 Open order parsing

- `BOTH` mode normalization
- `LONG` / `SHORT` mapping
- custom client order id retention
- partial fill fields

### 13.4 Order creation

- post-only maps to `GTX`
- standard limit maps to `GTC`
- custom id length trimming
- min-notional validation
- hedge close/open validation rules

### 13.5 Order cancellation

- successful cancel
- already-gone order handling
- unknown-order handling
- startup cleanup path using all-open-orders cancel

### 13.6 PnL fetching

- per-symbol fanout merge
- deduplication across symbols
- timestamp ordering
- realized PnL normalization

### 13.7 Exchange configuration

- leverage update path
- one-way/hedge position-mode path
- multi-assets mode path

## 14. Phase 2 Exit Criteria

Phase 2 is complete only when all are true:

- `AsterBot` can be instantiated from `setup_bot()`
- market metadata is cached and normalized correctly
- live bot startup can fetch balance, positions, open orders, and tickers
- orders can be created and canceled through REST
- leverage is applied from `live.leverage`
- `fetch_pnls()` works across a symbol pool
- there are unit tests covering the Aster-specific normalization and execution paths
- no `lighter` behavior or tests regress

## 15. Sources Used for This Phase

External:

- https://github.com/asterdex/api-docs/blob/master/V3%28Recommended%29/EN/aster-finance-futures-api-v3.md
- https://docs.asterdex.com/product/aster-perpetuals/api/api-documentation
- https://docs.asterdex.com/for-developers/aster-api
- https://docs.asterdex.com/for-developers/aster-api/how-to-create-an-api-key

Local:

- `src/exchanges/exchange_interface.py`
- `src/exchanges/ccxt_bot.py`
- `src/passivbot.py`
- `src/utils.py`
- `ASTER_code_example/api_client.py`
- `ASTER_code_example/market_maker.py`
- `ASTER_code_example/README.md`
