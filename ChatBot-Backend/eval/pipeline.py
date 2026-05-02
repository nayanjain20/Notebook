"""
Eval RAG pipeline.
Mirrors the main app's ingestion approach (unstructured + chunk_by_title)
but writes to a separate eval_chroma_db/ and a single 'eval_docs' collection.
"""

import os
import chromadb
from openai import AzureOpenAI
from dotenv import load_dotenv
from unstructured.partition.auto import partition
from unstructured.chunking.title import chunk_by_title

load_dotenv()

_CHROMA_PATH    = os.path.join(os.path.dirname(__file__), "..", "eval_chroma_db")
EVAL_COLLECTION = "eval_docs"
EVAL_DOCS_DIR   = os.path.join(os.path.dirname(__file__), "docs")

_chroma = chromadb.PersistentClient(path=_CHROMA_PATH)


# ── Azure clients ─────────────────────────────────────────────────────────────
def _embed_client():
    return AzureOpenAI(
        api_key=os.getenv("AZURE_OPENAI_API_KEY"),
        api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
    )


def _chat_client():
    return AzureOpenAI(
        api_key=os.getenv("AZURE_OPENAI_API_KEY"),
        api_version=os.getenv("AZURE_OPENAI_CHAT_API_VERSION"),
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
    )


def _embed(texts: list[str]) -> list[list[float]]:
    response = _embed_client().embeddings.create(
        input=texts,
        model=os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT"),
    )
    return [item.embedding for item in response.data]


# ── Chunking (matches main app ingestion.py) ──────────────────────────────────
def _extract_chunks(filepath: str) -> list[dict]:
    elements = partition(filename=filepath)
    chunks   = chunk_by_title(elements, max_characters=2000, overlap=200)
    result   = []
    doc_file = os.path.basename(filepath)
    for i, chunk in enumerate(chunks):
        text = chunk.text.strip()
        if not text:
            continue
        page = getattr(chunk.metadata, "page_number", None)
        result.append({
            "text":     text,
            "metadata": {
                "doc_file": doc_file,
                "chunk_id": i,
                "page":     str(page) if page else "N/A",
            },
        })
    return result


# ── Collection helpers ────────────────────────────────────────────────────────
def collection_exists(collection_name: str = EVAL_COLLECTION) -> bool:
    try:
        _chroma.get_collection(collection_name)
        return True
    except Exception:
        return False


def clear_collection(collection_name: str = EVAL_COLLECTION):
    try:
        _chroma.delete_collection(collection_name)
    except Exception:
        pass


# ── Ingest ────────────────────────────────────────────────────────────────────
def ingest(filepath: str, collection_name: str = EVAL_COLLECTION) -> int:
    """Chunk a file with unstructured, embed, and store in ChromaDB. Returns chunk count."""
    chunks = _extract_chunks(filepath)
    if not chunks:
        return 0

    try:
        collection = _chroma.get_collection(collection_name)
    except Exception:
        collection = _chroma.create_collection(collection_name)

    doc_file   = os.path.basename(filepath)
    texts      = [c["text"] for c in chunks]
    embeddings = _embed(texts)
    ids        = [f"{doc_file}__chunk_{c['metadata']['chunk_id']}" for c in chunks]
    metadatas  = [c["metadata"] for c in chunks]

    # Remove existing chunks for this doc_file before re-adding
    try:
        existing = collection.get(where={"doc_file": doc_file})
        if existing["ids"]:
            collection.delete(ids=existing["ids"])
    except Exception:
        pass

    collection.add(documents=texts, embeddings=embeddings, ids=ids, metadatas=metadatas)
    return len(chunks)


def ingest_all(collection_name: str = EVAL_COLLECTION) -> dict[str, int]:
    """Ingest every file in eval/docs/ into the collection. Returns {filename: chunk_count}."""
    results = {}
    for fname in os.listdir(EVAL_DOCS_DIR):
        fpath = os.path.join(EVAL_DOCS_DIR, fname)
        if os.path.isfile(fpath):
            results[fname] = ingest(fpath, collection_name)
    return results


# ── Retrieve ──────────────────────────────────────────────────────────────────
def retrieve(query: str, collection_name: str = EVAL_COLLECTION, top_k: int = 5) -> list[str]:
    collection  = _chroma.get_collection(collection_name)
    query_emb   = _embed([query])[0]
    results     = collection.query(
        query_embeddings=[query_emb],
        n_results=min(top_k, collection.count()),
    )
    return results["documents"][0]


# ── Generate ──────────────────────────────────────────────────────────────────
def generate(question: str, contexts: list[str]) -> str:
    context_str = "\n\n---\n\n".join(contexts)
    response = _chat_client().chat.completions.create(
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
