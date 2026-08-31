from __future__ import annotations

"""
Gemini backend for the LLM provider abstraction, via the current `google-genai`
SDK (NOT the deprecated `google-generativeai` package).

Why this module exists: Gemini can return an empty/blocked candidate (safety
filter, recitation check) with no exception raised at all — the SDK call
succeeds, `response.text` is just empty or None. Treating that as an empty
string instead of a real error would silently produce a blank post further
down the pipeline, so _call() below inspects prompt_feedback/finish_reason
whenever text is missing and turns it into ok=False with a concrete reason.
"""

import logging
import os

from google import genai
from google.genai import types

from .base import LLMProvider, LLMResponse

log = logging.getLogger(__name__)

# Verified against https://ai.google.dev/gemini-api/docs/latest-model and
# https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-3-6-flash-3-5-flash-lite-3-5-flash-cyber/
# on 2026-07-27. "gemini-flash-latest" is a Google-maintained alias that always
# resolves to the current recommended Flash model (gemini-3.6-flash as of that
# date; gemini-2.0-flash/-lite were shut down 2026-06-01). Pin GEMINI_MODEL to
# a dated model name instead of the alias if you need output to stay fixed
# across Google's silent model upgrades.
DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest")


class GeminiProvider(LLMProvider):
    def __init__(self, model: str | None = None, api_key: str | None = None):
        self.model = model or DEFAULT_MODEL
        self._client = genai.Client(
            api_key=api_key if api_key is not None else os.getenv("GEMINI_API_KEY")
        )

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> LLMResponse:
        config = types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
            system_instruction=system,
        )
        return self._call(prompt, config)

    def _complete_raw_json(
        self,
        prompt: str,
        *,
        system: str | None,
        schema: dict | None,
        temperature: float,
        max_tokens: int,
    ) -> LLMResponse:
        config_kwargs = dict(
            temperature=temperature,
            max_output_tokens=max_tokens,
            system_instruction=system,
            response_mime_type="application/json",
        )
        if schema:
            config_kwargs["response_schema"] = schema
        config = types.GenerateContentConfig(**config_kwargs)
        return self._call(prompt, config)

    def healthcheck(self) -> LLMResponse:
        return self.complete("Reply with exactly: ok", max_tokens=5)

    def _call(self, prompt: str, config: "types.GenerateContentConfig") -> LLMResponse:
        try:
            response = self._client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=config,
            )
        except Exception as exc:
            log.error("Gemini request failed for model %s: %s", self.model, exc)
            return LLMResponse(ok=False, error=str(exc), model=self.model)

        text = getattr(response, "text", None)
        if not text:
            feedback = getattr(response, "prompt_feedback", None)
            block_reason = getattr(feedback, "block_reason", None) if feedback else None
            candidates = getattr(response, "candidates", None) or []
            finish_reason = candidates[0].finish_reason if candidates else None
            error = (
                f"Gemini returned no text (block_reason={block_reason}, "
                f"finish_reason={finish_reason})"
            )
            log.error(error)
            return LLMResponse(ok=False, error=error, model=self.model)

        usage = getattr(response, "usage_metadata", None)
        return LLMResponse(
            ok=True,
            text=text,
            model=self.model,
            prompt_tokens=getattr(usage, "prompt_token_count", None),
            completion_tokens=getattr(usage, "candidates_token_count", None),
        )
