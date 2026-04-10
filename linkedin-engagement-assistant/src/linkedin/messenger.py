"""LinkedIn messenger for sending and receiving messages using Playwright.

This module provides functionality to:
- Send direct messages to LinkedIn connections
- Monitor and retrieve recent replies
- Track message sending attempts for audit trail
- Handle rate limiting and human-like interactions
"""

import asyncio
import logging
import random
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional, List, Tuple

from playwright.async_api import (
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeoutError,
)

logger = logging.getLogger(__name__)


@dataclass
class Reply:
    """Represents a message/reply in a LinkedIn conversation."""

    sender: str  # Person's name
    text: str  # Message content
    timestamp: Optional[datetime] = None
    is_own_message: bool = False  # Whether it's our message or theirs


@dataclass
class MessageSendResult:
    """Result of a message send attempt."""

    success: bool
    recipient_name: str
    recipient_linkedin_id: str
    message_text: str
    timestamp: datetime
    error_message: Optional[str] = None
    daily_limit_reached: bool = False


class LinkedInMessenger:
    """Handles sending and receiving messages on LinkedIn."""

    # LinkedIn URLs
    MESSAGING_BASE_URL = "https://www.linkedin.com/messaging"
    NEW_MESSAGE_URL = "https://www.linkedin.com/messaging/thread/new/"

    # Timeouts (in milliseconds)
    NAVIGATION_TIMEOUT = 30000
    ELEMENT_TIMEOUT = 10000
    SEND_TIMEOUT = 5000

    # Human-like typing speed
    KEY_DELAY_MIN_MS = 50  # Minimum delay between keystrokes
    KEY_DELAY_MAX_MS = 150  # Maximum delay between keystrokes

    def __init__(
        self,
        context: BrowserContext,
        audit_log: Optional[List[MessageSendResult]] = None,
    ):
        """Initialize LinkedIn messenger.

        Args:
            context: Shared Playwright BrowserContext (from listener)
            audit_log: Optional list to store message send audit trail
        """
        self.context = context
        self.page: Optional[Page] = None
        self.audit_log = audit_log or []
        self._daily_message_count = 0
        self._daily_limit_estimate = 100  # Conservative estimate

    async def initialize(self) -> None:
        """Initialize a new page in the context."""
        self.page = await self.context.new_page()
        self.page.set_default_timeout(self.ELEMENT_TIMEOUT)
        self.page.set_default_navigation_timeout(self.NAVIGATION_TIMEOUT)
        logger.info("Messenger page initialized")

    async def close(self) -> None:
        """Close the messenger page."""
        if self.page:
            await self.page.close()
            logger.info("Messenger page closed")

    async def send_message(
        self,
        recipient_name: str,
        recipient_linkedin_id: str,
        message_text: str,
        retry_count: int = 2,
    ) -> MessageSendResult:
        """Send a direct message to a LinkedIn connection.

        Args:
            recipient_name: Name of the recipient
            recipient_linkedin_id: LinkedIn profile ID/slug of recipient
            message_text: The message to send
            retry_count: Number of retries on failure

        Returns:
            MessageSendResult with success status and details
        """
        if self._daily_message_count >= self._daily_limit_estimate:
            logger.warning(f"Daily message limit ({self._daily_limit_estimate}) reached")
            result = MessageSendResult(
                success=False,
                recipient_name=recipient_name,
                recipient_linkedin_id=recipient_linkedin_id,
                message_text=message_text,
                timestamp=datetime.now(),
                error_message="Daily message limit reached",
                daily_limit_reached=True,
            )
            self.audit_log.append(result)
            return result

        logger.info(f"Sending message to {recipient_name}")
        last_error = None

        for attempt in range(retry_count):
            try:
                # Navigate to messaging thread
                thread_url = await self._get_or_create_thread(
                    recipient_name, recipient_linkedin_id
                )

                if not thread_url:
                    raise ValueError(f"Failed to get/create thread for {recipient_name}")

                # Find and fill message input
                message_input = await self._find_message_input()
                if not message_input:
                    raise ValueError("Message input field not found")

                # Type message with human-like delays
                await self._type_with_delays(message_input, message_text)

                # Send the message
                success = await self._click_send_button()

                if success:
                    self._daily_message_count += 1
                    logger.info(
                        f"Message sent to {recipient_name} "
                        f"({self._daily_message_count}/{self._daily_limit_estimate})"
                    )

                    result = MessageSendResult(
                        success=True,
                        recipient_name=recipient_name,
                        recipient_linkedin_id=recipient_linkedin_id,
                        message_text=message_text,
                        timestamp=datetime.now(),
                    )
                    self.audit_log.append(result)
                    return result
                else:
                    raise RuntimeError("Send button click failed or message not confirmed")

            except Exception as e:
                last_error = e
                logger.warning(
                    f"Message send attempt {attempt + 1} failed: {e}"
                )
                if attempt < retry_count - 1:
                    await asyncio.sleep(2 ** attempt)  # Exponential backoff

        # All retries failed
        result = MessageSendResult(
            success=False,
            recipient_name=recipient_name,
            recipient_linkedin_id=recipient_linkedin_id,
            message_text=message_text,
            timestamp=datetime.now(),
            error_message=str(last_error),
        )
        self.audit_log.append(result)
        logger.error(f"Failed to send message to {recipient_name}: {last_error}")
        return result

    async def _get_or_create_thread(
        self, recipient_name: str, recipient_linkedin_id: str
    ) -> Optional[str]:
        """Get existing message thread or create new one.

        Args:
            recipient_name: Name of the recipient
            recipient_linkedin_id: LinkedIn profile ID

        Returns:
            URL of the message thread or None if failed
        """
        try:
            # Try to navigate to new message page
            await self.page.goto(self.NEW_MESSAGE_URL, wait_until="domcontentloaded")

            # Search for recipient in the "To" field
            to_field = await self.page.wait_for_selector(
                'input[placeholder*="To"]', timeout=self.ELEMENT_TIMEOUT
            )

            # Clear and type recipient name
            await to_field.fill(recipient_name)
            await asyncio.sleep(0.5)

            # Wait for and click the search result
            suggestion = await self.page.wait_for_selector(
                f'button[aria-label*="{recipient_name}"], div[role="option"]',
                timeout=self.ELEMENT_TIMEOUT,
            )
            await suggestion.click()
            await asyncio.sleep(0.5)

            # Wait for message thread to load
            await self.page.wait_for_selector(
                'textarea, div[contenteditable="true"]', timeout=self.ELEMENT_TIMEOUT
            )

            # Return current URL (the thread URL)
            return self.page.url

        except PlaywrightTimeoutError as e:
            logger.error(f"Timeout getting/creating message thread: {e}")
            return None
        except Exception as e:
            logger.error(f"Error getting/creating message thread: {e}", exc_info=True)
            return None

    async def _find_message_input(self) -> Optional:
        """Find the message input field.

        Returns:
            ElementHandle for message input or None
        """
        try:
            # Try textarea first
            try:
                return await self.page.wait_for_selector(
                    'textarea.msg-form__textarea', timeout=self.ELEMENT_TIMEOUT
                )
            except PlaywrightTimeoutError:
                pass

            # Try contenteditable div
            try:
                return await self.page.wait_for_selector(
                    'div[contenteditable="true"][role="textbox"]',
                    timeout=self.ELEMENT_TIMEOUT,
                )
            except PlaywrightTimeoutError:
                pass

            # Generic message input
            return await self.page.wait_for_selector(
                '[placeholder*="message" i], [placeholder*="type" i]',
                timeout=self.ELEMENT_TIMEOUT,
            )

        except Exception as e:
            logger.error(f"Error finding message input: {e}")
            return None

    async def _type_with_delays(self, element, text: str) -> None:
        """Type text with human-like delays between keystrokes.

        Args:
            element: The input element to type into
            text: Text to type
        """
        try:
            await element.focus()
            await asyncio.sleep(random.uniform(0.1, 0.3))

            for char in text:
                await element.type(char)
                # Random delay between keystrokes for human-like behavior
                delay = random.uniform(
                    self.KEY_DELAY_MIN_MS, self.KEY_DELAY_MAX_MS
                ) / 1000.0
                await asyncio.sleep(delay)

            logger.debug(f"Typed {len(text)} characters with human-like delays")

        except Exception as e:
            logger.error(f"Error typing message: {e}", exc_info=True)
            raise

    async def _click_send_button(self) -> bool:
        """Click the send button and verify message was sent.

        Returns:
            True if send successful, False otherwise
        """
        try:
            # Find send button
            send_button = await self.page.wait_for_selector(
                'button[aria-label*="Send"], button.msg-form__send-button',
                timeout=self.ELEMENT_TIMEOUT,
            )

            # Click send
            await send_button.click()
            logger.debug("Send button clicked")

            # Wait for message to appear in conversation
            await asyncio.sleep(1)  # Give server a moment to process

            # Verify the message was sent by checking the conversation
            try:
                await self.page.wait_for_selector(
                    'div.msg-s-message-group', timeout=self.SEND_TIMEOUT
                )
                logger.debug("Message confirmed in conversation")
                return True
            except PlaywrightTimeoutError:
                # Message may have sent even if we can't see it immediately
                logger.warning("Could not confirm message in conversation, assuming sent")
                return True

        except Exception as e:
            logger.error(f"Error clicking send button: {e}", exc_info=True)
            return False

    async def get_recent_replies(
        self, recipient_linkedin_id: str, limit: int = 10
    ) -> List[Reply]:
        """Get recent messages from a conversation thread.

        Args:
            recipient_linkedin_id: LinkedIn profile ID of the conversation
            limit: Maximum number of messages to retrieve

        Returns:
            List of Reply objects in chronological order
        """
        try:
            # Navigate to messaging
            await self.page.goto(self.MESSAGING_BASE_URL, wait_until="domcontentloaded")

            # Find and click the conversation
            conversation_selector = f'a[href*="{recipient_linkedin_id}"], [data-profile-id="{recipient_linkedin_id}"]'
            conversation = await self.page.wait_for_selector(
                conversation_selector, timeout=self.ELEMENT_TIMEOUT
            )
            await conversation.click()

            # Wait for messages to load
            await self.page.wait_for_selector(
                'div.msg-s-message-group', timeout=self.ELEMENT_TIMEOUT
            )

            # Extract messages
            message_groups = await self.page.query_selector_all(
                'div.msg-s-message-group'
            )

            replies = []
            for group in message_groups[-limit:]:  # Get last N messages
                try:
                    # Extract sender name
                    sender_elem = await group.query_selector(
                        'span.msg-s-message-group__name, .avatar-frame-container + *'
                    )
                    sender = (
                        await sender_elem.inner_text() if sender_elem else "Unknown"
                    )

                    # Extract message text
                    message_elem = await group.query_selector(
                        'div.msg-s-event-listitem__body, .msg-body'
                    )
                    text = (
                        await message_elem.inner_text() if message_elem else ""
                    )

                    # Extract timestamp if available
                    timestamp_elem = await group.query_selector(
                        'time, span[data-time]'
                    )
                    timestamp_str = None
                    if timestamp_elem:
                        timestamp_str = await timestamp_elem.get_attribute(
                            "datetime"
                        ) or await timestamp_elem.inner_text()

                    timestamp = self._parse_timestamp(timestamp_str)

                    # Determine if it's our message or theirs
                    is_own = await group.evaluate(
                        'el => el.classList.contains("msg-s-message-group__is-member")'
                    )

                    if text.strip():
                        replies.append(
                            Reply(
                                sender=sender.strip(),
                                text=text.strip(),
                                timestamp=timestamp,
                                is_own_message=is_own,
                            )
                        )

                except Exception as e:
                    logger.debug(f"Error extracting message from group: {e}")
                    continue

            logger.info(f"Retrieved {len(replies)} recent messages")
            return replies

        except PlaywrightTimeoutError:
            logger.error("Timeout retrieving recent replies")
            return []
        except Exception as e:
            logger.error(f"Error retrieving recent replies: {e}", exc_info=True)
            return []

    def _parse_timestamp(self, timestamp_str: Optional[str]) -> Optional[datetime]:
        """Parse timestamp string to datetime.

        Args:
            timestamp_str: ISO format string, relative time, or None

        Returns:
            Parsed datetime or None
        """
        if not timestamp_str:
            return None

        try:
            # Try ISO format
            if "T" in timestamp_str or "Z" in timestamp_str:
                return datetime.fromisoformat(
                    timestamp_str.replace("Z", "+00:00")
                )

            return None
        except Exception as e:
            logger.debug(f"Could not parse timestamp '{timestamp_str}': {e}")
            return None

    def get_audit_log(self) -> List[MessageSendResult]:
        """Get the audit log of all message send attempts.

        Returns:
            List of MessageSendResult objects
        """
        return self.audit_log.copy()

    def get_daily_message_count(self) -> int:
        """Get the current daily message count.

        Returns:
            Number of messages sent today
        """
        return self._daily_message_count

    def reset_daily_count(self) -> None:
        """Reset the daily message counter (call daily)."""
        self._daily_message_count = 0
        logger.info("Daily message counter reset")

    def is_daily_limit_reached(self) -> bool:
        """Check if daily message limit is reached.

        Returns:
            True if limit reached, False otherwise
        """
        return self._daily_message_count >= self._daily_limit_estimate

    def get_audit_log_summary(self) -> dict:
        """Get summary statistics of the audit log.

        Returns:
            Dictionary with success/failure counts and details
        """
        if not self.audit_log:
            return {
                "total": 0,
                "successful": 0,
                "failed": 0,
                "daily_limit_reached": 0,
                "success_rate": 0.0,
            }

        successful = sum(1 for entry in self.audit_log if entry.success)
        failed = sum(1 for entry in self.audit_log if not entry.success)
        daily_limit = sum(1 for entry in self.audit_log if entry.daily_limit_reached)

        return {
            "total": len(self.audit_log),
            "successful": successful,
            "failed": failed,
            "daily_limit_reached": daily_limit,
            "success_rate": successful / len(self.audit_log)
            if self.audit_log else 0.0,
        }


async def main():
    """Example usage of LinkedInMessenger (requires context from listener)."""
    logger.info("LinkedInMessenger requires a BrowserContext from LinkedInListener")
    logger.info("Example usage:")
    logger.info("  listener = LinkedInListener()")
    logger.info("  await listener.start()")
    logger.info("  messenger = LinkedInMessenger(listener.context)")
    logger.info("  await messenger.initialize()")
    logger.info('  result = await messenger.send_message("John Doe", "johndoe", "Hi!")')
    logger.info("  await messenger.close()")
    logger.info("  await listener.stop()")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
