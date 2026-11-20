// Dashboard shell. Loads panels from /api/core/panels and mounts each one.
//
// Panel contract: a panel module exports `mount(el, api)`.
// `api` (below) is the only thing panels should use to talk to the server
// and to each other. Shared events:
//   "items:changed"  {}       — emit after you create/update/transition items
//   "item:open"      {id}     — the user wants to open an item (S1 listens)

const bus = new EventTarget();

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
};

async function loadHealth() {
  const el = document.getElementById("health");
  try {
    const hl = await api.get("/core/health");
    const llm = hl.llm.mode === "fake" ? "LLM: fake" : `LLM: ${hl.llm.model || "no model set"}`;
    el.textContent = `${llm} · Mail: ${hl.mail.mode}`;
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

async function mountPanels() {
  const container = document.getElementById("panels");
  let panels = [];
  try {
    panels = await api.get("/core/panels");
  } catch (err) {
    container.append(h("p", { class: "error" }, `Could not load panels: ${err.message}`));
    return;
  }
  for (const p of panels) {
    const body = h("div", { class: "panel-body" });
    container.append(
      h("section", { class: "panel", id: `panel-${p.id}`, "data-slice": p.slice },
        h("h2", { class: "panel-title" }, p.title), body)
    );
    try {
      const mod = await import(p.module);
      await mod.mount(body, api);
    } catch (err) {
      console.error(`panel ${p.id} failed`, err);
      body.replaceChildren(h("p", { class: "error" }, `Panel failed to load: ${err.message}`));
    }
  }
}

loadHealth();
setupNotifications();
mountPanels();
