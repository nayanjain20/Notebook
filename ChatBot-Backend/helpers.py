"""Small pure helpers shared across the agent modules.

These functions have no side effects and no LLM calls — they just shape data
(chat history, chunk lists, Mermaid text, session memory) for the agent.
"""

# Mermaid diagram types we accept; anything else is treated as invalid output.
_MERMAID_STARTERS = (
    "graph", "flowchart", "sequencediagram", "classdiagram", "statediagram",
    "erdiagram", "mindmap", "gantt", "pie", "journey", "gitgraph", "timeline",
    "quadrantchart", "requirementdiagram",
)


def history_to_messages(history: list) -> list:
    """Convert the frontend chat history into OpenAI-style messages."""
    messages = []
    for message in history:
        role = "assistant" if message.get("role") == "model" else "user"
        for part in message.get("parts", []):
            if part.get("text"):
                messages.append({"role": role, "content": part["text"]})
                break
    return messages


def sample_text(chunks: list, limit: int = 8000) -> str:
    """Join chunk texts into a single string, truncated to ``limit`` characters."""
    return "\n\n".join(chunk["text"] for chunk in chunks)[:limit]


def chunks_digest(chunks: list, limit: int = 8) -> str:
    """A compact, human-readable digest of retrieved chunks for a reflection prompt."""
    if not chunks:
        return "(nothing retrieved yet)"
    lines = []
    for i, chunk in enumerate(chunks[:limit], 1):
        snippet = " ".join(chunk["text"].split())[:180]
        lines.append(f"[{i}] {chunk['metadata']['filename']}: {snippet}")
    if len(chunks) > limit:
        lines.append(f"…and {len(chunks) - limit} more passages")
    return "\n".join(lines)


def merge_chunks(existing: list, new: list) -> list:
    """Append ``new`` chunks to ``existing``, de-duplicating by chunk id."""
    seen = {chunk["id"] for chunk in existing}
    for chunk in new:
        if chunk["id"] not in seen:
            seen.add(chunk["id"])
            existing.append(chunk)
    return existing


def clean_mermaid(text: str) -> str:
    """Strip code fences and validate a Mermaid diagram's declared type.

    Returns the cleaned diagram source, or an empty string if it doesn't start
    with a recognised Mermaid diagram type (so we never render broken output).
    """
    if not text:
        return ""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1] if "```" in text[3:] else text[3:]
        if text.lower().startswith("mermaid"):
            text = text[len("mermaid"):]
        text = text.strip()
    first_token = text.lstrip().split(maxsplit=1)[0].lower() if text.strip() else ""
    return text if first_token in _MERMAID_STARTERS else ""


def merge_session_update(context: dict, update: dict) -> dict:
    """Fold a model-produced ``session_update`` into the session memory dict."""
    if not isinstance(update, dict):
        return context
    for key in ("purpose", "expectations", "current_topic", "next_step", "example_domain"):
        value = update.get(key)
        if isinstance(value, str) and value.strip():
            context[key] = value.strip()
    if update.get("covered_add"):
        covered = context.get("covered", [])
        for item in update["covered_add"]:
            if isinstance(item, str) and item.strip() and item not in covered:
                covered.append(item.strip())
        context["covered"] = covered[-30:]
    if update.get("interests"):
        interests = context.get("interests", [])
        for item in update["interests"]:
            if isinstance(item, str) and item.strip() and item not in interests:
                interests.append(item.strip())
        context["interests"] = interests[-20:]
    return context
