// S5 Weekly review panel (FR-50..FR-54).
// Full-width Review tab (region "review"): week selector, a headline row of
// five numbers, then mix by type and outcome, staleness
// closures, "Could have waited", override log with highlighted patterns, and
// the AI acceptance rate. DOM is built with api.h / textContent only; no HTML parsing.

const STYLE_ID = "s5-review-style";
const CSS = `
.s5 { display: grid; gap: 16px; min-width: 0; }
.s5-nav { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.s5-range { font-weight: 600; margin-right: auto; }
.s5-body { display: grid; gap: 16px; min-width: 0; }
.s5-stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(110px, 1fr)); gap: 8px; }
.s5-stat { border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); padding: 10px 12px; min-width: 0; }
.s5-stat-value { font-size: 26px; font-weight: 700; display: block; line-height: 1.15; font-variant-numeric: tabular-nums; }
.s5-stat-label { font-size: 12px; color: var(--muted); }
.s5-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 320px), 1fr)); gap: 16px 24px; align-items: start; }
.s5-section { min-width: 0; }
.s5-section h3 { font-size: 12px; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); margin: 0 0 8px; }
.s5-bars { display: grid; grid-template-columns: max-content 1fr max-content; gap: 4px 8px; align-items: center; font-size: 13px; }
.s5-bar { height: 8px; background: var(--accent); border-radius: 4px; min-width: 2px; }
.s5-bar-zero { background: var(--border); }
.s5-list { list-style: none; margin: 0; padding: 0; font-size: 13px; }
.s5-list li { padding: 6px 0; border-bottom: 1px solid var(--border); overflow-wrap: anywhere; }
.s5-list li:last-child { border-bottom: 0; }
.s5-chips { display: flex; flex-wrap: wrap; gap: 4px; }
.s5-hot { background: var(--warn); color: var(--surface); font-weight: 600; }
.s5-reason { display: block; color: var(--muted); }
`;

const TYPES_ORDER = ["incident", "interrupt", "toil", "planned", "unclassified"];
const OUTCOMES = { do_now: "Do now", timebox: "Timebox", schedule: "Schedule", delegate: "Delegate", decline: "Decline" };

function ensureStyle(h) {
  if (document.getElementById(STYLE_ID)) return;
  const style = h("style", { id: STYLE_ID });
  style.textContent = CSS;
  document.head.append(style);
}

function fmtRange(startIso, endIso) {
  const opts = { weekday: "short", day: "numeric", month: "short" };
  const s = new Date(startIso + "T00:00:00");
  const e = new Date(endIso + "T00:00:00");
  return `${s.toLocaleDateString(undefined, opts)} – ${e.toLocaleDateString(undefined, { ...opts, year: "numeric" })}`;
}

export function mount(el, api, ctx) {
  const { h } = api;
  ensureStyle(h);

  let start = null; // null = current week (server decides, via clock.now())
  let last = null;

  const range = h("span", { class: "s5-range" }, "…");
  const prevBtn = h("button", { type: "button", class: "btn btn-ghost btn-small", "aria-label": "Previous week",
    onclick: () => { if (last) { start = last.week.prev; refresh(); } } }, "‹ Prev");
  const currentBtn = h("button", { type: "button", class: "btn btn-ghost btn-small",
    onclick: () => { start = null; refresh(); } }, "This week");
  const nextBtn = h("button", { type: "button", class: "btn btn-ghost btn-small", "aria-label": "Next week",
    onclick: () => { if (last) { start = last.week.next; refresh(); } } }, "Next ›");
  const nav = h("div", { class: "s5-nav" }, range, prevBtn, currentBtn, nextBtn);
  const body = h("div", { class: "s5-body", "aria-live": "polite" });

  function stat(value, label, title) {
    return h("div", { class: "s5-stat", title: title || null },
      h("span", { class: "s5-stat-value" }, String(value)),
      h("span", { class: "s5-stat-label" }, label));
  }

  function section(title, ...children) {
    return h("section", { class: "s5-section" }, h("h3", {}, title), ...children);
  }

  function bars(entries) {
    const max = Math.max(1, ...entries.map(([, n]) => n));
    return h("div", { class: "s5-bars" }, entries.flatMap(([label, n]) => [
      h("span", {}, label),
      h("span", { class: n ? "s5-bar" : "s5-bar s5-bar-zero", style: `width:${Math.round((n / max) * 100)}%` }),
      h("span", { class: "muted" }, String(n)),
    ]));
  }

  function empty(text) {
    return h("p", { class: "muted" }, text);
  }

  function chips(groups) {
    if (!groups.length) return empty("None.");
    return h("div", { class: "s5-chips" }, groups.map((g) =>
      h("span", { class: g.highlight ? "badge s5-hot" : "badge", title: g.highlight ? "Appears more than twice" : null,
        "data-highlight": g.highlight ? "1" : null }, `${g.label} ×${g.count}`)));
  }

  function render(r) {
    last = r;
    range.textContent = fmtRange(r.week.start, r.week.end);
    range.title = `Week boundaries in ${r.week.timezone}`;
    currentBtn.disabled = r.week.is_current;
    nextBtn.disabled = r.week.is_current || r.week.is_future;

    const acc = r.acceptance;
    const stats = h("div", { class: "s5-stats" },
      stat(r.decided.total, "items decided"),
      stat(acc.percent === null ? "—" : `${acc.percent}%`, "AI accepted",
        `${acc.accepted} accepted, ${acc.overridden} overridden`),
      stat(r.overrides.count, "overrides"),
      stat(r.staleness_closures.count, "closed stale"),
      stat(r.could_have_waited.count, "could have waited"));

    const typeEntries = TYPES_ORDER.filter((t) => t in r.decided.by_type).map((t) => [t, r.decided.by_type[t]]);
    const outcomeEntries = Object.keys(OUTCOMES).map((o) => [OUTCOMES[o], r.decided.by_outcome[o] || 0]);

    const staleList = r.staleness_closures.items.length
      ? h("ul", { class: "s5-list" }, r.staleness_closures.items.map((i) => h("li", {},
          i.title, " ", h("span", { class: "muted" },
            i.silent_days != null ? `· silent ${i.silent_days} working days` : "", ` · ${api.timeAgo(i.at)}`))))
      : empty("No staleness closures this week.");

    const waitedList = r.could_have_waited.items.length
      ? h("ul", { class: "s5-list" }, r.could_have_waited.items.map((i) => h("li", {},
          h("span", { class: "badge" }, r.could_have_waited.label), " ", i.title, " ",
          h("span", { class: "muted" }, `· ${i.requester} · declined ${api.timeAgo(i.declined_at)}`))))
      : empty("No declined items without follow-up this week.");

    const overrideList = r.overrides.log.length
      ? h("ul", { class: "s5-list" }, r.overrides.log.map((o) => h("li", {},
          h("strong", {}, o.title), " ",
          h("span", { class: o.highlight_requester ? "badge s5-hot" : "badge" }, o.requester), " ",
          h("span", { class: "muted" }, `AI: ${OUTCOMES[o.suggested_outcome] || "?"} → ${OUTCOMES[o.chosen_outcome] || o.chosen_outcome || "?"}`),
          h("span", { class: "s5-reason" }, o.reason ? `“${o.reason}”` : "(no reason recorded)"))))
      : empty("No overrides this week.");

    body.replaceChildren(
      stats,
      h("div", { class: "s5-grid" },
        section("By type", r.decided.total ? bars(typeEntries) : empty("Nothing decided this week.")),
        section("By outcome", r.decided.total ? bars(outcomeEntries) : empty("Nothing decided this week.")),
        section("Overrides by requester", chips(r.overrides.by_requester)),
        section("Overrides by reason keyword", chips(r.overrides.by_keyword))),
      h("div", { class: "s5-grid" },
        section("Override log", overrideList),
        section("Staleness closures", staleList),
        section("Could have waited", waitedList)),
    );
  }

  async function refresh() {
    try {
      const q = start ? `?start=${encodeURIComponent(start)}` : "";
      render(await api.get(`/s5_review/week${q}`));
    } catch (err) {
      body.replaceChildren(h("p", { class: "error" }, `Could not load review: ${err.message}`));
    }
  }

  el.append(h("div", { class: "s5" }, nav, body));
  api.on("items:changed", refresh);
  api.on("tab:shown", (d) => { if (d?.tab === "review") refresh(); });
  refresh();
}
