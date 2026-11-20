"""LLM client: one function, ``complete_json``, returning schema-validated JSON.

Real mode talks to OpenCode Zen's OpenAI-compatible endpoint. Fake mode
(``TASKBOT_LLM=fake``) needs no key or network: it returns ``schema["examples"][0]``
if present, otherwise a minimal value synthesised from the schema.

Smoke-test the real endpoint with:  python -m taskbot.core.llm
"""

from __future__ import annotations

import copy
import json
import logging
import re
import sys
from collections.abc import Callable
from typing import Any

import jsonschema

from .config import settings

log = logging.getLogger("taskbot.llm")

# Tests may replace this with a factory returning a stub OpenAI-like client.
client_factory: Callable[[], Any] | None = None


class LLMError(RuntimeError):
    """The LLM could not produce valid output (after one retry) or is misconfigured."""


def complete_json(
    system: str,
    user: str,
    schema: dict[str, Any],
    model: str | None = None,
) -> dict[str, Any]:
    s = settings()
    if s.llm_mode == "fake":
        result = fake_from_schema(schema)
        _validate(result, schema)
        return result

    model = model or s.opencode_model
    if not model:
        raise LLMError("OPENCODE_MODEL is not set")
    client = (client_factory or _default_client)()

    messages: list[dict[str, str]] = [
        {"role": "system", "content": system + _schema_instruction(schema)},
        {"role": "user", "content": user},
    ]
    last_error: Exception | None = None
    for _attempt in range(2):
        try:
            response = client.chat.completions.create(
                model=model, messages=messages, temperature=0.2
            )
            content = response.choices[0].message.content or ""
        except Exception as exc:  # network / API error: retry once
            last_error = exc
            continue
        if s.debug:
            log.info("LLM request=%s response=%s", messages, content)
        try:
            data = _parse_json(content)
            _validate(data, schema)
            return data
        except (ValueError, jsonschema.ValidationError) as exc:
            last_error = exc
            reason = getattr(exc, "message", str(exc))[:500]
            messages += [
                {"role": "assistant", "content": content},
                {
                    "role": "user",
                    "content": f"That response was invalid: {reason}. "
                    "Reply again with only the corrected JSON object.",
                },
            ]
    raise LLMError(f"LLM call failed after retry: {last_error}") from last_error


def _default_client() -> Any:
    s = settings()
    if not s.opencode_api_key:
        raise LLMError("OPENCODE_API_KEY is not set (or use TASKBOT_LLM=fake)")
    from openai import OpenAI

    return OpenAI(
        api_key=s.opencode_api_key,
        base_url=s.opencode_base_url,
        timeout=60,
        max_retries=0,
    )


def _strip_examples(schema: Any) -> Any:
    if isinstance(schema, dict):
        return {k: _strip_examples(v) for k, v in schema.items() if k != "examples"}
    if isinstance(schema, list):
        return [_strip_examples(v) for v in schema]
    return schema


def _schema_instruction(schema: dict[str, Any]) -> str:
    return (
        "\n\nRespond with a single JSON object only — no prose, no code fences. "
        "It must validate against this JSON Schema:\n"
        + json.dumps(_strip_examples(schema))
    )


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def _parse_json(content: str) -> dict[str, Any]:
    text = _FENCE_RE.sub("", content.strip()).strip()
    if not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object found in response")
        text = text[start : end + 1]
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("response JSON is not an object")
    return data


def _validate(data: Any, schema: dict[str, Any]) -> None:
    jsonschema.validate(data, schema)


def fake_from_schema(schema: dict[str, Any]) -> Any:
    """Deterministic value for a JSON schema (prefers ``examples[0]``)."""
    if schema.get("examples"):
        return copy.deepcopy(schema["examples"][0])
    if "const" in schema:
        return schema["const"]
    if schema.get("enum"):
        return schema["enum"][0]
    for key in ("anyOf", "oneOf"):
        if schema.get(key):
            return fake_from_schema(schema[key][0])

    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), "null")
    if kind == "object" or (kind is None and "properties" in schema):
        props = schema.get("properties", {})
        return {name: fake_from_schema(sub) for name, sub in props.items()}
    if kind == "array":
        count = int(schema.get("minItems", 0))
        item_schema = schema.get("items", {"type": "string"})
        return [fake_from_schema(item_schema) for _ in range(count)]
    if kind == "string":
        fmt = schema.get("format")
        if fmt == "date":
            return "2026-01-01"
        if fmt == "date-time":
            return "2026-01-01T00:00:00+00:00"
        return "x" * max(int(schema.get("minLength", 4)), 1) if "minLength" in schema else "fake"
    if kind == "integer":
        return int(schema.get("minimum", 0))
    if kind == "number":
        return schema.get("minimum", 0)
    if kind == "boolean":
        return False
    return None


def _smoke() -> int:
    s = settings()
    print(f"mode={s.llm_mode} base_url={s.opencode_base_url} model={s.opencode_model}")
    schema = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}, "model_says": {"type": "string"}},
        "required": ["ok", "model_says"],
    }
    try:
        result = complete_json(
            "You are a connectivity check.",
            'Return {"ok": true, "model_says": "<a five-word greeting>"}.',
            schema,
        )
    except LLMError as exc:
        print(f"FAILED: {exc}")
        return 1
    print(f"OK: {result}")
    return 0


if __name__ == "__main__":
    sys.exit(_smoke())
