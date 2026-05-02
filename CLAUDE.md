# CLAUDE.md — Notebook Chatbot

AI assistant context for this repository.

---

## Project Summary

Full-stack AI chatbot ("Notebook") with:
- **React + TypeScript** frontend (Vite, Tailwind CSS)
- **Python Flask** backend (SQLite, ChromaDB, Azure OpenAI)
- **Advanced RAG pipeline**: multi-query expansion → hybrid retrieval (semantic + BM25) → RRF fusion → Cohere rerank
- **Per-session document uploads**: each chat session has its own isolated set of documents and vector embeddings
- **Session persistence**: conversations stored in SQLite and resumable

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend framework | React 19, TypeScript 5.8, Vite 6 |
| Frontend styling | Tailwind CSS 4.x |
| HTTP client | native `fetch` (not axios) |
| Backend framework | Flask (Python 3.10+) |
| Chat/embeddings LLM | Azure OpenAI — `gpt-4o`, `text-embedding-3-small` |
| Vector store | ChromaDB (persistent, `./chroma_db/`) |
| Keyword search | BM25 (`rank-bm25`) — in-memory, per session |
| Reranker | Azure Cohere Rerank v4.0-fast |
| Session DB | SQLite (`./sessions.db`) |
| Document parsing | `unstructured[pdf]` |

---

## Key File Map

```
Bot_project/
├── README.md                              # Project overview + setup guide
├── CLAUDE.md                              # This file
├── ChatBot-Backend/
│   ├── app.py                             # All Flask routes
│   ├── db.py                              # SQLite session/message CRUD
│   ├── ingestion.py                       # Chunking, embedding, RAG pipeline
│   ├── requirements.txt
│   └── tests/test_rerank.py
└── ChatBot/react-ai-tool/src/
    ├── App.tsx                            # Root component, session state
    ├── hooks/useChat.ts                   # Message state + sendMessage
    └── components/
        ├── SessionList.tsx                # Session sidebar (forwardRef)
        ├── FileUpload.tsx                 # Document upload panel
        ├── ChatPannel.tsx                 # Message rendering
        ├── ChatHeader.tsx                 # Static header
        └── LoaderSpinner.tsx              # Loading overlay
```

---

## Current Branch: `feature/chat-sessions`

This branch adds session persistence on top of the Phase 3 RAG work:
- `db.py` is **new** — SQLite for sessions + messages
- `ingestion.py` is largely **new** — full RAG pipeline (was simpler in Phase 3)
- `app.py` has significant additions — session CRUD routes, advanced_search integration
- `SessionList.tsx` and `FileUpload.tsx` are **new** frontend components
- `App.tsx` was refactored to support multi-session UX

---

## Conventions

- **No `axios`** — use native `fetch` in the frontend
- **No code comments** unless the WHY is non-obvious
- **SQLite** for session/message persistence; do not add MongoDB (it's installed but unused)
- **ChromaDB** for vector storage; collection name is `"documents"`
- **Session isolation**: all ChromaDB queries and BM25 indexes are scoped by `session_id`
- **User ID**: hardcoded as `"default"` — single-user, no auth
- **Message role values**: `"user"` and `"model"` (not `"assistant"`) in the frontend/DB; converted to `"assistant"` only when calling the OpenAI API
- **Chunk ID format in ChromaDB**: `<session_id>__<filename>__chunk_<n>`

---

## Azure Service Names

| Service | Deployment / Model |
|---------|-------------------|
| Chat | `gpt-4o` |
| Embeddings | `text-embedding-3-small` |
| Reranker | `cohere-rerank-v4.0-fast` (Azure-hosted) |

---

## Data Relationships

```
session (sessions table)
  └─ messages[] (messages table, FK → session.id, CASCADE DELETE)
  └─ documents[] (ChromaDB chunks, filtered by session_id metadata)
  └─ files[] (disk: docs/<session_id>/<filename>)
  └─ bm25_index (in-memory: ingestion._bm25_index[session_id])
```

Deleting a session removes all four.

---

## Known Issues / TODOs

- **No authentication** — all sessions visible to any client; `user_id` is always `"default"`
- **No rate limiting** on any endpoint
- **pymongo installed but unused** — can be removed from `requirements.txt`
- **`axios` installed but unused** in the frontend — can be removed from `package.json`
- **Multiple Google AI packages** installed in frontend (`@google/genai` + `@google/generative-ai`) — unused, can be removed
- **BM25 index is in-memory only** — restarts lose all indexes (rebuilt on first search or upload)
- **CORS is fully open** (`CORS(app)`) — restrict to frontend origin before production deployment
- **`.env` file with real credentials** is committed in git history — rotate keys if this repo goes public
- **Flask runs in `debug=True`** — change to `False` for production
