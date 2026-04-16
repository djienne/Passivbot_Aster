# Aster Connector Notes

Date updated: 2026-04-16

This repo now includes an `aster` connector for Aster perpetuals with:

- V3-first REST support
- V3 private/public websocket support with REST reconciliation
- fill-event integration
- historical candle fetching for backtesting and downloader flows

Current assumption:

- only the Aster Pro V3 API is supported
- V1/V2 fallback paths are removed from the connector

## Source Of Truth

The current Aster docs are inconsistent across official properties.

- The GitHub docs repo still says `V3 (Recommended)` for new integrations and documents the wallet-signing Pro API used by this connector.
- Parts of the docs website still show older API-key / HMAC-style futures pages and older listen-key examples.

For this Passivbot connector, treat these as authoritative first:

- `https://github.com/asterdex/api-docs/blob/master/README.md`
- `https://github.com/asterdex/api-docs/blob/master/V3(Recommended)/EN/aster-finance-futures-api-v3.md`

Do not use the local `ASTER_code_example` as the source of truth for user-stream auth. It is a useful reference for message shapes and workflows, but it is a legacy/hybrid example and should not override the official V3 docs.

## Credentials

Use the `aster_01` example in `api-keys.json.example`.

Required fields:

- `exchange = "aster"`
- `api_user`
- `api_signer`
- `api_private_key`

These are the current Pro API V3 credentials used by the connector.

Optional fields:

- `balance_mode`

## Current defaults

- quote currency: `USDT`
- example leverage: `10`
- time-in-force:
  - `post_only` maps to `GTX`
  - normal limit maps to `GTC`

## Hedge mode

The connector currently keeps Aster in one-way mode by default. This is the safer default because Aster does not accept `reduceOnly` in hedge mode.

## Websocket Behavior

Current official V3 user-stream rules:

- `listenKey` is valid for 60 minutes after creation
- `PUT /fapi/v3/listenKey` extends it for another 60 minutes
- user streams are accessed at `wss://fstream.asterdex.com/ws/<listenKey>`
- a single websocket connection is only valid for 24 hours
- user data payloads are not guaranteed to be ordered during heavy periods; order them by event time `E`
- websocket server ping cadence documented for market streams is every 5 minutes, with disconnect if no pong is received within 15 minutes

Connector policy in this repo:

- private WS keepalive refresh runs every 30 minutes
- private WS is rotated before the 24-hour cutoff
- private WS does not assume silence means failure
  Quiet accounts may legitimately receive no user events for long periods.
- aiohttp websocket heartbeat/autoping is used so ping/pong is handled automatically
- on reconnect, the connector reconciles balances, positions, and open orders via REST
- reconnects use exponential backoff with jitter rather than a tight retry loop

Practical guidance:

- for private WS, do not trigger reconnects just because there have been no account events for a short period
- for public WS, a shorter stale timer is still reasonable because `bookTicker` should update regularly on active symbols
- if `listenKeyExpired` is received, reacquire a fresh listen key and resync state
- if repeated reconnects happen, back off aggressively to avoid self-inflicted churn and rate-limit pressure

## Troubleshooting

For private user-stream debugging, use:

- `python src/tools/aster_live_user_stream_probe.py configs/config_hype_aster.json --confirm-live`

This probe:

- creates a V3 listen key using the current wallet-signing flow
- optionally sends an immediate keepalive
- connects directly to the private websocket
- logs the exact payloads received, including any `listenKeyExpired` event

If you see immediate `listenKeyExpired` events in production:

- treat that as a listen-key lifecycle/authentication problem, not as a quiet-socket stale timeout
- capture the raw payload and connection age before changing reconnect thresholds
- verify the behavior against the current official V3 docs and production with the probe before changing the main bot logic further

Observed on 2026-04-16:

- a standalone live probe using the current V3 wallet-signing flow was able to create a listen key, connect, and stay connected for 20 seconds with no `listenKeyExpired` event
- the full Passivbot live run still received payloads shaped like:
  - `{"e":"listenKeyExpired","E":...,"listenKey":"..."}`
- in the full bot path, these expirations were seen after roughly `0.5s` to `27.7s` from connect

Inference:

- production does appear to accept the current V3 wallet-signed listen-key flow at least in a direct probe
- the remaining problem is likely in the bot-path lifecycle or startup interaction around the private user stream, not an outright rejection of V3 by Aster production

## Historical data

Aster now works with:

- `src/hlcv_preparation.py`
- `src/downloader.py`
- combined backtest preparation paths

Historical candles come from `/fapi/v3/klines` through the standard Passivbot cache layout.

## Example config

See:

- `configs/config_aster.json`

## Validation status

Implemented and unit-tested in this repo:

- market metadata
- live REST flows
- websocket cache/reconciliation flows
- fill-event recovery
- offline candle/backtest integration

Validated manually in this environment:

- live credential/auth checks
- leverage update on `HYPE`
- post-only order create/detect/cancel via REST and WS
- maker fill detection with zero-fee entry verification
- reduce-only market close detection

Still not validated here:

- long-duration unattended live runs over many hours or days
