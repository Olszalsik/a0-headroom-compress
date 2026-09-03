"""
Headroom Context Compression - stats recorder.

Writes one row per compression / retrieval event into a local SQLite DB. Used
by the dashboard to show real savings and by the user to verify the plugin is
actually doing work.

The recorder is a no-op if `stats_enabled` is false in the config, so users
who care about maximum performance can disable persistence entirely.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import Any

from usr.plugins.headroom_compress.helpers import config as _config


class StatsRecorder:
    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self.enabled = bool(cfg.get("stats_enabled", True))
        self._lock = threading.Lock()
        self._db: sqlite3.Connection | None = None
        if self.enabled:
            self._init_db()

    def _print(self, msg: str) -> None:
        sys.stderr.write(f"[headroom_compress.stats] {msg}\n")
        sys.stderr.flush()

    def _db_path(self) -> Path:
        return _config.stats_db_path(self.cfg)

    def _init_db(self) -> None:
        path = self._db_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=NORMAL")
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    kind TEXT NOT NULL,
                    source TEXT,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    saved_tokens INTEGER NOT NULL DEFAULT 0,
                    duration_ms REAL NOT NULL DEFAULT 0,
                    details TEXT
                );
                CREATE INDEX IF NOT EXISTS events_ts ON events(ts);
                CREATE INDEX IF NOT EXISTS events_kind ON events(kind);
                """
            )
        except Exception as exc:  # noqa: BLE001
            self._print(f"stats db init failed ({exc}); disabling stats")
            self._db = None
            self.enabled = False

    def record(
        self,
        kind: str,
        source: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        duration_ms: float = 0.0,
        details: dict[str, Any] | None = None,
    ) -> None:
        if not self.enabled or self._db is None:
            return
        saved = max(0, int(input_tokens) - int(output_tokens))
        details_blob = json.dumps(details or {}, ensure_ascii=False, default=str)
        with self._lock:
            try:
                self._db.execute(
                    """
                    INSERT INTO events(ts, kind, source, input_tokens, output_tokens,
                                       saved_tokens, duration_ms, details)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        time.time(),
                        kind,
                        source,
                        int(input_tokens),
                        int(output_tokens),
                        saved,
                        float(duration_ms),
                        details_blob,
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                self._print(f"stats insert failed: {exc}")

    def summary(self, since: float | None = None) -> dict[str, Any]:
        """Return aggregate counters for the dashboard. If `since` is given
        (unix timestamp), only events newer than that are counted.

        v0.3.0: returns per-kind (input vs output side) so the dashboard can
        show the multiplication effect when Headroom is stacked with Caveman.
        """
        empty = {
            "enabled": False,
            "count": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "saved_tokens": 0,
            "avg_duration_ms": 0.0,
            "by_side": {
                "input": {"count": 0, "saved_tokens": 0},
                "output": {"count": 0, "saved_tokens": 0},
                "other": {"count": 0, "saved_tokens": 0},
            },
            "by_kind": {},
        }
        if not self.enabled or self._db is None:
            return empty
        params: tuple = ()
        where = ""
        if since is not None:
            where = " WHERE ts >= ?"
            params = (float(since),)
        try:
            cur = self._db.execute(
                f"""
                SELECT COUNT(*),
                       COALESCE(SUM(input_tokens),0),
                       COALESCE(SUM(output_tokens),0),
                       COALESCE(SUM(saved_tokens),0),
                       COALESCE(AVG(duration_ms),0.0)
                FROM events{where}
                """,
                params,
            )
            count, in_t, out_t, saved, avg = cur.fetchone()

            cur2 = self._db.execute(
                f"""
                SELECT kind, COUNT(*), COALESCE(SUM(saved_tokens),0)
                FROM events{where}
                GROUP BY kind
                """,
                params,
            )
            by_kind: dict[str, dict[str, int]] = {}
            for kind, k_count, k_saved in cur2.fetchall():
                by_kind[kind] = {
                    "count": int(k_count or 0),
                    "saved_tokens": int(k_saved or 0),
                }

            input_total = {"count": 0, "saved_tokens": 0}
            output_total = {"count": 0, "saved_tokens": 0}
            other_total = {"count": 0, "saved_tokens": 0}
            for kind, bucket in by_kind.items():
                if kind in (
                    "compress_text",
                    "compress_history",
                    "compress_tool_output",
                    "compress_user_message",
                    "shrink_tool_descriptions",
                    "retrieve",
                ):
                    input_total["count"] += bucket["count"]
                    input_total["saved_tokens"] += bucket["saved_tokens"]
                elif kind in ("caveman_bridge",):
                    output_total["count"] += bucket["count"]
                    output_total["saved_tokens"] += bucket["saved_tokens"]
                else:
                    other_total["count"] += bucket["count"]
                    other_total["saved_tokens"] += bucket["saved_tokens"]

            return {
                "enabled": True,
                "count": int(count or 0),
                "input_tokens": int(in_t or 0),
                "output_tokens": int(out_t or 0),
                "saved_tokens": int(saved or 0),
                "avg_duration_ms": float(avg or 0.0),
                "by_side": {
                    "input": input_total,
                    "output": output_total,
                    "other": other_total,
                },
                "by_kind": by_kind,
            }
        except Exception as exc:  # noqa: BLE001
            self._print(f"stats summary failed: {exc}")
            return empty

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        if not self.enabled or self._db is None:
            return []
        try:
            cur = self._db.execute(
                """
                SELECT id, ts, kind, source, input_tokens, output_tokens, saved_tokens,
                       duration_ms, details
                FROM events
                ORDER BY id DESC
                LIMIT ?
                """,
                (int(limit),),
            )
            out: list[dict[str, Any]] = []
            for row in cur.fetchall():
                out.append(
                    {
                        "id": row[0],
                        "ts": row[1],
                        "kind": row[2],
                        "source": row[3],
                        "input_tokens": row[4],
                        "output_tokens": row[5],
                        "saved_tokens": row[6],
                        "duration_ms": row[7],
                        "details": row[8],
                    }
                )
            return out
        except Exception as exc:  # noqa: BLE001
            self._print(f"stats recent failed: {exc}")
            return []

    def close(self) -> None:
        with self._lock:
            if self._db is not None:
                try:
                    self._db.close()
                except Exception:  # noqa: BLE001
                    pass
                self._db = None
