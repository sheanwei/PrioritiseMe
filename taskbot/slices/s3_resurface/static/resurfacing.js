// S3 panel: "Resurfacing today" (FR-31). Lists items whose scheduled date
// arrived today (local). Clicking one opens it in the intake gate.

export function mount(el, api) {
  const { h } = api;

  const list = h("ul", { class: "item-list", "aria-label": "Items resurfaced today" });
  const empty = h("p", { class: "muted", hidden: true }, "Nothing came back today.");

  async function refresh() {
    try {
      const data = await api.get("/s3_resurface/resurfaced");
      empty.hidden = data.items.length > 0;
      list.replaceChildren(...data.items.map((item) => h("li", {},
        h("button", {
          class: "item", type: "button", "data-item-id": item.id,
          onclick: () => api.emit("item:open", { id: item.id }),
        },
          h("span", { class: "item-title" }, item.title),
          h("span", { class: "item-meta" },
            item.status === "tray" ? null : h("span", { class: "badge" }, item.status),
            item.scheduled_for ? h("span", { class: "muted" }, `for ${item.scheduled_for}`) : null,
            h("span", { class: "muted" }, api.timeAgo(item.resurfaced_at)),
          ),
        ),
      )));
    } catch (err) {
      list.replaceChildren(h("li", { class: "error" }, `Could not load: ${err.message}`));
    }
  }

  el.append(list, empty);
  api.on("items:changed", refresh);
  setInterval(refresh, 60000);
  refresh();
}
