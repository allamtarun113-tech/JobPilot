"""Per-job LaTeX copies of the base resume (decision 31, D2).

The base resume's .tex (the one fixed template filled with the base
resume) is copied for each job; only that copy is ever edited. This
module knows the copy's layout: the preamble and the header (name,
contact details) are locked, every resumesection command starts a
section, and Education is locked too. It also turns LaTeX into the
plain text a reader (or an applicant tracking system) sees, for the
claim checks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from jobpilot.models.resume_doc import ResumeDoc
from jobpilot.resume.render import render_tex

SECTION = re.compile(r"\\resumesection\{([^{}]*)\}")
END = "\\end{document}"
LOCKED_SECTIONS = ("education",)


@dataclass(frozen=True)
class Section:
    """Where one resume section sits in the copy's LaTeX."""

    title: str
    start: int  # index of "\resumesection"
    end: int  # index where the next section (or \end{document}) starts

    @property
    def locked(self) -> bool:
        """True for sections the editor may not change (Education)."""
        return self.title.strip().lower() in LOCKED_SECTIONS


def base_tex(doc: ResumeDoc) -> str:
    """The base resume as LaTeX - the starting copy for a job. The original is never edited."""
    return render_tex(doc)


def sections(tex: str) -> list[Section]:
    """Return every section of the copy, with its start and end offsets."""
    found = list(SECTION.finditer(tex))
    end_of_body = tex.rfind(END)
    out = []
    for i, m in enumerate(found):
        stop = (
            found[i + 1].start() if i + 1 < len(found) else (end_of_body if end_of_body != -1 else len(tex))
        )
        out.append(Section(m.group(1), m.start(), stop))
    return out


def section_titles(tex: str) -> list[str]:
    """Return the section titles in document order."""
    return [s.title for s in sections(tex)]


def section_at(tex: str, title: str) -> Section | None:
    """Return the section with this title (case/space-insensitive), or None."""
    wanted = " ".join(title.lower().split())
    return next((s for s in sections(tex) if " ".join(s.title.lower().split()) == wanted), None)


def editable_text(tex: str) -> dict[str, str]:
    """Section title -> its LaTeX, for every section the editor may change."""
    return {s.title: tex[s.start : s.end].rstrip() for s in sections(tex) if not s.locked}


_SYMBOLS = {
    r"\textendash{}": "–",
    r"\textemdash{}": "—",
    r"\textbullet{}": "•",
    r"\textbullet": "•",
    r"\ldots{}": "...",
    r"\&": "&",
    r"\%": "%",
    r"\$": "$",
    r"\#": "#",
    r"\_": "_",
    r"\{": "{",
    r"\}": "}",
    r"\textasciitilde{}": "~",
    r"\textbackslash{}": "\\",
    r"\enspace": " ",
    r"\\": "\n",
    "``": '"',
    "''": '"',
    "~": " ",
}
_DROP_WITH_ARGS = re.compile(r"\\(?:vspace|hspace|fontsize|fontfamily)\*?(?:\{[^{}]*\})+")
_HREF = re.compile(r"\\href\{[^{}]*\}\{((?:[^{}]|\{[^{}]*\})*)\}")
_SKILL = re.compile(r"\\skill\{([^{}]*)\}\{([^{}]*)\}")
_DATED = re.compile(r"\\datedrow\{((?:[^{}]|\{[^{}]*\})*)\}\{((?:[^{}]|\{[^{}]*\})*)\}")
_COMMAND = re.compile(r"\\[a-zA-Z]+\*?")


def plain(tex: str) -> str:
    """The words a reader sees in this LaTeX (commands and layout removed)."""
    text = SECTION.sub(r"\n\1\n", tex)  # a heading is its own line
    text = _DROP_WITH_ARGS.sub(" ", text)
    text = _HREF.sub(r"\1", text)
    text = _SKILL.sub(r"\1: \2", text)
    text = _DATED.sub(r"\1 \2", text)
    text = text.replace(r"\item", "\n•")
    # Symbols after the commands above, so their arguments are kept.
    for latex, symbol in _SYMBOLS.items():
        text = text.replace(latex, symbol)
    text = re.sub(r"\\(?:begin|end)\{[^{}]*\}", " ", text)
    text = _COMMAND.sub(" ", text)
    text = text.replace("{", " ").replace("}", " ")
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)
