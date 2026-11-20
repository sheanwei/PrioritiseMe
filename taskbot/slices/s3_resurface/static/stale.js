// S3 "Probably not needed" (FR-33), region "list-footer": scheduled/delegated
// items with no follow-up for 5+ working days, as compact rows with Close / Keep.

import { subscribeCandidates, refreshCandidates } from "./stale-data.js";

const STYLE_ID = "s3-stale-style";
const CSS = `
.s3-stale { display: grid; gap: 4px; }
.s3-row { display: flex; align-items: center; gap: 6px; padding: 4px 0; border-bottom: 1px solid var(--border); min-width: 0; }
.s3-row:last-child { border-bottom: 0; }
.s3-row-main { flex: 1; min-width: 0; display: grid; }
.s3-row-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 14px; }
.s3-row-meta { font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.s3-row .btn-small { padding: 3px 8px; font-size: 12px; }
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
  el.setAttribute("data-s3-stale", "");

  const list = h("ul", { class: "s3-stale", "aria-label": "Probably not needed", style: "list-style:none;margin:0;padding:0" });
  const empty = h("p", { class: "muted", hidden: true }, "Nothing has gone quiet.");
  el.append(list, empty);

  async function act(item, action, buttons) {
    buttons.forEach((b) => { b.disabled = true; });
    try {
      await api.post(`/s3_resurface/items/${item.id}/${action}`);
      api.toast(action === "close" ? `Closed as stale: ${item.title}` : `Kept: ${item.title}`);
      api.emit("items:changed");
    } catch (err) {
      api.toast(`Could not ${action}: ${err.message}`, "error");
      buttons.forEach((b) => { b.disabled = false; });
      refreshCandidates(api);
    }
  }

  function row(item) {
    const close = h("button", { type: "button", class: "btn btn-ghost btn-small", title: "Close as stale" }, "Close");
    const keep = h("button", { type: "button", class: "btn btn-ghost btn-small", title: "Keep it; reset the quiet timer" }, "Keep");
    close.addEventListener("click", () => act(item, "close", [close, keep]));
    keep.addEventListener("click", () => act(item, "keep", [close, keep]));
    const who = item.status === "delegated" && item.delegated_to ? `delegated to ${item.delegated_to}` : item.status;
    return h("li", { class: "s3-row", "data-item-id": item.id },
      h("div", { class: "s3-row-main" },
        h("span", { class: "s3-row-title", title: item.title }, item.title),
        h("span", { class: "s3-row-meta muted", title: `Silent since ${item.silent_since}` },
          `${who} · ${item.silent_days} working days silent`)),
      close, keep);
  }

  subscribeCandidates(api, (err, items) => {
    if (err) {
      list.replaceChildren(h("li", { class: "error" }, `Could not load: ${err.message}`));
      return;
    }
    ctx?.setTitle?.(`Probably not needed (${items.length})`);
    ctx?.setHidden?.(items.length === 0);
    empty.hidden = items.length > 0;
    list.replaceChildren(...items.map(row));
  });
}
