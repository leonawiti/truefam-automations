"""
Rate limiting and safety module for LinkedIn Engagement Assistant.

Tracks message sending limits and enforces deduplication to ensure safe,
compliant automated engagement with appropriate throttling.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


class RateLimiter:
    """
    Rate limiter for controlling message frequency and enforcing safety limits.

    Enforces:
    - Daily message limit (default: 20 messages/day)
    - Minimum delay between sends (default: 180 seconds)
    - Deduplication via database checks
    """

    def __init__(
        self,
        database,
        max_messages_per_day: int = 20,
        min_delay_seconds: int = 180,
    ):
        """
        Initialize rate limiter.

        Args:
            database: Database instance for tracking sends and checking duplicates
            max_messages_per_day: Maximum messages allowed per calendar day (default: 20)
            min_delay_seconds: Minimum seconds between sends (default: 180 = 3 minutes)
        """
        self.database = database
        self.max_messages_per_day = max_messages_per_day
        self.min_delay_seconds = min_delay_seconds
        self.logger = logging.getLogger(__name__)

    def can_send(self) -> bool:
        """
        Check if a message can be sent based on rate limits.

        Verifies both daily limit and minimum delay since last send.

        Returns:
            bool: True if message can be sent, False otherwise
        """
        # Check daily limit
        messages_today = self.database.get_daily_send_count()

        if messages_today >= self.max_messages_per_day:
            self.logger.warning(
                f"Daily limit reached: {messages_today}/{self.max_messages_per_day}"
            )
            return False

        # Check minimum delay since last send
        last_send = self.database.get_last_send_time()
        if last_send is not None:
            time_since_last = datetime.utcnow() - last_send
            if time_since_last.total_seconds() < self.min_delay_seconds:
                self.logger.debug(
                    f"Minimum delay not met: {time_since_last.total_seconds():.1f}s "
                    f"< {self.min_delay_seconds}s"
                )
                return False

        return True

    def record_send(self, recipient_name: str, event_type: str, message_id: str) -> None:
        """
        Record a sent message in the database.

        Args:
            recipient_name: Name of the recipient
            event_type: Type of engagement event (e.g., "comment", "message", "reply")
            message_id: Unique identifier for the sent message
        """
        self.database.update_message_status(
            message_id=message_id,
            status="sent",
            sent_at=datetime.utcnow(),
        )
        self.database.log_audit(
            action="message_sent",
            target_name=recipient_name,
            details={
                "event_type": event_type,
                "message_id": message_id,
            },
        )
        self.logger.debug(
            f"Recorded send: {recipient_name} ({event_type}) - ID: {message_id}"
        )

    def get_remaining_today(self) -> int:
        """
        Get number of messages remaining before hitting daily limit.

        Returns:
            int: Number of messages that can still be sent today (0 if limit reached)
        """
        messages_today = self.database.get_daily_send_count()
        remaining = max(0, self.max_messages_per_day - messages_today)
        return remaining

    def time_until_next_allowed(self) -> float:
        """
        Calculate seconds until next message is allowed by minimum delay rule.

        Returns:
            float: Seconds to wait (0 if message can be sent immediately)
        """
        last_send = self.database.get_last_send_time()
        if last_send is None:
            return 0.0

        time_since_last = datetime.utcnow() - last_send
        wait_time = self.min_delay_seconds - time_since_last.total_seconds()
        return max(0.0, wait_time)

    def check_duplicate(self, recipient_name: str, event_type: str) -> bool:
        """
        Check if an engagement with this person and event type has already been sent.

        Prevents duplicate outreach to the same person for the same type of engagement
        within a configurable window.

        Args:
            recipient_name: Name of the recipient
            event_type: Type of engagement event

        Returns:
            bool: True if duplicate found (don't send), False if safe to send
        """
        # Check for recent duplicate within last 7 days (168 hours)
        is_duplicate = self.database.check_duplicate(
            recipient_name=recipient_name,
            event_type=event_type,
            hours=168,
        )

        if is_duplicate:
            self.logger.warning(
                f"Duplicate detected: {recipient_name} ({event_type}) "
                f"- previous send in last 7 days"
            )
            return True

        return False

    def get_status(self) -> dict:
        """
        Get current rate limiting status.

        Returns:
            dict: Status information including daily sends, remaining quota,
                  time until next allowed, and daily reset time
        """
        messages_today = self.database.get_daily_send_count()
        remaining = self.get_remaining_today()
        wait_time = self.time_until_next_allowed()

        # Daily reset at midnight UTC
        today = datetime.utcnow().date()
        tomorrow = today + timedelta(days=1)
        reset_time = datetime.combine(tomorrow, datetime.min.time())
        time_to_reset = (reset_time - datetime.utcnow()).total_seconds()

        return {
            "messages_sent_today": messages_today,
            "daily_limit": self.max_messages_per_day,
            "remaining_today": remaining,
            "seconds_until_next_allowed": wait_time,
            "seconds_until_daily_reset": max(0, time_to_reset),
            "min_delay_seconds": self.min_delay_seconds,
        }
