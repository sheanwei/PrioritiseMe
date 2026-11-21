"""FastAPI application: core API, slice discovery, dashboard shell.

Slice contract (see taskbot/slices/README.md): a folder ``taskbot/slices/<name>/``
with a ``router.py`` exposing ``router`` (an ``APIRouter``) and optionally
``startup()``. Its API is mounted at ``/api/<name>``, its ``static/`` folder at
``/slices/<name>/``, and its ``static/panel.json`` is picked up by the shell.
Folders starting with ``_`` or ``.`` are ignored.
"""

from __future__ import annotations

import importlib
import json
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db, scheduler
from .config import settings
from .routes import router as core_router

PACKAGE_DIR = Path(__file__).resolve().parents[1]
STATIC_DIR = PACKAGE_DIR / "static"
SLICES_DIR = PACKAGE_DIR / "slices"
SLICES_PACKAGE = "taskbot.slices"

log = logging.getLogger("taskbot.app")


@dataclass
class SliceInfo:
    name: str
    router: APIRouter
    startup: Callable[[], None] | None
    static_dir: Path

    def panels(self) -> list[dict[str, Any]]:
        manifest = self.static_dir / "panel.json"
        if not manifest.exists():
            return []
        raw = json.loads(manifest.read_text(encoding="utf-8"))
        entries = raw if isinstance(raw, list) else [raw]
        panels = []
        for i, entry in enumerate(entries):
            module = entry["module"]
            if not module.startswith("/"):
                module = f"/slices/{self.name}/{module}"
            panels.append(
                {
                    "id": entry.get("id", self.name if len(entries) == 1 else f"{self.name}-{i}"),
                    "title": entry.get("title", self.name),
                    "module": module,
                    "order": entry.get("order", 100),
                    "region": entry.get("region"),
                    "slice": self.name,
                    # Optional: a list-footer section that starts expanded.
                    **({"open": bool(entry["open"])} if "open" in entry else {}),
                }
            )
        return panels


def discover_slices(slices_dir: Path = SLICES_DIR, package: str = SLICES_PACKAGE) -> list[SliceInfo]:
    found: list[SliceInfo] = []
    for folder in sorted(p for p in slices_dir.iterdir() if p.is_dir()):
        if folder.name.startswith(("_", ".")) or not (folder / "router.py").exists():
            continue
        module = importlib.import_module(f"{package}.{folder.name}.router")
        router = getattr(module, "router", None)
        if not isinstance(router, APIRouter):
            raise TypeError(f"{module.__name__} must define `router = APIRouter()`")
        found.append(
            SliceInfo(
                name=folder.name,
                router=router,
                startup=getattr(module, "startup", None),
                static_dir=folder / "static",
            )
        )
    return found


#: Shell regions a panel manifest may name (see PRD §4.7). Anything else, or a
#: missing ``region``, mounts in the "More" fallback section of the Triage tab.
REGIONS = ("status", "list-footer", "detail", "mail", "review", "hidden")


def core_panels() -> list[dict[str, Any]]:
    raw = json.loads((STATIC_DIR / "panels.json").read_text(encoding="utf-8"))
    return [{"order": 100, **p, "region": p.get("region")} for p in raw]


def create_app(slices_dir: Path = SLICES_DIR, slices_package: str = SLICES_PACKAGE) -> FastAPI:
    slices = discover_slices(slices_dir, slices_package)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db.migrate()
        for s in slices:
            if s.startup is not None:
                s.startup()
        sched = scheduler.Scheduler()
        if settings().scheduler_enabled:
            sched.start()
        try:
            yield
        finally:
            await sched.stop()

    app = FastAPI(title="TaskBot", lifespan=lifespan)
    app.state.slices = slices
    app.include_router(core_router, prefix="/api/core")
    for s in slices:
        app.include_router(s.router, prefix=f"/api/{s.name}", tags=[s.name])
        if s.static_dir.is_dir():
            app.mount(f"/slices/{s.name}", StaticFiles(directory=s.static_dir), name=f"slice-{s.name}")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
