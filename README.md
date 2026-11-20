# TaskBot

A personal, local intake gate for ad-hoc work. See [PRD.md](PRD.md) (build contract) and [SPEC.md](SPEC.md) (rationale).

## Setup (Windows, PowerShell)

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
Copy-Item .env.example .env   # then fill in OPENCODE_API_KEY and OPENCODE_MODEL
```

## Run

```powershell
.venv\Scripts\python -m taskbot              # http://127.0.0.1:8765
.venv\Scripts\python -m taskbot --port 8770  # if 8765 is taken
```

Without a key, run offline with `$env:TASKBOT_LLM="fake"; $env:TASKBOT_MAIL="fake"` first.

## Check the LLM endpoint

```powershell
.venv\Scripts\python -m taskbot.core.llm
```

This makes one real call through OpenCode Zen and prints `OK: {...}` or the reason it failed.

## Test

```powershell
.venv\Scripts\python -m pytest
```

Tests always run with a fake LLM, fake mail, a temporary database and the scheduler off. No network or key is needed.

## 5-minute demo

Start with mail in sample mode (`TASKBOT_MAIL=fake` in `.env`) and the real LLM, then open the dashboard.

1. **Triage tab.** The left list holds everything waiting for a decision. Icons show where each item came from: ✉ email, ⟳ "back today" (resurfaced), ⧗ "re-assess" (timebox ended), • typed in. `×N` is follow-up pressure. The top line reads e.g. `6 to triage · timebox 1:41 · 2 probably not needed`.
2. **Add a noisy request.** Type a title, click **+ thread**, paste a chat thread full of "+1" and "any update?", then **Add**.
3. **Open it.** In about 5 s the card shows who's asking, the one-sentence request, and the AI's verdict with one reason. The "+1"s become a pressure count instead of content.
4. **Accept or override.**
   - Accept with the primary button (e.g. **Delegate…**, which asks for a name).
   - Or choose **Something else**, pick a different outcome, and give the one-line reason it requires.
   - **Draft reply** shows a ready-to-send answer. **Why** holds the four intake questions, the open questions and the raw thread.
5. **Incident.** Open the prod-500s item and click **Incident, log after**. It goes straight to active/P1, with no AI wait.
6. **Timebox.** Timebox an item for 1 minute. The status line counts down. When it ends, the item comes back marked ⧗ re-assess, and deciding is blocked until you answer "What did you learn?" in the banner.
7. **Probably not needed.** Open the folded section under the list. Items with 5+ working days of silence can be closed as stale (or kept).
8. **Mail tab.** **Sync now** sorts mail into needs action, FYI and noise. Only "needs action" reaches the triage list. Fix a wrong call with **Mark:**.
9. **Review tab.** This week's decisions, AI acceptance, overrides, stale closures and "could have waited".

### Real Outlook (smoke test, FR-63)

Set `TASKBOT_MAIL=mcp` in `.env`, restart, open the Mail tab and follow the Microsoft device-code sign-in. If your tenant blocks the default app, set `MS365_MCP_CLIENT_ID` to your own Entra app registration (see `.env.example`).

## Layout

| Path | What | Owner |
|---|---|---|
| `taskbot/core/` | config, db, models, items, llm, scheduler, clock, app, routes | S0 (frozen: additive changes only) |
| `taskbot/static/` | dashboard shell (`index.html`, `app.js`, `style.css`) and core panels | S0 |
| `taskbot/slices/<name>/` | one vertical slice each. See [taskbot/slices/README.md](taskbot/slices/README.md) | that slice |
| `migrations/` | `NNNN_name.sql`, numbered ranges per slice | per range |
| `tests/` | core tests and `tests/fixtures` seed helpers | S0 |
| `conftest.py` | shared fixtures: `taskbot_env` (autouse), `client` | S0 |
