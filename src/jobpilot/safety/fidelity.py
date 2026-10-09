"""Faithful-rewrite checks (decision 36): a reworded line must say what its one source fact says, no more.

The skill and number checks (claims.py, citations.py) let through rewrites that change the meaning with
ordinary words: "Learned about BEML projects" became "Contributed to BEML vehicle design", "Worked in Gen AI
projects" became "Spearheaded Gen AI projects ... achieving impactful results". These checks close that gap
with code, not a model:

- vocabulary: every content word of the rewrite comes from the source fact (or its entry's own context),
  allowing word forms ("train" / "training"), typo fixes ("succefully" -> "successfully") and a few neutral
  connectors. So no new verbs, results, adjectives or claims;
- coverage: the rewrite keeps most of the source's content words, so detail isn't thrown away;
- tense: planned work stays planned ("will", "plan", "aim");
- length, repetition and formatting stay sane;
- numbers, skills and title words: the existing claim checks.

The writing that passes is a tidier sentence of the same fact: reordered, trimmed, typo-free.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from jobpilot.safety import claims
from jobpilot.safety.citations import supported_numbers

STOPWORDS = frozenset(
    """a an the and or nor but of for to in on at by with from as into onto via per using use used is are
    was were be been being this that these those it its our we us i my me their they them which who whom
    whose while also both each such than then so not no all any some other own same very can could would
    should may might must has have had do does did done across within through over under about after before
    during between including include includes included where when what how""".split()
)
# Words a rewrite may add although its source doesn't use them: connectors and plain "did" verbs that
# claim nothing beyond the fact itself.
NEUTRAL = frozenset(
    """developed develop building build built create created implemented implement worked work working
    making made covering covered featuring featured end focused focus""".split()
)
# Words that make planned work sound finished, or finished work sound planned.
FUTURE = frozenset(
    {"will", "plan", "plans", "planned", "planning", "aim", "aims", "aiming", "going", "upcoming"}
)
# Puffery: refused in a summary (and, being new words, in any rewrite) unless the profile says it.
HYPE = frozenset(
    """expert expertise proven extensive seasoned strong deep mastery master guru ninja rockstar world-class
    spearheaded spearhead pioneered pioneering orchestrated championed impactful significantly
    significant exceptional outstanding passionate results-driven dynamic visionary""".split()
)
# Impact claims a summary never makes: they belong in a bullet, backed by its fact, or nowhere.
IMPACT = (
    "improv",
    "enhanc",
    "increas",
    "reduc",
    "optimi",
    "boost",
    "achiev",
    "impact",
    "efficien",
    "accura",
    "scalab",
    "spearhead",
    "drove",
    "driving",
    "ensur",
    "success",
    "streamlin",
    "revolution",
)
# Job titles a summary may not give the candidate unless a confirmed role says it.
ROLE_NOUNS = frozenset(
    {"engineer", "developer", "specialist", "expert", "professional", "consultant", "architect", "analyst"}
)
_IRREGULAR = {"led": "lead", "built": "build", "wrote": "write", "made": "make", "ran": "run",
              "drove": "drive", "began": "begin", "taught": "teach", "won": "win", "met": "meet"}  # fmt: skip
_SUFFIXES = ("izations", "ization", "isation", "ations", "ation", "ments", "ment", "ings", "ing", "ers",
             "er", "ied", "ies", "ed", "es", "s", "ly")  # fmt: skip
# Plain "made something" verbs: one may stand in for another.
BUILDERS = frozenset({"develop", "build", "creat", "implement", "make"})
# Generic verbs a rewrite may replace with a more specific verb the fact itself uses.
GENERIC = frozenset({"work", "involv", "focus"})
# Involvement short of doing: it must survive a rewrite ("learned about" never becomes "designed").
WEAK = ("learn", "stud", "observ", "shadow", "assist", "attend", "understand", "gain", "expos", "familiar",
        "help", "support", "participat", "explor")  # fmt: skip
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9+#.'-]*[A-Za-z0-9+#]|[A-Za-z]")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def words(text: str) -> list[str]:
    """The words of a text, lower-cased (C++, Node.js and FV/DG parts stay whole)."""
    return [w.lower().strip(".'-") for w in _WORD.findall(text.replace("/", " "))]


def root(word: str) -> str:
    """A crude stem, enough to see 'training' and 'trained' as one word."""
    word = _IRREGULAR.get(word, word)
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            stem = word[: -len(suffix)]
            return stem + "y" if suffix in ("ied", "ies") else stem
    return word


def lead_verb(text: str) -> str | None:
    """The first past-tense verb of a line ('At BEML I designed ...' -> 'designed'), or None."""
    return next((w for w in words(text) if w in _IRREGULAR or (len(w) > 4 and w.endswith("ed"))), None)


def verb_problem(new: str, source: str) -> str | None:
    """Why the rewrite changes what the candidate did, or None."""
    old_verb, new_verb = lead_verb(source), lead_verb(new)
    source_roots = content_roots(source)
    if new_verb is not None and root(new_verb) != (root(old_verb) if old_verb else None):
        old, new_root = root(old_verb) if old_verb else None, root(new_verb)
        same_kind = old in BUILDERS and new_root in BUILDERS
        specific = (old is None or old in GENERIC) and _known(new_root, source_roots)
        if not (same_kind or specific):
            return f"changes what you did ('{old_verb or 'no verb'}' -> '{new_verb}')"
    new_roots = content_roots(new)
    ws = words(source)
    verbs = {
        root(w) for i, w in enumerate(ws) if i == 0 or w.endswith("ed")
    }  # "Reinforcement Learning" isn't
    for weak in WEAK:
        if any(r.startswith(weak) for r in verbs) and not any(r.startswith(weak) for r in new_roots):
            return f"drops '{weak}...' - it changes what you did"
    return None


def content_roots(text: str) -> set[str]:
    """The stems of the meaningful words (stopwords dropped)."""
    return {root(w) for w in words(text) if w not in STOPWORDS}


def _known(stem: str, allowed: set[str]) -> bool:
    """True if the stem is allowed, as is, as a longer/shorter form, or as a typo fix of an allowed word."""
    if stem in allowed:
        return True
    for other in allowed:
        short, long_ = sorted((stem, other), key=len)
        if len(short) >= 4 and long_.startswith(short):
            return True
        if len(stem) >= 5 and len(other) >= 5 and SequenceMatcher(None, stem, other).ratio() >= 0.8:
            return True
    return False


def clean(text: str) -> str:
    """A fact as it may appear on a resume: markdown marks removed, spacing tidied."""
    text = re.sub(r"(\*\*|__|`)", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def repeated_sentence(text: str) -> bool:
    """True if a sentence (or a run of 8+ words) appears twice."""
    sentences = [" ".join(words(s)) for s in _SENTENCE.split(text) if s.strip()]
    if len(sentences) != len(set(sentences)):
        return True
    ws = words(text)
    grams = [" ".join(ws[i : i + 8]) for i in range(max(0, len(ws) - 7))]
    return len(grams) != len(set(grams))


def job_terms(job_text: str) -> dict[str, str]:
    """Skill id -> the job's own name for it ('Generative AI', 'PostgreSQL'), from the job description."""
    return claims.skills_in(job_text or "")


def same_skill_words(backing: str, terms: dict[str, str]) -> set[str]:
    """Stems of the job's names for skills the fact already names: 'Gen AI' in the fact lets the job's
    'Generative AI' in, because the skills taxonomy says they are the same skill. Nothing new is claimed."""
    held = claims.skills_in(backing)
    return content_roots(" ".join(name for sid, name in terms.items() if sid in held))


def foreign_characters(new: str, source: str) -> list[str]:
    """Non-ASCII characters in `new` that `source` doesn't have (a stray '撇' from the model)."""
    return sorted({c for c in new if ord(c) > 127 and c not in source})


def rewrite_problem(
    new: str,
    source: str,
    context: str = "",
    *,
    min_keep: float = 0.6,
    terms: dict[str, str] | None = None,
) -> str | None:
    """Why `new` is not a faithful rewording of `source`, or None.

    Args:
        new: The reworded line.
        source: The one fact it rewords (the original line).
        context: Its entry's own words (title, note, tools) - they back words too.
        min_keep: The share of the source's content words the rewrite must keep.
        terms: The job's skill names (job_terms); the job's name for a skill the fact names is allowed.

    Returns:
        The reason to keep the original instead, or None when faithful.
    """
    new, source = new.strip(), clean(source)
    if not new:
        return "is empty"
    if re.search(r"[\\{}$*_`]|\]\(", new):
        return "contains formatting characters"
    backing = f"{source}\n{context}"
    if foreign := foreign_characters(new, backing):
        return f"has characters the original doesn't: {''.join(foreign[:5])}"
    if not supported_numbers(new, [backing]):
        return "adds a number your profile doesn't have"
    if problems := claims.unsupported(new, [backing]):
        return "adds " + ", ".join(problems)
    allowed = content_roots(backing) | {root(w) for w in NEUTRAL} | same_skill_words(backing, terms or {})
    added = sorted({w for w in words(new) if w not in STOPWORDS and not _known(root(w), allowed)})
    if added:
        return "adds words your profile doesn't say: " + ", ".join(added[:5])
    source_roots = content_roots(source)
    kept = {r for r in source_roots if _known(r, content_roots(new))}
    if source_roots and len(kept) / len(source_roots) < min_keep:
        return "drops too much of the original detail"
    if FUTURE & set(words(source)) and not FUTURE & set(words(new)):
        return "turns planned work into finished work"
    if problem := verb_problem(new, source):
        return problem
    if len(words(new)) > len(words(source)) + 6:
        return "is longer than the original"
    if repeated_sentence(new):
        return "repeats itself"
    return None


def summary_problem(text: str, facts: list[str], base_summary: str, role_titles: list[str]) -> str | None:
    """Why a written summary can't go on the resume, or None.

    A summary is new text, so it is checked against the whole confirmed profile: skills, numbers and tools
    must be in it; no title or job noun the candidate doesn't hold; no puffery; no repetition.

    Args:
        text: The proposed summary.
        facts: Every confirmed profile fact text.
        base_summary: The summary on the base resume.
        role_titles: The candidate's confirmed role titles.

    Returns:
        The reason to keep the base summary, or None.
    """
    text = text.strip()
    if not text:
        return "is empty"
    if re.search(r"[\\{}$*_`]", text):
        return "contains formatting characters"
    if foreign := foreign_characters(text, " ".join([*facts, base_summary])):
        return f"has characters your profile doesn't: {''.join(foreign[:5])}"
    n_words = len(words(text))
    if n_words < 15 or n_words > 70:
        return "must be 15-70 words"
    if not supported_numbers(text, [*facts, base_summary]):
        return "adds a number your profile doesn't have"
    if problems := claims.unsupported(text, [*facts, base_summary]):
        return "adds " + ", ".join(problems)
    if added_titles := claims.title_words_in(text) - claims.title_words_in(base_summary):
        return f"gives you a title ('{sorted(added_titles)[0]}')"
    held = {w for title in role_titles for w in words(title)}
    lowered = set(words(text))
    if nouns := sorted({w.rstrip("s") for w in lowered} & ROLE_NOUNS - held):
        return f"calls you a{'n' if nouns[0][0] in 'aeiou' else ''} {nouns[0]}"
    if hype := sorted(lowered & HYPE - set(words(" ".join(facts)))):
        return f"uses puffery ('{hype[0]}')"
    if impact := sorted(w for w in lowered if w.startswith(IMPACT)):
        return f"claims an impact ('{impact[0]}')"
    if "years" in lowered and "years" not in set(words(" ".join(facts))):
        return "claims years of experience"
    if repeated_sentence(text):
        return "repeats itself"
    return None
