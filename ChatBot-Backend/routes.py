"""HTTP API for Notebook, grouped as one ``/api`` blueprint.

Routes stay thin: they validate input, delegate to the agent / ingestion /
persistence layers, and shape the JSON (or SSE) response. All reasoning lives
in ``agent.py`` and ``onboarding.py``; all storage in ``db.py`` / ``ingestion.py``.
"""

import json
import logging
import os
import shutil
from urllib.parse import urlparse

from flask import Blueprint, Response, jsonify, request
from werkzeug.utils import secure_filename

import agent
import config
import db
import onboarding
import providers
from ingestion import (
    delete_document, delete_session_documents, embed_and_store, extract_and_chunk,
    extract_and_chunk_url, list_documents, source_name_for_url,
)

logger = logging.getLogger(__name__)

api = Blueprint("api", __name__, url_prefix="/api")


def _activate_session_providers(session_id: str | None, confidential: bool | None = None,
                                model: str | None = None) -> None:
    """Point chat + embedding providers at a session's mode & model for this request.

    Values may be passed directly (e.g. at creation); otherwise they're read from
    the session. This is how the lock is *enforced*: the backend always derives
    the backend from the session's fixed mode/model, never from live client input.
    """
    if confidential is None:
        confidential = db.get_session_confidential(session_id) if session_id else False
    if model is None and session_id:
        model = db.get_session_model(session_id)
    providers.activate(confidential, model)


# ─── Models ───────────────────────────────────────────────────────────────────

@api.route("/models", methods=["GET"])
def get_models():
    """Dynamically list the chat models available on this system (local + cloud)."""
    return jsonify(providers.list_models())


# ─── Chat ─────────────────────────────────────────────────────────────────────

@api.route("/chat_stream", methods=["POST"])
def chat_stream():
    """Stream the agent's thinking steps and final answer as Server-Sent Events."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400
    user_message = (data.get("message") or "").strip()
    if not user_message:
        return jsonify({"error": "message is required"}), 400

    visuals = data.get("visuals", True)
    session_id = data.get("session_id")
    history = data.get("history", [])

    # Providers are fixed by the session's stored mode & model — never client input.
    confidential = db.get_session_confidential(session_id) if session_id else False
    model = db.get_session_model(session_id) if session_id else None

    def sse():
        providers.activate(confidential, model)
        for event in agent.run_agent_stream(user_message, history, session_id, visuals):
            yield f"data: {json.dumps(event)}\n\n"

    return Response(
        sse(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ─── Sessions ─────────────────────────────────────────────────────────────────

@api.route("/sessions", methods=["GET"])
def list_sessions():
    return jsonify({"sessions": db.list_sessions()})


@api.route("/sessions", methods=["POST"])
def create_session():
    data = request.get_json(silent=True) or {}
    confidential = bool(data.get("confidential", False))
    model = (data.get("model") or "").strip() or None
    return jsonify(db.create_session(confidential=confidential, model=model)), 201


@api.route("/sessions/<session_id>", methods=["GET"])
def get_session(session_id):
    return jsonify({
        "messages": db.get_session_messages(session_id),
        "confidential": db.get_session_confidential(session_id),
        "model": db.get_session_model(session_id),
    })


@api.route("/sessions/<session_id>", methods=["DELETE"])
def delete_session(session_id):
    _activate_session_providers(session_id)   # clear docs from the right collection
    db.delete_session(session_id)
    delete_session_documents(session_id)
    session_dir = os.path.join(config.DOCS_DIR, session_id)
    if os.path.isdir(session_dir):
        shutil.rmtree(session_dir)
    return jsonify({"status": "deleted"})


# ─── Documents ────────────────────────────────────────────────────────────────

@api.route("/upload", methods=["POST"])
def upload_file():
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400
    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "No file selected"}), 400

    session_id = request.form.get("session_id")
    if not session_id:
        return jsonify({"error": "session_id is required"}), 400

    ext = file.filename.rsplit(".", 1)[-1].lower()
    if ext not in config.ALLOWED_EXTENSIONS:
        return jsonify({"error": "Unsupported file type. Use PDF or TXT."}), 400

    file.seek(0, 2)
    size = file.tell()
    file.seek(0)
    if size > config.MAX_FILE_SIZE:
        return jsonify({"error": "File exceeds 5 MB limit."}), 400

    filename = secure_filename(file.filename)
    session_dir = os.path.join(config.DOCS_DIR, session_id)
    os.makedirs(session_dir, exist_ok=True)
    filepath = os.path.join(session_dir, filename)
    file.save(filepath)

    _activate_session_providers(session_id)   # embed via the session's mode (local/public)
    try:
        chunks = extract_and_chunk(filepath, filename)
        embed_and_store(chunks, filename, session_id)
        summary = onboarding.announce_new_source(session_id, filename, chunks)
        return jsonify({
            "status": "indexed",
            "filename": filename,
            "chunks_indexed": len(chunks),
            "summary": summary,
        })
    except Exception as exc:
        logger.error("Ingestion error: %s", exc)
        return jsonify({"error": "Failed to process document."}), 500


@api.route("/upload_url", methods=["POST"])
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

    _activate_session_providers(session_id)   # embed via the session's mode (local/public)

    # Dedup: if this URL canonicalizes to an existing source, skip re-ingesting.
    name = source_name_for_url(url)
    if name in list_documents(session_id):
        logger.info("[Ingest] URL '%s' already added as '%s' — skipping", url, name)
        return jsonify({"status": "exists", "filename": name, "chunks_indexed": 0})

    try:
        name, chunks = extract_and_chunk_url(url)
        if not chunks:
            return jsonify({"error": "No readable content found at that URL."}), 422
        embed_and_store(chunks, name, session_id)
        summary = onboarding.announce_new_source(session_id, name, chunks)
        return jsonify({
            "status": "indexed",
            "filename": name,
            "chunks_indexed": len(chunks),
            "summary": summary,
        })
    except Exception as exc:
        logger.error("URL ingestion error: %s", exc)
        return jsonify({"error": "Failed to fetch or process that URL."}), 500


@api.route("/docs", methods=["GET"])
def get_docs():
    session_id = request.args.get("session_id")
    if not session_id:
        return jsonify({"error": "session_id is required"}), 400
    _activate_session_providers(session_id)
    return jsonify({"documents": list_documents(session_id)})


@api.route("/docs/<filename>", methods=["DELETE"])
def delete_doc(filename):
    session_id = request.args.get("session_id")
    if not session_id:
        return jsonify({"error": "session_id is required"}), 400
    _activate_session_providers(session_id)
    delete_document(filename, session_id)
    filepath = os.path.join(config.DOCS_DIR, session_id, filename)
    if os.path.exists(filepath):
        os.remove(filepath)
    return jsonify({"status": "deleted", "filename": filename})
