"""The match score, computed by code from the extracted Requirements and the confirmed profile (rubric v3).

Same JD + same profile = same score; a model upgrade can't silently move every score.
Dimensions and weights: config/scoring.yaml - technologies 30, skills 20, concepts 10, domain 10,
projects 20, nice-to-have 10. Experience and role direction are not scored (decisions 29, 30).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from jobpilot.core.config import load_yaml
from jobpilot.integrations.vectors import VectorStore
from jobpilot.llm.embeddings import Embedder, cosine, scale
from jobpilot.matching import gap as gap_mod
from jobpilot.matching.embed_score import best_context_match
from jobpilot.matching.profile_view import ProfileView
from jobpilot.models.requirements import Requirements
from jobpilot.models.score import Fit, ScoreResult, SkillMatch
from jobpilot.reference import catalog
from jobpilot.safety import claims

LISTED = ("technologies", "skills", "concepts")  # required items, matched one by one
DIMENSIONS = (*LISTED, "domain", "projects", "nice")
# Abilities and concepts are free phrases ("model deployment"), so they also earn partial credit by meaning.
# Technologies are named tools: knowing TensorFlow is not knowing PyTorch, so only a real match counts.
PARTIAL = ("skills", "concepts")


@lru_cache
def rubric() -> dict[str, Any]:
    """Return the scoring rubric from ``config/scoring.yaml`` (read once)."""
    return load_yaml("scoring.yaml")


def weights() -> dict[str, float]:
    """Return each dimension's weight from the rubric."""
    dims = rubric()["dimensions"]
    return {name: float(dims[name]["weight"]) for name in DIMENSIONS}


@dataclass
class SkillMatcher:
    """Matches JD items against the profile; caches profile vectors per score."""

    view: ProfileView
    embedder: Embedder
    _profile_vecs: dict[int, list[float]] = field(default_factory=dict)
    _context_vecs: dict[int, list[float]] = field(default_factory=dict)

    async def _vectors(self) -> dict[int, list[float]]:
        """Return (embedding once) the vectors of the user's skill items."""
        if not self._profile_vecs and self.view.skill_texts:
            ids = list(self.view.skill_texts)
            vecs = await self.embedder.embed([self.view.skill_texts[i] for i in ids])
            self._profile_vecs = dict(zip(ids, vecs, strict=True))
        return self._profile_vecs

    async def _context_vectors(self) -> dict[int, list[float]]:
        """Return (embedding once) the vectors of projects, roles and achievements."""
        if not self._context_vecs and self.view.context_items:
            ids = list(self.view.context_items)
            vecs = await self.embedder.embed([self.view.context_items[i] for i in ids])
            self._context_vecs = dict(zip(ids, vecs, strict=True))
        return self._context_vecs

    async def closest(self, phrase: str) -> tuple[float, int | None]:
        """The profile item (a skill, project, role or achievement) closest in meaning to `phrase`."""
        candidates = {**await self._vectors(), **await self._context_vectors()}
        if not candidates:
            return 0.0, None
        (vec,) = await self.embedder.embed([phrase])
        best_id, best = max(((i, cosine(vec, v)) for i, v in candidates.items()), key=lambda x: x[1])
        return best, best_id

    def in_experience(self, skill: str, sid: str | None) -> list[int]:
        """Find project, role or achievement items that name this skill.

        Matches by taxonomy id, or the JD's exact phrase as whole words:
        your confirmed experience shows the skill even though it isn't a
        listed skill.

        Args:
            skill: The skill as the JD names it.
            sid: Its taxonomy id, if known.

        Returns:
            The ids of the items that mention it.
        """
        phrase = " ".join(skill.lower().split())
        # Two-letter phrases ("go", "ai") match too many ordinary words.
        if len(phrase) < 3:
            return []
        pattern = re.compile(rf"(?<![\w+#.]){re.escape(phrase)}(?![\w+#])")
        return [
            item_id
            for item_id, text in self.view.context_items.items()
            if (sid and sid in claims.skills_in(text)) or pattern.search(text.lower())
        ]

    async def match(self, skill: str, kind: str) -> SkillMatch | None:
        """Find the profile evidence for one JD item, strongest match first.

        Tries, in order: the taxonomy id, the exact name, a mention in
        your
        experience, then embedding similarity to a listed skill.

        Args:
            skill: The item as the JD names it.
            kind: technology, skill, concept or nice.

        Returns:
            The match with its evidence ids and how it matched, or None.
        """
        sid = catalog.skill_id(skill)
        if sid and sid in self.view.skill_ids:
            return SkillMatch(
                skill=skill, skill_id=sid, kind=kind, evidence=self.view.skill_ids[sid], how="id"
            )
        if (ids := self.view.skill_names.get(skill.lower().strip())) is not None:
            return SkillMatch(skill=skill, skill_id=sid, kind=kind, evidence=ids, how="name")
        if ids := self.in_experience(skill, sid):
            return SkillMatch(skill=skill, skill_id=sid, kind=kind, evidence=ids, how="experience")
        vectors = await self._vectors()
        if vectors:
            (vec,) = await self.embedder.embed([skill])
            best_id, best = max(((i, cosine(vec, v)) for i, v in vectors.items()), key=lambda x: x[1])
            if best >= rubric()["calibration"]["skill_similar_min"]:
                return SkillMatch(skill=skill, skill_id=sid, kind=kind, evidence=[best_id], how="similar")
        return None


def required_lists(req: Requirements) -> dict[str, list[str]]:
    """Return the required items per dimension, each counted once.

    The first list an item appears in wins. A rubric v1 extraction only
    has must_skills: those count as technologies.

    Args:
        req: The extracted requirements.

    Returns:
        Dimension -> items, including ``nice``.
    """
    lists = {"technologies": req.technologies, "skills": req.skills, "concepts": req.concepts}
    if not any(lists.values()) and req.must_skills:
        lists = {"technologies": req.must_skills, "skills": [], "concepts": []}
    seen: set[str] = set()
    out: dict[str, list[str]] = {}
    for dim, items in lists.items():
        out[dim] = []
        for item in items:
            # Compare by taxonomy id when known, so "JS" and
            # "JavaScript" are
            # one item.
            key = catalog.skill_id(item) or " ".join(item.lower().split())
            if item.strip() and key not in seen:
                seen.add(key)
                out[dim].append(item.strip())
    nice = []
    for item in req.nice_skills:
        key = catalog.skill_id(item) or " ".join(item.lower().split())
        if item.strip() and key not in seen:
            seen.add(key)
            nice.append(item.strip())
    out["nice"] = nice
    return out


def effective_weights(lists: dict[str, list[str]], w: dict[str, float]) -> dict[str, float]:
    """Pass the weight of required dimensions the JD doesn't use to those it uses.

    Args:
        lists: The required items per dimension.
        w: The rubric weights.

    Returns:
        The weights to score this JD with.
    """
    present = [d for d in LISTED if lists[d]]
    if not present:
        return dict(w)
    pool = sum(w[d] for d in LISTED)
    share = pool / sum(w[d] for d in present)
    return {**w, **{d: (w[d] * share if d in present else 0.0) for d in LISTED}}


async def compute(
    req: Requirements,
    view: ProfileView,
    *,
    embedder: Embedder,
    vectors: VectorStore,
    matcher: SkillMatcher | None = None,
    jd_is_snippet: bool = False,
) -> ScoreResult:
    """Compute the match score, its parts, the gap and the reasons.

    Same JD and same profile always give the same score.

    Args:
        req: The extracted requirements.
        view: The confirmed profile.
        embedder: Embeds phrases for similarity.
        vectors: Qdrant, which holds the profile's vectors.
        matcher: A matcher to reuse (its vectors are cached).
        jd_is_snippet: A snippet rarely lists skills, so an empty list
            there earns half (not full) points - otherwise snippet-only
            jobs would clear the bar too easily.

    Returns:
        The score result, with the fit explanation filled in.
    """
    w = weights()
    empty_share = 0.5 if jd_is_snippet else 1.0
    matcher = matcher or SkillMatcher(view, embedder)
    lists = required_lists(req)
    # A snippet says little, so a dimension it doesn't mention earns half its own weight instead of handing
    # it over - otherwise one vague phrase in a snippet could carry 60 points.
    eff = dict(w) if jd_is_snippet else effective_weights(lists, w)
    kinds = gap_mod.KIND
    cal = rubric()["calibration"]
    matched: list[SkillMatch] = []
    missing: dict[str, list[str]] = {d: [] for d in (*LISTED, "nice")}
    earned: dict[str, float] = {d: 0.0 for d in (*LISTED, "nice")}  # items' worth of credit per dimension
    gaps: list[tuple[str, str, float]] = []  # (dimension, item, points still missing)
    for dim in (*LISTED, "nice"):
        # Each required item is worth an equal share of its dimension.
        item_worth = eff[dim] / len(lists[dim]) if lists[dim] else 0.0
        for item in lists[dim]:
            m = await matcher.match(item, kinds[dim])
            credit = 1.0 if m else 0.0
            if m is None and dim in PARTIAL:  # an ability or concept: partial credit by meaning
                sim, best_id = await matcher.closest(item)
                credit = scale(sim, cal["phrase_low"], cal["phrase_high"])
                if credit >= 0.5 and best_id is not None:
                    m = SkillMatch(skill=item, skill_id=catalog.skill_id(item), kind=kinds[dim],
                                   evidence=[best_id], how="similar", credit=round(credit, 2))  # fmt: skip
            if m is not None:
                matched.append(m)
            if credit < 1.0:
                missing[dim].append(item)
                gaps.append((dim, item, round(item_worth * (1 - credit), 2)))
            earned[dim] += credit
    parts: dict[str, float] = {}
    any_listed = any(lists[d] for d in LISTED)
    for dim in (*LISTED, "nice"):
        if lists[dim]:
            parts[dim] = eff[dim] * earned[dim] / len(lists[dim])
        elif dim == "nice" or not any_listed or jd_is_snippet:
            parts[dim] = w[dim] * empty_share
        else:
            parts[dim] = 0.0  # its weight went to the dimensions the JD uses
    # Domain: how close the JD's domain is to your closest project/role.
    sim, closest = await best_context_match(req.domain, embedder, vectors)
    parts["domain"] = w["domain"] * scale(sim, cal["domain_low"], cal["domain_high"])
    # Projects: how close the kinds of things built are to your work;
    # the title stands in when the JD names none.
    built = req.project_types or [req.title_norm]
    project_sims = [await best_context_match(p, embedder, vectors) for p in built if p.strip()]
    best_project = max(project_sims, key=lambda x: x[0]) if project_sims else (0.0, None)
    project_frac = (
        sum(scale(s, cal["projects_low"], cal["projects_high"]) for s, _ in project_sims) / len(project_sims)
        if project_sims
        else 0.0
    )
    parts["projects"] = w["projects"] * project_frac
    parts = {d: round(parts[d], 2) for d in DIMENSIONS}

    def share(dim: str) -> str:
        """Return earned credit over items for a dimension, e.g. "2.5/4"."""
        return f"{earned[dim]:.1f}/{len(lists[dim])}".replace(".0/", "/")

    reasons = [
        *(f"has {share(d)} required {d}" for d in LISTED if lists[d]),
        *([] if any_listed else ["JD lists no required technologies, skills or concepts"]),
        f"domain closest to: {closest[:60]}" if closest else "no project to compare the domain with",
        f"built something like it: {best_project[1][:60]}"
        if best_project[1] and project_frac >= 0.5
        else "no close project yet",
    ]
    result = ScoreResult(
        score=round(sum(parts.values()), 1),
        parts=parts,
        matched=matched,
        gap=gap_mod.gap(gaps),
        reasons=reasons,
        domain_closest=closest,
    )
    result.fit = explain(result, view)
    return result


_HOW = {"id": "in your skills", "name": "in your skills", "similar": "close to your skill"}


def explain(result: ScoreResult, view: ProfileView) -> Fit:
    """Explain the score in words: why you fit and what's missing.

    Why you fit lists the strongest matches, each with where your
    profile shows it; what's missing is the top of the ranked gap. Code
    only, so the card, the dashboard and the email all say the same
    thing.

    Args:
        result: The computed score.
        view: The confirmed profile.

    Returns:
        The fit explanation.
    """
    order = {"technology": 0, "skill": 1, "concept": 2, "nice": 3}
    why: list[str] = []
    # Technologies before skills before concepts; within each, matches
    # shown in your experience first.
    for m in sorted(result.matched, key=lambda m: (order.get(m.kind, 9), m.how != "experience")):
        if len(why) >= 4:
            break
        if m.how == "experience" and m.evidence and (text := view.context_items.get(m.evidence[0])):
            why.append(f"{m.skill}: used in {_short(text)}")
        else:
            why.append(
                f"{m.skill}: {_HOW.get(m.how, 'in your profile')}"
                + (f" ({m.credit:.0%})" if m.credit < 1 else "")
            )
    if result.domain_closest and result.parts.get("domain", 0) >= 0.5 * weights()["domain"]:
        why.append(f"Similar work: {_short(result.domain_closest)}")
    missing = [f"{g.skill} (+{g.points:g}{', quick to learn' if g.easy else ''})" for g in result.gap[:3]]
    full = [m.skill for m in result.matched if m.credit >= 1.0]
    strong = ", ".join(full[:3])
    lacking = [g.skill for g in result.gap if g.skill not in full][:2]
    summary = (f"Strong on {strong}" if strong else "Few direct matches") + (
        f"; missing {', '.join(lacking)}" if lacking else "; nothing missing"
    )
    return Fit(summary=summary[:200], why=why, missing=missing)


def _short(text: str, limit: int = 70) -> str:
    """Shorten text to ``limit`` characters at a word boundary, adding "..."."""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "..."
