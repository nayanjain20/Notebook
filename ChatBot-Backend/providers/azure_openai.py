"""Azure OpenAI provider — the default cloud backend.

Wraps the Azure OpenAI SDK. ``call_tool`` uses native function calling (forcing
a single tool), which Azure's models support reliably.
"""

import json
import logging

from openai import AzureOpenAI

import config
from providers.base import ChatProvider, EmbeddingProvider

logger = logging.getLogger(__name__)


def _make_client() -> AzureOpenAI:
    return AzureOpenAI(
        api_key=config.AZURE_OPENAI_API_KEY,
        api_version=config.AZURE_OPENAI_CHAT_API_VERSION,
        azure_endpoint=config.AZURE_OPENAI_ENDPOINT,
    )


class AzureOpenAIChatProvider(ChatProvider):
    """Chat backed by an Azure OpenAI chat deployment (e.g. gpt-4o)."""

    def __init__(self, model: str | None = None):
        self._client = _make_client()
        self._deployment = model or config.CHAT_DEPLOYMENT

    def call_tool(self, messages: list, tool: dict, *, max_tokens: int = 800,
                  temperature: float = 0.5) -> dict:
        tool_name = tool["function"]["name"]
        response = self._client.chat.completions.create(
            model=self._deployment,
            messages=messages,
            tools=[tool],
            tool_choice={"type": "function", "function": {"name": tool_name}},
            max_completion_tokens=max_tokens,
            temperature=temperature,
        )
        return json.loads(response.choices[0].message.tool_calls[0].function.arguments)

    def call_text(self, messages: list, *, max_tokens: int = 400,
                  temperature: float = 0.4) -> str:
        response = self._client.chat.completions.create(
            model=self._deployment,
            messages=messages,
            max_completion_tokens=max_tokens,
            temperature=temperature,
        )
        return (response.choices[0].message.content or "").strip()


class AzureOpenAIEmbeddingProvider(EmbeddingProvider):
    """Embeddings backed by an Azure OpenAI embedding deployment."""

    def __init__(self):
        self._client = _make_client()
        self._deployment = config.AZURE_OPENAI_EMBEDDING_DEPLOYMENT

    def embed(self, text: str) -> list[float]:
        response = self._client.embeddings.create(model=self._deployment, input=text)
        return response.data[0].embedding
