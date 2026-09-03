"""Tool-description shrinking hook - runs after history compression.

Agent Zero exposes the active tools via `self.agent.tools`. The names +
descriptions + parameters of every tool are pasted into the system prompt at
each LLM call, so long descriptions cost tokens on EVERY turn, even turns
with zero tool calls. This hook trims each tool description to a sane
budget while keeping it reversible via the CCR cache.

When the plugin is disabled, this hook is a zero-cost no-op.

v0.3.0 feature: pairs with Caveman for input-side compression.
"""

from __future__ import annotations

import hashlib
import sys
from typing import Any

from helpers.extension import Extension

from usr.plugins.headroom_compress.helpers import compressor
from usr.plugins.headroom_compress.helpers import config as _config


def _print(msg: str) -> None:
    sys.stderr.write(f"[headroom_compress.tools] {msg}\n")
    sys.stderr.flush()


def _shrink_text(text: str, max_chars: int) -> str:
    """Trim a string to max_chars while keeping the first sentence intact."""
    if not text or len(text) <= max_chars:
        return text
    head = text[:max_chars]
    last_period = max(head.rfind(". "), head.rfind("\n"))
    if last_period > max_chars * 0.4:
        head = head[: last_period + 1]
    return head.rstrip() + " [...]"


def _ccr_key_for(text: str, name: str) -> str:
    h = hashlib.sha256()
    h.update(name.encode("utf-8"))
    h.update(text.encode("utf-8", errors="replace"))
    return h.hexdigest()[:32]


class ShrinkToolDescriptions(Extension):
    """Trim long tool descriptions to keep the system prompt cheap."""

    async def execute(self, loop_data: dict | None = None, **kwargs) -> None:
        if not self.agent:
            return

        cfg = _config.get_config(agent=self.agent)
        if not cfg.get("enabled", False):
            return
        if not cfg.get("shrink_tool_descriptions", True):
            return

        max_chars = int(cfg.get("shrink_tool_descriptions_max_chars", 800) or 800)
        min_chars = int(cfg.get("shrink_tool_descriptions_min_chars", 200) or 200)
        if max_chars <= min_chars:
            return

        tools = getattr(self.agent, "tools", None)
        if not tools:
            return

        shrunk = 0
        for tool in tools:
            name = getattr(tool, "name", None) or type(tool).__name__
            desc = getattr(tool, "description", None) or ""
            if not isinstance(desc, str):
                continue
            if len(desc) <= min_chars:
                continue

            original = desc
            new_desc = _shrink_text(original, max_chars)
            if new_desc == original:
                continue

            try:
                if cfg.get("ccr_enabled", True):
                    from usr.plugins.headroom_compress.helpers.ccr_cache import CcrCache
                    ccr = CcrCache(cfg)
                    ccr.put(
                        _ccr_key_for(original, name),
                        original,
                        len(original) // 4,
                        len(new_desc) // 4,
                        source=f"tool_desc:{name}",
                    )
                try:
                    tool.description = new_desc
                except Exception:
                    pass
                shrunk += 1
            except Exception as exc:  # noqa: BLE001
                _print(f"tool desc shrink failed for {name}: {exc}")

        if shrunk > 0 and cfg.get("verbose", True):
            _print(f"shrunk {shrunk} tool descriptions to <= {max_chars} chars")
