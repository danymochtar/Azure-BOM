"""Browser-side persistence via streamlit-local-storage.

Wraps the raw component in a small, resilient API:

- `load_prefs()` / `save_prefs()` for non-sensitive UI preferences
- `load_api_key()` / `save_api_key()` / `clear_api_key()` for opt-in
  credential persistence
- `load_last_bom()` / `save_last_bom()` so the last assessment result
  survives browser refreshes

Gotchas:
- The component is async; the first render of a page may return None
  before localStorage responds. All loaders gracefully return defaults.
- All keys are namespaced under `aca_` to avoid collisions with other
  apps sharing the same origin.
- JSON is the on-wire format.
"""
from __future__ import annotations

import json
from typing import Any, Optional

try:
    from streamlit_local_storage import LocalStorage
    _AVAILABLE = True
except Exception:  # dependency missing (e.g. tests) — no-op storage
    _AVAILABLE = False
    LocalStorage = None  # type: ignore


NS = "aca_"
KEY_PREFS = NS + "prefs"
KEY_API = NS + "anthropic_api_key"
KEY_LAST_BOM = NS + "last_bom"
KEY_LAST_PROFILE = NS + "last_profile"


def _client() -> Optional["LocalStorage"]:
    if not _AVAILABLE:
        return None
    try:
        return LocalStorage()
    except Exception:
        return None


def _get_json(key: str, default: Any = None) -> Any:
    ls = _client()
    if ls is None:
        return default
    try:
        raw = ls.getItem(key)
    except Exception:
        return default
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def _set_json(key: str, value: Any) -> None:
    ls = _client()
    if ls is None:
        return
    try:
        ls.setItem(key, json.dumps(value))
    except Exception:
        pass


def _delete(key: str) -> None:
    ls = _client()
    if ls is None:
        return
    try:
        ls.deleteItem(key)
    except Exception:
        pass


# ---------- preferences (non-sensitive) ----------

DEFAULT_PREFS: dict = {
    "app_name": "",
    "region": "",
    "currency": "",
    "strategy_key": "iaas",
    "pricing_mode": "payg",
    "include_lz": True,
    "include_ha": False,
    "include_bcdr": False,
    "lz_selected": [],
    "sec_enabled": [],
    "backup_pct": 40,
    "la_mb_per_vm_per_day": 200,
    "bandwidth_gb": 200,
    "headroom": 1.3,
    "disk_tier": "Premium SSD",
    "os_mode": "as-detected",
}


def load_prefs() -> dict:
    prefs = _get_json(KEY_PREFS, {}) or {}
    merged = {**DEFAULT_PREFS, **prefs}
    return merged


def save_prefs(prefs: dict) -> None:
    # Only keep keys we know about — avoid accidentally persisting bulky state
    clean = {k: prefs.get(k, v) for k, v in DEFAULT_PREFS.items()}
    _set_json(KEY_PREFS, clean)


def clear_prefs() -> None:
    _delete(KEY_PREFS)


# ---------- API key (opt-in) ----------

def load_api_key() -> str:
    val = _get_json(KEY_API, "")
    return val if isinstance(val, str) else ""


def save_api_key(key: str) -> None:
    _set_json(KEY_API, key or "")


def clear_api_key() -> None:
    _delete(KEY_API)


# ---------- last BOM result ----------

def load_last_bom() -> Optional[dict]:
    return _get_json(KEY_LAST_BOM)


def save_last_bom(payload: dict) -> None:
    _set_json(KEY_LAST_BOM, payload)


def clear_last_bom() -> None:
    _delete(KEY_LAST_BOM)


# ---------- nuke everything ----------

def clear_all() -> None:
    clear_prefs()
    clear_api_key()
    clear_last_bom()
    _delete(KEY_LAST_PROFILE)
