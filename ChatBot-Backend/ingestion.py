import os
import json
import logging
import requests
from urllib.parse import urlparse
from openai import AzureOpenAI
from sentence_transformers import CrossEncoder
from dotenv import load_dotenv
import chromadb
from rank_bm25 import BM25Okapi
from unstructured.partition.auto import partition
from unstructured.partition.html import partition_html
from unstructured.chunking.title import chunk_by_title

load_dotenv()

logger = logging.getLogger(__name__)

EMBEDDING_DEPLOYMENT = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT")
CHAT_DEPLOYMENT = os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT")

_cross_encoder = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")

_client = AzureOpenAI(
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    api_version=os.getenv("AZURE_OPENAI_CHAT_API_VERSION"),
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
)

_CHROMA_DB_PATH = os.getenv("CHROMA_DB_PATH", os.path.join(os.path.dirname(__file__), "chroma_db"))
_chroma = chromadb.PersistentClient(path=_CHROMA_DB_PATH)
_collection = _chroma.get_or_create_collection("documents")

# Per-session BM25 indexes — rebuilt on every upload/delete for that session.
# Each entry: {"bm25": BM25Okapi, "ids": list[str], "docs": list[str]}
# In a multi-worker deployment each worker maintains its own copy (acceptable: it's a cache of ChromaDB data).
_bm25_index: dict = {}


def _rebuild_bm25(session_id: str):
    result = _collection.get(where={"session_id": {"$eq": session_id}})
    if not result["ids"]:
        _bm25_index.pop(session_id, None)
        logger.info(f"[BM25] Session {session_id[:8]}… — index cleared (no docs)")
        return
    _bm25_index[session_id] = {
        "bm25": BM25Okapi([doc.lower().split() for doc in result["documents"]]),
        "ids": result["ids"],
        "docs": result["documents"],
    }
    logger.info(f"[BM25] Session {session_id[:8]}… — {len(result['ids'])} chunks indexed")


# ─── Embedding ────────────────────────────────────────────────────────────────

def _embed(text: str) -> list:
    response = _client.embeddings.create(model=EMBEDDING_DEPLOYMENT, input=text)
    return response.data[0].embedding


# ─── Ingestion ────────────────────────────────────────────────────────────────

def extract_and_chunk(filepath: str, filename: str) -> list:
    """Parse file with unstructured, chunk by section, return list of chunk dicts."""
    logger.info(f"[Ingest] Parsing '{filename}'")
    elements = partition(filepath)
    chunks = chunk_by_title(elements, max_characters=2000, overlap=200)

    result = []
    for i, chunk in enumerate(chunks):
        text = str(chunk).strip()
        if not text:
            continue

        page = "N/A"
        if hasattr(chunk, "metadata") and hasattr(chunk.metadata, "page_number"):
            if chunk.metadata.page_number is not None:
                page = chunk.metadata.page_number

        result.append({
            "text": text,
            "metadata": {"filename": filename, "chunk_id": i, "page": str(page)},
        })

    logger.info(f"[Ingest] '{filename}' → {len(result)} chunks extracted")
    return result


def _canonical_path(path: str) -> str:
    """Collapse equivalent doc URL paths (trailing slash, .html, index.html)."""
    path = path.rstrip("/")
    for suffix in ("/index.html", "/index.htm"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
    for ext in (".html", ".htm"):
        if path.endswith(ext):
            path = path[: -len(ext)]
    return path


def _url_to_name(url: str) -> str:
    """Derive a slash-free, human-readable source name from a URL.

    URLs are canonicalized first (lowercased host, trailing slash / .html /
    index.html stripped) so equivalent pages map to the same source name and
    don't create duplicate entries. Kept free of '/' so it stays compatible
    with the docs list, citation, and DELETE /api/docs/<filename> flow.
    """
    parsed = urlparse(url)
    netloc = parsed.netloc.lower()
    path = _canonical_path(parsed.path)
    base = f"{netloc}{path}".rstrip("/")
    if not base:
        base = url
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in base)
    safe = safe.strip("_") or "web_source"
    return safe[:120]


def source_name_for_url(url: str) -> str:
    """Public helper: canonical source name for a URL (for dedup/filtering)."""
    return _url_to_name(url)


def extract_and_chunk_url(url: str) -> tuple:
    """Fetch a web page, extract readable text, chunk by section.

    Returns (name, chunks) where name is a slash-free source label used as the
    'filename' throughout the per-session RAG pipeline.

    The page is fetched with a browser-like User-Agent because many sites
    (e.g. Wikipedia) reject requests with no/blank User-Agent (HTTP 403).
    """
    name = _url_to_name(url)
    logger.info(f"[Ingest] Fetching URL '{url}' → source '{name}'")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        )
    }
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()

    elements = partition_html(text=response.text)
    chunks = chunk_by_title(elements, max_characters=2000, overlap=200)

    result = []
    for i, chunk in enumerate(chunks):
        text = str(chunk).strip()
        if not text:
            continue
        result.append({
            "text": text,
            "metadata": {"filename": name, "chunk_id": i, "page": "N/A"},
        })

    logger.info(f"[Ingest] '{url}' → {len(result)} chunks extracted")
    return name, result


def embed_and_store(chunks: list, filename: str, session_id: str):
    """Embed chunks, store in ChromaDB under the given session, rebuild BM25 for that session."""
    if not chunks:
        logger.warning(f"[Ingest] No chunks to embed for '{filename}'")
        return

    existing = _collection.get(where={
        "$and": [{"filename": {"$eq": filename}}, {"session_id": {"$eq": session_id}}]
    })
    if existing["ids"]:
        logger.info(f"[Ingest] Replacing {len(existing['ids'])} existing chunks for '{filename}' in session {session_id[:8]}…")
        _collection.delete(ids=existing["ids"])

    ids = [f"{session_id}__{filename}__chunk_{i}" for i in range(len(chunks))]
    texts = [c["text"] for c in chunks]
    metadatas = [
        {**c["metadata"], "session_id": session_id}
        for c in chunks
    ]

    logger.info(f"[Ingest] Embedding {len(chunks)} chunks for '{filename}'…")
    embeddings = [_embed(t) for t in texts]

    _collection.add(ids=ids, documents=texts, embeddings=embeddings, metadatas=metadatas)
    logger.info(f"[Ingest] Stored {len(chunks)} chunks. Collection total: {_collection.count()}")
    _rebuild_bm25(session_id)


# ─── Retrieval primitives ─────────────────────────────────────────────────────

def _semantic_search(query: str, top_k: int = 10, session_id: str = None) -> list:
    where = {"session_id": {"$eq": session_id}} if session_id else None
    existing = _collection.get(where=where) if where else _collection.get()
    count = len(existing["ids"])
    if count == 0:
        return []
    results = _collection.query(
        query_embeddings=[_embed(query)],
        n_results=min(top_k, count),
        where=where,
    )
    hits = [
        {"id": id_, "text": doc, "metadata": meta}
        for id_, doc, meta in zip(
            results["ids"][0], results["documents"][0], results["metadatas"][0]
        )
    ]
    logger.debug(f"[Semantic] '{query[:60]}' → {len(hits)} hits")
    return hits


def _keyword_search(query: str, top_k: int = 10, session_id: str = None) -> list:
    entry = _bm25_index.get(session_id) if session_id else None
    if not entry:
        return []
    scores = entry["bm25"].get_scores(query.lower().split())
    top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
    top_indices = [i for i in top_indices if scores[i] > 0]
    if not top_indices:
        logger.debug(f"[BM25] '{query[:60]}' → 0 hits")
        return []
    ids = [entry["ids"][i] for i in top_indices]
    results = _collection.get(ids=ids)
    hits = [
        {"id": id_, "text": doc, "metadata": meta}
        for id_, doc, meta in zip(results["ids"], results["documents"], results["metadatas"])
    ]
    logger.debug(f"[BM25] '{query[:60]}' → {len(hits)} hits")
    return hits


def _rrf(ranked_lists: list, weights: list = None, k: int = 60) -> list:
    """Reciprocal Rank Fusion across multiple ranked lists with optional per-list weights."""
    if weights is None:
        weights = [1.0] * len(ranked_lists)
    scores = {}
    chunks_map = {}
    for ranked_list, weight in zip(ranked_lists, weights):
        for rank, chunk in enumerate(ranked_list):
            cid = chunk["id"]
            scores[cid] = scores.get(cid, 0.0) + weight / (k + rank + 1)
            chunks_map[cid] = chunk
    return [chunks_map[cid] for cid in sorted(scores, key=scores.get, reverse=True)]


# ─── Multi-Query Generation ───────────────────────────────────────────────────

def _format_history(history: list) -> str:
    """Convert frontend history to a compact readable string for the LLM."""
    lines = []
    for msg in history[-6:]:  # last 3 turns max
        role = "User" if msg.get("role") == "user" else "Assistant"
        for part in msg.get("parts", []):
            if "text" in part and part["text"]:
                lines.append(f"{role}: {part['text']}")
                break
    return "\n".join(lines)


def _llm_json_list(messages: list, n: int) -> list:
    """Call the LLM and parse a JSON array response."""
    response = _client.chat.completions.create(
        model=CHAT_DEPLOYMENT,
        messages=messages,
        temperature=0.8,
        max_completion_tokens=300,
    )
    text = response.choices[0].message.content.strip()
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())[:n]


def _generate_query_variations(query: str, history: list = None, n: int = 5) -> list:
    """
    Two LLM calls — half the variations use conversation history to resolve
    context (e.g. 'Tell about 2013' → 'Microsoft revenue in 2013'), the other
    half rephrase the query in isolation for broader lexical coverage.
    """
    n_ctx = n // 2 + n % 2   # 3 when n=5
    n_free = n // 2           # 2 when n=5

    history_str = _format_history(history) if history else ""

    if history_str:
        ctx_messages = [
            {
                "role": "system",
                "content": (
                    f"Given the conversation history below, generate {n_ctx} search queries that "
                    "rephrase the user's latest question with full context resolved "
                    "(replace pronouns, fill in implicit references from history). "
                    "Return ONLY a valid JSON array of strings, no explanation.\n\n"
                    f"History:\n{history_str}"
                ),
            },
            {"role": "user", "content": query},
        ]
    else:
        ctx_messages = [
            {
                "role": "system",
                "content": (
                    f"Generate {n_ctx} different phrasings of the user's question from different angles. "
                    "Return ONLY a valid JSON array of strings, no explanation."
                ),
            },
            {"role": "user", "content": query},
        ]

    free_messages = [
        {
            "role": "system",
            "content": (
                f"Generate {n_free} alternative phrasings of this question as if it stands completely "
                "alone, using different vocabulary and sentence structure. "
                "Return ONLY a valid JSON array of strings, no explanation."
            ),
        },
        {"role": "user", "content": query},
    ]

    ctx_vars = _llm_json_list(ctx_messages, n_ctx)
    free_vars = _llm_json_list(free_messages, n_free)
    return ctx_vars + free_vars


# ─── Reranking ────────────────────────────────────────────────────────────────

def _rerank(query: str, chunks: list, top_n: int = 5) -> list:
    """Rerank candidates using the local cross-encoder (no API quota)."""
    if not chunks:
        return []
    pairs = [(query, c["text"]) for c in chunks]
    scores = _cross_encoder.predict(pairs)
    ranked = sorted(zip(scores, chunks), key=lambda x: x[0], reverse=True)
    reranked = [c for _, c in ranked[:top_n]]
    logger.info(f"[Rerank] {len(chunks)} candidates → top {len(reranked)} returned")
    for i, (score, chunk) in enumerate(ranked[:top_n], 1):
        logger.info(f"  #{i} score={score:.4f}  {chunk['metadata']['filename']} p.{chunk['metadata']['page']}")
    return reranked


# ─── Advanced RAG pipeline ───────────────────────────────────────────────────

def advanced_search(query: str, top_n: int = 5, history: list = None, session_id: str = None) -> list:
    """
    Multi-query × hybrid retrieval × RRF × cross-query RRF × rerank.
    Returns top_n reranked chunks with id, text, and metadata.
    """
    # Check for session documents
    if session_id and session_id not in _bm25_index:
        probe = _collection.get(where={"session_id": {"$eq": session_id}})
        if not probe["ids"]:
            logger.info(f"[RAG] No documents for session {session_id[:8]}… — skipping")
            return []

    logger.info(f"[RAG] Query: '{query}'  session={session_id[:8] if session_id else 'none'}…")

    # Step 1 — Generate query variations (fallback to original if LLM fails)
    try:
        variations = _generate_query_variations(query, history=history, n=5)
        logger.info(f"[RAG] Step 1 — Generated {len(variations)} query variations")
        for i, v in enumerate(variations, 1):
            logger.info(f"  v{i}: {v}")
    except Exception as e:
        logger.warning(f"[RAG] Step 1 — Query variation failed ({e}), using original only")
        variations = []
    all_queries = [query] + variations

    # Step 2 — Hybrid retrieval + RRF (0.7 semantic / 0.3 keyword) per query
    per_query_results = []
    for i, q in enumerate(all_queries):
        label = "original" if i == 0 else f"v{i}"
        semantic = _semantic_search(q, top_k=10, session_id=session_id)
        keyword = _keyword_search(q, top_k=10, session_id=session_id)
        fused = _rrf([semantic, keyword], weights=[0.7, 0.3])
        logger.info(f"[RAG] Step 2 [{label}] semantic={len(semantic)}, keyword={len(keyword)}, after RRF={len(fused)}")
        per_query_results.append(fused)

    # Step 3 — Cross-query RRF (original query gets 2× weight: its exact keywords
    # may be dropped by LLM variations, so rare BM25 signals must not get diluted)
    cross_weights = [2.0] + [1.0] * len(variations)
    merged = _rrf(per_query_results, weights=cross_weights)
    logger.info(f"[RAG] Step 3 — Cross-query RRF: {len(merged)} unique candidates")

    # Step 4 — Rerank top 30, return top_n
    candidates = merged[:30]
    logger.info(f"[RAG] Step 4 — Sending top {len(candidates)} candidates to reranker")
    try:
        results = _rerank(query, candidates, top_n=top_n)
    except Exception as e:
        logger.warning(f"[RAG] Step 4 — Reranking failed ({e}), falling back to RRF results")
        results = candidates[:top_n]

    logger.info(f"[RAG] Done — returning {len(results)} chunks to LLM")
    return results


# ─── Utilities ───────────────────────────────────────────────────────────────

def list_documents(session_id: str) -> list:
    result = _collection.get(where={"session_id": {"$eq": session_id}})
    return sorted({m["filename"] for m in result["metadatas"]}) if result["metadatas"] else []


def delete_document(filename: str, session_id: str):
    existing = _collection.get(where={
        "$and": [{"filename": {"$eq": filename}}, {"session_id": {"$eq": session_id}}]
    })
    if existing["ids"]:
        logger.info(f"[Ingest] Deleting {len(existing['ids'])} chunks for '{filename}' in session {session_id[:8]}…")
        _collection.delete(ids=existing["ids"])
    _rebuild_bm25(session_id)


def delete_session_documents(session_id: str):
    """Remove all ChromaDB chunks and BM25 index for a session. Called on session deletion."""
    existing = _collection.get(where={"session_id": {"$eq": session_id}})
    if existing["ids"]:
        _collection.delete(ids=existing["ids"])
        logger.info(f"[Ingest] Deleted {len(existing['ids'])} chunks for session {session_id[:8]}…")
    _bm25_index.pop(session_id, None)
