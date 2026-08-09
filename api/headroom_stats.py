"""headroom_stats: dashboard data API for the Headroom plugin.

POST /api/plugins/headroom_compress/headroom_stats

Returns aggregate counters from the local CCR cache and the events DB plus
recent entries. Used by the webui/dashboard.html modal. All queries are
read-only and fail-safe: if the plugin is not installed, the handler returns
an empty payload instead of a stack trace.

IMPORTANT: this file must contain only ONE ApiHandler subclass. The route slug
is the filename (headroom_stats), and the framework only registers
classes[0] per file (see /a0/helpers/api.py line 238).
"""

from __future__ import annotations

import time
from typing import Any

from helpers.api import ApiHandler, Input, Output, Request, Response
from usr.plugins.headroom_compress.helpers.ccr_cache import CcrCache
from usr.plugins.headroom_compress.helpers.stats import StatsRecorder
from usr.plugins.headroom_compress.helpers import config as _config


class HeadroomStats(ApiHandler):
    @classmethod
    def requires_auth(cls):
        return True

    async def process(self, input: Input, request: Request) -> Output:
        try:
            cfg = _config.get_config(agent=getattr(self, "agent", None))
            ccr = CcrCache(cfg)
            stats = StatsRecorder(cfg)

            try:
                limit = int((input or {}).get("limit", 25) or 25)
            except (TypeError, ValueError):
                limit = 25
            try:
                window_hours = int((input or {}).get("window_hours", 24) or 24)
            except (TypeError, ValueError):
                window_hours = 24

            since = time.time() - (window_hours * 3600)
            ccr_stats = ccr.stats()
            stats_summary = stats.summary(since=since)
            lifetime_summary = stats.summary(since=None)
            recent_events = stats.recent(limit=limit)
            recent_ccr = ccr.list_keys(limit=limit)

            ccr_saved = max(
                0,
                int(ccr_stats.get("original_tokens", 0))
                - int(ccr_stats.get("compressed_tokens", 0)),
            )

            return {
                "ok": True,
                "plugin": "headroom_compress",
                "config": {
                    "enabled": bool(cfg.get("enabled", False)),
                    "strategy": cfg.get("strategy"),
                    "level": cfg.get("level"),
                    "model_hint": cfg.get("model_hint"),
                    "ccr_enabled": bool(cfg.get("ccr_enabled", True)),
                    "ccr_backend": cfg.get("ccr_backend"),
                    "stats_enabled": bool(cfg.get("stats_enabled", True)),
                },
                "ccr": {
                    **ccr_stats,
                    "saved_tokens": ccr_saved,
                    "recent": recent_ccr,
                },
                "events": {
                    "window_hours": window_hours,
                    "summary_window": stats_summary,
                    "summary_lifetime": lifetime_summary,
                    "recent": recent_events,
                },
            }
        except Exception as exc:  # noqa: BLE001 - never crash the dashboard
            return Response(
                {"ok": False, "error": str(exc)},
                500,
            )
