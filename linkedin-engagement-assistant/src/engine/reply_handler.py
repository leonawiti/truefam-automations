"""
Reply Handler for LinkedIn Engagement Assistant

Handles processing of LinkedIn replies with classification, response generation,
and routing (auto-send, queue, or manual review). Supports both general engagement
and CAP MKE outreach-specific handling.
"""

import json
import logging
from dataclasses import dataclass
from typing import Optional

from src.engine.classifier import EventClassifier, ClassifiedReply, ReplyType
from src.engine.generator import MessageGenerator, GenerationContext
from src.engine.exclusion import ExclusionFilter
from src.utils.database import Database


logger = logging.getLogger(__name__)


@dataclass
class ReplyResult:
    """Result of reply processing."""
    response_text: Optional[str]
    status: str  # auto_send, queued, flagged, blocked
    classification: ReplyType
    sender_name: str
    sender_linkedin_id: str


class ReplyHandler:
    """
    Processes LinkedIn replies, classifies them, and generates contextual responses.

    Handles both general LinkedIn engagement and CAP MKE outreach-specific flows,
    including FAQ lookup, exclusion filtering, and manual review routing.
    """

    def __init__(
        self,
        generator: MessageGenerator,
        classifier: EventClassifier,
        exclusion_filter: ExclusionFilter,
        database: Database,
        cap_mke_faq_path: str = "config/cap_mke_faq.json",
    ):
        """
        Initialize the ReplyHandler.

        Args:
            generator: MessageGenerator instance for creating responses
            classifier: EventClassifier instance for classifying replies
            exclusion_filter: ExclusionFilter instance for checking exclusions
            database: Database instance for storing reply threads
            cap_mke_faq_path: Path to CAP MKE FAQ JSON configuration
        """
        self.generator = generator
        self.classifier = classifier
        self.exclusion_filter = exclusion_filter
        self.database = database
        self.cap_mke_faq = self._load_cap_mke_faq(cap_mke_faq_path)

    def _load_cap_mke_faq(self, faq_path: str) -> list:
        """
        Load CAP MKE FAQ from JSON configuration file.

        Args:
            faq_path: Path to the FAQ JSON file

        Returns:
            List of FAQ entries with category, keywords, and approved_answer fields
        """
        try:
            with open(faq_path, "r") as f:
                faq_data = json.load(f)
                # Extract faq_entries array from the loaded JSON
                faq_entries = faq_data.get("faq_entries", [])
                logger.info(f"Loaded CAP MKE FAQ with {len(faq_entries)} entries")
                return faq_entries
        except FileNotFoundError:
            logger.warning(f"CAP MKE FAQ file not found at {faq_path}")
            return []
        except json.JSONDecodeError:
            logger.error(f"Invalid JSON in CAP MKE FAQ file: {faq_path}")
            return []

    def process_reply(
        self,
        sender_name: str,
        sender_linkedin_id: str,
        reply_text: str,
        message_history: Optional[list] = None,
        is_cap_mke_thread: bool = False,
    ) -> ReplyResult:
        """
        Process a LinkedIn reply with classification and response generation.

        Flow:
        1. Check exclusion list (log and block if excluded)
        2. Classify the reply
        3. Route based on classification type
        4. Generate response or flag for review
        5. Save to database
        6. Return result

        Args:
            sender_name: Name of the person sending the reply
            sender_linkedin_id: LinkedIn ID of the sender
            reply_text: The text of the reply
            message_history: Optional list of prior messages in thread
            is_cap_mke_thread: Whether this is a CAP MKE outreach thread

        Returns:
            ReplyResult with status, response text, and classification
        """
        # Step 1: Check exclusion list
        if self.exclusion_filter.is_excluded(sender_name):
            logger.info(f"EXCLUDED — {sender_name}")
            return ReplyResult(
                response_text=None,
                status="blocked",
                classification=ReplyType.OTHER,
                sender_name=sender_name,
                sender_linkedin_id=sender_linkedin_id,
            )

        # Auto-detect CAP MKE thread if not explicitly specified
        if not is_cap_mke_thread and message_history:
            is_cap_mke_thread = self.detect_cap_mke_thread(message_history)

        # Step 2: Classify the reply
        classified_reply = self.classifier.classify_reply(
            reply_text, context={"is_cap_mke_thread": is_cap_mke_thread}
        )

        # Step 3 & 4: Route based on classification and generate response
        response_text = None
        response_status = "queued"
        generated_message = None

        # Create generation context for all response types
        gen_context = GenerationContext(
            recipient_name=sender_name,
            company_name=None,
            job_title=None,
            milestone_years=None,
            event_type=None,
            reply_type=classified_reply.reply_type,
            original_message=reply_text,
            excluded_recipients=None,
        )

        if classified_reply.reply_type == ReplyType.SIMPLE_ACK:
            generated_message = self.generator.generate_reply_message(
                classified_reply.reply_type, gen_context
            )
            response_status = "auto_send"

        elif classified_reply.reply_type == ReplyType.SUBSTANTIVE:
            response_status = "flagged"
            logger.info(f"Flagged for manual review: {sender_name}")

        elif classified_reply.reply_type == ReplyType.CAP_MKE_POSITIVE:
            generated_message = self.generator.generate_reply_message(
                classified_reply.reply_type, gen_context
            )
            response_status = "auto_send"

        elif classified_reply.reply_type == ReplyType.CAP_MKE_QUESTION:
            faq_answer = self._lookup_faq(reply_text)
            if faq_answer == "FLAG_FOR_REVIEW":
                response_status = "flagged"
                logger.info(f"CAP MKE question flagged for review: {sender_name}")
            else:
                # Include FAQ answer in generation context
                gen_context.original_message = faq_answer
                generated_message = self.generator.generate_reply_message(
                    classified_reply.reply_type, gen_context
                )
                response_status = "auto_send"

        elif classified_reply.reply_type == ReplyType.CAP_MKE_ALREADY_AWARE:
            generated_message = self.generator.generate_reply_message(
                classified_reply.reply_type, gen_context
            )
            response_status = "auto_send"

        elif classified_reply.reply_type == ReplyType.CAP_MKE_NOT_INTERESTED:
            generated_message = self.generator.generate_reply_message(
                classified_reply.reply_type, gen_context
            )
            response_status = "auto_send"

        # Extract response text and handle blocked status
        if generated_message:
            response_text = generated_message.content
            if generated_message.status == "BLOCKED":
                response_status = "blocked"
            elif generated_message.status == "FLAG_FOR_REVIEW":
                response_status = "flagged"

        # Step 5: Save reply thread to database
        source_tag = "CAP_MKE_OUTREACH" if is_cap_mke_thread else "GENERAL"
        self.database.save_reply_thread(
            original_message_id=None,  # Not available in reply context
            recipient_name=sender_name,
            reply_text=reply_text,
            source_tag=source_tag,
            recipient_linkedin_id=sender_linkedin_id,
            reply_classification=classified_reply.reply_type.value,
        )

        # Log audit trail
        self.database.log_audit(
            action="REPLY_PROCESSED",
            target_name=sender_name,
            details={
                "classification": classified_reply.reply_type.value,
                "status": response_status,
                "is_cap_mke": is_cap_mke_thread,
            },
        )

        # Step 6: Return result
        return ReplyResult(
            response_text=response_text,
            status=response_status,
            classification=classified_reply.reply_type,
            sender_name=sender_name,
            sender_linkedin_id=sender_linkedin_id,
        )

    def detect_cap_mke_thread(self, message_history: list) -> bool:
        """
        Detect if a thread originated from a CAP MKE outreach message.

        Checks for CAP MKE template text or message tags indicating the thread
        was initiated as part of CAP MKE outreach campaign.

        Args:
            message_history: List of messages in the thread

        Returns:
            True if thread is CAP MKE outreach, False otherwise
        """
        if not message_history:
            return False

        cap_mke_indicators = [
            "cap mke",
            "CAP MKE",
            "cap_mke_outreach",
            "source:CAP_MKE",
        ]

        for message in message_history:
            message_text = message.get("text", "") if isinstance(message, dict) else str(message)
            if any(indicator in message_text for indicator in cap_mke_indicators):
                return True

        return False


    def _lookup_faq(self, question_text: str) -> str:
        """
        Look up a CAP MKE FAQ answer based on question text.

        Matches question keywords against FAQ entries and returns the corresponding
        approved answer. If no clear match or manual review needed, returns "FLAG_FOR_REVIEW".

        Args:
            question_text: The question to look up

        Returns:
            FAQ answer text or "FLAG_FOR_REVIEW" if no match found
        """
        if not self.cap_mke_faq:
            return "FLAG_FOR_REVIEW"

        question_lower = question_text.lower()

        for faq_entry in self.cap_mke_faq:
            # Each entry has: category, keywords (list), approved_answer
            keywords = faq_entry.get("keywords", [])
            if isinstance(keywords, list):
                # Check if any keyword appears in the question
                for keyword in keywords:
                    if keyword.lower() in question_lower:
                        answer = faq_entry.get("approved_answer")
                        return answer if answer else "FLAG_FOR_REVIEW"

        return "FLAG_FOR_REVIEW"
