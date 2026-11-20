// S7 linked items (region "detail", order 18: between S2's re-assess banner and
// the S1 card). Shows which open items the selected one blocks / is blocked by,
// and the priority it inherits through those links. Also decorates triage rows
// with a "⛓" badge via api.registerRowBadge (when the shell has it).
// All DOM is built with api.h / textContent; titles and reasons are untrusted.

const BASE = "/s7_links";
const SUMMARY_EVERY_MS = 30000;

function addStylesheet() {
  const href = "/slices/s7_links/panel.css";
  if (document.querySelector(`link[href="${href}"]`)) return;
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = href;
  document.head.append(link);
}

const isHigh = (p) => p !== null && p !== undefined && Number(p) <= 2;
const prio = (p) => (p === null || p === undefined ? "" : `P${p}`);

export function mount(el, api, ctx) {
  const { h } = api;
  addStylesheet();

  let currentId = null;
  let seq = 0;                 // drops stale responses when items are opened quickly
  const detected = new Set();  // item ids we already ran detect for (once per page load)
  let summary = {};            // "<item_id>" -> {blocks, blocked_by, inherited_priority}
  let summarySig = "";
  let summarySeq = 0;

  const setHidden = (hidden) => {
    if (ctx?.setHidden) ctx.setHidden(hidden);
    el.hidden = hidden;
  };

  const strip = h("ul", { class: "s7-strip", "aria-label": "Linked items" });
  el.append(strip);

  function openItem(id) {
    if (typeof api.showTab === "function") api.showTab("triage");
    api.emit("item:open", { id });
  }

  async function dismiss(link, btn) {
    btn.disabled = true;
    // The user just said "not linked": don't auto-detect on either side this page load.
    if (currentId !== null) detected.add(String(currentId));
    detected.add(String(link.id));
    try {
      await api.post(`${BASE}/links/${encodeURIComponent(link.link_id)}/dismiss`);
    } catch (err) {
      api.toast(`Could not dismiss link: ${err.message}`, "error");
      btn.disabled = false;
      return;
    }
    await load(currentId);
    await refreshSummary(false);
    if (typeof api.refreshList === "function") api.refreshList();
    api.emit("items:changed");
  }

  function line(kind, link) {
    const warn = kind === "blocks" && isHigh(link.priority);
    const verb = kind === "blocks" ? "Blocks" : "Blocked by";
    const p = prio(link.priority);
    const reason = (link.reason || "").trim();
    const x = h("button", {
      type: "button", class: "s7-x", title: "Not linked: dismiss",
      "aria-label": `Dismiss link: ${verb.toLowerCase()} #${link.id}`,
    }, "×");
    x.addEventListener("click", () => dismiss(link, x));
    return h("li", { class: `s7-line${warn ? " s7-warn" : ""}` },
      h("button", {
        type: "button", class: "s7-open", title: `Open #${link.id}${link.status ? ` (${link.status})` : ""}`,
        onclick: () => openItem(link.id),
      },
        h("span", { class: "s7-verb" }, verb), " ",
        h("span", { class: "s7-ref" }, `#${link.id}`), " · ",
        h("span", { class: "s7-title" }, link.title || ""),
        p ? " · " : null, p ? h("span", { class: "s7-prio" }, p) : null,
        link.source === "user" ? h("span", { class: "s7-src" }, " · by you") : null,
        reason ? h("span", { class: "s7-reason" }, reason) : null,
      ),
      x,
    );
  }

  function hasLinks(data) {
    return Boolean(data && ((data.blocks || []).length || (data.blocked_by || []).length));
  }

  function render(data) {
    if (!hasLinks(data)) { strip.replaceChildren(); setHidden(true); return; }
    const blocks = data.blocks || [];
    const blockedBy = data.blocked_by || [];
    const rows = [
      ...blocks.map((l) => line("blocks", l)),
      ...blockedBy.map((l) => line("blocked_by", l)),
    ];
    const ip = data.inherited_priority;
    if (ip !== null && ip !== undefined) {
      const from = data.inherited_from;
      const label = from ? `Inherits ${prio(ip)} from #${from.id}` : `Inherits ${prio(ip)}`;
      const cls = `s7-inherit${isHigh(ip) ? " s7-warn" : ""}`;
      rows.push(h("li", { class: "s7-line" }, from
        ? h("button", {
            type: "button", class: `s7-open ${cls}`, title: from.title || "",
            onclick: () => openItem(from.id),
          }, label)
        : h("span", { class: cls }, label)));
    }
    strip.classList.toggle("has-warn", blocks.some((l) => isHigh(l.priority)) || isHigh(ip));
    strip.replaceChildren(...rows);
    setHidden(false);
  }

  async function detect(id, mine) {
    detected.add(String(id));
    let res;
    try {
      res = await api.post(`${BASE}/items/${encodeURIComponent(id)}/detect`);
    } catch {
      return; // no backend / LLM error: stay hidden
    }
    if (!res || !(res.new || []).length) return;
    if (mine === seq) render(res.links);
    await refreshSummary(false);
    if (typeof api.refreshList === "function") api.refreshList();
    api.emit("items:changed"); // the S1 card re-reads links
  }

  async function load(id) {
    const mine = ++seq;
    if (id === null || id === undefined) { render(null); return; }
    let data;
    try {
      data = await api.get(`${BASE}/items/${encodeURIComponent(id)}`);
    } catch {
      if (mine === seq) render(null); // 404 (no S7 backend, unknown item): nothing to show
      return;
    }
    if (mine !== seq) return;
    render(data);
    if (!hasLinks(data) && !detected.has(String(id))) detect(id, mine);
  }

  // ---- row badges ------------------------------------------------------------

  function badge(item) {
    const s = summary[String(item.id)];
    if (!s) return null;
    if (s.blocks > 0) {
      const p = s.inherited_priority;
      const text = p === null || p === undefined ? "⛓ blocks" : `⛓ blocks · ${prio(p)}`;
      return h("span", {
        class: `badge s7-badge${isHigh(p) ? " s7-warn" : ""}`,
        title: `Blocks ${s.blocks} item${s.blocks === 1 ? "" : "s"}${p ? `; inherits ${prio(p)}` : ""}`,
      }, text);
    }
    if (s.blocked_by > 0) {
      return h("span", {
        class: "badge s7-badge",
        title: `Blocked by ${s.blocked_by} item${s.blocked_by === 1 ? "" : "s"}`,
      }, "⛓ blocked");
    }
    return null;
  }

  /** Re-fetch /summary; re-render the list if it changed (and `rerender`). */
  async function refreshSummary(rerender = true) {
    const mine = ++summarySeq;
    let next;
    try {
      next = (await api.get(`${BASE}/summary`)) || {};
    } catch {
      next = {}; // no S7 backend: no badges
    }
    if (mine !== summarySeq) return;
    const sig = JSON.stringify(next);
    if (sig === summarySig) return;
    summary = next;
    summarySig = sig;
    if (rerender && typeof api.refreshList === "function") api.refreshList();
  }

  if (typeof api.registerRowBadge === "function") {
    api.registerRowBadge(badge);
    refreshSummary();
    setInterval(() => refreshSummary(), SUMMARY_EVERY_MS);
    api.on("items:changed", () => refreshSummary());
  }

  // ---- wiring ----------------------------------------------------------------

  api.on("item:open", (detail) => {
    currentId = detail?.id ?? null;
    load(currentId);
    if (typeof api.registerRowBadge === "function") refreshSummary();
  });
  api.on("items:changed", () => { if (currentId !== null) load(currentId); });

  setHidden(true);
  if (typeof api.selectedId === "function") {
    const id = api.selectedId();
    if (id !== null && id !== undefined) { currentId = id; load(id); }
  }
}
