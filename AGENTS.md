# headroom_compress

> Toggleable context compression for Agent Zero. Wraps the headroom library (LLM context-window budgeter) so users can flip compression on/off from the WebUI without editing config files.

**Version:** 0.4.2 · **Plugin ID:** `headroom_compress`

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

## v0.4.2 Audit Fixes (2026-09-03)

- **One-turn-late compression**: compression hooks moved from `before_main_llm_call/` to `message_loop_prompts_before/`. In `agent.py:prepare_prompt()` the `message_loop_prompts_before` point fires BEFORE `history.output()` materializes the prompt, so compressed history now affects the CURRENT turn instead of the next one. All three hooks (`_05_auto_clarity`, `_10_compress_history`, `_20_shrink_tool_descriptions`) only read `agent.history`/`agent.tools`, so the move is behavior-preserving apart from timing.
- **Caveman bridge wired**: `record_caveman_savings()` had no caller (dashboard read the `caveman_bridge` event kind but nothing wrote it). New `extensions/python/monologue_end/_30_caveman_bridge.py` estimates output savings per monologue (chars/4 × caveman's level fraction, gated by caveman's own per-chat state + global config) and records the event.
- Title updated to "Headroom Context Compression (CCR)" — distinct from emasoudy/headroom-compress-a0 in the Plugin Hub.

## Verification

Toggle compression on, run a chat that would normally exceed the model context window, confirm the agent continues without a context-length error.

## See also

- `plugin.yaml` — manifest (name, version, settings_sections, per_project_config, per_agent_config)
- `default_config.yaml` — defaults (referenced by `install()` and the WebUI settings UI)
- `README.md` — user-facing docs (what the plugin does from a user's perspective)
- Framework references: `helpers/plugins.py` (lifecycle), `helpers/api.py` (API dispatch), `helpers/ui_server.py` (asset serving)
