"""Auto-clarity pass for Headroom Context Compression.

Runs in `message_loop_prompts_before`. Detects destructive commands in the user
message and sets a per-context skip flag so the compressor refuses to
shrink that chat's context. Mirrors Caveman's auto-clarity rule on the
input side so safety wins and cost wins.

Framework contract (helpers/extension.py): extensions are discovered by
modules.load_classes_from_folder, which only picks up CLASSES subclassing
helpers.extension.Extension. A bare module-level function is never discovered,
so this must be a class.

The skip flag itself lives in helpers/clarity.py - a shared helper module
imported via the canonical `usr.plugins...` package path by BOTH this
extension and the compressor. It must NOT live in this file: A0 loads
extension files as synthetic modules (basename, not registered in sys.modules),
so importing this file by package path would create a second module instance
and the flag would never cross between the two.
"""

from __future__ import annotations

import re
import sys
from typing import Any

from helpers.extension import Extension

from usr.plugins.headroom_compress.helpers import clarity as _clarity_store

PLUGIN_NAME = "headroom_compress"

_DESTRUCTIVE_PATTERNS: list[tuple[str, str]] = [
    (r"\brm\s+-rf?\b", "rm_rf"),
    (r"\brm\s+-fr?\b", "rm_rf"),
    (r"\bgit\s+push\s+(-f|--force(|-with-lease))\b", "git_force_push"),
    (r"\bgit\s+reset\s+--hard\b", "git_reset_hard"),
    (r"\bgit\s+clean\s+-fdx?\b", "git_clean"),
    (r"\bDROP\s+(TABLE|DATABASE|SCHEMA)\b", "drop_table"),
    (r"\bTRUNCATE\s+TABLE\b", "truncate"),
    (r"\bDELETE\s+FROM\s+\w+\s*;?\s*$", "delete_all"),
    (r"\bformat\s+[a-zA-Z]:", "format_drive"),
    (r"\bshutdown\s+/s\b", "shutdown"),
    (r"\b(?:curl|wget)\s+[^|]*\|\s*(?:sh|bash|sudo)\b", "remote_pipe_shell"),
    (r"\bchmod\s+-R\s+777\b", "chmod_777"),
    (r"\bdd\s+if=/dev/(zero|random|urandom)\s+of=/dev/sd", "dd_overwrite"),
    (r"\bmkfs\.", "mkfs"),
]

_COMPILED = [(re.compile(pat, re.IGNORECASE), label) for pat, label in _DESTRUCTIVE_PATTERNS]


def _print(msg: str) -> None:
    sys.stderr.write("[headroom_compress/auto_clarity] %s\n" % msg)
    sys.stderr.flush()


def _detect(message: str) -> tuple[bool, str]:
    """Return (matched, label) if the user message contains a destructive pattern."""
    if not message:
        return False, ""
    for regex, label in _COMPILED:
        if regex.search(message):
            return True, label
    return False, ""


def _extract_text(content: Any) -> str:
    """Pull plain text out of a MessageContent value (str or dict)."""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        for key in ("content", "message", "text"):
            val = content.get(key)
            if isinstance(val, str):
                return val
    return ""


def _last_user_message(agent: Any) -> str:
    """Best-effort: text of the most recent non-AI history message.

    Tool-result messages (hist_add_tool_result stores {tool_name,
    tool_result} dicts) extract to "" -- keep scanning past them so the
    user's actual destructive-command message is still detected after the
    first tool call of the task.
    """
    try:
        history = getattr(agent, "history", None)
        messages = getattr(history, "messages", None) if history is not None else None
        if not messages:
            return ""
        for msg in reversed(list(messages)):
            if getattr(msg, "ai", False):
                continue
            if getattr(msg, "summary", None):
                continue
            text = _extract_text(getattr(msg, "content", None))
            if text and text.strip():
                return text
            continue
    except Exception:
        return ""
    return ""


def _resolve_context_id(agent: Any) -> str:
    try:
        ctx = getattr(agent, "context", None)
        if ctx is not None and getattr(ctx, "id", None):
            return str(ctx.id)
    except Exception:
        pass
    return ""


class AutoClarity(Extension):
    """Set a compression-skip flag when the latest user message is destructive."""

    async def execute(self, loop_data: dict | None = None, **kwargs) -> None:
        try:
            if not self.agent:
                return
            ctx_id = _resolve_context_id(self.agent)
            if not ctx_id:
                return
            last_user_text = _last_user_message(self.agent)
            matched, label = _detect(last_user_text)
            if matched:
                _clarity_store.set_skip(ctx_id, label)
                _print("skip flag set for %s (label=%s)" % (ctx_id, label))
        except Exception as exc:
            _print("message_loop_prompts_before error: %s" % exc)
