# Installing Passivbot Aster Fork

This guide covers the current install paths for this repo. The short version is:

- For live trading, Docker or Linux/WSL is still the safest route.
- For local Python installs, use Python 3.12 only.
- `src/main.py`, `src/backtest.py`, and `src/optimize.py` can auto-build the Rust extension when needed.

For the quickest path, see the [README](../README.md). This file adds the extra detail for fresh machines and failed installs.

## 1. Choose a setup path

Use one of these:

- **Docker (recommended for live use)**: lowest-friction path for running the Aster bot continuously
- **Local Python environment**: best when you want to edit code, run tests, backtest, or debug tools directly

## 2. Prerequisites

Required for local installs:

- **Python 3.12 only**
- **Rust 1.90+**
- **C/C++ build tools**
- **A virtual environment** such as `venv` or Conda

Important:

- Do not create new environments on Python 3.10, 3.11, or 3.13.
- This repo will refuse to start on older Python versions.

Platform notes:

- **Ubuntu/Debian**:
  - `sudo apt install python3.12 python3.12-venv python3.12-dev build-essential`
- **Rust**:
  - install via [rustup](https://rustup.rs/) if `rustc --version` is not available
- **macOS**:
  - install Xcode command-line tools with `xcode-select --install`
- **Windows**:
  - WSL2 is recommended for local development and live trading

## 3. Clone the repo

```bash
git clone https://github.com/djienne/Passivbot_Aster.git passivbot_aster
cd passivbot_aster
```

## 4. Create and activate an environment

### `venv`

```bash
python3.12 -m venv venv
source venv/bin/activate
```

Windows PowerShell:

```powershell
py -3.12 -m venv venv
.\venv\Scripts\Activate.ps1
```

### Conda

The repo also ships a pinned Conda environment:

```bash
conda env create -f environment.yml
conda activate passivbot
```

## 5. Install Python dependencies

For normal local bot usage:

```bash
pip install -U pip
pip install -r requirements-live.txt
```

For a fuller development/tooling environment:

```bash
pip install -U pip
pip install -r requirements.txt
```

Notes:

- `requirements-live.txt` already includes `requirements-rust.txt`.
- `requirements.txt` includes the live dependencies plus extra developer, docs, optimizer, and dashboard packages.
- `requirements-rust.txt` mainly matters when you want the explicit Rust toolchain pieces such as `maturin`.

## 6. First run and Rust extension behavior

This repo uses a Rust extension from `passivbot-rust/` for performance-critical code.

Current behavior:

- `python src/main.py ...`
- `python src/backtest.py ...`
- `python src/optimize.py ...`

all call the Rust bootstrap helper before loading the main app. If the extension is missing or stale, the repo tries to rebuild it automatically with:

```bash
maturin develop --release
```

That means a normal first run is usually enough:

```bash
python src/main.py configs/config_hype_aster.json
```

Useful flags:

- `--skip-rust-compile`: skip the Rust build check
- `--force-rust-compile`: force a rebuild
- `--fail-on-stale-rust`: fail instead of rebuilding automatically

Example:

```bash
python src/main.py --force-rust-compile configs/config_hype_aster.json
```

## 7. Manual Rust build

If automatic compilation fails, build it explicitly:

```bash
cd passivbot-rust
maturin develop --release
cd ..
```

After that, retry:

```bash
python src/main.py configs/config_hype_aster.json
```

## 8. Aster-first local run

This fork is documented primarily around Aster.

Typical local live command:

```bash
python src/main.py configs/config_hype_aster.json
```

Other shipped Aster examples:

- `configs/config_aster.json`
- `configs/hype_top_aster.json`

Before live trading, also set up:

```bash
cp api-keys.json.example api-keys.json
```

Then fill in the `aster_01` credentials in `api-keys.json`.

See also:

- [aster_live.md](./aster_live.md)
- [live.md](./live.md)

## 9. Docker path

For the repo's default Docker setup:

```bash
docker compose up -d
docker logs -f passivbot-aster-live
```

The current compose file starts `passivbot-aster-live` with `configs/config_hype_aster.json`.

To stop it:

```bash
docker compose down
```

## 10. Verify the install

Helpful checks:

```bash
python --version
rustc --version
python src/main.py -h
python src/backtest.py -h
pytest -q
```

If help commands or tests fail with missing `passivbot_rust`, build the extension manually and try again.

If you want the broadest local tooling and test environment, prefer `pip install -r requirements.txt`.

## 11. Keeping it up to date

When pulling new commits:

```bash
git pull
pip install -r requirements-live.txt
```

If you installed the fuller development environment, repeat `pip install -r requirements.txt` instead.

If Rust sources changed or the extension looks stale:

```bash
cd passivbot-rust
maturin develop --release
cd ..
```

## 12. Troubleshooting

| Symptom | Fix |
|---------|-----|
| `Passivbot requires Python 3.12` | Recreate the environment with Python 3.12. |
| `Rust extension check failed` | Install Rust/build tools, then run `maturin develop --release` in `passivbot-rust/`. |
| `ModuleNotFoundError: passivbot_rust...` | Activate the correct environment and rebuild the Rust extension. |
| `No such command 'maturin'` | Reinstall dependencies or run `pip install -r requirements-rust.txt`. |
| `linker cc not found` or `cannot find crt1.o` | Install system build tools such as `build-essential` and `python3.12-dev`. |
| `maturin develop` cannot find Python | Make sure the virtual environment is active before building. |
| Native Windows install is flaky | Prefer WSL2 or Docker. |

Still stuck? Open an issue with:

- your OS and architecture
- Python version
- Rust version
- the full failing command
- the full traceback or compiler output
