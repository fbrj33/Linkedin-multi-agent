"""Verify the configured official Groq connection."""

from dotenv import load_dotenv

load_dotenv(dotenv_path=".env")

from llm import get_llm

provider = get_llm(role="planning")
response = provider.complete("Reply with exactly: GROQ CONNECTION OK", max_tokens=20)

if not response.ok:
    raise SystemExit(f"Groq connection failed: {response.error}")

print(f"Provider: {provider.__class__.__name__}")
print(f"Model: {response.model or provider.model}")
print(f"Response: {response.text.strip()}")
