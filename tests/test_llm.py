from types import SimpleNamespace

import jsonschema
import pytest

from taskbot.core import llm

SCHEMA = {
    "type": "object",
    "properties": {
        "request": {"type": "string"},
        "deadline_kind": {"type": "string", "enum": ["real", "felt", "none"]},
        "blocked_count": {"type": "integer", "minimum": 0},
        "open_questions": {"type": "array", "items": {"type": "string"}},
        "deadline": {"type": ["string", "null"], "format": "date"},
    },
    "required": ["request", "deadline_kind", "blocked_count", "open_questions", "deadline"],
    "additionalProperties": False,
}


class StubClient:
    """Mimics openai.OpenAI().chat.completions.create, returning queued replies."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=reply))])


@pytest.fixture
def real_mode(monkeypatch):
    monkeypatch.setenv("TASKBOT_LLM", "real")
    monkeypatch.setenv("OPENCODE_MODEL", "test-model")
    monkeypatch.setenv("OPENCODE_API_KEY", "test-key")


def use_stub(replies):
    stub = StubClient(replies)
    llm.client_factory = lambda: stub
    return stub


# --- fake mode (AC3) ---

def test_fake_mode_synthesises_schema_valid_data():
    result = llm.complete_json("sys", "user", SCHEMA)
    jsonschema.validate(result, SCHEMA)
    assert result["deadline_kind"] == "real"


def test_fake_mode_prefers_examples():
    schema = {**SCHEMA, "examples": [{"request": "Fix login", "deadline_kind": "felt",
                                      "blocked_count": 2, "open_questions": [], "deadline": None}]}
    assert llm.complete_json("s", "u", schema)["request"] == "Fix login"


def test_fake_mode_rejects_invalid_examples():
    schema = {**SCHEMA, "examples": [{"request": 1}]}
    with pytest.raises(jsonschema.ValidationError):
        llm.complete_json("s", "u", schema)


# --- real mode with a stub client ---

GOOD = ('{"request": "Fix login", "deadline_kind": "real", "blocked_count": 3, '
        '"open_questions": ["Which env?"], "deadline": "2026-11-25"}')


def test_real_mode_returns_validated_json(real_mode):
    stub = use_stub([GOOD])
    assert llm.complete_json("sys", "user", SCHEMA)["blocked_count"] == 3
    call = stub.calls[0]
    assert call["model"] == "test-model"
    assert "JSON Schema" in call["messages"][0]["content"]


def test_real_mode_accepts_code_fences_and_prose(real_mode):
    use_stub([f"Sure! Here you go:\n```json\n{GOOD}\n```"])
    assert llm.complete_json("s", "u", SCHEMA)["request"] == "Fix login"


def test_real_mode_retries_once_on_invalid_output(real_mode):
    stub = use_stub(['{"request": "x"}', GOOD])
    assert llm.complete_json("s", "u", SCHEMA)["deadline"] == "2026-11-25"
    retry_messages = stub.calls[1]["messages"]
    assert retry_messages[-1]["role"] == "user"
    assert "invalid" in retry_messages[-1]["content"]


def test_real_mode_retries_once_on_api_error(real_mode):
    use_stub([ConnectionError("network down"), GOOD])
    assert llm.complete_json("s", "u", SCHEMA)["blocked_count"] == 3


def test_real_mode_raises_llm_error_after_two_failures(real_mode):
    use_stub(["not json", "still not json"])
    with pytest.raises(llm.LLMError):
        llm.complete_json("s", "u", SCHEMA)


def test_real_mode_without_key_or_model_raises(monkeypatch):
    monkeypatch.setenv("TASKBOT_LLM", "real")
    monkeypatch.setenv("OPENCODE_MODEL", "m")
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    monkeypatch.setenv("OPENCODE_API_KEY", "")
    with pytest.raises(llm.LLMError, match="OPENCODE_API_KEY"):
        llm.complete_json("s", "u", SCHEMA)
    monkeypatch.setenv("OPENCODE_MODEL", "")
    with pytest.raises(llm.LLMError, match="OPENCODE_MODEL"):
        llm.complete_json("s", "u", SCHEMA)


def test_examples_are_not_sent_to_the_model(real_mode):
    stub = use_stub([GOOD])
    schema = {**SCHEMA, "examples": [{"request": "SECRET_EXAMPLE"}]}
    llm.complete_json("s", "u", schema)
    assert "SECRET_EXAMPLE" not in stub.calls[0]["messages"][0]["content"]


# --- OPENCODE_EXTRA_BODY and list-shaped content ---

def test_extra_body_is_sent_only_when_configured(real_mode, monkeypatch):
    monkeypatch.delenv("OPENCODE_EXTRA_BODY", raising=False)  # a local .env may set it
    stub = use_stub([GOOD])
    llm.complete_json("s", "u", SCHEMA)
    assert "extra_body" not in stub.calls[0]

    monkeypatch.setenv("OPENCODE_EXTRA_BODY", '{"thinking": {"type": "disabled"}}')
    stub = use_stub([GOOD])
    llm.complete_json("s", "u", SCHEMA)
    assert stub.calls[0]["extra_body"] == {"thinking": {"type": "disabled"}}


def test_invalid_extra_body_is_rejected(monkeypatch):
    from taskbot.core.config import settings

    monkeypatch.setenv("OPENCODE_EXTRA_BODY", "not json")
    with pytest.raises(ValueError, match="OPENCODE_EXTRA_BODY"):
        settings()
    monkeypatch.setenv("OPENCODE_EXTRA_BODY", "[1, 2]")
    with pytest.raises(ValueError, match="JSON object"):
        settings()


def test_list_shaped_content_is_joined(real_mode):
    # Some models (seen with mistral-large-4) return content as a list of parts.
    half = len(GOOD) // 2
    use_stub([[{"type": "text", "text": GOOD[:half]}, {"type": "text", "text": GOOD[half:]}]])
    assert llm.complete_json("s", "u", SCHEMA)["blocked_count"] == 3
