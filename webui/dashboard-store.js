// dashboard-store.js - Alpine store for the Headroom stats dashboard modal.
//
// IMPORTANT FIX (v0.1.1): use callJsonApi from /js/api.js instead of raw
// fetch(). The earlier code used /api/plugins/headroom_stats/headroom_stats
// which is wrong on TWO counts:
//   1. The plugin name is `headroom_compress`, not `headroom_stats`
//      (headroom_stats is just the API file name).
//   2. callJsonApi expects the path WITHOUT the /api/ prefix and adds it
//      itself; prepending /api/ was causing 404s for some users and
//      bypassing CSRF protection for others.

import { createStore } from "/js/AlpineStore.js";
import { callJsonApi } from "/js/api.js";
import {
  toastFrontendError,
  toastFrontendSuccess,
} from "/components/notifications/notification-store.js";

const DEBUG = false;

function _log(...args) {
  if (DEBUG) console.info("[headroom-dashboard]", ...args);
}
function _warn(...args) {
  console.warn("[headroom-dashboard]", ...args);
}

export const store = createStore("dashboardStore", {
  busy: false,
  windowHours: 24,
  config: null,
  windowSummary: null,
  lifetimeSummary: null,
  ccr: null,
  ccrRecent: [],
  events: [],
  scope: null,
  toggleState: null,
  headroomAvailable: null,

  // -----------------------------------------------------------------
  // Computed
  // -----------------------------------------------------------------
  get toggleClass() {
    return this.toggleState === "on" ? "on" : "off";
  },
  get ratioLabel() {
    const s = this.windowSummary;
    if (!s || !s.input_tokens) return "—";
    const r = s.output_tokens / Math.max(1, s.input_tokens);
    return r.toFixed(2);
  },

  // -----------------------------------------------------------------
  // Formatters
  // -----------------------------------------------------------------
  fmt(n) {
    n = Number(n) || 0;
    if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + "M";
    if (n >= 1_000) return (n / 1_000).toFixed(1) + "k";
    return String(n);
  },
  fmtTime(ts) {
    if (!ts) return "—";
    const d = new Date(ts * 1000);
    return d.toLocaleString();
  },

  // -----------------------------------------------------------------
  // Lifecycle
  // -----------------------------------------------------------------
  onOpen() {
    this.refresh();
  },

  cleanup() {
    this.busy = false;
  },

  // -----------------------------------------------------------------
  // Actions
  // -----------------------------------------------------------------
  async refresh() {
    if (this.busy) return;
    this.busy = true;
    try {
      const windowHours =
        this.windowHours === 0 ? 0 : Math.max(1, Number(this.windowHours) || 24);
      // callJsonApi normalises the URL (adds /api/) and attaches CSRF + auth.
      // Path format: /plugins/<plugin_name>/<handler_filename_without_py>
      const data = await callJsonApi(
        "/plugins/headroom_compress/headroom_stats",
        { window_hours: windowHours, limit: 50 }
      );
      if (!data || !data.ok) {
        toastFrontendError(
          `Stats request error: ${data?.error || "unknown"}`,
          "Headroom"
        );
        return;
      }
      this.config = data.config || null;
      this.toggleState = data.config?.enabled ? "on" : "off";
      this.headroomAvailable = true;
      this.windowSummary = data?.events?.summary_window || null;
      this.lifetimeSummary = data?.events?.summary_lifetime || null;
      this.events = data?.events?.recent || [];
      this.ccr = data?.ccr || null;
      this.ccrRecent = data?.ccr?.recent || [];
    } catch (err) {
      _warn("refresh failed:", err);
      toastFrontendError(`Stats request failed: ${err?.message || err}`, "Headroom");
    } finally {
      this.busy = false;
    }
  },

  async prune() {
    if (this.busy) return;
    this.busy = true;
    try {
      // We don't have a dedicated /prune endpoint; the cheapest path is to
      // re-fetch stats (the server already auto-pruned expired entries on
      // the most recent write) and report the new entry count. A dedicated
      // /prune endpoint can be added later if needed.
      const data = await callJsonApi(
        "/plugins/headroom_compress/headroom_stats",
        { window_hours: 0, limit: 1 }
      );
      const before = this.ccr?.count || 0;
      const after = data?.ccr?.count || 0;
      this.ccr = data?.ccr || this.ccr;
      toastFrontendSuccess(
        `CCR cache refreshed. Entries: ${after} (was ${before}).`,
        "Headroom"
      );
    } catch (err) {
      _warn("prune failed:", err);
      toastFrontendError(`Prune failed: ${err?.message || err}`, "Headroom");
    } finally {
      this.busy = false;
      await this.refresh();
    }
  },
});
