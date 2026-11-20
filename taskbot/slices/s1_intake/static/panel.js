// S1 intake gate panel (FR-10..FR-18): a decision-first triage card.
// Opens when the shell/tray emits "item:open" {id}; {id: null} means nothing is selected.
// All untrusted text (threads, emails, LLM output) goes through api.h / textContent only.

const OUTCOMES = [
  ["do_now", "Do now"],
  ["timebox", "Timebox & re-assess"],
  ["schedule", "Schedule"],
  ["delegate", "Delegate"],
  ["decline", "Decline"],
];
const TYPES = [["incident", "Incident"], ["interrupt", "Interrupt"], ["toil", "Toil"], ["planned", "Planned"]];
const PRIORITIES = [["1", "P1 (highest)"], ["2", "P2"], ["3", "P3"], ["4", "P4 (lowest)"]];
const COST = [["", "—"], ["none", "Nothing"], ["minor", "Something minor"], ["significant", "Something significant"]];
const DEADLINE = [["", "—"], ["real", "Real (external, dated)"], ["felt", "Felt"], ["none", "No deadline"]];
const YESNO = [["", "—"], ["true", "Yes"], ["false", "No"]];
const DEADLINE_KINDS = [["none", "None"], ["real", "Real"], ["felt", "Felt"]];
const DEFAULT_MINUTES = 30;
const EMPTY_TEXT = "Select an item to triage";

const label = (pairs, value) => (pairs.find(([v]) => v === String(value)) || [null, value])[1];

function ensureCss() {
  if (document.querySelector("link[data-s1-intake]")) return;
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = new URL("./panel.css", import.meta.url).href;
  link.dataset.s1Intake = "";
  document.head.append(link);
}

function isoLocal(d) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

const todayLocal = () => isoLocal(new Date());

/** Default schedule date: the next working day (Mon-Fri). */
function nextWorkdayLocal() {
  const d = new Date();
  do { d.setDate(d.getDate() + 1); } while (d.getDay() === 0 || d.getDay() === 6);
  return isoLocal(d);
}

/** "2026-11-24" -> "Tue 24 Nov" (local date, no timezone shift). */
function shortDate(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || "");
  if (!m) return iso || "";
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  const wd = d.toLocaleDateString("en-GB", { weekday: "short" });
  const mo = d.toLocaleDateString("en-GB", { month: "short" });
  return `${wd} ${d.getDate()} ${mo}`;
}

/** The decision as a short phrase, e.g. "Delegate to Chen", "Schedule for Tue 24 Nov". */
function phrase(outcome, { minutes, date, person } = {}) {
  switch (outcome) {
    case "do_now": return "Do now";
    case "decline": return "Decline";
    case "timebox": return `Timebox ${minutes || DEFAULT_MINUTES} min`;
    case "schedule": return date ? `Schedule for ${shortDate(date)}` : "Schedule";
    case "delegate": return person ? `Delegate to ${person}` : "Delegate";
    default: return label(OUTCOMES, outcome) || "Decide";
  }
}

/** Optional details the suggestion may carry (the current API does not send them). */
function sugDetails(sug) {
  return {
    minutes: Number(sug?.timebox_minutes) || null,
    date: typeof sug?.scheduled_for === "string" ? sug.scheduled_for : null,
    person: typeof sug?.delegated_to === "string" ? sug.delegated_to.trim() : null,
  };
}

/** Native replaceChildren() renders null/undefined as text, so drop them first. */
function put(node, ...kids) {
  node.replaceChildren(...kids.flat().filter((k) => k !== null && k !== undefined && k !== false));
}

export function mount(el, api, ctx) {
  ensureCss();
  const { h } = api;
  ctx?.setHidden?.(false);
  const gate = h("div", { class: "s1-gate", "aria-live": "polite" });
  const pending = h("div", { class: "s1-pending" });
  el.append(gate, pending);

  // Bumped on every open/close so late responses for an old item are ignored.
  let token = 0;
  // Per-item view state (which sections are expanded) kept across re-renders.
  let view = {};

  function select(name, options, value, ariaLabel) {
    const s = h("select", { class: "input", name, "aria-label": ariaLabel });
    for (const [v, text] of options) {
      const o = h("option", { value: v }, text);
      if (String(value ?? "") === v) o.selected = true;
      s.append(o);
    }
    return s;
  }

  function field(text, control, extra = {}) {
    return h("label", { class: "s1-field", ...extra }, h("span", { class: "s1-label" }, text), control);
  }

  function showEmpty() {
    token += 1;
    view = {};
    // The tabbed shell (ctx present) shows its own "Nothing selected" state;
    // only the legacy shell needs ours, so the two never stack.
    if (ctx) put(gate);
    else put(gate, h("p", { class: "muted s1-empty" }, EMPTY_TEXT));
  }

  function showError(msg) {
    put(gate, h("p", { class: "error", role: "alert" }, msg),
      h("button", { type: "button", class: "btn btn-ghost btn-small", onclick: showEmpty }, "Close"));
  }

  /** Focus the primary control unless the user is busy elsewhere on the page. */
  function focusIfIdle(node) {
    const a = document.activeElement;
    if (node && (!a || a === document.body || el.contains(a))) node.focus({ preventScroll: true });
  }

  async function openItem(id) {
    if (id === null || id === undefined) { showEmpty(); return; }
    const my = ++token;
    view = {};
    put(gate, h("p", { class: "muted" }, "Opening…"));
    if (!ctx) el.closest(".panel")?.scrollIntoView({ behavior: "smooth", block: "start" });
    let state;
    try {
      state = await api.get(`/s1_intake/items/${id}`);
    } catch (err) {
      if (my === token) showError(`Could not open item: ${err.message}`);
      return;
    }
    if (my !== token) return;
    if (state.item.status !== "tray") {
      showError(`"${state.item.title}" is no longer in the tray (${state.item.status}).`);
      return;
    }
    if (state.suggestion) {
      render(state, my, false);
      return;
    }
    render(state, my, true);
    await analyze(id, my, false);
  }

  async function analyze(id, my, force) {
    try {
      const state = await api.post(`/s1_intake/items/${id}/analyze${force ? "?force=true" : ""}`);
      if (my !== token) return;
      render(state, my, false);
    } catch (err) {
      if (my !== token) return;
      // e.g. 409: decided elsewhere while the AI was reading. Re-read the gate state.
      try {
        const state = await api.get(`/s1_intake/items/${id}`);
        if (my !== token) return;
        if (state.item.status !== "tray") {
          showError(`"${state.item.title}" is no longer in the tray (${state.item.status}).`);
          return;
        }
        render({ ...state, error: `AI analysis failed: ${err.message}` }, my, false);
      } catch (err2) {
        if (my === token) showError(`Could not open item: ${err2.message}`);
      }
    }
  }

  async function decide(item, body, btn, what) {
    btn.disabled = true;
    try {
      const res = await api.post(`/s1_intake/items/${item.id}/decide`, body);
      const verb = res.event.payload.accepted ? "Accepted" : "Decided";
      api.toast(`${verb}: ${what} — ${item.title}`);
      // Clear first: the shell may open the next item synchronously on items:changed.
      showEmpty();
      api.emit("items:changed");
    } catch (err) {
      api.toast(`Could not decide: ${err.message}`, "error");
      btn.disabled = false;
    }
  }

  function render(state, my, loading) {
    const item = state.item;
    const card = state.ask_card || null;
    const sug = loading ? null : state.suggestion || null;
    const intake = state.intake || {};
    const manual = !loading && !sug;
    if (manual && view.elseOpen === undefined) view.elseOpen = true;

    // --- 1. meta line ---
    const pressure = state.pressure_count || 0;
    const kind = card?.deadline_kind || "none";
    const deadline = kind === "none" ? "no deadline"
      : `${kind} deadline${card?.deadline ? ` ${shortDate(card.deadline)}` : ""}`;
    const impact = state.production_impact === true ? "prod impact"
      : state.production_impact === false ? "no prod impact" : null;
    const metaParts = [
      h("span", {}, card?.requester || item.source),
      card ? h("span", {}, deadline) : null,
      impact ? h("span", { class: impact === "prod impact" ? "s1-impact" : null }, impact) : null,
      h("span", { class: pressure > 0 ? "s1-pressure" : null }, `pressure ×${pressure}`),
    ].filter(Boolean);
    const meta = h("p", { class: "s1-meta muted" });
    metaParts.forEach((p, i) => { if (i) meta.append(h("span", { class: "s1-dot", "aria-hidden": "true" }, "·")); meta.append(p); });

    // --- 2. the request ---
    const request = h("h3", { class: "s1-request" }, card?.request || item.title);

    // --- AI notice ---
    const notice = state.error
      ? h("p", { class: "s1-notice", role: "alert" },
          h("span", {}, sug ? `${state.error}.` : "AI unavailable — decide manually."),
          h("button", { type: "button", class: "btn btn-ghost btn-small", title: state.error,
            onclick: () => { render({ ...state, error: null }, my, true); analyze(item.id, my, true); } }, "Retry"))
      : null;

    // --- 3. verdict ---
    const details = sugDetails(sug);
    let verdict = null;
    if (loading) {
      verdict = h("div", { class: "s1-verdict s1-verdict-loading", "aria-busy": "true" },
        h("div", { class: "s1-skel" }), h("p", { class: "s1-loading" }, "Reading the thread…"));
    } else if (sug) {
      verdict = h("div", { class: "s1-verdict" },
        h("div", { class: "s1-verdict-head" },
          h("strong", { class: "s1-outcome" }, phrase(sug.outcome, details)),
          h("span", { class: "badge" }, label(TYPES, sug.type)),
          h("span", { class: "badge" }, `P${sug.priority}`)),
        sug.rationale ? h("p", { class: "s1-rationale" }, sug.rationale) : null,
        sug.rule_adjusted
          ? h("p", { class: "s1-rule-note" },
              `${(sug.rules || []).join(" ")} AI suggested: ${label(OUTCOMES, sug.llm_outcome)}.`.trim())
          : null);
    } else if (!state.error) {
      verdict = h("p", { class: "s1-notice" }, h("span", {}, "No AI suggestion — decide manually."));
    }

    // --- the Why form's intake answers (read by every decision) ---
    const qCost = select("cost_of_delay", COST, intake.cost_of_delay, "Cost of delay");
    const qDeadline = select("deadline_real", DEADLINE, intake.deadline_real, "Deadline real or felt");
    const qBlocked = h("input", { class: "input", type: "number", min: "0", step: "1", name: "blocked_count",
      "aria-label": "People blocked", value: intake.blocked_count ?? "" });
    const qDelegable = select("delegable", YESNO, intake.delegable, "Could someone else do it");
    const readIntake = () => ({
      cost_of_delay: qCost.value || null,
      deadline_real: qDeadline.value || null,
      blocked_count: qBlocked.value === "" ? null : Math.max(0, parseInt(qBlocked.value, 10) || 0),
      delegable: qDelegable.value === "" ? null : qDelegable.value === "true",
    });

    // --- 4. actions row ---
    const needsValue = sug && ((sug.outcome === "schedule" && !details.date)
      || (sug.outcome === "delegate" && !details.person));
    const primary = sug ? h("button", { type: "button", class: "btn s1-primary", "aria-expanded": needsValue ? "false" : null },
      needsValue ? `${phrase(sug.outcome)}…` : phrase(sug.outcome, details)) : null;

    const elseBtn = h("button", { type: "button", class: "btn btn-ghost btn-small",
      "aria-expanded": String(!!view.elseOpen), disabled: loading }, "Something else");
    const draftBtn = sug?.solution_sketch
      ? h("button", { type: "button", class: "btn btn-ghost btn-small", "aria-expanded": String(!!view.draftOpen) }, "Draft reply")
      : null;
    const fastPath = h("button", {
      type: "button", class: "btn btn-ghost btn-small s1-fast",
      title: "Skip the gate: do it now as an incident and log the details afterwards",
      onclick: async () => {
        fastPath.disabled = true;
        try {
          await api.post(`/s1_intake/items/${item.id}/incident`);
          api.toast(`Incident started: ${item.title}. Log the details when done.`);
          showEmpty();
          api.emit("items:changed");
        } catch (err) {
          api.toast(`Could not start incident: ${err.message}`, "error");
          fastPath.disabled = false;
        }
      },
    }, "Incident, log after");
    const actions = h("div", { class: "s1-actions" }, primary, elseBtn, draftBtn, fastPath);

    // Inline value for the primary action (delegate name / schedule date).
    let inline = null;
    if (primary && needsValue) {
      const isDate = sug.outcome === "schedule";
      const input = isDate
        ? h("input", { class: "input", type: "date", name: "scheduled_for", min: todayLocal(),
            value: nextWorkdayLocal(), "aria-label": "Schedule for" })
        : h("input", { class: "input", type: "text", name: "delegated_to", maxlength: "200",
            placeholder: "Delegate to whom?", "aria-label": "Delegate to" });
      const confirm = h("button", { type: "submit", class: "btn btn-small" });
      const cancel = h("button", { type: "button", class: "btn btn-ghost btn-small" }, "Cancel");
      const value = () => input.value.trim();
      const sync = () => {
        confirm.textContent = phrase(sug.outcome, isDate ? { date: value() } : { person: value() });
        confirm.disabled = !value();
      };
      inline = h("form", { class: "s1-inline", hidden: true, novalidate: true, onsubmit: (e) => {
        e.preventDefault();
        sync();
        if (confirm.disabled) return;
        const body = { outcome: sug.outcome, type: sug.type, priority: sug.priority, override_reason: null, intake: readIntake() };
        if (isDate) body.scheduled_for = value(); else body.delegated_to = value();
        decide(item, body, confirm, confirm.textContent);
      } }, input, confirm, cancel);
      const close = () => { inline.hidden = true; primary.hidden = false; primary.setAttribute("aria-expanded", "false"); primary.focus(); };
      input.addEventListener("input", sync);
      input.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.stopPropagation(); close(); } });
      cancel.addEventListener("click", close);
      primary.addEventListener("click", () => {
        inline.hidden = false;
        primary.hidden = true;
        primary.setAttribute("aria-expanded", "true");
        sync();
        input.focus();
      });
    } else if (primary) {
      primary.addEventListener("click", () => {
        const body = { outcome: sug.outcome, type: sug.type, priority: sug.priority, override_reason: null, intake: readIntake() };
        if (sug.outcome === "timebox") body.timebox_minutes = details.minutes || DEFAULT_MINUTES;
        if (sug.outcome === "schedule") body.scheduled_for = details.date;
        if (sug.outcome === "delegate") body.delegated_to = details.person;
        decide(item, body, primary, phrase(sug.outcome, details));
      });
    }

    // --- "Something else": the full picker (FR-14..16) ---
    const dOutcome = select("outcome", sug ? OUTCOMES : [["", "Choose outcome…"], ...OUTCOMES], sug?.outcome ?? "", "Outcome");
    const dType = select("type", TYPES, sug?.type ?? item.type ?? "interrupt", "Work type");
    const dPriority = select("priority", PRIORITIES, String(sug?.priority ?? item.priority ?? 3), "Priority");
    const dMinutes = h("input", { class: "input", type: "number", min: "1", max: "480", step: "1",
      value: String(details.minutes || DEFAULT_MINUTES), name: "timebox_minutes" });
    const dDate = h("input", { class: "input", type: "date", name: "scheduled_for", min: todayLocal(),
      value: details.date || nextWorkdayLocal() });
    const dPerson = h("input", { class: "input", type: "text", name: "delegated_to", maxlength: "200",
      placeholder: "Who?", value: details.person || "" });
    const dReason = h("input", { class: "input", type: "text", name: "override_reason", maxlength: "300",
      placeholder: "Why not the suggestion? (one line, required)" });
    const wMinutes = field("Minutes", dMinutes);
    const wDate = field("Date", dDate);
    const wPerson = field("Delegate to", dPerson);
    const wReason = field("Override reason", dReason);
    const decideBtn = h("button", { type: "submit", class: "btn" }, "Decide");
    const pickedPhrase = () => phrase(dOutcome.value, {
      minutes: parseInt(dMinutes.value, 10), date: dDate.value, person: dPerson.value.trim(),
    });

    function syncPicker() {
      const o = dOutcome.value;
      wMinutes.hidden = o !== "timebox";
      wDate.hidden = o !== "schedule";
      wPerson.hidden = o !== "delegate";
      const matches = sug && o === sug.outcome && dType.value === sug.type && Number(dPriority.value) === sug.priority;
      const override = !!sug && !matches;
      wReason.hidden = !override;
      let ok = !!o && !loading;
      if (override && !dReason.value.trim()) ok = false;
      if (o === "schedule" && !dDate.value) ok = false;
      if (o === "delegate" && !dPerson.value.trim()) ok = false;
      if (o === "timebox" && !(Number(dMinutes.value) >= 1)) ok = false;
      decideBtn.disabled = !ok;
      decideBtn.textContent = !o ? "Decide" : matches ? `Accept: ${pickedPhrase()}` : override ? `Override: ${pickedPhrase()}` : pickedPhrase();
      decideBtn.classList.toggle("s1-btn-warn", override);
    }

    const picker = h("form", { class: "s1-picker", novalidate: true, hidden: !view.elseOpen, onsubmit: (e) => {
      e.preventDefault();
      syncPicker();
      if (decideBtn.disabled) return;
      const o = dOutcome.value;
      const body = {
        outcome: o, type: dType.value, priority: Number(dPriority.value),
        override_reason: wReason.hidden ? null : dReason.value.trim() || null, intake: readIntake(),
      };
      if (o === "timebox") body.timebox_minutes = parseInt(dMinutes.value, 10) || DEFAULT_MINUTES;
      if (o === "schedule") body.scheduled_for = dDate.value;
      if (o === "delegate") body.delegated_to = dPerson.value.trim();
      decide(item, body, decideBtn, pickedPhrase());
    } },
    h("fieldset", { disabled: loading },
      h("legend", {}, sug ? "Decide something else" : "Decide manually"),
      h("div", { class: "s1-row" }, field("Outcome", dOutcome), field("Type", dType), field("Priority", dPriority)),
      h("div", { class: "s1-row" }, wMinutes, wDate, wPerson),
      wReason,
      decideBtn));
    picker.addEventListener("input", syncPicker);
    picker.addEventListener("change", syncPicker);
    syncPicker();

    const setElse = (open) => {
      view.elseOpen = open;
      picker.hidden = !open;
      elseBtn.setAttribute("aria-expanded", String(open));
      if (open) dOutcome.focus();
    };
    elseBtn.addEventListener("click", () => setElse(picker.hidden));
    picker.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && sug) { e.preventDefault(); setElse(false); elseBtn.focus(); }
    });

    // --- "Draft reply": the solution sketch ---
    let draft = null;
    if (draftBtn) {
      const copyBtn = h("button", { type: "button", class: "btn btn-ghost btn-small" }, "Copy");
      copyBtn.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(sug.solution_sketch);
          api.toast("Copied to clipboard.");
        } catch {
          api.toast("Could not copy. Select the text and copy it manually.", "error");
        }
      });
      draft = h("div", { class: "s1-draft", hidden: !view.draftOpen },
        h("div", { class: "s1-draft-head" }, h("span", { class: "s1-label" }, "Solution sketch & draft reply"), copyBtn),
        h("pre", { class: "s1-pre" }, sug.solution_sketch));
      draftBtn.addEventListener("click", () => {
        view.draftOpen = draft.hidden;
        draft.hidden = !view.draftOpen;
        draftBtn.setAttribute("aria-expanded", String(view.draftOpen));
      });
    }

    // --- 5. "Why" disclosure: questions, flags, open questions, raw thread ---
    const fImpact = h("input", { type: "checkbox", name: "production_impact" });
    fImpact.checked = !!state.production_impact;
    const fSize = h("input", { type: "checkbox", name: "size_known" });
    fSize.checked = state.size_known !== false; // unknown until the AI says so: default "known"
    const resuggestBtn = h("button", { type: "button", class: "btn btn-small", hidden: true }, "Re-suggest");
    resuggestBtn.addEventListener("click", async () => {
      resuggestBtn.disabled = true;
      resuggestBtn.textContent = "Re-suggesting…";
      try {
        const next = await api.post(`/s1_intake/items/${item.id}/resuggest`, {
          intake: readIntake(), production_impact: fImpact.checked, size_known: fSize.checked,
        });
        if (my === token) render(next, my, false);
      } catch (err) {
        api.toast(`Re-suggest failed: ${err.message}`, "error");
        resuggestBtn.disabled = false;
        resuggestBtn.textContent = "Re-suggest";
      }
    });
    const questions = h("fieldset", { class: "s1-questions-form", disabled: loading },
      h("legend", {}, "Intake questions"),
      field("What breaks if this waits a week?", qCost),
      field("Is the deadline real or felt?", qDeadline),
      field("How many people are blocked?", qBlocked),
      field("Could someone else reasonably do this?", qDelegable),
      h("div", { class: "s1-flags" },
        h("label", {}, fImpact, " Production impact"),
        h("label", {}, fSize, " Size is known")),
      resuggestBtn);
    questions.addEventListener("input", () => { resuggestBtn.hidden = false; });
    questions.addEventListener("change", () => { resuggestBtn.hidden = false; });

    const openQs = card?.open_questions || [];
    // FR-12: the raw thread stays collapsed by default.
    const raw = item.raw_context
      ? h("details", { class: "s1-raw" },
          h("summary", {}, `Raw thread (${item.raw_context.length.toLocaleString()} chars)`),
          h("pre", { class: "s1-pre" }, item.raw_context))
      : null;
    const why = h("details", { class: "s1-why", open: !!view.whyOpen },
      h("summary", {}, "Why"),
      questions,
      openQs.length ? h("div", { class: "s1-openqs" }, h("span", { class: "s1-label" }, "Open questions"),
        h("ul", {}, ...openQs.map((q) => h("li", {}, q)))) : null,
      raw);
    why.addEventListener("toggle", () => { view.whyOpen = why.open; });

    put(gate, meta, request, notice, verdict, actions, inline, picker, draft, why);
    if (primary) focusIfIdle(primary);
  }

  // --- incidents to log afterwards (FR-17) ---

  function renderLogForm(item) {
    const my = ++token;
    const card = item.ask_card || {};
    const request = h("input", { class: "input", name: "request", maxlength: "500", value: card.request || item.title });
    const requester = h("input", { class: "input", name: "requester", maxlength: "200", value: card.requester || "" });
    const deadline = h("input", { class: "input", type: "date", name: "deadline", value: card.deadline || "" });
    const deadlineKind = select("deadline_kind", DEADLINE_KINDS, card.deadline_kind || "none", "Deadline kind");
    const questions = h("textarea", { class: "input", rows: "3", name: "open_questions",
      placeholder: "One open question per line" });
    questions.value = (card.open_questions || []).join("\n");
    const qCost = select("cost_of_delay", COST, "", "Cost of delay");
    const qDeadline = select("deadline_real", DEADLINE, "", "Deadline real or felt");
    const qBlocked = h("input", { class: "input", type: "number", min: "0", step: "1", name: "blocked_count" });
    const qDelegable = select("delegable", YESNO, "", "Could someone else do it");
    const save = h("button", { type: "submit", class: "btn" }, "Save log");

    put(gate,
      h("p", { class: "s1-meta muted" }, "Incident fast-pathed · log the details"),
      h("h3", { class: "s1-request" }, item.title),
      h("form", { class: "s1-picker", novalidate: true, onsubmit: async (e) => {
        e.preventDefault();
        save.disabled = true;
        try {
          await api.put(`/s1_intake/items/${item.id}/log`, {
            ask_card: {
              request: request.value.trim(), requester: requester.value.trim(),
              deadline: deadline.value || null, deadline_kind: deadlineKind.value,
              open_questions: questions.value.split("\n").map((q) => q.trim()).filter(Boolean),
            },
            intake: {
              cost_of_delay: qCost.value || null, deadline_real: qDeadline.value || null,
              blocked_count: qBlocked.value === "" ? null : Math.max(0, parseInt(qBlocked.value, 10) || 0),
              delegable: qDelegable.value === "" ? null : qDelegable.value === "true",
            },
          });
          if (my !== token) return;
          api.toast("Incident logged.");
          showEmpty();
          api.emit("items:changed");
        } catch (err) {
          api.toast(`Could not save: ${err.message}`, "error");
          save.disabled = false;
        }
      } },
      h("fieldset", {}, h("legend", {}, "Ask card"),
        field("Request", request), field("Requester", requester),
        h("div", { class: "s1-row" }, field("Deadline", deadline), field("Deadline kind", deadlineKind)),
        field("Open questions", questions)),
      h("fieldset", {}, h("legend", {}, "Intake questions"),
        field("What breaks if this waits a week?", qCost),
        field("Is the deadline real or felt?", qDeadline),
        field("How many people are blocked?", qBlocked),
        field("Could someone else reasonably do this?", qDelegable)),
      h("div", { class: "s1-actions" }, save,
        h("button", { type: "button", class: "btn btn-ghost btn-small", onclick: showEmpty }, "Cancel"))));
    request.focus({ preventScroll: true });
  }

  async function refreshPending() {
    try {
      const list = await api.get("/s1_intake/pending-log");
      if (!list.length) { put(pending); return; }
      put(pending, h("p", { class: "s1-pending-line" },
        h("span", { class: "muted" }, `Incidents to log (${list.length}):`),
        ...list.map((item) => h("button", { type: "button", class: "s1-link", title: `Log details for: ${item.title}`,
          onclick: () => renderLogForm(item) }, item.title))));
    } catch (err) {
      put(pending, h("p", { class: "error" }, `Could not load incidents to log: ${err.message}`));
    }
  }

  api.on("item:open", (detail) => openItem(detail?.id ?? null));
  api.on("items:changed", refreshPending);
  const selected = typeof api.selectedId === "function" ? api.selectedId() : null;
  if (selected !== null && selected !== undefined) openItem(selected);
  else showEmpty();
  refreshPending();
}
