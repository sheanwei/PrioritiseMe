// S4 "Mail" panel (FR-42, FR-43, FR-46).
// Email content is untrusted: everything is built with api.h / textContent.
// FR-47: email never triggers a browser notification, so this panel only uses api.toast.

const LABELS = { needs_action: "Needs action", fyi: "FYI", noise: "Noise", unclassified: "Unclassified" };
const ALLOWED_SIGNIN_HOSTS = /^https:\/\/([a-z0-9-]+\.)*(microsoft\.com|microsoftonline\.com|live\.com)\//i;

export function mount(el, api) {
  const { h } = api;

  const syncBtn = h("button", { type: "button", class: "btn btn-small", onclick: () => syncNow() }, "Sync now");
  const statusLine = h("span", { class: "muted" });
  const header = h("div", { class: "quickadd-row" }, syncBtn, statusLine);
  const signinBox = h("div", { hidden: true, "aria-live": "polite" });
  const countsRow = h("p", { class: "item-meta" });
  const list = h("ul", { class: "item-list", "aria-label": "Recent mail" });
  const empty = h("p", { class: "muted", hidden: true }, "No mail yet. Press Sync now.");

  el.append(header, signinBox, countsRow, list, empty);

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
    signinTimer = setTimeout(async () => {
      await refresh();
    }, 3000);
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
    statusLine.textContent = bits.join(" · ");
    statusLine.classList.toggle("error", status.connection === "error" || Boolean(status.config_error));
    if (status.connection === "error" && status.last_error) statusLine.title = status.last_error;
    if (status.config_error) statusLine.textContent += ` · ${status.config_error}`;
  }

  function renderCounts(counts) {
    countsRow.replaceChildren(
      ...["needs_action", "fyi", "noise"].map((k) =>
        h("span", { class: `badge badge-mail-${k}`, title: LABELS[k] }, `${LABELS[k]}: ${counts[k] || 0}`)),
      counts.unclassified ? h("span", { class: "badge", title: "Waiting for the LLM" }, `Unclassified: ${counts.unclassified}`) : null,
    );
  }

  function renderList(recent) {
    empty.hidden = recent.length > 0;
    list.replaceChildren(...recent.map((m) => {
      const actionable = m.classification === "needs_action";
      const button = m.classification
        ? h("button", {
            type: "button", class: "btn btn-ghost btn-small",
            onclick: () => correct(m, actionable ? "fyi" : "needs_action"),
          }, actionable ? "Not actionable" : "This needs action")
        : null;
      return h("li", { class: "item", "data-email-id": m.id },
        h("span", { class: "item-title", title: m.body_preview || "" }, m.subject || "(no subject)"),
        h("span", { class: "item-meta" },
          h("span", { class: "badge" }, LABELS[m.classification || "unclassified"]),
          m.user_corrected ? h("span", { class: "badge", title: "You corrected this" }, "corrected") : null,
          h("span", { class: "muted" }, m.from || ""),
          h("span", { class: "muted" }, api.timeAgo(m.received_at)),
          m.item_id ? h("button", {
            type: "button", class: "btn btn-ghost btn-small",
            onclick: () => api.emit("item:open", { id: m.item_id }),
          }, "Open item") : null,
          button,
        ),
      );
    }));
  }

  async function refresh() {
    try {
      const data = await api.get("/s4_outlook/state");
      renderStatus(data.status);
      renderSignin(data.status);
      renderCounts(data.counts);
      renderList(data.recent);
    } catch (err) {
      list.replaceChildren(h("li", { class: "error" }, `Could not load mail: ${err.message}`));
    }
  }

  api.on("items:changed", refresh);
  setInterval(refresh, 60000);
  refresh();
}
