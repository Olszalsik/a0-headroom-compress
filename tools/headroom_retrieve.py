"""headroom_retrieve: look up the full original for a compressed CCR key.

The compressor injects a CCR key (a 32-char hex prefix) into the compressed
text it returns, e.g.:

    [headroom: 10144 -> 1260 tokens, saved 8884; CCR key=a1b2c3d4...]

If the LLM (or the user) needs the full original that the compressed text was
derived from, it can call this tool with that key. Without this, lossy
compression would be irreversible - the LLM could only see the smaller
version.

Actions:
  - get       : return the original string for a CCR key (or a not-found msg)
  - meta      : return rich metadata for a CCR key (no full blob)
  - stats     : return aggregate counters for the CCR cache
  - list      : list recent CCR entries (metadata only, not the blobs)
  - prune     : delete entries older than the configured TTL
"""

from __future__ import annotations

import json

from helpers.tool import Tool, Response
from usr.plugins.headroom_compress.helpers import compressor
from usr.plugins.headroom_compress.helpers.ccr_cache import CcrCache
from usr.plugins.headroom_compress.helpers import config as _config


class HeadroomRetrieve(Tool):
    async def execute(self, **kwargs) -> Response:
        action = (self.args.get("action") or "get").strip().lower()
        cfg = _config.get_config(agent=self.agent)

        if not cfg.get("ccr_enabled", True):
            return Response(
                message="CCR is disabled in plugin config. "
                "Enable it in Settings -> Headroom to use headroom_retrieve.",
                break_loop=False,
            )

        ccr = CcrCache(cfg)

        if action == "meta":
            ccr_key = (self.args.get("ccr_key") or "").strip()
            if not ccr_key:
                return Response(
                    message="headroom_retrieve: 'ccr_key' argument is required for action=meta.",
                    break_loop=False,
                )
            meta = ccr.get_meta(ccr_key)
            if meta is None:
                return Response(
                    message=f"CCR key '{ccr_key}' not found (or expired).",
                    break_loop=False,
                )
            from usr.plugins.headroom_compress.helpers.ccr_cache import _format_age
            meta["age_human"] = _format_age(float(meta.get("age_seconds") or 0))
            return Response(
                message="Headroom CCR metadata:\n\n```json\n"
                + json.dumps(meta, indent=2, default=str)
                + "\n```",
                break_loop=False,
            )

        if action == "stats":
            data = ccr.stats()
            return Response(
                message="Headroom CCR stats:\n\n```json\n"
                + json.dumps(data, indent=2)
                + "\n```",
                break_loop=False,
            )

        if action == "list":
            limit = int(self.args.get("limit", 20) or 20)
            entries = ccr.list_keys(limit=limit)
            return Response(
                message=f"Recent CCR entries (limit {limit}):\n\n```json\n"
                + json.dumps(entries, indent=2, default=str)
                + "\n```",
                break_loop=False,
            )

        if action == "prune":
            removed = ccr.prune_expired()
            return Response(
                message=f"Pruned {removed} expired CCR entries (TTL={cfg.get('ccr_ttl_days')} days).",
                break_loop=False,
            )

        if action == "get":
            ccr_key = (self.args.get("ccr_key") or "").strip()
            if not ccr_key:
                return Response(
                    message="headroom_retrieve: 'ccr_key' argument is required for action=get.",
                    break_loop=False,
                )
            original = compressor.retrieve_original(ccr_key, agent=self.agent)
            if original is None:
                return Response(
                    message=f"CCR key '{ccr_key}' not found (or expired).",
                    break_loop=False,
                )
            preview = original if len(original) <= 4000 else original[:4000] + "\n... [truncated]"
            return Response(
                message=(
                    f"CCR key '{ccr_key}' original "
                    f"({len(original)} chars, ~{compressor._cheap_token_count(original)} tokens):\n\n"
                    f"{preview}"
                ),
                break_loop=False,
            )

        return Response(
            message="Unknown action. Valid: get, meta, stats, list, prune.",
            break_loop=False,
        )
