// Core panel: quick-add + tray list (FR-03, FR-04).
// Clicking an item emits "item:open" {id}; S1 (intake gate) listens for it.

export function mount(el, api) {
  const { h } = api;

  const title = h("input", {
    class: "input", name: "title", placeholder: "What just landed on you?",
    maxlength: "300", required: true, autocomplete: "off", "aria-label": "Task title",
  });
  const context = h("textarea", {
    class: "input", name: "raw_context", rows: "3", hidden: true,
    placeholder: "Paste the thread or email (optional)", "aria-label": "Pasted thread",
  });
  const toggle = h("button", {
    type: "button", class: "btn btn-ghost btn-small",
    onclick: () => { context.hidden = !context.hidden; if (!context.hidden) context.focus(); },
  }, "+ thread");
  const submit = h("button", { type: "submit", class: "btn" }, "Add");

  const form = h("form", { class: "quickadd", onsubmit: async (e) => {
    e.preventDefault();
    if (!title.value.trim()) return;
    submit.disabled = true;
    try {
      await api.post("/core/items", { title: title.value, raw_context: context.value || null });
      form.reset();
      context.hidden = true;
      api.emit("items:changed");
      title.focus();
    } catch (err) {
      api.toast(`Could not add: ${err.message}`, "error");
    } finally {
      submit.disabled = false;
    }
  } }, h("div", { class: "quickadd-row" }, title, toggle, submit), context);

  const list = h("ul", { class: "item-list", "aria-label": "Tray items" });
  const empty = h("p", { class: "muted", hidden: true }, "Tray is empty. Nice.");

  async function refresh() {
    try {
      const items = await api.get("/core/items?status=tray");
      empty.hidden = items.length > 0;
      list.replaceChildren(...items.map((item) => h("li", {},
        h("button", {
          class: "item", type: "button", "data-item-id": item.id,
          onclick: () => api.emit("item:open", { id: item.id }),
        },
          h("span", { class: "item-title" }, item.title),
          h("span", { class: "item-meta" },
            h("span", { class: `badge badge-${item.source}` }, item.source),
            item.pressure_count > 0 ? h("span", { class: "badge badge-pressure", title: "Follow-up pings" }, `×${item.pressure_count}`) : null,
            h("span", { class: "muted" }, api.timeAgo(item.created_at)),
          ),
        ),
      )));
    } catch (err) {
      list.replaceChildren(h("li", { class: "error" }, `Could not load tray: ${err.message}`));
    }
  }

  el.append(form, list, empty);
  api.on("items:changed", refresh);
  setInterval(refresh, 20000);
  refresh();
}
