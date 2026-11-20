// S1 intake gate panel (FR-10..FR-18).
// Opens when the tray emits "item:open" {id}. All untrusted text (threads, emails,
// LLM output) goes through api.h / textContent, never innerHTML.

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

export function mount(el, api) {
  ensureCss();
  const { h } = api;
  const gate = h("div", { class: "s1-gate", "aria-live": "polite" });
  const pending = h("div", { class: "s1-pending" });
  el.append(gate, pending);

  // Bumped on every open/close so late responses for an old item are ignored.
  let token = 0;

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

  function showPlaceholder() {
    token += 1;
    gate.replaceChildren(h("p", { class: "muted" }, "Click a tray item to open the intake gate."));
  }

  function showError(msg) {
    gate.replaceChildren(h("p", { class: "error", role: "alert" }, msg),
      h("button", { type: "button", class: "btn btn-ghost btn-small", onclick: showPlaceholder }, "Close"));
  }

  async function openItem(id) {
    const my = ++token;
    gate.replaceChildren(h("p", { class: "muted" }, "Opening…"));
    el.closest(".panel")?.scrollIntoView({ behavior: "smooth", block: "start" });
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
      renderGate(state, my, false);
      return;
    }
    renderGate(state, my, true);
    await analyze(id, my, false);
  }

  async function analyze(id, my, force) {
    try {
      const state = await api.post(`/s1_intake/items/${id}/analyze${force ? "?force=true" : ""}`);
      if (my !== token) return;
      renderGate(state, my, false);
    } catch (err) {
      if (my !== token) return;
      try {
        const state = await api.get(`/s1_intake/items/${id}`);
        if (my === token) renderGate({ ...state, error: `AI analysis failed: ${err.message}` }, my, false);
      } catch (err2) {
        if (my === token) showError(`Could not open item: ${err2.message}`);
      }
    }
  }

  function renderGate(state, my, loading) {
    const item = state.item;
    const card = state.ask_card;
    const sug = state.suggestion;
    const intake = state.intake || {};

    // --- header ---
    const fastPath = h("button", {
      type: "button", class: "btn s1-btn-danger btn-small",
      title: "Skip the gate: do it now as an incident and log the details afterwards",
      onclick: async () => {
        fastPath.disabled = true;
        try {
          await api.post(`/s1_intake/items/${item.id}/incident`);
          api.toast(`Incident started: ${item.title}. Log the details when done.`);
          api.emit("items:changed");
          showPlaceholder();
        } catch (err) {
          api.toast(`Could not start incident: ${err.message}`, "error");
          fastPath.disabled = false;
        }
      },
    }, "Incident — log after");
    const header = h("div", { class: "s1-header" },
      h("h3", { class: "s1-title" }, item.title),
      h("div", { class: "s1-actions" },
        fastPath,
        h("button", { type: "button", class: "btn btn-ghost btn-small", onclick: showPlaceholder }, "Close")));

    // --- status ---
    const status = [];
    if (loading) status.push(h("p", { class: "muted s1-loading" }, "Reading the thread and preparing a suggestion…"));
    if (state.error) {
      status.push(h("div", { class: "s1-error", role: "alert" },
        h("strong", {}, "AI unavailable. "), state.error, " You can still decide manually.",
        h("button", { type: "button", class: "btn btn-ghost btn-small",
          onclick: () => { renderGate({ ...state, error: null }, my, true); analyze(item.id, my, true); } }, "Retry")));
    }

    // --- ask card ---
    const pressure = state.pressure_count || 0;
    let cardEl;
    if (card) {
      const qs = card.open_questions || [];
      cardEl = h("div", { class: "s1-card" },
        h("p", { class: "s1-request" }, card.request || "(no request summarised)"),
        h("dl", { class: "s1-dl" },
          h("dt", {}, "Requester"), h("dd", {}, card.requester || "unknown"),
          h("dt", {}, "Deadline"), h("dd", {}, `${card.deadline || "none stated"} (${card.deadline_kind || "none"})`),
          h("dt", {}, "Pressure"), h("dd", {},
            h("span", { class: pressure > 0 ? "badge badge-pressure" : "badge" }, `×${pressure}`),
            " follow-up pings")),
        qs.length ? h("div", {}, h("span", { class: "s1-label" }, "Open questions"),
          h("ul", { class: "s1-questions" }, ...qs.map((q) => h("li", {}, q)))) : null);
    } else {
      cardEl = h("p", { class: "muted" }, loading ? "Ask card on its way…" : "No ask card.");
    }

    // --- raw thread (FR-12: collapsed by default) ---
    const raw = item.raw_context
      ? h("details", { class: "s1-raw" },
          h("summary", {}, `Raw thread (${item.raw_context.length.toLocaleString()} chars)`),
          h("pre", { class: "s1-pre" }, item.raw_context))
      : null;

    // --- the four questions (FR-11) + guardrail facts ---
    const qCost = select("cost_of_delay", COST, intake.cost_of_delay, "Cost of delay");
    const qDeadline = select("deadline_real", DEADLINE, intake.deadline_real, "Deadline real or felt");
    const qBlocked = h("input", { class: "input", type: "number", min: "0", step: "1", name: "blocked_count",
      "aria-label": "People blocked", value: intake.blocked_count ?? "" });
    const qDelegable = select("delegable", YESNO, intake.delegable, "Could someone else do it");
    const fImpact = h("input", { type: "checkbox", name: "production_impact", "aria-label": "Production impact" });
    fImpact.checked = !!state.production_impact;
    const fSize = h("input", { type: "checkbox", name: "size_known", "aria-label": "Size is known" });
    fSize.checked = state.size_known !== false; // unknown until the AI says so: default "known"

    const resuggestBtn = h("button", { type: "button", class: "btn btn-small", hidden: true }, "Re-suggest");
    const readIntake = () => ({
      cost_of_delay: qCost.value || null,
      deadline_real: qDeadline.value || null,
      blocked_count: qBlocked.value === "" ? null : Math.max(0, parseInt(qBlocked.value, 10) || 0),
      delegable: qDelegable.value === "" ? null : qDelegable.value === "true",
    });
    resuggestBtn.addEventListener("click", async () => {
      resuggestBtn.disabled = true;
      resuggestBtn.textContent = "Re-suggesting…";
      try {
        const next = await api.post(`/s1_intake/items/${item.id}/resuggest`, {
          intake: readIntake(), production_impact: fImpact.checked, size_known: fSize.checked,
        });
        if (my === token) renderGate(next, my, false);
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

    // --- suggestion ---
    let sugEl;
    if (sug) {
      sugEl = h("div", { class: "s1-suggestion" },
        h("div", { class: "s1-sug-head" },
          h("strong", {}, `Suggested: ${label(OUTCOMES, sug.outcome)}`),
          h("span", { class: "badge" }, label(TYPES, sug.type)),
          h("span", { class: "badge" }, `P${sug.priority}`),
          sug.rule_adjusted ? h("span", { class: "badge s1-rule", title: (sug.rules || []).join(" ") }, "rule-adjusted") : null),
        sug.rule_adjusted
          ? h("p", { class: "s1-rule-note" }, `${(sug.rules || []).join(" ")} (AI proposed: ${label(OUTCOMES, sug.llm_outcome)})`)
          : null,
        h("p", {}, sug.rationale),
        sug.solution_sketch ? h("details", { class: "s1-sketch", open: true },
          h("summary", {}, "Solution sketch"), h("pre", { class: "s1-pre" }, sug.solution_sketch)) : null);
    } else if (!loading) {
      sugEl = h("p", { class: "muted" }, "No AI suggestion. Decide manually.");
    }

    // --- decision (FR-14..16) ---
    const dOutcome = select("outcome", sug ? OUTCOMES : [["", "Choose outcome…"], ...OUTCOMES], sug?.outcome ?? "", "Outcome");
    const dType = select("type", TYPES, sug?.type ?? item.type ?? "interrupt", "Work type");
    const dPriority = select("priority", PRIORITIES, String(sug?.priority ?? item.priority ?? 3), "Priority");
    const dMinutes = h("input", { class: "input", type: "number", min: "1", max: "480", step: "1", value: "30", name: "timebox_minutes" });
    const dDate = h("input", { class: "input", type: "date", name: "scheduled_for", min: todayLocal(),
      value: nextWorkdayLocal() });
    const dPerson = h("input", { class: "input", type: "text", name: "delegated_to", maxlength: "200", placeholder: "Who?" });
    const dReason = h("input", { class: "input", type: "text", name: "override_reason", maxlength: "300",
      placeholder: "Why override? (one line, required)" });
    const wMinutes = field("Minutes", dMinutes);
    const wDate = field("Date", dDate);
    const wPerson = field("Delegate to", dPerson);
    const wReason = field("Override reason", dReason);
    const decideBtn = h("button", { type: "submit", class: "btn" }, "Decide");

    function sync() {
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
      decideBtn.textContent = !sug ? "Decide" : matches ? "Accept suggestion" : "Override";
      decideBtn.classList.toggle("s1-btn-warn", override);
    }

    const decision = h("form", { class: "s1-decision", novalidate: true, onsubmit: async (e) => {
      e.preventDefault();
      sync();
      if (decideBtn.disabled) return;
      decideBtn.disabled = true;
      const o = dOutcome.value;
      const body = {
        outcome: o, type: dType.value, priority: Number(dPriority.value),
        override_reason: wReason.hidden ? null : dReason.value.trim() || null, intake: readIntake(),
      };
      if (o === "timebox") body.timebox_minutes = parseInt(dMinutes.value, 10) || 30;
      if (o === "schedule") body.scheduled_for = dDate.value;
      if (o === "delegate") body.delegated_to = dPerson.value.trim();
      try {
        const res = await api.post(`/s1_intake/items/${item.id}/decide`, body);
        const verb = res.event.payload.accepted ? "Accepted" : "Decided";
        api.toast(`${verb}: ${label(OUTCOMES, o)} — ${item.title}`);
        api.emit("items:changed");
        showPlaceholder();
      } catch (err) {
        api.toast(`Could not decide: ${err.message}`, "error");
        decideBtn.disabled = false;
      }
    } },
    h("fieldset", { class: "s1-decision-fields", disabled: loading },
      h("legend", {}, "Decision"),
      h("div", { class: "s1-row" }, field("Outcome", dOutcome), field("Type", dType), field("Priority", dPriority)),
      h("div", { class: "s1-row" }, wMinutes, wDate, wPerson),
      wReason,
      decideBtn));
    decision.addEventListener("input", sync);
    decision.addEventListener("change", sync);
    sync();

    gate.replaceChildren(
      header,
      h("div", { class: "s1-meta" },
        h("span", { class: `badge badge-${item.source}` }, item.source),
        h("span", { class: "muted" }, api.timeAgo(item.created_at))),
      ...status,
      cardEl, raw, questions, sugEl, decision,
    );
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

    gate.replaceChildren(
      h("div", { class: "s1-header" },
        h("h3", { class: "s1-title" }, `Log incident: ${item.title}`),
        h("div", { class: "s1-actions" },
          h("button", { type: "button", class: "btn btn-ghost btn-small", onclick: showPlaceholder }, "Close"))),
      h("form", { class: "s1-decision", novalidate: true, onsubmit: async (e) => {
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
          api.emit("items:changed");
          showPlaceholder();
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
      save));
    el.closest(".panel")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function refreshPending() {
    try {
      const list = await api.get("/s1_intake/pending-log");
      if (!list.length) { pending.replaceChildren(); return; }
      pending.replaceChildren(
        h("h3", { class: "s1-subtitle" }, "Incidents to log"),
        h("ul", { class: "item-list" }, ...list.map((item) => h("li", {},
          h("button", { type: "button", class: "item", onclick: () => renderLogForm(item) },
            h("span", { class: "item-title" }, item.title),
            h("span", { class: "item-meta" },
              h("span", { class: "badge" }, item.status),
              h("span", { class: "muted" }, api.timeAgo(item.decided_at))))))));
    } catch (err) {
      pending.replaceChildren(h("p", { class: "error" }, `Could not load incidents to log: ${err.message}`));
    }
  }

  api.on("item:open", ({ id }) => openItem(id));
  api.on("items:changed", refreshPending);
  showPlaceholder();
  refreshPending();
}
