"""Untrusted text (JDs, emails, web pages, user notes) is cleaned and wrapped in a fenced data block.

Cleaning removes control / bidi characters, strips instruction-like phrases ("ignore previous instructions",
fake role headers, attempts to close our data fence) and caps the length. `suspicious()` lets callers flag
such text. Prompts place fenced blocks as data and tell the model never to follow instructions inside them.
"""

from __future__ import annotations

import re
import secrets

# C0 controls plus zero-width and bidi-override characters, which
# can hide or reorder text.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‏‪-‮⁦-⁩]")
# Phrases that try to steer the model: "ignore previous
# instructions", fake role headers, fence-closing tags, and "tap
# Approve" style social engineering.
_INJECTION = re.compile(
    r"(ignore|disregard|forget|override)\s+(all\s+|any\s+|the\s+|your\s+)?(previous|prior|above|earlier|"
    r"system)\s+(instructions?|rules|prompts?|messages?)"
    r"|you\s+are\s+now\s+(a|an|the)\b"
    r"|new\s+instructions?\s*:"
    r"|^\s*(system|assistant|developer)\s*:"
    r"|</?\s*data\s*:[\w-]*\s*>"
    r"|\b(begin|end)\s+(system|instructions?)\b"
    r"|\b(tap|press|click)\s+(confirm|approve|send)\b",
    re.I | re.M,
)
REMOVED = "[removed instruction-like text]"


def suspicious(text: str) -> bool:
    """Return True if the text contains instruction-like phrases."""
    return bool(_INJECTION.search(text or ""))


def clean(text: str, max_chars: int = 20_000) -> str:
    """Remove hidden characters and instruction-like phrases, then cap length.

    Args:
        text: Untrusted text.
        max_chars: The most characters to keep.

    Returns:
        The cleaned text.
    """
    text = _CONTROL.sub("", text or "").replace("\r\n", "\n")
    text = _INJECTION.sub(REMOVED, text)
    return text[:max_chars]


def clean_value(value: object) -> object:
    """clean() every string inside JSON-like data (so role headers are caught before JSON-escaping)."""
    if isinstance(value, str):
        return clean(value)
    if isinstance(value, list):
        return [clean_value(v) for v in value]
    if isinstance(value, dict):
        return {k: clean_value(v) for k, v in value.items()}
    return value


def fence(label: str, text: str) -> str:
    """Wrap untrusted text in a block whose delimiter cannot appear inside it."""
    # A random tag, so the text can't guess and fake the closing line.
    tag = f"{label}-{secrets.token_hex(4)}"
    return f"<data:{tag}>\n{clean(text)}\n</data:{tag}>"
