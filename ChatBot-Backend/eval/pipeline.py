"""
Self-contained RAG pipeline for evaluation.
Uses a separate ChromaDB path (eval_chroma_db/) so it never touches the main app's data.
"""

import os
import chromadb
from openai import AzureOpenAI
from dotenv import load_dotenv

load_dotenv()

_CHROMA_PATH = os.path.join(os.path.dirname(__file__), "..", "eval_chroma_db")
_CHUNK_SIZE = 1000
_CHUNK_OVERLAP = 150

_chroma = chromadb.PersistentClient(path=_CHROMA_PATH)


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


def _chunk(text: str) -> list[str]:
    chunks, start = [], 0
    while start < len(text):
        end = min(start + _CHUNK_SIZE, len(text))
        chunks.append(text[start:end].strip())
        if end == len(text):
            break
        start += _CHUNK_SIZE - _CHUNK_OVERLAP
    return [c for c in chunks if c]


def ingest(filepath: str, collection_name: str) -> int:
    """Chunk a text file, embed, and store in ChromaDB. Returns chunk count."""
    with open(filepath, "r", encoding="utf-8") as f:
        text = f.read()

    chunks = _chunk(text)

    try:
        _chroma.delete_collection(collection_name)
    except Exception:
        pass

    collection = _chroma.create_collection(collection_name)
    embeddings = _embed(chunks)
    collection.add(
        documents=chunks,
        embeddings=embeddings,
        ids=[f"chunk_{i}" for i in range(len(chunks))],
    )
    return len(chunks)


def retrieve(query: str, collection_name: str, top_k: int = 5) -> list[str]:
    """Return top_k chunk texts most relevant to the query."""
    collection = _chroma.get_collection(collection_name)
    query_emb = _embed([query])[0]
    results = collection.query(
        query_embeddings=[query_emb],
        n_results=min(top_k, collection.count()),
    )
    return results["documents"][0]


def generate(question: str, contexts: list[str]) -> str:
    """Generate an answer from the question and retrieved context chunks."""
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


def collection_exists(collection_name: str) -> bool:
    try:
        _chroma.get_collection(collection_name)
        return True
    except Exception:
        return False
