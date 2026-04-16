![Passivbot](docs/images/pbot_logo_full.svg)

# Passivbot - Aster Fork

This is a heavily modified fork of [Passivbot](https://github.com/enarjord/passivbot) intended and documented for **Aster perpetuals only**.

The main Aster advantage for this fork is simple: **0 maker fees**. Passivbot works best as a maker-style bot that keeps posting and refreshing passive limit orders, so Aster's fee model is a strong fit for how this strategy is meant to run.

This repo still contains a legacy [Lighter](https://lighter.xyz) connector, but treat it as secondary. The README, default configs, Docker setup, and active exchange work in this fork are all aimed at Aster.

:warning: **Use at your own risk** :warning:

> **Deployment note:** Docker or a Linux VPS is recommended for live use.
>
> **Python version policy:** Use **Python 3.12 only** for this repo. Do not use Python 3.10, 3.11, or 3.13.

Upstream base: `v7.8.4`

## Fork Overview

> :warning: This is a **heavily modified fork** of [enarjord/passivbot](https://github.com/enarjord/passivbot) (`v7.8.4`). It is **not a drop-in replacement** for upstream passivbot. The supported and documented path in this fork is Aster Pro / V3.

Key changes from upstream:

- **Aster connector** - V3/Pro API integration for REST, websocket state, fill events, and historical candle flows.
- **Backtesting integration** - Aster candle download and backtesting support wired into the repo's standard HLCV preparation paths.
- **Docker defaults for Aster** - `docker-compose.yml` starts `passivbot-aster-live` with `configs/config_hype_aster.json` by default.
- **Python 3.12-only runtime** - this repo version should be run on Python 3.12.
- **Lighter code retained** - the legacy Lighter path is still in-tree, but it is no longer the main target of docs or default deployment examples.

## Why Aster

- **Intended exchange:** Aster perpetuals via Pro API / V3.
- **Main economic advantage:** Aster currently offers `0 maker fees`.
- **Best fit for Passivbot:** Passivbot is designed around repeatedly posting and refreshing passive orders rather than chasing price with taker entries.
- **Strict maker-only recommendation:** if you want the bot to stay maker-only, set `time_in_force` to `post_only` and `market_orders_allowed` to `false` in your live config before going live.
- **Important accuracy note:** the shipped example configs are Aster-focused, but they are not all locked to strict maker-only defaults out of the box.
- **Do not assume upstream exchange parity:** this fork is custom and diverges significantly from upstream.

## Overview

Passivbot is a cryptocurrency trading bot written in Python and Rust, intended to require minimal user intervention.

It operates on perpetual futures derivatives markets, automatically creating and cancelling limit buy and sell orders on behalf of the user. It does not try to predict future price movements, it does not use technical indicators, nor does it follow trends. Rather, it is a contrarian market maker, providing resistance to price changes in both directions.

Passivbot's behavior may be backtested on historical price data, using the included backtester whose CPU-heavy functions are written in Rust for speed. Also included is an optimizer, which finds better configurations by iterating thousands of backtests with different candidates, converging on the optimal ones with an evolutionary algorithm.

## Strategy

Inspired by the Martingale betting strategy, the bot will make a small initial entry and double down on losing positions multiple times to bring the average entry price closer to current price action. Orders are placed in a grid, ready to absorb sudden price movements. After each re-entry, the bot updates its closing orders at a set take-profit markup so that even a modest reversal may allow the position to be closed in profit.

### Trailing Orders

In addition to grid-based entries and closes, Passivbot may be configured to use trailing entries and trailing closes.

For trailing entries, the bot waits for price to move beyond a specified threshold and then retrace by a defined percentage before placing a re-entry order. For trailing closes, the bot waits before placing closing orders until after price has moved favorably by a threshold percentage and then retraced by a specified percentage.

### Forager

The Forager feature dynamically chooses the most volatile markets on which to open positions. Volatility is defined as the EMA of the log range for the most recent 1-minute candles, i.e. `EMA(ln(high / low))`.

### Unstucking Mechanism

Passivbot manages underperforming, or "stuck", positions by realizing small losses over time. If multiple positions are stuck, the bot prioritizes positions with the smallest gap between the entry price and current market price for unstucking. Losses are limited by ensuring that the account balance does not fall under a set percentage below the past peak balance.

### Maker-Only Note

This fork is best understood as an **Aster maker-strategy fork**. If you intentionally leave market-order fallbacks enabled, you are changing the fee assumptions and drifting away from the main reason this Aster fork exists.

## Quickstart

### Prerequisites

- Docker Desktop or a Linux VPS recommended for live use
- Python 3.12
- Rust >= 1.90 only if running without Docker
- Docker & Docker Compose for the recommended deployment path
- An Aster Pro API setup for Aster trading
- Need an Aster account? Use the referral link and support this work: https://www.asterdex.com/en/referral/164f81

### 1. Clone

```bash
git clone <this-repo> passivbot_aster
cd passivbot_aster
```

### 2. Configure API Keys

Copy the example and fill in your Aster credentials:

```bash
cp api-keys.json.example api-keys.json
```

Edit `api-keys.json` and update the `aster_01` entry:

- `exchange = "aster"`
- `api_user`
- `api_signer`
- `api_private_key`
- optional: `balance_mode`

See also:

- [docs/aster_live.md](docs/aster_live.md)

### 3. Run with Docker (recommended)

```bash
docker compose up -d
docker logs -f passivbot-aster-live
docker compose down
```

By default, `docker-compose.yml` launches `passivbot-aster-live` with `configs/config_hype_aster.json`.

If you want strict maker-only behavior on Aster, review the chosen config before launch and set:

```json
"time_in_force": "post_only",
"market_orders_allowed": false
```

### 4. Run directly (without Docker)

```bash
pip install -r requirements-live.txt
python src/main.py configs/config_hype_aster.json
```

If you use Conda, the repo also includes a pinned environment file:

```bash
conda env create -f environment.yml
conda activate passivbot
```

## Logging

Passivbot uses Python's logging module throughout the bot, backtester, and supporting tools.

- Use `--log-level` on `src/main.py` or `src/backtest.py` to adjust verbosity at runtime. Accepts `warning`, `info`, `debug`, `trace` or numeric `0-3` (`0 = warnings only`, `1 = info`, `2 = debug`, `3 = trace`).
- You can also use `-v` or `--verbose` as a shorthand for `--log-level debug`.
- Persist a default by adding a top-level section to your config: `"logging": {"level": 2}`.
- The CLI flag always overrides the config value for that run.

## Running Multiple Bots

Running several Passivbot instances against the same exchange on one machine is supported. Each process shares the same on-disk OHLCV cache, and the candlestick manager uses short-lived, self-healing locks with automatic stale cleanup so that one stalled process cannot block the rest.

If you run multiple Dockerized bots, do not reuse the compose file unchanged. Give each stack its own:

- `container_name`
- image name
- config path
- account / API credential set

## Requirements

- Python 3.12
- Rust >= 1.90 for building the backtesting extension outside Docker
- [requirements-live.txt](requirements-live.txt) dependencies

## Lighter Status

Lighter support is still present in this repo, but from the perspective of this README it is a **legacy path**.

If you use Lighter anyway:

- use a **Simple Trading Account**
- run on Linux, WSL, or Docker
- review the retained `lighter_01` entry in `api-keys.json.example`

## Pre-optimized Configurations

Coming soon.

See also: https://pbconfigdb.scud.dedyn.io/

## Documentation

For more detailed information about this fork, start with:

- [docs/aster_live.md](docs/aster_live.md)
- [docs/installation.md](docs/installation.md)
- [docs/live.md](docs/live.md)
- [docs/](docs/)

## Third Party Links and Support

For this fork specifically, the Aster referral link above is the simplest way to support ongoing Aster work.

**Passivbot GUI**

A graphical user interface for Passivbot:
https://github.com/msei99/pbgui

**Upstream support links**

The donation links below are inherited from upstream Passivbot:

**BuyMeACoffee:**
https://www.buymeacoffee.com/enarjord

**Donations:**
If the robot is profitable, consider donating as showing gratitude for its development:

- USDT or USDC Binance Smart Chain BEP20:
0x4b7b5bf6bea228052b775c052843fde1c63ec530
- USDT or USDC Arbitrum One:
0x4b7b5bf6bea228052b775c052843fde1c63ec530

Bitcoin (BTC) via Strike:
enarjord@strike.me

## License

This is free and unencumbered software released into the public domain.

Anyone is free to copy, modify, publish, use, compile, sell, or
distribute this software, either in source code form or as a compiled
binary, for any purpose, commercial or non-commercial, and by any
means.

In jurisdictions that recognize copyright laws, the author or authors
of this software dedicate any and all copyright interest in the
software to the public domain. We make this dedication for the benefit
of the public at large and to the detriment of our heirs and
successors. We intend this dedication to be an overt act of
relinquishment in perpetuity of all present and future rights to this
software under copyright law.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
IN NO EVENT SHALL THE AUTHORS BE LIABLE FOR ANY CLAIM, DAMAGES OR
OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE,
ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
OTHER DEALINGS IN THE SOFTWARE.

For more information, please refer to <https://unlicense.org/>
