"""Fill the one fixed template with a ResumeDoc and compile it with Tectonic in a no-network sandbox.

Only templates/resume/resume.tex.j2 is ever compiled. Every value is LaTeX-escaped; URLs are limited to
http(s), mailto and tel. Tectonic runs with --untrusted --only-cached inside sandbox-exec (macOS), a
--network none container (Linux), or directly inside the cloud backend's own container.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import sys
import tempfile
import unicodedata
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from jobpilot.core.config import REPO_ROOT, get_config
from jobpilot.models.resume_doc import ResumeDoc

TEMPLATE_DIR = REPO_ROOT / "templates" / "resume"
SANDBOX_PROFILE = REPO_ROOT / "deploy" / "sandbox" / "tectonic.sb"
TEMPLATES = {"resume_v1": "resume.tex.j2"}

_LATEX_SPECIAL = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
# Tectonic is XeTeX-based and the template uses T1-encoded Type 1 fonts, so raw non-ASCII characters
# have no glyph and silently vanish. Map them to LaTeX commands; refuse anything else.
_UNICODE = {
    "–": r"\textendash{}",
    "—": r"\textemdash{}",
    "‘": "`",
    "’": "'",
    "“": "``",
    "”": "''",
    "•": r"\textbullet{}",
    "…": r"\ldots{}",
    "\u00a0": "~",
    "₹": "Rs.",
    "€": "EUR",
    "£": r"\pounds{}",
    "×": r"$\times$",
    "°": r"\textdegree{}",
    "™": r"\texttrademark{}",
    "©": r"\textcopyright{}",
    "®": r"\textregistered{}",
    "→": r"$\rightarrow$",
    "≥": r"$\geq$",
    "≤": r"$\leq$",
    "±": r"$\pm$",
    "\u200b": "",
    "\ufeff": "",
}
_ACCENTS = {
    "\u0301": "'",
    "\u0300": "`",
    "\u0302": "^",
    "\u0303": "~",
    "\u0308": '"',
    "\u0327": "c",
    "\u0304": "=",
}


class CompileError(RuntimeError):
    """Tectonic failed, timed out or isn't installed."""

    pass


class UnsupportedCharacter(ValueError):
    """A character the resume fonts can't print."""

    pass


def _escape_char(c: str) -> str:
    """Return the LaTeX for one character.

    Special characters are escaped, known Unicode is mapped to a
    command, and an accented Latin letter becomes an accent command.

    Raises:
        UnsupportedCharacter: The character can't be printed.
    """
    if c in _LATEX_SPECIAL:
        return _LATEX_SPECIAL[c]
    if c.isascii():
        return c
    if c in _UNICODE:
        return _UNICODE[c]
    decomposed = unicodedata.normalize("NFD", c)
    if len(decomposed) == 2 and decomposed[0].isascii() and decomposed[1] in _ACCENTS:
        accent = _ACCENTS[decomposed[1]]
        return f"\\{accent}{{{decomposed[0]}}}"
    raise UnsupportedCharacter(f"cannot render {c!r} (U+{ord(c):04X}) with the resume fonts")


def latex_escape(value: object) -> str:
    """Escape any value for safe use as LaTeX text (control chars dropped)."""
    text = _CONTROL.sub("", str(value))
    return "".join(_escape_char(c) for c in text)


def latex_url(value: object) -> str:
    """Return a URL safe for an href link; only http(s), mailto and tel.

    Raises:
        ValueError: Another scheme, or spaces, backslashes or braces.
    """
    url = _CONTROL.sub("", str(value)).strip()
    if not re.match(r"^(https?://|mailto:|tel:)", url):
        raise ValueError(f"refusing URL scheme in {url[:40]!r}")
    if re.search(r"[\s\\{}]", url):
        raise ValueError("refusing URL with spaces, backslashes or braces")
    return url.replace("%", r"\%").replace("#", r"\#").replace("~", r"\textasciitilde{}")


# LaTeX uses { } and % heavily, so the template uses (( )) style
# delimiters instead of Jinja's defaults.
_env = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    block_start_string="((*",
    block_end_string="*))",
    variable_start_string="(((",
    variable_end_string=")))",
    comment_start_string="((=",
    comment_end_string="=))",
    trim_blocks=True,
    lstrip_blocks=True,
    autoescape=False,
    undefined=StrictUndefined,
)
_env.filters["e"] = latex_escape
_env.filters["u"] = latex_url


def render_tex(doc: ResumeDoc) -> str:
    """Fill the fixed template with the resume and return the LaTeX source."""
    template = _env.get_template(TEMPLATES[doc.template_id])
    data = doc.model_dump(mode="json")
    return template.render(header=data["header"], sections=data["sections"])


def _tectonic_cache() -> Path:
    """Return Tectonic's package cache folder on this OS."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "Tectonic"
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "Tectonic"


def _sandbox_mode() -> str:
    """Return the sandbox to compile in: macos, container or docker."""
    mode = get_config().resume.sandbox
    if mode == "auto":
        if sys.platform == "darwin":
            return "macos"
        return "container" if os.environ.get("JOBPILOT_IN_CONTAINER") else "docker"
    return mode


def _command(workdir: Path) -> list[str]:
    """Return the sandboxed Tectonic command for a work folder.

    Raises:
        CompileError: Tectonic isn't installed.
    """
    tex = workdir / "resume.tex"
    mode = _sandbox_mode()
    if mode == "macos":
        tectonic = shutil.which("tectonic")
        if tectonic is None:
            raise CompileError("tectonic is not installed (brew install tectonic)")
        return [
            "/usr/bin/sandbox-exec",
            "-f",
            str(SANDBOX_PROFILE),
            "-D",
            f"WORKDIR={workdir.resolve()}",
            "-D",
            f"CACHE={_tectonic_cache().resolve()}",
            "-D",
            f"HOME={Path.home().resolve()}",
            tectonic,
            "--only-cached",
            "--untrusted",
            "--chatter",
            "minimal",
            str(tex),
        ]
    if mode == "container":
        # Already inside the backend's own container: Tectonic runs directly, with no package downloads
        # (--only-cached) and no shell escape (--untrusted). The container image holds the warmed cache.
        tectonic = shutil.which("tectonic")
        if tectonic is None:
            raise CompileError("tectonic is not installed in this container")
        return [tectonic, "--only-cached", "--untrusted", "--chatter", "minimal", str(tex)]
    return [
        "docker",
        "run",
        "--rm",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--network",
        "none",
        "--read-only",
        "--tmpfs",
        "/tmp",
        "-v",
        f"{workdir.resolve()}:/work",
        "jobpilot-tectonic",
        "tectonic",
        "--only-cached",
        "--untrusted",
        "--chatter",
        "minimal",
        "/work/resume.tex",
    ]


async def compile_pdf(tex_source: str) -> bytes:
    """Compile LaTeX produced by render_tex() in the sandbox. Raises CompileError with the log tail."""
    timeout = get_config().resume.compile_timeout_seconds
    with tempfile.TemporaryDirectory(prefix="jobpilot-tex-") as tmp:
        workdir = Path(tmp)
        (workdir / "resume.tex").write_text(tex_source)
        proc = await asyncio.create_subprocess_exec(
            *_command(workdir), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, cwd=workdir
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise CompileError(f"compile timed out after {timeout}s") from exc
        pdf = workdir / "resume.pdf"
        if proc.returncode != 0 or not pdf.exists():
            raise CompileError(out.decode(errors="replace")[-2000:])
        return pdf.read_bytes()


async def render_pdf(doc: ResumeDoc) -> bytes:
    """Render the resume to LaTeX and compile it to a PDF."""
    return await compile_pdf(render_tex(doc))
