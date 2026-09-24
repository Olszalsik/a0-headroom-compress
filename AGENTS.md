# headroom_compress

> Toggleable context compression for Agent Zero. Wraps the headroom library (LLM context-window budgeter) so users can flip compression on/off from the WebUI without editing config files.

**Version:** 0.4.6 · **Plugin ID:** `headroom_compress` · **Last work:** 2026-09-24 P0–P3 (adapter fix, 0.38 upgrade, protect_reads, clear-old-tool-results — live-verified)

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

## v0.4.6 (2026-09-24) — P3: drop-old-tool-results clearing

- New hook `extensions/python/message_loop_prompts_before/_07_clear_old_tool_results.py`
  (`ClearOldToolResults`): before each LLM call, replaces all but the most
  recent `clear_keep_recent` (default 3) tool results with a short placeholder.
  Tool results are user messages with dict content
  `{"tool_name": ..., "tool_result": ...}` (agent.py hist_add_tool_result).
- Config knobs (default_config.yaml + _DEFAULTS + config.json):
  `clear_old_tool_results: true` (default on — lossless-if-refetchable by
  design), `clear_keep_recent: 3`, `clear_min_tokens: 300` (small results are
  cheaper than the placeholder), `clear_exempt_tools: []`.
- Losslessness model: the original is stored in the CCR cache under key
  `compressor._ccr_key_for(result, "clear:<tool>")` BEFORE clearing; the
  placeholder embeds the key so the agent can restore it with
  `headroom_retrieve action=get key=...` or just re-run the tool. Nothing is
  destroyed — it is no longer paid for on every turn.
- Idempotency via `CLEAR_MARKER` in the result; skips already-cleared
  messages, exempted tool names, non-string results, and results below
  `clear_min_tokens`. Mutates `content["tool_result"]` in place and refreshes
  `msg.tokens = msg.calculate_tokens()` so the framework's token accounting
  stays honest. One aggregate stats event per pass:
  `record(kind="clear:tool_results", source="clear:old_tool_results",
  input_tokens=<total freed>)` — shows on the dashboard.
- Evidence: Anthropic context editing (`clear_tool_uses_20250919`, keep-3
  default) + TRACE (arXiv 2608.06503) — recent results stay intact
  (mislocalization protection), old ones become recoverable placeholders.
- Verified: tmp/headroom_verify_p3.py (fake agent + history stub): 6 tool
  results → oldest 2 cleared, recent 3 intact, CCR rows stored with
  source `clear:code_execution_tool`, placeholders carry CCR keys, idempotent
  re-run clears nothing more, msg.tokens recomputed, stats event recorded.
  Runs through the real plugin config path.
- **Live verification 2026-09-24 (real history, no stubs)** —
  tmp/headroom_verify_p3_real.py + _followup.py against the persisted history
  of real chat oQAVhAXH (160 messages, 48 tool results): all tool-result
  messages match the expected shape (dict content, tool_name + str
  tool_result, precomputed tokens); the hook is **already live in production**
  — stats.db shows 5 `clear:tool_results` passes recording 45,282 saved
  tokens, and persisted history holds 5 placeholders. (The "needs restart"
  note above is retired for extension files: the framework extensions
  watchdog — helpers/extension.py `register_extensions_watchdogs` — clears
  the extension-class cache on file change, so new extension files hot-load;
  helper-module changes (P0–P2) still need the pending restart.) All 5
  previously-cleared placeholders match the current template byte-for-byte
  and ALL 5 originals are retrievable from the production CCR db
  (345/1329/407/425/3001 tokens) — losslessness confirmed end-to-end.
  Idempotent in memory AND across a persistence round-trip
  (serialize → deserialize → re-run clears nothing more); the only
  CLEAR_MARKER occurrences in real tool outputs are the hook's own
  placeholders (no genuine-collision false skips).

## v0.4.5 (2026-09-24) — P2: coding-profile protect_reads

- New config knob `protect_reads: true` (default on; helpers/config.py
  `_DEFAULTS` + default_config.yaml + user config.json). Tool outputs whose
  source is file content the agent reasons from (source matching
  `read|editor|file|cat|view|content` — e.g. `tool:text_editor`) are never
  structurally compressed: lossless `_safe_transform` whitespace collapse
  only. Evidence: TRACE (arXiv 2608.06503) — execution-state
  mislocalization is the #1 compression failure mode; protect_reads is
  headroom's own `coding`-profile semantics adapted to the per-message hook
  architecture (the proxy-mode `coding` savings profile is not reachable
  from library-mode per-message compression).
- Router/SmartCrusher continue to compress everything else (logs/JSON/tool
  dumps). CCR stays on; never_compress_system_prompts stays on.
- `level` remains advisory (0.28+ router self-selects; no level knob).

## v0.4.4 Fixes (2026-09-24)

- **Normal-mode adapter rebuilt for headroom-ai v0.28+** (helpers/compressor.py
  `_normal_transform`). The old adapter called the retired string-level API
  `hr.compress(text, level=…, strategy=…)`; installed headroom-ai 0.28.0 takes
  `compress(messages, model, config) -> CompressResult` (message-list,
  context-window-fit), so EVERY normal-mode call raised and the plugin silently
  fell back to `_safe_transform` whitespace collapse. Consequence: the
  `level` setting (balanced/aggressive) was never applied at all — 0.28 has no
  level knob (v0.28 `CompressConfig` = target_ratio/min_tokens/protect_recent/
  compress_user_messages/savings_profile `agent-90|balanced`).
- New adapter targets the v0.28 per-content layer that matches this plugin's
  per-message hooks: `transforms.content_router.route_and_compress(text,
  context=source)` for `strategy: auto`, `transforms.smart_crusher.
  smart_crush_tool_output()` for `strategy: smart_crusher`. Router declines
  protected content (code, tracebacks) unchanged — honest 0-saved events;
  errors fall back to `_safe_transform`.
- Verified in-container via plugin `compress_text()` (real config, mode normal):
  repetitive log 200 lines → **96% saved** (ERROR/WARN lines kept verbatim);
  60-item JSON tool output → **25% saved** (schema-compacted rows);
  traceback → 0% (untouched by design, quality-first).
- requirements.txt pinned `headroom-ai>=0.28.0`.
- `level` is advisory from 0.28 on (router self-selects per content type);
  config kept at `level: balanced` — no behavioural knob exists for it.
- **headroom-ai upgraded 0.28.0 → 0.38.0 (2026-09-24, same day)** with
  `pip install --no-deps` — 0.38 requires `litellm>=1.96.2` but A0 pins
  litellm 1.88.1; a plain install would have force-upgraded litellm to
  1.102.1 (framework-wide risk). Installed without deps: adapter behavior
  verified IDENTICAL (log 96% / JSON 25% / traceback+code 0%). Known
  advisories at runtime: onnxruntime 1.19 < 1.24 → headroom uses pure-Python
  content detection (graceful, no action — do NOT upgrade onnxruntime,
  fastembed/context_engine depends on it); "Kompress model not ready" warning
  is inert unless headroom-ai[ml] is installed + warmed (not planned; ML
  compression rejected by quality budget). litellm stays 1.88.1.
- Upgrade path (see tmp/headroom-compression-roadmap-2026-09-24.md): research
  (2026-09-24) recommends headroom-ai 0.38.0 (Sep 22 2026) with its `coding`
  savings profile (protect_reads, delta-only, ~50% emergent savings) and CCR
  on; NEVER `agent-90` (0.10 keep-ratio — CompressAgent/TRACE show agent
  success collapse at 10-35% retention for tool-heavy agents).
- Verification scripts: tmp/headroom_{level_test,api_probe,config_probe,
  fidelity_test,forced_test,crusher_test,verify_fix}.py.

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
