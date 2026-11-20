// Core status item: "N to triage" (the number of tray items).
// Clicking it jumps to the Triage tab.

export function mount(el, api, ctx) {
  const btn = api.h("button", {
    type: "button", class: "status-link", onclick: () => api.showTab("triage"),
  });
  el.append(btn);

  async function refresh() {
    try {
      const items = await api.get("/core/items?status=tray");
      const n = items.length;
      btn.textContent = n ? `${n} to triage` : "Nothing to triage";
      btn.classList.toggle("muted", n === 0);
      ctx?.setHidden?.(false);
    } catch {
      ctx?.setHidden?.(true);
    }
  }

  api.on("items:changed", refresh);
  setInterval(refresh, 20000);
  refresh();
}
