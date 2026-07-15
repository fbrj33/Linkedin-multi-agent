from openai import OpenAI
import os
import time
from dotenv import load_dotenv

load_dotenv()

client = OpenAI(
    base_url = "https://openrouter.ai/api/v1",
    api_key  = os.getenv("OPENROUTER_API_KEY"),
)


MODELS = [
    "openrouter/auto",                          
    "deepseek/deepseek-chat-v3.1:free",         
    "meta-llama/llama-3.3-70b:free",           
    "google/gemma-3-12b-it:free",               
]


def chat(prompt: str, temperature: float = 0.7) -> str:

    for model in MODELS:
        try:
            print(f"   Using model: {model}")
            response = client.chat.completions.create(
                model       = model,
                temperature = temperature,
                messages    = [{"role": "user", "content": prompt}]
            )
            return response.choices[0].message.content

        except Exception as e:
            error_str = str(e)
            if "429" in error_str or "rate" in error_str.lower():
                print(f"    {model} rate-limited, trying next...")
                time.sleep(2)
                continue
            elif "404" in error_str:
                print(f"    {model} not found, trying next...")
                continue
            else:
                print(f"   {model} error: {e}")
                raise

    raise Exception(" All models failed. Check your OpenRouter API key and quota.")