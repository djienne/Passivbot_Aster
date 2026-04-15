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

Not validated in this environment:

- real-account live smoke tests against Aster production
