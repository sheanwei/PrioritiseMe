// S2 Timeboxes panel: live countdowns (FR-20), notify once per expiry (FR-22),
// re-assess notes (FR-23) and +15 min extensions (FR-24).
// All DOM is built with api.h / textContent; item titles are untrusted.

import { clockSkewMs, remainingMs, formatRemaining } from "./countdown.js";

const BASE = "/s2_timebox";
const TICK_MS = 250;            // countdown repaint; keeps display within 1 s of server time
const TIMEBOX_POLL_MS = 15000;
const EXPIRY_POLL_MS = 5000;

function addStylesheet() {
  const href = "/slices/s2_timebox/panel.css";
  if (document.querySelector(`link[href="${href}"]`)) return;
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = href;
  document.head.append(link);
}

export function mount(el, api) {
  const { h } = api;
  addStylesheet();

  let skew = 0;                 // add to Date.now() to get server time
  let timeboxes = [];
  let timeboxSig = "";
  let reassessSig = "";
  const drafts = new Map();     // item id -> unsaved note text
  const countdowns = new Map(); // item id -> {span, endMs, title}
  let expiryCheckQueued = false;

  // ---- running timeboxes -------------------------------------------------
  const runList = h("ul", { class: "item-list", "aria-label": "Running timeboxes" });
  const runEmpty = h("p", { class: "muted" }, "No timeboxes running.");
  const runSection = h("div", { class: "s2-section" },
    h("h3", { class: "s2-heading" }, "Running"), runList, runEmpty);

  // ---- re-assess ----------------------------------------------------------
  const reList = h("ul", { class: "item-list", "aria-label": "Items to re-assess" });
  const reSection = h("div", { class: "s2-section", hidden: true },
    h("h3", { class: "s2-heading" }, "Re-assess"), reList);

  el.append(runSection, reSection);

  async function timed(path) {
    const sent = Date.now();
    const data = await api.get(path);
    if (typeof data.server_now_ms === "number") {
      skew = clockSkewMs(data.server_now_ms, sent, Date.now());
    }
    return data;
  }

  function renderTimeboxes() {
    countdowns.clear();
    runEmpty.hidden = timeboxes.length > 0;
    runList.replaceChildren(...timeboxes.map((tb) => {
      const span = h("span", { class: "s2-countdown", "aria-label": "Time left" });
      countdowns.set(tb.id, { span, endMs: tb.timebox_end_ms, title: tb.title });
      const extendBtn = h("button", {
        type: "button", class: "btn btn-ghost btn-small", title: "Add 15 minutes",
        onclick: async () => {
          extendBtn.disabled = true;
          try {
            await api.post(`${BASE}/timeboxes/${tb.id}/extend`);
            api.toast(`Extended: ${tb.title}`);
            api.emit("items:changed");
          } catch (err) {
            api.toast(`Could not extend: ${err.message}`, "error");
            refreshTimeboxes();
          } finally {
            extendBtn.disabled = false;
          }
        },
      }, "+15 min");
      return h("li", { class: "s2-row", "data-item-id": tb.id },
        h("span", { class: "item-title" }, tb.title), span, extendBtn);
    }));
    tick();
  }

  function tick() {
    const now = Date.now();
    let anyUp = false;
    for (const { span, endMs } of countdowns.values()) {
      if (endMs == null) { span.textContent = "—"; continue; }
      const left = remainingMs(endMs, now, skew);
      const up = left <= 0;
      anyUp ||= up;
      span.textContent = up ? "Time's up" : formatRemaining(left);
      span.classList.toggle("s2-up", up);
    }
    // The server job runs every 30 s; poll expiries soon after a timer hits zero.
    if (anyUp && !expiryCheckQueued) {
      expiryCheckQueued = true;
      setTimeout(() => { expiryCheckQueued = false; checkExpiries(false); }, 3000);
    }
  }

  async function refreshTimeboxes() {
    try {
      const data = await timed(`${BASE}/timeboxes`);
      timeboxes = data.items;
      const sig = JSON.stringify(timeboxes.map((t) => [t.id, t.timebox_end_ms, t.title]));
      if (sig !== timeboxSig) { timeboxSig = sig; renderTimeboxes(); }
    } catch (err) {
      timeboxSig = "";
      runList.replaceChildren(h("li", { class: "error" }, `Could not load timeboxes: ${err.message}`));
    }
  }

  function renderReassess(rows) {
    reSection.hidden = rows.length === 0;
    reList.replaceChildren(...rows.map((row) => {
      const note = h("textarea", {
        class: "input", rows: "2", maxlength: "4000",
        placeholder: "What did you learn?", "aria-label": `What did you learn about ${row.title}?`,
      });
      note.value = drafts.get(row.item_id) ?? row.note ?? "";
      const save = h("button", { type: "button", class: "btn btn-small" }, "Save & re-assess");
      const sync = () => { save.disabled = !note.value.trim(); };
      note.addEventListener("input", () => { drafts.set(row.item_id, note.value); sync(); });
      save.addEventListener("click", async () => {
        if (!note.value.trim()) return;
        save.disabled = true;
        try {
          await api.put(`${BASE}/note/${row.item_id}`, { note: note.value });
          drafts.delete(row.item_id);
          reassessSig = "";
          api.emit("items:changed");
          api.emit("item:open", { id: row.item_id });   // S1 opens the intake gate
        } catch (err) {
          api.toast(`Could not save note: ${err.message}`, "error");
          sync();
        }
      });
      sync();
      return h("li", { class: "s2-reassess", "data-item-id": row.item_id },
        h("div", { class: "s2-reassess-head" },
          h("span", { class: "item-title" }, row.title),
          h("span", { class: "muted" }, row.expired_at ? `ended ${api.timeAgo(row.expired_at)}` : "")),
        note,
        h("div", { class: "s2-actions" },
          row.required ? null : h("span", { class: "muted" }, "Note saved"),
          save));
    }));
  }

  async function refreshReassess() {
    try {
      const rows = await api.get(`${BASE}/reassess`);
      const sig = JSON.stringify(rows.map((r) => [r.item_id, r.note, r.title]));
      if (sig !== reassessSig) { reassessSig = sig; renderReassess(rows); }
    } catch (err) {
      reassessSig = "";
      reSection.hidden = false;
      reList.replaceChildren(h("li", { class: "error" }, `Could not load re-assess list: ${err.message}`));
    }
  }

  // ---- notify once per expiry (FR-22) ------------------------------------
  // Each pending expiry is claimed with an ack; only the claiming tab notifies,
  // so a notification fires exactly once even with several tabs open.
  async function claimPending() {
    const data = await api.get(`${BASE}/expiries/pending`);
    const claimed = [];
    for (const exp of data.items) {
      try {
        const res = await api.post(`${BASE}/expiries/${exp.id}/ack`);
        if (res.claimed) claimed.push(exp);
      } catch { /* retried on the next poll */ }
    }
    return { pending: data.items, claimed };
  }

  let checking = false;
  async function checkExpiries(onLoad) {
    if (checking) return;
    checking = true;
    try {
      const { pending, claimed } = await claimPending();
      if (onLoad) showBanner(claimed);
      else for (const exp of claimed) api.notify(`Timebox up: ${exp.title}`, "Back in the tray: re-assess it.");
      if (pending.length) api.emit("items:changed");
    } catch (err) {
      console.warn("s2: expiry check failed", err);
    } finally {
      checking = false;
    }
  }

  // Expiries that happened while the dashboard was closed show as a banner.
  function showBanner(expired) {
    if (!expired.length) return;
    const n = expired.length;
    const banner = h("div", { class: "s2-banner", role: "alert" },
      h("div", {},
        h("strong", {}, n === 1 ? "A timebox ended while you were away" : `${n} timeboxes ended while you were away`),
        h("ul", {}, ...expired.map((exp) => h("li", {}, exp.title)))),
      h("button", {
        type: "button", class: "btn btn-ghost btn-small",
        onclick: () => banner.remove(),
      }, "Dismiss"));
    const main = document.getElementById("panels");
    if (main && main.parentNode) {
      banner.style.margin = "12px var(--gap, 16px) 0";
      main.parentNode.insertBefore(banner, main);
    } else {
      el.prepend(banner);
    }
  }

  function refreshAll() {
    refreshTimeboxes();
    refreshReassess();
  }

  api.on("items:changed", refreshAll);
  setInterval(tick, TICK_MS);
  setInterval(refreshAll, TIMEBOX_POLL_MS);
  setInterval(() => checkExpiries(false), EXPIRY_POLL_MS);
  refreshAll();
  checkExpiries(true);
}
