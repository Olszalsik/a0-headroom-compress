"""v2.2 banner extension — welcome-screen setup hint for the headroom plugin.

In Agent Zero v2.2, plugins can append dictionaries to a `banners` list via the
`banners` extension point. The Welcome Screen renders alert banners and
discovery cards from this list.

This banner only appears when headroom-ai is not yet installed in the framework
runtime. Once installed, the banner disappears (so configured plugins don't
keep advertising setup).

Framework contract (see extensions/python/banners/_10_unsecured_connection.py
and helpers/extension.py): extensions are discovered by
modules.load_classes_from_folder, which only picks up CLASSES subclassing
helpers.extension.Extension. A bare module-level execute() function is never
discovered, so this must be a class.
"""

from __future__ import annotations

import importlib.util
from typing import Any

from helpers.extension import Extension


def _headroom_installed() -> bool:
    """Cheap check: is the headroom-ai package importable right now?"""
    return importlib.util.find_spec("headroom") is not None


class HeadroomBanner(Extension):
    """Append setup/feature banners to the Welcome Screen."""

    async def execute(
        self,
        banners: list = [],
        frontend_context: dict = {},
        **kwargs: Any,
    ) -> None:
        """Append setup banners to the Welcome Screen if headroom-ai is missing."""
        if not isinstance(banners, list):
            return

        if _headroom_installed():
            # Already installed - still advertise the Caveman bridge.
            _append_caveman_bridge_card(banners)
            return

        banners.append(
            {
                "id": "headroom_compress_setup_hint",
                "type": "warning",
                "priority": 50,
                "title": "Headroom plugin: dependency missing",
                "description": (
                    "The headroom-ai Python package is not installed in the framework "
                    "runtime. The plugin is enabled but compression will silently "
                    "pass-through. Open the Headroom settings and click Install / "
                    "upgrade headroom-ai, or rebuild the Docker image with the "
                    "updated requirements.txt."
                ),
                "dismissible": True,
                "cta_text": "Open Headroom settings",
                "cta_action": "open-modal:/usr/plugins/headroom_compress/webui/config.html",
            }
        )

        banners.append(
            {
                "id": "headroom_compress_feature_card",
                "type": "feature",
                "priority": 30,
                "title": "Headroom Context Compression",
                "description": (
                    "Shrink tool outputs, logs, and history by 60-95% before they "
                    "reach the LLM. Originals are kept in a local reversible cache "
                    "(CCR) and can be retrieved on demand. v0.3 also shrinks "
                    "tool descriptions and pairs with the Caveman prompt-pack "
                    "for output-side savings."
                ),
                "cta_text": "Open Headroom settings",
                "cta_action": "open-modal:/usr/plugins/headroom_compress/webui/config.html",
            }
        )

        _append_caveman_bridge_card(banners)


def _append_caveman_bridge_card(banners: list) -> None:
    """Always advertise the Headroom <-> Caveman bridge when Headroom is on."""
    banners.append(
        {
            "id": "headroom_caveman_bridge_card",
            "type": "info",
            "priority": 20,
            "title": "Stack Headroom with Caveman for 2-sided compression",
            "description": (
                "Headroom cuts INPUT tokens (tool outputs, history, tool "
                "descriptions). The Caveman prompt-pack (separate plugin) "
                "cuts OUTPUT tokens by making the model speak tight. Together "
                "they cut both ends of the budget. Install Caveman from the "
                "Plugin Hub to enable the bridge."
            ),
            "dismissible": True,
            "cta_text": "Open Caveman repo",
            "cta_action": "open-url:https://github.com/JuliusBrussee/caveman",
        }
    )
