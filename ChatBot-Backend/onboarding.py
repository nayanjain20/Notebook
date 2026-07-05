"""The "a source was added" flow.

When the user adds a document or link, Notebook reacts like a teacher receiving
new material: for the *first* source it gives a tiny gist plus "how can I help?"
options; for *later* sources it gives a short acknowledgement tied to the goal.
No long summaries — the aim is to start the conversation, not to lecture.
"""

import logging

import db
import helpers
import llm
import prompts

logger = logging.getLogger(__name__)


def generate_intro(filename: str, chunks: list) -> dict:
    """First source: a tiny idea of the document plus a few "how can I help?" options."""
    system = (
        prompts.AGENT_PERSONA + "\n\nA user just added their first source. Give only a TINY idea of what it "
        "covers (2-3 sentences, no long summary), then propose 3-4 concrete things they might want to do with "
        "it (phrased as the user would ask). Keep it warm and short."
    )
    try:
        args = llm.call_tool(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": f"Source: {filename}\n\n{helpers.sample_text(chunks)}"},
            ],
            prompts.INTRO_TOOL,
            max_tokens=400,
        )
        idea = (args.get("idea") or "").strip()
        options = [s.strip() for s in (args.get("options") or []) if isinstance(s, str) and s.strip()][:4]
        return {"idea": idea, "options": options}
    except Exception as exc:
        logger.warning("[Intro] failed for '%s': %s", filename, exc)
        return {"idea": "", "options": []}


def generate_acknowledgement(filename: str, chunks: list, context: dict) -> str:
    """Later source: a short acknowledgement tied to the user's goal (no full summary)."""
    system = (
        prompts.AGENT_PERSONA + prompts.render_session_memory(context) + "\n\nThe user just added another "
        "source. In 1-2 short sentences, acknowledge it and say briefly what it adds and how it helps their "
        "goal. Do NOT write a full summary."
    )
    try:
        return llm.call_text(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": f"New source: {filename}\n\n{helpers.sample_text(chunks, 4000)}"},
            ],
            max_tokens=160,
        )
    except Exception as exc:
        logger.warning("[Ack] failed for '%s': %s", filename, exc)
        return ""


def generate_title(text: str) -> str:
    """A concise 3-6 word session title derived from some text."""
    try:
        return llm.call_text(
            [
                {"role": "system", "content": "Generate a concise 3-6 word title capturing the topic. Return ONLY the title — no quotes, no trailing punctuation."},
                {"role": "user", "content": text[:600]},
            ],
            max_tokens=20,
        )
    except Exception:
        return ""


def announce_new_source(session_id: str, filename: str, chunks: list) -> dict | None:
    """Post the assistant's reaction to a newly added source and persist it.

    On the first source, also titles the session. Returns the saved message
    payload (for the frontend), or ``None`` if nothing could be generated.
    """
    is_first = db.is_first_message(session_id)

    if is_first:
        intro = generate_intro(filename, chunks)
        if not intro["idea"]:
            return None
        text = f"{intro['idea']}\n\nHow can I help you with this?"
        options = intro["options"]
        db.save_message(session_id, "model", [{"text": text}], follow_ups=options or None)
        title = generate_title(intro["idea"]) or filename
        db.update_session_title(session_id, title)
        logger.info("[Intro] Onboarded session %s… from '%s'", session_id[:8], filename)
        return {"role": "model", "text": text, "follow_ups": options, "session_title": title}

    context = db.get_session_context(session_id)
    ack = generate_acknowledgement(filename, chunks, context)
    if not ack:
        ack = f"Added **{filename}** — I'll use it alongside your other sources."
    db.save_message(session_id, "model", [{"text": ack}])
    logger.info("[Ack] Acknowledged new source '%s' in session %s…", filename, session_id[:8])
    return {"role": "model", "text": ack}
