import os
import json
import logging
from flask import Flask, request, jsonify
from flask_cors import CORS
from openai import AzureOpenAI
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
from ingestion import extract_and_chunk, embed_and_store, advanced_search, list_documents, delete_document

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)

app = Flask(__name__)
CORS(app)

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
        "description": "Provide a structured answer to the user's question",
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
                }
            },
            "required": ["answer", "confidence", "source_indices"]
        }
    }
}

@app.route('/')
def index():
    return jsonify({"message": "Hello from GemBot Flask API!"})

@app.route('/api/get_response', methods=['POST'])
def get_response():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    user_message = data.get('message', '').strip()
    if not user_message:
        return jsonify({"error": "message is required"}), 400

    use_docs = data.get('use_docs', False)

    app.logger.info(f"[Request] use_docs={use_docs}  message='{user_message[:80]}'")

    # Frontend uses role "model"; OpenAI expects "assistant"
    history = data.get('history', [])
    system_prompt = "You are a helpful assistant."

    retrieved_chunks = []
    if use_docs:
        retrieved_chunks = advanced_search(user_message, history=history)
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
            system_prompt = (
                "You are a helpful assistant. Answer using only the numbered context sources below. "
                "In source_indices, return only the numbers of sources you actually used.\n\n"
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

    try:
        response = client.chat.completions.create(
            model=DEPLOYMENT,
            messages=openai_messages,
            tools=[ANSWER_TOOL],
            tool_choice={"type": "function", "function": {"name": "provide_answer"}},
            max_completion_tokens=800,
            temperature=0.7,
        )
        tool_call = response.choices[0].message.tool_calls[0]
        structured = json.loads(tool_call.function.arguments)
        app.logger.info(f"[Request] LLM confidence={structured.get('confidence', '?')}  used_sources={structured.get('source_indices', [])}")

        # Build sources only from chunks the LLM actually cited
        used_indices = structured.pop("source_indices", [])
        seen = set()
        unique_sources = []
        for idx in used_indices:
            i = idx - 1  # convert 1-based to 0-based
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
        return jsonify(structured)
    except Exception as e:
        app.logger.error(f"OpenAI API error: {e}")
        return jsonify({"error": "Failed to get a response from the AI service."}), 500

@app.route('/api/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files['file']
    if not file.filename:
        return jsonify({"error": "No file selected"}), 400

    ext = file.filename.rsplit('.', 1)[-1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({"error": "Unsupported file type. Use PDF or TXT."}), 400

    file.seek(0, 2)
    size = file.tell()
    file.seek(0)
    if size > MAX_FILE_SIZE:
        return jsonify({"error": "File exceeds 5 MB limit."}), 400

    filename = secure_filename(file.filename)
    filepath = os.path.join(DOCS_DIR, filename)
    file.save(filepath)

    try:
        chunks = extract_and_chunk(filepath, filename)
        embed_and_store(chunks, filename)
        return jsonify({"status": "indexed", "filename": filename, "chunks_indexed": len(chunks)})
    except Exception as e:
        app.logger.error(f"Ingestion error: {e}")
        return jsonify({"error": "Failed to process document."}), 500


@app.route('/api/docs', methods=['GET'])
def get_docs():
    return jsonify({"documents": list_documents()})


@app.route('/api/docs/<filename>', methods=['DELETE'])
def delete_doc(filename):
    filepath = os.path.join(DOCS_DIR, filename)

    delete_document(filename)

    if os.path.exists(filepath):
        os.remove(filepath)

    return jsonify({"status": "deleted", "filename": filename})


if __name__ == '__main__':
    app.run(debug=True, port=5000)
