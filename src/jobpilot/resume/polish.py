"""Polish resume text with code (decision 39): spelling, capitals, end punctuation, dates and titles.

No model and no network: an offline English dictionary (pyspellchecker) fixes misspelled words
("adn" -> "and", "succefully" -> "successfully", "cliend" -> "client"). A word is left alone when it is a
skill or tool (the taxonomy), a word from the profile's own names and titles, in `resume.known_words`, an
acronym or mixed-case name (RLHF, LangGraph, FENICSX), a capitalised name in mid-sentence (Turing, Euler),
or has digits or symbols. A fix must be within two letters, so a meaning can't change. Code also capitalises
the first letter, ends sentence bullets with a full stop, writes month names and "Present" in title case and
drops a stray full stop after an entry title.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache

from jobpilot.core.config import REPO_ROOT, get_config
from jobpilot.models.resume_doc import Layout, ResumeDoc
from jobpilot.reference.catalog import display_skill

TAXONOMY = REPO_ROOT / "Data" / "taxonomy" / "skills.json"
# Proper nouns often typed in lower case.
PROPER = {
    "india": "India", "indian": "Indian", "python": "Python", "java": "Java", "javascript": "JavaScript",
    "english": "English", "linux": "Linux", "github": "GitHub", "telegram": "Telegram",
}  # fmt: skip
MONTHS = frozenset(
    """january february march april may june july august september october november december jan feb mar apr
    jun jul aug sep sept oct nov dec present current""".split()
)
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9+#'/_-]*(?:\.[A-Za-z0-9]+)*")  # Node.js stays whole; "python." doesn't
_MIN_SENTENCE_WORDS = 6  # shorter bullets are headings ("Joint sports secretary"): no full stop added


@lru_cache
def _checker():  # type: ignore[no-untyped-def]
    """The offline dictionary (loaded once)."""
    from spellchecker import SpellChecker

    return SpellChecker()


@lru_cache
def taxonomy_words() -> frozenset[str]:
    """Every word of every skill name and alias: they are never 'corrected'."""
    data = json.loads(TAXONOMY.read_text())
    words: set[str] = set()
    for skill in data["skills"]:
        for name in (skill["id"], skill["name"], *skill.get("aliases", [])):
            words.update(w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9+#]*", name))
    return frozenset(words)


def known_words(extra: set[str] | frozenset[str] = frozenset()) -> frozenset[str]:
    """The words the spell check never changes: taxonomy, config `resume.known_words` and `extra`."""
    configured = {w.lower() for w in get_config().resume.known_words}
    return frozenset(taxonomy_words() | configured | {w.lower() for w in extra})


def _edit_distance(a: str, b: str) -> int:
    """Levenshtein distance (small words only)."""
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def fix_word(word: str, *, first: bool, known: frozenset[str]) -> str:
    """The word spelled right, or unchanged when it's a name, tool, acronym or already right."""
    lower = word.lower()
    if lower in PROPER:
        return PROPER[lower]
    plain = word.isalpha() and len(word) >= 3
    mixed = any(c.isupper() for c in word[1:])
    name = word[0].isupper() and not first  # a capitalised word mid-sentence is a name
    if not plain or mixed or name or lower in known:
        return word
    checker = _checker()
    if lower in checker:
        return word
    fixed = checker.correction(lower)
    if not fixed or fixed == lower or _edit_distance(fixed, lower) > 2:
        return word
    return fixed.capitalize() if word[0].isupper() else fixed


def sentence(text: str, known: frozenset[str], *, period: bool = True) -> str:
    """Spelling, single spaces, a capital first letter and (for a sentence) a full stop."""
    text = " ".join(re.sub(r"(\*\*|__|`)", "", text).split())
    if not text:
        return text
    out, last, first = [], 0, True
    for m in _WORD.finditer(text):
        out.append(text[last : m.start()])
        token = m.group(0)
        parts = re.split(r"([/-])", token)  # Python-Flask, FV/DG: check each part
        out.append("".join(p if p in "/-" else fix_word(p, first=first, known=known) for p in parts))
        last, first = m.end(), False
    out.append(text[last:])
    text = "".join(out)
    text = text[0].upper() + text[1:]
    if period and len(text.split()) >= _MIN_SENTENCE_WORDS and (text[-1].isalnum() or text.endswith(")")):
        text += "."
    return text


_SMALL = frozenset({"and", "or", "of", "for", "the", "a", "an", "in", "on", "to", "with"})


def label(text: str | None) -> str | None:
    """A skills label in title case ('Operating systems' -> 'Operating Systems'); acronyms are kept."""
    if not text:
        return text
    words = text.split()
    return " ".join(
        w
        if (w.isupper() or any(c.isupper() for c in w[1:]))
        else (w.lower() if i and w.lower() in _SMALL else w.capitalize())
        for i, w in enumerate(words)
    )


def date(text: str | None) -> str | None:
    """'june 2024--present' -> 'June 2024--Present'; 'MARCH 2020' -> 'March 2020'."""
    if not text:
        return text
    return re.sub(
        r"[A-Za-z]+", lambda m: m.group(0).capitalize() if m.group(0).lower() in MONTHS else m.group(0), text
    )


def title(text: str | None) -> str | None:
    """An entry title without a stray full stop at the end ('Turing - Delivery Data Scientist.')."""
    return text.rstrip(" .") if text else text


def polish(doc: ResumeDoc, known: frozenset[str]) -> list[str]:
    """Polish every line of the resume in place; return the spelling fixes made ('adn -> and')."""
    fixes: list[str] = []

    def track(old: str | None, new: str | None) -> str | None:
        if old and new and old != new:
            for a, b in zip(re.findall(r"[A-Za-z]+", old), re.findall(r"[A-Za-z]+", new), strict=False):
                if a.lower() != b.lower():
                    fixes.append(f"{a} -> {b}")
        return new

    for section in doc.sections:
        for e in section.entries:
            if section.layout == Layout.SKILLS:
                e.title = label(e.title)
                e.items = list(dict.fromkeys(display_skill(i) for i in e.items))
                continue
            e.title, e.date = title(e.title), date(e.date)
            if section.layout == Layout.DATED_PAIRS:
                continue  # education: only its dates are tidied
            if e.text:
                e.text = track(e.text, sentence(e.text, known))
            if e.note:
                e.note = track(e.note, sentence(e.note, known))
            period = section.layout != Layout.BULLETS  # activities read as a list: no full stops added
            for b in e.bullets:
                b.text = track(b.text, sentence(b.text, known, period=period)) or b.text
                if b.sub:
                    b.sub = track(b.sub, sentence(b.sub, known, period=period))
    return list(dict.fromkeys(fixes))
