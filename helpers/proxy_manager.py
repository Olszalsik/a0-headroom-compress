"""Proxy mode manager for the Headroom plugin.

Starts and stops the `headroom proxy` subprocess on the local machine. When
the proxy is running, Agent Zero's model providers can be re-pointed to
`http://127.0.0.1:<port>` to transparently compress every LLM request.

The proxy is a separate process so it does not block the agent loop. We
track its PID in a file and clean it up on uninstall / pre_update.

Usage:
    from usr.plugins.headroom_compress.helpers.proxy_manager import ProxyManager
    pm = ProxyManager(cfg)
    await pm.start()
    status = pm.status()
    pm.stop()
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from usr.plugins.headroom_compress.helpers import config as _config

PLUGIN_NAME = _config.PLUGIN_NAME
PLUGIN_DIR = _config.PLUGIN_DIR
PID_FILE = PLUGIN_DIR / "proxy.pid"
LOG_FILE = PLUGIN_DIR / "proxy.log"


def _print(msg: str) -> None:
    sys.stderr.write(f"[headroom_compress/proxy] {msg}\n")
    sys.stderr.flush()


class ProxyManager:
    """Manage the local headroom proxy subprocess."""

    def __init__(self, cfg: dict[str, Any] | None = None):
        self.cfg = cfg or _config.get_config(agent=None)
        proxy_cfg = self.cfg.get("proxy", {}) or {}
        self.host: str = proxy_cfg.get("host", "127.0.0.1")
        self.port: int = int(proxy_cfg.get("port", 8787))
        self.mode: str = proxy_cfg.get("mode", "token")
        self.auto_start: bool = bool(proxy_cfg.get("auto_start", False))
        self.headroom_bin: str = proxy_cfg.get("binary", "/opt/venv-a0/bin/headroom")
        self.env_overrides: dict[str, str] = dict(proxy_cfg.get("env", {}) or {})

    # -----------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------
    def is_running(self) -> bool:
        """Check if the proxy process is alive and listening."""
        pid = self._read_pid()
        if pid is None:
            return False
        try:
            os.kill(pid, 0)  # signal 0 = check existence
        except (OSError, ProcessLookupError):
            self._clear_pid()
            return False
        # Also verify the port is actually accepting connections.
        try:
            with socket.create_connection((self.host, self.port), timeout=1.0):
                return True
        except (OSError, socket.timeout):
            return False

    def status(self) -> dict[str, Any]:
        """Return a JSON-serializable status snapshot for the UI."""
        pid = self._read_pid()
        running = self.is_running()
        return {
            "running": running,
            "host": self.host,
            "port": self.port,
            "url": f"http://{self.host}:{self.port}",
            "mode": self.mode,
            "pid": pid if running else None,
            "auto_start": self.auto_start,
            "binary": self.headroom_bin,
            "log_file": str(LOG_FILE),
        }

    def start(self, wait_seconds: float = 10.0) -> dict[str, Any]:
        """Start the proxy as a background subprocess. Returns status dict."""
        if self.is_running():
            return {"ok": True, "already_running": True, **self.status()}

        if not os.path.exists(self.headroom_bin):
            return {
                "ok": False,
                "error": f"headroom binary not found at {self.headroom_bin}. "
                         f"Run Install / upgrade headroom-ai first.",
            }

        cmd = [
            self.headroom_bin,
            "proxy",
            "--host", self.host,
            "--port", str(self.port),
            "--mode", self.mode,
        ]
        env = os.environ.copy()
        env.update(self.env_overrides)
        env["HEADROOM_HOST"] = self.host
        env["HEADROOM_PORT"] = str(self.port)
        env["HEADROOM_MODE"] = self.mode

        try:
            log_fh = open(LOG_FILE, "ab")
            proc = subprocess.Popen(
                cmd,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                env=env,
                cwd=str(PLUGIN_DIR),
                start_new_session=True,  # detach from parent process group
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"failed to spawn proxy: {exc}"}

        self._write_pid(proc.pid)
        _print(f"started headroom proxy pid={proc.pid} on http://{self.host}:{self.port}")

        # Wait for the port to start accepting connections.
        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            try:
                with socket.create_connection((self.host, self.port), timeout=1.0):
                    return {"ok": True, "started": True, **self.status()}
            except (OSError, socket.timeout):
                time.sleep(0.3)

        # If we timed out, the process may still be starting; report partial success.
        return {
            "ok": True,
            "started": True,
            "port_not_yet_listening": True,
            **self.status(),
        }

    def stop(self) -> dict[str, Any]:
        """Stop the proxy subprocess if running. Returns status dict."""
        pid = self._read_pid()
        if pid is None:
            return {"ok": True, "already_stopped": True, **self.status()}

        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (OSError, ProcessLookupError, PermissionError):
            try:
                os.kill(pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass

        # Wait up to 5s for graceful exit, then force-kill.
        for _ in range(50):
            try:
                os.kill(pid, 0)
                time.sleep(0.1)
            except (OSError, ProcessLookupError):
                self._clear_pid()
                return {"ok": True, "stopped": True, **self.status()}

        try:
            os.kill(pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
        self._clear_pid()
        return {"ok": True, "stopped": True, "force_killed": True, **self.status()}

    def restart(self) -> dict[str, Any]:
        """Stop then start. Used after config changes."""
        self.stop()
        return self.start()

    # -----------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------
    def _read_pid(self) -> int | None:
        if not PID_FILE.exists():
            return None
        try:
            return int(PID_FILE.read_text().strip())
        except (ValueError, OSError):
            return None

    def _write_pid(self, pid: int) -> None:
        try:
            PID_FILE.write_text(str(pid))
        except OSError as exc:  # noqa: BLE001
            _print(f"could not write pid file: {exc}")

    def _clear_pid(self) -> None:
        try:
            PID_FILE.unlink(missing_ok=True)
        except OSError:
            pass


# ---------------------------------------------------------------------
# Convenience functions used by hooks.py
# ---------------------------------------------------------------------
def auto_start_if_configured() -> bool:
    """If the plugin's config has proxy.auto_start=true, start the proxy.
    Called from hooks.py install() so the proxy comes up automatically
    whenever the plugin is enabled."""
    try:
        cfg = _config.get_config(agent=None)
        if not cfg.get("proxy", {}).get("auto_start", False):
            return False
        pm = ProxyManager(cfg)
        result = pm.start()
        if result.get("ok"):
            _print("proxy auto-started on plugin activation")
        else:
            _print(f"proxy auto-start failed: {result.get('error')}")
        return result.get("ok", False)
    except Exception as exc:  # noqa: BLE001
        _print(f"auto_start_if_configured error: {exc}")
        return False
