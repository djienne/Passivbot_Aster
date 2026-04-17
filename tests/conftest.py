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


def _install_config_utils_stub():
    if "config_utils" in sys.modules:
        return
    try:
        import importlib

        importlib.import_module("config_utils")
        return
    except Exception:
        pass

    stub = types.ModuleType("config_utils")
    stub.__is_stub__ = True

    def _get_path(config, dotted):
        cur = config
        for part in str(dotted).split("."):
            if not isinstance(cur, dict) or part not in cur:
                raise KeyError(dotted)
            cur = cur[part]
        return cur

    def require_live_value(config, key):
        try:
            return config["live"][key]
        except (KeyError, TypeError) as exc:
            raise KeyError(f"missing live config value: {key}") from exc

    def require_config_value(config, dotted):
        try:
            return _get_path(config, dotted)
        except KeyError as exc:
            raise KeyError(f"missing config value: {dotted}") from exc

    def get_optional_live_value(config, key, default=None):
        try:
            return config.get("live", {}).get(key, default)
        except AttributeError:
            return default

    def get_optional_config_value(config, dotted, default=None):
        try:
            return _get_path(config, dotted)
        except KeyError:
            return default

    stub.require_live_value = require_live_value
    stub.require_config_value = require_config_value
    stub.get_optional_live_value = get_optional_live_value
    stub.get_optional_config_value = get_optional_config_value
    stub.load_config = lambda path, *a, **kw: {}
    stub.add_arguments_recursively = lambda parser, config, *a, **kw: None
    stub.update_config_with_args = lambda config, args, *a, **kw: config
    stub.format_config = lambda config, *a, **kw: config
    stub.normalize_coins_source = lambda source, *a, **kw: source
    stub.expand_PB_mode = lambda mode, *a, **kw: mode
    stub.get_template_config = lambda *a, **kw: {}
    stub.parse_overrides = lambda *a, **kw: {}
    stub.merge_negative_cli_values = lambda config, *a, **kw: config

    sys.modules["config_utils"] = stub


def _install_pure_funcs_stub():
    if "pure_funcs" in sys.modules:
        return
    try:
        import importlib

        importlib.import_module("pure_funcs")
        return
    except Exception:
        pass

    import re

    stub = types.ModuleType("pure_funcs")
    stub.__is_stub__ = True

    def _identity(x, *_a, **_kw):
        return x

    def _str2bool(value):
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        s = str(value).strip().lower()
        return s in ("1", "true", "yes", "y", "on")

    def _flatten(nested):
        out = []
        for item in nested:
            if isinstance(item, (list, tuple, set)):
                out.extend(_flatten(item))
            else:
                out.append(item)
        return out

    def _ensure_millis(ts):
        try:
            ts = float(ts)
        except (TypeError, ValueError):
            return 0
        if ts < 1e12:
            ts *= 1000.0
        return int(ts)

    def _ts_to_date(ts):
        import datetime as _dt

        return _dt.datetime.fromtimestamp(float(ts) / 1000.0, tz=_dt.timezone.utc).isoformat()

    def _shorten_custom_id(cid):
        s = str(cid or "")
        return s if len(s) <= 36 else s[:36]

    def _safe_filename(name):
        return re.sub(r"[^A-Za-z0-9._-]+", "_", str(name))

    def _sort_dict_keys(d):
        if isinstance(d, dict):
            return {k: _sort_dict_keys(d[k]) for k in sorted(d.keys())}
        if isinstance(d, list):
            return [_sort_dict_keys(x) for x in d]
        return d

    def _multi_replace(text, mapping):
        out = str(text)
        for old, new in (mapping or {}).items():
            out = out.replace(old, new)
        return out

    def _filter_orders(orders, *a, **kw):
        return list(orders or [])

    def _determine_side_from_order_tuple(order_tuple, *a, **kw):
        try:
            return "long" if float(order_tuple[0]) > 0 else "short"
        except Exception:
            return "long"

    def _log_dict_changes(old, new, *a, **kw):
        return None

    def _config_pretty_str(cfg, *a, **kw):
        return _json.dumps(cfg, indent=2, sort_keys=True, default=str)

    stub.numpyize = _identity
    stub.denumpyize = _identity
    stub.filter_orders = _filter_orders
    stub.multi_replace = _multi_replace
    stub.shorten_custom_id = _shorten_custom_id
    stub.determine_side_from_order_tuple = _determine_side_from_order_tuple
    stub.str2bool = _str2bool
    stub.flatten = _flatten
    stub.log_dict_changes = _log_dict_changes
    stub.ensure_millis = _ensure_millis
    stub.ts_to_date = _ts_to_date
    stub.config_pretty_str = _config_pretty_str
    stub.sort_dict_keys = _sort_dict_keys
    stub.safe_filename = _safe_filename

    sys.modules["pure_funcs"] = stub


def _install_tools_event_loop_policy_stub():
    mod_name = "tools.event_loop_policy"
    if mod_name in sys.modules:
        return
    try:
        import importlib

        importlib.import_module(mod_name)
        return
    except Exception:
        pass

    parent_name = "tools"
    if parent_name not in sys.modules:
        parent = types.ModuleType(parent_name)
        parent.__path__ = []
        sys.modules[parent_name] = parent
    stub = types.ModuleType(mod_name)
    stub.__is_stub__ = True
    stub.set_windows_event_loop_policy = lambda *a, **kw: None
    sys.modules[mod_name] = stub
    setattr(sys.modules[parent_name], "event_loop_policy", stub)


def _install_simple_stub(name, attrs=None):
    if name in sys.modules:
        return
    try:
        import importlib

        importlib.import_module(name)
        return
    except Exception:
        pass
    stub = types.ModuleType(name)
    stub.__is_stub__ = True
    for k, v in (attrs or {}).items():
        setattr(stub, k, v)
    sys.modules[name] = stub


_install_passivbot_rust_stub()
_install_config_utils_stub()
_install_pure_funcs_stub()
_install_tools_event_loop_policy_stub()

_install_simple_stub(
    "custom_endpoint_overrides",
    {
        "apply_rest_overrides_to_ccxt": lambda client, *a, **kw: client,
        "resolve_custom_endpoint_override": lambda *a, **kw: None,
        "configure_custom_endpoint_loader": lambda *a, **kw: None,
        "get_custom_endpoint_source": lambda *a, **kw: None,
        "load_custom_endpoint_config": lambda *a, **kw: {},
    },
)
_install_simple_stub("config_transform", {"record_transform": lambda *a, **kw: None})
_install_simple_stub(
    "logging_setup",
    {
        "configure_logging": lambda *a, **kw: None,
        "resolve_log_level": lambda *a, **kw: 0,
    },
)
_install_simple_stub("ohlcv_utils", {"dump_ohlcv_data": lambda *a, **kw: None})


def _install_exchange_interface_stub():
    mod_name = "exchanges.exchange_interface"
    if mod_name in sys.modules:
        return
    try:
        import importlib

        importlib.import_module(mod_name)
        return
    except Exception:
        pass

    parent = "exchanges"
    if parent not in sys.modules:
        parent_mod = types.ModuleType(parent)
        parent_mod.__path__ = [os.path.join(SRC_DIR, "exchanges")]
        sys.modules[parent] = parent_mod
    stub = types.ModuleType(mod_name)
    stub.__is_stub__ = True

    class ExchangeInterface:
        pass

    stub.ExchangeInterface = ExchangeInterface
    sys.modules[mod_name] = stub
    setattr(sys.modules[parent], "exchange_interface", stub)


_install_exchange_interface_stub()
_install_simple_stub(
    "warmup_utils",
    {
        "compute_backtest_warmup_minutes": lambda *a, **kw: 0,
        "compute_per_coin_warmup_minutes": lambda *a, **kw: {},
    },
)
_install_hjson_stub()
_install_portalocker_stub()
_install_prettytable_stub()
_install_sortedcontainers_stub()
