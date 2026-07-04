import os
import json
import logging
from urllib.parse import urlparse
from flask import Flask, request, jsonify
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
                }
            },
            "required": ["answer", "confidence", "source_indices"]
        }
    }
}

@app.route('/')
def index():
    return jsonify({"message": "Hello from GemBot Flask API!"})

# ─── Assistant persona ────────────────────────────────────────────────────────

AGENT_PERSONA = (
    "You are Notebook, a focused study assistant that helps the user understand a topic using their "
    "sources (uploaded documents and web links). Be concise but substantive, and write with clear "
    "structure — short paragraphs, bullet lists, and headings when they aid understanding.\n"
    "Critically: do NOT repeat what you already said earlier in this conversation. Each turn should add "
    "something new — a deeper detail, a concrete example, a distinction, or a next angle. If the user asks "
    "something broad you've partly covered, advance the explanation instead of restating it.\n"
    "When (and only when) it genuinely helps the user learn, use 'follow_ups' to offer 1-3 specific next "
    "questions/actions, and use 'suggested_links' to recommend authoritative pages the user could add as "
    "sources. Omit them for trivial replies. Never invent URLs — only suggest links you are confident are real."
)

# ─── Chat ─────────────────────────────────────────────────────────────────────

@app.route('/api/get_response', methods=['POST'])
def get_response():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    user_message = data.get('message', '').strip()
    if not user_message:
        return jsonify({"error": "message is required"}), 400

    use_docs = data.get('use_docs', False)
    session_id = data.get('session_id')

    app.logger.info(f"[Request] session={session_id}  use_docs={use_docs}  message='{user_message[:80]}'")

    history = data.get('history', [])
    system_prompt = AGENT_PERSONA + "\n\nAnswer from your general knowledge."

    grounded_mode = bool(use_docs and session_id)
    NOT_FOUND_MSG = "I couldn't find anything relevant in your uploaded documents or links to answer that."

    if use_docs and not session_id:
        app.logger.warning("[Request] use_docs=True but no session_id — skipping RAG")

    retrieved_chunks = []
    if grounded_mode:
        retrieved_chunks = advanced_search(user_message, history=history, session_id=session_id)
        app.logger.info(f"[Request] Retrieved {len(retrieved_chunks)} chunks from advanced_search")

        if retrieved_chunks:
            def _chunk_header(i, c):
                meta = c["metadata"]
                page = "" if meta["page"] == "N/A" else f", p.{meta['page']}"
                return f"[{i+1}] {meta['filename']} (part {meta['chunk_id']+1}{page})"
            context = "\n\n".join(
                f"{_chunk_header(i, c)}\n{c['text']}"
                for i, c in enumerate(retrieved_chunks)
            )
        else:
            context = "(no sources were retrieved)"

        system_prompt = (
            AGENT_PERSONA + "\n\n"
            "GROUNDING RULES (strict): Answer the user's question using ONLY the numbered context sources "
            "below. Do NOT use outside or prior knowledge in your ANSWER, and do NOT answer general-knowledge "
            "questions unless the answer is explicitly present in the context. "
            f"If the answer is not contained in the context, set 'answer' to exactly: \"{NOT_FOUND_MSG}\" "
            "and set source_indices to an empty list — but you may still use follow_ups and suggested_links "
            "to help the user find or add the right sources. When you do answer from the context, list the "
            "numbers of the sources you actually used in source_indices.\n"
            "Note: 'suggested_links' are recommendations of new pages to add — they may draw on your general "
            "knowledge of authoritative sources even though your ANSWER must stay grounded in the context.\n\n"
            "Context:\n" + context
        )

    openai_messages = [{"role": "system", "content": system_prompt}]
    for msg in history:
        role = "assistant" if msg.get("role") == "model" else "user"
        for part in msg.get("parts", []):
            if "text" in part and part["text"]:
                openai_messages.append({"role": role, "content": part["text"]})
                break

    openai_messages.append({"role": "user", "content": user_message})

    is_first = session_id and db.is_first_message(session_id)

    if session_id:
        db.save_message(session_id, "user", [{"text": user_message}])

    try:
        response = client.chat.completions.create(
            model=DEPLOYMENT,
            messages=openai_messages,
            tools=[ANSWER_TOOL],
            tool_choice={"type": "function", "function": {"name": "provide_answer"}},
            max_completion_tokens=1000,
            temperature=0.7,
        )
        tool_call = response.choices[0].message.tool_calls[0]
        structured = json.loads(tool_call.function.arguments)
        app.logger.info(f"[Request] LLM confidence={structured.get('confidence', '?')}  used_sources={structured.get('source_indices', [])}")

        used_indices = structured.pop("source_indices", [])

        # Agentic extras — sanitize follow_ups and suggested_links.
        follow_ups = [s.strip() for s in (structured.pop("follow_ups", None) or []) if isinstance(s, str) and s.strip()][:3]
        raw_links = structured.pop("suggested_links", None) or []
        suggested_links = []
        seen_urls = set()
        for link in raw_links:
            if not isinstance(link, dict):
                continue
            url = (link.get("url") or "").strip()
            title = (link.get("title") or "").strip() or url
            if url.lower().startswith(("http://", "https://")) and url not in seen_urls:
                seen_urls.add(url)
                suggested_links.append({"url": url, "title": title})
        suggested_links = suggested_links[:4]

        # Don't suggest sources the session already has (avoids bloat / repeats).
        if session_id:
            existing_names = set(list_documents(session_id))
            suggested_links = [
                l for l in suggested_links if source_name_for_url(l["url"]) not in existing_names
            ]

        seen = set()
        unique_sources = []
        for idx in used_indices:
            i = idx - 1
            if i < 0 or i >= len(retrieved_chunks):
                continue
            meta = retrieved_chunks[i]["metadata"]
            key = (meta["filename"], meta["page"]) if meta["page"] != "N/A" else (meta["filename"], meta["chunk_id"])
            if key not in seen:
                seen.add(key)
                unique_sources.append({
                    "filename": meta["filename"],
                    "page": meta["page"],
                    "chunk_id": meta["chunk_id"],
                })
        structured["sources"] = unique_sources
        structured["follow_ups"] = follow_ups
        structured["suggested_links"] = suggested_links

        # In grounded mode, refuse to answer from general knowledge: if the model
        # cited no sources, it did not ground its answer in the provided documents.
        if grounded_mode and not unique_sources:
            app.logger.info("[Request] Grounded mode with no cited sources — returning not-found message")
            structured["answer"] = NOT_FOUND_MSG
            structured["confidence"] = 0.0
            structured["sources"] = []

        if EVALS_MODE and retrieved_chunks:
            structured["contexts"] = [c["text"] for c in retrieved_chunks]

        if session_id:
            db.save_message(
                session_id, "model",
                [{"text": structured["answer"]}],
                confidence=structured.get("confidence"),
                sources=unique_sources or None,
                follow_ups=follow_ups or None,
                suggested_links=suggested_links or None,
            )

        if is_first and session_id:
            try:
                title_response = client.chat.completions.create(
                    model=DEPLOYMENT,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "Generate a concise 3-6 word title that captures the topic of this conversation. "
                                "Return ONLY the title — no quotes, no punctuation at the end."
                            ),
                        },
                        {
                            "role": "user",
                            "content": f"User: {user_message}\nAssistant: {structured['answer'][:300]}",
                        },
                    ],
                    max_completion_tokens=20,
                    temperature=0.4,
                )
                title = title_response.choices[0].message.content.strip()
                db.update_session_title(session_id, title)
                structured["session_title"] = title
                app.logger.info(f"[Session] Title set: '{title}'")
            except Exception as e:
                app.logger.warning(f"[Session] Title generation failed: {e}")

        return jsonify(structured)
    except Exception as e:
        app.logger.error(f"OpenAI API error: {e}")
        return jsonify({"error": "Failed to get a response from the AI service."}), 500

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
        return jsonify({"status": "indexed", "filename": filename, "chunks_indexed": len(chunks)})
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
        return jsonify({"status": "indexed", "filename": name, "chunks_indexed": len(chunks)})
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
    app.run(debug=True, port=5000)
