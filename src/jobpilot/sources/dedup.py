"""Layered de-duplication. A match becomes a new sighting (job_sources) of the same job - never a second card.

1. the board's own job id (from tracking links and API apply links),
2. normalized company + normalized title + city (same city, both unknown, or either remote; never used when
   the company is unknown),
3. near-duplicate JD text (SimHash) at the same company, as a tiebreaker.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection

from jobpilot.db.queries import jobs as jobs_repo
from jobpilot.models.job import JobPosting, PipelineStatus
from jobpilot.reference import normalize

# SimHashes this close (out of 64 bits) count as the same JD.
NEAR_DUP_BITS = 6
# Shorter texts are too generic to fingerprint reliably.
MIN_JD_CHARS_FOR_SIMHASH = 400


@dataclass(frozen=True)
class Normalized:
    """A posting with every field dedup compares, computed once."""

    posting: JobPosting
    board_id: tuple[str, str] | None
    company_norm: str
    title_norm: str
    city: str | None
    remote: bool
    canonical_key: str
    jd_hash: str | None
    simhash: int | None


def normalize_posting(p: JobPosting) -> Normalized:
    """Compute the normalized names, keys and fingerprints of a posting."""
    # Prefer the board id the source gave; else dig it out of the link.
    board_id = (p.board, p.board_job_id) if p.board and p.board_job_id else normalize.board_job_id(p.url)
    city = normalize.city_norm(p.location)
    jd = p.description.strip()
    company = normalize.company_norm(p.company)
    key = normalize.canonical_key(board_id, p.company, p.title, city)
    if not company and board_id is None:  # unknown company: never merge on the title alone
        key = f"src:{p.source.value}:{p.external_id}"
    return Normalized(
        posting=p,
        board_id=board_id,
        company_norm=company,
        title_norm=normalize.title_norm(p.title),
        city=city,
        remote=bool(p.remote) or normalize.is_remote(p.location, p.title),
        canonical_key=key,
        jd_hash=normalize.jd_hash(jd) if jd else None,
        simhash=normalize.simhash(jd) if len(jd) >= MIN_JD_CHARS_FOR_SIMHASH else None,
    )


async def find_existing(conn: AsyncConnection, n: Normalized) -> tuple[RowMapping | None, str]:
    """Return (existing job, layer that matched) or (None, '')."""
    row = await jobs_repo.by_canonical_key(conn, n.canonical_key)
    if row is not None:
        return row, "board_id" if n.board_id else "canonical_key"
    if not n.company_norm:
        return None, ""
    for row in await jobs_repo.by_company_title(conn, n.company_norm, n.title_norm):
        if row["city"] == n.city or row["remote"] or n.remote:  # same city, both unknown, or remote
            return row, "company_title_city"
    if n.simhash is not None:
        for row in await jobs_repo.with_simhash_at_company(conn, n.company_norm):
            if normalize.hamming(row["jd_simhash"], n.simhash) <= NEAR_DUP_BITS:
                return row, "near_duplicate_jd"
    return None, ""


@dataclass(frozen=True)
class Ingested:
    """What happened to one posting: new job or sighting, and which layer matched."""

    job_id: int
    new_job: bool
    new_sighting: bool
    layer: str  # which dedup layer matched ('' for a new job)


async def ingest(conn: AsyncConnection, postings: list[JobPosting], run_id: int | None) -> list[Ingested]:
    """Store postings inside the caller's transaction: a new job, or a new sighting of an existing one."""
    out: list[Ingested] = []
    for p in postings:
        n = normalize_posting(p)
        existing, layer = await find_existing(conn, n)
        # The raw fields as this source saw them, kept with the
        # sighting.
        snippet = {"title": p.title, "company": p.company, "location": p.location, **p.raw}
        if existing is None:
            job_id = await jobs_repo.insert_job_once(
                conn,
                canonical_key=n.canonical_key,
                board=n.board_id[0] if n.board_id else None,
                board_job_id=n.board_id[1] if n.board_id else None,
                company=p.company,
                company_norm=n.company_norm,
                title=p.title,
                title_norm=n.title_norm,
                city=n.city,
                location_raw=p.location,
                remote=n.remote,
                apply_url=p.url,
                jd_text=p.description,
                jd_is_snippet=p.description_is_snippet,
                jd_hash=n.jd_hash,
                jd_simhash=n.simhash,
                experience_text=p.experience_text,
                posted_at=p.posted_at,
            )
            new_sighting = await jobs_repo.add_sighting(
                conn,
                job_id=job_id,
                source=p.source.value,
                external_id=p.external_id,
                url=p.url,
                posted_at=p.posted_at,
                run_id=run_id,
                snippet=snippet,
            )
            out.append(Ingested(job_id, True, new_sighting, ""))
            continue
        new_sighting = await jobs_repo.add_sighting(
            conn,
            job_id=existing["id"],
            source=p.source.value,
            external_id=p.external_id,
            url=p.url,
            posted_at=p.posted_at,
            run_id=run_id,
            snippet=snippet,
        )
        # Only a real JD that replaces a snippet is worth an update.
        if (
            not p.description_is_snippet
            and p.description
            and normalize.material_jd_change(existing["jd_text"], p.description, existing["jd_is_snippet"])
        ):
            # A full JD arrived for a job known only from a snippet (cards/rescore.py re-scores carded jobs).
            values: dict[str, object] = {
                "jd_text": p.description,
                "jd_is_snippet": False,
                "jd_hash": n.jd_hash,
                "jd_simhash": n.simhash,
            }
            if existing["pipeline_status"] == PipelineStatus.CARDED.value:
                values.update(
                    pipeline_status=PipelineStatus.PENDING_EXTRACTION.value,
                    filter_reason="full JD arrived; rescore pending",
                )
            await jobs_repo.update(
                conn,
                existing["id"],
                existing["version"],
                **values,
            )
        out.append(Ingested(existing["id"], False, new_sighting, layer))
    return out
