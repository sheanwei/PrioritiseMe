// S3 panel: "Probably not needed" (FR-33). Scheduled/delegated items with no
// follow-up for 5+ working days, each with "Close as stale" and "Keep".

export function mount(el, api) {
  const { h } = api;

  const list = h("ul", { class: "item-list", "aria-label": "Stale candidates" });
  const empty = h("p", { class: "muted", hidden: true }, "Nothing has gone quiet.");

  async function act(item, action, buttons) {
    buttons.forEach((b) => { b.disabled = true; });
    try {
      await api.post(`/s3_resurface/items/${item.id}/${action}`);
      api.toast(action === "close" ? `Closed as stale: ${item.title}` : `Kept: ${item.title}`);
      api.emit("items:changed");
    } catch (err) {
      api.toast(`Could not ${action}: ${err.message}`, "error");
      buttons.forEach((b) => { b.disabled = false; });
      refresh();
    }
  }

  function row(item) {
    const close = h("button", { type: "button", class: "btn btn-small" }, "Close as stale");
    const keep = h("button", { type: "button", class: "btn btn-ghost btn-small" }, "Keep");
    close.addEventListener("click", () => act(item, "close", [close, keep]));
    keep.addEventListener("click", () => act(item, "keep", [close, keep]));
    const who = item.status === "delegated" && item.delegated_to ? `delegated to ${item.delegated_to}` : item.status;
    return h("li", { "data-item-id": item.id },
      h("div", { class: "item", style: "cursor: default; flex-wrap: wrap; align-items: center;" },
        h("span", { class: "item-title" }, item.title),
        h("span", { class: "item-meta", style: "align-items: center;" },
          h("span", { class: "badge" }, who),
          h("span", { class: "muted", title: `Silent since ${item.silent_since}` },
            `${item.silent_days} working days silent`),
          close, keep,
        ),
      ),
    );
  }

  async function refresh() {
    try {
      const data = await api.get("/s3_resurface/candidates");
      empty.hidden = data.items.length > 0;
      list.replaceChildren(...data.items.map(row));
    } catch (err) {
      list.replaceChildren(h("li", { class: "error" }, `Could not load: ${err.message}`));
    }
  }

  el.append(list, empty);
  api.on("items:changed", refresh);
  setInterval(refresh, 60000);
  refresh();
}
