// S4 Mail tab (region "mail"; FR-42, FR-43, FR-46).
// Email content is untrusted: everything is built with api.h / textContent.
// FR-47: email never triggers a browser notification, so this panel only uses api.toast.

const LABELS = { needs_action: "Needs action", fyi: "FYI", noise: "Noise", unclassified: "Unclassified" };
const COUNT_WORDS = { needs_action: "need action", fyi: "FYI", noise: "noise", unclassified: "unclassified" };
const CLASSES = ["needs_action", "fyi", "noise"];
const ALLOWED_SIGNIN_HOSTS = /^https:\/\/([a-z0-9-]+\.)*(microsoft\.com|microsoftonline\.com|live\.com)\//i;

const STYLE_ID = "s4-mail-style";
const CSS = `
.s4-mail { display: grid; gap: 12px; min-width: 0; }
.s4-head { display: flex; align-items: center; gap: 8px 12px; flex-wrap: wrap; }
.s4-counts { font-weight: 600; margin-right: auto; }
.s4-status { font-size: 13px; overflow-wrap: anywhere; }
.s4-signin { border: 1px solid var(--border); border-left: 4px solid var(--accent); border-radius: var(--radius); padding: 8px 12px; background: var(--surface); }
.s4-signin p { margin: 4px 0; overflow-wrap: anywhere; }
.s4-list { list-style: none; margin: 0; padding: 0; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); }
.s4-row { display: grid; grid-template-columns: minmax(0, 14rem) minmax(0, 1fr) auto; grid-template-areas: "from subj meta" "acts acts acts";
  gap: 2px 12px; align-items: baseline; padding: 8px 12px; border-bottom: 1px solid var(--border); }
.s4-row:last-child { border-bottom: 0; }
.s4-from { grid-area: from; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.s4-subj { grid-area: subj; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.s4-meta { grid-area: meta; display: flex; gap: 6px; align-items: baseline; font-size: 12px; white-space: nowrap; }
.s4-acts { grid-area: acts; display: flex; flex-wrap: wrap; gap: 4px; justify-content: flex-end; }
.s4-acts .btn-small { padding: 2px 8px; font-size: 12px; }
.s4-acts .s4-acts-label { font-size: 12px; align-self: center; }
.s4-row[data-class="needs_action"] .s4-class { color: var(--pressure); font-weight: 600; }
@media (max-width: 640px) {
  .s4-row { grid-template-columns: minmax(0, 1fr) auto; grid-template-areas: "from meta" "subj subj" "acts acts"; }
  .s4-acts { justify-content: flex-start; }
}
`;

function ensureStyle() {
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement("style");
  style.id = STYLE_ID;
  style.textContent = CSS;
  document.head.append(style);
}

export function mount(el, api, ctx) {
  const { h } = api;
  ensureStyle();

  const countsLine = h("span", { class: "s4-counts" }, "…");
  const syncBtn = h("button", { type: "button", class: "btn btn-small", onclick: () => syncNow() }, "Sync now");
  const statusLine = h("div", { class: "muted s4-status" });
  const signinBox = h("div", { class: "s4-signin", hidden: true, "aria-live": "polite" });
  const list = h("ul", { class: "s4-list", "aria-label": "Recent mail" });
  const empty = h("p", { class: "muted", hidden: true }, "No mail yet. Press Sync now.");
  const root = h("div", { class: "s4-mail" },
    h("div", {}, h("div", { class: "s4-head" }, countsLine, syncBtn), statusLine),
    signinBox, list, empty);
  el.append(root);

  let signinTimer = null;

  async function syncNow() {
    syncBtn.disabled = true;
    try {
      const r = await api.post("/s4_outlook/sync");
      if (r.ok) {
        api.toast(`Mail synced: ${r.new} new, ${r.created} to tray, ${r.bumped} follow-ups`);
        if (r.created || r.bumped) api.emit("items:changed");
      } else if (r.skipped) {
        api.toast(`Sync skipped: ${r.skipped}`);
      } else {
        api.toast(`Mail sync failed: ${r.error}`, "error");
      }
    } catch (err) {
      api.toast(`Mail sync failed: ${err.message}`, "error");
    } finally {
      syncBtn.disabled = false;
      refresh();
    }
  }

  async function correct(email, to) {
    try {
      const r = await api.post(`/s4_outlook/emails/${encodeURIComponent(email.id)}/correct`, { classification: to });
      api.toast(`Marked as ${LABELS[to]}${r.action && r.action !== "none" ? ` (${r.action.replace("_", " ")})` : ""}`);
      api.emit("items:changed");
    } catch (err) {
      api.toast(`Could not correct: ${err.message}`, "error");
    }
    refresh();
  }

  function openItem(id) {
    if (typeof api.showTab === "function") api.showTab("triage");
    api.emit("item:open", { id });
  }

  async function startSignin() {
    try {
      await api.post("/s4_outlook/signin");
    } catch (err) {
      api.toast(`Could not start sign-in: ${err.message}`, "error");
    }
    pollSignin();
  }

  function pollSignin() {
    clearTimeout(signinTimer);
    signinTimer = setTimeout(() => { refresh(); }, 3000);
  }

  function renderSignin(status) {
    const s = status.signin || {};
    const needs = status.mode === "mcp" && (status.connection === "needs_signin" || ["starting", "waiting_for_user", "failed"].includes(s.state));
    signinBox.hidden = !needs && s.state !== "signed_in";
    if (signinBox.hidden) return;
    const parts = [];
    if (s.state === "waiting_for_user" && s.code) {
      const link = ALLOWED_SIGNIN_HOSTS.test(s.url || "")
        ? h("a", { href: s.url, target: "_blank", rel: "noopener noreferrer" }, s.url)
        : h("span", {}, s.url || "");
      parts.push(h("p", {}, "Sign in to Outlook: open ", link, " and enter the code ", h("strong", {}, s.code)));
      parts.push(h("p", { class: "muted" }, "Waiting for you to finish signing in…"));
      pollSignin();
    } else if (s.state === "starting") {
      parts.push(h("p", { class: "muted" }, "Starting sign-in…"));
      pollSignin();
    } else if (s.state === "signed_in") {
      parts.push(h("p", { class: "muted" }, "Signed in. Press Sync now."));
    } else {
      if (s.state === "failed") parts.push(h("p", { class: "error" }, `Sign-in failed: ${s.error || "unknown error"}`));
      parts.push(h("p", {}, "Outlook needs you to sign in. ",
        h("button", { type: "button", class: "btn btn-small", onclick: startSignin }, "Sign in")));
    }
    signinBox.replaceChildren(...parts);
  }

  function renderStatus(status) {
    const last = status.last_sync;
    const bits = [`mode: ${status.mode}`];
    if (last) bits.push(`last sync ${api.timeAgo(last.at)}${last.ok ? "" : " (failed)"}`);
    if (status.config_error) bits.push(status.config_error);
    statusLine.textContent = bits.join(" · ");
    statusLine.classList.toggle("error", status.connection === "error" || Boolean(status.config_error));
    statusLine.classList.toggle("muted", !statusLine.classList.contains("error"));
    statusLine.title = status.connection === "error" && status.last_error ? status.last_error : "";
  }

  function renderCounts(counts) {
    const keys = [...CLASSES, ...(counts.unclassified ? ["unclassified"] : [])];
    countsLine.textContent = keys.map((k) => `${counts[k] || 0} ${COUNT_WORDS[k]}`).join(" · ");
  }

  function row(m) {
    const cls = m.classification || "unclassified";
    const acts = [];
    if (m.item_id) {
      acts.push(h("button", { type: "button", class: "btn btn-ghost btn-small", onclick: () => openItem(m.item_id) }, "Open item"));
    }
    if (m.classification) {
      acts.push(h("span", { class: "muted s4-acts-label" }, "Mark:"));
      for (const to of CLASSES.filter((c) => c !== m.classification)) {
        acts.push(h("button", {
          type: "button", class: "btn btn-ghost btn-small", title: `Correct the classification to ${LABELS[to]}`,
          onclick: () => correct(m, to),
        }, LABELS[to]));
      }
    }
    return h("li", { class: "s4-row", "data-email-id": m.id, "data-class": cls },
      h("span", { class: "s4-from", title: m.from || "" }, m.from || "(unknown sender)"),
      h("span", { class: "s4-subj", title: m.body_preview || m.subject || "" }, m.subject || "(no subject)"),
      h("span", { class: "s4-meta" },
        m.user_corrected ? h("span", { class: "badge", title: "You corrected this" }, "corrected") : null,
        h("span", { class: "badge s4-class" }, LABELS[cls]),
        h("span", { class: "muted", title: m.received_at || "" }, api.timeAgo(m.received_at))),
      acts.length ? h("span", { class: "s4-acts" }, ...acts) : null,
    );
  }

  function renderList(recent) {
    empty.hidden = recent.length > 0;
    list.hidden = recent.length === 0;
    list.replaceChildren(...recent.map(row));
  }

  async function refresh() {
    try {
      const data = await api.get("/s4_outlook/state?limit=50");
      renderStatus(data.status);
      renderSignin(data.status);
      renderCounts(data.counts);
      renderList(data.recent);
    } catch (err) {
      list.hidden = false;
      list.replaceChildren(h("li", { class: "error s4-row" }, `Could not load mail: ${err.message}`));
    }
  }

  api.on("items:changed", refresh);
  api.on("tab:shown", (d) => { if (d?.tab === "mail") refresh(); });
  setInterval(refresh, 60000);
  refresh();
}
