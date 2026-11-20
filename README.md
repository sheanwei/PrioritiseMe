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

## Layout

| Path | What | Owner |
|---|---|---|
| `taskbot/core/` | config, db, models, items, llm, scheduler, clock, app, routes | S0 (frozen: additive changes only) |
| `taskbot/static/` | dashboard shell (`index.html`, `app.js`, `style.css`) and core panels | S0 |
| `taskbot/slices/<name>/` | one vertical slice each. See [taskbot/slices/README.md](taskbot/slices/README.md) | that slice |
| `migrations/` | `NNNN_name.sql`, numbered ranges per slice | per range |
| `tests/` | core tests and `tests/fixtures` seed helpers | S0 |
| `conftest.py` | shared fixtures: `taskbot_env` (autouse), `client` | S0 |
