from __future__ import annotations

"""
OpenRouter backend for the LLM provider abstraction.

Why this module exists: OpenRouter fronts many different underlying models
(see the old MODELS fallback list in config/LLM.py), and 429s/5xxs from any
one of them are common enough that a request without backoff would starve a
whole plan- or post-generation run. The retry loop below is what used to be
"try the next free model" — now it's "retry the same model with backoff",
since the provider/model is chosen once via WIMBEE_LLM_MODEL instead of
walked through a hardcoded list.
"""

import logging
import os
import random
import time

from openai import APIError, APIStatusError, OpenAI

from .base import LLMProvider, LLMResponse

log = logging.getLogger(__name__)

DEFAULT_MODEL = os.getenv("OPENROUTER_MODEL", "openrouter/auto")
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
# Delay before each retry (not before the first attempt): ~1s, 4s, 12s, each
# jittered up to +25%. That's 3 retries on top of the initial attempt, per the
# "3 attempts, roughly 1s/4s/12s" backoff spec.
_RETRY_DELAYS = (1.0, 4.0, 12.0)


class OpenRouterProvider(LLMProvider):
    def __init__(self, model: str | None = None, api_key: str | None = None):
        self.model = model or DEFAULT_MODEL
        self._client = OpenAI(
            base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
            api_key=api_key if api_key is not None else os.getenv("OPENROUTER_API_KEY"),
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
        # response_format={"type": "json_object"} support varies by whichever
        # model OpenRouter routes to, so this is a best-effort hint — the
        # fence-stripping fallback in base.complete_json() is load-bearing,
        # not optional. Arbitrary JSON `schema` isn't reliably honored across
        # OpenRouter's model zoo, so it's folded into the prompt instead.
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
        return self.complete("Reply with exactly: ok", max_tokens=5)

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
        kwargs = dict(
            model=self.model,
            temperature=temperature,
            max_tokens=max_tokens,
            messages=messages,
        )
        if response_format:
            kwargs["response_format"] = response_format

        last_error: Exception | None = None
        delays = (0.0,) + _RETRY_DELAYS
        for attempt, delay in enumerate(delays):
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
                is_last_attempt = attempt == len(delays) - 1
                if exc.status_code not in _RETRYABLE_STATUS or is_last_attempt:
                    break
                log.warning(
                    "OpenRouter %s on attempt %d for model %s, retrying",
                    exc.status_code, attempt + 1, self.model,
                )
            except APIError as exc:
                last_error = exc
                break

        log.error("OpenRouter request failed for model %s: %s", self.model, last_error)
        return LLMResponse(ok=False, error=str(last_error), model=self.model)
