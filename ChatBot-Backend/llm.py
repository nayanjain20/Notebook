"""Azure OpenAI client and a thin wrapper for structured (tool-call) requests.

Centralising the client here means every module shares one configured instance
and a single, consistent way to invoke the chat model.
"""

import json
import logging

from openai import AzureOpenAI

import config

logger = logging.getLogger(__name__)

client = AzureOpenAI(
    api_key=config.AZURE_OPENAI_API_KEY,
    api_version=config.AZURE_OPENAI_CHAT_API_VERSION,
    azure_endpoint=config.AZURE_OPENAI_ENDPOINT,
)

DEPLOYMENT = config.CHAT_DEPLOYMENT


def call_tool(messages: list, tool: dict, *, max_tokens: int = 800, temperature: float = 0.5) -> dict:
    """Call the chat model forcing a single tool, and return the parsed arguments.

    Args:
        messages: OpenAI-style chat messages.
        tool: A tool/function schema; the model is forced to call it.
        max_tokens: Response token budget.
        temperature: Sampling temperature.

    Returns:
        The tool call's ``arguments`` parsed from JSON as a dict.
    """
    tool_name = tool["function"]["name"]
    response = client.chat.completions.create(
        model=DEPLOYMENT,
        messages=messages,
        tools=[tool],
        tool_choice={"type": "function", "function": {"name": tool_name}},
        max_completion_tokens=max_tokens,
        temperature=temperature,
    )
    return json.loads(response.choices[0].message.tool_calls[0].function.arguments)


def call_text(messages: list, *, max_tokens: int = 400, temperature: float = 0.4) -> str:
    """Call the chat model for a plain text response and return the stripped content."""
    response = client.chat.completions.create(
        model=DEPLOYMENT,
        messages=messages,
        max_completion_tokens=max_tokens,
        temperature=temperature,
    )
    return (response.choices[0].message.content or "").strip()
