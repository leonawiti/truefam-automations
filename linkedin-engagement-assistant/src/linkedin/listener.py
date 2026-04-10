"""LinkedIn notification listener using Playwright browser automation.

This module provides functionality to:
- Log into LinkedIn with session persistence
- Monitor notifications for engagement opportunities
- Extract structured data from notifications
- Detect milestone events (birthdays, anniversaries, new jobs, promotions)
"""

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List

from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeoutError,
)

logger = logging.getLogger(__name__)


@dataclass
class Notification:
    """Represents a LinkedIn notification with extracted engagement data."""

    notification_id: str
    text: str
    person_name: str
    event_type_hint: str  # 'birthday', 'work_anniversary', 'new_job', 'promotion', 'other'
    company: Optional[str] = None
    title: Optional[str] = None
    years: Optional[int] = None  # for anniversaries
    timestamp: Optional[datetime] = None
    raw_html: str = ""


class LinkedInListener:
    """Monitors LinkedIn notifications for engagement opportunities."""

    # LinkedIn URLs
    LOGIN_URL = "https://www.linkedin.com/login"
    NOTIFICATIONS_URL = "https://www.linkedin.com/notifications/"

    # Timeouts (in milliseconds)
    NAVIGATION_TIMEOUT = 30000
    ELEMENT_TIMEOUT = 10000

    # Session persistence
    COOKIES_FILE = Path.home() / ".linkedin_cookies.json"

    def __init__(
        self,
        email: Optional[str] = None,
        password: Optional[str] = None,
        cookies_file: Optional[Path] = None,
        headless: bool = True,
    ):
        """Initialize LinkedIn listener.

        Args:
            email: LinkedIn email (defaults to LINKEDIN_EMAIL env var)
            password: LinkedIn password (defaults to LINKEDIN_PASSWORD env var)
            cookies_file: Path to store/load cookies for session reuse
            headless: Run browser in headless mode
        """
        self.email = email or os.getenv("LINKEDIN_EMAIL")
        self.password = password or os.getenv("LINKEDIN_PASSWORD")
        self.cookies_file = cookies_file or self.COOKIES_FILE
        self.headless = headless

        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

        # Track seen notifications to avoid duplicates
        self._seen_notification_ids: set[str] = set()

        if not self.email or not self.password:
            raise ValueError(
                "LinkedIn credentials required: set LINKEDIN_EMAIL and "
                "LINKEDIN_PASSWORD environment variables"
            )

    async def start(self) -> None:
        """Start the browser and initialize context."""
        try:
            playwright = await async_playwright().start()
            self.browser = await playwright.chromium.launch(headless=self.headless)

            # Try to load existing session
            self.context = await self.browser.new_context()
            await self._load_cookies()

            self.page = await self.context.new_page()
            self.page.set_default_timeout(self.ELEMENT_TIMEOUT)
            self.page.set_default_navigation_timeout(self.NAVIGATION_TIMEOUT)

            logger.info("Browser started successfully")

            # Verify login or perform login
            if not await self._is_logged_in():
                await self._login()

        except Exception as e:
            logger.error(f"Failed to start browser: {e}", exc_info=True)
            raise

    async def stop(self) -> None:
        """Clean up browser resources."""
        try:
            if self.page:
                await self.page.close()
            if self.context:
                await self.context.close()
            if self.browser:
                await self.browser.close()
            logger.info("Browser closed successfully")
        except Exception as e:
            logger.error(f"Error closing browser: {e}", exc_info=True)

    async def _is_logged_in(self) -> bool:
        """Check if we're logged into LinkedIn."""
        try:
            await self.page.goto(self.NOTIFICATIONS_URL, wait_until="domcontentloaded")
            # If we see the notifications page without redirect, we're logged in
            await self.page.wait_for_selector('[data-test-id="notifications"]', timeout=5000)
            return True
        except PlaywrightTimeoutError:
            return False
        except Exception as e:
            logger.debug(f"Error checking login status: {e}")
            return False

    async def _login(self) -> None:
        """Log into LinkedIn with email and password."""
        logger.info("Logging into LinkedIn...")
        try:
            await self.page.goto(self.LOGIN_URL, wait_until="domcontentloaded")

            # Enter email
            email_input = await self.page.wait_for_selector(
                'input[name="email"]', timeout=self.ELEMENT_TIMEOUT
            )
            await email_input.fill(self.email)
            await asyncio.sleep(0.3)

            # Enter password
            password_input = await self.page.wait_for_selector(
                'input[name="password"]', timeout=self.ELEMENT_TIMEOUT
            )
            await password_input.fill(self.password)
            await asyncio.sleep(0.3)

            # Click sign in button
            sign_in_button = await self.page.wait_for_selector(
                'button[type="submit"]', timeout=self.ELEMENT_TIMEOUT
            )
            await sign_in_button.click()

            # Wait for navigation to complete
            await self.page.wait_for_url(
                lambda url: "feed" in str(url) or "notifications" in str(url),
                timeout=self.NAVIGATION_TIMEOUT,
            )

            # Save cookies for future sessions
            await self._save_cookies()
            logger.info("Login successful")

        except PlaywrightTimeoutError as e:
            logger.error(f"Login timeout: {e}")
            raise ValueError("LinkedIn login failed: timeout waiting for login elements")
        except Exception as e:
            logger.error(f"Login failed: {e}", exc_info=True)
            raise

    async def _save_cookies(self) -> None:
        """Save browser cookies to file."""
        try:
            cookies = await self.context.cookies()
            self.cookies_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.cookies_file, "w") as f:
                json.dump(cookies, f)
            logger.debug(f"Cookies saved to {self.cookies_file}")
        except Exception as e:
            logger.warning(f"Failed to save cookies: {e}")

    async def _load_cookies(self) -> None:
        """Load cookies from file if available."""
        try:
            if self.cookies_file.exists():
                with open(self.cookies_file, "r") as f:
                    cookies = json.load(f)
                await self.context.add_cookies(cookies)
                logger.debug(f"Cookies loaded from {self.cookies_file}")
        except Exception as e:
            logger.warning(f"Failed to load cookies: {e}")

    async def fetch_notifications(self, limit: int = 20) -> List[Notification]:
        """Fetch and parse notifications from LinkedIn.

        Args:
            limit: Maximum number of notifications to fetch

        Returns:
            List of parsed Notification objects
        """
        logger.info(f"Fetching notifications (limit: {limit})...")
        try:
            await self.page.goto(
                self.NOTIFICATIONS_URL, wait_until="domcontentloaded"
            )

            # Wait for notifications list to load
            await self.page.wait_for_selector(
                '[data-test-id="notifications-list"]', timeout=self.ELEMENT_TIMEOUT
            )

            # Scroll to load more notifications if needed
            for _ in range(3):  # Scroll 3 times to load additional notifications
                await self.page.evaluate("window.scrollBy(0, window.innerHeight)")
                await asyncio.sleep(0.5)

            # Extract notification items
            notification_elements = await self.page.query_selector_all(
                '[data-test-id*="notification-item"]'
            )

            notifications = []
            for element in notification_elements[:limit]:
                try:
                    html = await element.inner_html()
                    notification = await self._parse_notification_element(element, html)
                    if notification:
                        # Deduplication check
                        if notification.notification_id not in self._seen_notification_ids:
                            notifications.append(notification)
                            self._seen_notification_ids.add(notification.notification_id)
                except Exception as e:
                    logger.debug(f"Error parsing notification element: {e}")
                    continue

            logger.info(f"Fetched {len(notifications)} new notifications")
            return notifications

        except PlaywrightTimeoutError:
            logger.error("Timeout waiting for notifications to load")
            return []
        except Exception as e:
            logger.error(f"Error fetching notifications: {e}", exc_info=True)
            return []

    async def _parse_notification_element(
        self, element, html: str
    ) -> Optional[Notification]:
        """Parse a single notification element.

        Args:
            element: Playwright element handle
            html: Inner HTML of the element

        Returns:
            Parsed Notification object or None if parse fails
        """
        try:
            # Extract text content
            text = await element.inner_text()
            text = text.strip()

            if not text:
                return None

            # Generate notification ID from content
            notification_id = self._generate_notification_id(text)

            # Extract person name (usually first part of text)
            person_name = self._extract_person_name(text)

            # Detect event type and extract metadata
            event_type_hint, company, title, years = self._detect_event_type(text)

            # Try to extract timestamp
            timestamp_str = await self._extract_timestamp(element)
            timestamp = self._parse_timestamp(timestamp_str) if timestamp_str else None

            return Notification(
                notification_id=notification_id,
                text=text,
                person_name=person_name,
                event_type_hint=event_type_hint,
                company=company,
                title=title,
                years=years,
                timestamp=timestamp,
                raw_html=html,
            )

        except Exception as e:
            logger.debug(f"Error parsing notification: {e}")
            return None

    def _generate_notification_id(self, text: str) -> str:
        """Generate a deterministic ID from notification text."""
        import hashlib

        return hashlib.md5(text.encode()).hexdigest()[:12]

    def _extract_person_name(self, text: str) -> str:
        """Extract person's name from notification text."""
        # Usually the first few words before punctuation or specific keywords
        words = text.split()
        for i, word in enumerate(words):
            if i > 3:  # Don't go beyond first few words
                break
            if any(keyword in word.lower() for keyword in ["birthday", "anniversary"]):
                return " ".join(words[:i])

        # Default to first two words
        return " ".join(words[:2]) if len(words) >= 2 else words[0]

    def _detect_event_type(
        self, text: str
    ) -> tuple[str, Optional[str], Optional[str], Optional[int]]:
        """Detect event type and extract related metadata.

        Returns:
            Tuple of (event_type, company, title, years)
        """
        text_lower = text.lower()
        company = None
        title = None
        years = None

        # Detect birthday
        if "birthday" in text_lower or "celebrating" in text_lower:
            return "birthday", company, title, years

        # Detect work anniversary
        if "work anniversary" in text_lower or "anniversary" in text_lower:
            years_match = re.search(r"(\d+)\s*(?:year|yr)", text_lower)
            years = int(years_match.group(1)) if years_match else None
            return "work_anniversary", company, title, years

        # Detect new job
        if (
            "new job" in text_lower
            or "started" in text_lower
            or "joined" in text_lower
        ):
            # Try to extract company name
            company = self._extract_company_name(text)
            title = self._extract_job_title(text)
            return "new_job", company, title, years

        # Detect promotion
        if (
            "promoted" in text_lower
            or "promotion" in text_lower
            or "new role" in text_lower
        ):
            company = self._extract_company_name(text)
            title = self._extract_job_title(text)
            return "promotion", company, title, years

        return "other", company, title, years

    def _extract_company_name(self, text: str) -> Optional[str]:
        """Extract company name from notification text."""
        # Look for text in quotes or after "at" keyword
        at_match = re.search(r"at\s+([A-Z][^,\n]+)", text)
        if at_match:
            return at_match.group(1).strip()

        quote_match = re.search(r'"([^"]+)"', text)
        if quote_match:
            return quote_match.group(1).strip()

        return None

    def _extract_job_title(self, text: str) -> Optional[str]:
        """Extract job title from notification text."""
        # Look for text between "as" and "at"
        title_match = re.search(r"as\s+([^,]+?)\s+at", text, re.IGNORECASE)
        if title_match:
            return title_match.group(1).strip()

        return None

    async def _extract_timestamp(self, element) -> Optional[str]:
        """Extract timestamp string from notification element."""
        try:
            # Look for time element or timestamp attribute
            time_element = await element.query_selector("time")
            if time_element:
                return await time_element.get_attribute("datetime")

            # Try to find text like "1h ago", "2d ago", etc.
            text = await element.inner_text()
            time_match = re.search(r"(\d+[hd]|now)\s+ago", text)
            if time_match:
                return time_match.group(1)

            return None
        except Exception as e:
            logger.debug(f"Error extracting timestamp: {e}")
            return None

    def _parse_timestamp(self, timestamp_str: str) -> Optional[datetime]:
        """Parse relative timestamp string to datetime."""
        try:
            timestamp_str = timestamp_str.strip()

            if timestamp_str == "now":
                return datetime.now()

            # Parse relative times like "1h ago", "2d ago"
            match = re.match(r"(\d+)([hd])", timestamp_str)
            if match:
                amount = int(match.group(1))
                unit = match.group(2)

                if unit == "h":
                    return datetime.now() - timedelta(hours=amount)
                elif unit == "d":
                    return datetime.now() - timedelta(days=amount)

            # Try ISO format
            if "T" in timestamp_str:
                return datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))

            return None
        except Exception as e:
            logger.debug(f"Error parsing timestamp '{timestamp_str}': {e}")
            return None

    async def poll_notifications(
        self, interval_seconds: int = 300, max_iterations: Optional[int] = None
    ) -> None:
        """Poll LinkedIn notifications at regular intervals.

        Args:
            interval_seconds: Time between polls (default 5 minutes)
            max_iterations: Maximum number of polls (None for infinite)
        """
        iteration = 0
        logger.info(
            f"Starting notification polling (interval: {interval_seconds}s, "
            f"max iterations: {max_iterations or 'infinite'})"
        )

        try:
            while max_iterations is None or iteration < max_iterations:
                try:
                    notifications = await self.fetch_notifications()
                    if notifications:
                        logger.info(f"Poll #{iteration}: Found {len(notifications)} notifications")
                        for notif in notifications:
                            logger.info(
                                f"  - {notif.person_name}: {notif.event_type_hint} "
                                f"(company: {notif.company})"
                            )

                    iteration += 1
                    if max_iterations is None or iteration < max_iterations:
                        await asyncio.sleep(interval_seconds)

                except Exception as e:
                    logger.error(f"Error in polling loop: {e}", exc_info=True)
                    await asyncio.sleep(interval_seconds)

        except KeyboardInterrupt:
            logger.info("Polling stopped by user")

    def get_seen_notifications(self) -> set[str]:
        """Get the set of notification IDs already seen."""
        return self._seen_notification_ids.copy()

    def clear_seen_notifications(self) -> None:
        """Clear the cache of seen notification IDs."""
        self._seen_notification_ids.clear()
        logger.info("Seen notifications cache cleared")


async def main():
    """Example usage of LinkedInListener."""
    listener = LinkedInListener()
    try:
        await listener.start()
        notifications = await listener.fetch_notifications(limit=10)

        for notif in notifications:
            print(f"\n{notif.person_name} - {notif.event_type_hint}")
            print(f"  Text: {notif.text}")
            print(f"  Company: {notif.company}")
            print(f"  Title: {notif.title}")
            print(f"  Timestamp: {notif.timestamp}")

    finally:
        await listener.stop()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
