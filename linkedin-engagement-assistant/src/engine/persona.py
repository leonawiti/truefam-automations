"""
Persona Learning Module for LinkedIn Engagement Assistant

Analyzes Leon's past LinkedIn messages to extract persona characteristics and
builds a persona profile for use in message generation.
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional

from anthropic import Anthropic


logger = logging.getLogger(__name__)


@dataclass
class PersonaProfile:
    """
    Profile of Leon's communication persona extracted from past messages.

    Attributes:
        tone: Communication tone (casual/formal/mixed)
        common_phrases: List of recurring phrases Leon uses
        sign_offs: Common ways Leon signs off messages
        avg_length: Average message length in words
        uses_emojis: Whether Leon uses emojis
        emoji_examples: Examples of emojis Leon uses (if any)
        directness: Level of directness (direct/diplomatic/mixed)
        style_summary: Summary of overall communication style
        last_refreshed: Timestamp of last persona refresh
    """

    tone: str
    common_phrases: list[str] = field(default_factory=list)
    sign_offs: list[str] = field(default_factory=list)
    avg_length: float = 0.0
    uses_emojis: bool = False
    emoji_examples: list[str] = field(default_factory=list)
    directness: str = "direct"
    style_summary: str = ""
    last_refreshed: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert profile to dictionary for serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "PersonaProfile":
        """Create profile from dictionary."""
        return cls(**data)

    @classmethod
    def default_profile(cls) -> "PersonaProfile":
        """
        Return the default persona profile from the PRD.

        Default: "Leon's style is direct, warm, and occasionally uses casual phrasing.
        No hashtags, no emojis unless he uses them naturally."
        """
        return cls(
            tone="warm",
            common_phrases=[],
            sign_offs=[],
            avg_length=0.0,
            uses_emojis=False,
            emoji_examples=[],
            directness="direct",
            style_summary="Direct, warm, and occasionally casual phrasing. No hashtags, no emojis unless naturally used.",
            last_refreshed=None,
        )


class PersonaLearner:
    """
    Learns and manages Leon's communication persona from past LinkedIn messages.

    Uses Claude to analyze message patterns and extract tone, vocabulary, phrasing,
    and other stylistic characteristics. Provides persona profiles for injection
    into message generation prompts.
    """

    def __init__(self, database, anthropic_client: Anthropic):
        """
        Initialize the PersonaLearner.

        Args:
            database: Database instance for storing/retrieving persona profiles
            anthropic_client: Anthropic client for Claude API calls
        """
        self.database = database
        self.client = anthropic_client
        self.current_persona = self._load_persona_from_db()

    def _load_persona_from_db(self) -> PersonaProfile:
        """
        Load persona profile from database.

        Returns default profile if none exists in database.

        Returns:
            PersonaProfile from database or default profile
        """
        try:
            persona_data = self.database.get_persona_profile()
            if persona_data:
                return PersonaProfile.from_dict(persona_data)
        except Exception as e:
            logger.warning(f"Could not load persona from database: {e}")

        return PersonaProfile.default_profile()

    def analyze_messages(self, messages: list[str]) -> PersonaProfile:
        """
        Analyze Leon's past LinkedIn messages to extract persona characteristics.

        Uses Claude to identify tone, vocabulary patterns, common phrases, sign-offs,
        message length patterns, emoji usage, and directness level.

        Args:
            messages: List of message texts from Leon's past LinkedIn activity

        Returns:
            PersonaProfile with extracted characteristics
        """
        if not messages:
            logger.warning("No messages provided for persona analysis")
            return PersonaProfile.default_profile()

        # Prepare messages for analysis
        messages_text = "\n---\n".join(messages[:50])  # Limit to 50 most recent messages

        analysis_prompt = f"""Analyze the following LinkedIn messages from Leon to extract his communication persona.

MESSAGES:
{messages_text}

Provide a detailed analysis of:
1. Tone (casual/formal/mixed) - provide the primary tone
2. Common phrases and expressions he uses (list 5-10 key ones)
3. Common sign-offs (how he typically ends messages)
4. Average message length (estimate based on the messages)
5. Emoji usage (yes/no and which specific emojis if used)
6. Directness level (direct/diplomatic/mixed)
7. Overall style summary (1-2 sentences capturing his voice)

Respond in JSON format with these exact keys:
{{
    "tone": "...",
    "common_phrases": [...],
    "sign_offs": [...],
    "avg_length": 0,
    "uses_emojis": false,
    "emoji_examples": [...],
    "directness": "...",
    "style_summary": "..."
}}"""

        try:
            response = self.client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=1024,
                messages=[{"role": "user", "content": analysis_prompt}],
            )

            response_text = response.content[0].text

            # Extract JSON from response
            json_start = response_text.find("{")
            json_end = response_text.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                analysis_json = json.loads(response_text[json_start:json_end])
            else:
                logger.error("Could not extract JSON from Claude response")
                return PersonaProfile.default_profile()

            # Create PersonaProfile from analysis
            profile = PersonaProfile(
                tone=analysis_json.get("tone", "mixed"),
                common_phrases=analysis_json.get("common_phrases", []),
                sign_offs=analysis_json.get("sign_offs", []),
                avg_length=float(analysis_json.get("avg_length", 0.0)),
                uses_emojis=analysis_json.get("uses_emojis", False),
                emoji_examples=analysis_json.get("emoji_examples", []),
                directness=analysis_json.get("directness", "direct"),
                style_summary=analysis_json.get("style_summary", ""),
                last_refreshed=datetime.utcnow().isoformat(),
            )

            logger.info("Persona analysis completed successfully")
            return profile

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Claude response as JSON: {e}")
            return PersonaProfile.default_profile()
        except Exception as e:
            logger.error(f"Error analyzing messages with Claude: {e}")
            return PersonaProfile.default_profile()

    def build_persona_prompt(self, profile: PersonaProfile) -> str:
        """
        Convert a persona profile into a system prompt fragment.

        This fragment can be injected into message generation prompts to guide
        the model to generate responses matching Leon's communication style.

        Args:
            profile: PersonaProfile to convert

        Returns:
            String fragment suitable for use in system prompts
        """
        emoji_note = ""
        if profile.uses_emojis and profile.emoji_examples:
            emoji_list = ", ".join(profile.emoji_examples)
            emoji_note = f"\n- Uses emojis occasionally: {emoji_list}"
        elif not profile.uses_emojis:
            emoji_note = "\n- No hashtags, no emojis unless naturally used"

        common_phrases_str = ""
        if profile.common_phrases:
            phrases_list = ", ".join(profile.common_phrases[:5])
            common_phrases_str = f"\n- Common phrases: {phrases_list}"

        sign_off_str = ""
        if profile.sign_offs:
            sign_offs_list = ", ".join(profile.sign_offs[:3])
            sign_off_str = f"\n- Typical sign-offs: {sign_offs_list}"

        prompt_fragment = f"""You are helping Leon craft LinkedIn messages. Match his communication style:

TONE & VOICE:
- Tone: {profile.tone}
- Directness: {profile.directness}
- Overall style: {profile.style_summary}{common_phrases_str}{sign_off_str}{emoji_note}

MESSAGE LENGTH:
- Aim for approximately {int(profile.avg_length)} words (but adjust based on context)

GUIDELINES:
- Be authentic to Leon's voice
- Avoid overly formal or robotic phrasing
- Match his directness and warmth level
- Use his natural phrases and sign-offs when appropriate"""

        return prompt_fragment

    def refresh_persona(self, new_messages: list[str]) -> PersonaProfile:
        """
        Update the existing persona profile with new message data.

        Analyzes new messages and refreshes the stored persona profile in the database.

        Args:
            new_messages: List of new message texts to analyze

        Returns:
            Updated PersonaProfile
        """
        # Analyze the new messages
        updated_profile = self.analyze_messages(new_messages)

        # Save to database
        try:
            self.database.save_persona_profile(updated_profile.to_dict())
            self.current_persona = updated_profile
            logger.info("Persona profile refreshed and saved")
        except Exception as e:
            logger.error(f"Failed to save persona profile: {e}")

        return updated_profile

    def get_current_persona(self) -> PersonaProfile:
        """
        Get the current persona profile.

        Returns the profile from the last refresh, or loads from database.
        Returns default profile if none exists.

        Returns:
            Current PersonaProfile
        """
        if self.current_persona is None:
            self.current_persona = self._load_persona_from_db()

        return self.current_persona

    def get_persona_json(self) -> dict:
        """
        Get the current persona as a JSON-serializable dictionary.

        Returns:
            Dictionary representation of current persona
        """
        return self.get_current_persona().to_dict()
