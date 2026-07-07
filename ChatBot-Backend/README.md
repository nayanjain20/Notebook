# ChatBot-Backend

The Python Flask API for Notebook: chat sessions, document/URL ingestion, the
RAG pipeline, and the agentic answer loop, powered by Azure OpenAI and ChromaDB.

- **Architecture overview:** [../DESIGN.md](../DESIGN.md)
- **Detailed design & API reference:** [LLD.md](LLD.md)
- **Setup & environment variables:** [../README.md](../README.md)

## Quick start

```bash
python -m venv venv
venv\Scripts\activate            # Windows
pip install -r requirements.txt
# create .env (see ../README.md), then:
python app.py                    # http://127.0.0.1:5000
```

## Module layout

| File | Responsibility |
|------|----------------|
| `app.py` | application factory + entry point |
| `routes.py` | HTTP/SSE API (`/api` blueprint) |
| `agent.py` | reasoning loop + streaming orchestrator |
| `diagram.py` | diagram skill — fitting Mermaid type, semantic palette, safe rendering |
| `onboarding.py` | assistant's reaction when a source is added |
| `ingestion.py` | parsing, embedding, hybrid retrieval (RAG) |
| `prompts.py` | persona (`soul.md`), session memory, tool schemas |
| `llm.py` | chat facade → active provider |
| `providers/` | pluggable model backends (Azure, Ollama) via Strategy + Factory |
| `helpers.py` | pure data-shaping utilities |
| `config.py` | environment variables + constants |
| `db.py` | SQLite persistence |

## Tests

```bash
python -m pytest tests/ -v
```
