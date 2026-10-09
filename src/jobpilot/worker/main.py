"""Worker process: Telegram long polling, the work queue, runs and agents.

Holds read-only MCP connections only (web search and fetch); sending is the dispatcher's job.
Runs on the user's laptop (`make backend`); the dashboard is on Vercel.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
from concurrent.futures import ProcessPoolExecutor
from contextlib import AsyncExitStack
from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from jobpilot.bot import router
from jobpilot.core import cpu
from jobpilot.core.config import get_settings
from jobpilot.core.errors import JobPilotError
from jobpilot.core.logging import setup_logging
from jobpilot.db.session import dispose_engine, transaction
from jobpilot.hr_email.service import compose
from jobpilot.integrations.jobs_api import FallbackJobsClient
from jobpilot.integrations.mcp_client import MCPConnection
from jobpilot.integrations.mcp_process import ManagedClient
from jobpilot.integrations.vectors import VectorStore
from jobpilot.llm.agent_runtime import AgentRuntime
from jobpilot.llm.client import LLMClient
from jobpilot.llm.embeddings import default_embedder
from jobpilot.models.profile import ItemSource
from jobpilot.models.work import (
    EMAIL_DRAFT,
    PREP_REQUEST,
    PROFILE_NOTES,
    RENDER_RESUME,
    RUN_REQUEST,
    SHARED_JOB,
)
from jobpilot.monitoring.tracing import setup_tracing, shutdown_tracing
from jobpilot.prep import service as prep_service
from jobpilot.prep.research import PrepResearchAgent
from jobpilot.profile import items as profile_items
from jobpilot.profile.parser import extract_from_notes
from jobpilot.queue import outbox
from jobpilot.resume import doc as resume_doc
from jobpilot.run.deps import RunDeps, get_deps, set_deps
from jobpilot.run.graph import execute_run_request
from jobpilot.run.share import handle_share
from jobpilot.safety import idempotency
from jobpilot.sources.enrich_lookup import EnrichLookup
from jobpilot.sources.finder_agent import JobFinderAgent, find_contacts
from jobpilot.worker import scheduler, telegram_poller
from jobpilot.worker.queue_loop import QueueLoop

log = logging.getLogger("worker")


def worker_id() -> str:
    """Return this process's lease holder id (host name and pid)."""
    return f"worker-{socket.gethostname()}-{os.getpid()}"


async def tell_user(key: str, text: str) -> None:
    """A refusal from a background request (dashboard action) goes to the owner's chat, once."""
    async with transaction() as conn:
        await outbox.telegram_message(conn, text, idempotency_key=idempotency.outbox("work-result", key))


async def open_checkpointer(stack: AsyncExitStack) -> AsyncPostgresSaver:
    """The run checkpointer on a small pool that checks each connection before lending it. A single
    long-lived connection (LangGraph's from_conn_string) was closed by the cloud pooler after a few idle
    hours, and every later /start failed with OperationalError until the worker restarted."""
    pool: AsyncConnectionPool = AsyncConnectionPool(
        get_settings().psycopg_dsn,
        min_size=1,
        max_size=3,
        max_idle=300,
        check=AsyncConnectionPool.check_connection,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        open=False,
    )
    await pool.open()
    stack.push_async_callback(pool.close)
    checkpointer = AsyncPostgresSaver(pool)  # type: ignore[arg-type]  # the saver accepts a pool
    await checkpointer.setup()
    return checkpointer


def make_handlers(holder: str, checkpointer: AsyncPostgresSaver | None) -> dict[str, Any]:
    """Build the work-item handlers, keyed by work kind.

    Args:
        holder: This worker's lease holder id.
        checkpointer: The run checkpointer.

    Returns:
        Work kind -> async handler taking the item's payload.
    """

    async def on_event(payload: dict[str, Any]) -> None:
        """Route a stored Telegram event (command, tap or message)."""
        await router.handle_event(int(payload["event_id"]))

    async def on_run_request(payload: dict[str, Any]) -> None:
        """Take the run lease and run (or resume) the batch graph."""
        await execute_run_request(payload, holder=holder, checkpointer=checkpointer)

    async def on_shared_job(payload: dict[str, Any]) -> None:
        """Process a job link the user shared."""
        await handle_share(int(payload["event_id"]))

    async def on_email_draft(payload: dict[str, Any]) -> None:
        """Write (or show) the HR email draft for a job."""
        deps = get_deps()
        if deps.llm is None:
            await tell_user(
                f"email:{payload['job_id']}:{payload.get('nonce')}", "Email drafting needs OPENAI_API_KEY."
            )
            return
        agent = deps.agent

        async def contacts(company: str, job_id: int) -> list[dict[str, str]]:
            """Search the web for the company's hiring email (Job Finder task)."""
            return await find_contacts(agent, company, job_id) if agent is not None else []

        try:
            await compose(
                int(payload["job_id"]),
                deps.llm,
                auto=bool(payload.get("auto")),
                address_override=payload.get("address") or None,
                contacts=contacts,
                vectors=deps.vectors,
            )
        except (ValueError, JobPilotError) as exc:  # a refusal, not a crash: tell the user, don't retry
            if payload.get("auto"):  # the automatic draft is quiet; you see what's missing when you tap
                log.info("automatic email draft for job %s not made: %s", payload["job_id"], exc)
                return
            await tell_user(
                f"email:{payload['job_id']}:{payload.get('nonce')}", f"Email draft not created: {exc}"
            )

    async def on_profile_notes(payload: dict[str, Any]) -> None:
        """Turn pasted notes into draft profile items."""
        deps = get_deps()
        if deps.llm is None:
            await tell_user(f"notes:{payload.get('nonce')}", "Reading notes needs OPENAI_API_KEY.")
            return
        async with transaction() as conn:
            places = await profile_items.places(conn)
            skills = await profile_items.skill_names(conn)
        try:
            drafts = await extract_from_notes(
                deps.llm, payload["notes"], places, skills
            )  # outside any transaction
        except (ValueError, JobPilotError) as exc:
            await tell_user(f"notes:{payload.get('nonce')}", f"Your notes could not be read: {exc}")
            return
        async with transaction() as conn:
            await profile_items.store_drafts(conn, drafts, ItemSource.NOTES)

    async def on_render_resume(payload: dict[str, Any]) -> None:
        """Render the base resume and store its PDF."""
        await resume_doc.render_current()

    async def on_prep_request(payload: dict[str, Any]) -> None:
        """Build the Prepare pack for a job (marks it failed on error)."""
        job_id = int(payload["job_id"])
        try:
            deps = get_deps()
            research = (
                PrepResearchAgent(deps.llm, deps.web).run
                if deps.web is not None and deps.llm is not None
                else None
            )
            await prep_service.generate(job_id, deps, research)
        except Exception as exc:
            await prep_service.fail(job_id, exc)
            raise

    return {
        router.WORK_KIND: on_event,
        RUN_REQUEST: on_run_request,
        SHARED_JOB: on_shared_job,
        PROFILE_NOTES: on_profile_notes,
        RENDER_RESUME: on_render_resume,
        PREP_REQUEST: on_prep_request,
        EMAIL_DRAFT: on_email_draft,
    }


async def build_deps(stack: AsyncExitStack) -> RunDeps:
    """Connect what is configured; anything missing is reported by preflight and skipped by the run."""
    settings = get_settings()
    vectors = VectorStore.from_settings()
    stack.push_async_callback(vectors.close)
    jobs_api = FallbackJobsClient.from_settings()
    llm = LLMClient() if settings.openai_api_key and settings.openai_api_key.get_secret_value() else None
    web: MCPConnection | None = None
    if llm is not None:  # web search runs on the same OpenAI key
        web_client = await stack.enter_async_context(ManagedClient("web", "worker"))
        web = MCPConnection("web", "worker", web_client)
    agent = JobFinderAgent(AgentRuntime(llm, web)) if llm is not None and web is not None else None
    log.info(
        "sources: jobs_api=%s job_finder=%s llm=%s",
        bool(jobs_api),
        bool(agent),
        bool(llm),
    )
    return RunDeps(
        embedder=default_embedder(),
        vectors=vectors,
        llm=llm,
        jobs_api=jobs_api,
        enrich=EnrichLookup(jobs_api),
        web=web,
        agent=agent,
    )


async def main() -> None:
    """Run the worker: queue loop, scheduler and Telegram poller, until stopped."""
    setup_logging("worker")
    setup_tracing("worker")
    holder = worker_id()
    pool = ProcessPoolExecutor(max_workers=2)  # CPU-heavy work (PDF parsing) off the event loop
    cpu.set_executor(pool)
    try:
        async with AsyncExitStack() as stack:
            set_deps(await build_deps(stack))
            checkpointer = await open_checkpointer(stack)
            loop = QueueLoop(holder, make_handlers(holder, checkpointer))
            log.info("worker %s started", holder)
            async with asyncio.TaskGroup() as tg:
                tg.create_task(loop.run_forever())
                tg.create_task(scheduler.run(holder))
                tg.create_task(telegram_poller.run())
    finally:
        cpu.set_executor(None)
        pool.shutdown(cancel_futures=True)
        await dispose_engine()
        shutdown_tracing()


if __name__ == "__main__":
    asyncio.run(main())
    # Cleanup is done. Skip C++ static destructors: onnxruntime
    # (embeddings) can abort in them on macOS.
    # Cleanup is done. Skip C++ static destructors: onnxruntime (embeddings) can abort in them on macOS.
    os._exit(0)
