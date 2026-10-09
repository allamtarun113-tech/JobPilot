"""Compile through the real sandbox (Tectonic). Skipped where Tectonic is missing."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from jobpilot.models.resume_doc import Bullet, Entry, Layout, Section
from jobpilot.resume import parse_check
from jobpilot.resume.render import SANDBOX_PROFILE, render_pdf
from jobpilot.resume.tex_import import import_tex
from tests.conftest import SAMPLE_RESUME

pytestmark = pytest.mark.skipif(
    shutil.which("tectonic" if sys.platform == "darwin" else "docker") is None,
    reason="sandbox compiler not installed",
)


async def test_sample_renders_one_page_and_passes_parse_check() -> None:
    """The sample resume renders to one page with Unicode intact."""
    doc = import_tex(SAMPLE_RESUME.read_text())
    pdf = await render_pdf(doc)
    report = parse_check.check(doc, pdf)
    assert report.ok, report
    text, pages = parse_check.extract_text(pdf)
    assert pages == 1
    # Regression: Unicode punctuation must survive (XeTeX + T1 fonts silently dropped it once).
    assert "2019–2023" in text or "2019-2023" in text
    assert "R&D" in text and "40%" in text and "Café Finder" in text


async def test_parse_check_flags_missing_sections_and_page_limit() -> None:
    """A missing section and a second page both fail the parse check."""
    doc = import_tex(SAMPLE_RESUME.read_text())
    pdf = await render_pdf(doc)
    doc.sections.append(Section(id="s99", title="Publications", layout=Layout.BULLETS))
    assert parse_check.check(doc, pdf).missing == ["Publications"]

    long_doc = import_tex(SAMPLE_RESUME.read_text())
    long_doc.sections[5].entries.append(
        Entry(id="e99", bullets=[Bullet(id=f"x{i}", text="A long filler line " * 6) for i in range(60)])
    )
    report = parse_check.check(long_doc, await render_pdf(long_doc))
    assert report.pages > 1 and not report.ok


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS sandbox profile")
def test_sandbox_profile_blocks_network_and_outside_writes(tmp_path: Path) -> None:
    """The macOS sandbox blocks network, writes and reads outside its folder."""
    outside = Path.home() / ".jobpilot-sandbox-probe"
    base = [
        "/usr/bin/sandbox-exec",
        "-f",
        str(SANDBOX_PROFILE),
        "-D",
        f"WORKDIR={tmp_path}",
        "-D",
        f"CACHE={tmp_path / 'cache'}",
        "-D",
        f"HOME={Path.home().resolve()}",
    ]
    net = subprocess.run(
        [*base, "/usr/bin/curl", "-sS", "-m", "5", "https://example.com"], capture_output=True
    )
    assert net.returncode != 0
    write = subprocess.run([*base, "/usr/bin/touch", str(outside)], capture_output=True)
    assert write.returncode != 0 and not outside.exists()
    inside = subprocess.run([*base, "/usr/bin/touch", str(tmp_path / "ok")], capture_output=True)
    assert inside.returncode == 0
    secret = Path.home() / ".jobpilot-sandbox-read-probe"
    secret.write_text("private")
    try:
        read = subprocess.run([*base, "/bin/cat", str(secret)], capture_output=True)
        assert read.returncode != 0 and b"private" not in read.stdout  # home folder is unreadable
    finally:
        secret.unlink()


async def test_a_composed_resume_builds_and_passes_the_verifier() -> None:
    """A composed resume (a reworded bullet, a new project) builds in the sandbox and shows its changes."""
    from jobpilot.models.resume_doc import Layout
    from jobpilot.models.resume_plan import ResumePlan
    from jobpilot.resume import compose, pool, verifier
    from jobpilot.resume.render import render_tex

    doc = import_tex(SAMPLE_RESUME.read_text())
    section = next(s for s in doc.sections if s.layout in (Layout.PROJECTS, Layout.ROLES) and s.entries)
    entry = next(e for e in section.entries if e.bullets)
    rows = [
        {"id": 900, "kind": "project", "status": "confirmed", "text": "Resume Helper", "import_key": None,
         "attrs": {"name": "Resume Helper", "about": "A tool that formats resumes."}},
        {"id": 901, "kind": "achievement", "status": "confirmed", "text": "Built a formatter for resumes.",
         "import_key": None, "attrs": {"parent_id": 900}},
    ]  # fmt: skip
    candidates = pool.build(doc, rows)
    first = candidates.entry(entry.id).bullets[0]  # type: ignore[union-attr]
    plan = ResumePlan.model_validate({
        "summary": "", "skills": [], "projects": [{"id": "p900", "bullets": []}],
        "experience": [{"id": entry.id, "bullets": [{"id": first.id, "text": first.text.rstrip(".") + "."}]}]
        if section.layout == Layout.ROLES else [],
    })  # fmt: skip
    out = compose.compose(doc, candidates, plan)
    tex = render_tex(out.doc)
    pdf, report = await verifier.verify(tex, out.applied, name=doc.header.name, max_pages=5)
    assert pdf is not None and report.ok, report
    assert "resumehelper" in (await verifier.read_back(pdf)).lower().replace(" ", "")


async def test_page_one_preview_is_a_png_image() -> None:
    """Page 1 renders to a real PNG image."""
    from jobpilot.resume import preview

    png = await preview.render(await render_pdf(import_tex(SAMPLE_RESUME.read_text())))
    assert png.startswith(b"\x89PNG") and len(png) > 20_000
