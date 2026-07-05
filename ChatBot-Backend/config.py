"""Central configuration: environment variables and app-wide constants.

All environment access lives here so the rest of the codebase reads typed,
named constants instead of scattered ``os.getenv`` calls.
"""

import os

from dotenv import load_dotenv

load_dotenv()

# ─── Azure OpenAI ─────────────────────────────────────────────────────────────
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_CHAT_API_VERSION = os.getenv("AZURE_OPENAI_CHAT_API_VERSION")
CHAT_DEPLOYMENT = os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT")

# ─── Document uploads ─────────────────────────────────────────────────────────
DOCS_DIR = "docs"
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB
ALLOWED_EXTENSIONS = {"pdf", "txt"}

# ─── Modes ────────────────────────────────────────────────────────────────────
# When true, responses include the raw retrieved contexts (used by the RAG eval harness).
EVALS_MODE = os.getenv("EVALS_MODE", "false").lower() == "true"

# ─── Agent tuning ─────────────────────────────────────────────────────────────
MAX_REFLECT_ITERATIONS = 3   # how many times the agent may act before it must answer
MAX_ANSWER_REVISIONS = 2     # how many times the quality gate may re-compose an answer
MAX_AUTO_IMPORTS = 2         # cap on sources the agent fetches on its own per turn
