// S2 status-strip item (region "status"): live countdown of the nearest running
// timebox (FR-20), "N to re-assess" (FR-23), a click-open popover with each
// running timebox and its +15 min button (FR-24), and notify-once per expiry
// (FR-22). Expiries that happened while the dashboard was closed show as a toast.
// All DOM is built with api.h / textContent; item titles are untrusted.

import { clockSkewMs, remainingMs, formatRemaining } from "./countdown.js";

const BASE = "/s2_timebox";
const TICK_MS = 250;            // countdown repaint; keeps display within 1 s of server time
const POLL_MS = 15000;
const EXPIRY_POLL_MS = 5000;
const SOON_MS = 5 * 60 * 1000;  // warn colour under 5 minutes

export function addStylesheet() {
  const href = "/slices/s2_timebox/panel.css";
  if (document.querySelector(`link[href="${href}"]`)) return;
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = href;
  document.head.append(link);
}

export function mount(el, api, ctx) {
  const { h } = api;
  addStylesheet();

  let skew = 0;                 // add to Date.now() to get server time
  let timeboxes = [];           // [{id, title, timebox_end_ms}]
  let reassess = [];            // [{item_id, title, ...}]
  let sig = "";
  let expiryCheckQueued = false;
  const countdowns = new Map(); // item id -> {span, endMs} (popover rows)

  const setHidden = (hidden) => {
    if (ctx?.setHidden) ctx.setHidden(hidden);
    else idle.hidden = !hidden;   // old shell: no hiding, show a quiet placeholder instead
    trigger.hidden = hidden;
  };

  const label = h("span", {});
  const trigger = h("button", {
    type: "button", class: "s2-status", "aria-haspopup": "true", "aria-expanded": "false",
    title: "Running timeboxes", onclick: (e) => { e.stopPropagation(); togglePop(); },
  }, label);
  const idle = h("span", { class: "muted", hidden: true }, "No timeboxes running.");
  el.append(trigger, idle);

  // ---- popover -----------------------------------------------------------
  const pop = h("div", { class: "s2-pop", hidden: true, role: "dialog", "aria-label": "Timeboxes" });
  document.body.append(pop);

  function openPop() {
    renderPop();
    pop.hidden = false;
    trigger.setAttribute("aria-expanded", "true");
    placePop();
  }
  function closePop() {
    if (pop.hidden) return;
    pop.hidden = true;
    trigger.setAttribute("aria-expanded", "false");
  }
  function togglePop() { pop.hidden ? openPop() : closePop(); }
  function placePop() {
    const r = trigger.getBoundingClientRect();
    const w = pop.offsetWidth;
    const left = Math.max(16, Math.min(r.left, window.innerWidth - w - 16));
    pop.style.left = `${left}px`;
    pop.style.top = `${Math.round(r.bottom + 6)}px`;
  }
  document.addEventListener("click", (e) => { if (!pop.contains(e.target)) closePop(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closePop(); });
  window.addEventListener("resize", closePop);
  window.addEventListener("scroll", closePop, { passive: true });

  function openItem(id) {
    closePop();
    if (typeof api.showTab === "function") api.showTab("triage");
    api.emit("item:open", { id });
  }

  function renderPop() {
    countdowns.clear();
    const parts = [];
    if (timeboxes.length) {
      parts.push(h("section", {}, h("h3", {}, "Running"), h("ul", {}, ...timeboxes.map((tb) => {
        const span = h("span", { class: "s2-countdown", "aria-label": "Time left" });
        countdowns.set(tb.id, { span, endMs: tb.timebox_end_ms });
        const extendBtn = h("button", {
          type: "button", class: "btn btn-ghost btn-small", title: "Add 15 minutes",
          onclick: async (e) => {
            e.stopPropagation();
            extendBtn.disabled = true;
            try {
              await api.post(`${BASE}/timeboxes/${tb.id}/extend`);
              api.toast(`Extended: ${tb.title}`);
              api.emit("items:changed");
            } catch (err) {
              api.toast(`Could not extend: ${err.message}`, "error");
              refresh();
            } finally {
              extendBtn.disabled = false;
            }
          },
        }, "+15 min");
        return h("li", { class: "s2-pop-row", "data-item-id": tb.id },
          h("button", { type: "button", class: "s2-pop-link", title: tb.title, onclick: () => openItem(tb.id) }, tb.title),
          span, extendBtn);
      }))));
    }
    if (reassess.length) {
      parts.push(h("section", {}, h("h3", {}, "To re-assess"), h("ul", {}, ...reassess.map((row) =>
        h("li", { class: "s2-pop-row", "data-item-id": row.item_id },
          h("button", { type: "button", class: "s2-pop-link", title: row.title, onclick: () => openItem(row.item_id) }, row.title),
          h("span", { class: "muted" }, row.expired_at ? `ended ${api.timeAgo(row.expired_at)}` : ""))))));
    }
    if (!parts.length) parts.push(h("p", { class: "muted" }, "No timeboxes running."));
    pop.replaceChildren(...parts);
    tick();
  }

  // ---- status text --------------------------------------------------------
  function tick() {
    const now = Date.now();
    let anyUp = false;
    for (const { span, endMs } of countdowns.values()) {
      if (endMs == null) { span.textContent = "—"; continue; }
      const left = remainingMs(endMs, now, skew);
      span.textContent = left <= 0 ? "up" : formatRemaining(left);
      span.classList.toggle("s2-soon", left < SOON_MS);
    }

    const timed = timeboxes.filter((t) => t.timebox_end_ms != null);
    const bits = [];
    let soon = false;
    if (timeboxes.length) {
      if (timed.length) {
        const left = Math.min(...timed.map((t) => remainingMs(t.timebox_end_ms, now, skew)));
        anyUp = left <= 0;
        soon = left < SOON_MS;
        const shown = anyUp ? "time's up" : formatRemaining(left);
        bits.push(timeboxes.length === 1 ? `timebox ${shown}` : `${timeboxes.length} timeboxes · next ${shown}`);
      } else {
        bits.push(timeboxes.length === 1 ? "1 timebox" : `${timeboxes.length} timeboxes`);
      }
    }
    if (reassess.length) bits.push(`${reassess.length} to re-assess`);
    label.textContent = bits.join(" · ");
    trigger.classList.toggle("s2-soon", soon);

    // The server job runs every 30 s; poll expiries soon after a timer hits zero.
    if (anyUp && !expiryCheckQueued) {
      expiryCheckQueued = true;
      setTimeout(() => { expiryCheckQueued = false; checkExpiries(false); }, 3000);
    }
  }

  function render() {
    const empty = !timeboxes.length && !reassess.length;
    setHidden(empty);
    if (empty) closePop();
    else if (!pop.hidden) { renderPop(); placePop(); }
    tick();
  }

  async function refresh() {
    try {
      const sent = Date.now();
      const [tb, re] = await Promise.all([api.get(`${BASE}/timeboxes`), api.get(`${BASE}/reassess`)]);
      if (typeof tb.server_now_ms === "number") skew = clockSkewMs(tb.server_now_ms, sent, Date.now());
      timeboxes = tb.items;
      reassess = re;
      const next = JSON.stringify([
        timeboxes.map((t) => [t.id, t.timebox_end_ms, t.title]),
        reassess.map((r) => [r.item_id, r.title, r.expired_at]),
      ]);
      if (next !== sig) { sig = next; render(); }
    } catch (err) {
      sig = "";
      console.warn("s2: refresh failed", err);
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
      if (onLoad) awayNotice(claimed);
      else for (const exp of claimed) api.notify(`Timebox up: ${exp.title}`, "Back in the tray: re-assess it.");
      if (pending.length) api.emit("items:changed");
    } catch (err) {
      console.warn("s2: expiry check failed", err);
    } finally {
      checking = false;
    }
  }

  // Expiries that happened while the dashboard was closed: one toast, no banner.
  function awayNotice(expired) {
    if (!expired.length) return;
    const n = expired.length;
    const titles = expired.slice(0, 3).map((e) => e.title).join(", ") + (n > 3 ? `, +${n - 3} more` : "");
    api.toast(`${n === 1 ? "A timebox" : `${n} timeboxes`} ended while you were away: ${titles}`, "notify");
  }

  api.on("items:changed", refresh);
  setInterval(tick, TICK_MS);
  setInterval(refresh, POLL_MS);
  setInterval(() => checkExpiries(false), EXPIRY_POLL_MS);
  setHidden(true);
  refresh();
  checkExpiries(true);
}
