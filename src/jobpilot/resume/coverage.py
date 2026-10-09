"""Keyword coverage with a repetition guard."""

import re
from typing import Any

from jobpilot.models.requirements import Requirements
from jobpilot.models.resume_doc import ResumeDoc


def report(doc: ResumeDoc, requirements: Requirements) -> dict[str, Any]:
    """Return the keyword coverage report for a structured resume.

    Args:
        doc: The resume.
        requirements: The job's requirements (must + nice skills).

    Returns:
        Coverage percent, the repetition penalty, covered, missing and
        stuffed skills.
    """
    text = " ".join(
        bullet.text if bullet else " ".join([entry.text or "", *entry.items])
        for _, entry, bullet in doc.elements()
    )
    return report_text(text, requirements)


def report_text(text: str, requirements: Requirements) -> dict[str, Any]:
    """The same report from plain resume text (a resume v2 LaTeX copy, or a PDF read back)."""
    text = text.lower()
    skills = list(dict.fromkeys([*requirements.must_skills, *requirements.nice_skills]))
    # Word edges that also work for C++, C#, .NET and Node.js (\b fails after '+' or '#').
    counts = {
        skill: len(re.findall(rf"(?<![\w+#.]){re.escape(skill.lower())}(?![\w+#])", text)) for skill in skills
    }
    covered = [skill for skill, count in counts.items() if count]
    # A skill named more than three times counts as keyword stuffing.
    stuffed = [skill for skill, count in counts.items() if count > 3]
    percent = round(100 * len(covered) / len(skills), 1) if skills else 100.0
    penalty = min(30, len(stuffed) * 5)
    return {
        "percent": percent,
        "repetition_penalty": penalty,
        "adjusted_percent": max(0.0, percent - penalty),
        "covered": covered,
        "missing": [skill for skill in skills if skill not in covered],
        "counts": counts,
        "stuffed": stuffed,
        "ok": not stuffed,
    }
