"""headroom_config: return the merged, scope-aware plugin config.

POST /api/plugins/headroom_compress/headroom_config

Used by the webui/headroom-store.js to populate the settings form with the
currently effective settings (after project/agent-profile scope resolution).

IMPORTANT: this file must contain only ONE ApiHandler subclass. The route slug
is the filename (headroom_config), and the framework only registers
classes[0] per file (see /a0/helpers/api.py line 238).
"""

from __future__ import annotations

from helpers.api import ApiHandler, Input, Output, Request, Response
from usr.plugins.headroom_compress.helpers import config as _config


class HeadroomConfig(ApiHandler):
    @classmethod
    def requires_auth(cls):
        return True

    async def process(self, input: Input, request: Request) -> Output:
        try:
            cfg = _config.get_config(agent=getattr(self, "agent", None))
            return {"ok": True, "config": cfg}
        except Exception as exc:  # noqa: BLE001
            return Response({"ok": False, "error": str(exc)}, 500)
