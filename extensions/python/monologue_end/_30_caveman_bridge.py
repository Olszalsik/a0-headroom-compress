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
            # caveman's global config (same defaults its monologue_end
            # extension uses). Read lazily so caveman's config, not ours,
            # decides whether it is actually active.
            caveman_cfg: dict = {}
            try:
                from helpers import plugins as plugins_helper

                caveman_cfg = plugins_helper.get_plugin_config("caveman") or {}
            except Exception:  # noqa: BLE001
                pass
            caveman_default_on = bool(caveman_cfg.get("enabled", False))
            caveman_default_level = str(caveman_cfg.get("level", "full") or "full")

            # v0.4.2 audit fix: only record when caveman's state is readable
            # for THIS chat. If the state module can't be imported (or the
            # chat id is unavailable) we cannot confirm caveman is actually
            # active — recording then would credit phantom savings.
            state = _load_caveman_state()
            if state is None or not chat_id:
                return
            try:
                if not state.is_enabled(chat_id, caveman_default_on):
                    return
                level = state.get_level(chat_id, caveman_default_level)
                reduction = _FALLBACK_REDUCTION.get(level)
            except Exception as exc:  # noqa: BLE001
                _print(f"caveman state read failed: {exc}")
                return

            if reduction is None:
                reduction = _FALLBACK_REDUCTION.get(level or caveman_default_level, 0.65)
                if level is None:
                    level = "unknown"

            text = _get_response_text(loop_data)
            if not text:
                return
            est_tokens = max(1, len(text) // 4)
            est_saved = int(est_tokens * reduction)

            # Stats semantics: saved_tokens = max(0, input - output). Model
            # the event as "full-length response would have been est_tokens;
            # caveman actually produced est_tokens - est_saved" so the
            # dashboard's saved column reflects the output-side savings
            # (previously input=0/output=est_saved => saved=0, inert).
            _bridge.record_caveman_savings(
                cfg,
                source="monologue_end",
                input_tokens=est_tokens,
                output_tokens=max(0, est_tokens - est_saved),
                style_level=level,
                details={"chars": len(text), "estimated": True, "saved": est_saved},
            )
        except Exception as exc:  # noqa: BLE001
            _print(f"monologue_end error: {exc}")