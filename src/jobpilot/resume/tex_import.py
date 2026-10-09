"""Import a resume written with the JobPilot resume macros into a ResumeDoc.

Understands the macro set of templates/resume/resume.tex.j2 (built from
the user's own .tex): resumesection, datedrow and skill commands,
itemize lists, bold project titles and a summary paragraph.
Deterministic - no LLM.
"""

from __future__ import annotations

import re
import unicodedata

from jobpilot.models.resume_doc import Bullet, Contact, Entry, Header, Layout, ResumeDoc, Section


class ImportError_(ValueError):
    """The .tex doesn't have the layout the importer understands."""

    pass


def read_group(s: str, i: int) -> tuple[str, int]:
    """Return the content of the balanced {...} group starting at s[i] and the index after it."""
    if s[i] != "{":
        raise ImportError_(f"expected '{{' at {i}: {s[i : i + 30]!r}")
    depth = 0
    for j in range(i, len(s)):
        c = s[j]
        # Skip the character after a backslash: it is escaped.
        if c == "\\":
            continue
        if c == "{" and s[j - 1] != "\\":
            depth += 1
        elif c == "}" and s[j - 1] != "\\":
            depth -= 1
            if depth == 0:
                return s[i + 1 : j], j + 1
    raise ImportError_("unbalanced braces")


_DROP = [
    r"\\fontfamily\{[^}]*\}",
    r"\\fontsize\{[^}]*\}\{[^}]*\}",
    r"\\selectfont",
    r"\\bfseries",
    r"\\hspace\*?\{[^}]*\}",
    r"\\vspace\*?\{[^}]*\}",
    r"\\par\b",
    r"\\enspace",
    r"\\noindent",
]
_UNWRAP = re.compile(r"\\(?:textbf|textit|emph|underline|mbox|textsf|textsc)\{([^{}]*)\}")
# The inverse of resume/render.py's escaping, so render -> import round-trips.
_TEXT_COMMANDS = {
    "textendash": "–",
    "textemdash": "—",
    "textbullet": "•",
    "ldots": "…",
    "textbackslash": "\x00",
    "textasciitilde": "~",
    "textasciicircum": "^",
    "pounds": "£",
    "textdegree": "°",
    "texttrademark": "™",
    "textcopyright": "©",
    "textregistered": "®",
}
_TEXT_COMMAND = re.compile(r"\\(" + "|".join(_TEXT_COMMANDS) + r")(?![a-zA-Z])(?:\{\})?")
# Escaped braces become placeholders so they survive the removal of LaTeX grouping braces.
_ESCAPES = {r"\&": "&", r"\%": "%", r"\$": "$", r"\#": "#", r"\_": "_", r"\{": "\x01", r"\}": "\x02"}


_ACCENT_MARKS = {
    "'": "\u0301",
    "`": "\u0300",
    "^": "\u0302",
    "~": "\u0303",
    '"': "\u0308",
    "c": "\u0327",
    "=": "\u0304",
}
_ACCENT = re.compile(r"""\\(['`^~"=]|c(?=[{ ]))\s*(?:\{([A-Za-z])\}|([A-Za-z]))""")


def _accent(m: re.Match[str]) -> str:
    """Turn a LaTeX accent command match into the accented letter."""
    letter = m.group(2) or m.group(3)
    return unicodedata.normalize("NFC", letter + _ACCENT_MARKS[m.group(1)])


def tex_to_text(s: str) -> str:
    """Turn a LaTeX fragment into plain text.

    Drops comments and layout commands, unwraps formatting, and turns
    escapes and symbol commands back into characters - the inverse of
    the renderer's escaping.
    """
    s = "\n".join(line for line in s.splitlines() if not line.lstrip().startswith("%"))
    s = _ACCENT.sub(_accent, s)
    for pattern in _DROP:
        s = re.sub(pattern, " ", s)
    s = _TEXT_COMMAND.sub(lambda m: _TEXT_COMMANDS[m.group(1)], s)
    s = s.replace("``", "“").replace("''", "”").replace("\\\\", " ")
    while _UNWRAP.search(s):
        s = _UNWRAP.sub(r"\1", s)
    s = s.replace("---", "—").replace("--", "–")
    for k, v in _ESCAPES.items():
        s = s.replace(k, v)
    s = s.replace("{", "").replace("}", "")
    s = s.replace("\x00", "\\").replace("\x01", "{").replace("\x02", "}")  # literals restored last
    return re.sub(r"\s+", " ", s).strip()


def _commands(s: str, name: str, nargs: int) -> list[list[str]]:
    """Return the brace arguments of every use of a LaTeX command.

    Args:
        s: The LaTeX text.
        name: The command name without the backslash.
        nargs: How many brace groups to read after it.

    Returns:
        One list of arguments per use, in order.
    """
    out = []
    for m in re.finditer(rf"\\{name}(?![a-zA-Z])", s):
        i, args = m.end(), []
        for _ in range(nargs):
            while s[i].isspace():
                i += 1
            arg, i = read_group(s, i)
            args.append(arg)
        out.append(args)
    return out


def _itemize_items(block: str) -> list[tuple[str, str | None]]:
    """Items of one itemize body as (text, optional second line after \\\\)."""
    items = []
    for raw in re.split(r"\\item(?![a-zA-Z])", block)[1:]:
        main, _, sub = raw.partition("\\\\")
        items.append((tex_to_text(main), tex_to_text(sub) or None))
    return items


class _Ids:
    """Hands out sequential element ids: s1, s2 for sections, e1 for entries, b1 for bullets."""

    def __init__(self) -> None:
        """Start every counter at zero."""
        self.n = {"s": 0, "e": 0, "b": 0}

    def __call__(self, prefix: str) -> str:
        """Return the next id with this prefix."""
        self.n[prefix] += 1
        return f"{prefix}{self.n[prefix]}"


def _top_level_groups(s: str) -> list[str]:
    """Return the contents of the outermost brace groups, in order."""
    groups, i = [], 0
    while i < len(s):
        if s[i] == "{" and (i == 0 or s[i - 1] != "\\"):
            content, i = read_group(s, i)
            groups.append(content)
        else:
            i += 1
    return groups


def _header(body: str) -> Header:
    """Read the name and contact links from the centered header block.

    Raises:
        ImportError_: No header block, or it lacks name and contacts.
    """
    m = re.search(r"\\begin\{center\}(.*?)\\end\{center\}", body, re.S)
    if not m:
        raise ImportError_("no \\begin{center} header block")
    groups = _top_level_groups(m.group(1))
    if len(groups) < 2:
        raise ImportError_("header needs a name group and a contacts group")
    name = tex_to_text(groups[0])
    contacts: list[Contact] = []
    contact_src = re.sub(r"^\s*\\fontsize\{[^}]*\}\{[^}]*\}\\selectfont", "", groups[-1])
    for part in re.split(r"\\enspace\s*\\textbullet\s*\\enspace", contact_src):
        part = part.strip()
        href = _commands(part, "href", 2)
        if href:
            url, label = href[0]
            contacts.append(Contact(text=tex_to_text(label), url=url, underline="\\underline" in label))
        elif tex_to_text(part):
            contacts.append(Contact(text=tex_to_text(part)))
    return Header(name=name, contacts=contacts)


def _section(title: str, chunk: str, ids: _Ids) -> Section:
    """Build one Section, choosing its layout from the commands it uses.

    Args:
        title: The section title.
        chunk: The section's LaTeX.
        ids: The id counter.

    Returns:
        The section with its entries.
    """
    section = Section(id=ids("s"), title=tex_to_text(title), layout=Layout.PARAGRAPH)
    has_items = "\\begin{itemize}" in chunk
    if "\\skill" in chunk:
        section.layout = Layout.SKILLS
        for label, items in _commands(chunk, "skill", 2):
            section.entries.append(
                Entry(
                    id=ids("e"),
                    title=tex_to_text(label),
                    items=[tex_to_text(x) for x in items.split(",") if x.strip()],
                )
            )
    elif "\\datedrow" in chunk and not has_items:
        section.layout = Layout.DATED_PAIRS
        rows = _commands(chunk, "datedrow", 2)
        if len(rows) % 2:
            raise ImportError_(f"section {title!r}: dated rows must come in pairs")
        for top, bottom in zip(rows[::2], rows[1::2], strict=True):
            section.entries.append(
                Entry(
                    id=ids("e"),
                    title=tex_to_text(top[0]),
                    date=tex_to_text(top[1]),
                    subtitle=tex_to_text(bottom[0]),
                    detail=tex_to_text(bottom[1]),
                )
            )
    elif has_items:
        section.layout = (
            Layout.ROLES
            if "\\datedrow" in chunk
            else (Layout.PROJECTS if re.search(r"^\s*\\textbf\{", chunk, re.M) else Layout.BULLETS)
        )
        _sequential(section, chunk, ids)
    else:
        section.entries.append(Entry(id=ids("e"), text=tex_to_text(chunk)))
    return section


_TOKEN = re.compile(
    r"(?P<dated>\\datedrow)|(?P<title>^\s*\\textbf\{)|(?P<note>^\s*\\hspace\{[^}]*\})|"
    r"(?P<items>\\begin\{itemize\}(?P<body>.*?)\\end\{itemize\})",
    re.M | re.S,
)


def _sequential(section: Section, chunk: str, ids: _Ids) -> None:
    """Read entries and bullets in document order (roles, projects, lists).

    A dated row or a bold title starts an entry; a following note line
    and itemize list belong to it.
    """
    current: Entry | None = None
    for m in _TOKEN.finditer(chunk):
        if m.group("dated"):
            ((left, right),) = _commands(chunk[m.start() :], "datedrow", 2)[:1]
            current = Entry(id=ids("e"), title=tex_to_text(left), date=tex_to_text(right))
            section.entries.append(current)
        elif m.group("title"):
            start = chunk.index("\\textbf", m.start())
            text, _ = read_group(chunk, start + len("\\textbf"))
            current = Entry(id=ids("e"), title=tex_to_text(text))
            section.entries.append(current)
        elif m.group("note"):
            line = chunk[m.end() : chunk.find("\n", m.end())]
            if current is not None:
                current.note = tex_to_text(line)
        elif m.group("items"):
            if current is None:
                current = Entry(id=ids("e"))
                section.entries.append(current)
            for text, sub in _itemize_items(m.group("body")):
                current.bullets.append(Bullet(id=ids("b"), text=text, sub=sub))
            if section.layout == Layout.BULLETS:
                current = None


def import_tex(source: str) -> ResumeDoc:
    """Import a whole .tex resume into a ResumeDoc.

    Raises:
        ImportError_: No document body or no sections.
    """
    m = re.search(r"\\begin\{document\}(.*)\\end\{document\}", source, re.S)
    if not m:
        raise ImportError_("no document body")
    body = m.group(1)
    ids = _Ids()
    header = _header(body)
    parts = re.split(r"\\resumesection\{", body)
    sections = []
    for part in parts[1:]:
        title, end = read_group("{" + part, 0)
        sections.append(_section(title, part[end - 1 :], ids))
    if not sections:
        raise ImportError_("no \\resumesection found")
    return ResumeDoc(header=header, sections=sections)
