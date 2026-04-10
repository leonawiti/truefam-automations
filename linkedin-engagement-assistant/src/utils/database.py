"""
SQLite database layer for LinkedIn Engagement Assistant.

Manages persistent storage of messages, reply threads, persona profiles,
audit logs, and daily statistics with thread-safe access patterns.
"""

import sqlite3
import threading
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from contextlib import contextmanager


class Database:
    """Thread-safe SQLite database manager for LinkedIn Engagement Assistant."""

    def __init__(self, db_path: str = "data/linkedin_assistant.db"):
        """
        Initialize database connection and create tables if needed.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self._lock = threading.RLock()

        # Ensure directory exists
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        # Initialize database schema
        self._init_schema()

    @contextmanager
    def _get_connection(self):
        """
        Context manager for thread-safe database connections.

        Yields:
            sqlite3.Connection: Database connection
        """
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def _init_schema(self) -> None:
        """Create database tables if they don't exist."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Messages table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    recipient_name TEXT NOT NULL,
                    recipient_linkedin_id TEXT,
                    event_type TEXT NOT NULL CHECK(event_type IN (
                        'birthday', 'anniversary', 'new_job', 'promotion',
                        'cap_mke_reply', 'simple_reply'
                    )),
                    message_text TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN (
                        'pending', 'approved', 'sent', 'rejected', 'blocked'
                    )),
                    source_tag TEXT NOT NULL CHECK(source_tag IN (
                        'MILESTONE', 'CAP_MKE_OUTREACH', 'GENERAL'
                    )),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    sent_at TIMESTAMP,
                    flagged_for_review INTEGER DEFAULT 0,
                    flag_reason TEXT,
                    UNIQUE(recipient_name, recipient_linkedin_id, event_type, created_at)
                )
            """)

            # Reply threads table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS reply_threads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    original_message_id INTEGER NOT NULL,
                    recipient_name TEXT NOT NULL,
                    recipient_linkedin_id TEXT,
                    reply_text TEXT NOT NULL,
                    reply_classification TEXT,
                    response_text TEXT,
                    response_status TEXT CHECK(response_status IN (
                        'pending', 'approved', 'sent', 'rejected'
                    )),
                    source_tag TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (original_message_id) REFERENCES messages(id)
                        ON DELETE CASCADE
                )
            """)

            # Persona profile table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS persona_profile (
                    id INTEGER PRIMARY KEY,
                    style_notes TEXT,
                    common_phrases TEXT,
                    tone_description TEXT,
                    last_refreshed TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Audit log table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    action TEXT NOT NULL,
                    target_name TEXT,
                    details TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Daily stats table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS daily_stats (
                    date DATE PRIMARY KEY,
                    messages_sent INTEGER DEFAULT 0,
                    messages_blocked INTEGER DEFAULT 0,
                    messages_flagged INTEGER DEFAULT 0
                )
            """)

            # Create indexes separately (SQLite doesn't support INDEX in CREATE TABLE)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_messages_status
                ON messages(status)
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_messages_sent_at
                ON messages(sent_at)
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_messages_created_at
                ON messages(created_at)
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_log_action
                ON audit_log(action)
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp
                ON audit_log(timestamp)
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_daily_stats_date
                ON daily_stats(date)
            """)

            conn.commit()

    def save_message(
        self,
        recipient_name: str,
        event_type: str,
        message_text: str,
        source_tag: str,
        recipient_linkedin_id: Optional[str] = None,
        flagged_for_review: bool = False,
        flag_reason: Optional[str] = None,
    ) -> int:
        """
        Save a new message to the database.

        Args:
            recipient_name: Name of message recipient
            event_type: Type of event (birthday, anniversary, etc.)
            message_text: The message content
            source_tag: Source tag (MILESTONE, CAP_MKE_OUTREACH, GENERAL)
            recipient_linkedin_id: Optional LinkedIn ID
            flagged_for_review: Whether message is flagged
            flag_reason: Reason for flagging if applicable

        Returns:
            int: ID of newly created message

        Raises:
            ValueError: If message already exists for same recipient/event within 24h
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Check for recent duplicates
            cursor.execute("""
                SELECT id FROM messages
                WHERE recipient_name = ? AND event_type = ?
                AND created_at > datetime('now', '-24 hours')
                LIMIT 1
            """, (recipient_name, event_type))

            if cursor.fetchone():
                raise ValueError(
                    f"Message already exists for {recipient_name} ({event_type}) "
                    "within 24 hours"
                )

            cursor.execute("""
                INSERT INTO messages (
                    recipient_name, recipient_linkedin_id, event_type,
                    message_text, source_tag, flagged_for_review, flag_reason
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                recipient_name, recipient_linkedin_id, event_type,
                message_text, source_tag, int(flagged_for_review),
                flag_reason
            ))

            conn.commit()
            return cursor.lastrowid

    def update_message_status(
        self,
        message_id: int,
        status: str,
        sent_at: Optional[datetime] = None,
    ) -> bool:
        """
        Update the status of a message.

        Args:
            message_id: ID of message to update
            status: New status (pending, approved, sent, rejected, blocked)
            sent_at: Timestamp when message was sent

        Returns:
            bool: True if update succeeded, False if message not found
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE messages
                SET status = ?, sent_at = ?
                WHERE id = ?
            """, (status, sent_at or datetime.now(), message_id))

            conn.commit()
            return cursor.rowcount > 0

    def get_pending_messages(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Retrieve pending messages awaiting approval.

        Args:
            limit: Maximum number of messages to retrieve

        Returns:
            List of message dictionaries
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM messages
                WHERE status = 'pending'
                ORDER BY created_at ASC
                LIMIT ?
            """, (limit,))

            return [dict(row) for row in cursor.fetchall()]

    def get_messages_for_review(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Retrieve messages flagged for manual review.

        Args:
            limit: Maximum number of messages to retrieve

        Returns:
            List of flagged message dictionaries
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM messages
                WHERE flagged_for_review = 1
                ORDER BY created_at DESC
                LIMIT ?
            """, (limit,))

            return [dict(row) for row in cursor.fetchall()]

    def save_reply_thread(
        self,
        original_message_id: int,
        recipient_name: str,
        reply_text: str,
        source_tag: str,
        recipient_linkedin_id: Optional[str] = None,
        reply_classification: Optional[str] = None,
    ) -> int:
        """
        Save a reply thread record.

        Args:
            original_message_id: ID of the original message
            recipient_name: Name of person who replied
            reply_text: Text of their reply
            source_tag: Source classification
            recipient_linkedin_id: Optional LinkedIn ID
            reply_classification: Classification of reply type

        Returns:
            int: ID of newly created reply thread record
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO reply_threads (
                    original_message_id, recipient_name, recipient_linkedin_id,
                    reply_text, reply_classification, source_tag
                )
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                original_message_id, recipient_name, recipient_linkedin_id,
                reply_text, reply_classification, source_tag
            ))

            conn.commit()
            return cursor.lastrowid

    def log_audit(
        self,
        action: str,
        target_name: Optional[str] = None,
        details: Optional[str] = None,
    ) -> int:
        """
        Log an action to the audit log.

        Args:
            action: Type of action
            target_name: Name of target (if applicable)
            details: Additional details in JSON format

        Returns:
            int: ID of audit log entry
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO audit_log (action, target_name, details)
                VALUES (?, ?, ?)
            """, (action, target_name, details))

            conn.commit()
            return cursor.lastrowid

    def get_daily_send_count(
        self,
        date: Optional[str] = None,
    ) -> int:
        """
        Get the number of messages sent on a specific date.

        Args:
            date: Date string (YYYY-MM-DD), defaults to today

        Returns:
            int: Number of messages sent
        """
        if not date:
            date = datetime.now().strftime("%Y-%m-%d")

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(*) as count FROM messages
                WHERE status = 'sent' AND DATE(sent_at) = ?
            """, (date,))

            row = cursor.fetchone()
            return row["count"] if row else 0

    def save_persona_profile(
        self,
        style_notes: str,
        common_phrases: Dict[str, Any],
        tone_description: str,
    ) -> None:
        """
        Save or update persona profile.

        Args:
            style_notes: Notes about writing style
            common_phrases: Dictionary or JSON of common phrases
            tone_description: Description of tone/voice
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Convert dict to JSON if needed
            phrases_json = (
                json.dumps(common_phrases)
                if isinstance(common_phrases, dict)
                else common_phrases
            )

            cursor.execute("""
                INSERT OR REPLACE INTO persona_profile (
                    id, style_notes, common_phrases, tone_description,
                    last_refreshed
                )
                VALUES (1, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (style_notes, phrases_json, tone_description))

            conn.commit()

    def get_persona_profile(self) -> Optional[Dict[str, Any]]:
        """
        Retrieve the current persona profile.

        Returns:
            Dictionary with persona data, or None if not set
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM persona_profile WHERE id = 1
            """)

            row = cursor.fetchone()
            if not row:
                return None

            result = dict(row)
            # Parse JSON phrases
            if result["common_phrases"]:
                try:
                    result["common_phrases"] = json.loads(result["common_phrases"])
                except (json.JSONDecodeError, TypeError):
                    pass

            return result

    def get_stats_summary(
        self,
        days: int = 7,
    ) -> Dict[str, Any]:
        """
        Get summary statistics for the past N days.

        Args:
            days: Number of days to include in summary

        Returns:
            Dictionary with aggregated statistics
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

            cursor.execute("""
                SELECT
                    COUNT(CASE WHEN status = 'sent' THEN 1 END) as sent,
                    COUNT(CASE WHEN status = 'blocked' THEN 1 END) as blocked,
                    COUNT(CASE WHEN flagged_for_review = 1 THEN 1 END) as flagged,
                    COUNT(*) as total
                FROM messages
                WHERE created_at > ?
            """, (start_date,))

            row = cursor.fetchone()
            return dict(row) if row else {
                "sent": 0,
                "blocked": 0,
                "flagged": 0,
                "total": 0,
            }

    def check_duplicate(
        self,
        recipient_name: str,
        event_type: str,
        hours: int = 24,
    ) -> bool:
        """
        Check if a message was recently sent to this recipient for this event.

        Args:
            recipient_name: Name of recipient
            event_type: Type of event
            hours: Look back this many hours

        Returns:
            True if duplicate found, False otherwise
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id FROM messages
                WHERE recipient_name = ?
                AND event_type = ?
                AND created_at > datetime('now', ? || ' hours')
                AND status IN ('approved', 'sent')
                LIMIT 1
            """, (recipient_name, event_type, -hours))

            return cursor.fetchone() is not None

    def get_last_send_time(self) -> Optional[datetime]:
        """
        Get the timestamp of the last sent message.

        Returns:
            datetime of last sent message, or None if no messages sent
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT sent_at FROM messages
                WHERE status = 'sent' AND sent_at IS NOT NULL
                ORDER BY sent_at DESC
                LIMIT 1
            """)

            row = cursor.fetchone()
            if row and row["sent_at"]:
                return datetime.fromisoformat(row["sent_at"])
            return None

    def get_messages_by_status(
        self,
        status: str,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Get messages filtered by status.

        Args:
            status: Message status to filter by
            limit: Maximum number of messages to retrieve

        Returns:
            List of message dictionaries
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM messages
                WHERE status = ?
                ORDER BY created_at DESC
                LIMIT ?
            """, (status, limit))

            return [dict(row) for row in cursor.fetchall()]

    def get_audit_logs(
        self,
        limit: int = 100,
        action_filter: Optional[str] = None,
        date_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get filtered audit logs.

        Args:
            limit: Maximum number of logs to retrieve
            action_filter: Filter by action type (optional)
            date_filter: Filter by date in YYYY-MM-DD format (optional)

        Returns:
            List of audit log dictionaries
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()

            query = "SELECT * FROM audit_log WHERE 1=1"
            params = []

            if action_filter:
                query += " AND action = ?"
                params.append(action_filter)

            if date_filter:
                query += " AND DATE(timestamp) = ?"
                params.append(date_filter)

            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)

            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def get_all_exclusion_logs(self) -> List[Dict[str, Any]]:
        """
        Get all exclusion-related audit entries.

        Returns:
            List of audit log dictionaries with exclusion actions
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM audit_log
                WHERE action LIKE '%exclusion%' OR action LIKE '%blocked%'
                ORDER BY timestamp DESC
            """)

            return [dict(row) for row in cursor.fetchall()]

    def get_daily_stats_range(
        self,
        days: int = 7,
    ) -> List[Dict[str, Any]]:
        """
        Get daily statistics for the past N days.

        Args:
            days: Number of past days to retrieve

        Returns:
            List of daily stats dictionaries ordered by date
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

            cursor.execute("""
                SELECT * FROM daily_stats
                WHERE date >= ?
                ORDER BY date DESC
            """, (start_date,))

            return [dict(row) for row in cursor.fetchall()]

    def save_daily_stats(
        self,
        date: str,
        stats_dict: Dict[str, Any],
    ) -> None:
        """
        Save or update daily statistics.

        Args:
            date: Date in YYYY-MM-DD format
            stats_dict: Dictionary with statistics keys (messages_sent, messages_blocked, messages_flagged)
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                INSERT OR REPLACE INTO daily_stats (date, messages_sent, messages_blocked, messages_flagged)
                VALUES (?, ?, ?, ?)
            """, (
                date,
                stats_dict.get("messages_sent", 0),
                stats_dict.get("messages_blocked", 0),
                stats_dict.get("messages_flagged", 0),
            ))

            conn.commit()

    def count_messages_today_by_status(
        self,
        status: str = "sent",
    ) -> int:
        """
        Count messages by status for today.

        Args:
            status: Message status to filter by (default: 'sent')

        Returns:
            Count of messages matching the criteria
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            today = datetime.now().strftime("%Y-%m-%d")

            cursor.execute("""
                SELECT COUNT(*) as count FROM messages
                WHERE status = ? AND DATE(created_at) = ?
            """, (status, today))

            row = cursor.fetchone()
            return row["count"] if row else 0

    def update_reply_thread(
        self,
        thread_id: int,
        response_text: str,
        response_status: str,
    ) -> bool:
        """
        Update a reply thread with response information.

        Args:
            thread_id: ID of the reply thread to update
            response_text: Text of the response
            response_status: Status of the response (pending, approved, sent, rejected)

        Returns:
            True if update succeeded, False if thread not found
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE reply_threads
                SET response_text = ?, response_status = ?
                WHERE id = ?
            """, (response_text, response_status, thread_id))

            conn.commit()
            return cursor.rowcount > 0
