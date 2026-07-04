"""
Eval pipeline — delegates to the real app's ingestion.py.

Sets CHROMA_DB_PATH to eval_chroma_db/ BEFORE importing ingestion so that
ingestion's module-level _chroma client points at the eval database, not the
production one. Same retrieval code path (advanced_search: multi-query + BM25
+ semantic + RRF + rerank) as the live app.
"""

import os
import sys

# Must be set before ingestion is imported so its module-level _chroma init
# picks up the eval database path.
_EVAL_CHROMA_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "eval_chroma_db"))
os.environ.setdefault("CHROMA_DB_PATH", _EVAL_CHROMA_PATH)

# Ensure the ChatBot-Backend directory is on sys.path so `import ingestion` works
# when this module is imported from anywhere.
_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from dotenv import load_dotenv
from openai import AzureOpenAI

load_dotenv()

from ingestion import (
    advanced_search,
    extract_and_chunk,
    embed_and_store,
    delete_session_documents,
)

EVAL_SESSION_ID = "eval"
EVAL_DOCS_DIR   = os.path.join(os.path.dirname(__file__), "docs")
EVAL_COLLECTION = "eval_docs"   # kept for generate_testset.py compatibility


# ── Collection helpers ────────────────────────────────────────────────────────

def collection_exists(collection_name: str = EVAL_COLLECTION) -> bool:
    from ingestion import _collection
    try:
        result = _collection.get(where={"session_id": {"$eq": EVAL_SESSION_ID}})
        return len(result["ids"]) > 0
    except Exception:
        return False


def clear_collection(collection_name: str = EVAL_COLLECTION):
    delete_session_documents(EVAL_SESSION_ID)


# ── Ingest ────────────────────────────────────────────────────────────────────

def ingest(filepath: str, collection_name: str = EVAL_COLLECTION) -> int:
    filename = os.path.basename(filepath)
    chunks = extract_and_chunk(filepath, filename)
    if not chunks:
        return 0
    embed_and_store(chunks, filename, EVAL_SESSION_ID)
    return len(chunks)


def ingest_all(collection_name: str = EVAL_COLLECTION) -> dict[str, int]:
    results = {}
    for fname in os.listdir(EVAL_DOCS_DIR):
        fpath = os.path.join(EVAL_DOCS_DIR, fname)
        if os.path.isfile(fpath):
            results[fname] = ingest(fpath, collection_name)
    return results


# ── Retrieve (delegates to the same advanced_search the app uses) ─────────────

def retrieve(query: str, collection_name: str = EVAL_COLLECTION, top_k: int = 5) -> list[str]:
    chunks = advanced_search(query, top_n=top_k, session_id=EVAL_SESSION_ID)
    return [c["text"] for c in chunks]


# ── Generate ──────────────────────────────────────────────────────────────────

def generate(question: str, contexts: list[str]) -> str:
    _chat = AzureOpenAI(
        api_key=os.getenv("AZURE_OPENAI_API_KEY"),
        api_version=os.getenv("AZURE_OPENAI_CHAT_API_VERSION"),
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
    )
    context_str = "\n\n---\n\n".join(contexts)
    response = _chat.chat.completions.create(
        model=os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT"),
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a helpful assistant. Answer the question using ONLY "
                    "the provided context. If the answer is not in the context, "
                    "say exactly: 'I don't know based on the provided documents.'"
                ),
            },
            {
                "role": "user",
                "content": f"Context:\n{context_str}\n\nQuestion: {question}",
            },
        ],
        max_completion_tokens=300,
        temperature=0.0,
    )
    return response.choices[0].message.content.strip()
