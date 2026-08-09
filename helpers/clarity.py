"""Auto-clarity flag store for Headroom v0.4.0.

The auto-clarity extension detects destructive user commands and asks Headroom
to skip compression for that turn. The flag is stored here so the compressor
can consume it without importing the extension module (no cycle).

State file: <plugin_dir>/cache/clarity_flags.json
Shape: {"flags": {"<context_id>": {"reason": "...", "set_at": <epoch>}}, ...}
"""

from __future__ import annotations

import json
import time
from typing import Any

from usr.plugins.headroom_compress.helpers import config as _config

PLUGIN_DIR = _config.PLUGIN_DIR
FLAGS_PATH = PLUGIN_DIR / "cache" / "clarity_flags.json"
FLAG_TTL_SECONDS = 60 * 30


def _read_store() -> dict[str, Any]:
    if not FLAGS_PATH.exists():
        return {"flags": {}}
    try:
        return json.loads(FLAGS_PATH.read_text() or "{}")
    except (OSError, ValueError):
        return {"flags": {}}


def _write_store(store: dict[str, Any]) -> None:
    try:
        FLAGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        FLAGS_PATH.write_text(json.dumps(store, indent=2))
    except OSError:
        pass


def set_skip(context_id: str, reason: str) -> None:
    if not context_id:
        return
    store = _read_store()
    store.setdefault("flags", {})[context_id] = {
        "reason": str(reason)[:200],
        "set_at": int(time.time()),
    }
    _write_store(store)


def consume_skip(context_id: str) -> str | None:
    if not context_id:
        return None
    store = _read_store()
    flags = store.get("flags", {})
    entry = flags.pop(context_id, None)
    if entry is None:
        return None
    set_at = int(entry.get("set_at", 0))
    if FLAG_TTL_SECONDS > 0 and (int(time.time()) - set_at) > FLAG_TTL_SECONDS:
        _write_store(store)
        return None
    _write_store(store)
    return entry.get("reason")


def clear(context_id: str) -> bool:
    store = _read_store()
    flags = store.get("flags", {})
    if context_id in flags:
        flags.pop(context_id, None)
        _write_store(store)
        return True
    return False


def list_active() -> list[dict[str, Any]]:
    store = _read_store()
    flags = store.get("flags", {})
    return [
        {"context_id": cid, "reason": v.get("reason"), "set_at": v.get("set_at")}
        for cid, v in flags.items()
    ]
