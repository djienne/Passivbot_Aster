# Aster Connector Plan: Phase 3 - WebSockets, Reconciliation, and Fill-Events Parity

Date prepared: 2026-04-15

Phase 3 upgrades the Aster connector from a REST-correct connector to a live-trading connector with real-time state freshness and better accounting.

The main deliverables are:

- private user-stream support
- public stream support for fast market prices
- REST/WS reconciliation logic
- fill-events integration for live accounting, unstuck allowances, and PnL continuity

This phase is where the connector stops being merely functional and becomes production-usable.

## 1. Phase Goal

By the end of phase 3, Aster should behave like a modern live Passivbot connector:

- positions, balances, and order state update through private WS
- price freshness comes from public WS when available
- reconnects do not corrupt local state
- fills can be fed into Passivbot's fill-events flows
- REST remains the fallback and reconciliation layer

## 2. Why This Phase Is Separate

Aster's live-trading correctness depends on two moving parts:

- private account/order events
- public market-data events

The official Aster material is inconsistent about user-stream generation:

- official V3 docs describe `/fapi/v3/listenKey`
- docs site and local example still show `/fapi/v1/listenKey`
- local examples actively use V1-style HMAC user-stream auth while using V3 for trading/account endpoints

That mismatch is the exact reason phase 3 is isolated from phase 2. It lets the core trading path be correct before the user-stream transport is made authoritative.

## 3. Stream Targets

### 3.1 Private streams

Needed event types:

- `ACCOUNT_UPDATE`
- `ORDER_TRADE_UPDATE`
- `ACCOUNT_CONFIG_UPDATE`
- `listenKeyExpired`
- optionally `MARGIN_CALL`

### 3.2 Public streams

Minimum useful streams:

- `<symbol>@bookTicker`

Optional richer streams:

- `<symbol>@depth`
- `<symbol>@depth5`
- `<symbol>@depth10`
- `<symbol>@aggTrade`
- `<symbol>@tradepro`

Recommended phase 3 public priority:

1. `bookTicker`
2. `depth` only if needed for tighter execution pricing or diagnostics
3. `aggTrade` and `tradepro` only if later strategies or analytics need them

## 4. User-Stream Strategy

### 4.1 Primary target

Use V3 private streams first.

Expected V3 behavior from the official docs:

- `POST /fapi/v3/listenKey`
- `PUT /fapi/v3/listenKey`
- `DELETE /fapi/v3/listenKey`
- private stream at `wss://fstream.asterdex.com/ws/<listenKey>`
- user events may arrive out of order during heavy periods and must be ordered by `E`
- one WebSocket connection is valid for 24 hours

### 4.2 Compatibility fallback

If live verification shows that V3 user streams are not usable for the current account type:

- enable a V1 listen-key fallback behind configuration
- keep the rest of the connector on V3 endpoints
- isolate the fallback inside `aster_ws.py`

Do not spread V1/V3 branching throughout `AsterBot`.

### 4.3 Config knob

Recommended user-info fields:

- `prefer_v3_user_stream = true`
- `allow_v1_user_stream_fallback = true`

Recommended runtime behavior:

1. try V3 listen key
2. if the private stream fails in a detectable auth/version-specific way, and fallback is allowed
3. switch to V1 listen-key flow
4. log the downgrade loudly

## 5. Private WS Lifecycle

### 5.1 Startup sequence

Recommended:

1. boot REST session
2. fetch initial account snapshot:
   - account
   - positions
   - open orders
3. start private WS
4. obtain listen key
5. subscribe/connect
6. mark WS caches as warming
7. begin consuming events

### 5.2 Keepalive policy

Official docs say:

- listen key valid for 60 minutes
- keepalive extends it for 60 minutes
- the connection itself is only valid for 24 hours

Recommended implementation:

- keepalive every 30 minutes
- pre-emptive full reconnect every 23 hours
- immediate reconnect on `listenKeyExpired`

### 5.3 Reconnect policy

Recommended:

- exponential backoff with jitter
- upper backoff cap
- on reconnect:
  - stop consuming stale stream
  - fetch fresh REST snapshots
  - reset cache timestamps
  - resume event application

### 5.4 Ordering rule

Private event payloads are not guaranteed to be in order.

Therefore:

- track `E` event times per channel
- ignore older duplicate/out-of-order state mutations when clearly stale
- use REST snapshot reconciliation as the source of truth after reconnects

## 6. Private Event Handling

### 6.1 `ACCOUNT_UPDATE`

Use cases:

- balance refresh
- position refresh
- margin type changes
- funding and realized updates

Recommended handling:

- parse `a.B` balances
- parse `a.P` positions
- update cached balance state
- update cached positions
- update `self.balance` if safe
- set `execution_scheduled = True` when positions change materially

Balance policy must match phase 1:

- single-asset mode: use active quote asset
- multi-asset mode: aggregate approved stable assets only

### 6.2 `ORDER_TRADE_UPDATE`

Use cases:

- open-order cache updates
- fill detection
- client order id recovery
- realized PnL capture

Important fields from docs:

- `c` client order id
- `S` side
- `o` type
- `f` time in force
- `x` execution type
- `X` order status
- `i` order id
- `l` last filled qty
- `z` cumulative filled qty
- `L` last fill price
- `n` commission
- `N` commission asset
- `T` trade time
- `t` trade id
- `m` maker flag
- `R` reduce only
- `ps` position side
- `rp` realized profit

Recommended handling:

- normalize into Passivbot order-update shape for active orders
- separately emit fill-event records for fills and partial fills
- keep a local mapping:
  - `clientOrderId -> orderId`
  - `tradeId -> fill event`

### 6.3 `ACCOUNT_CONFIG_UPDATE`

Use cases:

- leverage updates
- hedge-mode flips
- multi-assets-mode changes

Recommended handling:

- update cached exchange-mode state
- log any drift from expected config
- if drift is severe, schedule reconciliation or controlled restart

### 6.4 `listenKeyExpired`

Recommended handling:

- immediately mark private WS stale
- obtain a new listen key
- reconnect
- refresh account/open-order/position snapshots before resuming

## 7. Public Market-Data Strategy

### 7.1 First-choice stream

Use `<symbol>@bookTicker` for:

- bid
- ask
- last-like midpoint

Why:

- lighter than depth
- enough for execution price estimation
- enough for stale ticker detection
- enough for emergency order pricing

### 7.2 When to use depth

Use `<symbol>@depth` only if needed for:

- more accurate top-of-book reconstruction
- validating book continuity on reconnect
- advanced diagnostics

The Aster docs include the exact local order-book rebuild sequence using:

- diff depth stream
- depth snapshot from `/fapi/v3/depth`
- `U`, `u`, and `pu` sequencing

If we introduce depth maintenance, follow that sequence exactly.

### 7.3 Trade streams

`@aggTrade` and `@tradepro` are useful for:

- market activity diagnostics
- slippage studies
- optional liquidity analytics

They are not required for Passivbot correctness and should remain optional.

## 8. WS Cache Design

Recommended runtime caches inside `AsterBot`:

- `_ws_balance_cache`
- `_ws_balance_cache_ts`
- `_ws_positions_cache`
- `_ws_positions_cache_ts`
- `_ws_open_orders_cache`
- `_ws_open_orders_cache_ts`
- `_ws_tickers_cache`
- `_ws_tickers_cache_ts`
- `_ws_last_message_ms`

Recommended staleness handling:

- private account caches stale after 20 to 30 seconds without updates
- public ticker cache stale after 5 to 10 seconds
- if stale:
  - fall back to REST
  - increment health counters
  - keep trading only if REST remains healthy

## 9. REST/WS Reconciliation Rules

This is one of the most important sections in the whole plan.

### 9.1 General rule

WS provides freshness. REST provides truth on cold start, after reconnect, and on contradiction.

### 9.2 Startup reconciliation

At startup:

1. fetch REST snapshots
2. start WS
3. allow WS to begin updating caches
4. if the first private events disagree strongly with REST:
   - trust the newer event only if event time is clearly newer
   - otherwise perform a fresh REST reconciliation

### 9.3 Order reconciliation

On reconnect:

1. fetch `openOrders`
2. compare with local in-memory open order map
3. remove orders that no longer exist
4. add newly observed orders
5. if local state contains active orders absent from REST and absent from recent WS events:
   - treat them as stale

### 9.4 Position reconciliation

On reconnect:

1. fetch `positionRisk`
2. rebuild normalized positions
3. replace WS-derived positions if WS is stale or contradictory

### 9.5 Balance reconciliation

On reconnect:

1. fetch `account`
2. recompute normalized balance using the phase-1 balance policy
3. replace WS balance if stale or contradictory

## 10. Fill Events Integration

Passivbot uses fill events for:

- realized PnL continuity
- unstuck allowance logic
- last-position-change tracking
- dashboarding and debugging

Relevant shared code:

- `src/passivbot.py:2899` `init_fill_events()`
- `src/passivbot.py:3005` `fetch_fill_events()`
- `src/passivbot.py:3226` `update_fill_events()`
- `src/fill_events_manager.py:3791` `_build_fetcher_for_bot()`

### 10.1 Recommended Aster fill strategy

Use two sources:

1. real-time fills from `ORDER_TRADE_UPDATE`
2. historical/backfill fills from `GET /fapi/v3/userTrades`

### 10.2 Why both are needed

`ORDER_TRADE_UPDATE` includes:

- client order id
- realized profit
- commission
- position side
- maker flag

This makes it ideal for live canonical fill events.

`userTrades` is needed for:

- startup backfill
- restart recovery
- gap filling if WS is missed

### 10.3 Main challenge

`userTrades` includes:

- trade id
- order id
- realized PnL
- position side

But may not always include the client order id needed to derive Passivbot order type.

Recommended recovery approach:

- first choice: use live `ORDER_TRADE_UPDATE` cache
- second choice: enrich from `allOrders` or `openOrder` when possible
- fallback: set `client_order_id=""` and `pb_order_type="unknown"`

### 10.4 Aster-specific fetcher addition

In phase 3, add Aster support to:

- `src/fill_events_manager.py`

That means:

- register `"aster"` in `EXCHANGE_BOT_CLASSES`
- add an Aster fetcher in `_build_fetcher_for_bot()`
- use Aster REST wrappers for:
  - user trades
  - optional order enrichment

Alternative:

- implement `AsterBot.fetch_fill_events()` directly and let Passivbot use it

Recommended choice:

- implement both
  - direct bot support for live runtime
  - fetcher registration for CLI/tooling parity

## 11. Proposed Canonical Fill Event Mapping

For each fill, normalize to:

```python
{
    "id": "<trade_id>",
    "timestamp": <ms>,
    "datetime": "<iso>",
    "symbol": "BTC/USDT:USDT",
    "side": "buy" | "sell",
    "qty": "...",
    "price": <fill price>,
    "pnl": <realized pnl>,
    "fees": {"currency": "...", "cost": ...} or list form,
    "pb_order_type": "<decoded or unknown>",
    "position_side": "long" | "short",
    "client_order_id": "<client id or empty>",
    "raw": [...],
}
```

When the fill comes from `ORDER_TRADE_UPDATE`, preserve the raw payload. When it comes from `userTrades`, preserve the trade response and any enrichment payloads.

## 12. Safety Features to Add in This Phase

### 12.1 WS watchdog

Track:

- last private message time
- last public ticker message time
- reconnect count

If stale:

- log warning
- fall back to REST
- if stale for too long, reconnect

### 12.2 24-hour forced reconnect

The official docs say a single connection is only valid for 24 hours.

Therefore:

- track connection start time
- force reconnect before 24h

### 12.3 Event deduplication

Deduplicate by:

- trade id for fills
- order id + status + update time for order updates

### 12.4 Contradiction guard

If WS claims an order is open but REST says it is gone:

- trust REST after a fresh recheck

If WS claims a balance/position update with obviously invalid values:

- reject the update
- preserve last good state
- reconcile via REST

## 13. Proposed Tests for Phase 3

Expand `tests/test_aster.py` with these groups.

### 13.1 Private WS parsing

- `ACCOUNT_UPDATE` parsing
- `ORDER_TRADE_UPDATE` parsing
- `ACCOUNT_CONFIG_UPDATE` parsing
- `listenKeyExpired` handling

### 13.2 Cache updates

- balance cache refresh
- positions cache refresh
- open-order cache reconciliation
- ticker cache refresh

### 13.3 Reconnect behavior

- stale private stream triggers reconnect
- stale public stream falls back to REST
- reconnect triggers REST resync
- 24h forced reconnect path

### 13.4 Fill-event generation

- partial fill event normalization
- full fill event normalization
- fee mapping
- realized PnL mapping
- client order id retention
- historical `userTrades` merge with WS fills

### 13.5 Safety rules

- invalid WS balance rejected
- impossible position update rejected
- contradictory REST snapshot wins after reconnect

## 14. Phase 3 Exit Criteria

Phase 3 is complete only when:

- private user stream runs with keepalive and reconnect
- public ticker stream updates market prices
- stale WS caches fall back cleanly to REST
- order, position, and balance caches reconcile correctly on reconnect
- live fill events are generated from `ORDER_TRADE_UPDATE`
- Aster is supported by the fill-events tooling path
- there are tests covering WS parsing and reconciliation

## 15. Sources Used for This Phase

External:

- https://github.com/asterdex/api-docs/blob/master/V3%28Recommended%29/EN/aster-finance-futures-api-v3.md
- https://docs.asterdex.com/product/aster-perpetuals/api/api-documentation

Local:

- `src/passivbot.py`
- `src/fill_events_manager.py`
- `ASTER_code_example/market_maker.py`
- `ASTER_code_example/data_collector.py`
- `ASTER_code_example/websocket_orders.py`
- `ASTER_code_example/tests/websocket_user_data.py`
- `ASTER_code_example/tests/test_user_stream_step_by_step.py`
