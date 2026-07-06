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
import diagram
import helpers
import llm
import prompts
import providers
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
    """Produce a diagram for an answer via the diagram skill (see ``diagram.py``)."""
    return diagram.generate(question, answer, example_domain)


# ─── Reflection (decide the next action) ──────────────────────────────────────

def reflect(user_message, history, context, chunks, source_names, iteration, max_iterations,
            searched_queries=None, search_exhausted=False) -> dict:
    """Decide the single next action to produce the best possible answer.

    Returns a dict with ``action`` (answer / search_more / fetch_source / clarify)
    plus any parameters that action needs.

    ``searched_queries`` (already-run searches) and ``search_exhausted`` (a search
    that added nothing new) steer the agent away from re-grinding the same sources:
    when the current corpus is tapped out, the way to a better answer is to bring
    in a NEW external source, not to search again.
    """
    already = sorted(searched_queries) if searched_queries else []
    exhausted_note = ""
    if search_exhausted:
        exhausted_note = (
            "\nIMPORTANT: Re-searching your CURRENT sources will NOT surface anything new — you've already mined "
            "them. If the material is still thin for a great answer, do NOT 'search_more' again; instead "
            "'fetch_source' a NEW authoritative page that isn't already a source, or 'answer' with what you have.\n"
        )
    system = (
        prompts.AGENT_PERSONA + prompts.render_session_memory(context) + "\n\n"
        "You are an autonomous learning agent. You have gathered some context from the user's sources (below). "
        "Decide the SINGLE next action to produce the BEST possible answer — not merely an acceptable one. "
        "Do NOT settle just because you found something relevant: the user's current source may be surface-level "
        "while a dedicated resource holds far richer material. Ask 'what would make this answer genuinely "
        "excellent?' and act on it:\n"
        "- 'search_more' if the passages are thin or off-target AND you have not already searched that angle "
        "(give a genuinely different, sharper query).\n"
        "- 'fetch_source' if a specific canonical page (e.g. official docs, a dedicated guide) would give "
        "materially deeper material and isn't already a source. Prefer this over re-searching shallow context. "
        "Never invent URLs — only propose a real, canonical URL you are confident exists.\n"
        "- 'clarify' ONLY if the request is genuinely ambiguous and you cannot proceed without one short question.\n"
        "- 'answer' when you have rich, sufficient material to teach this well.\n"
        f"This is reflection step {iteration} of at most {max_iterations}.\n"
        f"Current sources: {sorted(source_names) if source_names else 'none'}.\n"
        f"Queries already tried (do NOT repeat these): {already if already else 'none'}.\n"
        + exhausted_note +
        f"\nGathered context:\n{helpers.chunks_digest(chunks)}"
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


def _normalize_confidence(value) -> float:
    """Coerce a model confidence into [0, 1]. Some models return a percentage."""
    try:
        c = float(value)
    except (TypeError, ValueError):
        return 0.8
    if c > 1.0:
        c = c / 100.0
    return max(0.0, min(1.0, c))


def compose_answer(user_message: str, history: list, chunks: list, session_id: str, context: dict):
    """Produce a grounded answer plus its structured metadata.

    Uses two calls, which is far more reliable than cramming both into one
    constrained-JSON response (small local models truncate the answer badly when
    they must also fill a complex schema):
      1. free-form markdown answer (complete, well-formatted);
      2. a small structured call for citations, confidence, follow-ups, links,
         and session-memory updates about that answer.

    Returns ``(structured, session_update)``.
    """
    grounding = (
        prompts.AGENT_PERSONA + prompts.render_session_memory(context) + "\n\n"
        + _teaching_example_instruction(context) +
        "GROUNDING RULES (strict): Answer using ONLY the numbered context sources below. Do NOT use outside or "
        "prior knowledge for the FACTS in your ANSWER, and do NOT answer general-knowledge questions unless the "
        "answer is explicitly present in the context. (Everyday examples/analogies you use to illustrate are fine "
        "— they don't need to come from the sources.) "
        f"If the answer is not contained in the context, reply with exactly: \"{prompts.NOT_FOUND_MESSAGE}\"\n\n"
        "Context:\n" + _format_context(chunks)
    )

    # 1) Free-form answer — reliable and complete across models.
    answer_messages = [{"role": "system", "content": grounding}] + helpers.history_to_messages(history) + [
        {"role": "user", "content": user_message}
    ]
    answer = llm.call_text(answer_messages, max_tokens=1200, temperature=0.7)

    # 2) Structured metadata about that answer.
    meta_system = (
        prompts.render_session_memory(context) +
        "\n\nYou are extracting metadata for an answer a study assistant just gave. Using the numbered context and "
        "the drafted answer below, report which context sources the answer used (source_indices), your confidence "
        "(0.0-1.0), useful follow-up questions, any authoritative links, and session-memory updates.\n\n"
        "Context:\n" + _format_context(chunks) +
        "\n\nDrafted answer:\n" + answer
    )
    try:
        meta = llm.call_tool(
            [{"role": "system", "content": meta_system}, {"role": "user", "content": user_message}],
            prompts.ANSWER_META_TOOL, max_tokens=500, temperature=0.2,
        )
    except Exception as exc:
        logger.warning("[Compose] metadata call failed: %s", exc)
        meta = {}

    structured = {"answer": answer, "confidence": _normalize_confidence(meta.get("confidence", 0.8))}
    used_indices = meta.get("source_indices") or []
    session_update = meta.get("session_update") or None
    structured["follow_ups"] = [
        s.strip() for s in (meta.get("follow_ups") or []) if isinstance(s, str) and s.strip()
    ][:3]
    structured["suggested_links"] = _clean_suggested_links(meta.get("suggested_links"), session_id)
    structured["sources"] = _cited_sources(used_indices, chunks)

    # Grounding guard. A grounded answer must cite its sources. But models
    # (especially local ones) sometimes give a real, context-based answer yet
    # forget to fill source_indices. Distinguish the two cases:
    #   - answer looks substantive AND we did retrieve chunks → attribute the
    #     retrieved passages rather than wrongly reporting "nothing found".
    #   - otherwise (no answer / explicit refusal / no chunks) → refuse.
    if session_id and not structured["sources"]:
        answer_text = (structured.get("answer") or "").strip()
        looks_answered = bool(answer_text) and prompts.NOT_FOUND_MESSAGE not in answer_text
        if chunks and looks_answered:
            structured["sources"] = _fallback_sources(chunks)
            logger.info("[Compose] Model omitted citations; attributed %d retrieved source(s)", len(structured["sources"]))
        else:
            structured["answer"] = prompts.NOT_FOUND_MESSAGE
            structured["confidence"] = 0.0
            structured["sources"] = []

    if config.EVALS_MODE and chunks:
        structured["contexts"] = [c["text"] for c in chunks]

    return structured, session_update


def _fallback_sources(chunks: list, limit: int = 3) -> list:
    """Attribute the top retrieved passages when the model answered but didn't cite.

    Used only as a safety net: the answer prompt requires using ONLY the provided
    context, so the top reranked chunks are the material the answer drew from.
    """
    seen, sources = set(), []
    for chunk in chunks[:limit]:
        meta = chunk["metadata"]
        key = (meta["filename"], meta["page"]) if meta["page"] != "N/A" else (meta["filename"], meta["chunk_id"])
        if key not in seen:
            seen.add(key)
            sources.append({"filename": meta["filename"], "page": meta["page"], "chunk_id": meta["chunk_id"]})
    return sources


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

        # Reflect loop: search again, fetch a NEW source, clarify, or answer.
        # Iterations are intentionally NOT capped short for local models — they're
        # cheap and we want the best answer. Instead we keep every iteration
        # *productive*: never re-run the same search or re-fetch an owned/failed
        # source; when the current corpus is exhausted, escalate to fetching new
        # external material.
        clarify_question = None
        searched_queries = set()
        failed_urls = set()
        search_exhausted = False
        for iteration in range(1, config.MAX_REFLECT_ITERATIONS + 1):
            decision = reflect(
                user_message, history, context, chunks, source_names, iteration,
                config.MAX_REFLECT_ITERATIONS, searched_queries, search_exhausted,
            )
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
                qkey = query.strip().lower()
                if qkey in searched_queries:
                    # Same corpus, same query — can't add anything. Escalate next.
                    search_exhausted = True
                    continue
                searched_queries.add(qkey)
                yield step(f"Searching again: {query[:60]}")
                before = len(chunks)
                chunks = helpers.merge_chunks(chunks, retrieve_chunks(query, history, session_id))
                if len(chunks) == before:
                    # Retrieval surfaced nothing new — this corpus is tapped out.
                    search_exhausted = True
            elif action == "fetch_source" and decision["url"].lower().startswith(("http://", "https://")):
                url = decision["url"]
                # Already have this source? Re-fetching won't help — nudge to escalate.
                if source_name_for_url(url) in source_names:
                    search_exhausted = True
                    continue
                if url in failed_urls:
                    continue
                label = decision["title"] or url
                yield step(f"Sourcing “{label}”")
                try:
                    name = _fetch_and_store(url, session_id, source_names)
                    if name:
                        added.append(name)
                        search_exhausted = False  # new material to mine
                        chunks = helpers.merge_chunks(chunks, retrieve_chunks(user_message, history, session_id))
                    else:
                        failed_urls.add(url)
                except Exception:
                    failed_urls.add(url)
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
                url = verdict["fetch_url"]
                # Only fetch a genuinely new source (not one we already have or that failed).
                if source_name_for_url(url) not in source_names and url not in failed_urls:
                    label = verdict["fetch_title"] or url
                    yield step(f"Sourcing “{label}” for depth")
                    try:
                        name = _fetch_and_store(url, session_id, source_names)
                        if name:
                            added.append(name)
                            improved = True
                        else:
                            failed_urls.add(url)
                    except Exception:
                        failed_urls.add(url)
                        yield step(f"Couldn't fetch {label}")
            if verdict["better_query"] and verdict["better_query"].strip().lower() not in searched_queries:
                searched_queries.add(verdict["better_query"].strip().lower())
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

        # Optional visual (the diagram skill; local models split this into an
        # extra planning step, so surface that in the trace).
        diagram_result = None
        if visuals and structured.get("sources"):
            if providers.active_chat_is_local():
                yield step("Planning a diagram")
                yield step("Drawing the diagram")
            else:
                yield step("Sketching a diagram")
            example_domain = (session_update or {}).get("example_domain") or context.get("example_domain")
            diagram_result = generate_diagram(user_message, structured["answer"], example_domain)
        structured["diagram"] = diagram_result
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
                diagram=diagram_result,
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
