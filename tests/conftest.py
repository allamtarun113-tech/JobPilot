"""Shared fixtures. Tests run against the local `jobpilot_test` database (never the dev database)."""

from __future__ import annotations

import os

# Configure the environment before any jobpilot import reads settings.
# Configure the environment before any jobpilot import reads settings.
_url = os.environ.get("DATABASE_URL", "postgresql+psycopg://localhost/jobpilot_test")
# Never touch a database that isn't a *_test one.
if not _url.rstrip("/").endswith("_test"):
    _url = "postgresql+psycopg://localhost/jobpilot_test"
os.environ["DATABASE_URL"] = _url
os.environ["JOBPILOT_ENV"] = "test"
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["TELEGRAM_CHAT_ID"] = "4242"
os.environ["APPROVAL_TOKEN_KEY"] = "test-approval-key"
_LIVE_CONTRACTS = os.environ.get("CONTRACT_LIVE") == "1"  # nightly live contract tests keep their keys
for _key in (
    ()
    if _LIVE_CONTRACTS
    else (
        "OPENAI_API_KEY",
        "JSEARCH_API_KEY",
        "SUPABASE_URL",
        "SUPABASE_SERVICE_ROLE_KEY",
        "QDRANT_URL",
        "QDRANT_API_KEY",
        "DASHBOARD_TOTP_SECRET",
        "DASHBOARD_SESSION_SECRET",
        "GMAIL_OAUTH_CLIENT_ID",
        "GMAIL_OAUTH_CLIENT_SECRET",
        "GMAIL_TOKEN_KEY",
    )
):
    os.environ[_key] = ""
os.environ["VERCEL"] = "0"

# Tests embed with HashEmbedder. qdrant_client imports fastembed (and onnxruntime) when installed, and
# onnxruntime can abort the process in a C++ destructor at exit on macOS - keep it out of the test process.
import sys  # noqa: E402

sys.modules["fastembed"] = None  # type: ignore[assignment]

from collections.abc import AsyncIterator, Iterator  # noqa: E402
from typing import Any  # noqa: E402

import pytest  # noqa: E402
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter  # noqa: E402
from sqlalchemy import text  # noqa: E402

from jobpilot.core import clock  # noqa: E402
from jobpilot.core.config import get_settings  # noqa: E402
from jobpilot.core.errors import DeliveryError  # noqa: E402
from jobpilot.db.session import get_engine  # noqa: E402
from jobpilot.db.tables import metadata  # noqa: E402
from jobpilot.monitoring.tracing import setup_tracing  # noqa: E402

SPANS = InMemorySpanExporter()
setup_tracing("test", exporter=SPANS)

APP_TABLES = [t.name for t in metadata.sorted_tables]  # every table we own
CHECKPOINT_TABLES = ["checkpoints", "checkpoint_blobs", "checkpoint_writes"]
OWNER_CHAT = 4242


@pytest.fixture
def spans() -> Iterator[InMemorySpanExporter]:
    """Yield the in-memory span exporter, cleared for this test."""
    SPANS.clear()
    yield SPANS


@pytest.fixture
async def db() -> AsyncIterator[None]:
    """A clean database for each test that needs one."""
    assert get_settings().database_url.endswith("_test")
    async with get_engine().begin() as conn:
        await conn.execute(text(f"TRUNCATE {', '.join(APP_TABLES + CHECKPOINT_TABLES)} RESTART IDENTITY"))
    clock.reset_clock()
    yield
    clock.reset_clock()


@pytest.fixture(autouse=True)
def _fresh_config():  # type: ignore[no-untyped-def]
    """A test may change a setting on the cached config (a daily email limit); the next test starts clean."""
    yield
    from jobpilot.core import config

    config.get_config.cache_clear()


def _override_defaults(monkeypatch: pytest.MonkeyPatch, **sections: dict) -> None:
    """Override sections of ``defaults.yaml`` for one test.

    Args:
        monkeypatch: The pytest monkeypatch fixture.
        **sections: Top-level config sections to replace.
    """
    from jobpilot.core import config

    real = config.load_yaml

    def load(name: str) -> dict:
        """Load a config file, with the overrides applied to defaults.yaml."""
        data = real(name)
        if name == "defaults.yaml":
            data.update(sections)
        return data

    monkeypatch.setattr(config, "load_yaml", load)
    config.get_config.cache_clear()


@pytest.fixture
def frozen_clock() -> Iterator[clock.FrozenClock]:
    """Install a controllable clock for the test, then restore the real one."""
    fc = clock.FrozenClock()
    clock.set_clock(fc)
    yield fc
    clock.reset_clock()


@pytest.fixture
async def checkpointer() -> AsyncIterator[AsyncPostgresSaver]:
    """Yield a LangGraph Postgres checkpointer on the test database."""
    async with AsyncPostgresSaver.from_conn_string(get_settings().psycopg_dsn) as saver:
        yield saver


class FakeSender:
    """Records deliveries. `failures` is a list of booleans consumed per call (True = fail this call)."""

    def __init__(self, failures: list[bool] | None = None, retryable: bool = True) -> None:
        """Create the fake.

        Args:
            failures: Per-call flags; True makes that call fail.
            retryable: Whether the simulated failures are retryable.
        """
        self.calls: list[dict[str, Any]] = []
        self.failures = list(failures or [])
        self.retryable = retryable
        self.next_id = 500

    async def deliver(self, payload: dict[str, Any]) -> str | None:
        """Record the call, maybe fail, and return a made-up message id."""
        self.calls.append(dict(payload))
        if self.failures and self.failures.pop(0):
            raise DeliveryError("simulated failure", retryable=self.retryable)
        if payload.get("op") == "edit_message":
            return str(payload["message_id"])
        self.next_id += 1
        return str(self.next_id)


def telegram_update(update_id: int, text: str, chat_id: int = OWNER_CHAT) -> dict[str, Any]:
    """Build a minimal Telegram text-message update from ``chat_id``."""
    return {
        "update_id": update_id,
        "message": {"message_id": update_id, "chat": {"id": chat_id}, "text": text},
    }


SAMPLE_RESUME = __import__("pathlib").Path(__file__).parent / "fixtures" / "sample_resume.tex"


@pytest.fixture
def store(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    """Point every object-store user at a temp directory."""
    from jobpilot.integrations.storage import LocalStore

    st = LocalStore(tmp_path / "store")
    monkeypatch.setattr("jobpilot.resume.doc.get_store", lambda: st)
    monkeypatch.setattr("jobpilot.web.routes.resume.get_store", lambda: st)
    return st


class FakeLLM:
    """Stands in for LLMClient.structured: requirements derived from the JD text, deterministic."""

    def __init__(self) -> None:
        """Start with no recorded calls."""
        self.calls: list[str] = []

    async def structured(
        self, task: str, prompt: Any, output: type, run_id: Any = None, max_tokens: Any = None
    ) -> Any:
        """Return a canned output for the requested schema.

        Requirements list the known skills named in the prompt; the resume
        composer and email writer propose nothing and the reviewer passes.
        """
        from types import SimpleNamespace

        from jobpilot.models.requirements import Requirements, Seniority
        from jobpilot.models.resume_review import ResumeReview

        self.calls.append(task)
        from jobpilot.models.email import EmailPoints
        from jobpilot.models.resume_plan import BulletFixes, ResumePlan

        if output is BulletFixes:
            return SimpleNamespace(output=BulletFixes(bullets=[]))
        if output is ResumePlan:
            return SimpleNamespace(output=ResumePlan(summary="", projects=[], experience=[], skills=[]))
        if output is EmailPoints:
            return SimpleNamespace(output=EmailPoints(points=[]))
        if output is ResumeReview:
            return SimpleNamespace(output=ResumeReview(score=90, ready=True))
        text = prompt.user.lower()
        must = [
            s
            for s in ("Python", "SQL", "Tableau", "Excel", "FastAPI", "Kafka", "AutoCAD", "PyTorch")
            if s.lower() in text
        ] or ["Python"]
        req = Requirements(
            must_skills=must,
            nice_skills=["dbt"],
            years_min=1,
            years_max=3,
            seniority=Seniority.ENTRY,
            domain="software and analytics",
            title_norm="engineer",
            is_job_description="not a job" not in text,
        )
        return SimpleNamespace(output=req)


@pytest.fixture
async def p2_deps(monkeypatch):  # type: ignore[no-untyped-def]
    """Run dependencies with fakes: hashing embedder, in-memory Qdrant, fake LLM, no network lookups."""
    import httpx

    from jobpilot.integrations.vectors import VectorStore
    from jobpilot.llm.embeddings import HashEmbedder
    from jobpilot.run import deps as deps_mod
    from jobpilot.sources.enrich_lookup import EnrichLookup

    vectors = VectorStore.in_memory()
    deps = deps_mod.RunDeps(
        embedder=HashEmbedder(),
        vectors=vectors,
        llm=FakeLLM(),
        jobs_api=None,
        enrich=EnrichLookup(None, transport=httpx.MockTransport(lambda r: httpx.Response(404))),
    )
    deps_mod.set_deps(deps)
    yield deps
    await vectors.close()


async def confirmed_profile() -> None:
    """Import the sample resume and confirm every draft profile item."""
    from jobpilot.db.session import transaction
    from jobpilot.profile import confirm
    from jobpilot.resume import doc as resume_doc

    await resume_doc.import_from_tex(SAMPLE_RESUME)
    async with transaction() as conn:
        await confirm.confirm_all(conn)


JD = """About the role: Acme Analytics is hiring a Data Analyst to join the retail insights team in
Bengaluru. You will partner with category managers, store operations and finance to turn sales and
inventory data into decisions. Responsibilities include designing and maintaining executive
dashboards in Tableau and Power BI, writing efficient SQL against our Snowflake warehouse, building
automated weekly performance reports with Python and Pandas, running ad hoc deep dives on
promotions, pricing and basket composition, defining metrics with business owners and documenting
them in our data catalogue, and presenting findings to leadership. Requirements: one to three years
of experience in analytics or business intelligence, strong SQL including window functions, hands-on
experience with Tableau or Power BI, comfort with Excel modelling, and clear written and verbal
communication. Nice to have: dbt, Airflow, experience in retail or e-commerce, basic statistics and
A/B testing. We offer a hybrid work model, learning budget, health insurance for you and your
family, and a collaborative team."""


def fixture_postings() -> list[Any]:
    """Seven postings as a source would deliver them: Acme's Data Analyst role twice (LinkedIn and Naukri, so
    one job with two sightings) and one Mechanical role that the avoid-titles filter drops."""
    from jobpilot.models.job import JobPosting, SourceKind

    def p(n: int, board: str, title: str, company: str, city: str, text: str) -> JobPosting:
        """Build one posting from a board, with a stable board id for LinkedIn."""
        return JobPosting(
            source=SourceKind.SEARCH,
            external_id=f"fixture:{n}",
            title=title,
            company=company,
            location=city,
            url=f"https://www.{board}.com/jobs/view/{3912345678 + n}/",
            board=board,
            board_job_id=str(3912345678 + n) if board == "linkedin" else None,
            description=text,
        )

    data = "Data role using SQL, Python and Tableau dashboards for retail analytics."
    return [
        p(0, "linkedin", "Data Analyst", "Acme Analytics", "Bengaluru", data),
        p(11111111, "linkedin", "Junior Data Scientist", "Nimbus AI", "Bengaluru", data),
        p(2, "linkedin", "Senior Mechanical Design Engineer", "Forge Works", "Pune", "AutoCAD design."),
        p(3, "naukri", "Data Analyst", "Acme Analytics", "Bengaluru", data),
        p(4, "naukri", "Python Developer", "Orbit Labs", "Bengaluru", "Python FastAPI services with SQL."),
        p(5, "indeed", "Business Intelligence Analyst", "Delta Retail", "Hyderabad", data),
        p(6, "indeed", "Data Engineer", "Kappa Data", "Bengaluru", "Python, SQL and Kafka pipelines."),
    ]


class FixtureSource:
    """A job source serving fixed postings; its cursor counts what was served, so a repeat run gets none."""

    name = "job_finder"
    kind = "search"

    def __init__(self, postings: list[Any]) -> None:
        """Serve these postings."""
        self.postings = postings

    async def fetch(self, cursor: dict[str, Any] | None) -> Any:
        """Return the postings not yet served according to the cursor."""
        from jobpilot.sources.base import FetchResult

        served = int((cursor or {}).get("served", 0))
        fresh = self.postings[served:]
        return FetchResult(fresh, {"served": len(self.postings)}, {"fetched": len(fresh)})
