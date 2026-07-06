"""Ollama provider — a locally-hosted, self-managed backend.

Ollama (https://ollama.com) exposes an OpenAI-compatible API, so we reuse the
same ``openai`` SDK for plain text and embeddings — no extra dependency.

Structured output: rather than *asking* the model for JSON and hoping (which
lets weaker local models drop fields), ``call_tool`` uses Ollama's **structured
outputs**: it passes the tool's JSON schema to the native ``/api/chat``
``format`` parameter, which constrains decoding so the reply *must* match the
schema. This is the local equivalent of Azure's forced function-calling, and it
guarantees required fields (e.g. ``source_indices`` for citations) are present.
"""

import json
import logging

import requests
from openai import OpenAI

import config
from providers.base import ChatProvider, EmbeddingProvider

logger = logging.getLogger(__name__)


def _native_base_url() -> str:
    """The native Ollama API base (``.../api``), derived from the OpenAI base url."""
    base = config.OLLAMA_BASE_URL.rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    return base


def _schema_for_format(parameters: dict) -> dict:
    """Adapt a tool's ``parameters`` JSON schema for Ollama's ``format`` field.

    Ollama honours ``type``/``properties``/``required`` and enforces them during
    decoding. We keep the tool's *declared* ``required`` (e.g. answer, confidence,
    source_indices) so those are guaranteed, while leaving heavier optional fields
    (nested objects, link lists) truly optional — forcing a small model to fill
    everything tends to produce degenerate output.
    """
    return {
        "type": parameters.get("type", "object"),
        "properties": parameters.get("properties", {}),
        "required": parameters.get("required") or list(parameters.get("properties", {}).keys()),
    }


def _extract_json(text: str) -> dict:
    """Parse a JSON object from a model reply, tolerating code fences/prose."""
    text = (text or "").strip()
    if "```" in text:
        block = text.split("```")[1]
        if block.lower().startswith("json"):
            block = block[4:]
        text = block.strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start:end + 1]
    return json.loads(text)


class OllamaChatProvider(ChatProvider):
    """Chat backed by a local Ollama model (e.g. llama3.1, qwen2.5)."""

    def __init__(self, model: str | None = None):
        # Ollama ignores the API key, but the SDK requires a non-empty value.
        self._client = OpenAI(base_url=config.OLLAMA_BASE_URL, api_key="ollama")
        self._model = model or config.OLLAMA_CHAT_MODEL
        self._native_url = _native_base_url()

    def call_tool(self, messages: list, tool: dict, *, max_tokens: int = 800,
                  temperature: float = 0.5) -> dict:
        fn = tool["function"]
        schema = _schema_for_format(fn.get("parameters", {}))
        # A light instruction gives the model semantic intent; the `format`
        # schema below is what actually *enforces* the structure during decoding.
        instruction = {
            "role": "system",
            "content": (
                f"Complete the task '{fn['name']}': {fn.get('description', '')}. "
                "Respond with a single JSON object populating every field meaningfully."
            ),
        }
        payload = {
            "model": self._model,
            "messages": messages + [instruction],
            "format": schema,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        resp = requests.post(f"{self._native_url}/api/chat", json=payload, timeout=180)
        resp.raise_for_status()
        content = resp.json().get("message", {}).get("content", "")
        return _extract_json(content)

    def call_text(self, messages: list, *, max_tokens: int = 400,
                  temperature: float = 0.4) -> str:
        response = self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return (response.choices[0].message.content or "").strip()


class OllamaEmbeddingProvider(EmbeddingProvider):
    """Embeddings backed by a local Ollama embedding model (e.g. nomic-embed-text)."""

    def __init__(self):
        self._client = OpenAI(base_url=config.OLLAMA_BASE_URL, api_key="ollama")
        self._model = config.OLLAMA_EMBEDDING_MODEL

    def embed(self, text: str) -> list[float]:
        response = self._client.embeddings.create(model=self._model, input=text)
        return response.data[0].embedding
