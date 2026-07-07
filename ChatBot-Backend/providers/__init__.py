"""Pluggable LLM providers.

This package lets Notebook talk to different model backends through one common
interface, so you can switch between a cloud provider (Azure OpenAI) and a
locally-hosted model (Ollama) with a single config change — no changes to the
agent, onboarding, or retrieval code.

Design:
- ``base``    — the provider interfaces (Strategy pattern): ``ChatProvider`` and
                ``EmbeddingProvider``.
- ``azure_openai`` / ``ollama`` — concrete implementations of those interfaces.
- ``factory`` — selects and caches the active implementation from config.

The rest of the app depends only on the interfaces via ``factory.get_chat_provider``
and ``factory.get_embedding_provider``; it never imports a concrete provider.
"""

from providers.base import ChatProvider, EmbeddingProvider
from providers.factory import (
    activate,
    active_chat_is_local,
    active_embedding_name,
    default_chat_model_id,
    get_chat_provider,
    get_embedding_provider,
    list_models,
)

__all__ = [
    "ChatProvider",
    "EmbeddingProvider",
    "get_chat_provider",
    "get_embedding_provider",
    "activate",
    "active_chat_is_local",
    "active_embedding_name",
    "default_chat_model_id",
    "list_models",
]
