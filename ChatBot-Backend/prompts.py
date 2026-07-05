"""Prompt assets for the agent: the persona ("soul"), session-memory rendering,
and every tool (function-calling) schema the agent uses.

Keeping these declarative pieces in one place makes the agent's behaviour easy
to read and tune without touching control flow.
"""

import logging
import os

logger = logging.getLogger(__name__)

_SOUL_FALLBACK = (
    "You are Notebook, a warm learning companion that helps the user understand a topic "
    "using their sources. Be concise, use plain language and specific examples, teach step "
    "by step, show diagrams only for relationships/architecture, and refuse unsafe content."
)


def _load_soul() -> str:
    """Load the agent's soul (persona + teaching rules) from ``soul.md``."""
    path = os.path.join(os.path.dirname(__file__), "soul.md")
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError as exc:
        logger.warning("[Soul] Could not load soul.md (%s); using fallback", exc)
        return _SOUL_FALLBACK


# The agent's core behaviour, loaded once and used as the base of every prompt.
AGENT_PERSONA = _load_soul()

NOT_FOUND_MESSAGE = (
    "I couldn't find anything relevant in your uploaded documents or links to answer that."
)


def render_session_memory(context: dict) -> str:
    """Render the per-session memory into a prompt block the agent can reason over.

    The memory captures what the agent has learned about this learning session
    (the user's goal, what's been covered, the running example, and so on).
    """
    if not context:
        return ""
    lines = []
    if context.get("purpose"):
        lines.append(f"User's goal: {context['purpose']}")
    if context.get("expectations"):
        lines.append(f"Expectations: {context['expectations']}")
    if context.get("current_topic"):
        lines.append(f"Current topic: {context['current_topic']}")
    if context.get("example_domain"):
        lines.append(f"Running example to reuse: {context['example_domain']}")
    if context.get("covered"):
        lines.append("Already covered: " + "; ".join(context["covered"][-8:]))
    if context.get("interests"):
        lines.append("Interests: " + ", ".join(context["interests"][-6:]))
    if context.get("next_step"):
        lines.append(f"Planned next step: {context['next_step']}")
    if not lines:
        return ""
    return (
        "\n\nSESSION MEMORY (what you know so far — use it, keep it consistent):\n- "
        + "\n- ".join(lines)
    )


# ─── Tool schemas ─────────────────────────────────────────────────────────────

ANSWER_TOOL = {
    "type": "function",
    "function": {
        "name": "provide_answer",
        "description": "Provide a structured, helpful answer as a study assistant",
        "parameters": {
            "type": "object",
            "properties": {
                "answer": {"type": "string", "description": "A clear, helpful answer to the user's question"},
                "confidence": {
                    "type": "number",
                    "description": "Confidence in the answer, 0.0 (uncertain) to 1.0 (very confident)",
                },
                "source_indices": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "1-based indices of the context sources you actually used. Empty if answering from general knowledge.",
                },
                "follow_ups": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "0-3 short, specific next questions the user might want next, phrased as the user "
                        "would ask them. Include ONLY when they add value. Empty for trivial replies."
                    ),
                },
                "suggested_links": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string", "description": "A real, well-known http(s) URL"},
                            "title": {"type": "string", "description": "Short label describing the link"},
                        },
                        "required": ["url", "title"],
                    },
                    "description": (
                        "0-4 authoritative URLs that would deepen understanding and could be added as sources. "
                        "Only real, canonical URLs. If unsure, return an empty list — never invent URLs."
                    ),
                },
                "session_update": {
                    "type": "object",
                    "description": "Update your memory of this learning session. Include only fields that changed.",
                    "properties": {
                        "purpose": {"type": "string", "description": "The user's overall goal, once known"},
                        "expectations": {"type": "string", "description": "What kind of help/depth the user expects"},
                        "current_topic": {"type": "string", "description": "The topic of this turn"},
                        "example_domain": {
                            "type": "string",
                            "description": "The running example/analogy you use this session (e.g. 'coffee shop'). Set once; keep consistent.",
                        },
                        "covered_add": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Short labels of concepts you just taught, to remember as covered",
                        },
                        "interests": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Subtopics the user showed interest in",
                        },
                        "next_step": {"type": "string", "description": "The natural next step you plan to take"},
                    },
                },
            },
            "required": ["answer", "confidence", "source_indices"],
        },
    },
}

DIAGRAM_TOOL = {
    "type": "function",
    "function": {
        "name": "provide_diagram",
        "description": "Decide whether a diagram helps and, if so, produce a Mermaid diagram",
        "parameters": {
            "type": "object",
            "properties": {
                "needs_diagram": {
                    "type": "boolean",
                    "description": (
                        "True ONLY if the answer is fundamentally about how things RELATE or CONNECT: a system "
                        "architecture, how components interact, an entity/relationship, or a hierarchy — where a "
                        "box-and-arrow picture reveals structure words cannot. FALSE for everything else, including "
                        "lists of steps, how-tos, procedures, definitions, clarifications, comparisons, opinions, "
                        "and simple explanations. Most answers are FALSE. When unsure, choose false."
                    ),
                },
                "mermaid": {
                    "type": "string",
                    "description": "Valid Mermaid source (e.g. 'graph TD'). Short node labels. Empty if needs_diagram is false.",
                },
                "caption": {"type": "string", "description": "A short caption describing the diagram. Empty if no diagram."},
            },
            "required": ["needs_diagram", "mermaid", "caption"],
        },
    },
}

INTRO_TOOL = {
    "type": "function",
    "function": {
        "name": "provide_intro",
        "description": "Introduce a newly added source with a tiny gist and a few things the user might want.",
        "parameters": {
            "type": "object",
            "properties": {
                "idea": {"type": "string", "description": "2-3 short sentences: what this source is about. No long summary."},
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "3-4 concrete things the user might want to do/learn, phrased as the user would ask them.",
                },
            },
            "required": ["idea", "options"],
        },
    },
}

REFLECT_TOOL = {
    "type": "function",
    "function": {
        "name": "decide_next_action",
        "description": "As an autonomous learning agent, decide the single next action before answering.",
        "parameters": {
            "type": "object",
            "properties": {
                "thought": {
                    "type": "string",
                    "description": "ONE short first-person sentence for the user's thought-process trace.",
                },
                "action": {
                    "type": "string",
                    "enum": ["answer", "search_more", "fetch_source", "clarify"],
                    "description": (
                        "answer = you have enough to give a good grounded answer now. "
                        "search_more = gathered context is insufficient; search the sources again with a better query. "
                        "fetch_source = a specific authoritative page is needed and is NOT already a source; fetch it. "
                        "clarify = the request is genuinely ambiguous and you must ask ONE short question first."
                    ),
                },
                "query": {"type": "string", "description": "For search_more: a focused search query."},
                "url": {"type": "string", "description": "For fetch_source: a real, canonical http(s) URL. Never invent URLs."},
                "title": {"type": "string", "description": "For fetch_source: a short label for the URL."},
                "question": {"type": "string", "description": "For clarify: ONE short question to ask the user."},
            },
            "required": ["thought", "action"],
        },
    },
}

CRITIQUE_TOOL = {
    "type": "function",
    "function": {
        "name": "critique_answer",
        "description": "Judge whether a drafted answer is high-quality, and if not, how to improve it.",
        "parameters": {
            "type": "object",
            "properties": {
                "good_enough": {
                    "type": "boolean",
                    "description": (
                        "True if the draft genuinely helps the user learn: accurate, clear, well-grounded, with a "
                        "concrete specific example where a concept is explained. False if shallow, vague, or missing depth."
                    ),
                },
                "reason": {
                    "type": "string",
                    "description": "ONE short first-person sentence for the thought trace on what to improve (only if not good enough).",
                },
                "needs_deeper_source": {
                    "type": "boolean",
                    "description": "True if a dedicated/authoritative page (deeper than what's retrieved) would materially improve the answer.",
                },
                "fetch_url": {"type": "string", "description": "If needs_deeper_source: a real, canonical http(s) URL. Never invent URLs."},
                "fetch_title": {"type": "string", "description": "Short label for fetch_url."},
                "better_query": {"type": "string", "description": "If more retrieval from existing sources would help: a focused query."},
            },
            "required": ["good_enough"],
        },
    },
}
