# Notebook

A multi-turn AI chatbot with session persistence, document upload, and an advanced RAG (Retrieval-Augmented Generation) pipeline. Built with a React + TypeScript frontend and a Python Flask backend, powered by Azure OpenAI.

---

## Features

- **Multi-turn chat sessions** — persistent conversations stored in SQLite; resume any session at any time
- **Per-session document upload** — upload PDF or TXT files scoped to a chat session
- **Advanced RAG pipeline** — hybrid semantic + keyword retrieval, multi-query expansion, RRF fusion, and Cohere neural reranking
- **Structured citations** — every AI response includes confidence score and source citations (filename, page, chunk)
- **Auto-generated session titles** — LLM generates a concise title from the first message
- **Markdown rendering** — bot responses rendered with GitHub Flavored Markdown

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Browser (React)                       │
│  ┌──────────────┐  ┌────────────────┐  ┌─────────────────┐  │
│  │ SessionList  │  │   ChatPannel   │  │   FileUpload    │  │
│  │  (sidebar)   │  │  (messages)    │  │  (doc mgmt)     │  │
│  └──────┬───────┘  └───────┬────────┘  └────────┬────────┘  │
│         └──────────────────┴───────────────────┘            │
│                         App.tsx + useChat hook               │
└────────────────────────────┬────────────────────────────────┘
                             │ HTTP (fetch)
┌────────────────────────────▼────────────────────────────────┐
│                     Flask API (app.py)                       │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────┐  │
│  │    db.py    │  │ ingestion.py │  │  Azure OpenAI SDK  │  │
│  │  (SQLite)   │  │  (RAG pipe)  │  │  gpt-4o / embed    │  │
│  └─────────────┘  └──────┬───────┘  └────────────────────┘  │
│                          │                                   │
│               ┌──────────┴──────────┐                        │
│               │                     │                        │
│          ChromaDB              BM25 Index                    │
│        (vector store)        (in-memory)                     │
└─────────────────────────────────────────────────────────────┘
                             │
                    Azure Cohere Rerank
```

---

## Project Structure

```
Bot_project/
├── README.md
├── CLAUDE.md                        # AI assistant context
├── .gitignore
│
├── ChatBot-Backend/                 # Python Flask API
│   ├── app.py                       # All REST endpoints
│   ├── db.py                        # SQLite session/message CRUD
│   ├── ingestion.py                 # Chunking, embedding, RAG pipeline
│   ├── requirements.txt
│   ├── .env                         # Azure credentials (not committed)
│   ├── sessions.db                  # SQLite DB (runtime)
│   ├── chroma_db/                   # ChromaDB vector store (runtime)
│   ├── docs/                        # Uploaded files: docs/<session_id>/
│   └── tests/
│       └── test_rerank.py
│
└── ChatBot/
    └── react-ai-tool/               # React + TypeScript SPA
        ├── src/
        │   ├── App.tsx
        │   ├── hooks/useChat.ts
        │   └── components/
        │       ├── SessionList.tsx
        │       ├── ChatPannel.tsx
        │       ├── FileUpload.tsx
        │       ├── ChatHeader.tsx
        │       └── LoaderSpinner.tsx
        ├── .env.development
        ├── .env.production
        └── vite.config.ts
```

---

## Prerequisites

| Tool | Version |
|------|---------|
| Python | 3.10+ |
| Node.js | 18+ |
| npm | 9+ |
| Azure OpenAI | Deployment with `gpt-4o` and `text-embedding-3-small` |
| Azure Cohere Rerank | `cohere-rerank-v4.0-fast` deployment |

---

## Backend Setup

```bash
cd ChatBot-Backend

# Create and activate virtual environment
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS/Linux

# Install dependencies
pip install -r requirements.txt

# Configure environment variables
copy .env.example .env         # then fill in values (see table below)

# Run the server (port 5000)
python app.py
```

### Environment Variables

| Variable | Description |
|----------|-------------|
| `AZURE_OPENAI_API_KEY` | Azure OpenAI service key |
| `AZURE_OPENAI_ENDPOINT` | e.g. `https://<name>.openai.azure.com/` |
| `AZURE_OPENAI_API_VERSION` | e.g. `2023-05-15` |
| `AZURE_OPENAI_CHAT_API_VERSION` | e.g. `2024-12-01-preview` |
| `AZURE_OPENAI_CHAT_DEPLOYMENT` | Deployment name for chat model (e.g. `gpt-4o`) |
| `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` | Deployment name for embeddings (e.g. `text-embedding-3-small`) |
| `AZURE_COHERE_RERANK_ENDPOINT` | Azure-hosted Cohere Rerank endpoint URL |
| `AZURE_COHERE_API_KEY` | API key for Cohere Rerank |

---

## Frontend Setup

```bash
cd ChatBot/react-ai-tool

# Install dependencies
npm install

# Configure API base URL
# .env.development is already set to http://127.0.0.1:5000
# Edit .env.production for production deployments

# Start dev server (default port 5173)
npm run dev

# Build for production
npm run build
```

---

## Usage

1. **Start a session** — click `+` in the sidebar to create a new chat
2. **Upload documents** — drag-and-drop or click to upload PDF/TXT files in the documents panel
3. **Enable RAG** — toggle "Search uploaded docs" to ground responses in your documents
4. **Chat** — type a message and press Enter or click the send button
5. **View sources** — each response shows confidence score and source citations
6. **Switch sessions** — click any past session in the sidebar to resume it

---

## Development Phases

| Phase | Branch | Description |
|-------|--------|-------------|
| 1 | `main` (initial) | Basic multi-turn Q&A with Azure OpenAI |
| 2 | `feature/structured-output` | Structured JSON responses with confidence scores |
| 3 | `feature/rag-document-upload` | RAG pipeline with PDF/TXT upload and ChromaDB |
| 4 | `feature/chat-sessions` | SQLite session persistence, per-session docs, advanced RAG |

---

## Further Reading

- [Backend API Reference](ChatBot-Backend/README.md)
- [Frontend Component Guide](ChatBot/react-ai-tool/README.md)
