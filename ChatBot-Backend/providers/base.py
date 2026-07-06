"""Provider interfaces (the Strategy pattern).

Every model backend implements these two small interfaces. Because the whole
app depends only on these abstractions, adding a new backend (e.g. a different
local runtime) means writing one new class — nothing else changes.
"""

from abc import ABC, abstractmethod


class ChatProvider(ABC):
    """A chat model backend.

    Two call shapes cover everything the agent needs:
    - ``call_tool``  — get a single structured (JSON) result matching a schema.
    - ``call_text``  — get a plain free-text answer.
    """

    @abstractmethod
    def call_tool(self, messages: list, tool: dict, *, max_tokens: int = 800,
                  temperature: float = 0.5) -> dict:
        """Return a dict matching the tool's ``parameters`` JSON schema.

        Args:
            messages: OpenAI-style chat messages.
            tool: A tool/function schema (``{"function": {"name", "parameters"}}``).
            max_tokens: Response token budget.
            temperature: Sampling temperature.

        Returns:
            The structured arguments parsed from the model's response.
        """

    @abstractmethod
    def call_text(self, messages: list, *, max_tokens: int = 400,
                  temperature: float = 0.4) -> str:
        """Return the model's plain-text reply, stripped of surrounding whitespace."""


class EmbeddingProvider(ABC):
    """An embedding model backend."""

    @abstractmethod
    def embed(self, text: str) -> list[float]:
        """Return the embedding vector for a piece of text."""
