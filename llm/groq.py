from __future__ import annotations

"""Groq provider using Groq's official OpenAI-compatible API."""

import logging
import os
import random
import time

from openai import APIError, APIStatusError, OpenAI

from .base import LLMProvider, LLMResponse

log = logging.getLogger(__name__)

DEFAULT_MODEL = os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b")
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_RETRY_DELAYS = (1.0, 4.0, 12.0)


class GroqProvider(LLMProvider):
    def __init__(self, model: str | None = None, api_key: str | None = None):
        self.model = model or DEFAULT_MODEL
        self._client = OpenAI(
            base_url="https://api.groq.com/openai/v1",
            api_key=api_key if api_key is not None else os.getenv("GROQ_API_KEY"),
        )

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> LLMResponse:
        return self._call(
            self._build_messages(prompt, system),
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def _complete_raw_json(
        self,
        prompt: str,
        *,
        system: str | None,
        schema: dict | None,
        temperature: float,
        max_tokens: int,
    ) -> LLMResponse:
        prompt_with_schema = prompt
        if schema:
            prompt_with_schema = f"{prompt}\n\nRespond matching this JSON schema:\n{schema}"
        return self._call(
            self._build_messages(prompt_with_schema, system),
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )

    def healthcheck(self) -> LLMResponse:
        return self.complete("Reply with exactly: GROQ CONNECTION OK", max_tokens=20)

    @staticmethod
    def _build_messages(prompt: str, system: str | None) -> list[dict]:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return messages

    def _call(
        self,
        messages: list[dict],
        *,
        temperature: float,
        max_tokens: int,
        response_format: dict | None = None,
    ) -> LLMResponse:
        kwargs = {
            "model": self.model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if response_format:
            kwargs["response_format"] = response_format

        last_error: Exception | None = None
        for attempt, delay in enumerate((0.0,) + _RETRY_DELAYS):
            if delay:
                time.sleep(delay + random.uniform(0, delay * 0.25))
            try:
                response = self._client.chat.completions.create(**kwargs)
                choice = response.choices[0]
                usage = getattr(response, "usage", None)
                return LLMResponse(
                    ok=True,
                    text=choice.message.content or "",
                    model=response.model,
                    prompt_tokens=getattr(usage, "prompt_tokens", None),
                    completion_tokens=getattr(usage, "completion_tokens", None),
                )
            except APIStatusError as exc:
                last_error = exc
                if exc.status_code not in _RETRYABLE_STATUS or attempt == len(_RETRY_DELAYS):
                    break
                log.warning("Groq request returned HTTP %s; retrying", exc.status_code)
            except APIError as exc:
                last_error = exc
                break

        log.error("Groq request failed for model %s: %s", self.model, last_error)
        return LLMResponse(ok=False, error=str(last_error), model=self.model)
