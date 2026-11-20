// Dashboard shell: top bar with tabs + status strip, the Triage tab (quick-add,
// triage list, detail column), and the Mail / Review tabs. Panels come from
// /api/core/panels and are mounted into the region their manifest names.
//
// Panel contract (PRD §4.7): a panel module exports `mount(el, api, ctx)`.
//   ctx.region            "status" | "list-footer" | "detail" | "mail" | "review" | "more"
//   ctx.setTitle(text)    updates the <summary> of a list-footer panel (no-op elsewhere)
//   ctx.setHidden(bool)   hides/shows the panel's whole region slot
// `api` (below) is the only thing panels should use to talk to the server and
// to each other. Shared events:
//   "items:changed"  {}       emit after you create/update/transition items
//   "item:open"      {id}     open an item in the detail column; id null = nothing selected
//   "tab:shown"      {tab}    a tab became visible ("triage" | "mail" | "review")
// Row decoration: api.registerRowBadge(fn) adds fn(item) -> Node|null to every
// triage-list row's meta area; api.refreshList() re-fetches and re-renders the list.

const bus = new EventTarget();

const TABS = ["triage", "mail", "review"];
const REGIONS = ["status", "list-footer", "detail", "mail", "review", "hidden"];

let selectedId = null;
const rowBadgeFns = [];
let lastItems = null; // last tray items rendered, for re-renders when badges change

async function request(method, path, body) {
  const res = await fetch("/api" + path, {
    method,
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    } catch { /* not json */ }
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.status === 204 ? null : res.json();
}

/** Build a DOM element safely (text is never parsed as HTML). */
function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === undefined || c === null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

function toast(message, kind = "info") {
  const el = h("div", { class: `toast toast-${kind}`, role: "status" }, message);
  document.getElementById("toasts").append(el);
  setTimeout(() => el.remove(), 6000);
}

function timeAgo(iso) {
  if (!iso) return "";
  const secs = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (secs < 60) return "just now";
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 48) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

function currentTab() {
  const name = location.hash.replace(/^#/, "");
  return TABS.includes(name) ? name : "triage";
}

export const api = {
  get: (path) => request("GET", path),
  post: (path, body = {}) => request("POST", path, body),
  put: (path, body = {}) => request("PUT", path, body),
  patch: (path, body = {}) => request("PATCH", path, body),
  del: (path) => request("DELETE", path),
  on(type, fn) {
    const handler = (e) => fn(e.detail);
    bus.addEventListener(type, handler);
    return () => bus.removeEventListener(type, handler);
  },
  emit(type, detail = {}) {
    bus.dispatchEvent(new CustomEvent(type, { detail }));
  },
  /** Browser notification if permitted, otherwise an in-page toast. */
  notify(title, body = "") {
    if ("Notification" in window && Notification.permission === "granted") {
      new Notification(title, { body });
    }
    toast(body ? `${title} — ${body}` : title, "notify");
  },
  toast,
  h,
  timeAgo,
  /** Switch to "triage" | "mail" | "review" (kept in the URL hash). */
  showTab(name) {
    if (!TABS.includes(name)) return;
    if (currentTab() === name && location.hash) { applyTab(); return; }
    location.hash = name; // hashchange -> applyTab()
  },
  /** The id last opened via "item:open", or null. */
  selectedId: () => selectedId,
  /**
   * Decorate triage rows: `fn(item)` returns a DOM Node or null. It is called
   * for every row on every render (keep it synchronous and cheap); returned
   * nodes are appended to the row's meta area, before the age. Returns an
   * unregister function.
   */
  registerRowBadge(fn) {
    if (typeof fn !== "function") return () => {};
    rowBadgeFns.push(fn);
    if (lastItems) renderList(lastItems);
    return () => {
      const i = rowBadgeFns.indexOf(fn);
      if (i >= 0) rowBadgeFns.splice(i, 1);
      if (lastItems) renderList(lastItems);
    };
  },
  /** Re-fetch and re-render the triage list; resolves when done. */
  refreshList: () => refreshList(),
};

// ---------------------------------------------------------------- tabs

let shownTab = null;

function applyTab() {
  const tab = currentTab();
  for (const name of TABS) {
    const view = document.getElementById(`tab-${name}`);
    const btn = document.getElementById(`tabbtn-${name}`);
    view.hidden = name !== tab;
    btn.setAttribute("aria-selected", String(name === tab));
    btn.tabIndex = name === tab ? 0 : -1;
  }
  if (shownTab !== tab) {
    shownTab = tab;
    api.emit("tab:shown", { tab });
  }
}

function setupTabs() {
  const buttons = [...document.querySelectorAll(".tab")];
  for (const btn of buttons) {
    btn.addEventListener("click", () => api.showTab(btn.dataset.tab));
    btn.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      const i = buttons.indexOf(btn) + (e.key === "ArrowRight" ? 1 : -1);
      const next = buttons[(i + buttons.length) % buttons.length];
      next.focus();
      api.showTab(next.dataset.tab);
    });
  }
  window.addEventListener("hashchange", applyTab);
  shownTab = currentTab(); // the first "tab:shown" is emitted once panels are mounted
  applyTab();
}

// ---------------------------------------------------------------- top bar

async function loadHealth() {
  const el = document.getElementById("health");
  try {
    const hl = await api.get("/core/health");
    const llm = hl.llm.mode === "fake" ? "LLM fake" : `LLM ${hl.llm.model || "not set"}`;
    el.textContent = `${llm} · mail ${hl.mail.mode}`;
    el.classList.toggle("warn", hl.llm.mode === "real" && !(hl.llm.configured && hl.llm.model));
  } catch (err) {
    el.textContent = "Server unreachable";
    el.classList.add("warn");
  }
}

function setupNotifications() {
  const btn = document.getElementById("notify-btn");
  if (!("Notification" in window) || Notification.permission !== "default") return;
  btn.hidden = false;
  btn.addEventListener("click", async () => {
    await Notification.requestPermission();
    btn.hidden = true;
  });
}

// ---------------------------------------------------------------- quick-add

function buildQuickAdd() {
  const title = h("input", {
    class: "input", name: "title", placeholder: "What just landed on you?",
    maxlength: "300", required: true, autocomplete: "off", "aria-label": "Task title",
  });
  const context = h("textarea", {
    class: "input", name: "raw_context", rows: "3", hidden: true,
    placeholder: "Paste the thread or email (optional)", "aria-label": "Pasted thread",
  });
  const toggle = h("button", {
    type: "button", class: "btn btn-ghost btn-small", "aria-expanded": "false",
    onclick: () => {
      context.hidden = !context.hidden;
      toggle.setAttribute("aria-expanded", String(!context.hidden));
      if (!context.hidden) context.focus();
    },
  }, "+ thread");
  const submit = h("button", { type: "submit", class: "btn btn-small" }, "Add");

  const form = h("form", { class: "quickadd", onsubmit: async (e) => {
    e.preventDefault();
    if (!title.value.trim()) return;
    submit.disabled = true;
    try {
      await api.post("/core/items", { title: title.value, raw_context: context.value || null });
      form.reset();
      context.hidden = true;
      toggle.setAttribute("aria-expanded", "false");
      api.emit("items:changed");
      title.focus();
    } catch (err) {
      api.toast(`Could not add: ${err.message}`, "error");
    } finally {
      submit.disabled = false;
    }
  } }, h("div", { class: "quickadd-row" }, title, toggle, submit), context);

  document.getElementById("quickadd").append(form);
}

// ---------------------------------------------------------------- triage list

const SVG_NS = "http://www.w3.org/2000/svg";
const ICONS = {
  reassess: ["M6 3h12", "M6 21h12", "M7 3c0 4.5 4 6 5 9c1-3 5-4.5 5-9", "M7 21c0-4.5 4-6 5-9c1 3 5 4.5 5 9"],
  resurfaced: ["M20 12a8 8 0 1 1-2.34-5.66", "M20 4v5h-5"],
  email: ["M3.5 6.5h17v11h-17z", "M3.5 7l8.5 6.5L20.5 7"],
};

function icon(kind) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("class", `row-icon row-icon-${kind}`);
  if (kind === "manual") {
    const c = document.createElementNS(SVG_NS, "circle");
    c.setAttribute("cx", "12"); c.setAttribute("cy", "12"); c.setAttribute("r", "3.5");
    svg.append(c);
  } else {
    for (const d of ICONS[kind]) {
      const p = document.createElementNS(SVG_NS, "path");
      p.setAttribute("d", d);
      svg.append(p);
    }
  }
  return svg;
}

function rowKind(item) {
  if (item.extra && Number(item.extra.reassess) === 1) return "reassess";
  if (item.outcome === "schedule") return "resurfaced";
  if (item.source === "email") return "email";
  return "manual";
}

const KIND_LABEL = { reassess: "re-assess", resurfaced: "back today", email: "email", manual: "added by you" };

let trayIds = [];
let refreshSeq = 0;

function rowBadges(item) {
  const nodes = [];
  for (const fn of rowBadgeFns) {
    try {
      const node = fn(item);
      if (node instanceof Node) nodes.push(node);
    } catch (err) {
      console.error("row badge failed", err);
    }
  }
  return nodes;
}

function renderList(items) {
  lastItems = items;
  const list = document.getElementById("triage-list");
  document.getElementById("triage-empty").hidden = items.length > 0;
  list.replaceChildren(...items.map((item) => {
    const kind = rowKind(item);
    const showLabel = kind === "reassess" || kind === "resurfaced";
    return h("li", {},
      h("button", {
        class: `item triage-row kind-${kind}`, type: "button", "data-item-id": item.id,
        title: KIND_LABEL[kind],
        onclick: () => api.emit("item:open", { id: item.id }),
      },
        icon(kind),
        h("span", { class: "row-main" },
          showLabel ? h("span", { class: "row-label" }, KIND_LABEL[kind]) : null,
          h("span", { class: "row-title" }, item.title),
          item.pressure_count > 0
            ? h("span", { class: "row-pressure", title: `${item.pressure_count} follow-up pings` }, `×${item.pressure_count}`)
            : null,
        ),
        h("span", { class: "row-meta" },
          rowBadges(item),
          h("span", { class: "row-age muted" }, timeAgo(item.created_at)),
        ),
      ));
  }));
  markSelected();
}

function markSelected() {
  for (const btn of document.querySelectorAll("#triage-list .triage-row")) {
    const on = String(selectedId) === btn.dataset.itemId;
    btn.classList.toggle("is-selected", on);
    if (on) btn.setAttribute("aria-current", "true");
    else btn.removeAttribute("aria-current");
  }
  document.getElementById("detail-empty").hidden = selectedId !== null;
}

async function refreshList() {
  const seq = ++refreshSeq;
  let items;
  try {
    items = await api.get("/core/items?status=tray");
  } catch (err) {
    if (seq !== refreshSeq) return;
    document.getElementById("triage-list").replaceChildren(
      h("li", { class: "error" }, `Could not load the triage list: ${err.message}`));
    return;
  }
  if (seq !== refreshSeq) return; // a newer refresh is in flight
  const previous = trayIds;
  trayIds = items.map((i) => i.id);
  renderList(items);

  // Auto-advance: the selected item just left the triage list (decided, timeboxed, ...).
  // Items opened from elsewhere that were never in the list are left alone.
  const has = (ids) => ids.some((id) => String(id) === String(selectedId));
  if (selectedId !== null && has(previous) && !has(trayIds)) {
    api.emit("item:open", { id: trayIds.length ? trayIds[0] : null });
  }
}

function setupListKeys() {
  const list = document.getElementById("triage-list");
  const rows = () => [...list.querySelectorAll(".triage-row")];
  list.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    const all = rows();
    const i = all.indexOf(document.activeElement);
    if (i < 0) return;
    e.preventDefault();
    all[Math.max(0, Math.min(all.length - 1, i + (e.key === "ArrowDown" ? 1 : -1)))].focus();
  });
  // Arrow keys with nothing focused jump into the list.
  document.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    if (currentTab() !== "triage") return;
    if (document.activeElement && document.activeElement !== document.body) return;
    const all = rows();
    if (!all.length) return;
    e.preventDefault();
    (all.find((b) => b.classList.contains("is-selected")) || all[0]).focus();
  });
}

// ---------------------------------------------------------------- panel mounting

/** Create the DOM slot for one panel; returns {slot, el, setTitle}. */
function makeSlot(p, region) {
  const label = p.title || p.id;
  const common = { id: `panel-${p.id}`, "data-slice": p.slice, "data-region": region };
  if (region === "status") {
    const el = h("span", { ...common, class: "status-item panel" });
    document.getElementById("status-strip").append(el);
    return { slot: el, el, setTitle() {} };
  }
  if (region === "list-footer") {
    const summary = h("summary", {}, label);
    const el = h("div", { class: "panel-body" });
    const slot = h("details", { ...common, class: "footer-slot panel" }, summary, el);
    document.getElementById("region-list-footer").append(slot);
    return { slot, el, setTitle(text) { summary.textContent = text; } };
  }
  const el = h("div", { class: "panel-body" });
  if (region === "more") {
    const slot = h("section", { ...common, class: "more-slot panel" },
      h("h3", { class: "section-label" }, label), el);
    document.getElementById("region-more-slots").append(slot);
    document.getElementById("region-more").hidden = false;
    return { slot, el, setTitle() {} };
  }
  const slot = h("section", { ...common, class: `region-slot slot-${region} panel`, "aria-label": label }, el);
  document.getElementById(`region-${region}`).append(slot);
  return { slot, el, setTitle() {} };
}

function updateTabEmptyStates() {
  for (const p of document.querySelectorAll("[data-empty-for]")) {
    p.hidden = document.getElementById(p.dataset.emptyFor).children.length > 0;
  }
}

async function mountPanels() {
  let panels = [];
  try {
    panels = await api.get("/core/panels");
  } catch (err) {
    document.getElementById("region-more-slots").append(
      h("p", { class: "error" }, `Could not load panels: ${err.message}`));
    document.getElementById("region-more").hidden = false;
    return;
  }
  // Create every slot first (keeps manifest order), then load modules in parallel.
  const jobs = [];
  for (const p of panels) {
    let region = REGIONS.includes(p.region) ? p.region : "more";
    if (region === "hidden") continue;
    const { slot, el, setTitle } = makeSlot(p, region);
    const ctx = {
      region,
      setTitle: (text) => setTitle(String(text ?? "")),
      setHidden: (hidden) => { slot.hidden = Boolean(hidden); },
    };
    jobs.push((async () => {
      try {
        const mod = await import(p.module);
        await mod.mount(el, api, ctx);
      } catch (err) {
        console.error(`panel ${p.id} failed`, err);
        slot.hidden = false;
        el.replaceChildren(h("span", { class: "error" }, `${p.title || p.id} failed to load: ${err.message}`));
      }
    })());
  }
  updateTabEmptyStates();
  await Promise.all(jobs);
}

// ---------------------------------------------------------------- boot

api.on("item:open", ({ id } = {}) => {
  selectedId = id === undefined || id === null ? null : id;
  markSelected();
});
api.on("items:changed", refreshList);

setupTabs();
loadHealth();
setupNotifications();
buildQuickAdd();
setupListKeys();
refreshList();
setInterval(refreshList, 20000);
mountPanels().then(() => api.emit("tab:shown", { tab: currentTab() }));
