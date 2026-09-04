from __future__ import annotations

"""
Tests for the LLM provider abstraction (Phase 1 checkpoint).

Every HTTP client is faked at construction time (llm.openrouter.OpenAI /
llm.gemini.genai.Client are monkeypatched to return an in-memory fake), so
none of this needs network access or a real API key — only a dummy value in
OPENROUTER_API_KEY / GEMINI_API_KEY so the real client constructors don't
complain about a missing key.
"""

from types import SimpleNamespace

import httpx
import openai
import pytest

from llm import factory
from llm.base import LLMProvider, extract_json, sanitize_nullish
from llm.gemini import GeminiProvider
from llm.openrouter import OpenRouterProvider


@pytest.fixture(autouse=True)
def _reset_llm_factory_cache():
    factory.reset_cache()
    yield
    factory.reset_cache()


# ---------------------------------------------------------------------------
# sanitize_nullish
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw", ["null", "None", "NONE", "n/a", "N/A", "", "   ", "nan", "-", "--", "undefined"]
)
def test_sanitize_nullish_scalar(raw):
    assert sanitize_nullish(raw) is None


def test_sanitize_nullish_nested_dict_and_list():
    payload = {
        "theme": "AI trends",
        "special_day": "null",
        "tags": ["Data", "None", {"nested": "N/A", "keep": "Digital"}],
        "meta": {"trend_source": "none", "score": 4.2},
    }
    result = sanitize_nullish(payload)
    assert result["theme"] == "AI trends"
    assert result["special_day"] is None
    assert result["tags"][0] == "Data"
    assert result["tags"][1] is None
    assert result["tags"][2]["nested"] is None
    assert result["tags"][2]["keep"] == "Digital"
    assert result["meta"]["trend_source"] is None
    assert result["meta"]["score"] == 4.2


def test_sanitize_nullish_leaves_real_values_alone():
    assert sanitize_nullish("Wimbee") == "Wimbee"
    assert sanitize_nullish(0) == 0
    assert sanitize_nullish(False) is False
    assert sanitize_nullish(None) is None


# ---------------------------------------------------------------------------
# extract_json fallback (fenced / noisy responses)
# ---------------------------------------------------------------------------

def test_extract_json_recovers_fenced_response():
    raw = (
        "Here is the plan:\n```json\n"
        '{"month": "2026-08", "posts": [{"id": 1, "theme": "x"}]}\n'
        "```\nLet me know if you need changes."
    )
    assert extract_json(raw) == {"month": "2026-08", "posts": [{"id": 1, "theme": "x"}]}


def test_extract_json_picks_largest_balanced_span_ignoring_stray_braces():
    raw = 'note: {not json} then ```json\n{"a": 1, "b": [1, 2, {"c": 3}]}\n```'
    assert extract_json(raw) == {"a": 1, "b": [1, 2, {"c": 3}]}


def test_extract_json_returns_none_when_nothing_parses():
    assert extract_json("sorry, I can't help with that") is None


# ---------------------------------------------------------------------------
# Fakes for the OpenRouter (openai SDK) HTTP layer
# ---------------------------------------------------------------------------

class _FakeChoice:
    def __init__(self, content):
        self.message = SimpleNamespace(content=content)


class _FakeChatResponse:
    def __init__(self, content, model="fake-openrouter-model", prompt_tokens=10, completion_tokens=5):
        self.choices = [_FakeChoice(content)]
        self.model = model
        self.usage = SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)


class _FakeCompletions:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _FakeOpenAIClient:
    def __init__(self, responses):
        self.chat = SimpleNamespace(completions=_FakeCompletions(responses))


def _status_error(status_code: int) -> openai.APIStatusError:
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    response = httpx.Response(status_code, request=request)
    return openai.APIStatusError(f"status {status_code}", response=response, body=None)


def _make_openrouter(monkeypatch, responses):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr("llm.openrouter.time.sleep", lambda seconds: None)
    fake_client = _FakeOpenAIClient(responses)
    monkeypatch.setattr("llm.openrouter.OpenAI", lambda **kwargs: fake_client)
    return OpenRouterProvider(model="test-model"), fake_client


# ---------------------------------------------------------------------------
# Fakes for the Gemini (google-genai SDK) HTTP layer
# ---------------------------------------------------------------------------

class _FakeGenaiResponse:
    def __init__(self, text=None, prompt_feedback=None, candidates=None,
                 prompt_tokens=8, completion_tokens=3):
        self.text = text
        self.prompt_feedback = prompt_feedback
        self.candidates = candidates or []
        self.usage_metadata = SimpleNamespace(
            prompt_token_count=prompt_tokens, candidates_token_count=completion_tokens
        )


class _FakeModels:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _FakeGenaiClient:
    def __init__(self, responses):
        self.models = _FakeModels(responses)


def _make_gemini(monkeypatch, responses):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    fake_client = _FakeGenaiClient(responses)
    monkeypatch.setattr("llm.gemini.genai.Client", lambda **kwargs: fake_client)
    return GeminiProvider(model="test-model"), fake_client


# ---------------------------------------------------------------------------
# Both providers satisfy the same interface
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("provider_cls,maker", [
    (OpenRouterProvider, _make_openrouter),
    (GeminiProvider, _make_gemini),
])
def test_provider_satisfies_common_interface(monkeypatch, provider_cls, maker):
    assert issubclass(provider_cls, LLMProvider)
    provider, _client = maker(monkeypatch, [])
    assert callable(provider.complete)
    assert callable(provider.complete_json)
    assert callable(provider.healthcheck)


# ---------------------------------------------------------------------------
# OpenRouter behavior
# ---------------------------------------------------------------------------

def test_openrouter_complete_success(monkeypatch):
    provider, client = _make_openrouter(monkeypatch, [_FakeChatResponse("hello world")])
    response = provider.complete("say hi")
    assert response.ok
    assert response.text == "hello world"
    assert response.model == "fake-openrouter-model"
    assert response.prompt_tokens == 10


def test_openrouter_complete_includes_system_message(monkeypatch):
    provider, client = _make_openrouter(monkeypatch, [_FakeChatResponse("hi")])
    provider.complete("say hi", system="You are Wimbee's assistant")
    sent = client.chat.completions.calls[0]["messages"]
    assert sent[0] == {"role": "system", "content": "You are Wimbee's assistant"}


def test_openrouter_retries_on_429_then_succeeds(monkeypatch):
    provider, client = _make_openrouter(
        monkeypatch, [_status_error(429), _FakeChatResponse("ok after retry")]
    )
    response = provider.complete("hi")
    assert response.ok
    assert response.text == "ok after retry"
    assert len(client.chat.completions.calls) == 2


def test_openrouter_gives_up_on_non_retryable_status(monkeypatch):
    provider, client = _make_openrouter(monkeypatch, [_status_error(401)])
    response = provider.complete("hi")
    assert response.ok is False
    assert len(client.chat.completions.calls) == 1


def test_openrouter_complete_json_native_success(monkeypatch):
    provider, client = _make_openrouter(
        monkeypatch, [_FakeChatResponse('{"theme": "AI", "special_day": "null"}')]
    )
    parsed, response = provider.complete_json("brief me")
    assert response.ok
    assert parsed == {"theme": "AI", "special_day": None}
    assert client.chat.completions.calls[0]["response_format"] == {"type": "json_object"}


def test_openrouter_complete_json_recovers_fenced_response(monkeypatch):
    fenced = '```json\n{"a": 1, "b": "None"}\n```'
    provider, client = _make_openrouter(monkeypatch, [_FakeChatResponse(fenced)])
    parsed, response = provider.complete_json("brief me")
    assert response.ok
    assert parsed == {"a": 1, "b": None}


def test_openrouter_complete_json_retries_once_then_succeeds(monkeypatch):
    provider, client = _make_openrouter(monkeypatch, [
        _FakeChatResponse("not json at all, sorry"),
        _FakeChatResponse('{"ok": true}'),
    ])
    parsed, response = provider.complete_json("brief me")
    assert parsed == {"ok": True}
    assert response.ok
    assert len(client.chat.completions.calls) == 2


def test_openrouter_complete_json_gives_up_after_retry_fails(monkeypatch):
    provider, client = _make_openrouter(monkeypatch, [
        _FakeChatResponse("still not json"),
        _FakeChatResponse("still not json either"),
    ])
    parsed, response = provider.complete_json("brief me")
    assert parsed is None
    assert response.ok is False
    assert "parse" in response.error.lower()
    assert len(client.chat.completions.calls) == 2


# ---------------------------------------------------------------------------
# Gemini behavior
# ---------------------------------------------------------------------------

def test_gemini_complete_success(monkeypatch):
    provider, client = _make_gemini(monkeypatch, [_FakeGenaiResponse(text="hello from gemini")])
    response = provider.complete("say hi")
    assert response.ok
    assert response.text == "hello from gemini"
    assert response.prompt_tokens == 8


def test_gemini_blocked_response_is_not_ok(monkeypatch):
    feedback = SimpleNamespace(block_reason="SAFETY")
    provider, client = _make_gemini(
        monkeypatch, [_FakeGenaiResponse(text=None, prompt_feedback=feedback)]
    )
    response = provider.complete("say something risky")
    assert response.ok is False
    assert "SAFETY" in response.error


def test_content_chat_retries_gemini_503_with_exponential_backoff(monkeypatch):
    from agents import content_agent
    responses = [SimpleNamespace(ok=False, error="503 UNAVAILABLE"),
                 SimpleNamespace(ok=False, error="UNAVAILABLE"),
                 SimpleNamespace(ok=True, text="recovered")]
    calls = []

    class GeminiProvider:
        def complete(self, prompt, temperature=0.7):
            calls.append(prompt)
            return responses.pop(0)

    monkeypatch.setattr(content_agent, "get_llm", lambda role=None: GeminiProvider())
    monkeypatch.setattr(content_agent.time, "sleep", lambda seconds: calls.append(seconds))

    assert content_agent.chat("hello") == "recovered"
    assert calls == ["hello", 2, "hello", 4, "hello"]


def test_content_generates_image_prompt_for_carrousel(monkeypatch):
    from agents import content_agent

    monkeypatch.setattr(content_agent, "chat", lambda prompt, temperature=0.7: "Post\n---HASHTAGS---\n#Data")
    monkeypatch.setattr(content_agent, "_generate_image_prompt", lambda content: "visual prompt")

    result = content_agent.run_content({"theme": "AI", "format": "carrousel"})

    assert result["image_prompt"] == "visual prompt"


def test_gemini_complete_json_native_success(monkeypatch):
    provider, client = _make_gemini(
        monkeypatch, [_FakeGenaiResponse(text='{"theme": "AI", "special_day": "N/A"}')]
    )
    parsed, response = provider.complete_json("brief me")
    assert response.ok
    assert parsed == {"theme": "AI", "special_day": None}
    sent_config = client.models.calls[0]["config"]
    assert sent_config.response_mime_type == "application/json"


def test_gemini_complete_json_recovers_fenced_response(monkeypatch):
    fenced = '```json\n{"a": 1, "b": "null"}\n```'
    provider, client = _make_gemini(monkeypatch, [_FakeGenaiResponse(text=fenced)])
    parsed, response = provider.complete_json("brief me")
    assert response.ok
    assert parsed == {"a": 1, "b": None}


# ---------------------------------------------------------------------------
# Factory: env-driven provider/model selection, caching
# ---------------------------------------------------------------------------

def test_factory_switches_provider_via_env_with_no_code_change(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    monkeypatch.setenv("WIMBEE_LLM_PROVIDER", "openrouter")
    assert isinstance(factory.get_llm(), OpenRouterProvider)

    factory.reset_cache()
    monkeypatch.setenv("WIMBEE_LLM_PROVIDER", "gemini")
    assert isinstance(factory.get_llm(), GeminiProvider)


def test_factory_rejects_unknown_provider(monkeypatch):
    monkeypatch.setenv("WIMBEE_LLM_PROVIDER", "not-a-real-provider")
    with pytest.raises(ValueError):
        factory.get_llm()


def test_factory_per_role_model_override(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("WIMBEE_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("WIMBEE_LLM_MODEL", "generic-model")
    monkeypatch.setenv("WIMBEE_LLM_MODEL_PLANNING", "strong-model")

    planning_provider = factory.get_llm(role="planning")
    generic_provider = factory.get_llm(role="content")

    assert planning_provider.model == "strong-model"
    assert generic_provider.model == "generic-model"
    assert planning_provider is not generic_provider


def test_factory_caches_instances_per_provider_and_model(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("WIMBEE_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("WIMBEE_LLM_MODEL", "same-model")

    first = factory.get_llm()
    second = factory.get_llm()
    assert first is second
