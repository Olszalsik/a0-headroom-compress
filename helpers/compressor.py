"""Headroom compression library mode.

This module is the public entry point for Headroom Context Compression.
It wraps the optional headroom-ai package when available, falls back to a
safe deterministic local transformer when it is not, and exposes a single
`compress_text` function used by all other plugin code (tools, extensions,
API handlers, on-demand tools).

v0.4.0 additions:
- Per-chat override (helpers/per_chat.py) checked before any work.
- Auto-clarity destructive-command skip (extensions/python/message_loop_prompts_before/_05_auto_clarity.py)
  consumed before compression.
"""

from __future__ import annotations

import hashlib
import sys
import time
from typing import Any

from usr.plugins.headroom_compress.helpers import config as _config
from usr.plugins.headroom_compress.helpers.ccr_cache import CcrCache
from usr.plugins.headroom_compress.helpers.stats import StatsRecorder

PLUGIN_NAME = _config.PLUGIN_NAME

_HEADROOM_MODULE: Any = None
_HEADROOM_IMPORT_ATTEMPTED = False
_HEADROOM_IMPORT_ERROR: str | None = None

_per_chat = None
_clarity = None


def _get_per_chat():
    global _per_chat
    if _per_chat is None:
        try:
            from usr.plugins.headroom_compress.helpers import per_chat as _mod
            _per_chat = _mod
        except Exception as exc:
            _print(f"per-chat module unavailable: {exc}")
            _per_chat = False
    return _per_chat if _per_chat else None


def _get_clarity():
    """Return the shared auto-clarity flag store (helpers/clarity.py).

    IMPORTANT: the flag store must live in a *helper* module, not in the
    extension file. A0 loads extension files as synthetic modules (basename,
    not in sys.modules), so importing the extension file via the package path
    would create a second module instance with separate state and the skip
    flag would never reach the compressor. helpers.clarity is imported via the
    canonical usr.plugins package path by both the extension and us, so both
    sides share one store.
    """
    global _clarity
    if _clarity is None:
        try:
            from usr.plugins.headroom_compress.helpers import clarity as _mod
            _clarity = _mod
        except Exception as exc:
            _print(f"auto-clarity module unavailable: {exc}")
            _clarity = False
    return _clarity if _clarity else None


def _resolve_context_id(agent):
    try:
        if agent is not None and getattr(agent, "context", None) is not None:
            return str(getattr(agent.context, "id", "") or "")
    except Exception:
        pass
    return ""


def _print(msg: str) -> None:
    sys.stderr.write(f"[{PLUGIN_NAME}] {msg}\n")
    sys.stderr.flush()


def _cheap_token_count(text: str) -> int:
    """Rough token estimate without importing tiktoken."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def _ccr_key_for(text: str, source: str | None) -> str:
    h = hashlib.sha256()
    h.update((source or "").encode("utf-8"))
    h.update(text.encode("utf-8", errors="replace"))
    return h.hexdigest()[:32]


def _load_headroom() -> Any:
    """Lazy, cached import of the headroom-ai package."""
    global _HEADROOM_MODULE, _HEADROOM_IMPORT_ATTEMPTED, _HEADROOM_IMPORT_ERROR
    if _HEADROOM_IMPORT_ATTEMPTED:
        return _HEADROOM_MODULE
    _HEADROOM_IMPORT_ATTEMPTED = True
    try:
        import headroom
        _HEADROOM_MODULE = headroom
    except Exception as exc:
        _HEADROOM_IMPORT_ERROR = str(exc)
        _HEADROOM_MODULE = None
    return _HEADROOM_MODULE


def _safe_transform(text: str) -> str:
    """Deterministic local compression that does not need headroom-ai."""
    import re
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    lines = [ln.rstrip() for ln in text.split("\n")]
    return "\n".join(lines).strip()


def _normal_transform(text: str, *, level: str, model: str, strategy: str, source: str | None, stats: Any) -> tuple[str | None, str | None]:
    hr = _load_headroom()
    if hr is None:
        return _safe_transform(text), None
    try:
        if hasattr(hr, "compress"):
            result = hr.compress(text, level=level, model=model, strategy=strategy)
            if isinstance(result, str):
                return result, None
            return result.get("compressed_text") or result.get("text") or text, None
        if hasattr(hr, "Compressor"):
            c = hr.Compressor(level=level, model=model)
            return c.compress(text), None
    except Exception as exc:
        return None, str(exc)
    return _safe_transform(text), None


def compress_text(
    text: str,
    agent: Any = None,
    *,
    level: str | None = None,
    model_hint: str | None = None,
    source: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Compress a single string. Safe to call from anywhere."""
    cfg = _config.get_config(agent=agent)

    empty_result = {
        "text": text,
        "compressed": False,
        "original_tokens": _cheap_token_count(text or ""),
        "output_tokens": _cheap_token_count(text or ""),
        "saved_tokens": 0,
        "ratio": 1.0,
        "ccr_key": None,
        "skipped_reason": None,
        "mode": cfg.get("mode", "safe"),
        "dry_run": bool(cfg.get("dry_run", False)),
    }

    if not (cfg.get("enabled", False) or force):
        return {**empty_result, "skipped_reason": "disabled"}
    if not text or not isinstance(text, str):
        return {**empty_result, "skipped_reason": "empty"}

    context_id = _resolve_context_id(agent)
    if context_id:
        pc = _get_per_chat()
        if pc is not None and not pc.is_enabled(context_id):
            return {**empty_result, "skipped_reason": "per_chat_disabled"}
        # Auto-clarity skip: never consulted for explicit (force=True) tool
        # calls - the flag is meant to protect *automatic* compression only.
        if not force:
            ac = _get_clarity()
            auto_clarity_label = None
            if ac is not None:
                try:
                    auto_clarity_label = ac.consume_skip(context_id)
                except Exception:
                    auto_clarity_label = None
            if auto_clarity_label:
                return {**empty_result, "skipped_reason": f"auto_clarity:{auto_clarity_label}"}

    if not force:
        min_tokens = int(cfg.get("auto_compress_tool_outputs_min_tokens", 0) or 0)
        if min_tokens <= 0:
            return {**empty_result, "skipped_reason": "min_tokens=0"}
        if _cheap_token_count(text) < min_tokens:
            return {**empty_result, "skipped_reason": "below_threshold"}

    mode = (cfg.get("mode") or "safe").lower()
    if mode not in {"safe", "normal"}:
        mode = "safe"

    use_level = level or cfg.get("level") or "balanced"
    use_model = model_hint or cfg.get("model_hint") or "gpt-4o"
    use_strategy = (cfg.get("strategy") or "auto").lower()

    stats = StatsRecorder(cfg)
    ccr = CcrCache(cfg) if cfg.get("ccr_enabled", True) else None

    original_tokens = _cheap_token_count(text)
    ccr_key = _ccr_key_for(text, source) if ccr is not None else None

    started = time.perf_counter()
    compressed: str | None = None
    err: str | None = None

    if mode == "safe":
        compressed = _safe_transform(text)
    else:
        compressed, err = _normal_transform(
            text,
            level=use_level,
            model=use_model,
            strategy=use_strategy,
            source=source,
            stats=stats,
        )

    if not compressed:
        return {**empty_result, "skipped_reason": "transform_failed", "error": err}

    output_tokens = _cheap_token_count(compressed)
    saved_tokens = max(0, original_tokens - output_tokens)
    ratio = (output_tokens / original_tokens) if original_tokens else 1.0

    if ccr is not None and ccr_key is not None and saved_tokens > 0:
        try:
            ccr.put(
                ccr_key,
                text,
                original_tokens,
                output_tokens,
                source=source,
            )
        except Exception:
            pass

    try:
        stats.record(
            kind=source or "compress_text",
            source=source or "unknown",
            input_tokens=original_tokens,
            output_tokens=output_tokens,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
    except Exception:
        pass

    return {
        "text": compressed,
        "compressed": True,
        "original_tokens": original_tokens,
        "output_tokens": output_tokens,
        "saved_tokens": saved_tokens,
        "ratio": ratio,
        "ccr_key": ccr_key,
        "skipped_reason": None,
        "mode": mode,
        "dry_run": bool(cfg.get("dry_run", False)),
    }


def retrieve_original(ccr_key: str, agent: Any = None) -> str | None:
    """Return the full original text stored in the CCR cache for `ccr_key`.

    Used by the headroom_retrieve tool (action=get). Returns None when the
    key is unknown, expired, or CCR is disabled - never raises.
    """
    if not ccr_key or not isinstance(ccr_key, str):
        return None
    key = ccr_key.strip()
    # Tolerate the LLM pasting the key out of the hint line, e.g.
    # "key=abc123" or "CCR key abc123".
    for prefix in ("key=", "key ", "ccr key ", "ccr_key="):
        if key.lower().startswith(prefix):
            key = key[len(prefix):].strip()
    key = key.split()[0] if key else ""
    if not key:
        return None
    try:
        cfg = _config.get_config(agent=agent)
        if not cfg.get("ccr_enabled", True):
            return None
        ccr = CcrCache(cfg)
        return ccr.get(key)
    except Exception as exc:  # noqa: BLE001 - retrieval must never break the agent
        _print(f"retrieve_original failed for {key[:8]}...: {exc}")
        return None
