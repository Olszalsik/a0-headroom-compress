"""
Headroom Context Compression - runtime hooks for Agent Zero.

These hooks are called by the Agent Zero plugin runtime:
  - install()   : after the plugin is placed in usr/plugins/   (copies/sets up)
  - uninstall() : just before the plugin directory is removed   (cleanup)
  - pre_update(): just before the plugin is updated             (snapshot state)

The plugin ships OFF by default (.toggle-0). The user toggles it on in the
Plugins UI; that triggers the first install of the Python dependency, which is
why we keep hooks.py minimal and put the real install logic in execute.py.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

PLUGIN_DIR = Path(__file__).resolve().parent


def _log(message: str) -> None:
    sys.stdout.write(f"[headroom_compress] {message}\n")
    sys.stdout.flush()


def _print(message: str) -> None:
    print(f"[headroom_compress] {message}")


def _plugin_state_path() -> Path:
    return PLUGIN_DIR / ".plugin_state.json"


def _save_state(state: dict[str, Any]) -> None:
    try:
        _plugin_state_path().write_text(json.dumps(state, indent=2))
    except Exception as exc:  # noqa: BLE001 - best-effort state
        _print(f"warning: could not write plugin state: {exc}")


def _load_state() -> dict[str, Any]:
    path = _plugin_state_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text() or "{}")
    except Exception:  # noqa: BLE001
        return {}


def install() -> None:
    """Called once by Agent Zero after the plugin has been copied into place.

    Prepares plugin-owned directories AND ensures headroom-ai is present in
    the framework runtime (/opt/venv-a0). This is a Docker-restart-safe
    persistence fix: if the container was rebuilt without headroom-ai baked
    into the image, this hook will install it on first activation.

    The hook is safe to re-run and runs in the background (non-blocking) so
    the Plugins UI never hangs.
    """
    _print("install() - preparing plugin-owned directories")

    (PLUGIN_DIR / "ccr").mkdir(parents=True, exist_ok=True)
    (PLUGIN_DIR / "stats").mkdir(parents=True, exist_ok=True)
    (PLUGIN_DIR / "cache").mkdir(parents=True, exist_ok=True)

    _save_state({
        "installed": True,
        "version": "0.2.0",
    })

    # Ensure headroom-ai is installed in the framework runtime (Docker-restart safe).
    # This runs in a background thread so the Plugins UI does not block.
    try:
        import threading

        def _post_install():
            # Wait briefly for pip install to finish, then auto-start proxy if configured.
            import time
            time.sleep(2)
            _ensure_proxy_auto_start()

        threading.Thread(target=_ensure_headroom_installed, daemon=True).start()
        threading.Thread(target=_post_install, daemon=True).start()
        _print("install() complete - headroom-ai install and proxy check scheduled in background")
    except Exception as exc:  # noqa: BLE001
        _print(f"install() complete - could not schedule background tasks: {exc}")


def _ensure_proxy_auto_start() -> None:
    """If the plugin config has proxy.auto_start=true, start the proxy.

    Runs in a background thread so the Plugins UI never blocks. Silently
    no-ops if headroom-ai is not yet installed (the pip install thread
    may still be running).
    """
    try:
        from usr.plugins.headroom_compress.helpers.proxy_manager import (
            ProxyManager,
            auto_start_if_configured,
        )
        from usr.plugins.headroom_compress.helpers import config as _config

        cfg = _config.get_config(agent=None)
        if not cfg.get("proxy", {}).get("auto_start", False):
            return
        result = auto_start_if_configured()
        if result:
            _print("proxy auto-started on plugin activation")
        else:
            pm = ProxyManager(cfg)
            st = pm.status()
            _print(f"proxy auto-start did not succeed: running={st.get('running')}")
    except Exception as exc:  # noqa: BLE001
        _print(f"_ensure_proxy_auto_start error: {exc}")


def _ensure_headroom_installed() -> None:
    """Install headroom-ai into the framework runtime if missing.

    Runs in a background thread so the Plugins UI stays responsive. Logs to
    stdout so the user can see progress in the framework logs.
    """
    try:
        # Check if headroom is already importable
        import headroom  # noqa: F401
        _print("headroom-ai already installed, skipping")
        return
    except ImportError:
        pass

    # Detect the framework runtime interpreter. Agent Zero's framework runs
    # in /opt/venv-a0 (Python 3.12). The agent runtime is /opt/venv (3.13).
    # Extension hooks run in the framework runtime, so we install there.
    framework_python = "/opt/venv-a0/bin/python"
    if not os.path.exists(framework_python):
        # Fallback: use current interpreter
        framework_python = sys.executable
        _print(f"framework runtime not at /opt/venv-a0; using {framework_python}")

    _print(f"installing headroom-ai into {framework_python} ...")
    try:
        import subprocess

        result = subprocess.run(
            [framework_python, "-m", "pip", "install", "--quiet", "headroom-ai>=0.27.0"],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode == 0:
            _print("headroom-ai installed successfully")
        else:
            _print(f"headroom-ai install failed (exit {result.returncode}): {result.stderr[-500:]}")
    except subprocess.TimeoutExpired:
        _print("headroom-ai install timed out after 300s")
    except Exception as exc:  # noqa: BLE001
        _print(f"headroom-ai install error: {exc}")


def pre_update() -> dict[str, Any]:
    """Called immediately before the plugin is updated. Return a snapshot of
    state that the new version might need to restore (CCR/SQLite paths, etc.).
    """
    state = _load_state()
    state["snapshot_at"] = __import__("datetime").datetime.utcnow().isoformat() + "Z"
    _print(f"pre_update() - snapshotting state: {state}")
    return state


def uninstall() -> None:
    """Called just before the plugin directory is removed. We DO NOT uninstall
    the `headroom-ai` pip package here, because:
      1. The user may have other agents or tools that depend on it.
      2. The package is small and harmless to leave installed.
      3. Removing it can be slow and noisy.

    We DO stop the local headroom proxy subprocess (if running), remove the
    toggle files and the plugin_state file. CCR db and stats db are kept so
    the user can still inspect historical savings after re-install.
    """
    _print("uninstall() - cleaning up plugin state")

    # Stop the local headroom proxy if it's running.
    try:
        from usr.plugins.headroom_compress.helpers.proxy_manager import ProxyManager
        pm = ProxyManager()
        if pm.is_running():
            _print("uninstall() - stopping local headroom proxy")
            pm.stop()
    except Exception as exc:  # noqa: BLE001
        _print(f"uninstall() - could not stop proxy: {exc}")

    for filename in (".toggle-0", ".toggle-1", ".plugin_state.json"):
        path = PLUGIN_DIR / filename
        if path.exists():
            try:
                path.unlink()
            except Exception as exc:  # noqa: BLE001
                _print(f"warning: could not remove {filename}: {exc}")

    _print("uninstall() complete (pip package left in place; CCR/stats DBs kept)")
