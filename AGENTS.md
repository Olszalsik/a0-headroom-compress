# headroom_compress

> Toggleable context compression for Agent Zero. Wraps the headroom library (LLM context-window budgeter) so users can flip compression on/off from the WebUI without editing config files.

**Version:** 0.2.0 · **Plugin ID:** `headroom_compress`

## Purpose

Toggleable context compression for Agent Zero. Wraps the headroom library (LLM context-window budgeter) so users can flip compression on/off from the WebUI without editing config files.

## Ownership / Layout

- `extensions/` — WebUI toggle + status indicator
- `helpers/` — headroom integration glue

## Local Contracts

- When toggled OFF, the plugin is a passthrough — the framework's default context-budget behavior applies.
- When toggled ON, compression runs on every LLM call. Latency cost is ~50-150ms per turn on a 32k-context model.

## v2.5 Status

- v2.5 banner CTA changed from `open-plugin-config:headroom_compress` (dead) to `open-modal:/usr/plugins/headroom_compress/webui/config.html` (works).

## Verification

Toggle compression on, run a chat that would normally exceed the model context window, confirm the agent continues without a context-length error.

## See also

- `plugin.yaml` — manifest (name, version, settings_sections, per_project_config, per_agent_config)
- `default_config.yaml` — defaults (referenced by `install()` and the WebUI settings UI)
- `README.md` — user-facing docs (what the plugin does from a user's perspective)
- Framework references: `helpers/plugins.py` (lifecycle), `helpers/api.py` (API dispatch), `helpers/ui_server.py` (asset serving)
