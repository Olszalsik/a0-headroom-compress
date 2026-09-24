"""P3: drop-old-tool-results clearing (Anthropic context-editing style).

At `message_loop_prompts_before` (right before the LLM call), walks the live
history and replaces all but the most recent N tool results with a short
placeholder. This is the lossless-if-refetchable lever: the agent can either
re-run the tool (the data is re-derivable) or restore the exact original via
the CCR cache (headroom_retrieve), so nothing is destroyed - it is just no
longer paid for on every turn.

Evidence (see tmp/headroom-compression-roadmap-2026-09-24.md):
- TRACE (arXiv 2608.06503): every compression method underperforms full
  context on AppWorld because of execution-state mislocalization - so we
  keep recent results intact (clear_keep_recent) and make old ones
  recoverable, not deleted.
- Anthropic context editing `clear_tool_uses_20250919`: server-side clearing
  of oldest tool results, keep N recent. This extension is the in-process
  equivalent for Agent Zero's own history.

Tool results are user messages whose content is a dict
{"tool_name": ..., "tool_result": ...} (agent.py hist_add_tool_result).
Clearing mutates content["tool_result"] in place and updates msg.tokens.

When the plugin is disabled or the knob is off, this hook is a zero-cost
no-op. Never clears: the most recent N results, anything below
clear_min_tokens, exempted tool names, or already-cleared messages.
"""

from __future__ import annotations

import sys
from typing import Any

from helpers.extension import Extension

from usr.plugins.headroom_compress.helpers import compressor
from usr.plugins.headroom_compress.helpers import config as _config
from usr.plugins.headroom_compress.helpers.ccr_cache import CcrCache
from usr.plugins.headroom_compress.helpers.stats import StatsRecorder

CLEAR_MARKER = "cleared by headroom"
_PLACEHOLDER_TEMPLATE = (
    "[tool result cleared by headroom (age: {age} results back). "
    "{tokens} tokens freed. Full original: CCR key {ccr_key} "
    "(call headroom_retrieve action=get key={ccr_key}), or re-run the tool.]"
)


def _print(msg: str) -> None:
    sys.stderr.write(f"[headroom_compress] {msg}\n")
    sys.stderr.flush()


def _is_tool_result(msg: Any) -> tuple[bool, str]:
    """Return (is_tool_result_message, tool_name)."""
    content = getattr(msg, "content", None)
    if not isinstance(content, dict):
        return False, ""
    if "tool_result" not in content:
        return False, ""
    return True, str(content.get("tool_name", "unknown"))


class ClearOldToolResults(Extension):
    """Clear old large tool results from history before the LLM call."""

    def execute(self, data: dict | None = None, **kwargs) -> None:
        cfg = _config.get_config(agent=self.agent)
        if not cfg.get("enabled", False):
            return
        if not cfg.get("clear_old_tool_results", True):
            return

        history = getattr(self.agent, "history", None)
        if history is None:
            return
        messages = history.all_messages()
        if not messages:
            return

        keep_recent = max(0, int(cfg.get("clear_keep_recent", 3) or 0))
        min_tokens = int(cfg.get("clear_min_tokens", 300) or 0)
        exempt = {t.strip().lower() for t in (cfg.get("clear_exempt_tools") or []) if t}

        # Collect tool-result indices (chronological).
        tr_idx: list[int] = []
        for idx, msg in enumerate(messages):
            is_tr, _name = _is_tool_result(msg)
            if is_tr and not getattr(msg, "summary", ""):
                tr_idx.append(idx)

        # Nothing to do: nothing older than the protected recent window.
        if len(tr_idx) <= keep_recent:
            return
        clearable = tr_idx[:-keep_recent] if keep_recent else tr_idx

        ccr = CcrCache(cfg) if cfg.get("ccr_enabled", True) else None
        stats = StatsRecorder(cfg)

        total_saved = 0
        total_cleared = 0

        for idx in clearable:
            msg = messages[idx]
            is_tr, tool_name = _is_tool_result(msg)
            if not is_tr:
                continue
            # Age in tool-results: how many newer tool results follow this one.
            age = sum(1 for later in tr_idx if later > idx)
            if tool_name.lower() in exempt:
                continue
            content = msg.content
            result = content.get("tool_result")
            if not isinstance(result, str) or not result:
                continue
            # Idempotency: skip if a previous pass already cleared this msg.
            if CLEAR_MARKER in result:
                continue

            tokens = int(getattr(msg, "tokens", 0) or 0) or compressor._cheap_token_count(result)
            if tokens < min_tokens:
                continue

            ccr_key = None
            placeholder_tokens = None
            if ccr is not None:
                try:
                    placeholder_tokens = compressor._cheap_token_count(
                        _PLACEHOLDER_TEMPLATE.format(
                            age=age, tokens=tokens, ccr_key="0" * 8
                        )
                    )
                    ccr_key = compressor._ccr_key_for(result, f"clear:{tool_name}")
                    ccr.put(
                        ccr_key,
                        result,
                        tokens,
                        placeholder_tokens,
                        source=f"clear:{tool_name}",
                    )
                except Exception:
                    ccr_key = None

            placeholder = (
                _PLACEHOLDER_TEMPLATE.format(
                    age=age, tokens=tokens, ccr_key=ccr_key or "unavailable"
                )
                if ccr_key
                else (
                    f"[tool result cleared by headroom (age: {age} results back). "
                    f"{tokens} tokens freed. Re-run the tool if you need the data.]"
                )
            )
            content["tool_result"] = placeholder

            # Keep the framework's per-message token accounting honest.
            try:
                msg.tokens = msg.calculate_tokens()
            except Exception:
                pass

            total_saved += tokens
            total_cleared += 1
            if cfg.get("verbose", True):
                _print(
                    f"cleared old tool result '{tool_name}' ({tokens} tokens)"
                    + (f", CCR key {ccr_key}" if ccr_key else "")
                )

        if total_cleared and stats is not None:
            try:
                stats.record(
                    kind="clear:tool_results",
                    source="clear:old_tool_results",
                    input_tokens=total_saved,
                    output_tokens=0,
                    duration_ms=0,
                )
            except Exception:
                pass