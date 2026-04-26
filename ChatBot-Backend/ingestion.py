import os
from openai import AzureOpenAI
from dotenv import load_dotenv
import chromadb
from unstructured.partition.auto import partition
from unstructured.chunking.title import chunk_by_title

load_dotenv()

EMBEDDING_DEPLOYMENT = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT")

_embed_client = AzureOpenAI(
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    api_version=os.getenv("AZURE_OPENAI_CHAT_API_VERSION"),
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
)

_chroma = chromadb.PersistentClient(path="chroma_db")
_collection = _chroma.get_or_create_collection("documents")


def _embed(text: str) -> list:
    response = _embed_client.embeddings.create(model=EMBEDDING_DEPLOYMENT, input=text)
    return response.data[0].embedding


def extract_and_chunk(filepath: str, filename: str) -> list:
    """Parse file with unstructured, chunk by section, return list of chunk dicts."""
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
            "metadata": {
                "filename": filename,
                "chunk_id": i,
                "page": str(page),
            }
        })
        
    return result


def embed_and_store(chunks: list, filename: str):
    """Embed chunks and upsert into ChromaDB. Re-uploading the same file replaces old chunks."""
    if not chunks:
        return

    existing = _collection.get(where={"filename": filename})
    if existing["ids"]:
        _collection.delete(ids=existing["ids"])

    ids = [f"{filename}__chunk_{i}" for i in range(len(chunks))]
    texts = [c["text"] for c in chunks]
    metadatas = [c["metadata"] for c in chunks]
    embeddings = [_embed(t) for t in texts]

    _collection.add(ids=ids, documents=texts, embeddings=embeddings, metadatas=metadatas)


def search_docs(query: str, top_k: int = 3) -> list:
    """Embed query and return top-k most relevant chunks with metadata."""
    count = _collection.count()
    if count == 0:
        return []

    query_embedding = _embed(query)
    results = _collection.query(
        query_embeddings=[query_embedding],
        n_results=min(top_k, count),
    )

    return [
        {"text": doc, "metadata": meta}
        for doc, meta in zip(results["documents"][0], results["metadatas"][0])
    ]


def list_documents() -> list:
    """Return sorted list of unique filenames currently indexed."""
    if _collection.count() == 0:
        return []
    all_items = _collection.get()
    return sorted({m["filename"] for m in all_items["metadatas"]})


def delete_document(filename: str):
    """Delete all embeddings for a filename from ChromaDB."""
    existing = _collection.get(where={"filename": filename})
    if existing["ids"]:
        _collection.delete(ids=existing["ids"])
