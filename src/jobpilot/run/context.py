"""Per-run in-memory context.

Preferences, profile view, skill matcher and profile vector. Rebuilt
on demand - for example after a crashed run is resumed - so it never
needs to be checkpointed.
"""

from __future__ import annotations

from dataclasses import dataclass

from jobpilot.db.session import transaction
from jobpilot.matching import embed_score, profile_view
from jobpilot.matching.compute import SkillMatcher
from jobpilot.matching.profile_view import ProfileView
from jobpilot.models.preferences import Preferences
from jobpilot.profile import preferences
from jobpilot.run.deps import RunDeps, get_deps
from jobpilot.run.pipeline import JobContext


@dataclass
class RunContext:
    """What every step of one run reads: deps, preferences and the profile."""

    deps: RunDeps
    prefs: Preferences
    view: ProfileView
    matcher: SkillMatcher
    profile_vec: list[float] | None = None

    def job_context(self, holder: str, run_id: int | None) -> JobContext:
        """Return the context the per-job pipeline needs."""
        return JobContext(self.deps, self.prefs, self.view, self.matcher, holder, run_id)

    async def profile_vector(self) -> list[float]:
        """Return the whole-profile vector, syncing the profile index once."""
        if self.profile_vec is None:
            self.profile_vec = await embed_score.sync_profile(
                self.view, self.deps.embedder, self.deps.vectors
            )
        return self.profile_vec


# Run id -> its context, kept for the life of the run.
_contexts: dict[int, RunContext] = {}


async def build() -> RunContext:
    """Load the preferences and profile view and build a fresh context."""
    deps = get_deps()
    async with transaction() as conn:
        prefs, _ = await preferences.load(conn)
        view = await profile_view.load(conn)
    return RunContext(deps, prefs, view, SkillMatcher(view, deps.embedder))


async def for_run(run_id: int) -> RunContext:
    """Return the context of a run, building it the first time."""
    if run_id not in _contexts:
        _contexts[run_id] = await build()
    return _contexts[run_id]


def forget(run_id: int) -> None:
    """Drop a run's context and the agent's usage counters for it."""
    ctx = _contexts.pop(run_id, None)
    if ctx is not None and ctx.deps.agent is not None:
        ctx.deps.agent.runtime.forget(run_id)
        ctx.deps.agent.jd_lookups.pop(run_id, None)
