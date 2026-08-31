from __future__ import annotations

"""
Provider-agnostic LLM interface shared by every backend in this package.

Why this module exists: agents used to call OpenRouter's SDK directly, so
switching models meant editing agent code, and each provider disagreed on how
to signal failure. It also centralizes the fix for a live bug: models
sometimes emit the literal string "null" (or "None", "N/A", ...) for an empty
JSON field, and because a non-empty string is truthy in Python, downstream
`if value:` checks took the wrong branch and silently produced bad posts.
sanitize_nullish() lives here — not in a provider file — because callers
outside llm/ (plan expansion, backfills) need to run parsed JSON through the
same fixer.
"""

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace


@dataclass
class LLMResponse:
    ok: bool
    text: str = ""
    error: str | None = None
    model: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class LLMError(Exception):
    """Raised internally between a provider's HTTP call and its LLMResponse wrapper.

    Never crosses the complete()/complete_json()/healthcheck() boundary — those
    always return an LLMResponse with ok=False instead.
    """


NULLISH_STRINGS = {
    "null", "none", "nil", "n/a", "na", "nan", "undefined", "-", "--", "",
}


def sanitize_nullish(obj):
    """Recursively replace placeholder-for-null strings with real None.

    Applies at every depth, inside both dicts and lists, matching on the
    lowercased/stripped string value so "Null", " N/A ", "NONE" all normalize.
    """
    if isinstance(obj, dict):
        return {key: sanitize_nullish(value) for key, value in obj.items()}
    if isinstance(obj, list):
        return [sanitize_nullish(value) for value in obj]
    if isinstance(obj, str) and obj.strip().lower() in NULLISH_STRINGS:
        return None
    return obj


def _largest_balanced_span(text: str, open_ch: str, close_ch: str) -> str | None:
    """Scan every possible start position and return the longest balanced span.

    Brace/bracket depth tracking ignores braces that appear inside JSON string
    literals so a stray "{" in prose text can't desync the count.
    """
    best = None
    for start, ch in enumerate(text):
        if ch != open_ch:
            continue
        depth = 0
        in_string = False
        escape = False
        for pos in range(start, len(text)):
            c = text[pos]
            if in_string:
                if escape:
                    escape = False
                elif c == "\\":
                    escape = True
                elif c == '"':
                    in_string = False
                continue
            if c == '"':
                in_string = True
            elif c == open_ch:
                depth += 1
            elif c == close_ch:
                depth -= 1
                if depth == 0:
                    candidate = text[start:pos + 1]
                    if best is None or len(candidate) > len(best):
                        best = candidate
                    break
    return best


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str):
    """Fallback JSON extraction for providers/models that ignore JSON-mode.

    Strips a ```json fence if present, then tries the largest balanced
    {...} span and the largest balanced [...] span (longest candidate first)
    and returns whichever actually parses.
    """
    stripped = text.strip()
    fence_match = _FENCE_RE.search(stripped)
    if fence_match:
        stripped = fence_match.group(1).strip()

    candidates = [
        span
        for span in (
            _largest_balanced_span(stripped, "{", "}"),
            _largest_balanced_span(stripped, "[", "]"),
        )
        if span
    ]
    candidates.sort(key=len, reverse=True)

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


class LLMProvider(ABC):
    """Common interface every backend (OpenRouter, Gemini, ...) must satisfy."""

    @abstractmethod
    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> LLMResponse:
        ...

    @abstractmethod
    def _complete_raw_json(
        self,
        prompt: str,
        *,
        system: str | None,
        schema: dict | None,
        temperature: float,
        max_tokens: int,
    ) -> LLMResponse:
        """Provider-specific call requesting native JSON mode.

        Returns the raw LLMResponse — response.text is not guaranteed to be
        valid JSON (native JSON mode support varies by model), which is why
        complete_json() below still runs it through extract_json().
        """

    @abstractmethod
    def healthcheck(self) -> LLMResponse:
        ...

    def complete_json(
        self,
        prompt: str,
        *,
        system: str | None = None,
        schema: dict | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> tuple[dict | list | None, LLMResponse]:
        response = self._complete_raw_json(
            prompt, system=system, schema=schema,
            temperature=temperature, max_tokens=max_tokens,
        )
        if not response.ok:
            return None, response

        parsed = _parse_json_text(response.text)
        if parsed is not None:
            return sanitize_nullish(parsed), response

        corrective_prompt = (
            f"{prompt}\n\n"
            "Your previous reply could not be parsed as JSON. Reply with ONLY "
            "valid JSON — no commentary, no markdown fences.\n\n"
            f"Previous reply:\n{response.text}"
        )
        retry_response = self._complete_raw_json(
            corrective_prompt, system=system, schema=schema,
            temperature=temperature, max_tokens=max_tokens,
        )
        if retry_response.ok:
            parsed = _parse_json_text(retry_response.text)
            if parsed is not None:
                return sanitize_nullish(parsed), retry_response
            retry_response = replace(
                retry_response, ok=False,
                error="Could not parse JSON from response after one retry",
            )
        return None, retry_response


def _parse_json_text(text: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return extract_json(text)
