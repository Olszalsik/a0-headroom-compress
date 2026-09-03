# Headroom Context Compression v0.3.0

Toggleable context compression for Agent Zero. Wraps the [headroom-ai](https://github.com/headroomlabs-ai/headroom) library to shrink tool outputs, history, tool descriptions, and chat messages by **60-95%** before they reach the LLM. Originals are stored in a local **CCR cache** (reversible) and can be retrieved on demand.

## What's new in v0.3.0

- **Tool-description shrinking** on every LLM turn via `extensions/python/message_loop_prompts_before/_20_shrink_tool_descriptions.py`. Trims `agent.tools[*].description` to a configurable budget (default 800 chars). Originals go into CCR so the LLM can pull them back. Even turns with zero tool calls now save input tokens.
- **Caveman bridge** via `helpers/caveman_bridge.py` + the `monologue_end` hook (`extensions/python/monologue_end/_30_caveman_bridge.py`), which records each Caveman-styled response into the stats store. Pairs Headroom (input compression) with the [Caveman prompt-pack](https://github.com/JuliusBrussee/caveman) (output compression) for 2-sided savings. The bridge does NOT auto-install Caveman - the user must do that themselves. Headroom only records the cooperation so the stats dashboard can attribute OUTPUT savings.
- **Split stats dashboard**: `summary()` now returns `by_side` (input vs output) and `by_kind` buckets so the dashboard can show the multiplication effect. Verified empirically.
- **Docker persistence**: `requirements.txt` (`headroom-ai>=0.27.0`) so the package survives container rebuilds.
- **README rewrite** for v0.3 (this file).

## Shipped since v0.2.0

- Library-mode and proxy-mode compression (proxy wraps upstream `headroom proxy` on port 8787)
- Safe mode (deterministic transforms, no network calls) is the recommended first-run
- CCR reversible cache (sqlite, TTL = 7 days)
- `compress_text` and `headroom_retrieve` tools the LLM can call on demand
- `hist_add_tool_result` / `hist_add_before` / `message_loop_prompts_before` extension hooks
- `.toggle-0` / `.toggle-1` global + per-project + per-agent toggles
- WebUI config modal + stats dashboard
- v2.2 banner discovery + setup hint

## Headroom <-> Caveman: complementary, not redundant

| | **Headroom** | **Caveman** |
|---|---|---|
| Side | INPUT to the LLM | OUTPUT from the LLM |
| Mechanism | `compress()` rewrites bytes before the model reads them | Prompt injection makes the model write shorter responses |
| Targets | Tool outputs, logs, RAG chunks, file reads, history, tool descriptions | The assistant's own prose |
| Reversible? | Yes, via CCR (`headroom_retrieve`) | No, only re-prompt |
| State | Per-chat OFF, per-project, per-agent | Per-chat level (lite/full/ultra/...) |

**They multiply.** Headroom shrinks a 10k token log dump 10x AND Caveman shrinks the model's explanation of it 3x. Total cost is `1/30`, not `1/13`. With both on, you keep the model's input budget cheap AND its output budget cheap.

To enable the bridge:
1. Install Caveman from the Plugin Hub.
2. Toggle Caveman ON at your desired style level.
3. Headroom's stats dashboard will automatically show the OUTPUT-side savings coming from Caveman in the `caveman_bridge` event row, attributed by style level.

Headroom never auto-installs Caveman, never depends on Caveman's internals, and never breaks if Caveman is removed. The bridge is purely observational on the stats side.

## How the toggle behaves

The plugin is a true no-op when EITHER:
- the framework's `.toggle-0` file exists (global / project / agent profile scope), OR
- the active config's `enabled: false`.

In that case:
- `compress_text()` returns the input byte-for-byte unchanged.
- `headroom_retrieve()` returns `None` for any key.
- The hooks (`hist_add_tool_result`, `hist_add_before`, `message_loop_prompts_before`) are early-return no-ops.

## Docker persistence

v0.3.0 ships a `requirements.txt`:

```text
headroom-ai>=0.27.0
```

Add this line to your image build step (e.g. `RUN pip install -r requirements_headroom.txt` next to your existing `requirements.txt` install) and the package survives container rebuilds. Without this, the plugin's `hooks.py:install()` will lazy-install on first activation - which works but adds ~30s to the first chat.

## Verifying it works

After enabling Headroom and toggling it ON:

1. Open the **Headroom** settings modal.
2. Click **Test compress (sample text)** - green toast says the package is importable.
3. Click **Open stats dashboard** - shows 0 events so far.
4. In a chat, ask the LLM to run a tool that produces a long output.
5. The dashboard should show non-zero events with INPUT-side savings.
6. If Caveman is also installed and toggled on, the dashboard will additionally show OUTPUT-side savings attributed to `caveman_bridge`.

## Roadmap

| Phase | Status |
|---|---|
| 0-3 (plugin skeleton, library, CCR, hooks) | shipped |
| 4 (proxy mode) | shipped |
| 5 (MCP server) | partial - headroom-ai ships MCP, not yet wired into Agent Zero MCP UI |
| 6 (stats dashboard) | shipped (basic; charts can be added later) |
| v0.3.0 (tool desc shrinking + Caveman bridge + split stats + Docker persistence) | shipped |
| per-chat toggle UI | next |
| `headroom learn` (failure mining -> instruction files) | opt-in, future |
| Plugin Index submission | blocked until CI folder-name match confirmed |

## License

Apache License 2.0 - same as upstream headroom.
