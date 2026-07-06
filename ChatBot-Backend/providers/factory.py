"""Provider factory, model catalog, and per-session routing.

A session runs in one of two **modes**, fixed when it's created:

- **Confidential** — chat runs on a chosen local Ollama model; embeddings run on
  the local embedding model. Nothing leaves the machine.
- **Standard** — chat runs on Azure OpenAI; embeddings on Azure.

Within confidential mode the user picks *which* local chat model to use, from
whatever is actually installed on this system (discovered live). The catalog is
therefore dynamic: no local models installed → confidential mode offers nothing.

This module: discovers the catalog (``list_models``), maps a session's
(mode, model) to concrete providers, caches them, and tracks the providers
active for the current request via context variables so the ``llm`` facade and
``ingestion`` route correctly without threading parameters everywhere.

Model ids use ``"<provider>:<model>"`` (e.g. ``"azure:gpt-4o"``,
``"ollama:qwen2.5:7b-instruct"``).
"""

import contextvars
import logging

import requests

import config
from providers.base import ChatProvider, EmbeddingProvider

logger = logging.getLogger(__name__)

# Preference for ranking local chat models "best first". Earlier = better. Match
# is by substring on the model name; anything unmatched sorts last.
_LOCAL_PREFERENCE = ["qwen2.5", "qwen2", "llama3.1", "llama3", "mistral", "granite", "phi"]

_active_chat_model_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "active_chat_model_id", default=None
)
_active_embedding_name: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "active_embedding_name", default=None
)

_chat_providers: dict[str, ChatProvider] = {}
_embedding_providers: dict[str, EmbeddingProvider] = {}


# ─── Ids ──────────────────────────────────────────────────────────────────────

def _split_model_id(model_id: str) -> tuple[str, str]:
    provider, _, model = model_id.partition(":")
    return provider, model


def active_chat_is_local() -> bool:
    """True when the active request's chat model is a local (Ollama) model.

    Used to adapt multi-step skills: local models are weaker, so some steps that a
    cloud model can do in one call are split into smaller calls for reliability.
    """
    model_id = _active_chat_model_id.get() or default_chat_model_id()
    return _split_model_id(model_id)[0] == "ollama"


def _native_ollama_url() -> str:
    base = config.OLLAMA_BASE_URL.rstrip("/")
    return base[: -len("/v1")] if base.endswith("/v1") else base


# ─── Catalog ──────────────────────────────────────────────────────────────────

def _rank_local(name: str) -> tuple[int, str]:
    """Sort key for a local model name: preference index, then name."""
    for i, pref in enumerate(_LOCAL_PREFERENCE):
        if pref in name.lower():
            return (i, name)
    return (len(_LOCAL_PREFERENCE), name)


def _local_models() -> list[dict]:
    """Installed local chat models, best-first. Empty if Ollama isn't reachable."""
    try:
        resp = requests.get(f"{_native_ollama_url()}/api/tags", timeout=3)
        resp.raise_for_status()
        raw = resp.json().get("models", [])
    except Exception as exc:
        logger.info("[Providers] Ollama not reachable for catalog: %s", exc)
        return []
    names = [
        m.get("name", "") for m in raw
        if m.get("name") and "embed" not in m["name"].lower()
    ]
    names.sort(key=_rank_local)
    out = []
    for name in names:
        label = name[: -len(":latest")] if name.endswith(":latest") else name
        out.append({"id": f"ollama:{name}", "label": label, "provider": "ollama"})
    return out


def _cloud_models() -> list[dict]:
    """Configured cloud chat models (Azure). Empty if not configured."""
    if not config.CHAT_DEPLOYMENT:
        return []
    return [{"id": f"azure:{config.CHAT_DEPLOYMENT}", "label": config.CHAT_DEPLOYMENT, "provider": "azure"}]


def list_models() -> dict:
    """Dynamic catalog for the UI.

    Returns local + cloud chat models available on THIS system, each with a
    recommended default (best local model / the cloud model). Confidential mode
    uses ``local``; standard mode uses ``cloud``.
    """
    local = _local_models()
    cloud = _cloud_models()
    return {
        "local": local,
        "cloud": cloud,
        "recommended_local": local[0]["id"] if local else None,
        "recommended_cloud": cloud[0]["id"] if cloud else None,
    }


# ─── Per-session activation ───────────────────────────────────────────────────

def default_chat_model_id() -> str:
    """Fallback chat model id when a session doesn't specify one."""
    if config.LLM_PROVIDER == "ollama":
        local = _local_models()
        return local[0]["id"] if local else f"ollama:{config.OLLAMA_CHAT_MODEL}"
    return f"azure:{config.CHAT_DEPLOYMENT}"


def embedding_name_for(confidential: bool) -> str:
    return "ollama" if confidential else "azure"


def activate(confidential: bool, chat_model_id: str | None) -> None:
    """Point the active chat + embedding providers at a session's mode & model."""
    if not chat_model_id:
        chat_model_id = (
            (_local_models() or [{"id": default_chat_model_id()}])[0]["id"]
            if confidential else f"azure:{config.CHAT_DEPLOYMENT}"
        )
    _active_chat_model_id.set(chat_model_id)
    _active_embedding_name.set(embedding_name_for(confidential))


# ─── Chat providers ───────────────────────────────────────────────────────────

def _build_chat_provider(model_id: str) -> ChatProvider:
    provider, model = _split_model_id(model_id)
    if provider == "azure":
        from providers.azure_openai import AzureOpenAIChatProvider
        return AzureOpenAIChatProvider(model or None)
    if provider == "ollama":
        from providers.ollama import OllamaChatProvider
        return OllamaChatProvider(model or None)
    raise ValueError(f"Unknown chat model id '{model_id}'. Expected 'azure:…' or 'ollama:…'.")


def get_chat_provider() -> ChatProvider:
    """Chat provider for the active request (cached per model id)."""
    model_id = _active_chat_model_id.get() or default_chat_model_id()
    provider = _chat_providers.get(model_id)
    if provider is None:
        provider = _build_chat_provider(model_id)
        _chat_providers[model_id] = provider
        logger.info("[Providers] Built chat provider: %s", model_id)
    return provider


# ─── Embedding providers ──────────────────────────────────────────────────────

def _build_embedding_provider(name: str) -> EmbeddingProvider:
    if name == "azure":
        from providers.azure_openai import AzureOpenAIEmbeddingProvider
        return AzureOpenAIEmbeddingProvider()
    if name == "ollama":
        from providers.ollama import OllamaEmbeddingProvider
        return OllamaEmbeddingProvider()
    raise ValueError(f"Unknown embedding provider '{name}'. Supported: 'azure', 'ollama'.")


def active_embedding_name() -> str:
    return _active_embedding_name.get() or config.EMBEDDING_PROVIDER


def get_embedding_provider() -> EmbeddingProvider:
    """Embedding provider for the active request (cached per name)."""
    name = active_embedding_name()
    provider = _embedding_providers.get(name)
    if provider is None:
        provider = _build_embedding_provider(name)
        _embedding_providers[name] = provider
        logger.info("[Providers] Built embedding provider: %s", name)
    return provider
