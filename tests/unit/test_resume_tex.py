"""Importer, renderer (template) and escaping - no compiler needed."""

import pytest

from jobpilot.models.resume_doc import Layout
from jobpilot.resume.render import UnsupportedCharacter, latex_escape, latex_url, render_tex
from jobpilot.resume.tex_import import import_tex
from tests.conftest import SAMPLE_RESUME


def sample():
    """Return the sample resume imported from its .tex."""
    return import_tex(SAMPLE_RESUME.read_text())


def test_import_reads_every_layout() -> None:
    """The importer reads the header and every section layout."""
    doc = sample()
    assert doc.header.name == "ASHA VERMA"
    assert [c.text for c in doc.header.contacts] == [
        "+91 90000 00000",
        "asha@example.com",
        "github.com/asha-example",
    ]
    assert doc.header.contacts[2].underline and doc.header.contacts[1].url == "mailto:asha@example.com"
    assert [s.layout for s in doc.sections] == [
        Layout.PARAGRAPH,
        Layout.DATED_PAIRS,
        Layout.SKILLS,
        Layout.PROJECTS,
        Layout.ROLES,
        Layout.BULLETS,
    ]
    edu = doc.sections[1].entries[0]
    assert (edu.title, edu.date, edu.subtitle, edu.detail) == (
        "B.E. Computer Science",
        "2019–2023",
        "Example Institute of Technology, Pune",
        "8.1 CGPA",
    )
    projects = doc.sections[3].entries
    assert projects[0].note == "Built with two friends for a college hackathon."
    assert projects[1].title == "Café Finder" and projects[1].note is None
    assert doc.sections[5].entries[0].bullets[0].sub == "Fixed 5 bugs in a popular Python library"
    assert "R&D" in doc.sections[0].entries[0].text and "40%" in doc.sections[0].entries[0].text


def test_ids_are_unique() -> None:
    """Every section, entry and bullet id is unique."""
    doc = sample()
    ids = (
        [s.id for s in doc.sections]
        + [e.id for s in doc.sections for e in s.entries]
        + [b.id for s in doc.sections for e in s.entries for b in e.bullets]
    )
    assert len(ids) == len(set(ids))


def test_render_then_import_round_trips() -> None:
    """Rendering then importing gives back the same resume."""
    doc = sample()
    again = import_tex(render_tex(doc))
    assert again.model_dump() == doc.model_dump()


def test_hostile_text_round_trips_exactly() -> None:
    """LaTeX-like and Unicode text survives a round trip unchanged."""
    doc = sample()
    hostile = r"\input{/etc/passwd} 100% & #1 a_b ~x^ {braces} – “quotes” Café"
    doc.sections[4].entries[0].bullets[0].text = hostile
    assert import_tex(render_tex(doc)).sections[4].entries[0].bullets[0].text == hostile


def test_hidden_elements_are_not_rendered() -> None:
    """Hidden bullets and sections are left out."""
    doc = sample()
    doc.sections[4].entries[0].bullets[1].hidden = True
    doc.sections[5].hidden = True
    tex = render_tex(doc)
    assert "Migrated batch jobs" not in tex and "Activities" not in tex


def test_escaping_neutralizes_latex_in_data() -> None:
    """Commands in resume text are escaped, never run."""
    doc = sample()
    doc.sections[4].entries[0].bullets[0].text = r"\input{/etc/passwd} \write18{rm -rf /} $x$ 100% #1 a_b ~ ^"
    tex = render_tex(doc)
    assert r"\textbackslash{}input\{/etc/passwd\}" in tex
    assert r"\input{/etc/passwd}" not in tex and r"\write18{" not in tex
    assert r"100\% \#1 a\_b \textasciitilde{} \textasciicircum{}" in tex


def test_unicode_is_mapped_or_refused() -> None:
    """Known Unicode maps to LaTeX; unknown characters are refused."""
    assert latex_escape("2019–2023") == r"2019\textendash{}2023"
    assert latex_escape("Café") == r"Caf\'{e}"
    assert latex_escape("“x”") == "``x''"
    with pytest.raises(UnsupportedCharacter):
        latex_escape("中")


def test_urls_are_restricted() -> None:
    """Only safe http(s) URLs are allowed in links."""
    assert latex_url("https://x.dev/a#b") == r"https://x.dev/a\#b"
    for bad in ("javascript:alert(1)", "file:///etc/passwd", "https://x.dev/a}b", "https://x .dev"):
        with pytest.raises(ValueError):
            latex_url(bad)
