import math
import os
import sys
import types
import json as _json

# Ensure we can import modules from the src/ directory as "downloader"
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC_DIR = os.path.join(ROOT_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)
os.environ.setdefault("SKIP_CCXT_ASSERT", "1")


def _install_hjson_stub():
    if "hjson" in sys.modules:
        return
    try:
        import importlib

        importlib.import_module("hjson")
        return
    except Exception:
        pass

    stub = types.ModuleType("hjson")
    stub.load = lambda fp, *args, **kwargs: _json.load(fp)
    stub.loads = lambda s, *args, **kwargs: _json.loads(s)
    stub.dump = lambda obj, fp, *args, **kwargs: _json.dump(obj, fp)
    stub.dumps = lambda obj, *args, **kwargs: _json.dumps(obj)
    sys.modules["hjson"] = stub


def _install_portalocker_stub():
    if "portalocker" in sys.modules:
        return
    try:
        import importlib

        importlib.import_module("portalocker")
        return
    except Exception:
        pass

    stub = types.ModuleType("portalocker")

    class LockException(Exception):
        pass

    class Lock:
        def __init__(self, path, timeout=None, flags=None, **kwargs):
            self.path = path
            self.timeout = timeout
            self.flags = flags

        def acquire(self):
            return self

        def release(self):
            return None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            return False

    stub.LockException = LockException
    stub.Lock = Lock
    stub.LOCK_SH = 1
    stub.exceptions = types.SimpleNamespace(LockException=LockException)
    sys.modules["portalocker"] = stub


def _install_prettytable_stub():
    if "prettytable" in sys.modules:
        return
    try:
        import importlib

        importlib.import_module("prettytable")
        return
    except Exception:
        pass

    stub = types.ModuleType("prettytable")

    class PrettyTable:
        def __init__(self, *args, **kwargs):
            self.rows = []

        def add_row(self, row):
            self.rows.append(row)

        def get_string(self, *args, **kwargs):
            return "\n".join(str(row) for row in self.rows)

    stub.PrettyTable = PrettyTable
    sys.modules["prettytable"] = stub


def _install_sortedcontainers_stub():
    if "sortedcontainers" in sys.modules:
        return
    try:
        import importlib

        importlib.import_module("sortedcontainers")
        return
    except Exception:
        pass

    stub = types.ModuleType("sortedcontainers")

    class SortedDict(dict):
        pass

    stub.SortedDict = SortedDict
    sys.modules["sortedcontainers"] = stub


def _install_passivbot_rust_stub():
    if "passivbot_rust" in sys.modules:
        return

    try:
        import importlib

        importlib.import_module("passivbot_rust")
        return
    except Exception:
        pass

    stub = types.ModuleType("passivbot_rust")
    stub.__is_stub__ = True

    def _identity(x, *_args, **_kwargs):
        return x

    def _round(value, step):
        if step == 0:
            return value
        return round(value / step) * step

    def _round_up(value, step):
        if step == 0:
            return value
        return math.ceil(value / step) * step

    def _round_dn(value, step):
        if step == 0:
            return value
        return math.floor(value / step) * step

    stub.calc_diff = lambda price, reference: price - reference
    stub.calc_order_price_diff = lambda side, price, market: (
        (0.0 if not market else (1 - price / market))
        if str(side).lower() in ("buy", "long")
        else (0.0 if not market else (price / market - 1))
    )
    stub.calc_min_entry_qty = lambda *args, **kwargs: 0.0
    stub.calc_min_entry_qty_py = stub.calc_min_entry_qty
    stub.round_ = _round
    stub.round_dn = _round_dn
    stub.round_up = _round_up
    stub.round_dynamic = _identity
    stub.round_dynamic_up = _identity
    stub.round_dynamic_dn = _identity
    stub.calc_pnl_long = (
        lambda entry_price, close_price, qty, c_mult=1.0: (close_price - entry_price) * qty
    )
    stub.calc_pnl_short = (
        lambda entry_price, close_price, qty, c_mult=1.0: (entry_price - close_price) * qty
    )
    stub.calc_pprice_diff_int = lambda *args, **kwargs: 0

    def _calc_auto_unstuck_allowance(balance, loss_allowance_pct, pnl_cumsum_max, pnl_cumsum_last):
        balance_peak = balance + (pnl_cumsum_max - pnl_cumsum_last)
        drop_since_peak_pct = balance / balance_peak - 1.0
        return max(0.0, balance_peak * (loss_allowance_pct + drop_since_peak_pct))

    stub.calc_auto_unstuck_allowance = _calc_auto_unstuck_allowance
    stub.calc_wallet_exposure = (
        lambda c_mult, balance, size, price: abs(size) * price / max(balance, 1e-12)
    )
    stub.cost_to_qty = lambda cost, price, c_mult=1.0: (
        0.0 if price == 0 else cost / (price * (c_mult if c_mult else 1.0))
    )
    stub.qty_to_cost = lambda qty, price, c_mult=1.0: qty * price * (c_mult if c_mult else 1.0)

    stub.hysteresis = _identity
    stub.calc_entries_long_py = lambda *args, **kwargs: []
    stub.calc_entries_short_py = lambda *args, **kwargs: []
    stub.calc_closes_long_py = lambda *args, **kwargs: []
    stub.calc_closes_short_py = lambda *args, **kwargs: []
    stub.calc_unstucking_close_py = lambda *args, **kwargs: None

    # Order type IDs must match passivbot_rust exactly
    _order_map = {
        "entry_initial_normal_long": 0,
        "entry_initial_partial_long": 1,
        "entry_trailing_normal_long": 2,
        "entry_trailing_cropped_long": 3,
        "entry_grid_normal_long": 4,
        "entry_grid_cropped_long": 5,
        "entry_grid_inflated_long": 6,
        "close_grid_long": 7,
        "close_trailing_long": 8,
        "close_unstuck_long": 9,
        "close_auto_reduce_twel_long": 10,
        "entry_initial_normal_short": 11,
        "entry_initial_partial_short": 12,
        "entry_trailing_normal_short": 13,
        "entry_trailing_cropped_short": 14,
        "entry_grid_normal_short": 15,
        "entry_grid_cropped_short": 16,
        "entry_grid_inflated_short": 17,
        "close_grid_short": 18,
        "close_trailing_short": 19,
        "close_unstuck_short": 20,
        "close_auto_reduce_twel_short": 21,
        "close_panic_long": 22,
        "close_panic_short": 23,
        "close_auto_reduce_wel_long": 24,
        "close_auto_reduce_wel_short": 25,
        "empty": 65535,
    }
    stub.get_order_id_type_from_string = lambda name: _order_map.get(name, 0)
    stub.order_type_id_to_snake = lambda type_id: {v: k for k, v in _order_map.items()}.get(
        type_id, "other"
    )
    stub.order_type_snake_to_id = lambda name: _order_map.get(name, 0)

    stub.run_backtest = lambda *args, **kwargs: {}
    stub.gate_entries_by_twel_py = lambda *args, **kwargs: []
    stub.calc_twel_enforcer_orders_py = lambda *args, **kwargs: []

    # Minimal stub for orchestrator JSON API
    def _compute_ideal_orders_json(input_json: str) -> str:
        """Stub orchestrator that returns empty orders."""
        import json
        return json.dumps({"orders": []})

    stub.compute_ideal_orders_json = _compute_ideal_orders_json

    sys.modules["passivbot_rust"] = stub


_install_passivbot_rust_stub()
_install_hjson_stub()
_install_portalocker_stub()
_install_prettytable_stub()
_install_sortedcontainers_stub()
