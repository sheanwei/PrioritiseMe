// S8 "My tasks" (region list-footer, starts open): every open, already-triaged
// task grouped as Doing now / Timeboxed / Scheduled / Delegated, sorted by
// priority then date, with "Done" on doing-now and delegated rows.

const STYLE_ID = "s8-tasks-style";
const CSS = `
.s8-group + .s8-group { margin-top: 10px; }
.s8-group-label { font-size: 12px; color: var(--muted); margin: 0 0 2px; }
.s8-list { list-style: none; margin: 0; padding: 0; }
.s8-row { display: flex; align-items: center; gap: 8px; padding: 4px 0; min-width: 0; }
.s8-main { flex: 1; min-width: 0; display: grid; }
.s8-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 14px; }
.s8-meta { font-size: 12px; color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.s8-p { font-weight: 600; }
.s8-p.urgent { color: var(--warn); }
.s8-soon { color: var(--warn); }
.s8-row .btn-small { padding: 3px 8px; font-size: 12px; flex-shrink: 0; }
`;

function ensureStyle() {
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement("style");
  style.id = STYLE_ID;
  style.textContent = CSS;
  document.head.append(style);
}

function shortDate(ymd) {
  const d = new Date(`${ymd}T00:00:00`);
  if (Number.isNaN(d.getTime())) return ymd;
  return d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
}

function minutesLeft(iso, skewMs) {
  return Math.round((new Date(iso).getTime() - (Date.now() + skewMs)) / 60000);
}

export function mount(el, api, ctx) {
  const { h } = api;
  ensureStyle();
  const body = h("div", { class: "s8-tasks" });
  const empty = h("p", { class: "muted", hidden: true, style: "margin:0" }, "No triaged tasks yet.");
  el.append(body, empty);
  let skewMs = 0;

  function meta(t) {
    const parts = [];
    if (t.priority) parts.push(h("span", { class: `s8-p${t.priority <= 2 ? " urgent" : ""}` }, `P${t.priority}`));
    if (t.type) parts.push(t.type);
    if (t.status === "timeboxed" && t.timebox_end) {
      const m = minutesLeft(t.timebox_end, skewMs);
      parts.push(m > 0 ? h("span", { class: m <= 5 ? "s8-soon" : "" }, `${m} min left`) : h("span", { class: "s8-soon" }, "time's up"));
    }
    if (t.status === "scheduled" && t.scheduled_for) parts.push(shortDate(t.scheduled_for));
    if (t.status === "delegated") {
      parts.push(`to ${t.delegated_to || "someone"}${t.decided_at ? ` · ${api.timeAgo(t.decided_at)}` : ""}`);
    }
    if (t.status === "active" && t.decided_at) parts.push(`since ${api.timeAgo(t.decided_at)}`);
    const out = [];
    parts.forEach((p, i) => { if (i) out.push(" · "); out.push(p); });
    return out;
  }

  async function markDone(t, btn) {
    btn.disabled = true;
    try {
      await api.post(`/s8_tasks/items/${t.id}/done`);
      api.toast(`Done: ${t.title}`);
      api.emit("items:changed");
    } catch (err) {
      api.toast(`Could not mark done: ${err.message}`, "error");
      btn.disabled = false;
    }
  }

  function row(t) {
    const done = t.can_mark_done
      ? h("button", { type: "button", class: "btn btn-ghost btn-small", title: "Mark this task done" }, "Done")
      : null;
    done?.addEventListener("click", () => markDone(t, done));
    return h("li", { class: "s8-row", "data-item-id": t.id },
      h("div", { class: "s8-main" },
        h("span", { class: "s8-title", title: t.title }, t.title),
        h("span", { class: "s8-meta" }, ...meta(t))),
      done);
  }

  async function refresh() {
    try {
      const data = await api.get("/s8_tasks/open");
      skewMs = new Date(data.server_now).getTime() - Date.now();
      ctx?.setTitle?.(`My tasks (${data.total})`);
      empty.hidden = data.total > 0;
      body.replaceChildren(...data.groups.filter((g) => g.count > 0).map((g) =>
        h("section", { class: "s8-group" },
          h("p", { class: "s8-group-label" }, `${g.label} (${g.count})`),
          h("ul", { class: "s8-list" }, ...g.items.map(row)))));
    } catch (err) {
      body.replaceChildren(h("p", { class: "error", style: "margin:0" }, `Could not load tasks: ${err.message}`));
    }
  }

  api.on("items:changed", refresh);
  setInterval(refresh, 30000);
  refresh();
}
