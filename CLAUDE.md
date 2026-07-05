# CLAUDE.md — Notebook Chatbot

AI assistant context for this repository.

---

## Project Summary

Full-stack AI chatbot ("Notebook") with:

- **React + TypeScript** frontend (Vite, Tailwind CSS)
- **Python Flask** backend (SQLite, ChromaDB, Azure OpenAI)
- **Advanced RAG pipeline**: multi-query expansion → hybrid retrieval (semantic + BM25) → RRF fusion → cross-query RRF → local cross-encoder rerank
- **Per-session document uploads**: each chat session has its own isolated set of documents and vector embeddings. Sources can be PDF/TXT files **or web page URLs**
- **Session persistence**: conversations stored in SQLite and resumable
- **RAG evaluation harness**: RAGAS-based eval pipeline under `ChatBot-Backend/eval/`

---

## Tech Stack

| Layer               | Technology                                                                                          |
| ------------------- | --------------------------------------------------------------------------------------------------- |
| Frontend framework  | React 19, TypeScript 5.8, Vite 6                                                                    |
| Frontend styling    | Tailwind CSS 4.x                                                                                    |
| HTTP client         | native `fetch` (not axios)                                                                          |
| Backend framework   | Flask (Python 3.10+)                                                                                |
| Chat/embeddings LLM | Azure OpenAI — `gpt-4o`, `text-embedding-3-small`                                                   |
| Vector store        | ChromaDB (persistent, `./chroma_db/`)                                                               |
| Keyword search      | BM25 (`rank-bm25`) — in-memory, per session                                                         |
| Reranker            | Local cross-encoder `cross-encoder/ms-marco-MiniLM-L-6-v2` (`sentence-transformers`) — no API quota |
| Session DB          | SQLite (`./sessions.db`)                                                                            |
| Document parsing    | `unstructured[pdf]` (files) + `partition_html` (URLs, fetched via `requests`)                       |
| Structured output   | Azure OpenAI **tool/function calling** (`provide_answer` tool: answer, confidence, source_indices)  |
| RAG eval            | RAGAS + `langchain-openai` (`eval/` dir)                                                            |

---

## Key File Map

```
Notebook/
├── README.md                              # Project overview + setup guide
├── DESIGN.md                              # Lean architecture overview
├── CLAUDE.md                              # This file
├── ChatBot-Backend/
│   ├── app.py                             # Application factory + entry point
│   ├── routes.py                          # HTTP/SSE API (one /api blueprint)
│   ├── agent.py                           # Reasoning loop + streaming orchestrator
│   ├── onboarding.py                      # Assistant reaction when a source is added
│   ├── ingestion.py                       # Chunking, embedding, RAG pipeline, URL ingestion, cross-encoder rerank
│   ├── prompts.py                         # Persona (soul.md), session memory, tool schemas
│   ├── llm.py                             # Azure OpenAI client + call_tool/call_text helpers
│   ├── helpers.py                         # Pure data-shaping utilities
│   ├── config.py                          # Env vars + constants
│   ├── db.py                              # SQLite session/message CRUD
│   ├── soul.md                            # Agent persona and teaching rules
│   ├── LLD.md                             # Backend low-level design
│   ├── requirements.txt
│   ├── eval/                              # RAGAS eval harness (run_eval.py, pipeline.py, generate_testset.py)
│   ├── test_data/                         # Eval datasets (test_dataset.json)
│   └── tests/test_rerank.py
└── ChatBot/react-ai-tool/src/
    ├── App.tsx                            # Root component, session state
    ├── hooks/useChat.ts                   # Message state + SSE consumer
    └── components/
        ├── SessionList.tsx                # Session sidebar (forwardRef)
        ├── FileUpload.tsx                 # Source upload panel
        ├── AddSourceMenu.tsx              # "+" add-source menu (file/URL)
        ├── ChatPanel.tsx                  # Message rendering + thought trace
        ├── MermaidDiagram.tsx             # Diagram rendering with zoom
        └── ChatHeader.tsx                 # Static header
```

---

## Current Branch: `feature/web-link-support`

Built on top of `feature/chat-sessions` and `feature/rag-evaluation`. Adds:

- **Web link ingestion** — `extract_and_chunk_url()` in `ingestion.py` fetches a page (browser-like User-Agent), parses via `partition_html`, and stores chunks like any file. New route `POST /api/upload_url`. `FileUpload.tsx` gained a URL input.
- URL sources are labelled with a slash-free name (`_url_to_name`) so they flow through the existing docs list / citation / `DELETE /api/docs/<filename>` paths.

Notable changes inherited from `feature/rag-evaluation`:

- **Reranker swapped** from Azure Cohere Rerank to a **local cross-encoder** (`cross-encoder/ms-marco-MiniLM-L-6-v2`) — no API quota, runs in-process.
- **Cross-query RRF** boosts the original query 2× and widens the reranker candidate pool to 30.
- **RAGAS eval pipeline** added under `eval/`; `EVALS_MODE=true` makes `/api/get_response` include raw `contexts` in the response.

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
- **Structured output**: forced via the `provide_answer` tool call, not JSON mode. `source_indices` (1-based) map back to retrieved chunks to build `sources`
- **URL sources**: stored under a slash-free label from `_url_to_name()`; treated as a "filename" everywhere downstream
- **Reranking is local** — do not reintroduce Cohere/API reranker calls unless asked

---

## Azure Service Names

| Service    | Deployment / Model                                                      |
| ---------- | ----------------------------------------------------------------------- |
| Chat       | `gpt-4o`                                                                |
| Embeddings | `text-embedding-3-small`                                                |
| Reranker   | Local `cross-encoder/ms-marco-MiniLM-L-6-v2` (in-process, no Azure/API) |

Env vars of note: `AZURE_OPENAI_CHAT_DEPLOYMENT`, `AZURE_OPENAI_EMBEDDING_DEPLOYMENT`, `AZURE_OPENAI_CHAT_API_VERSION`, `EVALS_MODE`, `CHROMA_DB_PATH`.

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
- **Unused deps removed** — `pymongo` (backend) and `axios` + `@google/*` + `mongodb` + unused Azure/OpenAI/speech packages (frontend) have been pruned
- **BM25 index is in-memory only** — restarts lose all indexes (rebuilt on first search or upload)
- **CORS is fully open** (`CORS(app)`) — restrict to frontend origin before production deployment
- **A real Azure OpenAI key was committed in early git history** (in a since-deleted notebook) — the key MUST be rotated in the Azure Portal; deleting the file does not scrub history
- **Flask runs in `debug=True`** — change to `False` for production
