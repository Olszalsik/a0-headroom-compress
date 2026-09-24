"""
Headroom Context Compression - plugin configuration resolver.

Reads user-edited settings in the right priority order using the same
mechanism Agent Zero uses for any plugin: get_plugin_config(...).

Layered resolution (highest priority first):
  1. Per-project + per-agent scope: <project>/.a0proj/agents/<profile>/plugins/headroom_compress/config.json
  2. Per-project scope:             <project>/.a0proj/plugins/headroom_compress/config.json
  3. Per-agent scope:               usr/agents/<profile>/plugins/headroom_compress/config.json
  4. Global user scope:             usr/plugins/headroom_compress/config.json
  5. Plugin default:                usr/plugins/headroom_compress/default_config.yaml

Falls back to a hard-coded sane default if none of the above exist.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

PLUGIN_NAME = "headroom_compress"
PLUGIN_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PLUGIN_DIR / "default_config.yaml"

_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "mode": "safe",
    "strategy": "auto",
    "level": "balanced",
    "model_hint": "gpt-4o",
    "auto_compress_tool_outputs_min_tokens": 200,
    "auto_compress_history": True,
    "auto_compress_history_min_tokens": 4000,
    "ccr_enabled": True,
    "ccr_backend": "sqlite",
    "ccr_path": "",
    "ccr_ttl_days": 7,
    "stats_enabled": True,
    "stats_path": "",
    "expose_compress_tool": True,
    "never_compress_system_prompts": True,
    "protect_reads": True,
    "clear_old_tool_results": True,
    "clear_keep_recent": 3,
    "clear_min_tokens": 300,
    "clear_exempt_tools": [],
    "verbose": True,
}


def _coerce(value: Any, fallback: Any) -> Any:
    """If the user's value is of the same type as the default, return it;
    otherwise return the default. This keeps a typo'd string out of a bool
    field without throwing on load."""
    if value is None:
        return fallback
    if isinstance(fallback, bool):
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in ("true", "1", "yes", "on"):
            return True
        if isinstance(value, str) and value.lower() in ("false", "0", "no", "off"):
            return False
        return fallback
    if isinstance(fallback, int) and not isinstance(fallback, bool):
        try:
            return int(value)
        except (TypeError, ValueError):
            return fallback
    if isinstance(fallback, float):
        try:
            return float(value)
        except (TypeError, ValueError):
            return fallback
    return value


def load_defaults() -> dict[str, Any]:
    """Load defaults from default_config.yaml if present, else hard-coded."""
    if not DEFAULT_CONFIG_PATH.exists():
        return dict(_DEFAULTS)
    try:
        import yaml  # PyYAML ships with Agent Zero
    except ImportError:
        return dict(_DEFAULTS)
    try:
        with DEFAULT_CONFIG_PATH.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"[headroom_compress] could not read default_config.yaml: {exc}\n")
        return dict(_DEFAULTS)
    merged = dict(_DEFAULTS)
    for key, value in data.items():
        merged[key] = _coerce(value, merged.get(key))
    return merged


def get_config(agent: Any = None) -> dict[str, Any]:
    """Resolve the active configuration for the current agent / scope.

    Tries Agent Zero's own get_plugin_config() first (which already knows the
    project / agent-profile scope resolution rules). If anything goes wrong
    (e.g. agent is None and we're called too early) we fall back to reading
    the global config.json, then to defaults.
    """
    defaults = load_defaults()

    user_cfg: dict[str, Any] = {}
    try:
        from helpers.plugins import get_plugin_config  # type: ignore

        resolved = get_plugin_config(PLUGIN_NAME, agent=agent)
        if isinstance(resolved, dict):
            user_cfg = resolved
    except Exception:  # noqa: BLE001 - fall through to direct file read
        pass

    if not user_cfg:
        global_path = PLUGIN_DIR / "config.json"
        if global_path.exists():
            try:
                import json

                with global_path.open("r", encoding="utf-8") as fh:
                    user_cfg = json.load(fh) or {}
            except Exception:  # noqa: BLE001
                user_cfg = {}

    merged = dict(defaults)
    for key, value in user_cfg.items():
        merged[key] = _coerce(value, merged.get(key))
    return merged


def is_enabled(agent: Any = None) -> bool:
    """True only if BOTH the Agent Zero toggle file says ON and the config flag
    is on. We treat the config flag as authoritative within the active scope;
    Agent Zero's plugin runtime is responsible for not calling us at all if the
    plugin is toggled off at a higher level."""
    cfg = get_config(agent=agent)
    if not cfg.get("enabled", False):
        return False
    return True


def ccr_db_path(cfg: dict[str, Any]) -> Path:
    raw = (cfg.get("ccr_path") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (PLUGIN_DIR / "ccr" / "ccr.db").resolve()


def stats_db_path(cfg: dict[str, Any]) -> Path:
    raw = (cfg.get("stats_path") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (PLUGIN_DIR / "stats" / "stats.db").resolve()
