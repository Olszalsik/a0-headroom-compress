"""monologue_end hook — record Caveman output-side savings in Headroom stats.

Wires helpers/caveman_bridge.record_caveman_savings(), which previously had
no caller (dead code — the dashboard read the `caveman_bridge` event kind
but nothing ever wrote it).

At the end of each agent monologue, if BOTH plugins are active, estimate the
Caveman-style output reduction for this response (chars/4 * level fraction,
matching caveman's own monologue_end estimator) and record it as a
`caveman_bridge` event so the Headroom dashboard can show the combined
input+output stack. Headroom never mutates the response — this is
bookkeeping only.

Estimation detail is delegated to the caveman plugin when importable
(usr.plugins.caveman.helpers.state) so level names/fractions stay in sync;
falls back to a local copy of the table if caveman moves.

Framework contract: extensions must be classes subclassing
helpers.extension.Extension (module-level functions are never discovered by
modules.load_classes_from_folder).
"""

from __future__ import annotations

import sys
from typing import Any

from helpers.extension import Extension

from usr.plugins.headroom_compress.helpers import config as _config
from usr.plugins.headroom_compress.helpers import caveman_bridge as _bridge

PLUGIN_NAME = "headroom_compress"

# Fallback copy of caveman's REDUCTION_FRACTION (used only when the caveman
# package cannot be imported). Keep in sync with
# usr/plugins/caveman/extensions/python/monologue_end/_70_caveman_stats.py.
_FALLBACK_REDUCTION = {
    "lite": 0.30,
    "full": 0.65,
    "ultra": 0.80,
    "wenyan-lite": 0.55,
    "wenyan-full": 0.70,
    "wenyan-ultra": 0.80,
}


def _print(msg: str) -> None:
    sys.stderr.write(f"[headroom_compress/caveman_bridge] {msg}\n")
    sys.stderr.flush()


def _resolve_context_id(agent: Any) -> str:
    try:
        ctx = getattr(agent, "context", None)
        if ctx is not None and getattr(ctx, "id", None):
            return str(ctx.id)
    except Exception:
        pass
    return ""


def _get_response_text(loop_data: Any) -> str:
    if loop_data is None:
        return ""
    for attr in ("last_response", "response", "last_message"):
        v = getattr(loop_data, attr, None)
        if isinstance(v, str) and v.strip():
            return v
    return ""


def _load_caveman_state():
    """Return caveman's state helper module, or None if not importable."""
    try:
        from usr.plugins.caveman.helpers import state as caveman_state

        return caveman_state
    except Exception:
        return None


class CavemanBridgeStats(Extension):
    """Record Caveman output savings into the Headroom stats store."""

    async def execute(self, loop_data: dict | None = None, **kwargs) -> None:
        try:
            if not self.agent:
                return
            cfg = _config.get_config(agent=self.agent)
            if not cfg.get("enabled", False):
                return
            bridge = cfg.get("caveman_bridge", {}) or {}
            if not bridge.get("enabled", False):
                return
            if not _bridge.is_caveman_installed():
                return

            chat_id = _resolve_context_id(self.agent)
            level: str | None = None
            reduction: float | None = None

            # Mirror caveman's own gating: per-chat state, defaulting to
            # caveman's global config "enabled" (same default its
            # monologue_end extension uses). Read lazily so caveman's
            # config, not ours, decides whether it is actually active.
            caveman_default_on = False
            try:
                from helpers import plugins as plugins_helper

                caveman_default_on = bool(
                    (plugins_helper.get_plugin_config("caveman") or {}).get("enabled", False)
                )
            except Exception:  # noqa: BLE001
                pass

            state = _load_caveman_state()
            if state is not None and chat_id:
                try:
                    if not state.is_enabled(chat_id, caveman_default_on):
                        return
                    level = state.get_level(chat_id, "full")
                    reduction = _FALLBACK_REDUCTION.get(level)
                except Exception as exc:  # noqa: BLE001
                    _print(f"caveman state read failed: {exc}")
                    return
            elif not chat_id:
                # No chat id and no caveman state binding — cannot attribute.
                return

            if reduction is None:
                reduction = _FALLBACK_REDUCTION.get(level or "", 0.65)
                if level is None:
                    level = "unknown"

            text = _get_response_text(loop_data)
            if not text:
                return
            est_tokens = max(1, len(text) // 4)
            est_saved = int(est_tokens * reduction)

            _bridge.record_caveman_savings(
                cfg,
                source="monologue_end",
                input_tokens=0,
                output_tokens=est_saved,
                style_level=level,
                details={"chars": len(text), "estimated": True},
            )
        except Exception as exc:  # noqa: BLE001
            _print(f"monologue_end error: {exc}")