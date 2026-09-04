from types import SimpleNamespace

import httpx
import openai

from llm.xai import XAIProvider


class FakeCompletions:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeClient:
    def __init__(self, responses):
        self.chat = SimpleNamespace(completions=FakeCompletions(responses))


def fake_response(text):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
        model="grok-4.6",
        usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
    )


def status_error(status_code):
    request = httpx.Request("POST", "https://api.x.ai/v1/chat/completions")
    response = httpx.Response(status_code, request=request)
    return openai.APIStatusError("status", response=response, body=None)


def test_xai_provider_uses_official_endpoint_and_returns_text(monkeypatch):
    client = FakeClient([fake_response("GROK CONNECTION OK")])
    monkeypatch.setattr("llm.xai.OpenAI", lambda **kwargs: (client, kwargs)[0])
    provider = XAIProvider(model="grok-4.6", api_key="test-key")

    result = provider.complete("Reply with exactly: GROK CONNECTION OK", max_tokens=20)

    assert result.ok
    assert result.text == "GROK CONNECTION OK"
    assert client.chat.completions.calls[0]["model"] == "grok-4.6"


def test_xai_provider_retries_rate_limit(monkeypatch):
    client = FakeClient([status_error(429), fake_response("ok")])
    monkeypatch.setattr("llm.xai.OpenAI", lambda **kwargs: client)
    monkeypatch.setattr("llm.xai.time.sleep", lambda seconds: None)
    monkeypatch.setattr("llm.xai.random.uniform", lambda start, end: 0)
    provider = XAIProvider(model="grok-4.6", api_key="test-key")

    result = provider.complete("hello")

    assert result.ok
    assert len(client.chat.completions.calls) == 2
