"""History compression hook — runs in `message_loop_prompts_before`.

This is the SECONDARY fix. The primary fix (tool-output compression) already
catches new large outputs as they enter history. This hook catches OLD
uncompressed content already sitting in history from before the plugin was
active, and also compresses long assistant responses that accumulated.

Agent Zero calls:
    await extension.call_extensions_async("message_loop_prompts_before", self, loop_data=...)

We walk self.agent.history.messages and compress large text content in place.
We NEVER touch:
  - system messages
  - the first user message (initial task)
  - content under auto_compress_history_min_tokens

When the plugin is disabled, this hook is a zero-cost no-op.
"""

from __future__ import annotations

import sys
from typing import Any

from helpers.extension import Extension

from usr.plugins.headroom_compress.helpers import compressor
from usr.plugins.headroom_compress.helpers import config as _config


def _print(msg: str) -> None:
    sys.stderr.write(f"[headroom_compress] {msg}\n")
    sys.stderr.flush()


def _extract_text(content: Any) -> str | None:
    """Extract a plain-text string from a MessageContent value.
    Returns None if the content is not a simple string."""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        val = content.get("content") or content.get("message") or content.get("text")
        if isinstance(val, str):
            return val
    return None


def _set_text(content: Any, new_text: str) -> Any:
    """Replace text in a MessageContent value, preserving structure."""
    if isinstance(content, str):
        return new_text
    if isinstance(content, dict):
        for key in ("content", "message", "text"):
            if key in content and isinstance(content[key], str):
                content[key] = new_text
                return content
    return content


class CompressHistory(Extension):
    """Compress large history entries before the main LLM call."""

    async def execute(self, loop_data: dict | None = None, **kwargs) -> None:
        if not self.agent:
            return

        cfg = _config.get_config(agent=self.agent)
        if not cfg.get("enabled", False):
            return
        if not cfg.get("auto_compress_history", False):
            return

        min_tokens = int(cfg.get("auto_compress_history_min_tokens", 4000) or 0)
        if min_tokens <= 0:
            return

        # Auto-clarity: consume the destructive-command skip flag ONCE per
        # turn, here, so it protects the whole history walk. If we let each
        # per-message compress_text() call consume it instead, only the first
        # large message would be protected and the rest would compress anyway.
        # (compress_text also consults the flag for single-shot calls; after
        # this consume it finds nothing, which is the desired outcome.)
        try:
            ctx_id = str(getattr(getattr(self.agent, "context", None), "id", "") or "")
            if ctx_id:
                from usr.plugins.headroom_compress.helpers import clarity as _clarity_store

                skip_label = _clarity_store.consume_skip(ctx_id)
                if skip_label:
                    if cfg.get("verbose", True):
                        _print(f"history compression skipped (auto_clarity:{skip_label})")
                    return
        except Exception:
            pass

        history = getattr(self.agent, "history", None)
        if history is None:
            return

        messages = getattr(history, "messages", None)
        if not messages:
            return

        total_compressed = 0
        total_saved = 0

        for idx, msg in enumerate(messages):
            if idx == 0:
                continue
            if getattr(msg, "ai", False) is False and idx < 2:
                continue
            if getattr(msg, "summary", False):
                continue

            content = getattr(msg, "content", None)
            if content is None:
                continue

            text = _extract_text(content)
            if not text:
                continue

            cheap_tokens = compressor._cheap_token_count(text)
            if cheap_tokens < min_tokens:
                continue

            source = f"history:msg[{idx}]"
            result = compressor.compress_text(
                text,
                agent=self.agent,
                source=source,
            )

            if result.get("compressed") and result.get("saved_tokens", 0) > 0:
                new_text = result["text"]
                if result.get("ccr_key"):
                    ccr_key = result["ccr_key"]
                    new_text = (
                        f"[compressed by headroom - original saved as CCR key {ccr_key}; "
                        f"call headroom_retrieve with action=get key={ccr_key} to restore]\n"
                        f"{new_text}"
                    )
                msg.content = _set_text(content, new_text)
                total_compressed += 1
                total_saved += result.get("saved_tokens", 0)

        if total_compressed > 0 and cfg.get("verbose", True):
            _print(
                f"history compression: {total_compressed} messages compressed, "
                f"{total_saved} tokens saved"
            )
