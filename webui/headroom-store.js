// headroom-store.js - Alpine store for the Headroom plugin settings UI.
//
// Implements the Store Gate pattern required by Agent Zero:
//   - registered as `$store.headroomStore`
//   - exposes `onOpen()` / `cleanup()` for the x-init / x-destroy hooks
//   - uses A0 toast notifications (toastFrontendError / Success) only,
//     never inline error boxes
//
// IMPORTANT FIX (v0.1.1): use callJsonApi from /js/api.js instead of raw
// fetch(). The framework helper handles CSRF, auth, and normalises the URL
// (it expects the path WITHOUT the `/api/` prefix - the helper adds it).
// Raw fetch() was hitting a 404 on the install button because:
//   1. the endpoint URL was wrong (used /api/... instead of /plugins/...)
//   2. no CSRF token was attached so the request was rejected with 403
//
// The settings modal (config.html) is wrapped by Agent Zero's
// `pluginSettingsPrototype` which provides `config.*` and `context.*`.
// We read those directly from the Alpine scope (the framework store wires
// them onto the modal's root x-data).

import { createStore } from "/js/AlpineStore.js";
import { callJsonApi } from "/js/api.js";
import {
  toastFrontendError,
  toastFrontendSuccess,
  toastFrontendInfo,
  toastFrontendWarning,
} from "/components/notifications/notification-store.js";

const DEBUG = false; // set true to log to console

function _log(...args) {
  if (DEBUG) console.info("[headroom-store]", ...args);
}
function _warn(...args) {
  console.warn("[headroom-store]", ...args);
}

export const store = createStore("headroomStore", {
  busy: false,
  headroomAvailable: null, // null=unknown, true/false after detection
  toggleState: null, // null=unknown, "on" | "off"
  scope: null, // null=global, "project" | "agent"
  lastError: "", // last error message, shown in a dismissible inline banner

  // ----- Proxy mode state (Phase 4) -----
  proxyBusy: false,
  proxyStatus: null, // { running, host, port, url, mode, pid, ... } or null until first fetch


  // -----------------------------------------------------------------
  // Computed
  // -----------------------------------------------------------------
  get toggleLabel() {
    if (this.toggleState === "on") return "plugin: on";
    if (this.toggleState === "off") return "plugin: off";
    return "plugin: ?";
  },
  get toggleClass() {
    return this.toggleState === "on" ? "on" : "off";
  },

  // Maps a boolean checkbox to the numeric `auto_compress_tool_outputs_min_tokens`
  // config field. Toggling the checkbox on sets the threshold to the default
  // (200 tokens); toggling it off sets it to 0 which is the framework's
  // "auto-compress disabled" sentinel.
  get autoCompressToolOutputs() {
    const v = this.config && this.config.auto_compress_tool_outputs_min_tokens;
    return typeof v === "number" ? v > 0 : true;
  },
  set autoCompressToolOutputs(val) {
    if (!this.config) return;
    this.config.auto_compress_tool_outputs_min_tokens = val ? 200 : 0;
  },

  // -----------------------------------------------------------------
  // Lifecycle
  // -----------------------------------------------------------------
  onOpen() {
    _log("onOpen");
    this._refreshAll();
  },

  cleanup() {
    _log("cleanup");
    this.busy = false;
    this.lastError = "";
  },

  async _refreshAll() {
    // Best-effort: refresh config (read-only) and probe stats endpoint to
    // detect whether the api routes are wired up. Never throws.
    await Promise.all([this._refreshConfig(), this._refreshStatus()]);
  },

  async _refreshConfig() {
    try {
      // callJsonApi takes the path WITHOUT /api/ - it normalises it.
      const data = await callJsonApi(
        "/plugins/headroom_compress/headroom_config",
        {}
      );
      if (data && data.ok && data.config) {
        this.scope = data.config?.__scope__ || "global";
      }
    } catch (err) {
      _warn("refresh config failed (this is OK if the api isn't wired yet):", err);
    }
  },

  async _refreshStatus() {
    try {
      const data = await callJsonApi(
        "/plugins/headroom_compress/headroom_stats",
        { window_hours: 1, limit: 1 }
      );
      if (data && data.ok) {
        this.headroomAvailable = true;
        this.toggleState = data.config?.enabled ? "on" : "off";
      }
    } catch (err) {
      this.headroomAvailable = false;
      this.toggleState = null;
      _warn("refresh status failed (api not yet wired?):", err?.message || err);
    }
  },

  // -----------------------------------------------------------------
  // Actions
  // -----------------------------------------------------------------
  async installPackage() {
    if (this.busy) return;
    this.busy = true;
    this.lastError = "";
    _log("installPackage: starting pip install");
    try {
      const data = await callJsonApi(
        "/plugins/headroom_compress/headroom_execute",
        { args: ["--upgrade", "--extras", "all"] }
      );
      _log("installPackage: response", data);
      if (data && data.ok) {
        toastFrontendSuccess(
          "headroom-ai installed / upgraded. The plugin is now active.",
          "Headroom"
        );
        this.headroomAvailable = true;
      } else {
        const tail = (data?.stderr || data?.stdout || data?.error || "unknown error")
          .toString()
          .slice(-600);
        this.lastError = `Install failed: ${tail}`;
        toastFrontendError(this.lastError, "Headroom");
      }
    } catch (err) {
      _warn("installPackage: request failed", err);
      this.lastError = `Install request failed: ${err?.message || err}`;
      toastFrontendError(this.lastError, "Headroom");
    } finally {
      this.busy = false;
      this._refreshStatus();
    }
  },

  openDashboard() {
    if (typeof globalThis.openModal === "function") {
      globalThis.openModal("/plugins/headroom_compress/webui/dashboard.html");
    } else {
      toastFrontendError("openModal is not available in this context", "Headroom");
    }
  },

  async testCompress() {
    if (this.busy) return;
    this.busy = true;
    this.lastError = "";
    try {
      const data = await callJsonApi(
        "/plugins/headroom_compress/headroom_execute",
        { args: ["--no-install"] } // probe-only: just verifies the import
      );
      if (data && data.ok) {
        this.headroomAvailable = true;
        toastFrontendSuccess(
          "headroom-ai is importable. Try the compress_text tool from a chat.",
          "Headroom"
        );
      } else {
        this.headroomAvailable = false;
        const tail = (data?.stderr || data?.stdout || data?.error || "")
          .toString()
          .slice(-400);
        this.lastError = `headroom-ai import check failed: ${tail || "unknown"}`;
        toastFrontendError(this.lastError, "Headroom");
      }
    } catch (err) {
      this.lastError = `Test request failed: ${err?.message || err}`;
      toastFrontendError(this.lastError, "Headroom");
    } finally {
      this.busy = false;
    }
  },

  dismissError() {
    this.lastError = "";
  },

  // -----------------------------------------------------------------
  // Proxy mode (Phase 4)
  // -----------------------------------------------------------------
  async refreshProxyStatus() {
    if (this.proxyBusy) return;
    this.proxyBusy = true;
    try {
      const data = await callJsonApi(
        "/plugins/headroom_compress/headroom_proxy",
        { action: "status" }
      );
      if (data && data.ok) {
        this.proxyStatus = data;
      } else {
        this.proxyStatus = { running: false, error: data?.error || "unknown" };
      }
    } catch (err) {
      this.proxyStatus = { running: false, error: err?.message || String(err) };
    } finally {
      this.proxyBusy = false;
    }
  },

  async startProxy() {
    if (this.proxyBusy) return;
    this.proxyBusy = true;
    try {
      const data = await callJsonApi(
        "/plugins/headroom_compress/headroom_proxy",
        { action: "start" }
      );
      if (data?.ok) {
        this.proxyStatus = data;
        toastFrontendSuccess(
          `Headroom proxy started at ${data.url || "http://127.0.0.1:8787"} (pid ${data.pid || "?"})`,
          "Headroom"
        );
      } else {
        toastFrontendError(
          `Proxy start failed: ${data?.error || "unknown"}`,
          "Headroom"
        );
      }
    } catch (err) {
      toastFrontendError(`Proxy start error: ${err?.message || err}`, "Headroom");
    } finally {
      this.proxyBusy = false;
    }
  },

  async stopProxy() {
    if (this.proxyBusy) return;
    this.proxyBusy = true;
    try {
      const data = await callJsonApi(
        "/plugins/headroom_compress/headroom_proxy",
        { action: "stop" }
      );
      if (data?.ok) {
        this.proxyStatus = data;
        toastFrontendInfo(
          data?.force_killed
            ? "Headroom proxy was force-killed after refusing to stop."
            : "Headroom proxy stopped.",
          "Headroom"
        );
      } else {
        toastFrontendError(
          `Proxy stop failed: ${data?.error || "unknown"}`,
          "Headroom"
        );
      }
    } catch (err) {
      toastFrontendError(`Proxy stop error: ${err?.message || err}`, "Headroom");
    } finally {
      this.proxyBusy = false;
    }
  },

  async restartProxy() {
    if (this.proxyBusy) return;
    this.proxyBusy = true;
    try {
      const data = await callJsonApi(
        "/plugins/headroom_compress/headroom_proxy",
        { action: "restart" }
      );
      if (data?.ok) {
        this.proxyStatus = data;
        toastFrontendSuccess("Headroom proxy restarted.", "Headroom");
      } else {
        toastFrontendError(
          `Proxy restart failed: ${data?.error || "unknown"}`,
          "Headroom"
        );
      }
    } catch (err) {
      toastFrontendError(`Proxy restart error: ${err?.message || err}`, "Headroom");
    } finally {
      this.proxyBusy = false;
    }
  },

  },
});
