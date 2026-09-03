"""
Headroom Context Compression - CCR (reversible compression) cache.

Stores the full original content for any string the compressor shrank, so the
LLM (or the user) can call `headroom_retrieve(key)` and get the full original
back on demand. Without this, lossy compression would be irreversible - the
LLM could only see the compressed version.

We deliberately use a *tiny* SQLite-backed implementation instead of importing
the headroom-ai CCR store directly:
  * Zero risk of clashing with the headroom-ai Rust-backed default backend.
  * Survives even when headroom-ai is not installed (e.g. on a fresh toggle-ON
    before the user has run execute.py).
  * Easy to inspect, dump, and back up from the file system.
  * Trivially prunable by TTL.

Three backends are supported but only SQLite and in_memory are implemented
locally; the headroom-ai Redis backend can be enabled in the config but we
don't take a hard dep on redis-py.
"""

from __future__ import annotations

import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import Any

from usr.plugins.headroom_compress.helpers import config as _config




def _iso_from_epoch(epoch: float) -> str:
    """Best-effort ISO-8601 UTC string from a Unix epoch.
    Falls back to the raw number on platforms where datetime fails.
    """
    try:
        e = float(epoch or 0.0)
    except (TypeError, ValueError):
        return ""
    if e <= 0.0:
        return ""
    try:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(e, tz=timezone.utc).isoformat()
    except Exception:  # noqa: BLE001
        return f"{e:.0f}"


def _format_age(seconds: float) -> str:
    """Human-readable age string from a seconds count."""
    s = int(max(0.0, seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60}s"
    if s < 86400:
        h, rem = divmod(s, 3600)
        return f"{h}h{rem // 60}m"
    d, rem = divmod(s, 86400)
    return f"{d}d{rem // 3600}h"


class CcrCache:
    """Thread-safe CCR store keyed by 32-char sha256 prefixes."""

    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self.backend = (cfg.get("ccr_backend") or "sqlite").lower()
        self.ttl_days = int(cfg.get("ccr_ttl_days", 7) or 0)
        self._lock = threading.Lock()
        self._mem: dict[str, tuple[bytes, int, int, float, str | None]] = {}
        self._db: sqlite3.Connection | None = None

        if self.backend == "in_memory":
            pass
        elif self.backend == "sqlite":
            self._init_sqlite()
        elif self.backend == "redis":
            self._print(
                "redis backend requested - the local plugin does not depend on redis-py. "
                "Install with: pip install redis. Falling back to in_memory until then."
            )
            self.backend = "in_memory"
        else:
            self._print(f"unknown ccr_backend={self.backend!r}, falling back to in_memory")
            self.backend = "in_memory"

    def _print(self, msg: str) -> None:
        sys.stderr.write(f"[headroom_compress.ccr] {msg}\n")
        sys.stderr.flush()

    def _db_path(self) -> Path:
        return _config.ccr_db_path(self.cfg)

    def _init_sqlite(self) -> None:
        path = self._db_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=NORMAL")
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS ccr (
                    key TEXT PRIMARY KEY,
                    original BLOB NOT NULL,
                    original_tokens INTEGER NOT NULL,
                    compressed_tokens INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    source TEXT
                );
                CREATE INDEX IF NOT EXISTS ccr_created_at ON ccr(created_at);
                """
            )
        except Exception as exc:  # noqa: BLE001
            self._print(f"sqlite init failed ({exc}); falling back to in_memory")
            self._db = None
            self.backend = "in_memory"

    def put(
        self,
        key: str,
        original: str,
        original_tokens: int,
        compressed_tokens: int,
        source: str | None = None,
    ) -> None:
        if not key or not original:
            return
        now = time.time()
        blob = original.encode("utf-8", errors="replace")
        with self._lock:
            if self.backend == "sqlite" and self._db is not None:
                try:
                    self._db.execute(
                        """
                        INSERT INTO ccr(key, original, original_tokens, compressed_tokens, created_at, source)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(key) DO UPDATE SET
                            original=excluded.original,
                            original_tokens=excluded.original_tokens,
                            compressed_tokens=excluded.compressed_tokens,
                            created_at=excluded.created_at,
                            source=excluded.source
                        """,
                        (key, blob, original_tokens, compressed_tokens, now, source),
                    )
                except Exception as exc:  # noqa: BLE001
                    self._print(f"sqlite put failed: {exc}")
            else:
                self._mem[key] = (blob, original_tokens, compressed_tokens, now, source)

    def get(self, key: str) -> str | None:
        if not key:
            return None
        with self._lock:
            if self.backend == "sqlite" and self._db is not None:
                try:
                    cur = self._db.execute(
                        "SELECT original, created_at FROM ccr WHERE key = ?", (key,)
                    )
                    row = cur.fetchone()
                    if not row:
                        return None
                    blob, created_at = row
                    if self.ttl_days > 0 and (time.time() - float(created_at)) > self.ttl_days * 86400:
                        try:
                            self._db.execute("DELETE FROM ccr WHERE key = ?", (key,))
                        except Exception:  # noqa: BLE001
                            pass
                        return None
                    return blob.decode("utf-8", errors="replace")
                except Exception as exc:  # noqa: BLE001
                    self._print(f"sqlite get failed: {exc}")
                    return None
            entry = self._mem.get(key)
            if not entry:
                return None
            blob, _ot, _ct, created_at, _src = entry
            if self.ttl_days > 0 and (time.time() - created_at) > self.ttl_days * 86400:
                self._mem.pop(key, None)
                return None
            return blob.decode("utf-8", errors="replace")

    def list_keys(self, limit: int = 200) -> list[dict[str, Any]]:
        """Lightweight listing for the dashboard. Returns dicts with metadata,
        not the original blob (which can be huge)."""
        out: list[dict[str, Any]] = []
        with self._lock:
            if self.backend == "sqlite" and self._db is not None:
                try:
                    cur = self._db.execute(
                        "SELECT key, original_tokens, compressed_tokens, created_at, source "
                        "FROM ccr ORDER BY created_at DESC LIMIT ?",
                        (int(limit),),
                    )
                    for row in cur.fetchall():
                        out.append(
                            {
                                "key": row[0],
                                "original_tokens": row[1],
                                "compressed_tokens": row[2],
                                "created_at": row[3],
                                "source": row[4],
                            }
                        )
                    return out
                except Exception as exc:  # noqa: BLE001
                    self._print(f"sqlite list_keys failed: {exc}")
                    return out
            for key, entry in sorted(self._mem.items(), key=lambda kv: kv[1][3], reverse=True)[:limit]:
                blob, ot, ct, ts, src = entry
                out.append(
                    {
                        "key": key,
                        "original_tokens": ot,
                        "compressed_tokens": ct,
                        "created_at": ts,
                        "source": src,
                    }
                )
        return out

    def prune_expired(self) -> int:
        """Delete entries older than the configured TTL. Returns the number of
        rows removed. Safe to call on a schedule (e.g. daily from a scheduled
        task) or on demand from the dashboard."""
        if self.ttl_days <= 0:
            return 0
        cutoff = time.time() - self.ttl_days * 86400
        with self._lock:
            if self.backend == "sqlite" and self._db is not None:
                try:
                    cur = self._db.execute("DELETE FROM ccr WHERE created_at < ?", (cutoff,))
                    return int(cur.rowcount or 0)
                except Exception as exc:  # noqa: BLE001
                    self._print(f"sqlite prune failed: {exc}")
                    return 0
            before = len(self._mem)
            self._mem = {k: v for k, v in self._mem.items() if v[3] >= cutoff}
            return before - len(self._mem)

    def get_meta(self, key: str) -> dict[str, Any] | None:
        """Return rich metadata for a CCR key without the full blob.

        Returned dict (always safe to JSON-encode):
        - key: the CCR key
        - exists: True
        - original_tokens: tokens in the original blob
        - compressed_tokens: tokens in the compressed replacement (approximate)
        - size_bytes: byte length of the original (utf-8 encoded)
        - size_chars: character length of the original
        - age_seconds: seconds since the entry was stored
        - created_at: epoch seconds
        - created_at_iso: ISO-8601 UTC string for human display
        - source: optional source label passed at compression time
        - preview: first 400 chars of the original (truncated)
        - confidence: heuristic 0.0-1.0 quality estimate based on compression ratio
        - ratio: original_tokens / compressed_tokens (>= 1.0)

        Returns None when the key is not found or expired.
        """
        with self._lock:
            entry = self._read_entry(key)
            if entry is None:
                return None
            blob_bytes, orig_tok, comp_tok, created_at, source = entry
            try:
                blob = blob_bytes.decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                return None
            age = max(0.0, time.time() - float(created_at or 0.0))
            ratio = (orig_tok / comp_tok) if comp_tok else 1.0
            # Confidence: higher ratio = cleaner reversible compression. Clamp 0..1.
            confidence = max(0.0, min(1.0, 0.5 + 0.05 * (ratio - 1.0)))
            return {
                "key": key,
                "exists": True,
                "original_tokens": int(orig_tok or 0),
                "compressed_tokens": int(comp_tok or 0),
                "size_bytes": len(blob_bytes),
                "size_chars": len(blob),
                "age_seconds": int(age),
                "created_at": float(created_at or 0.0),
                "created_at_iso": _iso_from_epoch(float(created_at or 0.0)),
                "source": source or "",
                "preview": (blob[:400] + ("…" if len(blob) > 400 else "")),
                "confidence": round(confidence, 3),
                "ratio": round(ratio, 3),
            }

    def _read_entry(self, key: str) -> tuple | None:
        """Internal: read a raw entry tuple or None. Caller must hold the lock."""
        if self.backend == "sqlite" and self._db is not None:
            try:
                cur = self._db.execute(
                    "SELECT original, original_tokens, compressed_tokens, created_at, source "
                    "FROM ccr WHERE key = ?",
                    (key,),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                if self.ttl_days > 0 and (time.time() - (row[3] or 0.0)) > self.ttl_days * 86400:
                    try:
                        self._db.execute("DELETE FROM ccr WHERE key = ?", (key,))
                        self._db.commit()
                    except Exception:  # noqa: BLE001
                        pass
                    return None
                return (row[0], row[1], row[2], row[3], row[4])
            except Exception as exc:  # noqa: BLE001
                self._print(f"sqlite get_meta failed: {exc}")
                return None
        entry = self._mem.get(key)
        if entry is None:
            return None
        blob, ot, ct, ts, src = entry
        if self.ttl_days > 0 and (time.time() - ts) > self.ttl_days * 86400:
            self._mem.pop(key, None)
            return None
        return entry

    def stats(self) -> dict[str, int]:
        """Aggregate counters for the dashboard: total entries, total original
        tokens, total compressed tokens."""
        with self._lock:
            if self.backend == "sqlite" and self._db is not None:
                try:
                    cur = self._db.execute(
                        "SELECT COUNT(*), COALESCE(SUM(original_tokens),0), "
                        "COALESCE(SUM(compressed_tokens),0) FROM ccr"
                    )
                    row = cur.fetchone()
                    return {
                        "count": int(row[0] or 0),
                        "original_tokens": int(row[1] or 0),
                        "compressed_tokens": int(row[2] or 0),
                    }
                except Exception as exc:  # noqa: BLE001
                    self._print(f"sqlite stats failed: {exc}")
                    return {"count": 0, "original_tokens": 0, "compressed_tokens": 0}
            total_o = sum(v[1] for v in self._mem.values())
            total_c = sum(v[2] for v in self._mem.values())
            return {
                "count": len(self._mem),
                "original_tokens": int(total_o),
                "compressed_tokens": int(total_c),
            }

    def close(self) -> None:
        with self._lock:
            if self._db is not None:
                try:
                    self._db.close()
                except Exception:  # noqa: BLE001
                    pass
                self._db = None
