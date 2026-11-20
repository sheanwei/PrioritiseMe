// S2 re-assess banner (region "detail", above the decision card). When the
// opened item came back from an expired timebox and still needs a note (FR-23),
// it asks "What did you learn?" with a one-line input. Hidden otherwise.
// All DOM is built with api.h / textContent; item titles are untrusted.

import { addStylesheet } from "./status.js";

const BASE = "/s2_timebox";

export function mount(el, api, ctx) {
  const { h } = api;
  addStylesheet();

  let currentId = null;
  let seq = 0;                   // drops stale responses when items are opened quickly
  let savedFor = null;           // item id whose note was just saved here
  const drafts = new Map();      // item id -> unsaved note text

  const setHidden = (hidden) => {
    if (ctx?.setHidden) ctx.setHidden(hidden);
    el.hidden = hidden;
  };

  const input = h("input", {
    type: "text", class: "input", maxlength: "4000", placeholder: "What did you learn?",
    "aria-label": "What did you learn?",
  });
  const save = h("button", { type: "submit", class: "btn btn-small" }, "Save");
  const form = h("form", { class: "s2-banner-form" }, input, save);
  const head = h("div", { class: "s2-banner-head" }, "Timebox ended. What did you learn?");
  const saved = h("div", { class: "s2-saved muted", hidden: true });
  const banner = h("div", { class: "s2-banner", role: "region", "aria-label": "Re-assess" }, head, form, saved);
  el.append(banner);

  const sync = () => { save.disabled = !input.value.trim(); };
  input.addEventListener("input", () => { if (currentId != null) drafts.set(currentId, input.value); sync(); });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const id = currentId;
    const text = input.value.trim();
    if (id == null || !text) return;
    save.disabled = true;
    try {
      await api.put(`${BASE}/note/${id}`, { note: text });
      drafts.delete(id);
      savedFor = id;
      if (id === currentId) showSaved(text);
      api.emit("items:changed");
    } catch (err) {
      api.toast(`Could not save note: ${err.message}`, "error");
      sync();
    }
  });

  function showForm() {
    head.hidden = false;
    form.hidden = false;
    saved.hidden = true;
    const draft = drafts.get(currentId) ?? "";
    if (input.value !== draft) input.value = draft;
    sync();
    setHidden(false);
  }

  function showSaved(note) {
    head.hidden = true;
    form.hidden = true;
    saved.hidden = false;
    saved.textContent = note ? `Note saved: ${note}` : "Note saved";
    setHidden(false);
  }

  async function check() {
    const mine = ++seq;
    const id = currentId;
    if (id == null) { setHidden(true); return; }
    try {
      const n = await api.get(`${BASE}/note/${id}`);
      if (mine !== seq) return;
      if (n.required) showForm();
      else if (savedFor === id && n.reassess) showSaved(n.note);
      else setHidden(true);
    } catch {
      if (mine === seq) setHidden(true);   // 404 etc.: nothing to re-assess
    }
  }

  function open(id) {
    if (id !== currentId) savedFor = null;
    currentId = id ?? null;
    check();
  }

  api.on("item:open", (detail) => open(detail?.id));
  api.on("items:changed", check);

  setHidden(true);
  if (typeof api.selectedId === "function") {
    const id = api.selectedId();
    if (id != null) open(id);
  }
}
