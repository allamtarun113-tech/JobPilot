"""Apply the composer's plan (resume v3, decision 36): code builds the tailored resume from the pool.

The plan only picks, orders and rewords; code decides what lands:

- Experience: every role stays, in the base order; every confirmed detail stays as its own bullet (bullets
  the plan forgets are added back in their original order). Nothing is merged or moved between entries.
- Projects: the plan's order; projects it leaves out follow, in their own order. A project from the
  profile that isn't on the base resume is added.
- A reworded bullet replaces its original only if it is a faithful rewording of that one fact
  (safety/fidelity.py) and doesn't repeat another line; otherwise the original wording stays. It may use
  the job's own name for a skill the fact already names ("Gen AI" -> the job's "Generative AI").
- The summary is used only if it passes the summary checks; otherwise the base summary stays.
- Skills (decision 41): only your confirmed skills that the job's requirements or description ask for
  (every skill when fewer than three match), written properly, on tidy lines (an unplaced skill joins a line
  of its kind; a skill that only repeats a label is dropped); the job's required skills come first.
- Code then polishes every line (resume/polish.py): spelling, capitals, full stops, dates, titles.
- Nothing is ever removed (decision 39): every role, project, detail, skill and activity stays; the plan only
  orders and rewords, and the profile adds. Education, Activities and the header are never changed.
- A bullet stays within two lines (resume.max_bullet_chars); a longer fact is split by sentence in the pool.

The result is a ResumeDoc rendered by the one fixed template: the model never writes LaTeX.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from jobpilot.models.resume_doc import Bullet, Entry, Layout, ResumeDoc
from jobpilot.models.resume_plan import PlannedEntry, ResumePlan
from jobpilot.reference import catalog
from jobpilot.resume import polish
from jobpilot.resume.pool import Pool, PoolEntry, skill_key
from jobpilot.resume.render import UnsupportedCharacter, latex_escape
from jobpilot.safety import claims, fidelity

MIN_PROJECTS = 2
# Lines for confirmed skills the plan didn't place, by taxonomy category.
GROUPS = {
    "ml": "AI and LLM Tools",
    "backend": "Backend and Databases",
    "database": "Backend and Databases",
    "cloud": "Cloud, DevOps and Tools",
    "devops": "Cloud, DevOps and Tools",
    "tool": "Cloud, DevOps and Tools",
    "testing": "Cloud, DevOps and Tools",
}
OTHER = "Additional Skills"
# Words that mark a base label as the same group (a "Databases" line takes database skills).
GROUP_WORDS = {
    "AI and LLM Tools": ("ai", "llm", "llms", "artificial intelligence", "machine learning", "genai"),
    "Backend and Databases": ("backend", "database", "databases"),
    "Cloud, DevOps and Tools": ("cloud", "devops", "tools"),
}
MIN_JOB_SKILLS = 3  # with fewer job skills than this, every skill is shown rather than a near-empty section
MAX_NEW_SKILL_LINES = 2
_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 &/+.,-]{0,38}$")


@dataclass
class Composed:
    """The tailored resume and what changed from the base."""

    doc: ResumeDoc
    applied: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    terms: dict[str, str] = field(default_factory=dict)  # the job's skill names (fidelity.job_terms)
    max_chars: int = 210  # the two-line bullet limit
    required: frozenset[str] = frozenset()  # the job's required skills: taxonomy ids and lower-cased names
    nice: frozenset[str] = frozenset()  # the job's nice-to-have skills, the same way
    refused_bullets: list[dict[str, str]] = field(default_factory=list)  # for the second try (decision 41)
    categories: frozenset[str] = frozenset()  # taxonomy categories broad requirements ask for ("AI/ML" -> ml)

    def rank(self, skill: str) -> int:
        """0 required by the job, 1 nice to have, 2 named in the job description, 3 other."""
        keys = {skill.lower(), skill_key(skill)} | ({sid} if (sid := catalog.skill_id(skill)) else set())
        if keys & self.required:
            return 0
        if keys & self.nice:
            return 1
        if keys & set(self.terms):
            return 2
        found = catalog.get(sid) if sid else None
        return 2 if found is not None and found.category in self.categories else 3

    def relevance(self, text: str) -> int:
        """How much a text speaks to this job: 3 per required skill, 2 per nice-to-have, 1 per related one."""
        return sum(3 - r for sid in claims.skills_in(text) if (r := self.rank(sid)) <= 2)


def printable(text: str) -> bool:
    """True if the fixed template can typeset every character (the fonts have no CJK, emoji...)."""
    try:
        latex_escape(text)
    except UnsupportedCharacter:
        return False
    return True


def _same(a: str, b: str) -> bool:
    """True if two lines say nearly the same words."""
    return SequenceMatcher(None, " ".join(fidelity.words(a)), " ".join(fidelity.words(b))).ratio() > 0.85


def _bullets(entry: PoolEntry, planned: PlannedEntry | None, section: str, out: Composed) -> list[Bullet]:
    """The entry's bullets in the planned order, reworded only where the rewording is faithful."""
    by_id = {b.id: b for b in entry.bullets}
    order: list[tuple[str, str]] = []
    for pb in planned.bullets if planned else []:
        if pb.id not in by_id:
            out.rejected.append(f"{entry.title}: bullet {pb.id} isn't part of this entry - not moved here")
        elif pb.id not in {i for i, _ in order}:
            order.append((pb.id, pb.text))
    order += [(b.id, "") for b in entry.bullets if b.id not in {i for i, _ in order}]
    result: list[Bullet] = []
    for bullet_id, wording in order:
        src = by_id[bullet_id]
        text, wording = src.text, " ".join(wording.split())
        if wording and wording != src.text:
            long_source = len(src.text) > out.max_chars  # shortening it may drop more of its words
            problem = fidelity.rewrite_problem(
                wording, src.text, entry.context, terms=out.terms, min_keep=0.4 if long_source else 0.6
            )
            if problem is None and len(wording) > out.max_chars:
                problem = "runs past two lines"
            others = [b.text for b in entry.bullets if b.id != bullet_id] + [b.text for b in result]
            if problem is None and any(_same(wording, o) for o in others):
                problem = "repeats another line"
            if problem is None and not printable(wording):
                problem = "has a character the resume fonts can't print"
            if problem:
                out.rejected.append(f"{entry.title}: kept the original wording of {bullet_id} ({problem})")
                out.refused_bullets.append(
                    {"id": bullet_id, "original": src.text, "refused": wording, "reason": problem}
                )
            else:
                text = wording
                out.applied.append(
                    {
                        "op": "rewrite_bullet",
                        "id": bullet_id,
                        "section": section,
                        "reason": f"reworded for this job ({entry.title})",
                        "evidence": src.evidence,
                        "before": src.text,
                        "after": text,
                    }
                )
        result.append(Bullet(id=src.id, text=text, sub=src.sub, evidence=list(src.evidence)))
    if [b.id for b in result] != [b.id for b in entry.bullets]:
        out.applied.append(
            {
                "op": "reorder",
                "id": entry.id,
                "section": section,
                "reason": f"most relevant points first ({entry.title})",
            }
        )
    return result


def _text(e: PoolEntry) -> str:
    """Everything an entry says (title, note, tools, bullets), for relevance."""
    return "\n".join([e.context, *(b.text for b in e.bullets)])


def _entry(e: PoolEntry, bullets: list[Bullet]) -> Entry:
    """A resume entry from a pool entry."""
    return Entry(id=e.id, title=e.title, date=e.date, note=e.note, bullets=bullets, evidence=list(e.evidence))


def _projects(pool: Pool, plan: ResumePlan, section: str, out: Composed) -> list[Entry]:
    """Every project, the planned ones first in the planned order, each with all its bullets."""
    chosen: list[PoolEntry] = []
    planned = {p.id: p for p in plan.projects}
    for p in plan.projects:
        e = pool.entry(p.id)
        if e is not None and e.kind == "project" and e not in chosen:
            chosen.append(e)
        elif e is None or e.kind != "project":
            out.rejected.append(f"Projects: '{p.id}' is not one of your projects")
    ranked = len(chosen)
    rest = [e for e in pool.projects if e not in chosen]  # never dropped: the most job-relevant first
    chosen += sorted(rest, key=lambda e: -out.relevance(_text(e)))
    entries = []
    for e in chosen:
        if not e.on_base:
            out.applied.append(
                {
                    "op": "add_entry",
                    "id": e.id,
                    "section": section,
                    "reason": f"added project {e.title} from your profile",
                    "after": e.title,
                }
            )
        entries.append(_entry(e, _bullets(e, planned.get(e.id), section, out)))
    if [e.id for e in chosen[:ranked]] != [e.id for e in pool.projects if e in chosen[:ranked]]:
        out.applied.append(
            {"op": "reorder", "id": "projects", "section": section, "reason": "most relevant projects first"}
        )
    return entries


def _roles(pool: Pool, plan: ResumePlan, section: str, out: Composed) -> list[Entry]:
    """Every role, in the base order, each with all its bullets."""
    planned = {p.id: p for p in plan.experience}
    entries = []
    for e in pool.roles:
        if not e.on_base:
            out.applied.append(
                {
                    "op": "add_entry",
                    "id": e.id,
                    "section": section,
                    "reason": f"added {e.title} from your profile",
                    "after": e.title,
                }
            )
        if any(b.id.startswith("p") for b in e.bullets) and e.on_base:
            out.applied.append(
                {
                    "op": "add_details",
                    "id": e.id,
                    "section": section,
                    "reason": f"added your confirmed details for {e.title}",
                }
            )
        entries.append(_entry(e, _bullets(e, planned.get(e.id), section, out)))
    return entries


# Label words that suit a skill of each taxonomy category ("Python" belongs under "Programming Languages").
LABEL_FIT = {
    "language": ("language", "languages", "programming"),
    "ml": ("ai", "artificial intelligence", "machine learning", "llm"),
    "data": ("data", "libraries", "analytics"),
    "database": ("database", "databases"),
    "backend": ("backend", "web"),
    "frontend": ("frontend", "web"),
    "devops": ("devops", "cloud", "tools"),
    "cloud": ("cloud",),
    "testing": ("testing", "tools"),
    "tool": ("tools",),
    "concept": ("concepts",),
}


def _fits(skill: str, label: str) -> bool:
    """True if a skills label suits the skill's kind (taxonomy category)."""
    found = catalog.get(catalog.skill_id(skill) or "")
    words = LABEL_FIT.get(found.category, ()) if found else ()
    return any(re.search(rf"\b{re.escape(w)}\b", label.lower()) for w in words)


def _same_group(label: str, group: str) -> bool:
    """True if a skills line's label is the group's own label or names it ('Databases' for databases)."""
    lowered = label.lower()
    if lowered == group.lower():
        return True
    return any(re.search(rf"\b{re.escape(w)}\b", lowered) for w in GROUP_WORDS.get(group, ()))


def _skills(
    pool: Pool, plan: ResumePlan, base_entries: list[Entry], section: str, out: Composed
) -> list[Entry]:
    """Skills lines with confirmed skills only, each once; base lines keep all their skills."""
    allowed = {skill_key(s): s for _label, items in pool.skill_lines for s in items}
    for s in pool.extra_skills:
        allowed.setdefault(skill_key(s), s)
    base_by_label = {(e.title or "").lower(): e for e in base_entries}
    used: set[str] = set()
    lines: list[Entry] = []
    new_labels = 0
    for line in plan.skills:
        label = " ".join(line.label.split())
        base = base_by_label.get(label.lower())
        if base is None:
            if not _LABEL.match(label) or new_labels >= MAX_NEW_SKILL_LINES:
                out.rejected.append(f"Skills: new line '{label[:40]}' not added")
                continue
            new_labels += 1
        if base is not None and any(e.id == base.id for e in lines):
            continue
        items: list[str] = []
        for raw in line.items:
            key = skill_key(raw)
            if key not in allowed:
                out.rejected.append(f"Skills: '{raw[:40]}' is not a confirmed skill")
            elif key not in used:
                used.add(key)
                items.append(allowed[key])
        if base is not None:  # a base line never loses a skill, even one another line also lists
            for s in base.items:
                if skill_key(s) not in {skill_key(i) for i in items}:
                    used.add(skill_key(s))
                    items.append(s)
        if items:
            entry_id = base.id if base is not None else f"new-skills-{len(lines) + 1}"
            evidence = list(base.evidence) if base is not None else []
            lines.append(
                Entry(id=entry_id, title=base.title if base else label, items=items, evidence=evidence)
            )
    for base in base_entries:  # a base line the plan left out goes last, whole
        if not any(e.id == base.id for e in lines):
            used.update(skill_key(s) for s in base.items)
            lines.append(
                Entry(id=base.id, title=base.title, items=list(base.items), evidence=list(base.evidence))
            )
    rest = [s for s in pool.extra_skills if skill_key(s) not in used]  # every confirmed skill is shown
    groups: dict[str, list[str]] = {}
    for skill in rest:  # what the plan didn't place goes on lines by taxonomy category
        found = catalog.get(catalog.skill_id(skill) or "")
        groups.setdefault(GROUPS.get(found.category, OTHER) if found else OTHER, []).append(skill)
    for label in [*dict.fromkeys(GROUPS.values()), OTHER]:
        if groups.get(label):
            used.update(skill_key(s) for s in groups[label])
            existing = next((e for e in lines if _same_group(e.title or "", label)), None)
            if existing is not None:  # "Vector Database" joins an existing "Databases" line
                existing.items += [
                    s for s in groups[label] if skill_key(s) not in map(skill_key, existing.items)
                ]
            else:
                lines.append(Entry(id=f"new-skills-{len(lines) + 1}", title=label, items=groups[label]))
    labels = {skill_key(e.title or "") for e in lines}
    for e in lines:  # a skill that only repeats a label ("Artificial Intelligence") says nothing new
        e.items = [
            i for i in e.items if skill_key(i) not in labels or skill_key(i) == skill_key(e.title or "")
        ]
    asked = [i for e in lines for i in e.items if out.rank(i) <= 2]
    if out.terms and len(asked) >= MIN_JOB_SKILLS:  # only the skills this job asks for (decision 41)
        for e in lines:
            e.items = [i for i in e.items if out.rank(i) <= 2]
        lines = [e for e in lines if e.items]
        out.applied.append({"op": "job_skills", "section": section,
                            "reason": f"kept the {len(asked)} skills this job asks for"})  # fmt: skip
    else:
        lines = [e for e in lines if e.items]
    for e in lines:  # the job's required skills first, then its nice-to-have skills, then the rest
        e.items = sorted(e.items, key=lambda item: out.rank(item))
    order = {id(e): i for i, e in enumerate(lines)}
    lines.sort(
        key=lambda e: (
            min(out.rank(i) for i in e.items),
            -sum(out.rank(i) == 0 for i in e.items),
            order[id(e)],
        )
    )
    if any(a.get("op") == "job_skills" for a in out.applied):  # a short, job-only section lists a skill once
        home: dict[str, int] = {}  # skill -> the line it stays on: one of its kind, else the first
        for idx, e in enumerate(lines):
            for item in e.items:
                key = skill_key(item)
                if key not in home or (
                    _fits(item, e.title or "") and not _fits(item, lines[home[key]].title or "")
                ):
                    home[key] = idx
        for idx, e in enumerate(lines):
            e.items = [i for i in e.items if home[skill_key(i)] == idx]
        lines = [e for e in lines if e.items]
    shown = {skill_key(i) for e in lines for i in e.items}
    added = [k for k in shown if k not in {skill_key(x) for _l, items in pool.skill_lines for x in items}]
    if added:
        out.applied.append(
            {
                "op": "add_confirmed_skills",
                "section": section,
                "reason": "added confirmed skills: " + ", ".join(allowed[k] for k in sorted(added)),
            }
        )
    return lines


_MADE = re.compile(r"^(?:Developed|Built|Created|Designed)\s+(an?\s+.+)$", re.I)


def objective_from_facts(pool: Pool, out: Composed) -> str:
    """A 2-3 sentence objective made only of profile facts and the job's skills you have.

    'Delivery Data Scientist at Turing with hands-on experience in LLMs, RAG and LangGraph. Built JobPilot,
    an AI assistant that ... B.Tech graduate from Indian Institute of Technology, Palakkad.'
    """
    parts: list[str] = []
    skills = [s for _label, items in pool.skill_lines for s in items] + pool.extra_skills
    wanted = list(
        dict.fromkeys(catalog.display_skill(s) for s in sorted(skills, key=out.rank) if out.rank(s) <= 2)
    )[:5]
    if pool.role_heads and pool.role_heads[0][0]:
        title, org = pool.role_heads[0]
        head = f"{title.rstrip('. ')} at {org}" if org else title.rstrip(". ")
        if wanted:
            listed = wanted[0] if len(wanted) == 1 else ", ".join(wanted[:-1]) + " and " + wanted[-1]
            head += f" with hands-on experience in {listed}"
        parts.append(head + ".")
    texts = {e.title: _text(e) for e in pool.projects}
    ranked = sorted(pool.project_abouts, key=lambda na: -out.relevance(texts.get(na[0], na[1])))
    for name, about in ranked:  # the most job-relevant project that reads as "built X"
        m = _MADE.match(about)
        if m and name:
            parts.append(f"Built {name}, {m.group(1)[0].lower()}{m.group(1)[1:].rstrip('.')}.")
            break
    for qualification, institution in pool.degrees[:1]:
        parts.append(f"{qualification} graduate from {institution}.")
    return " ".join(parts)


# Words in a broad requirement ("AI/ML technologies") and the taxonomy category they ask for.
CATEGORY_WORDS = {
    "ml": ("ai", "ml", "machine learning", "artificial intelligence", "genai", "generative", "llm", "llms",
           "nlp", "deep learning", "data science"),
    "database": ("database", "databases", "sql", "nosql"),
    "cloud": ("cloud", "aws", "azure", "gcp"),
    "devops": ("devops", "ci/cd", "cicd", "deployment", "containers"),
    "backend": ("backend", "back-end", "api", "apis", "microservices"),
    "frontend": ("frontend", "front-end", "ui"),
    "testing": ("testing", "qa", "test automation"),
}  # fmt: skip


def categories_of(names: list[str]) -> frozenset[str]:
    """Taxonomy categories that broad requirements (ones that are not a single skill) ask for."""
    found: set[str] = set()
    for name in names:
        if catalog.skill_id(name):
            continue
        lowered = name.lower()
        for category, words in CATEGORY_WORDS.items():
            if any(re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", lowered) for w in words):
                found.add(category)
    return frozenset(found)


def skill_keys(names: list[str]) -> frozenset[str]:
    """Taxonomy ids and lower-cased names of the job's skills (for `Composed.rank`)."""
    keys = {n.lower().strip() for n in names if n.strip()}
    keys |= {sid for n in names if (sid := catalog.skill_id(n))}
    return frozenset(keys)


def compose(
    base: ResumeDoc,
    pool: Pool,
    plan: ResumePlan,
    job_text: str = "",
    required: list[str] | None = None,
    nice: list[str] | None = None,
) -> Composed:
    """Build the tailored resume from the base resume, the pool and the plan.

    Args:
        base: The base resume (layout, header, education, activities).
        pool: Every confirmed entry, detail and skill.
        plan: The composer's choices.
        job_text: The job description: a bullet may use its name for a skill the bullet already names.
        required: The job's required skills (shown first on the skills lines).
        nice: The job's nice-to-have skills (shown next).

    Returns:
        The tailored resume, the changes and the refused proposals.
    """
    doc = base.model_copy(deep=True)
    doc.composed = True
    out = Composed(
        doc,
        terms=fidelity.job_terms(job_text),
        max_chars=pool.max_chars,
        required=skill_keys(list(required or [])),
        nice=skill_keys(list(nice or [])),
        categories=categories_of([*(required or []), *(nice or [])]),
    )
    have_projects = any(s.layout == Layout.PROJECTS for s in doc.sections)
    for section in doc.sections:
        if section.layout == Layout.PARAGRAPH and section.entries:
            text, problem = " ".join(plan.summary.split()), None
            if text:
                problem = fidelity.summary_problem(text, pool.facts, pool.summary, pool.role_titles)
                if problem is None and not printable(text):
                    problem = "has a character the resume fonts can't print"
                if problem:
                    out.rejected.append(f"{section.title}: the written objective was refused ({problem})")
            reason = "objective aimed at this job"
            if not text or problem:  # write a true one from the profile instead of keeping the generic one
                text, reason = objective_from_facts(pool, out), "objective written from your profile facts"
                if text and (fidelity.summary_problem(text, pool.facts, pool.summary, pool.role_titles)
                             or not printable(text)):  # fmt: skip
                    text = ""
            if text and text != pool.summary:
                out.applied.append(
                    {"op": "summary", "section": section.title, "reason": reason,
                     "before": pool.summary, "after": text}
                )  # fmt: skip
                section.entries[0].text = text
        elif section.layout == Layout.PROJECTS:
            section.entries = _projects(pool, plan, section.title, out)
        elif section.layout == Layout.ROLES:
            section.entries = _roles(pool, plan, section.title, out)
        elif section.layout == Layout.SKILLS:
            section.entries = _skills(pool, plan, section.entries, section.title, out)
    if not have_projects and pool.projects:
        out.rejected.append("Projects: the base resume has no Projects section to add projects to")
    if fixes := polish.polish(doc, pool.known):  # spelling, capitals, full stops, dates, titles
        out.applied.append(
            {"op": "polish", "section": "all", "reason": "fixed spelling: " + ", ".join(fixes[:8])}
        )
    texts = {b.id: b.text for _s, _e, b in doc.elements() if b}
    summary = next((e.text for s in doc.sections if s.layout == Layout.PARAGRAPH for e in s.entries), None)
    for a in out.applied:  # what the PDF will show, for the verifier's "did it land" check
        if a.get("op") == "rewrite_bullet" and a.get("id") in texts:
            a["after"] = texts[a["id"]]
        elif a.get("op") == "summary" and summary:
            a["after"] = summary
    return out
