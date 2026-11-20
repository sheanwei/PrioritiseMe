# TaskBot — v1 Spec

_Status: draft for confirmation · 2026-11-20_

## 1. Problem

I'm an engineer carrying both on-call and project load. Ad-hoc tasks and incidents arrive feeling urgent, their size is unknown up front, and many people commenting on them makes it hard to think. As a result, interrupt work crowds out planned work — even when, in hindsight, much of it could have been deprioritized.

## 2. Goal

A **personal**, **local** tool that forces a deliberate decision the moment ad-hoc work arrives, and shows — with data — how much "urgent" work could have waited.

**Non-goals (v1):**
- team or shared use, and requester-facing views
- Slack in any form
- PagerDuty / Jira integrations
- capacity budgets and time tracking (deferred, see §9)
- full backlog grooming

## 3. Core concepts

### 3.1 Work types

| Type | Meaning |
|---|---|
| **Incident** | Production impact or SLO at risk |
| **Interrupt** | Ad-hoc request from a person (incl. emails needing a reply) |
| **Toil** | Recurring ops work |
| **Planned** | Project / roadmap work |

Types are used for prioritization and reporting. There are no capacity budgets in v1.

### 3.2 Intake gate (the heart of the product)

Every new item must reach one outcome before work starts:

**Do now · Timebox & re-assess · Schedule · Delegate · Decline**

Flow:

1. **Capture**: an item arrives from manual capture or email (see §4).
2. **Ask card**: the LLM condenses the thread into one card (see §3.3).
3. **Four intake questions**, pre-filled by the LLM where it can. The user confirms or edits the answers.
   1. What breaks if this waits a week? (none / minor / significant)
   2. Is the deadline real (external, dated) or felt?
   3. Who is blocked, and how many?
   4. Could someone else reasonably do this?
4. **Suggestion**: the LLM proposes a work type, an outcome, a priority, and a short rationale. It reasons from the answers and the user's current active and planned items. Where it can, it also proposes a **solution sketch**: likely first steps, questions to ask the requester, or a draft email reply.
5. **Decision**: the user accepts the suggestion, or overrides it with a **mandatory one-line reason**. Every override is logged.

Guardrails (rules, applied on top of the LLM):
- An item typed **Incident** with production impact defaults to **Do now**. It skips the gate and is logged afterwards.
- If the item's size is unknown, the default outcome is **Timebox & re-assess** (30 min).
- The LLM only suggests. It never makes a decision on its own.

### 3.3 Ask card (noise reduction)

The LLM summary shown instead of the raw thread:
- **Request**: one sentence
- **Requester**
- **Stated deadline**, or "none stated"
- **Open questions**
- **Pressure counter**: "+1", "any update?" and repeated pings increment a counter instead of showing as content

The raw thread is hidden by default and can be expanded on demand.

### 3.4 Triage timebox

- For unknown-size items: a 30-minute default timebox, adjustable per item.
- When the timer ends, a browser notification fires from the dashboard.
- A **mandatory re-assess** then re-runs the intake gate with what has been learned.

### 3.5 Deferred work resurfacing

- **Scheduled** and **Deferred** items reappear on their chosen date.
- **Staleness check**: if a deferred item has had no follow-up from anyone for **5 working days**, the tool suggests closing it as "not actually needed." This is the core evidence metric.
- For email items, "follow-up" means a new message in the same conversation.

## 4. Inputs

| Source | v1 behaviour |
|---|---|
| **Manual capture** | Quick-add box on the dashboard. Free text, with an optional pasted thread as context. |
| **Outlook (full mailbox)** | All incoming mail is ingested. The LLM classifies each message: **needs action from me** / **FYI** / **noise**. Only "needs action" messages become intake candidates, defaulting to type **Interrupt**. Others are hidden in the tool's view only; the mailbox itself is never modified. |

Design note: pulling the whole mailbox risks turning TaskBot into a new source of interrupts. Mitigations:
- Email candidates collect in an **inbox tray**. Arriving email never triggers a notification.
- The user triages the tray in batches.
- Misclassifications can be corrected with one click. Corrections are kept as few-shot examples for future classification.

## 5. Dashboard (local web page)

- **Today**: active items, timeboxes with countdowns, top 3 Planned items, items resurfacing today.
- **Inbox tray**: captured and email candidates awaiting the intake gate.
- **Weekly review**:
  - Mix of items by type and by outcome
  - Staleness closures: "urgent" items deferred or declined with no consequence
  - Override log, with recurring requesters and recurring reasons highlighted

## 6. Architecture (MVP / demo)

Optimized for the fastest path to a working demo.

| Concern | Choice |
|---|---|
| **Runtime** | Python 3.13 (already installed) as one local process: web server, background mail poller, and scheduler |
| **Web / dashboard** | FastAPI serving a single HTML page with plain JS, on `http://localhost` |
| **Storage** | SQLite via Python's built-in `sqlite3`, in one local file |
| **LLM** | OpenCode Zen through its OpenAI-compatible endpoint (`https://opencode.ai/zen/v1`) using the `openai` Python SDK. Config comes from `.env`: `OPENCODE_API_KEY`, plus `OPENCODE_MODEL`, which is swappable. |
| **Outlook** | TaskBot acts as an **MCP client** using the Python `mcp` SDK. It launches an Outlook/Microsoft 365 MCP server over stdio via `npx` (Node is installed). Candidate server: `@softeria/ms-365-mcp-server` with its mail-only preset. Login is a Microsoft device-code sign-in done once by the user. |
| **Mail access** | **Read-only**: TaskBot only calls list/read tools. If the server supports a read-only mode, it is enabled. |
| **Notifications** | Browser notifications from the open dashboard tab, for timebox ends and resurfacing items |

MVP simplifications:
- No global hotkey; capture happens in the dashboard's quick-add box.
- Mail is polled every few minutes; there are no push subscriptions.
- One LLM call per item produces the ask card, pre-filled intake answers, suggestion and solution sketch. Email classification uses a separate, cheaper call per message.

## 7. Data model (sketch)

```
Item
  id, source (manual|email), source_ref (email conversation id), raw_context
  ask_card {request, requester, deadline, deadline_kind(real|felt|none), open_questions[]}
  pressure_count
  type (incident|interrupt|toil|planned)
  intake {cost_of_delay, deadline_real, blocked_count, delegable}
  suggestion {outcome, priority, rationale, solution_sketch}
  decision {outcome, override_reason?}
  timebox_end?, scheduled_for?
  status (tray|active|timeboxed|scheduled|delegated|declined|done|closed_stale)
  timestamps: created, decided, last_external_activity, closed

EmailMessage    { id, conversation_id, from, subject, received_at, classification, user_corrected }
```

## 8. Success criteria

After 4 weeks of use:
- Every new ad-hoc item went through the gate before work started (or was logged afterwards, for incidents).
- There is a measurable count of Interrupt items that were deferred, declined or closed stale with no follow-up from the requester.
- The override log reveals at least one recurring pattern I can act on.

## 9. Deferred to later

- Capacity budgets per work type (on-call vs off-call profiles) and time tracking
- Global capture hotkey and native Windows notifications
- Slack capture
- PagerDuty / Jira import
