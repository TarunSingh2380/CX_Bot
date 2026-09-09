"""
PostgreSQL chat history storage.

Table: chat_history
  - Stores each message (user + assistant) keyed by token
  - Also stores email, phone, customer_id, lead_id for reference
  - message_data (JSONB) holds extra UI data (options, loanSelect, loanDocs, etc.)
  - Auto-deletes records older than 24 hours
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import psycopg2
from psycopg2.extras import RealDictCursor

log = logging.getLogger("db")

_pool = None


def _get_conn():
    global _pool
    if _pool is None or _pool.closed:
        _pool = psycopg2.connect(
            host=os.getenv("POSTGRES_HOST", "localhost"),
            port=int(os.getenv("POSTGRES_PORT", "5432")),
            dbname=os.getenv("POSTGRES_DB", "ramfincorp_chatbot"),
            user=os.getenv("POSTGRES_USER", "postgres"),
            password=os.getenv("POSTGRES_PASSWORD", ""),
            connect_timeout=10,
        )
        _pool.autocommit = True
    return _pool


def init_db():
    """Create the chat_history table if it doesn't exist."""
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS chat_history (
                    id SERIAL PRIMARY KEY,
                    token VARCHAR(512) NOT NULL,
                    email VARCHAR(255),
                    phone VARCHAR(50),
                    customer_id VARCHAR(100),
                    lead_id VARCHAR(100),
                    role VARCHAR(20) NOT NULL,
                    content TEXT NOT NULL,
                    message_data JSONB,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                );
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_chat_history_token
                ON chat_history(token);
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_chat_history_created_at
                ON chat_history(created_at);
            """)
        log.info("DB initialized — chat_history table ready")
    except Exception as exc:
        log.error("DB init failed: %s", exc)
        raise


def save_message(
    token: str,
    role: str,
    content: str,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    customer_id: Optional[str] = None,
    lead_id: Optional[str] = None,
    message_data: Optional[Dict[str, Any]] = None,
):
    """Save a single chat message to the database."""
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO chat_history
                    (token, email, phone, customer_id, lead_id, role, content, message_data)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    token,
                    email,
                    phone,
                    customer_id,
                    lead_id,
                    role,
                    content,
                    json.dumps(message_data) if message_data else None,
                ),
            )
        log.info("Saved %s message for token=%s...%s", role, token[:8], token[-4:])
    except Exception as exc:
        log.error("Failed to save message: %s", exc)


def get_history(token: str) -> List[Dict[str, Any]]:
    """Get all chat messages for a token, ordered by creation time."""
    try:
        conn = _get_conn()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT role, content, message_data, created_at
                FROM chat_history
                WHERE token = %s
                ORDER BY created_at ASC
                """,
                (token,),
            )
            rows = cur.fetchall()

        messages = []
        for row in rows:
            msg = {
                "role": row["role"],
                "content": row["content"],
                "timestamp": row["created_at"].isoformat() if row["created_at"] else None,
            }
            if row["message_data"]:
                data = row["message_data"]
                if isinstance(data, str):
                    data = json.loads(data)
                # backward compat: old records stored documents as "loanSelect"
                if "loanSelect" in data and "documents" not in data:
                    data["documents"] = data.pop("loanSelect")
                msg.update(data)
            messages.append(msg)

        log.info("Loaded %d messages for token=%s...%s", len(messages), token[:8], token[-4:])
        return messages
    except Exception as exc:
        log.error("Failed to get history: %s", exc)
        return []


def get_user_info(token: str) -> Optional[Dict[str, Optional[str]]]:
    """Get stored user info (email, phone, customer_id, lead_id) for a token."""
    try:
        conn = _get_conn()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT email, phone, customer_id, lead_id
                FROM chat_history
                WHERE token = %s
                LIMIT 1
                """,
                (token,),
            )
            row = cur.fetchone()
        if row:
            return dict(row)
        return None
    except Exception as exc:
        log.error("Failed to get user info: %s", exc)
        return None


def cleanup_old(hours: int = 24):
    """Delete chat history older than the given hours."""
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM chat_history WHERE created_at < %s",
                (cutoff,),
            )
            deleted = cur.rowcount
        if deleted > 0:
            log.info("Cleaned up %d old messages (older than %dh)", deleted, hours)
    except Exception as exc:
        log.error("Cleanup failed: %s", exc)


def delete_history(token: str):
    """Delete all chat history for a token (used on logout)."""
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM chat_history WHERE token = %s", (token,))
            deleted = cur.rowcount
        log.info("Deleted %d messages for token=%s...%s", deleted, token[:8], token[-4:])
        return deleted
    except Exception as exc:
        log.error("Failed to delete history: %s", exc)
        return 0
