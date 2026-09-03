"""compress_text: on-demand headroom-ai compression tool for the agent.

The agent can call this tool on any string it has just produced or received that
looks like it could be much shorter. Useful for:
  * Large log dumps from a code_execution call
  * JSON API responses with tons of repeated fields
  * Long stack traces
  * Source code pastes

The compressor is a no-op when the plugin is toggled OFF, so this tool is safe
to register always and costs nothing extra when disabled.

Actions:
  - compress       : compress a single string, return the compressed version
  - estimate       : return the cheap token estimate without compressing
  - toggle_info    : return the current enable / scope / config state
"""

from __future__ import annotations

import json

from helpers.tool import Tool, Response
from usr.plugins.headroom_compress.helpers import compressor
from usr.plugins.headroom_compress.helpers import config as _config


class CompressText(Tool):
    async def execute(self, **kwargs) -> Response:
        action = (self.args.get("action") or "compress").strip().lower()

        if action == "toggle_info":
            cfg = _config.get_config(agent=self.agent)
            enabled_flag = bool(cfg.get("enabled", False))
            try:
                from helpers.plugins import get_toggle_state  # type: ignore

                state = str(getattr(get_toggle_state("headroom_compress"), "value", "") or "")
                # ToggleState is a str enum: "enabled" / "disabled" / "always_enabled".
                toggle_state = {
                    "enabled": "on",
                    "always_enabled": "on",
                    "disabled": "off",
                }.get(state, "unknown")
            except Exception:  # noqa: BLE001
                toggle_state = "unknown"
            data = {
                "plugin_toggle": toggle_state,
                "config_enabled": enabled_flag,
                "effective": enabled_flag and toggle_state != "off",
                "strategy": cfg.get("strategy"),
                "level": cfg.get("level"),
                "model_hint": cfg.get("model_hint"),
                "ccr_enabled": cfg.get("ccr_enabled"),
                "stats_enabled": cfg.get("stats_enabled"),
            }
            return Response(
                message="Current Headroom plugin state:\n\n```json\n"
                + json.dumps(data, indent=2)
                + "\n```",
                break_loop=False,
            )

        if action == "estimate":
            text = self.args.get("text") or ""
            est = compressor._cheap_token_count(text)  # type: ignore[attr-defined]
            data = {"text_length": len(text), "estimated_tokens": est}
            return Response(
                message=f"Estimated tokens: {est} (text length {len(text)}).\n\n```json\n"
                + json.dumps(data, indent=2)
                + "\n```",
                break_loop=False,
            )

        if action == "compress":
            text = self.args.get("text") or ""
            if not isinstance(text, str) or not text:
                return Response(
                    message="compress_text: 'text' argument is required and must be a non-empty string.",
                    break_loop=False,
                )
            level = self.args.get("level")
            model_hint = self.args.get("model_hint")
            source = self.args.get("source") or "compress_text.tool"
            force = bool(self.args.get("force", False))

            result = compressor.compress_text(
                text,
                agent=self.agent,
                level=level,
                model_hint=model_hint,
                source=source,
                force=force,
            )
            return Response(
                message=f"Headroom compress_text result:\n\n```json\n"
                + json.dumps(result, indent=2, default=str)
                + "\n```\n\nCompressed text:\n\n"
                + result["text"],
                break_loop=False,
            )

        return Response(
            message=(
                "Unknown action. Valid actions: "
                "compress, estimate, toggle_info."
            ),
            break_loop=False,
        )
