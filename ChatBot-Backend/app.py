import os
import json
import logging
from urllib.parse import urlparse
from flask import Flask, request, jsonify, Response
from flask_cors import CORS
from openai import AzureOpenAI
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
from ingestion import (
    extract_and_chunk, extract_and_chunk_url, embed_and_store, advanced_search,
    list_documents, delete_document, delete_session_documents, source_name_for_url,
)
import db

load_dotenv()

EVALS_MODE = os.getenv("EVALS_MODE", "false").lower() == "true"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)

app = Flask(__name__)
CORS(app)

db.init_db()

DOCS_DIR = "docs"
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB
ALLOWED_EXTENSIONS = {"pdf", "txt"}
os.makedirs(DOCS_DIR, exist_ok=True)

client = AzureOpenAI(
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    api_version=os.getenv("AZURE_OPENAI_CHAT_API_VERSION"),
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
)
DEPLOYMENT = os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT")

ANSWER_TOOL = {
    "type": "function",
    "function": {
        "name": "provide_answer",
        "description": "Provide a structured, helpful answer as a study assistant",
        "parameters": {
            "type": "object",
            "properties": {
                "answer": {
                    "type": "string",
                    "description": "A clear, helpful answer to the user's question"
                },
                "confidence": {
                    "type": "number",
                    "description": "Confidence in the answer, 0.0 (uncertain) to 1.0 (very confident)"
                },
                "source_indices": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "1-based indices of the context sources you actually used. Leave empty if answering from general knowledge."
                },
                "follow_ups": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "0-3 short, specific next questions or actions the user might want next, "
                        "phrased as the user would ask them (e.g. 'Explain how Kafka partitions work'). "
                        "Include ONLY when they genuinely add value and move learning forward. "
                        "Leave empty for simple factual replies or when nothing useful follows."
                    )
                },
                "suggested_links": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string", "description": "A real, well-known http(s) URL the user could add as a source"},
                            "title": {"type": "string", "description": "Short label describing the link"}
                        },
                        "required": ["url", "title"]
                    },
                    "description": (
                        "0-4 authoritative documentation/reference URLs that would deepen understanding of the "
                        "topic and that the user can add as new sources. Only suggest URLs you are confident are "
                        "real and canonical (e.g. official docs). If unsure, return an empty list — never invent URLs."
                    )
                },
                "session_update": {
                    "type": "object",
                    "description": "Update your memory of this learning session. Include only fields that changed.",
                    "properties": {
                        "purpose": {"type": "string", "description": "The user's overall goal, once known"},
                        "expectations": {"type": "string", "description": "What kind of help/depth the user expects"},
                        "current_topic": {"type": "string", "description": "The topic of this turn"},
                        "example_domain": {"type": "string", "description": "The running example/analogy you are using this session (e.g. 'coffee shop', 'library'). Set once you pick one; keep it consistent; change only if the user asks."},
                        "covered_add": {"type": "array", "items": {"type": "string"}, "description": "Short labels of concepts you just taught, to remember as covered"},
                        "interests": {"type": "array", "items": {"type": "string"}, "description": "Subtopics the user showed interest in"},
                        "next_step": {"type": "string", "description": "The natural next step you plan to take"}
                    }
                }
            },
            "required": ["answer", "confidence", "source_indices"]
        }
    }
}

@app.route('/')
def index():
    return jsonify({"message": "Hello from GemBot Flask API!"})

# ─── Assistant soul (persona + rules) ─────────────────────────────────────────

_AGENT_PERSONA_FALLBACK = (
    "You are Notebook, a warm learning companion that helps the user understand a topic "
    "using their sources. Be concise, use plain language and examples, teach step by step, "
    "show diagrams only when they help, and refuse harmful or unsafe content."
)


def _load_soul() -> str:
    try:
        with open(os.path.join(os.path.dirname(__file__), "soul.md"), encoding="utf-8") as f:
            return f.read().strip()
    except Exception as e:
        logging.getLogger(__name__).warning(f"[Soul] Could not load soul.md ({e}); using fallback")
        return _AGENT_PERSONA_FALLBACK


# The "soul" is the agent's core behavior, loaded once and used as the base of every prompt.
AGENT_PERSONA = _load_soul()


def _context_block(context: dict) -> str:
    """Render the session context (agent memory) for injection into the prompt."""
    if not context:
        return ""
    parts = []
    if context.get("purpose"):
        parts.append(f"User's goal: {context['purpose']}")
    if context.get("expectations"):
        parts.append(f"Expectations: {context['expectations']}")
    if context.get("current_topic"):
        parts.append(f"Current topic: {context['current_topic']}")
    if context.get("example_domain"):
        parts.append(f"Running example to reuse: {context['example_domain']}")
    if context.get("covered"):
        parts.append("Already covered: " + "; ".join(context["covered"][-8:]))
    if context.get("interests"):
        parts.append("Interests: " + ", ".join(context["interests"][-6:]))
    if context.get("next_step"):
        parts.append(f"Planned next step: {context['next_step']}")
    return ("\n\nSESSION MEMORY (what you know so far — use it, keep it consistent):\n- "
            + "\n- ".join(parts)) if parts else ""


# ─── Diagram (Mermaid) generation ─────────────────────────────────────────────

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
                    )
                },
                "mermaid": {
                    "type": "string",
                    "description": "Valid Mermaid diagram source (e.g. 'graph TD' / 'sequenceDiagram' / 'flowchart LR'). Keep node labels short. Empty string if needs_diagram is false."
                },
                "caption": {
                    "type": "string",
                    "description": "A short caption describing the diagram. Empty if no diagram."
                }
            },
            "required": ["needs_diagram", "mermaid", "caption"]
        }
    }
}

_MERMAID_STARTERS = (
    "graph", "flowchart", "sequencediagram", "classdiagram", "statediagram",
    "erdiagram", "mindmap", "gantt", "pie", "journey", "gitgraph", "timeline",
    "quadrantchart", "requirementdiagram",
)


def _clean_mermaid(text: str) -> str:
    """Strip code fences and validate the diagram starts with a known Mermaid type."""
    if not text:
        return ""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1] if "```" in text[3:] else text[3:]
        if text.lower().startswith("mermaid"):
            text = text[len("mermaid"):]
        text = text.strip()
    first = text.lstrip().split(maxsplit=1)[0].lower() if text.strip() else ""
    return text if first in _MERMAID_STARTERS else ""


def generate_diagram(question: str, answer: str, example_domain: str = None) -> dict:
    """Secondary LLM call: returns {mermaid, caption} when a diagram helps, else None."""
    example_hint = ""
    if example_domain:
        example_hint = (
            f" A running example is in play for this session: \"{example_domain}\". If you draw a diagram, you may "
            "make it EITHER a technical diagram of the concept OR a diagram of the example itself (whichever teaches "
            "better) — and label nodes using the example's concrete terms so the picture matches the explanation "
            f"(e.g. for a library example, nodes like 'Science section (topic)', 'Journal shelf (partition)')."
        )
    try:
        response = client.chat.completions.create(
            model=DEPLOYMENT,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You decide whether an explanation needs a diagram, and you are VERY conservative — the "
                        "large majority of answers need NO diagram. Only produce a Mermaid diagram when the answer "
                        "is fundamentally about how things RELATE or CONNECT: a system architecture, how components "
                        "interact, an entity/relationship, or a hierarchy. Do NOT diagram lists of steps, how-tos, "
                        "procedures, definitions, clarifications, comparisons, opinions, or simple explanations — "
                        "return needs_diagram=false for those. A list of steps is NOT a reason to draw a diagram. "
                        "When in doubt, return false. If you do make one, output valid, minimal Mermaid with short "
                        "labels; avoid parentheses and special characters inside node text." + example_hint
                    ),
                },
                {"role": "user", "content": f"Question:\n{question}\n\nAnswer:\n{answer[:2500]}"},
            ],
            tools=[DIAGRAM_TOOL],
            tool_choice={"type": "function", "function": {"name": "provide_diagram"}},
            max_completion_tokens=600,
            temperature=0.3,
        )
        args = json.loads(response.choices[0].message.tool_calls[0].function.arguments)
        if not args.get("needs_diagram"):
            return None
        mermaid = _clean_mermaid(args.get("mermaid", ""))
        if not mermaid:
            return None
        app.logger.info(f"[Diagram] Generated ({mermaid.splitlines()[0][:40]}…)")
        return {"mermaid": mermaid, "caption": (args.get("caption") or "").strip()}
    except Exception as e:
        app.logger.warning(f"[Diagram] Generation failed: {e}")
        return None


def summarize_source(filename: str, chunks: list) -> str:
    """Generate a brief summary of a newly added source from its chunks."""
    text = "\n\n".join(c["text"] for c in chunks)[:12000]
    try:
        response = client.chat.completions.create(
            model=DEPLOYMENT,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a study assistant. Write a brief, well-structured summary (120-180 words) of the "
                        "document below so the reader quickly grasps what it covers. Use 2-4 short bullet points for "
                        "the key topics. Start with one sentence naming what the document is about."
                    ),
                },
                {"role": "user", "content": f"Document: {filename}\n\n{text}"},
            ],
            max_completion_tokens=400,
            temperature=0.3,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        app.logger.warning(f"[Summary] Failed for '{filename}': {e}")
        return ""


def _sample_text(chunks: list, limit: int = 8000) -> str:
    return "\n\n".join(c["text"] for c in chunks)[:limit]


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
                    "description": "3-4 concrete things the user might want to do/learn with this source, phrased as the user would ask them."
                }
            },
            "required": ["idea", "options"]
        }
    }
}


def generate_intro(filename: str, chunks: list) -> dict:
    """First source: a tiny idea of the doc + a few 'how can I help' options."""
    try:
        resp = client.chat.completions.create(
            model=DEPLOYMENT,
            messages=[
                {"role": "system", "content": (
                    AGENT_PERSONA + "\n\nA user just added their first source. Give only a TINY idea of what it "
                    "covers (2-3 sentences, no long summary), then propose 3-4 concrete things they might want to "
                    "do with it (phrased as the user would ask). Keep it warm and short."
                )},
                {"role": "user", "content": f"Source: {filename}\n\n{_sample_text(chunks)}"},
            ],
            tools=[INTRO_TOOL],
            tool_choice={"type": "function", "function": {"name": "provide_intro"}},
            max_completion_tokens=400,
            temperature=0.4,
        )
        args = json.loads(resp.choices[0].message.tool_calls[0].function.arguments)
        idea = (args.get("idea") or "").strip()
        options = [s.strip() for s in (args.get("options") or []) if isinstance(s, str) and s.strip()][:4]
        return {"idea": idea, "options": options}
    except Exception as e:
        app.logger.warning(f"[Intro] failed for '{filename}': {e}")
        return {"idea": "", "options": []}


def generate_ack(filename: str, chunks: list, context: dict) -> str:
    """Later source: a short acknowledgment tied to the user's goal (no long summary)."""
    try:
        resp = client.chat.completions.create(
            model=DEPLOYMENT,
            messages=[
                {"role": "system", "content": (
                    AGENT_PERSONA + _context_block(context) + "\n\nThe user just added another source. In 1-2 short "
                    "sentences, acknowledge it and say briefly what it adds and how it helps their goal. Do NOT write "
                    "a full summary."
                )},
                {"role": "user", "content": f"New source: {filename}\n\n{_sample_text(chunks, 4000)}"},
            ],
            max_completion_tokens=160,
            temperature=0.4,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        app.logger.warning(f"[Ack] failed for '{filename}': {e}")
        return ""


def generate_title(text: str) -> str:
    """Concise 3-6 word title from some text."""
    try:
        response = client.chat.completions.create(
            model=DEPLOYMENT,
            messages=[
                {"role": "system", "content": "Generate a concise 3-6 word title capturing the topic. Return ONLY the title — no quotes, no trailing punctuation."},
                {"role": "user", "content": text[:600]},
            ],
            max_completion_tokens=20,
            temperature=0.4,
        )
        return response.choices[0].message.content.strip()
    except Exception:
        return ""


def summarize_and_save(session_id: str, filename: str, chunks: list, visuals: bool = True) -> dict:
    """On the FIRST source: post a tiny idea + 'how can I help' options and title the session.
    On LATER sources: post a short acknowledgment tied to the user's goal. No long summaries."""
    is_first = db.is_first_message(session_id)

    if is_first:
        intro = generate_intro(filename, chunks)
        idea = intro["idea"]
        if not idea:
            return None
        text = f"{idea}\n\nHow can I help you with this?"
        options = intro["options"]
        db.save_message(session_id, "model", [{"text": text}], follow_ups=options or None)
        title = generate_title(idea) or filename
        db.update_session_title(session_id, title)
        app.logger.info(f"[Intro] Onboarded session {session_id[:8]}… from '{filename}'")
        return {"role": "model", "text": text, "follow_ups": options, "session_title": title}

    context = db.get_session_context(session_id)
    ack = generate_ack(filename, chunks, context)
    if not ack:
        ack = f"Added **{filename}** — I'll use it alongside your other sources."
    db.save_message(session_id, "model", [{"text": ack}])
    app.logger.info(f"[Ack] Acknowledged new source '{filename}' in session {session_id[:8]}…")
    return {"role": "model", "text": ack}

# ─── Chat ─────────────────────────────────────────────────────────────────────

NOT_FOUND_MSG = "I couldn't find anything relevant in your uploaded documents or links to answer that."

PLAN_TOOL = {
    "type": "function",
    "function": {
        "name": "provide_plan",
        "description": "Plan the next move before answering: a brief thought and any sources to pull in.",
        "parameters": {
            "type": "object",
            "properties": {
                "thought": {
                    "type": "string",
                    "description": "ONE short first-person sentence (max ~18 words) describing what you're about to do, e.g. 'Let me check your sources for how partitions work.'"
                },
                "auto_import": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string"},
                            "title": {"type": "string"}
                        },
                        "required": ["url", "title"]
                    },
                    "description": "Up to 2 real, canonical http(s) URLs that would clearly help the user's current goal and are NOT already sources. Empty unless genuinely needed. Never invent URLs."
                }
            },
            "required": ["thought", "auto_import"]
        }
    }
}


def _merge_session_update(context: dict, update: dict) -> dict:
    if not isinstance(update, dict):
        return context
    for k in ("purpose", "expectations", "current_topic", "next_step", "example_domain"):
        v = update.get(k)
        if isinstance(v, str) and v.strip():
            context[k] = v.strip()
    if update.get("covered_add"):
        covered = context.get("covered", [])
        for c in update["covered_add"]:
            if isinstance(c, str) and c.strip() and c not in covered:
                covered.append(c.strip())
        context["covered"] = covered[-30:]
    if update.get("interests"):
        ints = context.get("interests", [])
        for c in update["interests"]:
            if isinstance(c, str) and c.strip() and c not in ints:
                ints.append(c.strip())
        context["interests"] = ints[-20:]
    return context


def _history_messages(history: list) -> list:
    msgs = []
    for msg in history:
        role = "assistant" if msg.get("role") == "model" else "user"
        for part in msg.get("parts", []):
            if "text" in part and part["text"]:
                msgs.append({"role": role, "content": part["text"]})
                break
    return msgs


def retrieve_chunks(user_message: str, history: list, session_id: str) -> list:
    if not session_id:
        return []
    chunks = advanced_search(user_message, history=history, session_id=session_id)
    app.logger.info(f"[Request] Retrieved {len(chunks)} chunks from advanced_search")
    return chunks


def _merge_chunks(existing: list, new: list) -> list:
    """Merge retrieved chunk lists, de-duplicating by chunk id."""
    seen = {c["id"] for c in existing}
    for c in new:
        if c["id"] not in seen:
            seen.add(c["id"])
            existing.append(c)
    return existing


def _chunks_digest(chunks: list, limit: int = 8) -> str:
    """A compact view of gathered context for the reflection call."""
    if not chunks:
        return "(nothing retrieved yet)"
    lines = []
    for i, c in enumerate(chunks[:limit], 1):
        snippet = " ".join(c["text"].split())[:180]
        lines.append(f"[{i}] {c['metadata']['filename']}: {snippet}")
    if len(chunks) > limit:
        lines.append(f"…and {len(chunks) - limit} more passages")
    return "\n".join(lines)


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
                    "description": "ONE short first-person sentence for the user's thought-process trace, e.g. 'The sources cover this — let me explain.' or 'I need the official docs for the exact APIs.'"
                },
                "action": {
                    "type": "string",
                    "enum": ["answer", "search_more", "fetch_source", "clarify"],
                    "description": (
                        "answer = you have enough to give a good, grounded answer now. "
                        "search_more = the gathered context is insufficient; search the sources again with a better query. "
                        "fetch_source = a specific authoritative page is needed and is NOT already a source; fetch it. "
                        "clarify = the user's request is genuinely ambiguous and you must ask ONE short question first."
                    )
                },
                "query": {"type": "string", "description": "For search_more: a focused search query."},
                "url": {"type": "string", "description": "For fetch_source: a real, canonical http(s) URL. Never invent URLs."},
                "title": {"type": "string", "description": "For fetch_source: a short label for the URL."},
                "question": {"type": "string", "description": "For clarify: ONE short question to ask the user."}
            },
            "required": ["thought", "action"]
        }
    }
}


def agent_reflect(user_message, history, context, chunks, existing_names, iteration, max_iterations):
    """Decide the next agent action given what has been gathered so far."""
    try:
        sys = (
            AGENT_PERSONA + _context_block(context) + "\n\n"
            "You are an autonomous learning agent. You have gathered some context from the user's sources (below). "
            "Decide the SINGLE next action to produce the BEST possible answer — not merely an acceptable one. "
            "Do NOT settle just because you found something relevant: the user's current source may be surface-level "
            "while a dedicated/linked resource holds far richer material. Ask yourself 'what would make this answer "
            "genuinely excellent?' and act on it:\n"
            "- 'search_more' if the passages are thin or off-target (give a sharper query).\n"
            "- 'fetch_source' if a specific canonical page (e.g. official docs, a dedicated guide) would give "
            "materially deeper/better material than what's retrieved and isn't already a source. Prefer this over "
            "answering from shallow context. Never invent URLs.\n"
            "- 'clarify' ONLY if the request is genuinely ambiguous and you truly cannot proceed without one short "
            "question.\n"
            "- 'answer' when you have rich, sufficient material to teach this well.\n"
            f"This is reflection step {iteration} of at most {max_iterations}. If near the limit, answer.\n"
            f"Current sources: {sorted(existing_names) if existing_names else 'none'}.\n\n"
            f"Gathered context:\n{_chunks_digest(chunks)}"
        )
        messages = [{"role": "system", "content": sys}] + _history_messages(history) + [
            {"role": "user", "content": user_message}
        ]
        resp = client.chat.completions.create(
            model=DEPLOYMENT,
            messages=messages,
            tools=[REFLECT_TOOL],
            tool_choice={"type": "function", "function": {"name": "decide_next_action"}},
            max_completion_tokens=250,
            temperature=0.3,
        )
        args = json.loads(resp.choices[0].message.tool_calls[0].function.arguments)
        action = args.get("action", "answer")
        if action not in ("answer", "search_more", "fetch_source", "clarify"):
            action = "answer"
        return {
            "action": action,
            "thought": (args.get("thought") or "").strip(),
            "query": (args.get("query") or "").strip(),
            "url": (args.get("url") or "").strip(),
            "title": (args.get("title") or "").strip(),
            "question": (args.get("question") or "").strip(),
        }
    except Exception as e:
        app.logger.warning(f"[Reflect] failed: {e}")
        return {"action": "answer", "thought": "", "query": "", "url": "", "title": "", "question": ""}


def plan_actions(user_message: str, history: list, context: dict, existing_names: set) -> dict:
    """Quick planning call: a first-person thought + any links worth pulling in now."""
    try:
        sys = (
            AGENT_PERSONA + _context_block(context) + "\n\n"
            "You are about to help the user. In 'thought', write ONE short first-person sentence about what "
            "you'll do next. In 'auto_import', include up to 2 canonical URLs that would clearly help the "
            "user's CURRENT goal and are not already among their sources. "
            f"Current sources: {sorted(existing_names) if existing_names else 'none'}."
        )
        messages = [{"role": "system", "content": sys}] + _history_messages(history) + [
            {"role": "user", "content": user_message}
        ]
        resp = client.chat.completions.create(
            model=DEPLOYMENT,
            messages=messages,
            tools=[PLAN_TOOL],
            tool_choice={"type": "function", "function": {"name": "provide_plan"}},
            max_completion_tokens=250,
            temperature=0.4,
        )
        args = json.loads(resp.choices[0].message.tool_calls[0].function.arguments)
        thought = (args.get("thought") or "").strip()
        imports = []
        seen = set()
        for link in (args.get("auto_import") or []):
            if not isinstance(link, dict):
                continue
            url = (link.get("url") or "").strip()
            title = (link.get("title") or "").strip() or url
            name = source_name_for_url(url) if url else ""
            if url.lower().startswith(("http://", "https://")) and name not in existing_names and url not in seen:
                seen.add(url)
                imports.append({"url": url, "title": title})
        return {"thought": thought, "auto_import": imports[:2]}
    except Exception as e:
        app.logger.warning(f"[Plan] failed: {e}")
        return {"thought": "", "auto_import": []}


def compose_answer(user_message: str, history: list, retrieved_chunks: list, session_id: str, context: dict):
    """Grounded answer call. Returns (structured, session_update)."""
    grounded_mode = bool(session_id)

    if retrieved_chunks:
        def _chunk_header(i, c):
            meta = c["metadata"]
            page = "" if meta["page"] == "N/A" else f", p.{meta['page']}"
            return f"[{i+1}] {meta['filename']} (part {meta['chunk_id']+1}{page})"
        context_text = "\n\n".join(
            f"{_chunk_header(i, c)}\n{c['text']}" for i, c in enumerate(retrieved_chunks)
        )
    else:
        context_text = "(no sources were retrieved)"

    example_line = ""
    if context.get("example_domain"):
        example_line = (
            f"IMPORTANT — teaching example: You are using a consistent running example this session: "
            f"\"{context['example_domain']}\". Whenever you EXPLAIN a concept, illustrate it with THIS same "
            "example. Make the mapping SPECIFIC and well-chosen, not generic: pick concrete instances within the "
            "example and map each technical part to a precise counterpart (e.g. if the example is a library and the "
            "topic is data partitioning, say something like 'the science-journals section is a topic, and each "
            "individual journal gets its own shelf — that shelf is a partition'). Avoid vague one-liners; make the "
            "analogy actually teach the mechanism. Do not switch examples unless the user asks.\n"
        )
    else:
        example_line = (
            "IMPORTANT — teaching example: Whenever you EXPLAIN a concept, include at least one concrete, "
            "SPECIFIC example or analogy that maps each key part of the concept to a precise counterpart (not a "
            "vague one-liner — make the mapping teach the mechanism). Pick a single relatable example domain and "
            "set it in session_update.example_domain so you reuse the SAME example for later concepts.\n"
        )

    system_prompt = (
        AGENT_PERSONA + _context_block(context) + "\n\n"
        + example_line +
        "GROUNDING RULES (strict): Answer the user's question using ONLY the numbered context sources "
        "below. Do NOT use outside or prior knowledge for the FACTS in your ANSWER, and do NOT answer "
        "general-knowledge questions unless the answer is explicitly present in the context. (Everyday "
        "examples/analogies you use to illustrate are fine — they don't need to come from the sources.) "
        f"If the answer is not contained in the context, set 'answer' to exactly: \"{NOT_FOUND_MSG}\" "
        "and set source_indices to an empty list — but you may still use follow_ups and suggested_links "
        "to help the user find or add the right sources. When you do answer from the context, list the "
        "numbers of the sources you actually used in source_indices.\n"
        "Also keep your session memory current via 'session_update' (including example_domain). "
        "'suggested_links' may draw on your general knowledge of authoritative sources.\n\n"
        "Context:\n" + context_text
    )

    openai_messages = [{"role": "system", "content": system_prompt}] + _history_messages(history) + [
        {"role": "user", "content": user_message}
    ]

    response = client.chat.completions.create(
        model=DEPLOYMENT,
        messages=openai_messages,
        tools=[ANSWER_TOOL],
        tool_choice={"type": "function", "function": {"name": "provide_answer"}},
        max_completion_tokens=1000,
        temperature=0.7,
    )
    structured = json.loads(response.choices[0].message.tool_calls[0].function.arguments)

    used_indices = structured.pop("source_indices", [])
    follow_ups = [s.strip() for s in (structured.pop("follow_ups", None) or []) if isinstance(s, str) and s.strip()][:3]
    session_update = structured.pop("session_update", None)

    raw_links = structured.pop("suggested_links", None) or []
    suggested_links, seen_urls = [], set()
    for link in raw_links:
        if not isinstance(link, dict):
            continue
        url = (link.get("url") or "").strip()
        title = (link.get("title") or "").strip() or url
        if url.lower().startswith(("http://", "https://")) and url not in seen_urls:
            seen_urls.add(url)
            suggested_links.append({"url": url, "title": title})
    suggested_links = suggested_links[:4]
    if session_id:
        existing_names = set(list_documents(session_id))
        suggested_links = [l for l in suggested_links if source_name_for_url(l["url"]) not in existing_names]

    seen, unique_sources = set(), []
    for idx in used_indices:
        i = idx - 1
        if i < 0 or i >= len(retrieved_chunks):
            continue
        meta = retrieved_chunks[i]["metadata"]
        key = (meta["filename"], meta["page"]) if meta["page"] != "N/A" else (meta["filename"], meta["chunk_id"])
        if key not in seen:
            seen.add(key)
            unique_sources.append({"filename": meta["filename"], "page": meta["page"], "chunk_id": meta["chunk_id"]})

    structured["sources"] = unique_sources
    structured["follow_ups"] = follow_ups
    structured["suggested_links"] = suggested_links

    if grounded_mode and not unique_sources:
        structured["answer"] = NOT_FOUND_MSG
        structured["confidence"] = 0.0
        structured["sources"] = []

    if EVALS_MODE and retrieved_chunks:
        structured["contexts"] = [c["text"] for c in retrieved_chunks]

    return structured, session_update


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
                    "description": "True if the draft genuinely helps the user learn: accurate, clear, well-grounded, with a concrete specific example where a concept is explained, and no obvious gaps. False if it is shallow, vague, generic, or misses depth the user likely wants."
                },
                "reason": {
                    "type": "string",
                    "description": "ONE short first-person sentence for the thought trace on what to improve (only if not good enough), e.g. 'The example is too generic — let me make the mapping concrete.'"
                },
                "needs_deeper_source": {
                    "type": "boolean",
                    "description": "True if a dedicated/authoritative page (deeper than what's currently retrieved) would materially improve the answer."
                },
                "fetch_url": {"type": "string", "description": "If needs_deeper_source: a real, canonical http(s) URL to fetch. Never invent URLs."},
                "fetch_title": {"type": "string", "description": "Short label for fetch_url."},
                "better_query": {"type": "string", "description": "If more retrieval from existing sources would help: a focused search query."}
            },
            "required": ["good_enough"]
        }
    }
}


def critique_answer(user_message, context, draft_answer, existing_names):
    """Quality gate: judge the draft and suggest how to improve it."""
    try:
        sys = (
            AGENT_PERSONA + _context_block(context) + "\n\n"
            "You are the quality reviewer for a learning agent. Judge the DRAFT answer below by one standard: does "
            "it give the user the BEST possible understanding? Be demanding about depth and about examples — a good "
            "answer explains the mechanism and, when explaining a concept, includes a SPECIFIC, well-mapped example "
            "(not a vague one-liner). If a dedicated/authoritative source would make it materially better than the "
            "current surface-level material, say so via needs_deeper_source (with a real canonical URL). "
            f"Current sources: {sorted(existing_names) if existing_names else 'none'}.\n\n"
            f"User asked:\n{user_message}\n\nDRAFT answer:\n{draft_answer[:2500]}"
        )
        resp = client.chat.completions.create(
            model=DEPLOYMENT,
            messages=[{"role": "system", "content": sys}],
            tools=[CRITIQUE_TOOL],
            tool_choice={"type": "function", "function": {"name": "critique_answer"}},
            max_completion_tokens=250,
            temperature=0.2,
        )
        args = json.loads(resp.choices[0].message.tool_calls[0].function.arguments)
        return {
            "good_enough": bool(args.get("good_enough", True)),
            "reason": (args.get("reason") or "").strip(),
            "needs_deeper_source": bool(args.get("needs_deeper_source", False)),
            "fetch_url": (args.get("fetch_url") or "").strip(),
            "fetch_title": (args.get("fetch_title") or "").strip(),
            "better_query": (args.get("better_query") or "").strip(),
        }
    except Exception as e:
        app.logger.warning(f"[Critique] failed: {e}")
        return {"good_enough": True, "reason": "", "needs_deeper_source": False, "fetch_url": "", "fetch_title": "", "better_query": ""}
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    user_message = data.get('message', '').strip()
    if not user_message:
        return jsonify({"error": "message is required"}), 400

    visuals = data.get('visuals', True)
    session_id = data.get('session_id')
    history = data.get('history', [])
    app.logger.info(f"[Request] session={session_id}  visuals={visuals}  message='{user_message[:80]}'")

    context = db.get_session_context(session_id) if session_id else {}
    is_first = session_id and db.is_first_message(session_id)
    if session_id:
        db.save_message(session_id, "user", [{"text": user_message}])

    try:
        retrieved_chunks = retrieve_chunks(user_message, history, session_id)
        structured, session_update = compose_answer(user_message, history, retrieved_chunks, session_id, context)

        diagram = generate_diagram(user_message, structured["answer"]) if (visuals and structured.get("sources")) else None
        structured["diagram"] = diagram

        if session_id:
            if session_update:
                db.update_session_context(session_id, _merge_session_update(context, session_update))
            db.save_message(
                session_id, "model", [{"text": structured["answer"]}],
                confidence=structured.get("confidence"),
                sources=structured.get("sources") or None,
                follow_ups=structured.get("follow_ups") or None,
                suggested_links=structured.get("suggested_links") or None,
                diagram=diagram,
            )
            if is_first:
                title = generate_title(f"User: {user_message}\nAssistant: {structured['answer'][:300]}")
                if title:
                    db.update_session_title(session_id, title)
                    structured["session_title"] = title

        return jsonify(structured)
    except Exception as e:
        app.logger.error(f"OpenAI API error: {e}")
        return jsonify({"error": "Failed to get a response from the AI service."}), 500


@app.route('/api/chat_stream', methods=['POST'])
def chat_stream():
    """Streaming agent: emits live 'thinking' steps, then the final structured answer (SSE)."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400
    user_message = (data.get('message') or '').strip()
    if not user_message:
        return jsonify({"error": "message is required"}), 400
    visuals = data.get('visuals', True)
    session_id = data.get('session_id')
    history = data.get('history', [])

    def emit(kind, text=None, steps=None, **extra):
        ev = {"type": kind}
        if text is not None:
            ev["text"] = text
        if steps is not None:
            ev["steps"] = steps
        ev.update(extra)
        return f"data: {json.dumps(ev)}\n\n"

    def gen():
        steps = []

        def step(text):
            steps.append(text)
            return emit("step", text)

        try:
            context = db.get_session_context(session_id) if session_id else {}
            is_first = session_id and db.is_first_message(session_id)
            if session_id:
                db.save_message(session_id, "user", [{"text": user_message}])

            yield step("Understanding your question")

            existing = set(list_documents(session_id)) if session_id else set()
            added = []

            # Initial retrieval, then an agentic reflect loop: the agent may search
            # again, fetch a new source, ask a clarifying question, or answer.
            chunks = retrieve_chunks(user_message, history, session_id)

            MAX_ITERS = 3
            clarify_question = None
            for iteration in range(1, MAX_ITERS + 1):
                decision = agent_reflect(user_message, history, context, chunks, existing, iteration, MAX_ITERS)
                if decision["thought"]:
                    yield step(decision["thought"])
                action = decision["action"]

                if action == "answer":
                    break

                elif action == "clarify" and decision["question"] and iteration < MAX_ITERS:
                    clarify_question = decision["question"]
                    break

                elif action == "search_more":
                    q = decision["query"] or user_message
                    yield step(f"Searching again: {q[:60]}")
                    chunks = _merge_chunks(chunks, retrieve_chunks(q, history, session_id))

                elif action == "fetch_source" and decision["url"].lower().startswith(("http://", "https://")):
                    name = source_name_for_url(decision["url"])
                    if name in existing:
                        continue
                    label = decision["title"] or decision["url"]
                    yield step(f"Sourcing “{label}”")
                    try:
                        _, new_chunks = extract_and_chunk_url(decision["url"])
                        if new_chunks:
                            embed_and_store(new_chunks, name, session_id)
                            existing.add(name)
                            added.append(name)
                            chunks = _merge_chunks(chunks, retrieve_chunks(user_message, history, session_id))
                    except Exception:
                        yield step(f"Couldn't fetch {label}")
                else:
                    break

            # If the agent needs clarification, ask it as the reply and stop here.
            if clarify_question:
                if added:
                    yield emit("sources_added", sources=added)
                structured = {
                    "answer": clarify_question,
                    "confidence": 1.0,
                    "sources": [],
                    "follow_ups": [],
                    "suggested_links": [],
                    "diagram": None,
                    "steps": steps,
                }
                if session_id:
                    db.save_message(session_id, "model", [{"text": clarify_question}], steps=steps or None)
                    if is_first:
                        title = generate_title(f"User: {user_message}\nAssistant: {clarify_question[:200]}")
                        if title:
                            db.update_session_title(session_id, title)
                            structured["session_title"] = title
                yield emit("final", **structured)
                return

            yield step("Composing the answer")
            structured, session_update = compose_answer(user_message, history, chunks, session_id, context)

            # Quality gate: critique the draft; if weak, gather more and re-compose (bounded).
            MAX_REVISIONS = 2
            for rev in range(MAX_REVISIONS):
                if not structured.get("sources"):
                    break  # not-found / ungrounded: nothing to improve by re-composing
                verdict = critique_answer(user_message, context, structured["answer"], existing)
                if verdict["good_enough"]:
                    break
                if verdict["reason"]:
                    yield step(verdict["reason"])

                improved = False
                if verdict["needs_deeper_source"] and verdict["fetch_url"].lower().startswith(("http://", "https://")):
                    name = source_name_for_url(verdict["fetch_url"])
                    if name not in existing:
                        label = verdict["fetch_title"] or verdict["fetch_url"]
                        yield step(f"Sourcing “{label}” for depth")
                        try:
                            _, new_chunks = extract_and_chunk_url(verdict["fetch_url"])
                            if new_chunks:
                                embed_and_store(new_chunks, name, session_id)
                                existing.add(name)
                                added.append(name)
                                improved = True
                        except Exception:
                            yield step(f"Couldn't fetch {label}")
                if verdict["better_query"]:
                    before = len(chunks)
                    chunks = _merge_chunks(chunks, retrieve_chunks(verdict["better_query"], history, session_id))
                    improved = improved or len(chunks) > before
                elif improved:
                    chunks = _merge_chunks(chunks, retrieve_chunks(user_message, history, session_id))

                if not improved:
                    break
                yield step("Improving the answer")
                structured, session_update = compose_answer(user_message, history, chunks, session_id, context)

            if added:
                yield emit("sources_added", sources=added)

            diagram = None
            if visuals and structured.get("sources"):
                yield step("Sketching a diagram")
                example_domain = (session_update or {}).get("example_domain") or context.get("example_domain")
                diagram = generate_diagram(user_message, structured["answer"], example_domain)
            structured["diagram"] = diagram
            structured["steps"] = steps

            if session_id:
                if session_update:
                    context = _merge_session_update(context, session_update)
                    db.update_session_context(session_id, context)
                db.save_message(
                    session_id, "model", [{"text": structured["answer"]}],
                    confidence=structured.get("confidence"),
                    sources=structured.get("sources") or None,
                    follow_ups=structured.get("follow_ups") or None,
                    suggested_links=structured.get("suggested_links") or None,
                    diagram=diagram,
                    steps=steps or None,
                )
                if is_first:
                    title = generate_title(f"User: {user_message}\nAssistant: {structured['answer'][:300]}")
                    if title:
                        db.update_session_title(session_id, title)
                        structured["session_title"] = title

            yield emit("final", **structured)
        except Exception as e:
            app.logger.error(f"chat_stream error: {e}")
            yield emit("error", text="Failed to get a response from the AI service.")

    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

# ─── Sessions ─────────────────────────────────────────────────────────────────

@app.route('/api/sessions', methods=['GET'])
def get_sessions():
    return jsonify({"sessions": db.list_sessions()})


@app.route('/api/sessions', methods=['POST'])
def create_session():
    session = db.create_session()
    return jsonify(session), 201


@app.route('/api/sessions/<session_id>', methods=['GET'])
def get_session(session_id):
    messages = db.get_session_messages(session_id)
    return jsonify({"messages": messages})


@app.route('/api/sessions/<session_id>', methods=['DELETE'])
def delete_session_route(session_id):
    db.delete_session(session_id)
    delete_session_documents(session_id)

    # Remove session's docs directory from disk
    session_dir = os.path.join(DOCS_DIR, session_id)
    if os.path.isdir(session_dir):
        import shutil
        shutil.rmtree(session_dir)

    return jsonify({"status": "deleted"})

# ─── Documents ────────────────────────────────────────────────────────────────

@app.route('/api/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files['file']
    if not file.filename:
        return jsonify({"error": "No file selected"}), 400

    session_id = request.form.get("session_id")
    if not session_id:
        return jsonify({"error": "session_id is required"}), 400

    ext = file.filename.rsplit('.', 1)[-1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({"error": "Unsupported file type. Use PDF or TXT."}), 400

    file.seek(0, 2)
    size = file.tell()
    file.seek(0)
    if size > MAX_FILE_SIZE:
        return jsonify({"error": "File exceeds 5 MB limit."}), 400

    filename = secure_filename(file.filename)
    session_dir = os.path.join(DOCS_DIR, session_id)
    os.makedirs(session_dir, exist_ok=True)
    filepath = os.path.join(session_dir, filename)
    file.save(filepath)

    try:
        chunks = extract_and_chunk(filepath, filename)
        embed_and_store(chunks, filename, session_id)
        visuals = (request.form.get("visuals", "true").lower() != "false")
        summary = summarize_and_save(session_id, filename, chunks, visuals)
        return jsonify({
            "status": "indexed",
            "filename": filename,
            "chunks_indexed": len(chunks),
            "summary": summary,
        })
    except Exception as e:
        app.logger.error(f"Ingestion error: {e}")
        return jsonify({"error": "Failed to process document."}), 500


@app.route('/api/upload_url', methods=['POST'])
def upload_url():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    url = (data.get("url") or "").strip()
    session_id = data.get("session_id")

    if not url:
        return jsonify({"error": "url is required"}), 400
    if not session_id:
        return jsonify({"error": "session_id is required"}), 400

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return jsonify({"error": "Provide a valid http(s) URL."}), 400

    # Dedup: if this URL canonicalizes to an already-added source, skip re-ingesting.
    name = source_name_for_url(url)
    if name in list_documents(session_id):
        app.logger.info(f"[Ingest] URL '{url}' already added as '{name}' — skipping")
        return jsonify({"status": "exists", "filename": name, "chunks_indexed": 0})

    try:
        name, chunks = extract_and_chunk_url(url)
        if not chunks:
            return jsonify({"error": "No readable content found at that URL."}), 422
        embed_and_store(chunks, name, session_id)
        visuals = bool(data.get("visuals", True))
        summary = summarize_and_save(session_id, name, chunks, visuals)
        return jsonify({
            "status": "indexed",
            "filename": name,
            "chunks_indexed": len(chunks),
            "summary": summary,
        })
    except Exception as e:
        app.logger.error(f"URL ingestion error: {e}")
        return jsonify({"error": "Failed to fetch or process that URL."}), 500


@app.route('/api/docs', methods=['GET'])
def get_docs():
    session_id = request.args.get("session_id")
    if not session_id:
        return jsonify({"error": "session_id is required"}), 400
    return jsonify({"documents": list_documents(session_id)})


@app.route('/api/docs/<filename>', methods=['DELETE'])
def delete_doc(filename):
    session_id = request.args.get("session_id")
    if not session_id:
        return jsonify({"error": "session_id is required"}), 400

    delete_document(filename, session_id)

    filepath = os.path.join(DOCS_DIR, session_id, filename)
    if os.path.exists(filepath):
        os.remove(filepath)

    return jsonify({"status": "deleted", "filename": filename})


if __name__ == '__main__':
    app.run(debug=True, port=5000, threaded=True)
