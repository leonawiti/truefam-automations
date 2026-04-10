"""
LinkedIn Engagement Assistant - Event Classifier

Classifies LinkedIn notifications and reply messages into specific event types
and reply categories using pattern matching and Claude AI.
"""

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import anthropic


class EventType(str, Enum):
    """LinkedIn notification event types."""
    BIRTHDAY = "BIRTHDAY"
    WORK_ANNIVERSARY = "WORK_ANNIVERSARY"
    NEW_JOB = "NEW_JOB"
    PROMOTION = "PROMOTION"
    UNKNOWN = "UNKNOWN"


class ReplyType(str, Enum):
    """Reply message classification types."""
    SIMPLE_ACK = "SIMPLE_ACK"
    SUBSTANTIVE = "SUBSTANTIVE"
    CAP_MKE_POSITIVE = "CAP_MKE_POSITIVE"
    CAP_MKE_QUESTION = "CAP_MKE_QUESTION"
    CAP_MKE_ALREADY_AWARE = "CAP_MKE_ALREADY_AWARE"
    CAP_MKE_NOT_INTERESTED = "CAP_MKE_NOT_INTERESTED"
    OTHER = "OTHER"


@dataclass
class ClassifiedEvent:
    """Structured representation of a classified LinkedIn notification."""
    event_type: EventType
    recipient_name: Optional[str] = None
    company_name: Optional[str] = None
    job_title: Optional[str] = None
    milestone_years: Optional[int] = None
    raw_text: Optional[str] = None
    confidence: float = 0.0


@dataclass
class ClassifiedReply:
    """Structured representation of a classified reply message."""
    reply_type: ReplyType
    raw_text: Optional[str] = None
    confidence: float = 0.0
    context: Optional[str] = None


class EventClassifier:
    """Classifies LinkedIn notifications and reply messages."""

    def __init__(self, settings_path: str = "config/settings.json"):
        """
        Initialize the classifier with settings.

        Args:
            settings_path: Path to the settings.json configuration file.
        """
        self.settings = self._load_settings(settings_path)
        self.claude_client = anthropic.Anthropic()
        self.model = self.settings.get("claude_model", "claude-sonnet-4-20250514")

    def _load_settings(self, settings_path: str) -> dict:
        """Load settings from configuration file."""
        try:
            with open(settings_path, "r") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {
                "claude_model": "claude-sonnet-4-20250514",
                "classifier_temperature": 0.3,
                "classifier_max_tokens": 100,
            }

    def classify_notification(self, notification_text: str) -> ClassifiedEvent:
        """
        Classify a LinkedIn notification into an event type.

        Uses pattern matching first for high-confidence classification,
        falls back to Claude AI for ambiguous cases.

        Args:
            notification_text: Raw notification text from LinkedIn.

        Returns:
            ClassifiedEvent with detected event type and metadata.
        """
        # Try pattern-based classification first
        event_type, confidence = self._pattern_match_event(notification_text)

        if event_type != EventType.UNKNOWN and confidence >= 0.8:
            # High confidence pattern match - extract metadata
            metadata = self._extract_event_metadata(notification_text, event_type)
            return ClassifiedEvent(
                event_type=event_type,
                raw_text=notification_text,
                confidence=confidence,
                **metadata,
            )

        # Fall back to Claude for low-confidence or ambiguous cases
        return self._classify_with_claude(notification_text)

    def _pattern_match_event(self, text: str) -> tuple[EventType, float]:
        """
        Use regex patterns to classify notification into event type.

        Returns:
            Tuple of (EventType, confidence_score)
        """
        text_lower = text.lower()

        # Birthday patterns
        if re.search(
            r"\b(birthday|born|celebrates? (?:their )?birthday|happy birthday)\b",
            text_lower,
        ):
            return EventType.BIRTHDAY, 0.95

        # Work anniversary patterns
        if re.search(
            r"\b(work anniversary|anniversary with|celebrates? \d+ (?:years?|year anniversary))\b",
            text_lower,
        ):
            return EventType.WORK_ANNIVERSARY, 0.95

        # New job patterns
        if re.search(
            r"\b(started (a )?(?:new )?(?:job|role|position)|joined|now (?:at|works? at)|is now (?:at|working at))\b",
            text_lower,
        ):
            return EventType.NEW_JOB, 0.90

        # Promotion patterns
        if re.search(
            r"\b(promoted|(?:became|is now|accepted) (?:a |their )?(?:new |next )?(?:role|position)|promoted to)\b",
            text_lower,
        ):
            return EventType.PROMOTION, 0.90

        return EventType.UNKNOWN, 0.0

    def _extract_event_metadata(
        self, text: str, event_type: EventType
    ) -> dict[str, Optional[str]]:
        """Extract metadata from notification text."""
        metadata = {
            "recipient_name": None,
            "company_name": None,
            "job_title": None,
            "milestone_years": None,
        }

        # Extract name (usually at start or after "is" or "celebrates")
        name_match = re.search(
            r"^(\w+\s+\w+)\b|(?:is|celebrates?)\s+(\w+\s+\w+)\b", text
        )
        if name_match:
            metadata["recipient_name"] = (
                name_match.group(1) or name_match.group(2)
            ).strip()

        # Extract company name (usually after "at" or "with")
        company_match = re.search(
            r"(?:at|with|company|firm|organization)\s+([^,\n]+?)(?:\s+as|\s+on|,|\n|$)",
            text,
            re.IGNORECASE,
        )
        if company_match:
            metadata["company_name"] = company_match.group(1).strip()

        # Extract job title
        job_match = re.search(
            r"(?:as|role|position|titled?)\s+([^,\n]+?)(?:,|\n|$)", text, re.IGNORECASE
        )
        if job_match:
            metadata["job_title"] = job_match.group(1).strip()

        # Extract milestone years
        years_match = re.search(r"(\d+)\s+(?:years?|year\s+anniversary)", text)
        if years_match:
            metadata["milestone_years"] = int(years_match.group(1))

        return metadata

    def _classify_with_claude(self, notification_text: str) -> ClassifiedEvent:
        """
        Use Claude AI to classify notification when pattern matching is uncertain.

        Args:
            notification_text: Raw notification text.

        Returns:
            ClassifiedEvent with Claude-determined classification.
        """
        prompt = f"""Classify this LinkedIn notification into exactly one category: BIRTHDAY, WORK_ANNIVERSARY, NEW_JOB, PROMOTION, or UNKNOWN.

Extract metadata if available: recipient name, company name, job title, milestone years.

Notification:
{notification_text}

Respond in JSON format:
{{
    "event_type": "BIRTHDAY|WORK_ANNIVERSARY|NEW_JOB|PROMOTION|UNKNOWN",
    "recipient_name": "string or null",
    "company_name": "string or null",
    "job_title": "string or null",
    "milestone_years": "integer or null",
    "confidence": 0.0-1.0
}}"""

        message = self.claude_client.messages.create(
            model=self.model,
            max_tokens=self.settings.get("classifier_max_tokens", 100),
            temperature=self.settings.get("classifier_temperature", 0.3),
            messages=[{"role": "user", "content": prompt}],
        )

        try:
            response_text = message.content[0].text
            # Extract JSON from response
            json_match = re.search(r"\{.*\}", response_text, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                return ClassifiedEvent(
                    event_type=EventType[data["event_type"]],
                    recipient_name=data.get("recipient_name"),
                    company_name=data.get("company_name"),
                    job_title=data.get("job_title"),
                    milestone_years=data.get("milestone_years"),
                    raw_text=notification_text,
                    confidence=data.get("confidence", 0.5),
                )
        except (json.JSONDecodeError, KeyError, ValueError):
            pass

        return ClassifiedEvent(
            event_type=EventType.UNKNOWN,
            raw_text=notification_text,
            confidence=0.0,
        )

    def classify_reply(
        self, reply_text: str, context: Optional[str] = None
    ) -> ClassifiedReply:
        """
        Classify a reply message into a reply type.

        Uses pattern matching for simple cases, Claude AI for complex replies.

        Args:
            reply_text: The reply message text.
            context: Optional context about the original message being replied to.

        Returns:
            ClassifiedReply with determined reply type.
        """
        # Try pattern-based classification first
        reply_type, confidence = self._pattern_match_reply(reply_text)

        if reply_type != ReplyType.OTHER and confidence >= 0.8:
            return ClassifiedReply(
                reply_type=reply_type,
                raw_text=reply_text,
                confidence=confidence,
                context=context,
            )

        # Fall back to Claude for complex cases
        return self._classify_reply_with_claude(reply_text, context)

    def _pattern_match_reply(self, text: str) -> tuple[ReplyType, float]:
        """
        Use regex patterns to classify reply into type.

        Returns:
            Tuple of (ReplyType, confidence_score)
        """
        text_lower = text.lower().strip()
        text_clean = re.sub(r"\s+", " ", text_lower)

        # Simple acknowledgments
        simple_acks = [
            r"^(absolutely|definitely|of course|sure|thanks|thank you|yes|yep|yeah|great|awesome|love it)!?$",
            r"^(anytime|my pleasure|happy to|glad to).*$",
            r"^(congrats|congratulations|awesome|amazing|great|wonderful)!?$",
        ]

        for pattern in simple_acks:
            if re.search(pattern, text_clean):
                return ReplyType.SIMPLE_ACK, 0.95

        # CAP MKE patterns
        cap_mke_positive = [
            r"sounds? great|really excited|love (this|that|it)|awesome use",
            r"impressive|well done|impressive work",
        ]
        for pattern in cap_mke_positive:
            if re.search(pattern, text_lower):
                return ReplyType.CAP_MKE_POSITIVE, 0.85

        cap_mke_question = [r"how|what|when|where|why|can (you|we)|would (you|we)"]
        for pattern in cap_mke_question:
            if re.search(pattern, text_lower):
                return ReplyType.CAP_MKE_QUESTION, 0.70

        cap_mke_already_aware = [r"already|know (about|of)|aware|familiar with"]
        for pattern in cap_mke_already_aware:
            if re.search(pattern, text_lower):
                return ReplyType.CAP_MKE_ALREADY_AWARE, 0.75

        cap_mke_not_interested = [
            r"not interested|not for (me|us)|not really|doesn't seem|pass|skip",
        ]
        for pattern in cap_mke_not_interested:
            if re.search(pattern, text_lower):
                return ReplyType.CAP_MKE_NOT_INTERESTED, 0.80

        return ReplyType.OTHER, 0.0

    def _classify_reply_with_claude(
        self, reply_text: str, context: Optional[str] = None
    ) -> ClassifiedReply:
        """
        Use Claude AI to classify reply when pattern matching is uncertain.

        Args:
            reply_text: The reply message text.
            context: Optional context about original message.

        Returns:
            ClassifiedReply with Claude-determined classification.
        """
        context_str = f"Context: {context}\n" if context else ""

        prompt = f"""Classify this reply message into exactly one category:
- SIMPLE_ACK: brief acknowledgment (yes, thanks, anytime, etc)
- SUBSTANTIVE: thoughtful response with substance
- CAP_MKE_POSITIVE: positive response to CAP MKE initiative
- CAP_MKE_QUESTION: question about CAP MKE
- CAP_MKE_ALREADY_AWARE: already knows about CAP MKE
- CAP_MKE_NOT_INTERESTED: not interested in CAP MKE
- OTHER: doesn't fit categories above

{context_str}Reply:
{reply_text}

Respond in JSON format:
{{
    "reply_type": "SIMPLE_ACK|SUBSTANTIVE|CAP_MKE_POSITIVE|CAP_MKE_QUESTION|CAP_MKE_ALREADY_AWARE|CAP_MKE_NOT_INTERESTED|OTHER",
    "confidence": 0.0-1.0
}}"""

        message = self.claude_client.messages.create(
            model=self.model,
            max_tokens=self.settings.get("classifier_max_tokens", 100),
            temperature=self.settings.get("classifier_temperature", 0.3),
            messages=[{"role": "user", "content": prompt}],
        )

        try:
            response_text = message.content[0].text
            json_match = re.search(r"\{.*\}", response_text, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                return ClassifiedReply(
                    reply_type=ReplyType[data["reply_type"]],
                    raw_text=reply_text,
                    confidence=data.get("confidence", 0.5),
                    context=context,
                )
        except (json.JSONDecodeError, KeyError, ValueError):
            pass

        return ClassifiedReply(
            reply_type=ReplyType.OTHER,
            raw_text=reply_text,
            confidence=0.0,
            context=context,
        )
