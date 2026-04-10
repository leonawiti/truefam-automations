"""
HARD exclusion filter for LinkedIn Engagement Assistant.

This is the most critical safety component. All names are checked against
the exclusion list BEFORE any message generation, classification, or processing.
"""

import json
from pathlib import Path
from typing import List, Set, Tuple
from difflib import SequenceMatcher
import logging


logger = logging.getLogger(__name__)


class ExclusionFilter:
    """
    Hard exclusion filter that prevents message generation for excluded individuals.

    Uses exact matching and fuzzy matching with Levenshtein distance <= 1
    to catch typos and name variations.
    """

    # Default exclusion list (used if config file not found)
    DEFAULT_EXCLUDED_NAMES = [
        "Linda Chaba",
        "Christine Chaba",
        "Michael Chaba",
        "Mary Odede",
    ]

    def __init__(self, config_path: str = "config/exclusion_list.json", db=None):
        """
        Initialize exclusion filter.

        Args:
            config_path: Path to exclusion list JSON file
            db: Optional database connection for audit logging
        """
        self.config_path = config_path
        self.db = db
        self._excluded_set: Set[Tuple[str, str]] = set()
        self._load_exclusion_list()

    def _load_exclusion_list(self) -> None:
        """
        Load exclusion list from JSON config file.

        Falls back to DEFAULT_EXCLUDED_NAMES if file not found.
        """
        try:
            config_file = Path(self.config_path)
            if config_file.exists():
                with open(config_file, "r") as f:
                    data = json.load(f)
                    names = data.get("excluded_names", self.DEFAULT_EXCLUDED_NAMES)
            else:
                logger.warning(
                    f"Exclusion list config not found at {self.config_path}, "
                    "using defaults"
                )
                names = self.DEFAULT_EXCLUDED_NAMES
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Failed to load exclusion list: {e}, using defaults")
            names = self.DEFAULT_EXCLUDED_NAMES

        # Parse names into (first_name, last_name) tuples for fuzzy matching
        for name in names:
            parts = name.strip().split()
            if len(parts) >= 2:
                first_name = parts[0].lower()
                last_name = " ".join(parts[1:]).lower()
                self._excluded_set.add((first_name, last_name))
            elif len(parts) == 1:
                # Single name entry
                self._excluded_set.add((parts[0].lower(), ""))

        logger.info(f"Loaded {len(self._excluded_set)} excluded names")

    @staticmethod
    def _levenshtein_distance(s1: str, s2: str) -> int:
        """
        Calculate Levenshtein distance between two strings.

        Args:
            s1: First string
            s2: Second string

        Returns:
            int: Edit distance
        """
        if len(s1) < len(s2):
            return ExclusionFilter._levenshtein_distance(s2, s1)

        if len(s2) == 0:
            return len(s1)

        previous_row = range(len(s2) + 1)
        for i, c1 in enumerate(s1):
            current_row = [i + 1]
            for j, c2 in enumerate(s2):
                insertions = previous_row[j + 1] + 1
                deletions = current_row[j] + 1
                substitutions = previous_row[j] + (c1 != c2)
                current_row.append(min(insertions, deletions, substitutions))
            previous_row = current_row

        return previous_row[-1]

    def _fuzzy_match(self, input_str: str, target_str: str) -> bool:
        """
        Check if input string fuzzy-matches target with Levenshtein distance <= 1.

        Args:
            input_str: User input string
            target_str: Target string from exclusion list

        Returns:
            bool: True if fuzzy match found
        """
        distance = self._levenshtein_distance(
            input_str.lower().strip(),
            target_str.lower().strip()
        )
        return distance <= 1

    def is_excluded(self, name: str) -> bool:
        """
        Check if a name is on the exclusion list.

        This is the PRIMARY SAFETY CHECK - called before ANY message generation.
        Uses both exact matching and fuzzy matching.

        Args:
            name: Full name to check

        Returns:
            bool: True if name is excluded, False otherwise
        """
        if not name or not name.strip():
            return False

        name_parts = name.strip().split()
        if len(name_parts) < 2:
            # Single name - won't match multi-part names
            self._log_exclusion_check(name, False, "insufficient_parts")
            return False

        input_first = name_parts[0].lower()
        input_last = " ".join(name_parts[1:]).lower()

        # Check each excluded name for matches
        for excluded_first, excluded_last in self._excluded_set:
            # Exact match on first name and last name
            if input_first == excluded_first and input_last == excluded_last:
                self._log_exclusion_check(name, True, "exact_match")
                return True

            # Fuzzy match on first name
            if self._fuzzy_match(input_first, excluded_first):
                # Also check last name (exact or fuzzy)
                if (input_last == excluded_last or
                    self._fuzzy_match(input_last, excluded_last)):
                    self._log_exclusion_check(name, True, "fuzzy_match")
                    return True

            # Full name fuzzy match (for single word last names)
            if self._fuzzy_match(name, f"{excluded_first} {excluded_last}"):
                self._log_exclusion_check(name, True, "full_name_fuzzy")
                return True

        self._log_exclusion_check(name, False, "not_excluded")
        return False

    def get_excluded_names(self) -> List[str]:
        """
        Get list of excluded names.

        Returns:
            List of full names in exclusion list
        """
        names = []
        for first, last in sorted(self._excluded_set):
            if last:
                names.append(f"{first.title()} {last.title()}")
            else:
                names.append(first.title())

        return names

    def _log_exclusion_check(
        self,
        name: str,
        is_excluded: bool,
        reason: str,
    ) -> None:
        """
        Log an exclusion check result to audit log.

        Args:
            name: Name that was checked
            is_excluded: Whether the name was excluded
            reason: Reason for the decision
        """
        if self.db:
            action = "EXCLUSION_HIT" if is_excluded else "EXCLUSION_PASS"
            details = json.dumps({
                "name": name,
                "reason": reason,
            })
            try:
                self.db.log_audit(
                    action=action,
                    target_name=name,
                    details=details
                )
            except Exception as e:
                logger.error(f"Failed to log exclusion check: {e}")

    def reload_exclusion_list(self) -> None:
        """
        Reload the exclusion list from config file.

        Useful for hot-reloading without restarting the application.
        """
        self._excluded_set.clear()
        self._load_exclusion_list()
        logger.info("Exclusion list reloaded")
