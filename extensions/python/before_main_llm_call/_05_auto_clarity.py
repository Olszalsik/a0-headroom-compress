"""Auto-clarity pass for Headroom Context Compression.

Runs in `before_main_llm_call`. Detects destructive commands in the user
message and sets a per-context skip flag so the compressor refuses to
shrink that chat's context. Mirrors Caveman's auto-clarity rule on the
input side so safety wins and cost wins.
"""

from __future__ import annotations

import re
import sys
from typing import Any

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

_SKIP_FLAGS: dict[str, dict[str, Any]] = {}


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


def _resolve_context_id(agent_data: Any) -> str:
    try:
        ctx = agent_data.get("context") if isinstance(agent_data, dict) else None
        if ctx is not None and getattr(ctx, "id", None):
            return str(ctx.id)
    except Exception:
        pass
    return ""


def before_main_llm_call(agent: Any = None, agent_data: Any = None, *args, **kwargs) -> None:
    """Headroom extension hook: set skip flag if message is destructive."""
    try:
        payload = agent_data if agent_data is not None else kwargs.get("data") or kwargs.get("agent_data")
        if payload is None and agent is not None:
            payload = getattr(agent, "agent_data", None) or getattr(agent, "data", None)
        if payload is None:
            return
        ctx_id = _resolve_context_id(payload)
        if not ctx_id:
            return
        history = payload.get("history") if isinstance(payload, dict) else None
        if not history:
            return
        # Find the latest user message
        last_user_text = ""
        try:
            for msg in reversed(list(history)):
                if isinstance(msg, dict) and msg.get("role") == "user":
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        last_user_text = content
                        break
                    if isinstance(content, list):
                        for part in content:
                            if isinstance(part, dict) and part.get("type") == "text":
                                last_user_text = part.get("text", "")
                                break
                        if last_user_text:
                            break
        except Exception:
            return
        matched, label = _detect(last_user_text)
        if matched:
            _SKIP_FLAGS[ctx_id] = {"label": label, "ts": __import__("time").time()}
            _print("skip flag set for %s (label=%s)" % (ctx_id, label))
    except Exception as exc:
        _print("before_main_llm_call error: %s" % exc)


def consume_skip_flag(context_id: str) -> str | None:
    """Compressor calls this. Returns label and clears the flag."""
    if not context_id:
        return None
    entry = _SKIP_FLAGS.pop(context_id, None)
    if not entry:
        return None
    return str(entry.get("label", "destructive"))


def clear_skip_flag(context_id: str) -> bool:
    return _SKIP_FLAGS.pop(context_id, None) is not None


def list_skip_flags() -> dict[str, dict[str, Any]]:
    return {k: dict(v) for k, v in _SKIP_FLAGS.items()}
