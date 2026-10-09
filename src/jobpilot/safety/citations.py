"""Claim checks shared by generated resume and outreach text."""

from __future__ import annotations

import re
from decimal import Decimal

# A number, then an optional unit: a suffix glued to it ("40%", "5k", "1.2M") or a separate word
# ("40 percent", "10 lakh", "1.2 million"). A separate letter is NOT a unit ("3 modules" is 3).
_NUMBER = re.compile(
    r"(?<![\w.])(\d+(?:\.\d+)?)"
    r"(?:(%|[kmb](?![a-z]))|\s*(percent|per cent|thousand|million|mn|billion|bn|lakhs?|lacs?|crores?|cr)\b)?",
    re.I,
)
_SCALE = {
    "k": 1_000,
    "thousand": 1_000,
    "m": 1_000_000,
    "million": 1_000_000,
    "mn": 1_000_000,
    "b": 1_000_000_000,
    "billion": 1_000_000_000,
    "bn": 1_000_000_000,
    "lakh": 100_000,
    "lakhs": 100_000,
    "lac": 100_000,
    "lacs": 100_000,
    "crore": 10_000_000,
    "crores": 10_000_000,
    "cr": 10_000_000,
}
_ONES = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}
_TENS = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}


def numbers(text: str) -> set[str]:
    """Return every number in ``text`` in one normal form.

    "40%", "forty percent" and "40 percent" all become "40%"; "1.2M"
    and "1,200,000" both become "1200000". Spelled-out numbers below
    100 are included, so a rewrite can't hide a new number in words.

    Args:
        text: Any text.

    Returns:
        The normalized numbers as strings.
    """
    out: set[str] = set()
    # Drop thousands separators first so "1,200" reads as one number.
    for digits, glued, word in _NUMBER.findall(text.replace(",", "")):
        unit = (glued or word).lower()
        value = Decimal(digits) * _SCALE.get(unit, 1)
        percent = unit in ("%", "percent", "per cent")
        out.add(f"{value.normalize():f}" + ("%" if percent else ""))
    # Spelled-out numbers: "forty two" -> 42, "forty percent" -> 40%.
    words = re.findall(r"[a-z]+", text.lower().replace("-", " "))
    for i, word in enumerate(words):
        if word in _ONES:
            out.add(str(_ONES[word]))
        if word in _TENS:
            word_value = _TENS[word] + (_ONES.get(words[i + 1], 0) if i + 1 < len(words) else 0)
            out.add(str(word_value) + ("%" if i + 1 < len(words) and words[i + 1] == "percent" else ""))
    return out


def supported_numbers(text: str, cited_facts: list[str]) -> bool:
    """Return True if every number in ``text`` appears in the cited facts."""
    return numbers(text).issubset(numbers(" ".join(cited_facts)))
