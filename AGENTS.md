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

## v0.4.3 Fixes (2026-09-03)

- **Dead history reads (three hooks)**: `History` has NO `.messages`
  attribute — it lives on `Topic` (helpers/history.py:185); the accessor is
  `History.all_messages()` (helpers/history.py:455). Reading
  `getattr(history, "messages", None)` returned None every time, so:
  - `_10_compress_history` (message_loop_prompts_before) — the ENTIRE
    old-history compression feature was dead since the hook was written
    (every audit missed it; found by grep sweep in the third pass). Fixed
    to `history.all_messages()`.
  - `hist_add_before/_10_compress_user_message` — the initial-task guard I
    added in v0.4.2 read the nonexistent attribute, fired unconditionally,
    and made user-message compression a total no-op (my own regression).
    Fixed the same way.
  - `_05_auto_clarity` — the `_last_user_message` lookup was the same dead
    read, so auto-clarity never saw the previous user turn. Fixed.

## v0.4.2 Audit Fixes (2026-09-03)

- **One-turn-late compression**: compression hooks moved from `before_main_llm_call/` to `message_loop_prompts_before/`. In `agent.py:prepare_prompt()` the `message_loop_prompts_before` point fires BEFORE `history.output()` materializes the prompt, so compressed history now affects the CURRENT turn instead of the next one. The two remaining hooks (`_05_auto_clarity`, `_10_compress_history`) only read `agent.history`, so the move is behavior-preserving apart from timing. (`_20_shrink_tool_descriptions` was removed later the same day -- see below.)
- **Caveman bridge wired**: `record_caveman_savings()` had no caller (dashboard read the `caveman_bridge` event kind but nothing wrote it). New `extensions/python/monologue_end/_30_caveman_bridge.py` estimates output savings per monologue (chars/4 × caveman's level fraction, gated by caveman's own per-chat state + global config) and records the event.
- Title updated to "Headroom Context Compression (CCR)" — distinct from emasoudy/headroom-compress-a0 in the Plugin Hub.
- **Caveman bridge stats fixed**: events were recorded as `input=0, output=saved`, but the dashboard computes `saved = max(0, input - output)` — every `caveman_bridge` row showed 0 saved. Now modeled as full-length vs actual output tokens. Recording is also skipped when caveman's per-chat state is unreadable (no phantom credits), and the default level comes from caveman's config, not a hardcoded "full".
- **Removed `_20_shrink_tool_descriptions` (dead feature)**: it read `agent.tools`, which the framework never defines — tool descriptions enter the system prompt from `agent.system.tool.*.md` prompt files (`extensions/python/system_prompt/_11_tools_prompt.py`), so the hook was a silent no-op since v0.3.0. Config knobs `shrink_tool_descriptions*` removed; README claim dropped.
- **Settings checkbox fixed**: the "Auto-compress tool outputs" checkbox bound to `$store.headroomStore.autoCompressToolOutputs`, whose getter/setter read `this.config` — a field the standalone store never receives, so it always displayed checked and toggling did nothing. Now binds directly to `config.auto_compress_tool_outputs_min_tokens` (>0 = on) on the modal's config scope.

## Verification

Toggle compression on, run a chat that would normally exceed the model context window, confirm the agent continues without a context-length error.

## See also

- `plugin.yaml` — manifest (name, version, settings_sections, per_project_config, per_agent_config)
- `default_config.yaml` — defaults (referenced by `install()` and the WebUI settings UI)
- `README.md` — user-facing docs (what the plugin does from a user's perspective)
- Framework references: `helpers/plugins.py` (lifecycle), `helpers/api.py` (API dispatch), `helpers/ui_server.py` (asset serving)
