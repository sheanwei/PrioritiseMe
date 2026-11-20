# Building a slice

Read [PRD.md](../../PRD.md) §4 (contracts) and your own slice section first. This file is the practical checklist.

## Your folder

```
taskbot/slices/s2_timebox/
  __init__.py
  router.py        # REQUIRED: router = APIRouter(); optional startup()
  service.py       # your logic (call taskbot.core.items, never another slice)
  prompts.py       # LLM prompts + JSON schemas (if you use the LLM)
  static/
    panel.json     # dashboard panel manifest (object or list of objects)
    panel.js       # export function mount(el, api) { ... }
  tests/
    test_*.py
migrations/0200_s2_timebox.sql   # your number range only
```

Folder names: `s1_intake`, `s2_timebox`, `s3_resurface`, `s4_outlook`, `s5_review`.

## Wiring (automatic)

- `router` is mounted at `/api/<folder>`. For example, `@router.get("/state")` becomes `GET /api/s2_timebox/state`.
- `startup()` runs once at app start, after migrations. Register background jobs here:
  ```python
  from taskbot.core.scheduler import register_job
  def startup():
      register_job("s2_timebox.expire", 30, expire_due)  # name it "<slice>.<job>"
  ```
- `static/` is served at `/slices/<folder>/`.
- `static/panel.json` adds your panel(s) to the dashboard. A relative `module` is resolved inside your static folder:
  ```json
  {"title": "Timeboxes", "module": "panel.js", "order": 30, "region": "list-footer"}
  ```
  Orders: core status item 10, S1 20, S2 30, S3 40–45, S4 50, S5 60.
- `region` picks where the panel mounts (PRD §4.7):
  - `status`: inline item in the top-bar status strip (`el` is a `<span>`; the shell adds `·` separators)
  - `list-footer`: collapsible `<details>` under the triage list, collapsed, `<summary>` = your title
  - `detail`: right column of the Triage tab, stacked by `order`
  - `mail` / `review`: the Mail / Review tab, full width
  - `hidden`: never mounted

  Missing or unknown → a fallback "More" section at the bottom of the Triage tab.

## Core API you'll use

```python
from taskbot.core import items, llm, clock
from taskbot.core.models import Status, Outcome, WorkType, EventKind, OUTCOME_TO_STATUS, is_open

items.create_item(title, source, raw_context=..., source_ref=..., **columns)
items.get_item(id) / items.list_items(status=..., source=..., source_ref=...)
items.update_fields(id, ask_card={...}, type="interrupt", my_slice_column=...)  # not status
items.transition(id, Status.SCHEDULED, outcome="schedule", scheduled_for="2026-11-25")
items.log_event(id, EventKind.DECIDED, {...})
items.list_events(item_id=..., kinds=[...], since=iso, until=iso)

llm.complete_json(system, user, schema, model=None)  # raises llm.LLMError
clock.now() / clock.now_iso()                         # never datetime.now()
```

- `transition()` enforces the state machine and logs `status_changed` for you. Log your own domain events (e.g. `decided`, `timebox_expired`) with `log_event`.
- Several writes in one transaction: `with db.connect() as conn:` and pass `conn=conn` to each call.
- Timestamps are UTC ISO strings (`2026-11-20T06:30:00+00:00`). Dates without a time (`scheduled_for`) are `YYYY-MM-DD`.
- Columns you add in your migration show up in `item.extra["col"]` and can be set with `update_fields`.
- Migrations: plain SQL with no `BEGIN`/`COMMIT` (the runner wraps each file in a transaction). Never alter or drop core columns.
- LLM schemas: add an `"examples": [ {...} ]` entry. Fake mode returns `examples[0]`, so you control what your tests see. Examples are never sent to the real model.

## Panel API (`mount(el, api, ctx)`)

```js
api.get("/s2_timebox/state")   api.post(path, body)   api.put/patch/del
api.on("items:changed", fn)    api.emit("items:changed")
api.on("item:open", ({id}) => ...)   // the triage list emits this on click/Enter; id null = nothing selected
api.on("tab:shown", ({tab}) => ...)  // "triage" | "mail" | "review"
api.showTab("triage")          api.selectedId()      // id last opened via item:open, or null
api.notify(title, body)        // browser notification + toast
api.toast(msg, "error")        api.h(tag, attrs, ...children)   api.timeAgo(iso)

ctx.region                     // your region ("more" in the fallback section)
ctx.setTitle("Resurfacing · 3")  // list-footer: updates the <summary>; no-op elsewhere
ctx.setHidden(true)            // hide your whole slot when there is nothing to show
```

When the selected item leaves the triage list, the shell auto-advances: it emits `item:open` for the first remaining row, or `{id: null}` when the list is empty.

Style with the tokens in `taskbot/static/style.css` and the shared classes `.btn .btn-ghost .btn-small .input .badge .muted .error .item-list .item`. Don't draw boxed cards around your panel; the shell owns the layout.

Build DOM with `api.h` (or `textContent`) and never with `innerHTML`, because email content is untrusted.

## Tests

- The fixtures in the root `conftest.py` apply automatically: a fresh temp database, fake LLM, fake mail, and the scheduler off.
- Use the `client` fixture for API tests (it runs your `startup()`).
- Seed "given" state with `from tests.fixtures import seed_item, seed_event`.
- Freeze time with `clock.set_now(datetime(..., tzinfo=timezone.utc))`. It's reset after each test.
- Run your jobs directly (call the function), or call `scheduler.run_all_once()`.
- Run with `.venv\Scripts\python -m pytest taskbot/slices/<folder>`.
