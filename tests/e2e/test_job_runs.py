"""End to end: /start fetches, dedups, filters, screens, scores and queues.

The source's cursor makes the next run fetch nothing old; shares
measure recall.
"""

from typing import Any

import pytest
from sqlalchemy import func, select

from jobpilot.bot import router
from jobpilot.db.session import transaction
from jobpilot.db.tables import cards, job_sources, jobs, queue, source_cursors
from jobpilot.models.preferences import Preferences
from jobpilot.profile import preferences
from tests.conftest import FixtureSource, confirmed_profile, fixture_postings, telegram_update
from tests.e2e.test_start_flow import deliver_all, drain_work

pytestmark = pytest.mark.usefixtures("db")


async def set_prefs(**kw: Any) -> None:
    """Save preferences built from the given fields."""
    async with transaction() as conn:
        _, version = await preferences.load(conn)
        await preferences.save(conn, Preferences(**kw), version)


def use_fixture_source(deps: Any) -> None:
    """Make the run fetch only from the fixed fixture postings."""
    source = FixtureSource(fixture_postings())
    deps.sources = lambda prefs, run_id=None: [source]


async def scalar(stmt: Any) -> Any:
    """Run a statement and return its single value."""
    async with transaction() as conn:
        return (await conn.execute(stmt)).scalar_one()


async def statuses() -> dict[str, int]:
    """Return how many jobs are in each pipeline status."""
    async with transaction() as conn:
        rows = await conn.execute(
            select(jobs.c.pipeline_status, func.count()).group_by(jobs.c.pipeline_status)
        )
        return dict(rows.all())  # type: ignore[arg-type]


async def test_start_runs_flow_2_end_to_end(p2_deps: Any, checkpointer: Any) -> None:
    """A /start dedups 7 postings into 6 jobs, filters, scores and cards; a rerun fetches nothing."""
    await confirmed_profile()
    await set_prefs(titles_avoid=["Mechanical"], threshold=40)
    use_fixture_source(p2_deps)
    await router.ingest_update(telegram_update(8001, "/start"))
    await drain_work(checkpointer)

    assert await scalar(select(func.count()).select_from(jobs)) == 6  # 7 postings, Acme seen twice
    assert await scalar(select(func.count()).select_from(job_sources)) == 7
    st = await statuses()
    assert st.get("filtered") == 1  # Senior Mechanical Design Engineer (title to avoid)
    assert st.get("carded", 0) + st.get("below_bar", 0) == 5
    assert await scalar(select(func.count()).select_from(cards)) == st.get("carded", 0)
    assert await scalar(select(func.count()).select_from(queue).where(queue.c.served_at.is_(None))) == 0
    cursor = await scalar(select(source_cursors.c.cursor).where(source_cursors.c.source == "job_finder"))
    assert cursor == {"served": 7}
    texts = await deliver_all()
    summary = next(t for t in texts if "finished" in t)
    assert "Job Finder: 7 found, 6 new" in summary and "1 filtered" in summary

    # The next /start: the cursor means nothing old is fetched again.
    await router.ingest_update(telegram_update(8002, "/start"))
    await drain_work(checkpointer)
    texts = await deliver_all()
    assert any("Job Finder: 0 found, 0 new" in t for t in texts)
    assert await scalar(select(func.count()).select_from(jobs)) == 6


async def test_run_stops_until_profile_is_confirmed(p2_deps: Any, checkpointer: Any) -> None:
    """Without a confirmed profile the run stops at preflight and says why."""
    await router.ingest_update(telegram_update(8101, "/start"))
    await drain_work(checkpointer)
    summary = next(t for t in await deliver_all() if "finished" in t)
    assert "preflight check failed" in summary and "Confirm your profile" in summary


async def test_next_run_retries_jobs_left_new_after_pipeline_failure(
    p2_deps: Any, checkpointer: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Jobs left new by a failing run are processed by the next run."""
    from jobpilot.run import pipeline

    await confirmed_profile()
    await set_prefs(threshold=20)  # this test is about retrying, not scoring: every fixture job clears it
    original = pipeline.process

    async def unavailable(*args: Any, **kwargs: Any) -> str:
        """Fail like a provider outage."""
        raise RuntimeError("temporary provider failure")

    use_fixture_source(p2_deps)
    monkeypatch.setattr(pipeline, "process", unavailable)
    await router.ingest_update(telegram_update(8501, "/start"))
    await drain_work(checkpointer)
    assert (await statuses()).get("new", 0) > 0
    from jobpilot.safety import killswitch

    async with transaction() as conn:  # the error spike halted everything automatically
        assert await killswitch.is_engaged(conn)
        await killswitch.release(conn)
    monkeypatch.setattr(pipeline, "process", original)
    await set_prefs(threshold=20, cards_per_day=5)
    await router.ingest_update(telegram_update(8502, "/start"))
    await drain_work(checkpointer)
    # The five-card cap leaves the sixth posting for another day.
    assert (await statuses()).get("new", 0) == 1
    assert (await statuses()).get("carded", 0) == 5


async def test_jobs_wait_for_the_api_key(p2_deps: Any, checkpointer: Any) -> None:
    """Without an LLM a shared job waits for extraction and the user is told."""
    await confirmed_profile()
    p2_deps.llm = None
    await router.ingest_update(
        telegram_update(
            8201,
            "share https://www.linkedin.com/jobs/view/4100000001/ "
            "Backend Engineer at Orbit\nPython FastAPI role " + "x " * 250,
        )
    )
    await drain_work(checkpointer)
    assert (await statuses()).get("pending_extraction") == 1
    assert any("waits for the API key" in t for t in await deliver_all())


async def test_shares_measure_recall(p2_deps: Any, checkpointer: Any) -> None:
    """A known shared job counts as a hit, an unknown one as a miss."""
    await confirmed_profile()
    use_fixture_source(p2_deps)
    await router.ingest_update(telegram_update(8301, "/start"))
    await drain_work(checkpointer)
    await deliver_all()
    # A job JobPilot already found (by its LinkedIn id) -> hit.
    await router.ingest_update(telegram_update(8302, "https://www.linkedin.com/jobs/view/3923456789/"))
    # A job it never saw -> miss, then scored like any other.
    await router.ingest_update(
        telegram_update(
            8303,
            "Platform Engineer at Zeta Systems\n"
            "https://www.linkedin.com/jobs/view/4200000002/\n"
            "We need Python, Kafka and SQL. " * 20,
        )
    )
    await drain_work(checkpointer)
    texts = await deliver_all()
    assert any(t.startswith("I already had this one: Junior Data Scientist") for t in texts)
    assert any(t.startswith("New to me") and "Zeta Systems" in t and "scored" in t for t in texts)
    await router.ingest_update(telegram_update(8304, "/status"))
    await drain_work(checkpointer)
    assert any("Recall (30 days): 1/2 shared jobs already found (50%)" in t for t in await deliver_all())


async def test_replaying_a_run_rescores_its_jobs_the_same_way(
    p2_deps: Any, checkpointer: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The replay script re-scores a run's jobs; same JD + same profile gives the same score."""
    from jobpilot.db.tables import runs
    from scripts import replay_run

    await confirmed_profile()
    await set_prefs(titles_avoid=["Mechanical"], threshold=40)
    use_fixture_source(p2_deps)
    await router.ingest_update(telegram_update(8601, "/start"))
    await drain_work(checkpointer)
    run_id = await scalar(select(runs.c.id))

    async def keep_open() -> None:
        """Leave the shared test store open; the p2_deps fixture closes it."""

    monkeypatch.setattr(p2_deps.vectors, "close", keep_open)
    monkeypatch.setattr(replay_run.VectorStore, "from_settings", lambda: p2_deps.vectors)
    monkeypatch.setattr(replay_run, "default_embedder", lambda: p2_deps.embedder)
    capsys.readouterr()
    await replay_run.main(run_id)
    lines = [line for line in capsys.readouterr().out.splitlines() if "->" in line]
    assert len(lines) == 5  # every scored job (the filtered one has no score)
    for line in lines:
        old, new = line.rsplit(": ", 1)[1].split(" -> ")
        assert float(old) == float(new)
