"""headroom_per_chat: get/set/clear per-chat compression overrides.

POST /api/plugins/headroom_compress/headroom_per_chat
Body: {"action": "get|set|clear|list", "context_id": "...", "enabled": bool, "note": "..."}
"""

from __future__ import annotations

from typing import Any

from helpers.api import ApiHandler, Input, Output, Request, Response

from usr.plugins.headroom_compress.helpers import per_chat as _per_chat


class HeadroomPerChat(ApiHandler):
    @classmethod
    def requires_auth(cls):
        return True

    async def process(self, input: Input, request: Request) -> Output:
        try:
            data = input or {}
            action = str(data.get("action") or "list").strip().lower()

            if action == "list":
                return {"ok": True, "overrides": _per_chat.list_overrides()}

            if action == "get":
                context_id = str(data.get("context_id") or "")
                return {"ok": True, "override": _per_chat.get_override(context_id)}

            if action == "set":
                context_id = str(data.get("context_id") or "")
                if not context_id:
                    return Response({"ok": False, "error": "context_id required"}, 400)
                entry = _per_chat.set_override(
                    context_id,
                    enabled=data.get("enabled"),
                    note=data.get("note"),
                )
                return {"ok": True, "entry": entry}

            if action == "clear":
                context_id = str(data.get("context_id") or "")
                if not context_id:
                    return Response({"ok": False, "error": "context_id required"}, 400)
                cleared = _per_chat.set_override(context_id, enabled=None)
                return {"ok": True, "cleared": cleared}

            return Response({"ok": False, "error": "unknown action"}, 400)
        except Exception as exc:
            return Response({"ok": False, "error": str(exc)}, 500)
