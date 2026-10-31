"""Shared LLM client.

Single source of truth for the Groq AsyncOpenAI-compatible client.
All LLM calls in vision.py, agent.py and conversation.py import from here.
"""

from openai import AsyncOpenAI

from app.config import settings

# max_retries=1 + timeout=20s: keeps a failed call from stalling the request.
groq_client: AsyncOpenAI = AsyncOpenAI(
    api_key=settings.groq_api_key,
    base_url="https://api.groq.com/openai/v1",
    max_retries=1,
    timeout=20.0,
)
