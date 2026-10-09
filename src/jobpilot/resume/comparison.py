"""Resume presentation comparison; does not change the confirmed-profile job-fit score."""

from typing import Any

from jobpilot.models.requirements import Requirements
from jobpilot.models.resume_doc import ResumeDoc
from jobpilot.resume import coverage, tex_versions


def compare(before: ResumeDoc, after: ResumeDoc, requirements: Requirements) -> dict[str, Any]:
    """Compare a tailored resume with the base for the job's keywords.

    Args:
        before: The base resume.
        after: The tailored resume.
        requirements: The job's extracted requirements.

    Returns:
        Coverage before and after, the change in adjusted coverage, and
        every element whose text changed.
    """

    def elements(doc: ResumeDoc) -> dict[str, tuple[str, str]]:
        """Map each element id to (where it sits, its text)."""
        return {
            bullet.id if bullet else entry.id: (
                f"{section.title} / {entry.title or 'Summary'}",
                bullet.text if bullet else " ".join([entry.text or "", *entry.items]).strip(),
            )
            for section, entry, bullet in doc.elements()
        }

    old, new = elements(before), elements(after)
    changes = []
    for key in dict.fromkeys([*old, *new]):
        previous = old.get(key, ("", ""))
        current = new.get(key, ("", ""))
        if previous[1] != current[1]:
            changes.append({"section": current[0] or previous[0], "before": previous[1], "after": current[1]})
    baseline, tailored = coverage.report(before, requirements), coverage.report(after, requirements)
    return {
        "before": baseline,
        "after": tailored,
        "delta": round(tailored["adjusted_percent"] - baseline["adjusted_percent"], 1),
        "changes": changes,
    }


def compare_tex(before: ResumeDoc, version: dict[str, Any], requirements: Requirements) -> dict[str, Any]:
    """The same comparison for a resume v2/v3 version: its LaTeX against the base resume, with each
    accepted change's before / after text."""
    baseline = coverage.report(before, requirements)
    tailored = coverage.report_text(tex_versions.plain(str(version["tex"])), requirements)
    changes = [
        {"section": op.get("section", ""), "before": op.get("before", ""), "after": op.get("after", "")}
        for op in version.get("operations") or []
        if op.get("before") or op.get("after")
    ]
    return {
        "before": baseline,
        "after": tailored,
        "delta": round(tailored["adjusted_percent"] - baseline["adjusted_percent"], 1),
        "changes": changes,
    }
