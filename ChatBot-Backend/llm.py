"""Chat model facade.

The rest of the app calls ``llm.call_tool`` and ``llm.call_text`` without caring
which backend answers. Those calls are delegated to the configured chat provider
(Azure OpenAI or a local Ollama model) selected in ``config`` and built by
``providers.factory``. Swapping backends is a config change, not a code change.
"""

import logging

from providers.factory import get_chat_provider

logger = logging.getLogger(__name__)


def call_tool(messages: list, tool: dict, *, max_tokens: int = 800,
              temperature: float = 0.5) -> dict:
    """Get a single structured (JSON) result matching a tool schema.

    Args:
        messages: OpenAI-style chat messages.
        tool: A tool/function schema; its ``parameters`` define the output shape.
        max_tokens: Response token budget.
        temperature: Sampling temperature.

    Returns:
        The structured arguments as a dict.
    """
    return get_chat_provider().call_tool(
        messages, tool, max_tokens=max_tokens, temperature=temperature
    )


def call_text(messages: list, *, max_tokens: int = 400,
              temperature: float = 0.4) -> str:
    """Get a plain-text reply from the configured chat model."""
    return get_chat_provider().call_text(
        messages, max_tokens=max_tokens, temperature=temperature
    )
