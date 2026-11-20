// S3 status-strip item: "N probably not needed". Hidden when N is 0.
// Clicking it switches to Triage and opens/scrolls to the list footer.

import { subscribeCandidates } from "./stale-data.js";

export function mount(el, api, ctx) {
  const { h } = api;

  const btn = h("button", {
    type: "button",
    style: "font:inherit;color:inherit;background:none;border:0;padding:0;cursor:pointer;white-space:nowrap",
    title: "Scheduled or delegated items with no follow-up for 5+ working days",
    onclick: () => {
      if (typeof api.showTab === "function") api.showTab("triage");
      const footer = document.querySelector("[data-s3-stale]");
      if (!footer) return;
      const details = footer.closest("details");
      if (details) details.open = true;
      footer.scrollIntoView({ behavior: "smooth", block: "nearest" });
    },
  });
  el.append(btn);

  const setHidden = (hidden) => {
    if (ctx?.setHidden) ctx.setHidden(hidden);
    btn.hidden = hidden;
  };
  setHidden(true);

  subscribeCandidates(api, (err, items) => {
    if (err) return;   // the footer shows the error; keep the strip quiet
    btn.textContent = `${items.length} probably not needed`;
    setHidden(items.length === 0);
  });
}
