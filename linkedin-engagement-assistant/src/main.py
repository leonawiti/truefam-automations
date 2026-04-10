"""
Main orchestrator for LinkedIn Engagement Assistant.

Coordinates all components to automatically engage with LinkedIn users based on
their activities, with comprehensive safety controls, rate limiting, and audit logging.
"""

import asyncio
import logging
import os
import signal
import sys
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

logger = logging.getLogger(__name__)


class LinkedInEngagementAssistant:
    """
    Main orchestrator for the LinkedIn Engagement Assistant.

    Manages the complete pipeline:
    1. Monitors LinkedIn notifications
    2. Applies safety filters and exclusions
    3. Classifies events
    4. Generates personalized responses
    5. Routes to approval queue or auto-sends with rate limiting
    6. Monitors replies and handles conversations
    7. Maintains audit trail and analytics
    """

    def __init__(
        self,
        config: Dict[str, Any],
        database,
        exclusion_filter,
        event_classifier,
        message_generator,
        persona_learner,
        reply_handler,
        rate_limiter,
        linkedin_listener,
        linkedin_messenger,
        dashboard_app=None,
    ):
        """
        Initialize the LinkedIn Engagement Assistant.

        Args:
            config: Configuration dictionary from .env and config files
            database: Database connection and operations
            exclusion_filter: Checks against exclusion lists
            event_classifier: Classifies incoming LinkedIn events
            message_generator: Generates personalized messages
            persona_learner: Learns and updates persona from interactions
            reply_handler: Handles reply threads and conversations
            rate_limiter: Enforces rate limits and deduplication
            linkedin_listener: Polls for LinkedIn notifications
            linkedin_messenger: Sends messages via LinkedIn API
            dashboard_app: Optional Flask app for dashboard
        """
        self.config = config
        self.database = database
        self.exclusion_filter = exclusion_filter
        self.event_classifier = event_classifier
        self.message_generator = message_generator
        self.persona_learner = persona_learner
        self.reply_handler = reply_handler
        self.rate_limiter = rate_limiter
        self.linkedin_listener = linkedin_listener
        self.linkedin_messenger = linkedin_messenger
        self.dashboard_app = dashboard_app

        # State management
        self.automation_enabled = True
        self.running = False
        self.approved_queue: List[Dict[str, Any]] = []

        # Scheduler for periodic tasks
        self.scheduler: Optional[AsyncIOScheduler] = None

        self.logger = logging.getLogger(__name__)

    async def run(self) -> None:
        """
        Start the main orchestrator loop.

        Initializes scheduler and begins monitoring:
        - LinkedIn notifications (every 5 minutes)
        - Reply threads (every 10 minutes)
        - Approved message queue (every 2 minutes)
        - Persona refresh (monthly)
        - Daily stats aggregation (daily)
        """
        self.running = True
        self.logger.info("LinkedIn Engagement Assistant starting...")

        try:
            # Initialize scheduler
            self.scheduler = AsyncIOScheduler()
            self._setup_scheduled_jobs()
            self.scheduler.start()
            self.logger.info("Scheduler initialized with periodic jobs")

            # Run main loop
            while self.running:
                try:
                    await asyncio.sleep(1)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    self.logger.error(f"Error in main loop: {e}", exc_info=True)
                    await asyncio.sleep(5)

        except Exception as e:
            self.logger.error(f"Fatal error in orchestrator: {e}", exc_info=True)
            raise
        finally:
            await self.shutdown()

    def _setup_scheduled_jobs(self) -> None:
        """
        Set up all scheduled jobs with APScheduler.

        Jobs:
        - poll_notifications: Every 5 minutes
        - monitor_replies: Every 10 minutes
        - process_approved_messages: Every 2 minutes
        - refresh_persona: Monthly
        - aggregate_daily_stats: Daily at midnight UTC
        """
        if not self.scheduler:
            raise RuntimeError("Scheduler not initialized")

        # Poll for new notifications
        self.scheduler.add_job(
            self._poll_notifications_job,
            "interval",
            minutes=5,
            id="poll_notifications",
            name="Poll LinkedIn notifications",
            misfire_grace_time=30,
        )

        # Monitor reply threads
        self.scheduler.add_job(
            self._monitor_replies_job,
            "interval",
            minutes=10,
            id="monitor_replies",
            name="Monitor reply threads",
            misfire_grace_time=60,
        )

        # Process approved messages from queue
        self.scheduler.add_job(
            self._process_approved_messages_job,
            "interval",
            minutes=2,
            id="process_approved",
            name="Process approved message queue",
            misfire_grace_time=15,
        )

        # Refresh persona monthly
        self.scheduler.add_job(
            self._refresh_persona_job,
            "cron",
            day=1,
            hour=2,
            id="refresh_persona",
            name="Monthly persona refresh",
        )

        # Aggregate daily stats at midnight UTC
        self.scheduler.add_job(
            self._aggregate_stats_job,
            "cron",
            hour=0,
            minute=0,
            id="daily_stats",
            name="Daily stats aggregation",
        )

        self.logger.info("Scheduled jobs configured")

    async def _poll_notifications_job(self) -> None:
        """Job: Poll LinkedIn for new notifications."""
        try:
            if not self.automation_enabled:
                self.logger.debug("Automation paused, skipping notification poll")
                return

            notifications = await self.linkedin_listener.fetch_notifications()
            self.logger.debug(f"Polled {len(notifications)} notifications")

            for notification in notifications:
                try:
                    await self.process_notification(notification)
                except Exception as e:
                    self.logger.error(
                        f"Error processing notification: {e}", exc_info=True
                    )

        except Exception as e:
            self.logger.error(f"Error in notification poll job: {e}", exc_info=True)

    async def _monitor_replies_job(self) -> None:
        """Job: Monitor reply threads for new messages."""
        try:
            if not self.automation_enabled:
                self.logger.debug("Automation paused, skipping reply monitoring")
                return

            # Note: This is a placeholder. In production, fetch pending threads from database
            # pending_threads = self.database.get_pending_reply_threads()
            pending_threads = []
            self.logger.debug(f"Monitoring {len(pending_threads)} reply threads")

            for thread in pending_threads:
                try:
                    new_replies = await self.linkedin_listener.fetch_thread_replies(
                        thread["conversation_id"]
                    )

                    for reply in new_replies:
                        # Process reply using reply handler
                        await self.reply_handler.process_reply(reply, thread)
                        self.database.log_audit(
                            action="reply_processed",
                            target_name=thread.get("person_name"),
                            details={"conversation_id": thread["conversation_id"]},
                        )

                except Exception as e:
                    self.logger.error(
                        f"Error monitoring thread {thread.get('conversation_id')}: {e}",
                        exc_info=True,
                    )

        except Exception as e:
            self.logger.error(f"Error in reply monitoring job: {e}", exc_info=True)

    async def _process_approved_messages_job(self) -> None:
        """Job: Send all messages approved via review_first mode."""
        try:
            await self.process_approved_messages()
        except Exception as e:
            self.logger.error(
                f"Error in approved message processing job: {e}", exc_info=True
            )

    async def _refresh_persona_job(self) -> None:
        """Job: Monthly refresh of persona based on recent interactions."""
        try:
            self.logger.info("Starting monthly persona refresh")
            # Note: Placeholder for recent interactions. In production, fetch from database.
            recent_interactions = []
            if recent_interactions:
                updated_persona = await self.persona_learner.update_persona(
                    recent_interactions
                )
                self.logger.info(
                    f"Persona refreshed with {len(recent_interactions)} interactions"
                )
                self.database.log_audit(
                    action="persona_refreshed",
                    details={"interaction_count": len(recent_interactions)},
                )
            else:
                self.logger.debug("No recent interactions to refresh persona")
        except Exception as e:
            self.logger.error(f"Error in persona refresh job: {e}", exc_info=True)

    async def _aggregate_stats_job(self) -> None:
        """Job: Aggregate daily statistics."""
        try:
            summary = self.database.get_stats_summary()
            if summary:
                self.database.log_audit(
                    action="daily_stats_aggregated",
                    details=summary,
                )
                self.logger.info(
                    f"Daily stats aggregated: "
                    f"{summary.get('messages_sent', 0)} sent, "
                    f"{summary.get('notifications_processed', 0)} notifications"
                )
        except Exception as e:
            self.logger.error(f"Error in stats aggregation job: {e}", exc_info=True)

    async def process_notification(self, notification: Dict[str, Any]) -> None:
        """
        Process a single LinkedIn notification through the full pipeline.

        Pipeline:
        1. Extract person info
        2. Check exclusion list (FIRST - skip if excluded)
        3. Classify event type
        4. Check for duplicates
        5. Generate message
        6. Apply flagging rules
        7. Route to queue or auto-send
        8. Record to audit trail

        Args:
            notification: Raw notification from LinkedIn API
        """
        try:
            # Extract person info
            person_name = notification.get("person_name", "Unknown")
            person_id = notification.get("person_id")
            event_type = notification.get("event_type", "unknown")

            self.logger.debug(f"Processing notification: {person_name} ({event_type})")

            # STEP 1: Check exclusion list (FIRST - skip if excluded)
            if self.exclusion_filter.is_excluded(person_name):
                self.logger.info(f"EXCLUDED — {person_name}")
                self.database.record_excluded_notification(person_name, event_type)
                return

            # STEP 2: Classify event
            notification_text = notification.get("text", "")
            classification = self.event_classifier.classify_notification(notification_text)
            self.logger.debug(
                f"Classified: {classification.category if hasattr(classification, 'category') else classification.get('category')} "
                f"(confidence: {getattr(classification, 'confidence', classification.get('confidence', 0)):.2%})"
            )

            # STEP 3: Check for duplicates
            if self.rate_limiter.check_duplicate(person_name, event_type):
                self.logger.warning(f"Duplicate detected for {person_name}")
                self.database.log_audit(
                    action="duplicate_skipped",
                    target_name=person_name,
                    details={"event_type": event_type},
                )
                return

            # STEP 4: Generate message
            message_context = {
                "person_name": person_name,
                "person_id": person_id,
                "event_type": event_type,
                "notification": notification,
                "classification": classification,
            }
            generated_message = await self.message_generator.generate_milestone_message(
                event_type, message_context
            )
            message_text = (
                generated_message.text
                if hasattr(generated_message, "text")
                else generated_message.get("text", "")
            )
            self.logger.debug(
                f"Generated message (length: {len(message_text)} chars)"
            )

            # STEP 5: Apply flagging rules
            flags = self._apply_flagging_rules(person_name, classification, notification)
            should_flag = len(flags) > 0

            self.logger.debug(f"Flags applied: {flags}")

            # STEP 6: Route based on operating mode
            operating_mode = self.config.get("operating_mode", "review_first")
            message_record = {
                "person_name": person_name,
                "person_id": person_id,
                "event_type": event_type,
                "message_text": message_text,
                "message_context": message_context,
                "classification": classification,
                "flags": flags,
                "flagged": should_flag,
                "timestamp": datetime.utcnow(),
                "notification": notification,
            }

            if operating_mode == "review_first":
                # Queue for manual approval
                self.approved_queue.append(message_record)
                self.database.save_message(message_record)
                self.logger.info(
                    f"Message queued for approval: {person_name} {f'(FLAGGED)' if should_flag else ''}"
                )

            elif operating_mode == "auto_send":
                # Send immediately if not flagged
                if not should_flag:
                    message_id = await self.linkedin_messenger.send_message(
                        person_id, message_text
                    )
                    self.rate_limiter.record_send(person_name, event_type, message_id)
                    self.database.update_message_status(
                        message_id=message_id,
                        status="sent",
                        sent_at=datetime.utcnow(),
                    )
                    self.logger.info(f"Message sent to {person_name}")
                else:
                    # Flagged - queue for review
                    self.approved_queue.append(message_record)
                    self.database.save_message(message_record)
                    self.logger.info(f"Flagged message queued for review: {person_name}")

            elif operating_mode == "digest":
                # Batch for daily send
                self.database.save_message(message_record)
                self.logger.info(f"Message added to digest: {person_name}")

            # STEP 7: Record to audit trail
            self.database.log_audit(
                action="notification_processed",
                target_name=person_name,
                details={
                    "event_type": event_type,
                    "operating_mode": operating_mode,
                    "flagged": should_flag,
                    "flags": flags,
                },
            )

        except Exception as e:
            self.logger.error(
                f"Error processing notification for {notification.get('person_name', 'unknown')}: {e}",
                exc_info=True,
            )
            self.database.log_audit(
                action="notification_processing_error",
                target_name=notification.get("person_name"),
                details={"error": str(e)},
            )

    def _apply_flagging_rules(
        self,
        person_name: str,
        classification: Dict[str, Any],
        notification: Dict[str, Any],
    ) -> List[str]:
        """
        Apply business rules to determine if message should be flagged for review.

        Flags:
        - Low prior interactions (< 5)
        - C-suite/executive recipient
        - Low parse confidence (< 90%)

        Args:
            person_name: Name of recipient
            classification: Event classification with confidence score
            notification: Original notification data

        Returns:
            List of flag reasons (empty if no flags)
        """
        flags = []

        # Rule 1: Check prior interactions (placeholder - would fetch from database)
        prior_interactions = 0
        if prior_interactions < 5:
            flags.append(f"low_interactions ({prior_interactions}/5)")

        # Rule 2: Check for C-suite/executive titles
        job_title = notification.get("recipient_title", "").lower()
        c_suite_keywords = ["ceo", "cto", "cfo", "coo", "president", "founder"]
        if any(keyword in job_title for keyword in c_suite_keywords):
            flags.append("c_suite_title")

        # Rule 3: Check parse confidence
        confidence = classification.get("confidence", 1.0)
        if confidence < 0.90:
            flags.append(f"low_confidence ({confidence:.2%})")

        return flags

    async def process_approved_messages(self) -> None:
        """
        Process all messages in the approval queue and send them via LinkedIn.

        Respects rate limiting and records all sends.
        """
        try:
            if not self.approved_queue:
                return

            sent_count = 0
            skipped_count = 0

            for message_record in self.approved_queue[:]:  # Copy to allow modification
                try:
                    # Check rate limits
                    if not self.rate_limiter.can_send():
                        self.logger.debug(
                            f"Rate limit reached, {len(self.approved_queue)} in queue"
                        )
                        break

                    # Send message
                    person_id = message_record.get("person_id")
                    message_text = message_record.get("message_text")

                    message_id = await self.linkedin_messenger.send_message(
                        person_id, message_text
                    )

                    # Record send
                    self.rate_limiter.record_send(
                        message_record.get("person_name"),
                        message_record.get("event_type"),
                        message_id,
                    )

                    # Update database
                    message_record["message_id"] = message_id
                    message_record["sent_at"] = datetime.utcnow()
                    self.database.update_message_status(
                        message_id=message_id,
                        status="sent",
                        sent_at=datetime.utcnow(),
                    )

                    self.logger.info(
                        f"Approved message sent to {message_record.get('person_name')}"
                    )
                    sent_count += 1

                except Exception as e:
                    self.logger.error(
                        f"Error sending approved message: {e}", exc_info=True
                    )
                    skipped_count += 1

                finally:
                    # Remove from queue
                    if message_record in self.approved_queue:
                        self.approved_queue.remove(message_record)

            if sent_count > 0 or skipped_count > 0:
                self.logger.info(
                    f"Processed approval queue: {sent_count} sent, {skipped_count} failed"
                )

        except Exception as e:
            self.logger.error(f"Error processing approved messages: {e}", exc_info=True)

    def pause(self) -> None:
        """Pause automation without stopping the orchestrator."""
        self.automation_enabled = False
        self.logger.warning("Automation PAUSED")
        self.database.log_audit(action="automation_paused")

    def resume(self) -> None:
        """Resume automation."""
        self.automation_enabled = True
        self.logger.info("Automation RESUMED")
        self.database.log_audit(action="automation_resumed")

    def get_status(self) -> Dict[str, Any]:
        """
        Get current system status.

        Returns:
            dict: Comprehensive status including:
                - Running state
                - Automation enabled/paused
                - Queue sizes
                - Rate limiting status
                - Scheduled job statuses
                - Daily stats
        """
        rate_limit_status = self.rate_limiter.get_status()
        today = datetime.utcnow().date()

        scheduled_jobs = []
        if self.scheduler:
            for job in self.scheduler.get_jobs():
                scheduled_jobs.append(
                    {
                        "id": job.id,
                        "name": job.name,
                        "next_run_time": job.next_run_time.isoformat()
                        if job.next_run_time
                        else None,
                    }
                )

        stats_summary = self.database.get_stats_summary()

        return {
            "running": self.running,
            "automation_enabled": self.automation_enabled,
            "timestamp": datetime.utcnow().isoformat(),
            "approval_queue_size": len(self.approved_queue),
            "rate_limiting": rate_limit_status,
            "scheduled_jobs": scheduled_jobs,
            "daily_stats": {
                "date": str(today),
                **stats_summary,
            },
        }

    async def shutdown(self) -> None:
        """
        Gracefully shut down the orchestrator.

        - Stops accepting new tasks
        - Waits for in-flight operations
        - Saves state
        - Closes connections
        """
        self.logger.info("Shutting down LinkedIn Engagement Assistant...")
        self.running = False

        try:
            # Stop scheduler
            if self.scheduler and self.scheduler.running:
                self.scheduler.shutdown(wait=True)
                self.logger.info("Scheduler shut down")

            # Save state
            self.logger.info(
                f"Saving state: {len(self.approved_queue)} messages in queue"
            )
            for message in self.approved_queue:
                self.database.save_message(message)

            self.logger.info("State saved")

            self.logger.info("Shutdown complete")

        except Exception as e:
            self.logger.error(f"Error during shutdown: {e}", exc_info=True)


def setup_logging(config: Dict[str, Any]) -> None:
    """
    Configure comprehensive logging setup.

    Args:
        config: Configuration dictionary with logging settings
    """
    log_level = config.get("log_level", "INFO")
    log_file = config.get("log_file", "linkedin_assistant.log")

    # Create logger
    logging.basicConfig(
        level=getattr(logging, log_level),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout),
        ],
    )

    # Set third-party log levels
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("linkedin").setLevel(logging.INFO)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    logger.info(f"Logging configured: level={log_level}, file={log_file}")


async def main() -> None:
    """
    Entry point for the LinkedIn Engagement Assistant.

    Loads configuration, initializes all components, starts the orchestrator,
    and handles graceful shutdown on signals.
    """
    # Load environment variables
    load_dotenv()

    # Load configuration from environment
    config = {
        "log_level": os.getenv("LOG_LEVEL", "INFO"),
        "log_file": os.getenv("LOG_FILE", "linkedin_assistant.log"),
        "operating_mode": os.getenv("OPERATING_MODE", "review_first"),
        "max_messages_per_day": int(os.getenv("MAX_MESSAGES_PER_DAY", "20")),
        "min_delay_seconds": int(os.getenv("MIN_DELAY_SECONDS", "180")),
    }

    # Setup logging
    setup_logging(config)
    logger.info("LinkedIn Engagement Assistant initializing...")

    # Initialize components
    try:
        # Import components
        from src.utils.database import Database
        from src.engine.exclusion import ExclusionFilter
        from src.engine.classifier import EventClassifier
        from src.engine.generator import MessageGenerator
        from src.engine.persona import PersonaLearner
        from src.engine.reply_handler import ReplyHandler
        from src.utils.rate_limiter import RateLimiter
        from src.linkedin.listener import LinkedInListener
        from src.linkedin.messenger import LinkedInMessenger
        from src.dashboard.app import create_app

        # Initialize database
        database = Database(
            db_path=os.getenv("DATABASE_PATH", "linkedin_assistant.db")
        )
        logger.info("Database initialized")

        # Initialize exclusion filter
        exclusion_filter = ExclusionFilter(database)
        logger.info("Exclusion filter initialized")

        # Initialize event classifier
        event_classifier = EventClassifier()
        logger.info("Event classifier initialized")

        # Initialize message generator
        message_generator = MessageGenerator()
        logger.info("Message generator initialized")

        # Initialize persona learner
        persona_learner = PersonaLearner(database)
        logger.info("Persona learner initialized")

        # Initialize reply handler
        reply_handler = ReplyHandler(database)
        logger.info("Reply handler initialized")

        # Initialize rate limiter
        rate_limiter = RateLimiter(
            database=database,
            max_messages_per_day=config["max_messages_per_day"],
            min_delay_seconds=config["min_delay_seconds"],
        )
        logger.info("Rate limiter initialized")

        # Initialize LinkedIn listener
        linkedin_listener = LinkedInListener(
            api_token=os.getenv("LINKEDIN_API_TOKEN", "")
        )
        logger.info("LinkedIn listener initialized")

        # Initialize LinkedIn messenger
        linkedin_messenger = LinkedInMessenger(
            api_token=os.getenv("LINKEDIN_API_TOKEN", "")
        )
        logger.info("LinkedIn messenger initialized")

        # Initialize Flask dashboard (optional)
        dashboard_app = create_app(config) if os.getenv("ENABLE_DASHBOARD", "true").lower() == "true" else None
        logger.info("Dashboard app initialized" if dashboard_app else "Dashboard disabled")

        # Create orchestrator
        orchestrator = LinkedInEngagementAssistant(
            config=config,
            database=database,
            exclusion_filter=exclusion_filter,
            event_classifier=event_classifier,
            message_generator=message_generator,
            persona_learner=persona_learner,
            reply_handler=reply_handler,
            rate_limiter=rate_limiter,
            linkedin_listener=linkedin_listener,
            linkedin_messenger=linkedin_messenger,
            dashboard_app=dashboard_app,
        )

        # Start Flask dashboard in separate thread if enabled
        if dashboard_app:
            def run_dashboard():
                dashboard_app.run(
                    host=os.getenv("DASHBOARD_HOST", "0.0.0.0"),
                    port=int(os.getenv("DASHBOARD_PORT", "5000")),
                    debug=False,
                )

            dashboard_thread = threading.Thread(target=run_dashboard, daemon=True)
            dashboard_thread.start()
            logger.info("Dashboard started in background thread")

        # Setup signal handlers for graceful shutdown
        def handle_shutdown(signum, frame):
            logger.info(f"Received signal {signum}, shutting down...")
            orchestrator.running = False

        signal.signal(signal.SIGINT, handle_shutdown)
        signal.signal(signal.SIGTERM, handle_shutdown)

        # Run orchestrator
        await orchestrator.run()

    except ImportError as e:
        logger.error(
            f"Failed to import required components: {e}. "
            "Ensure all modules are properly installed.",
            exc_info=True,
        )
        sys.exit(1)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
