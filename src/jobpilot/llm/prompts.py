"""Versioned prompts: prompts/<area>/<name>_v<N>.md, the version pinned in config/defaults.yaml.

File format: a '## system' section and a '## user' section, both Jinja templates.
Untrusted text (JDs, emails, web pages) must be passed through guardrails/sanitize.py and placed in a
fenced data block by the template - never interpolated as instructions.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from jinja2 import Environment, StrictUndefined

from jobpilot.core.config import REPO_ROOT, get_config

PROMPTS_DIR = REPO_ROOT / "prompts"
_env = Environment(undefined=StrictUndefined, autoescape=False, keep_trailing_newline=False)


@dataclass(frozen=True)
class RenderedPrompt:
    """A prompt ready to send: its name, version and the two messages."""

    name: str
    version: int
    system: str
    user: str

    @property
    def id(self) -> str:
        """The prompt's id stored with every output, e.g. ``tailor_v3``."""
        return f"{self.name}_v{self.version}"


@lru_cache
def _load(name: str, version: int) -> tuple[str, str]:
    """Read a prompt file and split it into system and user templates.

    Args:
        name: The prompt path under ``prompts/`` without the version.
        version: The version number.

    Returns:
        The (system, user) template texts.

    Raises:
        ValueError: The file lacks a ``## system`` or ``## user``
            section.
    """
    path = PROMPTS_DIR / f"{name}_v{version}.md"
    text = path.read_text()
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        if line.strip().lower() in ("## system", "## user"):
            current = line.strip().lower()[3:]
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    if "system" not in sections or "user" not in sections:
        raise ValueError(f"{path} needs '## system' and '## user' sections")
    return "\n".join(sections["system"]).strip(), "\n".join(sections["user"]).strip()


def render(name: str, version: int | None = None, **variables: Any) -> RenderedPrompt:
    """Render a prompt's templates with ``variables``.

    Args:
        name: The prompt path under ``prompts/`` without the version.
        version: The version; defaults to the one pinned in config.
        **variables: Template variables; a missing one is an error.

    Returns:
        The rendered prompt.
    """
    version = version if version is not None else get_config().prompt_versions[name]
    system_t, user_t = _load(name, version)
    return RenderedPrompt(
        name=name,
        version=version,
        system=_env.from_string(system_t).render(**variables),
        user=_env.from_string(user_t).render(**variables),
    )
