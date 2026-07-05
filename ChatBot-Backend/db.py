import json
import sqlite3
import uuid
from datetime import datetime, timezone

DB_PATH = "sessions.db"
USER_ID = "default"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id          TEXT PRIMARY KEY,
                user_id     TEXT NOT NULL DEFAULT 'default',
                title       TEXT NOT NULL DEFAULT 'New Chat',
                context     TEXT,
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            )
        """)
        # Migrate older databases that predate the session context column.
        session_cols = {row[1] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()}
        if "context" not in session_cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN context TEXT")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT NOT NULL,
                role        TEXT NOT NULL,
                parts       TEXT NOT NULL,
                confidence  REAL,
                sources     TEXT,
                follow_ups  TEXT,
                suggested_links TEXT,
                diagram     TEXT,
                steps       TEXT,
                created_at  TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            )
        """)
        # Migrate older databases that predate newer columns.
        existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(messages)").fetchall()}
        if "follow_ups" not in existing_cols:
            conn.execute("ALTER TABLE messages ADD COLUMN follow_ups TEXT")
        if "suggested_links" not in existing_cols:
            conn.execute("ALTER TABLE messages ADD COLUMN suggested_links TEXT")
        if "diagram" not in existing_cols:
            conn.execute("ALTER TABLE messages ADD COLUMN diagram TEXT")
        if "steps" not in existing_cols:
            conn.execute("ALTER TABLE messages ADD COLUMN steps TEXT")
        conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─── Sessions ────────────────────────────────────────────────────────────────

def create_session() -> dict:
    sid = str(uuid.uuid4())
    now = _now()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO sessions (id, user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (sid, USER_ID, "New Chat", now, now),
        )
        conn.commit()
    return {"id": sid, "title": "New Chat", "created_at": now, "updated_at": now}


def list_sessions() -> list:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, title, created_at, updated_at FROM sessions WHERE user_id = ? ORDER BY updated_at DESC",
            (USER_ID,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_session_messages(session_id: str) -> list:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT role, parts, confidence, sources, follow_ups, suggested_links, diagram, steps FROM messages WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        ).fetchall()
    result = []
    for r in rows:
        msg = {"role": r["role"], "parts": json.loads(r["parts"])}
        if r["confidence"] is not None:
            msg["confidence"] = r["confidence"]
        if r["sources"]:
            msg["sources"] = json.loads(r["sources"])
        if r["follow_ups"]:
            msg["follow_ups"] = json.loads(r["follow_ups"])
        if r["suggested_links"]:
            msg["suggested_links"] = json.loads(r["suggested_links"])
        if r["diagram"]:
            msg["diagram"] = json.loads(r["diagram"])
        if r["steps"]:
            msg["steps"] = json.loads(r["steps"])
        result.append(msg)
    return result


def delete_session(session_id: str):
    with _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        conn.commit()


def update_session_title(session_id: str, title: str):
    with _connect() as conn:
        conn.execute("UPDATE sessions SET title = ? WHERE id = ?", (title, session_id))
        conn.commit()


def get_session_context(session_id: str) -> dict:
    with _connect() as conn:
        row = conn.execute("SELECT context FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if row and row["context"]:
        try:
            return json.loads(row["context"])
        except (ValueError, TypeError):
            return {}
    return {}


def update_session_context(session_id: str, context: dict):
    with _connect() as conn:
        conn.execute("UPDATE sessions SET context = ? WHERE id = ?", (json.dumps(context), session_id))
        conn.commit()


def _set_title(session_id: str, title: str, conn: sqlite3.Connection):
    conn.execute("UPDATE sessions SET title = ? WHERE id = ?", (title, session_id))


# ─── Messages ────────────────────────────────────────────────────────────────

def is_first_message(session_id: str) -> bool:
    with _connect() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ?", (session_id,)
        ).fetchone()[0] == 0


def save_message(session_id: str, role: str, parts: list, confidence=None, sources=None,
                 follow_ups=None, suggested_links=None, diagram=None, steps=None):
    now = _now()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO messages (session_id, role, parts, confidence, sources, follow_ups, suggested_links, diagram, steps, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, role, json.dumps(parts), confidence,
             json.dumps(sources) if sources is not None else None,
             json.dumps(follow_ups) if follow_ups is not None else None,
             json.dumps(suggested_links) if suggested_links is not None else None,
             json.dumps(diagram) if diagram is not None else None,
             json.dumps(steps) if steps is not None else None,
             now),
        )
        conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))
        conn.commit()
