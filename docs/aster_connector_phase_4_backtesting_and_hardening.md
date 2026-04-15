# Aster Connector Plan: Phase 4 - Backtesting, Historical Data, and Hardening

Date prepared: 2026-04-15

Phase 4 brings Aster to parity with the rest of Passivbot's offline and operational tooling.

The goals here are:

- historical candle download
- backtesting support
- suite-run compatibility
- long-run reliability
- docs/config examples
- regression protection

This phase should complete the Aster integration so it is not just a live connector but a full Passivbot exchange target.

## 1. Phase Goal

By the end of phase 4, Aster should support:

- historical 1m candle retrieval and caching
- backtest preparation flows
- optimizer/suite preparation flows
- repeatable smoke testing and regression testing
- operator documentation

The target outcome is that Aster behaves like a normal first-class exchange within the repo, even if it still uses a custom connector internally.

## 2. Primary Design Choice for Backtesting

### 2.1 Preferred path

Use the generic HLCV preparation path.

Relevant code:

- `src/hlcv_preparation.py:88` `class HLCVManager`
- `src/hlcv_preparation.py:692` `prepare_hlcvs()`
- `src/hlcv_preparation.py:957` `prepare_hlcvs_combined()`
- `src/backtest.py:889` uses `prepare_hlcvs_combined()`
- `src/backtest.py:893` uses `prepare_hlcvs()`

Why this is preferred:

- avoids creating another lighter-style special case
- keeps Aster aligned with the generic backtesting model
- reduces long-term maintenance cost

### 2.2 Avoid copying lighter's special loader

`src/hlcv_preparation.py:517` defines `_prepare_hlcvs_lighter()`. That function exists because lighter has a separate pre-downloaded data path.

For Aster, do not start there.

Only introduce `_prepare_hlcvs_aster()` if one of these proves true:

- the generic HLCV path cannot support a non-CCXT exchange cleanly
- paginated `/fapi/v3/klines` fetching is too slow for practical use
- we need a high-throughput archival collector with a different cache format

## 3. Historical Candle Strategy

### 3.1 Core endpoint

Use:

- `GET /fapi/v3/klines`

Docs summary:

- max `limit` 1500
- supports `startTime`
- supports `endTime`
- returns standard OHLCV arrays

### 3.2 Pagination strategy

Recommended downloader behavior:

1. normalize requested `startTime` to minute boundary
2. request up to 1500 1m bars per call
3. advance by last returned open time + 60s
4. deduplicate timestamps
5. stop when:
   - no more data
   - last bar reaches end boundary

### 3.3 Data storage format

Use existing Passivbot-compatible storage:

- daily 1m arrays
- same `[timestamp, open, high, low, close, volume]` layout
- same cache and shard expectations as generic HLCV preparation

Do not invent an Aster-only candle format unless there is a hard technical reason.

## 4. Implementation Options for Historical Data

### 4.1 Option A: generic HLCVManager integration

This is the preferred path.

Implementation idea:

- extend `HLCVManager` to support Aster via a custom non-CCXT market/candle fetch adapter
- allow `load_markets()` to read Aster market metadata from the custom Aster cache
- allow `get_ohlcvs()` to call the custom Aster candle fetcher

Benefits:

- reuses existing gap logic
- reuses CandlestickManager persistence
- reuses combined-exchange backtest preparation

### 4.2 Option B: dedicated Aster OHLCV collector

Only use this if option A is not good enough.

This would look like:

- `src/tools/aster_ohlcv_collector.py`

Modeled more on:

- `src/tools/lighter_ohlcv_collector.py`

Responsibilities:

- discover markets
- backfill 1m candles by day
- maintain resume cursors
- write daily `.npy` files

If this path is chosen later, the collector should still write Passivbot-compatible daily files, not an Aster-only custom format.

## 5. Market Metadata for Backtesting

Backtesting needs symbol metadata that matches live trading.

Therefore:

- the same Aster `exchangeInfo` translation used for live must be reusable in backtest code
- `coin_to_symbol()` and `symbol_to_coin()` should work from cached Aster symbol maps
- market settings must match live:
  - `price_step`
  - `qty_step`
  - `min_qty`
  - `min_cost`
  - `c_mult`
  - fees

### 5.1 Fees

Recommended policy:

- default to official commission-rate endpoint when available
- otherwise use a safe default from docs or configured override

Potential sources:

- `GET /fapi/v3/commissionRate`
- exchange defaults or config override

The backtester should not guess fees differently from the live connector.

## 6. `load_markets()` Integration

Current relevant code:

- `src/utils.py:490` `load_markets()`
- `src/utils.py:513` special-cases `lighter`

Phase 4 recommendation:

- add an Aster custom market-loading branch if phase 2 implemented Aster without CCXT
- make it return cached Aster market metadata in the same structural shape used elsewhere

Important:

- do not route Aster through CCXT unless the repo actually upgrades and validates Aster support
- backtesting must not silently depend on a future CCXT upgrade that has not happened

## 7. Backtest Preparation Integration

### 7.1 Single-exchange preparation

Ensure Aster works with:

- `prepare_hlcvs(config, exchange="aster")`

That means:

- markets load correctly
- coins normalize correctly
- `HLCVManager.get_ohlcvs()` can fetch and standardize Aster data
- returned `mss`, `timestamps`, and `unified_array` are valid

### 7.2 Combined-exchange preparation

Ensure Aster works with:

- `prepare_hlcvs_combined()`

Important scenarios:

- Aster only
- Aster + Binance
- Aster + Bybit
- coin-source forcing where some coins must come from Aster

### 7.3 Volume ratios in combined mode

If Aster is used in combined-exchange mode, it must participate in:

- `compute_exchange_volume_ratios()`

That means Aster 1m candle volume must be on the same semantic footing as other exchanges. If Aster returns base volume, convert consistently where the backtester expects quote volume.

## 8. Historical Trade and Research Tooling

The local `ASTER_code_example` includes data-collection tooling for:

- order book depth
- trades
- mid-price series

This is useful for market-making research but is not required for core Passivbot backtesting.

Recommended treatment:

- keep those ideas separate from the main Passivbot candle downloader
- optionally add an Aster research collector later under `src/tools/`
- do not let research collectors dictate the main Passivbot candle format

## 9. Operational Hardening

Phase 4 should also finish the operational side of the integration.

### 9.1 Health logging

Add Aster-specific health summary logging comparable to other connectors:

- orders placed
- orders canceled
- fills observed
- WS reconnects
- rate-limit hits
- REST fallback counts

### 9.2 Startup and shutdown behavior

Verify:

- startup cleanup does not accidentally cancel unrelated symbols unless intended
- shutdown closes:
  - HTTP sessions
  - public WS
  - private WS
  - keepalive tasks

### 9.3 Restart safety

Test:

- restart with open orders
- restart with open positions
- restart with stale listen key
- restart after a 503 execution-unknown create/cancel response

### 9.4 Rate-limit hardening

Validate:

- request pacing for `klines`
- request pacing for `userTrades`
- request pacing for `openOrders`
- order pacing for live create/cancel

### 9.5 Unknown execution-state handling

Aster documents 503 as potentially "execution unknown".

Therefore hardening must include:

- order-create reconciliation after unknown response
- cancel reconciliation after unknown response
- restart-safe lookup logic using:
  - `openOrder`
  - `openOrders`
  - `allOrders`

## 10. Test Expansion

Phase 4 should extend beyond unit tests.

### 10.1 Unit tests

Expand `tests/test_aster.py` to cover:

- downloader pagination
- candle normalization
- backtest preparation
- combined-exchange preparation with Aster
- fee mapping
- volume semantics

### 10.2 Integration tests

Add focused integration-style tests for:

- market cache generation
- startup sequence with cached markets
- restart after persisted candle cache
- fill-event cache continuity

### 10.3 Regression tests

Add negative regression tests proving Aster work does not break:

- `lighter`
- `setup_bot()` unknown exchange fallback
- existing downloader behavior
- existing combined backtest flows

Relevant existing anchors:

- `tests/test_setup_bot_fallback.py`
- `tests/test_lighter.py`
- `tests/test_ohlcvs_downloader.py`

## 11. Docs and Config Examples

Phase 4 should add user-facing docs so the connector is actually usable.

Recommended additions:

- an Aster live-trading doc
- an Aster credentials example in `api-keys.json.example`
- an Aster config example under `configs/`
- notes on:
  - V3-first support
  - optional V1 user-stream fallback if still needed
  - one-way versus hedge mode support status
  - single-asset versus multi-asset balance handling
  - expected default leverage example at 10x

### 11.1 Example config policy

Provide an Aster example config that:

- defaults `live.leverage` to `10`
- keeps the balance/exposure parameters conservative
- clearly states whether hedge mode is fully supported yet

## 12. Smoke-Test Runbook

Before declaring Aster complete, run this manual sequence.

### 12.1 Metadata smoke test

- load markets
- verify symbol mapping for BTC, ETH, BNB, SOL
- verify min qty, step size, tick size

### 12.2 Read-only live smoke test

- fetch account
- fetch balance
- fetch positions
- fetch open orders
- fetch book ticker
- fetch recent 1m candles

### 12.3 Order lifecycle smoke test

On a tiny size:

- place far-away post-only order
- verify open-order visibility
- cancel order
- verify cancellation

### 12.4 WS smoke test

- private stream starts
- public ticker stream starts
- keepalive runs
- reconnect path works

### 12.5 Fill-event smoke test

- place a tiny fillable order
- confirm live fill event
- restart bot
- confirm fill can still be reconstructed from historical trade path

### 12.6 Backtest smoke test

- download Aster candles for a small set of symbols
- run `prepare_hlcvs()`
- run one backtest
- run one suite/combine scenario if Aster is intended to coexist with other exchanges

## 13. Exit Criteria for Full Aster Support

Aster should only be considered fully integrated when all of these are true:

- live REST trading works
- private and public WS paths are reliable
- fill events are captured and recoverable
- Aster candles can be downloaded historically
- Aster can be backtested through the repo's standard flows
- Aster can participate in combined backtests if desired
- docs and config examples exist
- Aster tests pass without breaking `lighter`

## 14. Recommended Final Status Labels

When the work is done, classify Aster support explicitly as one of:

### 14.1 Beta

If:

- live trading works
- WS works
- historical backtesting works
- but hedge mode or multi-asset accounting still has restrictions

### 14.2 Production-ready

Only if:

- one-way and hedge modes are both validated
- multi-asset and single-asset balance handling are validated
- fill events are robust across restarts
- backtesting and live paths use consistent market metadata

## 15. Sources Used for This Phase

External:

- https://github.com/asterdex/api-docs/blob/master/V3%28Recommended%29/EN/aster-finance-futures-api-v3.md
- https://docs.asterdex.com/product/aster-perpetuals/api/api-documentation
- https://docs.asterdex.com/for-developers/aster-api

Local:

- `src/hlcv_preparation.py`
- `src/backtest.py`
- `src/utils.py`
- `src/candlestick_manager.py`
- `src/tools/lighter_ohlcv_collector.py`
- `tests/test_setup_bot_fallback.py`
- `tests/test_lighter.py`
- `ASTER_code_example/data_collector.py`
- `ASTER_code_example/README.md`
