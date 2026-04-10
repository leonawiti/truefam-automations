"""
LinkedIn Engagement Assistant - Message Generator

Generates personalized LinkedIn messages using Claude Sonnet via Anthropic API
for different event types and reply scenarios.
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


class ReplyType(str, Enum):
    """Reply message classification types."""
    SIMPLE_ACK = "SIMPLE_ACK"
    SUBSTANTIVE = "SUBSTANTIVE"
    CAP_MKE_POSITIVE = "CAP_MKE_POSITIVE"
    CAP_MKE_QUESTION = "CAP_MKE_QUESTION"
    CAP_MKE_ALREADY_AWARE = "CAP_MKE_ALREADY_AWARE"
    CAP_MKE_NOT_INTERESTED = "CAP_MKE_NOT_INTERESTED"


@dataclass
class GenerationContext:
    """Context information for message generation."""
    recipient_name: str
    company_name: Optional[str] = None
    job_title: Optional[str] = None
    milestone_years: Optional[int] = None
    event_type: Optional[EventType] = None
    reply_type: Optional[ReplyType] = None
    original_message: Optional[str] = None
    excluded_recipients: Optional[set[str]] = None


@dataclass
class GeneratedMessage:
    """Result of message generation."""
    content: str
    status: str  # "SUCCESS", "BLOCKED", "FLAG_FOR_REVIEW"
    event_type: Optional[str] = None
    reply_type: Optional[str] = None


class MessageGenerator:
    """Generates personalized LinkedIn messages using Claude AI."""

    def __init__(
        self,
        settings_path: str = "config/settings.json",
        faq_path: str = "config/cap_mke_faq.json",
    ):
        """
        Initialize the message generator with settings and FAQ.

        Args:
            settings_path: Path to the settings.json configuration file.
            faq_path: Path to the CAP MKE FAQ configuration file.
        """
        self.settings = self._load_settings(settings_path)
        self.faq = self._load_faq(faq_path)
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
                "milestone_temperature": 0.7,
                "reply_temperature": 0.5,
                "milestone_max_tokens": 120,
                "reply_max_tokens": 120,
                "cap_mke_question_max_tokens": 180,
            }

    def _load_faq(self, faq_path: str) -> dict:
        """Load CAP MKE FAQ from configuration file."""
        try:
            with open(faq_path, "r") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {"faqs": [], "default_response": ""}

    def generate_milestone_message(
        self,
        event_type: EventType,
        context: GenerationContext,
        persona_profile: Optional[str] = None,
    ) -> GeneratedMessage:
        """
        Generate a message for a milestone event (birthday, anniversary, etc).

        Args:
            event_type: Type of milestone event.
            context: Context information about recipient and event.
            persona_profile: Optional persona profile to inject Leon's style.

        Returns:
            GeneratedMessage with generated content or status.
        """
        # Check exclusion list
        if context.excluded_recipients and context.recipient_name in context.excluded_recipients:
            return GeneratedMessage(
                content="BLOCKED",
                status="BLOCKED",
                event_type=event_type.value,
            )

        # Build prompt based on event type
        if event_type == EventType.BIRTHDAY:
            prompt = self._build_birthday_prompt(context, persona_profile)
        elif event_type == EventType.WORK_ANNIVERSARY:
            prompt = self._build_work_anniversary_prompt(context, persona_profile)
        elif event_type == EventType.NEW_JOB:
            prompt = self._build_new_job_prompt(context, persona_profile)
        elif event_type == EventType.PROMOTION:
            prompt = self._build_promotion_prompt(context, persona_profile)
        else:
            return GeneratedMessage(
                content="",
                status="FLAG_FOR_REVIEW",
                event_type=event_type.value,
            )

        return self._generate_with_claude(
            prompt=prompt,
            temperature=self.settings.get("milestone_temperature", 0.7),
            max_tokens=self.settings.get("milestone_max_tokens", 120),
            event_type=event_type.value,
        )

    def _build_birthday_prompt(
        self, context: GenerationContext, persona_profile: Optional[str] = None
    ) -> str:
        """Build prompt for birthday message."""
        persona_str = (
            f"\nAdopt this persona/style: {persona_profile}" if persona_profile else ""
        )

        return f"""Generate a warm, personal birthday message for LinkedIn.

Requirements:
- 1-2 sentences maximum
- Warm and personal, never generic
- No hashtags, no emojis, no markdown, no filler phrases
- Check if {context.recipient_name} is on the exclusion list before generating. If yes, return BLOCKED and nothing else.

Recipient: {context.recipient_name}
{f"Company: {context.company_name}" if context.company_name else ""}{persona_str}

Generate only the message text, nothing else."""

    def _build_work_anniversary_prompt(
        self, context: GenerationContext, persona_profile: Optional[str] = None
    ) -> str:
        """Build prompt for work anniversary message."""
        persona_str = (
            f"\nAdopt this persona/style: {persona_profile}" if persona_profile else ""
        )

        years_str = f" {context.milestone_years} years" if context.milestone_years else ""

        return f"""Generate a work anniversary message for LinkedIn.

Requirements:
- Maximum 2 sentences
- Naturally reference the{years_str} milestone
- No hashtags, no emojis, no markdown, no filler phrases
- Check if {context.recipient_name} is on the exclusion list before generating. If yes, return BLOCKED and nothing else.

Recipient: {context.recipient_name}
{f"Company: {context.company_name}" if context.company_name else ""}{persona_str}

Generate only the message text, nothing else."""

    def _build_new_job_prompt(
        self, context: GenerationContext, persona_profile: Optional[str] = None
    ) -> str:
        """Build prompt for new job message."""
        persona_str = (
            f"\nAdopt this persona/style: {persona_profile}" if persona_profile else ""
        )

        job_str = f" as {context.job_title}" if context.job_title else ""

        return f"""Generate an encouraging message for someone's new job on LinkedIn.

Requirements:
- 2-3 sentences
- Encouraging and forward-looking
- No hashtags, no emojis, no markdown, no filler phrases
- Check if {context.recipient_name} is on the exclusion list before generating. If yes, return BLOCKED and nothing else.

Recipient: {context.recipient_name}
{f"New role/company: {context.company_name}{job_str}" if context.company_name or context.job_title else ""}{persona_str}

Generate only the message text, nothing else."""

    def _build_promotion_prompt(
        self, context: GenerationContext, persona_profile: Optional[str] = None
    ) -> str:
        """Build prompt for promotion message."""
        persona_str = (
            f"\nAdopt this persona/style: {persona_profile}" if persona_profile else ""
        )

        job_str = f" to {context.job_title}" if context.job_title else ""

        return f"""Generate a celebratory message for someone's promotion on LinkedIn.

Requirements:
- 2-3 sentences
- Celebratory and genuine, brief and sincere
- No hashtags, no emojis, no markdown, no filler phrases
- Check if {context.recipient_name} is on the exclusion list before generating. If yes, return BLOCKED and nothing else.

Recipient: {context.recipient_name}
{f"Company: {context.company_name}" if context.company_name else ""}{f"New position{job_str}" if context.job_title else ""}{persona_str}

Generate only the message text, nothing else."""

    def generate_reply_message(
        self,
        reply_type: ReplyType,
        context: GenerationContext,
        persona_profile: Optional[str] = None,
    ) -> GeneratedMessage:
        """
        Generate a reply message based on classification type.

        Args:
            reply_type: Type of reply message.
            context: Context information including original message if available.
            persona_profile: Optional persona profile to inject Leon's style.

        Returns:
            GeneratedMessage with generated reply or status.
        """
        # Check exclusion list
        if context.excluded_recipients and context.recipient_name in context.excluded_recipients:
            return GeneratedMessage(
                content="BLOCKED",
                status="BLOCKED",
                reply_type=reply_type.value,
            )

        if reply_type == ReplyType.SIMPLE_ACK:
            prompt = self._build_simple_ack_prompt(context, persona_profile)
            temperature = self.settings.get("reply_temperature", 0.5)
            max_tokens = self.settings.get("reply_max_tokens", 120)
        elif reply_type == ReplyType.SUBSTANTIVE:
            prompt = self._build_substantive_reply_prompt(context, persona_profile)
            temperature = self.settings.get("reply_temperature", 0.5)
            max_tokens = self.settings.get("reply_max_tokens", 120)
        elif reply_type in [
            ReplyType.CAP_MKE_POSITIVE,
            ReplyType.CAP_MKE_ALREADY_AWARE,
            ReplyType.CAP_MKE_NOT_INTERESTED,
        ]:
            prompt = self._build_cap_mke_reply_prompt(reply_type, context, persona_profile)
            temperature = self.settings.get("reply_temperature", 0.5)
            max_tokens = self.settings.get("reply_max_tokens", 120)
        elif reply_type == ReplyType.CAP_MKE_QUESTION:
            prompt = self._build_cap_mke_question_prompt(context, persona_profile)
            temperature = self.settings.get("reply_temperature", 0.5)
            max_tokens = self.settings.get("cap_mke_question_max_tokens", 180)
        else:
            return GeneratedMessage(
                content="",
                status="FLAG_FOR_REVIEW",
                reply_type=reply_type.value,
            )

        return self._generate_with_claude(
            prompt=prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            reply_type=reply_type.value,
        )

    def _build_simple_ack_prompt(
        self, context: GenerationContext, persona_profile: Optional[str] = None
    ) -> str:
        """Build prompt for simple acknowledgment reply."""
        persona_str = (
            f"\nAdopt this persona/style: {persona_profile}" if persona_profile else ""
        )

        return f"""Generate a warm, brief acknowledgment reply for LinkedIn.

Requirements:
- Single sentence maximum
- Warm and genuine (e.g., "Of course!", "Absolutely!", "Anytime!")
- No hashtags, no emojis (unless persona specifies), no markdown, no filler phrases
- Check if {context.recipient_name} is on the exclusion list before generating. If yes, return BLOCKED and nothing else.

Recipient: {context.recipient_name}{f"Context: {context.original_message}" if context.original_message else ""}{persona_str}

Generate only the reply text, nothing else."""

    def _build_substantive_reply_prompt(
        self, context: GenerationContext, persona_profile: Optional[str] = None
    ) -> str:
        """Build prompt for substantive reply."""
        persona_str = (
            f"\nAdopt this persona/style: {persona_profile}" if persona_profile else ""
        )

        return f"""Generate a thoughtful, substantive reply for LinkedIn.

Requirements:
- 1-3 sentences
- Thoughtful and authentic
- No hashtags, no emojis (unless persona specifies), no markdown, no filler phrases
- Check if {context.recipient_name} is on the exclusion list before generating. If yes, return BLOCKED and nothing else.

Recipient: {context.recipient_name}{f"Original message: {context.original_message}" if context.original_message else ""}{persona_str}

Generate only the reply text, nothing else."""

    def _build_cap_mke_reply_prompt(
        self,
        reply_type: ReplyType,
        context: GenerationContext,
        persona_profile: Optional[str] = None,
    ) -> str:
        """Build prompt for CAP MKE classified reply."""
        persona_str = (
            f"\nAdopt this persona/style: {persona_profile}" if persona_profile else ""
        )

        if reply_type == ReplyType.CAP_MKE_POSITIVE:
            instruction = "Generate a positive, encouraging response to this CAP MKE message."
        elif reply_type == ReplyType.CAP_MKE_ALREADY_AWARE:
            instruction = "Generate a response acknowledging they're already aware of CAP MKE."
        elif reply_type == ReplyType.CAP_MKE_NOT_INTERESTED:
            instruction = "Generate a polite response indicating you're not interested."
        else:
            instruction = "Generate an appropriate response."

        return f"""{instruction}

Requirements:
- 1-3 sentences
- No hashtags, no emojis (unless persona specifies), no markdown, no filler phrases
- Check if {context.recipient_name} is on the exclusion list before generating. If yes, return BLOCKED and nothing else.

Recipient: {context.recipient_name}{f"Original message: {context.original_message}" if context.original_message else ""}{persona_str}

Generate only the reply text, nothing else."""

    def _build_cap_mke_question_prompt(
        self, context: GenerationContext, persona_profile: Optional[str] = None
    ) -> str:
        """Build prompt for CAP MKE question reply using FAQ."""
        persona_str = (
            f"\nAdopt this persona/style: {persona_profile}" if persona_profile else ""
        )

        faq_content = self._format_faq_for_prompt()

        return f"""Answer this CAP MKE question using the provided FAQ information.

FAQ Information:
{faq_content}

Requirements:
- 1-3 sentences, can be up to 180 tokens for detailed explanation
- Use FAQ to inform your answer
- No hashtags, no emojis (unless persona specifies), no markdown, no filler phrases
- Check if {context.recipient_name} is on the exclusion list before generating. If yes, return BLOCKED and nothing else.

Recipient: {context.recipient_name}
Question/Message: {context.original_message if context.original_message else "General CAP MKE inquiry"}{persona_str}

Generate only the reply text, nothing else."""

    def _format_faq_for_prompt(self) -> str:
        """Format FAQ data for inclusion in prompt."""
        if not self.faq or not self.faq.get("faqs"):
            return "No FAQ information available."

        faq_lines = []
        for item in self.faq.get("faqs", []):
            question = item.get("question", "")
            answer = item.get("answer", "")
            if question and answer:
                faq_lines.append(f"Q: {question}\nA: {answer}")

        return "\n\n".join(faq_lines) if faq_lines else "No FAQ information available."

    def _generate_with_claude(
        self,
        prompt: str,
        temperature: float,
        max_tokens: int,
        event_type: Optional[str] = None,
        reply_type: Optional[str] = None,
    ) -> GeneratedMessage:
        """
        Call Claude API to generate message.

        Args:
            prompt: The prompt to send to Claude.
            temperature: Temperature setting for generation.
            max_tokens: Maximum tokens for response.
            event_type: Optional event type for response metadata.
            reply_type: Optional reply type for response metadata.

        Returns:
            GeneratedMessage with generated content or status.
        """
        try:
            message = self.claude_client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=[{"role": "user", "content": prompt}],
            )

            content = message.content[0].text.strip()

            # Check for BLOCKED response
            if content.upper() == "BLOCKED":
                return GeneratedMessage(
                    content="BLOCKED",
                    status="BLOCKED",
                    event_type=event_type,
                    reply_type=reply_type,
                )

            # Check for content restrictions
            if self._violates_content_restrictions(content):
                return GeneratedMessage(
                    content=content,
                    status="FLAG_FOR_REVIEW",
                    event_type=event_type,
                    reply_type=reply_type,
                )

            return GeneratedMessage(
                content=content,
                status="SUCCESS",
                event_type=event_type,
                reply_type=reply_type,
            )

        except anthropic.APIError as e:
            return GeneratedMessage(
                content=f"API Error: {str(e)}",
                status="FLAG_FOR_REVIEW",
                event_type=event_type,
                reply_type=reply_type,
            )

    def _violates_content_restrictions(self, text: str) -> bool:
        """
        Check if generated content violates content restrictions.

        Returns True if content should be flagged for review.
        """
        # Check for markdown
        if re.search(r"[*_`#\[\]]", text):
            return True

        # Check for excessive hashtags (more than 1)
        if text.count("#") > 1:
            return True

        return False
