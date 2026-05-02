# ChatBot-Backend

Python Flask REST API powering the Notebook chatbot. Handles chat sessions, message persistence, document ingestion, and a multi-stage RAG pipeline backed by Azure OpenAI and ChromaDB.

---

## Modules

| File | Responsibility |
|------|---------------|
| `app.py` | Flask application — all routes, request parsing, LLM calls |
| `db.py` | SQLite CRUD for sessions and messages |
| `ingestion.py` | Document chunking, embedding, hybrid search, RAG orchestration |

---

## Running the Server

```bash
# From ChatBot-Backend/
python app.py
# Runs on http://127.0.0.1:5000 (debug=True)
```

---

## Environment Variables

Create a `.env` file in this directory:

```
AZURE_OPENAI_API_KEY=<key>
AZURE_OPENAI_ENDPOINT=https://<name>.openai.azure.com/
AZURE_OPENAI_API_VERSION=2023-05-15
AZURE_OPENAI_CHAT_API_VERSION=2024-12-01-preview
AZURE_OPENAI_CHAT_DEPLOYMENT=gpt-4o
AZURE_OPENAI_EMBEDDING_DEPLOYMENT=text-embedding-3-small
AZURE_COHERE_RERANK_ENDPOINT=<azure-cohere-endpoint>
AZURE_COHERE_API_KEY=<key>
```

---

## REST API Reference

### Health Check

#### `GET /`

Returns a liveness message.

**Response `200`:**
```json
{ "message": "Hello from GemBot Flask API!" }
```

---

### Chat

#### `POST /api/get_response`

Send a user message and receive an AI response. Optionally retrieves context from uploaded documents via the RAG pipeline.

**Request body:**
```json
{
  "message": "What is the capital of France?",
  "session_id": "uuid-string",
  "use_docs": false,
  "history": [
    { "role": "user",  "parts": [{ "text": "Hello" }] },
    { "role": "model", "parts": [{ "text": "Hi there!" }] }
  ]
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `message` | string | yes | The user's current message |
| `session_id` | string | no | Active session UUID; required for RAG and message persistence |
| `use_docs` | boolean | no | Set `true` to run the RAG pipeline against uploaded docs |
| `history` | array | no | Prior messages; last 6 entries (3 turns) are used for context |

**Response `200`:**
```json
{
  "answer": "Paris is the capital of France.",
  "confidence": 0.97,
  "sources": [
    { "filename": "france.pdf", "page": "3", "chunk_id": 5 }
  ],
  "session_title": "Capital of France"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `answer` | string | LLM response text |
| `confidence` | float | 0.0–1.0 confidence score |
| `sources` | array | List of cited document chunks (empty if `use_docs=false`) |
| `session_title` | string | Auto-generated title (only on the first message of a session) |

---

### Sessions

#### `GET /api/sessions`

List all chat sessions, newest first.

**Response `200`:**
```json
{
  "sessions": [
    {
      "id": "uuid",
      "title": "Capital of France",
      "created_at": "2024-01-15T10:30:00Z",
      "updated_at": "2024-01-15T10:31:00Z"
    }
  ]
}
```

---

#### `POST /api/sessions`

Create a new chat session.

**Response `201`:**
```json
{
  "id": "uuid",
  "title": "New Chat",
  "created_at": "2024-01-15T10:30:00Z",
  "updated_at": "2024-01-15T10:30:00Z"
}
```

---

#### `GET /api/sessions/<session_id>`

Retrieve all messages for a session.

**Response `200`:**
```json
{
  "messages": [
    {
      "role": "user",
      "parts": [{ "text": "Hello" }]
    },
    {
      "role": "model",
      "parts": [{ "text": "Hi there!" }],
      "confidence": 0.95,
      "sources": []
    }
  ]
}
```

---

#### `DELETE /api/sessions/<session_id>`

Delete a session, all its messages, uploaded files, and vector embeddings.

**Response `200`:**
```json
{ "status": "deleted" }
```

---

### Documents

#### `POST /api/upload`

Upload a PDF or TXT document to a session. The file is chunked, embedded, and indexed immediately.

**Request:** `multipart/form-data`

| Field | Type | Description |
|-------|------|-------------|
| `file` | file | PDF or TXT, max 5 MB |
| `session_id` | string | Target session UUID |

**Response `200`:**
```json
{
  "status": "indexed",
  "filename": "document.pdf",
  "chunks_indexed": 18
}
```

**Errors:**
- `400` — missing file, missing session_id, wrong extension, or file too large
- `500` — embedding/storage failure

---

#### `GET /api/docs?session_id=<uuid>`

List filenames of documents uploaded to a session.

**Response `200`:**
```json
{ "documents": ["document.pdf", "notes.txt"] }
```

---

#### `DELETE /api/docs/<filename>?session_id=<uuid>`

Delete a document and remove its chunks from the vector store and BM25 index.

**Response `200`:**
```json
{ "status": "deleted", "filename": "document.pdf" }
```

---

## Database Schema

SQLite database at `./sessions.db`.

### Table: `sessions`

| Column | Type | Description |
|--------|------|-------------|
| `id` | TEXT PK | UUID |
| `user_id` | TEXT | Hardcoded `"default"` (single-user) |
| `title` | TEXT | Session title, default `"New Chat"` |
| `created_at` | TEXT | ISO 8601 UTC timestamp |
| `updated_at` | TEXT | ISO 8601 UTC timestamp, updated on each message |

### Table: `messages`

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Auto-increment |
| `session_id` | TEXT FK | References `sessions.id` — CASCADE DELETE |
| `role` | TEXT | `"user"` or `"model"` |
| `parts` | TEXT | JSON: `[{"text": "..."}]` |
| `confidence` | REAL | Optional; only on model messages |
| `sources` | TEXT | JSON: `[{"filename", "page", "chunk_id"}]` |
| `created_at` | TEXT | ISO 8601 UTC timestamp |

---

## RAG Pipeline

Activated when `use_docs=true` and a `session_id` is provided with uploaded documents.

```
User Query
    │
    ▼
Query Variation Generation  ──── LLM generates 5 variations
    │  (2 LLM calls)              Call 1: 3 context-aware (uses history)
    │                             Call 2: 2 standalone rephrases
    │
    ▼
6 Queries (original + 5 variations)
    │
    ├─── Semantic Search (ChromaDB, top 10 per query)
    └─── Keyword Search  (BM25Okapi, top 10 per query)
    │
    ▼
Per-Query RRF Fusion
    │  weights: 0.7 semantic / 0.3 keyword
    │  k = 60
    ▼
Cross-Query RRF Fusion
    │  equal weights across all 6 result lists
    │  → top 20 candidates
    ▼
Cohere Rerank v4.0-fast
    │  → top 5 final chunks
    ▼
LLM (gpt-4o) with chunks as context
    │
    ▼
Structured response: answer + confidence + sources
```

### Key Tuning Constants

| Parameter | Value | Location |
|-----------|-------|----------|
| Query variations | 5 | `ingestion.py:_generate_query_variations` |
| Semantic hits per query | 10 | `ingestion.py:_semantic_search` |
| BM25 hits per query | 10 | `ingestion.py:_keyword_search` |
| RRF k constant | 60 | `ingestion.py:_rrf` |
| Semantic weight (intra-query RRF) | 0.7 | `ingestion.py:advanced_search` |
| Keyword weight (intra-query RRF) | 0.3 | `ingestion.py:advanced_search` |
| Rerank candidates sent | 20 | `ingestion.py:advanced_search` |
| Final chunks returned | 5 | `ingestion.py:advanced_search` |

---

## Document Storage

Uploaded files are stored on disk at `./docs/<session_id>/<filename>`.

Chunks are stored in ChromaDB (`./chroma_db/`) with metadata:
```json
{ "filename": "doc.pdf", "chunk_id": 3, "page": "12", "session_id": "uuid" }
```

Per-session BM25 indexes are held **in-memory** (`ingestion._bm25_index[session_id]`) and rebuilt on every upload or delete.

Chunk IDs in ChromaDB follow the format: `<session_id>__<filename>__chunk_<n>`

---

## Running Tests

```bash
# From ChatBot-Backend/
python -m pytest tests/test_rerank.py -v
```

`test_rerank.py` tests the Azure Cohere rerank endpoint directly.
