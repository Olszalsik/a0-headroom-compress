"""headroom_execute: trigger the execute.py maintenance script via the API.

POST /api/plugins/headroom_stats/headroom_execute

Body: {"args": ["--upgrade", "--extras", "all"]}

We run execute.py as a subprocess so failures are isolated and the user sees
the same output as running it from the shell. Used by the settings modal
"Install / upgrade headroom-ai" button and the "Test compress" probe.

IMPORTANT: this file must contain only ONE ApiHandler subclass. The route slug
is the filename (headroom_execute), and the framework only registers
classes[0] per file (see /a0/helpers/api.py line 238).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from helpers.api import ApiHandler, Input, Output, Request, Response


class HeadroomExecute(ApiHandler):
    @classmethod
    def requires_auth(cls):
        return True

    async def process(self, input: Input, request: Request) -> Output:
        args = (input or {}).get("args") or []
        if not isinstance(args, list):
            return Response({"ok": False, "error": "args must be a list"}, 400)

        plugin_dir = Path(__file__).resolve().parent.parent
        script = plugin_dir / "execute.py"
        if not script.exists():
            return Response({"ok": False, "error": "execute.py not found"}, 404)

        cmd = [sys.executable, str(script), *args]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(plugin_dir),
            )
            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(), timeout=600
                )
            except asyncio.TimeoutError:
                proc.kill()
                return Response(
                    {"ok": False, "error": "execute.py timed out after 10 minutes"},
                    504,
                )
            return {
                "ok": proc.returncode == 0,
                "exit_code": proc.returncode,
                "stdout": stdout_b.decode("utf-8", errors="replace")[-8000:],
                "stderr": stderr_b.decode("utf-8", errors="replace")[-4000:],
            }
        except Exception as exc:  # noqa: BLE001
            return Response({"ok": False, "error": str(exc)}, 500)
