"""Application entry point.

Builds the Flask app: configures logging, enables CORS, initialises the SQLite
store, ensures the uploads directory exists, and registers the API blueprint.
All behaviour lives in the focused modules this wires together:

    config      environment + constants
    llm         Azure OpenAI client and call helpers
    prompts     persona, session-memory rendering, tool schemas
    helpers     pure data-shaping utilities
    ingestion   document/URL parsing, embedding, hybrid retrieval (RAG)
    agent       the reasoning brain + streaming orchestrator
    onboarding  the "a source was added" reaction
    routes      the HTTP/SSE API
    db          SQLite persistence for sessions and messages
"""

import logging
import os

from flask import Flask
from flask_cors import CORS

import config
import db
from routes import api


def create_app() -> Flask:
    """Create and configure the Notebook Flask application."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )

    app = Flask(__name__)
    CORS(app)

    db.init_db()
    os.makedirs(config.DOCS_DIR, exist_ok=True)

    app.register_blueprint(api)
    return app


app = create_app()


if __name__ == "__main__":
    # threaded=True is required so Server-Sent Events can stream while other
    # requests are served.
    app.run(debug=True, port=5000, threaded=True)
