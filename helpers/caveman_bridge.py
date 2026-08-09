"""Headroom <-> Caveman bridge helpers.

Caveman (juliusbrussee/caveman) is an output-style prompt pack that makes
Agent Zero speak in tight, terse output. Headroom is an input-side
compressor that rewrites tool outputs before the model sees them. Both
stack cleanly: Headroom reduces INPUT tokens, Caveman reduces OUTPUT
tokens. Together they cut both ends of the token budget.

This helper exists so both plugins can advertise each other and so the
Headroom stats dashboard can attribute output-side savings coming from
Caveman. It does NOT auto-enable Caveman - the user must install Caveman
separately and toggle it on. Headroom only records the cooperation.

v0.3.0 feature.
"""

from __future__ import annotations

import importlib.util
import sys
from typing import Any

from usr.plugins.headroom_compress.helpers import config as _config
from usr.plugins.headroom_compress.helpers.stats import StatsRecorder


def _print(msg: str) -> None:
    sys.stderr.write(f"[headroom_compress.caveman_bridge] {msg}\n")
    sys.stderr.flush()


def is_caveman_installed() -> bool:
    """Return True iff the Caveman plugin is discovered by Agent Zero."""
    try:
        spec = importlib.util.find_spec("usr.plugins.caveman")
        return spec is not None
    except Exception:
        return False


def get_caveman_state() -> dict[str, Any]:
    """Inspect Caveman's current state if it exposes one via plugin_state.json."""
    if not is_caveman_installed():
        return {}
    state: dict[str, Any] = {}
    try:
        from pathlib import Path
        import json
        candidate = Path("/a0/usr/plugins/caveman/.plugin_state.json")
        if candidate.exists():
            state = json.loads(candidate.read_text() or "{}")
    except Exception as exc:  # noqa: BLE001
        _print(f"could not read caveman state: {exc}")
    return state


def record_caveman_savings(
    cfg: dict[str, Any],
    *,
    source: str,
    input_tokens: int,
    output_tokens: int,
    style_level: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Record a Caveman-originated compression event so the dashboard
    can attribute OUTPUT savings to Caveman and show the combined stack.
    No-op when stats are disabled or the bridge is off.
    """
    bridge = cfg.get("caveman_bridge", {}) or {}
    if not bridge.get("enabled", False):
        return
    if not bridge.get("track_caveman_stats", True):
        return
    if not is_caveman_installed():
        return
    stats = StatsRecorder(cfg)
    extra: dict[str, Any] = {"side": "output", "style_level": style_level or "unknown"}
    if details:
        extra.update(details)
    stats.record(
        kind="caveman_bridge",
        source=source,
        input_tokens=int(input_tokens),
        output_tokens=int(output_tokens),
        details=extra,
    )
