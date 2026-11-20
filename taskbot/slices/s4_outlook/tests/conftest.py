"""S4 test helpers. The root conftest already gives a fresh DB, fake LLM/mail, scheduler off."""

from __future__ import annotations

import json
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest

from taskbot.core import llm
from taskbot.slices.s4_outlook import service, signin


@pytest.fixture(autouse=True)
def _reset_s4_state():
    service.reset_status()
    signin.reset()
    yield
    service.reset_status()
    signin.reset()


class StubLLM:
    """OpenAI-shaped stub: ``decide(user_prompt) -> classification``. Records prompts."""

    def __init__(self, decide: Callable[[str], str]) -> None:
        self.decide = decide
        self.calls: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, *, model: str, messages: list[dict[str, str]], temperature: float) -> Any:
        self.calls.append({"model": model, "messages": messages})
        label = self.decide(messages[-1]["content"])
        content = json.dumps({"classification": label, "reason": "stub"})
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


@pytest.fixture
def stub_llm(monkeypatch):
    """Switch to the real LLM code path with a network-free stub client."""

    def install(decide: Callable[[str], str]) -> StubLLM:
        stub = StubLLM(decide)
        monkeypatch.setenv("TASKBOT_LLM", "real")
        monkeypatch.setenv("OPENCODE_MODEL", "main-model")
        monkeypatch.setenv("OPENCODE_MODEL_CHEAP", "cheap-model")
        monkeypatch.setattr(llm, "client_factory", lambda: stub)
        return stub

    return install
