"""hist_add_before hook — compress large user-message content in v2.2.

New in Agent Zero v2.2: `Agent.hist_add_message` now calls:

    extension.call_extensions_sync("hist_add_before", self, content_data=..., ai=ai)

before the message is appended to history. This is a wider net than
hist_add_tool_result — it catches ALL messages (user, AI, tool, system),
but the framework only calls hist_add_before when @extensible annotations
fire, so we register at the named point.

For the headroom plugin we only compress here when:
  * the message is a USER message (ai=False), AND
  * its text content is large enough to benefit (>auto_compress_history_min_tokens), AND
  * the plugin is enabled and CCR is enabled

We deliberately do NOT touch:
  * system messages (ai is True only for AI responses, so we skip those)
  * the very first user message (idx==0; the initial task)

The hook mutates content_data["content"] in place — the framework then
passes the rewritten content into history.add_message.
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
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        val = content.get("content") or content.get("message") or content.get("text")
        if isinstance(val, str):
            return val
    return None


def _set_text(content: Any, new_text: str) -> Any:
    if isinstance(content, str):
        return new_text
    if isinstance(content, dict):
        for key in ("content", "message", "text"):
            if key in content and isinstance(content[key], str):
                content[key] = new_text
                return content
    return content


class CompressUserMessageBefore(Extension):
    """Pre-add hook: compress large user-message content in v2.2."""

    def execute(self, content_data: dict | None = None, ai: bool = False, **kwargs) -> None:
        if content_data is None:
            return
        if ai:
            # Only compress user-side messages here. AI responses are handled by
            # the message_loop_prompts_before history hook.
            return

        cfg = _config.get_config(agent=self.agent)
        if not cfg.get("enabled", False):
            return
        if not cfg.get("auto_compress_history", False):
            return

        min_tokens = int(cfg.get("auto_compress_history_min_tokens", 4000) or 0)
        if min_tokens <= 0:
            return

        content = content_data.get("content")
        if content is None:
            return

        text = _extract_text(content)
        if not text:
            return

        cheap_tokens = compressor._cheap_token_count(text)
        if cheap_tokens < min_tokens:
            return

        source = "hist_add_before:user"
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
            content_data["content"] = _set_text(content, new_text)

            if cfg.get("verbose", True):
                _print(
                    f"hist_add_before: compressed user message "
                    f"{result['original_tokens']} -> {result['output_tokens']} tokens "
                    f"({result['saved_tokens']} saved)"
                )
