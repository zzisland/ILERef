from __future__ import annotations

import re


# Canonical names used by the Decision Layer, prompt assets, and refactoring reports.
INTERNAL_SMELLS = {
    "Assertion_Roulette",
    "Conditional_Test_Logic",
    "Constructor_Initialization",
    "Default_Test",
    "EmptyTest",
    "Exception_Catching_Throwing",
    "General_Fixture",
    "Mystery_Guest",
    "Print_Statement",
    "Redundant_Assertion",
    "Sensitive_Equality",
    "Verbose_Test",
    "Sleepy_Test",
    "Eager_Test",
    "Lazy_Test",
    "Duplicate_Assert",
    "Unknown_Test",
    "IgnoredTest",
    "Resource_Optimism",
    "Magic_Number_Test",
    "Dependent_Test",
}

DETECTOR_TO_INTERNAL = {
    "Assertion Roulette": "Assertion_Roulette",
    "Conditional Test Logic": "Conditional_Test_Logic",
    "Constructor Initialization": "Constructor_Initialization",
    "Default Test": "Default_Test",
    "EmptyTest": "EmptyTest",
    "Empty Test": "EmptyTest",
    "Exception Catching Throwing": "Exception_Catching_Throwing",
    "General Fixture": "General_Fixture",
    "Mystery Guest": "Mystery_Guest",
    "Print Statement": "Print_Statement",
    "Redundant Assertion": "Redundant_Assertion",
    "Sensitive Equality": "Sensitive_Equality",
    "Verbose Test": "Verbose_Test",
    "Sleepy Test": "Sleepy_Test",
    "Eager Test": "Eager_Test",
    "Lazy Test": "Lazy_Test",
    "Duplicate Assert": "Duplicate_Assert",
    "Unknown Test": "Unknown_Test",
    "IgnoredTest": "IgnoredTest",
    "Ignored Test": "IgnoredTest",
    "Resource Optimism": "Resource_Optimism",
    "Magic Number Test": "Magic_Number_Test",
    "Dependent Test": "Dependent_Test",
}

# These values must match the existing Chroma metadata exactly.
INTERNAL_TO_KNOWLEDGE_BASE = {
    "Assertion_Roulette": "Assertion Roulette",
    "Exception_Catching_Throwing": "Exception Handling",
    "Sleepy_Test": "Sleepy Test",
    "Eager_Test": "Eager Test",
}


def to_internal_smell(smell: str) -> str:
    """Convert a detector/display/legacy smell name to the canonical internal name."""
    value = str(smell or "").strip()
    if not value:
        return value
    if value in INTERNAL_SMELLS:
        return value
    if value in DETECTOR_TO_INTERNAL:
        return DETECTOR_TO_INTERNAL[value]

    normalized_display = re.sub(r"\s+", " ", value.replace("_", " ")).strip()
    for detector_name, internal_name in DETECTOR_TO_INTERNAL.items():
        if detector_name.casefold() == normalized_display.casefold():
            return internal_name
    return value


def to_internal_smells(smells) -> list[str]:
    return [to_internal_smell(smell) for smell in (smells or [])]


def to_knowledge_base_smell(smell: str) -> str:
    """Convert an internal (or detector) name to existing knowledge-base metadata."""
    internal_name = to_internal_smell(smell)
    return INTERNAL_TO_KNOWLEDGE_BASE.get(
        internal_name,
        internal_name.replace("_", " "),
    )
