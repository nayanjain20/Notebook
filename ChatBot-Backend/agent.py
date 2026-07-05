"""The agent's reasoning brain.

A user question is not answered in one shot. Instead the agent runs a small
loop: it retrieves context, reflects on whether that context is good enough,
and may search again, fetch a new source, or ask a clarifying question before
composing an answer. After drafting, a quality gate critiques the answer and
may improve it. Diagrams are added only when they genuinely aid understanding.

``run_agent_stream`` is the orchestrator: it yields event dicts describing each
step so the API can stream the agent's "thinking" to the user in real time.
"""

import logging

import config
import db
import helpers
import llm
import prompts
from ingestion import (
    advanced_search, embed_and_store, extract_and_chunk_url, list_documents, source_name_for_url,
)

logger = logging.getLogger(__name__)


# ─── Retrieval ────────────────────────────────────────────────────────────────

def retrieve_chunks(query: str, history: list, session_id: str) -> list:
    """Retrieve relevant chunks for a query from the session's sources."""
    if not session_id:
        return []
    chunks = advanced_search(query, history=history, session_id=session_id)
    logger.info("[Retrieve] '%s' → %d chunks", query[:60], len(chunks))
    return chunks


# ─── Diagrams ─────────────────────────────────────────────────────────────────

def generate_diagram(question: str, answer: str, example_domain: str = None) -> dict | None:
    """Return ``{mermaid, caption}`` when a diagram aids understanding, else ``None``.

    A separate, conservative model call decides this — most answers get no diagram.
    If a running example is in play, the diagram may be framed around it.
    """
    example_hint = ""
    if example_domain:
        example_hint = (
            f" A running example is in play for this session: \"{example_domain}\". If you draw a diagram, you may "
            "make it EITHER a technical diagram of the concept OR a diagram of the example itself (whichever teaches "
            "better) — and label nodes using the example's concrete terms so the picture matches the explanation."
        )
    system = (
        "You decide whether an explanation needs a diagram, and you are VERY conservative — the large majority of "
        "answers need NO diagram. Only produce a Mermaid diagram when the answer is fundamentally about how things "
        "RELATE or CONNECT: a system architecture, how components interact, an entity/relationship, or a hierarchy. "
        "Do NOT diagram lists of steps, how-tos, procedures, definitions, clarifications, comparisons, opinions, or "
        "simple explanations — return needs_diagram=false for those. A list of steps is NOT a reason to draw a "
        "diagram. When in doubt, return false. If you do make one, output valid, minimal Mermaid with short labels; "
        "avoid parentheses and special characters inside node text." + example_hint
    )
    try:
        args = llm.call_tool(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": f"Question:\n{question}\n\nAnswer:\n{answer[:2500]}"},
            ],
            prompts.DIAGRAM_TOOL,
            max_tokens=600,
            temperature=0.3,
        )
        if not args.get("needs_diagram"):
            return None
        mermaid = helpers.clean_mermaid(args.get("mermaid", ""))
        if not mermaid:
            return None
        logger.info("[Diagram] Generated (%s…)", mermaid.splitlines()[0][:40])
        return {"mermaid": mermaid, "caption": (args.get("caption") or "").strip()}
    except Exception as exc:
        logger.warning("[Diagram] Generation failed: %s", exc)
        return None


# ─── Reflection (decide the next action) ──────────────────────────────────────

def reflect(user_message, history, context, chunks, source_names, iteration, max_iterations) -> dict:
    """Decide the single next action to produce the best possible answer.

    Returns a dict with ``action`` (answer / search_more / fetch_source / clarify)
    plus any parameters that action needs.
    """
    system = (
        prompts.AGENT_PERSONA + prompts.render_session_memory(context) + "\n\n"
        "You are an autonomous learning agent. You have gathered some context from the user's sources (below). "
        "Decide the SINGLE next action to produce the BEST possible answer — not merely an acceptable one. "
        "Do NOT settle just because you found something relevant: the user's current source may be surface-level "
        "while a dedicated/linked resource holds far richer material. Ask 'what would make this answer genuinely "
        "excellent?' and act on it:\n"
        "- 'search_more' if the passages are thin or off-target (give a sharper query).\n"
        "- 'fetch_source' if a specific canonical page (e.g. official docs, a dedicated guide) would give "
        "materially deeper material and isn't already a source. Prefer this over answering from shallow context. "
        "Never invent URLs.\n"
        "- 'clarify' ONLY if the request is genuinely ambiguous and you cannot proceed without one short question.\n"
        "- 'answer' when you have rich, sufficient material to teach this well.\n"
        f"This is reflection step {iteration} of at most {max_iterations}. If near the limit, answer.\n"
        f"Current sources: {sorted(source_names) if source_names else 'none'}.\n\n"
        f"Gathered context:\n{helpers.chunks_digest(chunks)}"
    )
    messages = [{"role": "system", "content": system}] + helpers.history_to_messages(history) + [
        {"role": "user", "content": user_message}
    ]
    try:
        args = llm.call_tool(messages, prompts.REFLECT_TOOL, max_tokens=250, temperature=0.3)
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
    except Exception as exc:
        logger.warning("[Reflect] failed: %s", exc)
        return {"action": "answer", "thought": "", "query": "", "url": "", "title": "", "question": ""}


# ─── Compose the grounded answer ──────────────────────────────────────────────

def _teaching_example_instruction(context: dict) -> str:
    """Instruction that pushes the model to teach with a specific, consistent example."""
    if context.get("example_domain"):
        return (
            f"IMPORTANT — teaching example: You are using a consistent running example this session: "
            f"\"{context['example_domain']}\". Whenever you EXPLAIN a concept, illustrate it with THIS same example. "
            "Make the mapping SPECIFIC, not generic: map each technical part to a precise counterpart (e.g. if the "
            "example is a library and the topic is data partitioning, 'the science-journals section is a topic, and "
            "each journal gets its own shelf — that shelf is a partition'). Avoid vague one-liners; make the analogy "
            "actually teach the mechanism. Do not switch examples unless the user asks.\n"
        )
    return (
        "IMPORTANT — teaching example: Whenever you EXPLAIN a concept, include at least one concrete, SPECIFIC "
        "example or analogy that maps each key part of the concept to a precise counterpart (not a vague one-liner). "
        "Pick a single relatable example domain and set it in session_update.example_domain so you reuse the SAME "
        "example for later concepts.\n"
    )


def _format_context(chunks: list) -> str:
    """Format retrieved chunks as numbered, citable context for the answer prompt."""
    if not chunks:
        return "(no sources were retrieved)"
    lines = []
    for i, chunk in enumerate(chunks):
        meta = chunk["metadata"]
        page = "" if meta["page"] == "N/A" else f", p.{meta['page']}"
        header = f"[{i + 1}] {meta['filename']} (part {meta['chunk_id'] + 1}{page})"
        lines.append(f"{header}\n{chunk['text']}")
    return "\n\n".join(lines)


def compose_answer(user_message: str, history: list, chunks: list, session_id: str, context: dict):
    """Produce a grounded, structured answer from the retrieved chunks.

    Returns ``(structured, session_update)`` where ``structured`` carries the
    answer, confidence, cited sources, follow-ups, and suggested links.
    """
    system_prompt = (
        prompts.AGENT_PERSONA + prompts.render_session_memory(context) + "\n\n"
        + _teaching_example_instruction(context) +
        "GROUNDING RULES (strict): Answer using ONLY the numbered context sources below. Do NOT use outside or "
        "prior knowledge for the FACTS in your ANSWER, and do NOT answer general-knowledge questions unless the "
        "answer is explicitly present in the context. (Everyday examples/analogies you use to illustrate are fine "
        "— they don't need to come from the sources.) "
        f"If the answer is not contained in the context, set 'answer' to exactly: \"{prompts.NOT_FOUND_MESSAGE}\" "
        "and set source_indices to an empty list — but you may still use follow_ups and suggested_links to help the "
        "user find or add the right sources. When you answer from the context, list the source numbers you actually "
        "used in source_indices.\n"
        "Also keep your session memory current via 'session_update' (including example_domain).\n\n"
        "Context:\n" + _format_context(chunks)
    )
    messages = [{"role": "system", "content": system_prompt}] + helpers.history_to_messages(history) + [
        {"role": "user", "content": user_message}
    ]
    structured = llm.call_tool(messages, prompts.ANSWER_TOOL, max_tokens=1000, temperature=0.7)

    used_indices = structured.pop("source_indices", [])
    session_update = structured.pop("session_update", None)
    structured["follow_ups"] = [
        s.strip() for s in (structured.pop("follow_ups", None) or []) if isinstance(s, str) and s.strip()
    ][:3]
    structured["suggested_links"] = _clean_suggested_links(structured.pop("suggested_links", None), session_id)
    structured["sources"] = _cited_sources(used_indices, chunks)

    # In grounded mode, a cited-source-free answer means it wasn't grounded — refuse.
    if session_id and not structured["sources"]:
        structured["answer"] = prompts.NOT_FOUND_MESSAGE
        structured["confidence"] = 0.0
        structured["sources"] = []

    if config.EVALS_MODE and chunks:
        structured["contexts"] = [c["text"] for c in chunks]

    return structured, session_update


def _clean_suggested_links(raw_links, session_id: str) -> list:
    """Validate suggested links and drop any the session already has."""
    links, seen = [], set()
    for link in (raw_links or []):
        if not isinstance(link, dict):
            continue
        url = (link.get("url") or "").strip()
        title = (link.get("title") or "").strip() or url
        if url.lower().startswith(("http://", "https://")) and url not in seen:
            seen.add(url)
            links.append({"url": url, "title": title})
    links = links[:4]
    if session_id:
        existing = set(list_documents(session_id))
        links = [l for l in links if source_name_for_url(l["url"]) not in existing]
    return links


def _cited_sources(used_indices, chunks: list) -> list:
    """Map the model's 1-based source indices to unique source citations."""
    seen, sources = set(), []
    for idx in used_indices:
        i = idx - 1
        if i < 0 or i >= len(chunks):
            continue
        meta = chunks[i]["metadata"]
        key = (meta["filename"], meta["page"]) if meta["page"] != "N/A" else (meta["filename"], meta["chunk_id"])
        if key not in seen:
            seen.add(key)
            sources.append({"filename": meta["filename"], "page": meta["page"], "chunk_id": meta["chunk_id"]})
    return sources


# ─── Quality gate ─────────────────────────────────────────────────────────────

def critique_answer(user_message: str, context: dict, draft_answer: str, source_names) -> dict:
    """Judge a drafted answer and suggest how to improve it (deeper source / better query)."""
    system = (
        prompts.AGENT_PERSONA + prompts.render_session_memory(context) + "\n\n"
        "You are the quality reviewer for a learning agent. Judge the DRAFT answer below by one standard: does it "
        "give the user the BEST possible understanding? Be demanding about depth and about examples — a good answer "
        "explains the mechanism and, when explaining a concept, includes a SPECIFIC, well-mapped example (not a "
        "vague one-liner). If a dedicated/authoritative source would make it materially better than the current "
        "surface-level material, say so via needs_deeper_source (with a real canonical URL). "
        f"Current sources: {sorted(source_names) if source_names else 'none'}.\n\n"
        f"User asked:\n{user_message}\n\nDRAFT answer:\n{draft_answer[:2500]}"
    )
    try:
        args = llm.call_tool([{"role": "system", "content": system}], prompts.CRITIQUE_TOOL, max_tokens=250, temperature=0.2)
        return {
            "good_enough": bool(args.get("good_enough", True)),
            "reason": (args.get("reason") or "").strip(),
            "needs_deeper_source": bool(args.get("needs_deeper_source", False)),
            "fetch_url": (args.get("fetch_url") or "").strip(),
            "fetch_title": (args.get("fetch_title") or "").strip(),
            "better_query": (args.get("better_query") or "").strip(),
        }
    except Exception as exc:
        logger.warning("[Critique] failed: %s", exc)
        return {"good_enough": True, "reason": "", "needs_deeper_source": False, "fetch_url": "", "fetch_title": "", "better_query": ""}


# ─── Source fetching (used by the agent, not the user) ────────────────────────

def _fetch_and_store(url: str, session_id: str, source_names: set) -> str | None:
    """Fetch a URL, embed it into the session, and return its source name (or None)."""
    name = source_name_for_url(url)
    if name in source_names:
        return None
    _, chunks = extract_and_chunk_url(url)
    if not chunks:
        return None
    embed_and_store(chunks, name, session_id)
    source_names.add(name)
    return name


# ─── Orchestrator ─────────────────────────────────────────────────────────────

def run_agent_stream(user_message: str, history: list, session_id: str, visuals: bool):
    """Run the full agent turn, yielding event dicts as it works.

    Event types yielded:
      - ``{"type": "step", "text": ...}``            a thinking-trace line
      - ``{"type": "sources_added", "sources": [...]}``  sources the agent fetched
      - ``{"type": "final", ...structured}``         the finished answer
      - ``{"type": "error", "text": ...}``           a failure
    """
    steps = []

    def step(text: str) -> dict:
        steps.append(text)
        return {"type": "step", "text": text}

    try:
        context = db.get_session_context(session_id) if session_id else {}
        is_first = session_id and db.is_first_message(session_id)
        if session_id:
            db.save_message(session_id, "user", [{"text": user_message}])

        yield step("Understanding your question")

        source_names = set(list_documents(session_id)) if session_id else set()
        added = []
        chunks = retrieve_chunks(user_message, history, session_id)

        # Reflect loop: search again, fetch a source, clarify, or answer.
        clarify_question = None
        for iteration in range(1, config.MAX_REFLECT_ITERATIONS + 1):
            decision = reflect(user_message, history, context, chunks, source_names, iteration, config.MAX_REFLECT_ITERATIONS)
            if decision["thought"]:
                yield step(decision["thought"])
            action = decision["action"]

            if action == "answer":
                break
            if action == "clarify" and decision["question"] and iteration < config.MAX_REFLECT_ITERATIONS:
                clarify_question = decision["question"]
                break
            if action == "search_more":
                query = decision["query"] or user_message
                yield step(f"Searching again: {query[:60]}")
                chunks = helpers.merge_chunks(chunks, retrieve_chunks(query, history, session_id))
            elif action == "fetch_source" and decision["url"].lower().startswith(("http://", "https://")):
                label = decision["title"] or decision["url"]
                yield step(f"Sourcing “{label}”")
                try:
                    name = _fetch_and_store(decision["url"], session_id, source_names)
                    if name:
                        added.append(name)
                        chunks = helpers.merge_chunks(chunks, retrieve_chunks(user_message, history, session_id))
                except Exception:
                    yield step(f"Couldn't fetch {label}")
            else:
                break

        # Clarifying question: ask it and stop this turn here.
        if clarify_question:
            if added:
                yield {"type": "sources_added", "sources": added}
            yield _finalize_clarification(session_id, is_first, user_message, clarify_question, steps)
            return

        yield step("Composing the answer")
        structured, session_update = compose_answer(user_message, history, chunks, session_id, context)

        # Quality gate: critique and, if weak, gather more and re-compose (bounded).
        for _ in range(config.MAX_ANSWER_REVISIONS):
            if not structured.get("sources"):
                break
            verdict = critique_answer(user_message, context, structured["answer"], source_names)
            if verdict["good_enough"]:
                break
            if verdict["reason"]:
                yield step(verdict["reason"])

            improved = False
            if verdict["needs_deeper_source"] and verdict["fetch_url"].lower().startswith(("http://", "https://")):
                label = verdict["fetch_title"] or verdict["fetch_url"]
                yield step(f"Sourcing “{label}” for depth")
                try:
                    name = _fetch_and_store(verdict["fetch_url"], session_id, source_names)
                    if name:
                        added.append(name)
                        improved = True
                except Exception:
                    yield step(f"Couldn't fetch {label}")
            if verdict["better_query"]:
                before = len(chunks)
                chunks = helpers.merge_chunks(chunks, retrieve_chunks(verdict["better_query"], history, session_id))
                improved = improved or len(chunks) > before
            elif improved:
                chunks = helpers.merge_chunks(chunks, retrieve_chunks(user_message, history, session_id))

            if not improved:
                break
            yield step("Improving the answer")
            structured, session_update = compose_answer(user_message, history, chunks, session_id, context)

        if added:
            yield {"type": "sources_added", "sources": added}

        # Optional visual.
        diagram = None
        if visuals and structured.get("sources"):
            yield step("Sketching a diagram")
            example_domain = (session_update or {}).get("example_domain") or context.get("example_domain")
            diagram = generate_diagram(user_message, structured["answer"], example_domain)
        structured["diagram"] = diagram
        structured["steps"] = steps

        if session_id:
            if session_update:
                context = helpers.merge_session_update(context, session_update)
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
                title = onboarding_title(user_message, structured["answer"])
                if title:
                    db.update_session_title(session_id, title)
                    structured["session_title"] = title

        yield {"type": "final", **structured}
    except Exception as exc:
        logger.error("run_agent_stream error: %s", exc)
        yield {"type": "error", "text": "Failed to get a response from the AI service."}


def _finalize_clarification(session_id, is_first, user_message, question, steps) -> dict:
    """Persist and package a clarifying question as the turn's final event."""
    structured = {
        "answer": question,
        "confidence": 1.0,
        "sources": [],
        "follow_ups": [],
        "suggested_links": [],
        "diagram": None,
        "steps": steps,
    }
    if session_id:
        db.save_message(session_id, "model", [{"text": question}], steps=steps or None)
        if is_first:
            title = onboarding_title(user_message, question)
            if title:
                db.update_session_title(session_id, title)
                structured["session_title"] = title
    return {"type": "final", **structured}


def onboarding_title(user_message: str, answer: str) -> str:
    """Title a brand-new session from its first exchange."""
    import onboarding
    return onboarding.generate_title(f"User: {user_message}\nAssistant: {answer[:300]}")
