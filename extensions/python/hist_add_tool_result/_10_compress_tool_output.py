"""Automatic tool-output compression hook.

This is the PRIMARY fix for the 'no compression happened' bug. Without this
file, the plugin only compressed when the LLM explicitly called the
compress_text tool — which it almost never does. This hook runs on EVERY
tool result before it enters agent history.

Agent Zero's Agent.hist_add_tool_result calls:
    extension.call_extensions_sync("hist_add_tool_result", self, data=data)
where data = {"tool_name": ..., "tool_result": ..., **kwargs}

We mutate data["tool_result"] in place, replacing large outputs with their
headroom-compressed version. The original is stored in the CCR cache so the
LLM can retrieve it via the headroom_retrieve tool.

When the plugin is disabled, this hook is a zero-cost no-op.
"""

from __future__ import annotations

import sys
from typing import Any

from helpers.extension import Extension

# Import the plugin's compressor and config resolver.
# Use the fully-qualified usr.plugins path per Agent Zero plugin conventions.
from usr.plugins.headroom_compress.helpers import compressor
from usr.plugins.headroom_compress.helpers import config as _config


def _print(msg: str) -> None:
    sys.stderr.write(f"[headroom_compress] {msg}\n")
    sys.stderr.flush()


class CompressToolOutput(Extension):
    """Automatically compress large tool outputs before they enter history."""

    def execute(self, data: dict | None = None, **kwargs) -> None:
        if data is None:
            return

        # Resolve the effective config for this agent context.
        # This respects all toggles (global, per-project, per-agent).
        cfg = _config.get_config(agent=self.agent)

        # True no-op when disabled — zero cost, zero side effects.
        if not cfg.get("enabled", False):
            return

        tool_result = data.get("tool_result")
        if not tool_result or not isinstance(tool_result, str):
            return

        # Check the auto-compress threshold from config.
        min_tokens = int(cfg.get("auto_compress_tool_outputs_min_tokens", 0) or 0)
        if min_tokens <= 0:
            return

        # Cheap pre-check: skip small outputs without importing headroom-ai.
        cheap_tokens = compressor._cheap_token_count(tool_result)
        if cheap_tokens < min_tokens:
            return

        tool_name = data.get("tool_name", "unknown")
        source = f"tool:{tool_name}"

        # Call the real compressor. It handles:
        #   - headroom-ai import (lazy, cached)
        #   - CCR storage (if enabled)
        #   - stats recording (if enabled)
        #   - error handling (never breaks the agent)
        result = compressor.compress_text(
            tool_result,
            agent=self.agent,
            source=source,
        )

        # Only replace if compression actually produced savings.
        if result.get("compressed") and result.get("saved_tokens", 0) > 0:
            data["tool_result"] = result["text"]
            # Stash the CCR key so the LLM can retrieve the original.
            if result.get("ccr_key"):
                data["tool_result_ccr_key"] = result["ccr_key"]
                # Prepend a small retrieval hint for the LLM.
                ccr_key = result["ccr_key"]
                data["tool_result"] = (
                    f"[compressed by headroom — original saved as CCR key {ccr_key}; "
                    f"call headroom_retrieve with action=get key={ccr_key} to restore]\n"
                    f"{result['text']}"
                )

            if cfg.get("verbose", True):
                _print(
                    f"auto-compressed tool '{tool_name}': "
                    f"{result['original_tokens']} -> {result['output_tokens']} tokens "
                    f"({result['saved_tokens']} saved, ratio {result['ratio']:.2f})"
                )
