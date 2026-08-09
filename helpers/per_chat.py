"""Per-chat overrides for Headroom.

JSON file at <plugin_dir>/cache/per_chat_overrides.json maps
context_id -> { enabled: bool, updated_ts: float, note: str }.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

from usr.plugins.headroom_compress.helpers import config as _config

PLUGIN_DIR = _config.PLUGIN_DIR
STORE_PATH = PLUGIN_DIR / "cache" / "per_chat_overrides.json"


def _print(msg: str) -> None:
    sys.stderr.write("[%s/per_chat] %s\n" % (_config.PLUGIN_NAME, msg))
    sys.stderr.flush()


def _ensure_store() -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not STORE_PATH.exists():
        STORE_PATH.write_text("{}", encoding="utf-8")


def _read_all() -> dict[str, dict[str, Any]]:
    try:
        with open(STORE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return {}


def _write_all(data: dict[str, dict[str, Any]]) -> None:
    _ensure_store()
    tmp = STORE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(STORE_PATH)


def is_enabled(context_id: str, default: bool = True) -> bool:
    """Return True if compression is enabled for this chat."""
    if not context_id:
        return default
    data = _read_all()
    entry = data.get(context_id)
    if not entry:
        return default
    return bool(entry.get("enabled", default))


def set_enabled(context_id: str, enabled: bool, note: str = "") -> dict[str, Any]:
    """Set the per-chat override. Returns the new entry."""
    if not context_id:
        raise ValueError("context_id required")
    data = _read_all()
    entry = {
        "enabled": bool(enabled),
        "updated_ts": time.time(),
        "note": note or "",
    }
    data[context_id] = entry
    _write_all(data)
    _print("set %s -> enabled=%s" % (context_id, enabled))
    return entry


def clear(context_id: str) -> bool:
    data = _read_all()
    if context_id in data:
        del data[context_id]
        _write_all(data)
        _print("cleared override for %s" % context_id)
        return True
    return False


def list_all() -> list[dict[str, Any]]:
    data = _read_all()
    return [
        {"context_id": k, **v}
        for k, v in sorted(data.items(), key=lambda kv: kv[1].get("updated_ts", 0), reverse=True)
    ]
