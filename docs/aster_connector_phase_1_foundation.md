# Aster Connector Plan: Phase 1 - Foundation, Architecture, and Repo Preparation

Date prepared: 2026-04-15

This phase defines the architecture for adding an Aster Perpetuals connector to this Passivbot fork without breaking the existing `lighter` integration and without copying `lighter`-specific coupling into a second exchange.

The goal of phase 1 is not to make Aster trade yet. The goal is to make the repo structurally ready for Aster, define the rules of the integration, identify hard constraints from Aster's API, and minimize the number of shared-file edits before runtime work begins.

## 1. Phase Goal

By the end of phase 1, we should have a final agreed design for:

- Which Aster API generation is the target.
- Whether Aster is implemented as a direct custom exchange or via a CCXT upgrade track.
- Which repo modules will own REST, WebSocket, market metadata, order mapping, and historical candles.
- Which pieces are shared with `lighter`, and which are intentionally kept separate.
- What the first live-safe scope is.

The output of phase 1 should let phase 2 begin implementation without revisiting core architecture.

## 2. Non-Negotiable Design Rules

These rules are the backbone of the Aster integration.

### 2.1 Keep `lighter` fully intact

The existing `lighter` path is already deeply customized and should be treated as production behavior. Aster work must not:

- change `lighter` rate limiting behavior
- change `lighter` OHLCV collection behavior
- reuse `lighter`-specific auth/token code
- reuse `lighter` backtest special-casing unless unavoidable

Relevant repo touchpoints:

- `src/passivbot.py:5548` contains `setup_bot()`
- `src/passivbot.py:5587` registers `lighter`
- `src/exchanges/lighter.py:297` defines `class LighterBot`
- `src/hlcv_preparation.py:517` defines `_prepare_hlcvs_lighter`
- `src/tools/lighter_ohlcv_collector.py:2` defines the lighter-specific collector

### 2.2 Aster must be as independent from `lighter` as possible

The correct mental model is:

- `lighter` is one custom connector.
- `aster` should be a second custom connector.
- shared logic should only live in exchange-agnostic places.

That means:

- do not subclass `LighterBot`
- do not place Aster code inside `lighter.py`
- do not add Aster branches throughout `lighter` internals
- do not create Aster plans that depend on lighter-only cache formats

### 2.3 Target Aster Perpetuals V3 first

The official Aster docs repo explicitly says:

- use V3 for all new integrations
- starting on 2026-03-25, new V1 API key creation is no longer supported

That makes V3 the only sane primary target for a new connector.

Use these as the primary external references:

- Official Aster API overview: `https://docs.asterdex.com/for-developers/aster-api`
- Official API key docs: `https://docs.asterdex.com/for-developers/aster-api/how-to-create-an-api-key`
- Official docs repo README: `https://github.com/asterdex/api-docs/blob/master/README.md`
- Official futures API V3 docs: `https://github.com/asterdex/api-docs/blob/master/V3%28Recommended%29/EN/aster-finance-futures-api-v3.md`

### 2.4 Treat V1 user-stream examples as compatibility references only

There is an important mismatch in the currently available Aster material:

- the official V3 docs document `/fapi/v3/listenKey`
- the public docs site and the local `ASTER_code_example` still use `/fapi/v1/listenKey`
- the local example mixes V3 trading/account endpoints with V1 user-stream auth

This means phase 1 must adopt the following rule:

- V3 is the default implementation target.
- V1 listen-key flow is only a fallback path if real-world testing shows V3 user streams are not yet usable for the current account type.

Do not build the Aster connector around V1-first assumptions.

## 3. Current Repo Reality

This repo is not a clean multi-exchange upstream Passivbot anymore. It is a heavily modified lighter-focused fork:

- `README.md:35` says the fork was reworked to run exclusively on Lighter
- `README.md:39` calls out the Lighter exchange adapter
- `README.md:40` calls out Lighter-specific candle fetching

Even so, a lot of exchange-agnostic infrastructure remains and should be reused:

- `src/exchanges/exchange_interface.py`
- `src/exchanges/ccxt_bot.py`
- `src/passivbot.py`
- `src/utils.py`
- `src/candlestick_manager.py`
- `src/hlcv_preparation.py`
- `src/fill_events_manager.py`

This is good news: Aster can be added cleanly if we are disciplined about where custom logic goes.

## 4. Aster-Specific Facts That Affect Architecture

These details matter before any code is written.

### 4.1 The repo's current CCXT version does not expose Aster

Local repo/package facts:

- `requirements-live.txt:6` pins `ccxt==4.5.22`
- local installed runtime here is `ccxt 4.4.96`
- neither exposes `aster`

Latest public CCXT docs do list Aster, but that is not enough for this repo today.

Implication:

- We cannot rely on the current repo state to instantiate `CCXTBot(exchange="aster")`.
- Phase 1 must explicitly choose between:
  - a controlled CCXT upgrade track
  - a direct custom Aster REST/WebSocket implementation

### 4.2 Aster has two auth families in the material we collected

From the local example:

- `ASTER_code_example/README.md:72` shows Ethereum-style credentials for trading:
  - `API_USER`
  - `API_SIGNER`
  - `API_PRIVATE_KEY`
- `ASTER_code_example/README.md:77` shows HMAC API key/secret for user streams:
  - `APIV1_PUBLIC_KEY`
  - `APIV1_PRIVATE_KEY`

From the official V3 docs:

- V3 signed trade/user-data requests use `user`, `signer`, `nonce`, `signature`
- the nonce is in microseconds
- the docs include EIP-712 style signing material

Implication:

- The Aster connector must own its own auth layer.
- Reusing generic CCXT signing abstractions is only possible if the upgraded CCXT Aster implementation is mature enough.
- If we take the custom path, credentials must be stored under the Aster user in `api-keys.json` using Aster-specific fields.

### 4.3 Symbols are USDT perps, but balances may be multi-asset

From Aster docs and the local example:

- perp symbols are things like `BTCUSDT`, `ETHUSDT`, `BNBUSDT`
- the local market maker sums `USDF + USDT + USDC`
- Aster exposes multi-assets mode and single-asset mode

Implication:

- Aster symbols should map to Passivbot's normal `BTC/USDT:USDT` form
- Aster should remain a `USDT`-quoted exchange for market metadata and symbol mapping
- balance accounting must be explicit about single-asset versus multi-asset mode

Do not add Aster to the `USDC`-quote shortcut in `src/utils.py:672`.

### 4.4 Hedge mode is a real risk area

Official V3 docs for new orders say:

- `positionSide` is used in hedge mode
- `reduceOnly` cannot be sent in hedge mode

Passivbot relies heavily on close/reduce semantics.

Implication:

- Hedge-mode support on Aster is not something to assume.
- It must be validated with focused tests before being declared safe.
- The first implementation should include an explicit safety gate:
  - either one-way mode only at first
  - or hedge mode with carefully verified `positionSide`-only close semantics

## 5. Recommended Architecture Decision

### 5.1 Primary recommendation

Use a custom Aster connector first, with the same separation style as `lighter`, but much thinner:

- `src/exchanges/aster.py`
- `src/exchanges/aster_rest.py`
- `src/exchanges/aster_ws.py`
- optionally `src/tools/aster_ohlcv_collector.py` later only if needed

Reason:

- this repo cannot use Aster through its current CCXT dependency set
- Aster has custom signing requirements
- Aster user-stream material is inconsistent between V1 and V3
- we want tight control over live trading behavior and reconnection logic

### 5.2 Secondary track

Keep an explicit future path open for a CCXT-based simplification:

- once the repo upgrades to a CCXT release that supports Aster
- once we have verified which Aster methods are correctly unified
- once we know whether CCXT Aster handles auth, positions, orders, and user trades correctly

At that point we can decide whether to:

- keep the custom connector permanently
- or refactor Aster to `AsterBot(CCXTBot)` with targeted overrides

### 5.3 Why not copy `lighter`

`lighter` contains a lot of logic that is solving lighter-only problems:

- custom SDK quirks
- custom nonce handling
- volume quota logic
- lighter-specific WebSocket transaction sending
- lighter-only candle fetch workarounds

Aster does not have the same problem set. Copying `lighter` would create the wrong abstraction.

## 6. Proposed File Layout

These are the target modules to introduce when implementation starts.

### 6.1 New exchange modules

- `src/exchanges/aster.py`
  - `class AsterBot(Passivbot)`
  - owns exchange lifecycle
  - owns Passivbot method overrides
- `src/exchanges/aster_rest.py`
  - request signing
  - REST endpoint wrappers
  - error classification
  - market metadata loaders
- `src/exchanges/aster_ws.py`
  - public streams
  - private streams
  - listen-key lifecycle
  - reconnect and cache synchronization

### 6.2 Later optional modules

- `src/tools/aster_ohlcv_collector.py`
- `tests/test_aster.py`
- `tests/fixtures/aster_*.py`

### 6.3 Shared files that should see only small edits

- `src/passivbot.py`
  - add Aster registration in `setup_bot()`
- `src/utils.py`
  - allow market loading from cached/custom Aster market metadata if needed
  - keep quote handling as `USDT`
- `src/fill_events_manager.py`
  - register Aster fetcher later in phase 3
- docs/config example files

## 7. Credential Model to Standardize Early

The Aster user entry in `api-keys.json` should be designed up front.

Recommended shape:

```json
{
  "aster_01": {
    "exchange": "aster",
    "api_user": "0x...",
    "api_signer": "0x...",
    "api_private_key": "0x...",
    "api_key_v1": "",
    "api_secret_v1": "",
    "base_url": "https://fapi.asterdex.com",
    "ws_url": "wss://fstream.asterdex.com",
    "prefer_v3_user_stream": true,
    "balance_mode": "auto"
  }
}
```

Notes:

- `api_key_v1` and `api_secret_v1` should be optional and empty by default.
- `prefer_v3_user_stream=true` should be the default.
- `balance_mode` should be explicit:
  - `single_asset`
  - `multi_asset_stables`
  - `auto`

## 8. Balance Policy to Lock Down in Phase 1

This is one of the most important architecture choices.

Recommended policy:

### 8.1 In single-asset mode

If `GET /fapi/v3/multiAssetsMargin` reports `false`:

- use the account's `availableBalance`
- use the `USDT` asset entry as the source of truth
- size positions only from the active margin asset

### 8.2 In multi-assets mode

If `GET /fapi/v3/multiAssetsMargin` reports `true`:

- aggregate stable balances that are margin-eligible and liquid enough for strategy sizing
- initial safe set:
  - `USDT`
  - `USDC`
  - `USDF`
- use only assets where `marginAvailable=true`
- prefer `availableBalance`, not raw wallet balance

### 8.3 Why this matters

Passivbot's wallet exposure logic assumes a coherent quote-balance concept. If Aster balances are mishandled, every downstream sizing decision becomes wrong.

## 9. Phase 1 Implementation Blueprint

### 9.1 Add `AsterBot` registration path

Planned minimal shared-file edits:

- `src/passivbot.py`
  - add:
    - `elif user_info["exchange"] == "aster": from exchanges.aster import AsterBot`
    - `bot = AsterBot(config)`

This is the only mandatory shared runtime registration change at this stage.

### 9.2 Define the exchange object contract

`AsterBot` must eventually own these methods:

- `create_ccxt_sessions`
- `close`
- `set_market_specific_settings`
- `symbol_is_eligible`
- `fetch_positions`
- `fetch_open_orders`
- `fetch_tickers`
- `fetch_ohlcv`
- `fetch_ohlcvs_1m`
- `fetch_pnls`
- `execute_order`
- `execute_cancellation`
- `did_create_order`
- `did_cancel_order`
- `get_order_execution_params`
- `update_exchange_config`
- `update_exchange_config_by_symbols`
- `watch_orders`

Reference contracts:

- `src/exchanges/exchange_interface.py`
- `src/exchanges/ccxt_bot.py`
- `src/exchanges/lighter.py`

### 9.3 Decide what `create_ccxt_sessions()` means for Aster

For a direct custom connector, the method can still exist but should initialize:

- persistent `aiohttp.ClientSession`
- optional public WebSocket session manager
- optional private WebSocket/listen-key manager
- local rate-limit state

Keep the method name for Passivbot compatibility even if it no longer creates CCXT clients.

### 9.4 Define the market metadata adapter

Use `GET /fapi/v3/exchangeInfo` as the source of truth for:

- active symbols
- tick size
- step size
- min notional
- quantity and price precision
- filter sets

Cache the translated market metadata in:

- `caches/aster/markets.json`
- `caches/aster/coin_to_symbol_map.json`
- `caches/symbol_to_coin_map.json`

### 9.5 Standardize symbol mapping

Aster market ids are raw ids like `BTCUSDT`.
Passivbot wants normalized symbols like `BTC/USDT:USDT`.

Phase 1 decision:

- canonical Aster symbol inside Passivbot is `BTC/USDT:USDT`
- raw API symbol remains `BTCUSDT`
- every REST/WS adapter method must translate at the boundary

## 10. Planned Acceptance Criteria for Phase 1

Phase 1 is complete only when all of these are true:

- Aster V3 is documented as the primary target.
- The repo layout for Aster modules is fixed.
- The balance policy is fixed.
- The hedge-mode risk is explicitly gated.
- The shared-file edit list is minimized and documented.
- The future CCXT-upgrade option is documented but not required.
- There is no plan item that depends on modifying `lighter` internals.

## 11. Open Questions to Resolve Before Phase 2 Begins

### 11.1 V3 user streams in practice

Need to confirm against a live account:

- whether `/fapi/v3/listenKey` works for the account type in use
- whether V3 private events match the documented payloads
- whether V1 is needed at all for current production accounts

### 11.2 Hedge mode safety

Need a small live verification:

- can a close order in hedge mode be safely represented without `reduceOnly`
- is `side + positionSide` enough to guarantee no unintended position flips

If not, phase 2 should launch one-way mode first.

### 11.3 Balance aggregation in multi-asset mode

Need to validate whether:

- `USDF`, `USDT`, and `USDC` are all equally margin-usable for the intended account
- `availableBalance` behaves as expected across assets

### 11.4 Whether a custom OHLCV loader is needed

The preferred plan is to use the generic backtest/HLCV path later. Only introduce an Aster-specific historical collector if generic paginated `/fapi/v3/klines` fetching proves too slow or the current downloader architecture cannot support a non-CCXT exchange cleanly.

## 12. Sources Used for This Phase

External:

- https://docs.asterdex.com/for-developers/aster-api
- https://docs.asterdex.com/for-developers/aster-api/how-to-create-an-api-key
- https://docs.asterdex.com/product/aster-perpetuals/api/api-documentation
- https://docs.asterdex.com/trading/sub-accounts
- https://github.com/asterdex/api-docs/blob/master/README.md
- https://github.com/asterdex/api-docs/blob/master/V3%28Recommended%29/EN/aster-finance-futures-api-v3.md
- https://github.com/asterdex/aster-connector-python
- https://github.com/asterdex/aster-broker-pro-sdk
- https://github.com/ccxt/ccxt/wiki/manual

Local:

- `README.md`
- `src/passivbot.py`
- `src/exchanges/exchange_interface.py`
- `src/exchanges/ccxt_bot.py`
- `src/exchanges/lighter.py`
- `src/utils.py`
- `src/hlcv_preparation.py`
- `src/fill_events_manager.py`
- `ASTER_code_example/README.md`
- `ASTER_code_example/api_client.py`
- `ASTER_code_example/market_maker.py`
- `ASTER_code_example/data_collector.py`
