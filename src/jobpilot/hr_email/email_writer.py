"""The HR email (decisions 36, 38): code writes a professional frame; one call writes 2-3 checked fit lines.

Code writes the subject, greeting, opening ("I have applied for the <role> role at <company> and have attached
my resume"), the closing and the signature with the applicant's contact details - so the email is always
polite, short and correct, and never asks odd questions or states something unbacked. The model writes only
the fit sentences, each resting on exactly one experience or project fact; code keeps a sentence only if it
is a faithful first-person statement of that fact (safety/fidelity.py). A refused sentence falls back to the
fact itself when it reads as a sentence, otherwise it is dropped.

The email always shows a project (code adds the most job-relevant project fact when the model gave none) and
a "Relevant skills" line: your confirmed skills that the job description asks for (no model involved).
"""

from __future__ import annotations

import json
import re

from jobpilot.llm import prompts
from jobpilot.llm.client import LLMClient, LLMResult
from jobpilot.models.email import EmailDraft, EmailPoints
from jobpilot.reference.catalog import display_skill
from jobpilot.safety import claims, fidelity, sanitize

JOB_FIELDS = ("company", "title", "location_raw", "city", "jd_text")
MAX_POINT_WORDS = 75  # the fit sentences together; the frame and skills line add about 55 words (limit 150)
MAX_SKILLS = 8  # skills named on the skills line
# Words an email sentence may add around its fact: first person and the plain frame words.
EMAIL_WORDS = "I my me project role as part"
_PAST = re.compile(r"^(?:[A-Za-z]+ed|built|led|wrote|made|ran|won|taught|began)\b", re.I)


def _title_case(name: str) -> str:
    """THARUN ALLAM -> Tharun Allam; a name already in mixed case is kept."""
    return name.title() if name.isupper() or name.islower() else name


def fallback_sentence(fact: str) -> str | None:
    """A fact that starts with a past-tense verb, as a first-person sentence ('Built X' -> 'I built X')."""
    text = fidelity.clean(re.sub(r"\s*\[[^\]]*\]\s*$", "", fact))  # drop the "[its entry]" suffix
    if not _PAST.match(text):
        return None
    text = "I " + text[0].lower() + text[1:]
    return text if text.endswith(".") else text + "."


def entry_of(fact: str) -> str:
    """The experience or project a detail belongs to: the '[...]' suffix of its fact text."""
    m = re.search(r"\[([^\]]*)\]\s*$", fact)
    return m.group(1).strip() if m else ""


def project_sentence(projects: dict[int, str], job_text: str) -> tuple[int, str] | None:
    """(fact id, sentence) for the project fact that names the most of the job's skills, or None.

    'Built a multi-agent system ... [JobPilot]' -> 'In my JobPilot project, I built a multi-agent system ...'.
    """
    wanted = set(claims.skills_in(job_text))
    ranked = sorted(projects.items(), key=lambda kv: (-len(wanted & set(claims.skills_in(kv[1]))), kv[0]))
    for fact_id, fact in ranked:
        sentence = fallback_sentence(fact)
        if sentence is None or len(fidelity.words(sentence)) > 40:
            continue
        name = entry_of(fact)
        return fact_id, (f"In my {name} project, {sentence}" if name else sentence)
    return None


def skills_line(skills: dict[int, str], job_text: str) -> tuple[str, list[int]]:
    """('Relevant skills: Python, LangGraph and RAG.', their item ids): confirmed skills the job asks for."""
    wanted = set(claims.skills_in(job_text))
    names: dict[str, tuple[str, int]] = {}
    for item_id, text in skills.items():
        sid = next(iter(claims.skills_in(text)), None)
        if sid in wanted and sid not in names:
            names[sid] = (display_skill(text), item_id)
    picked = list(names.values())[:MAX_SKILLS]
    if not picked:
        return "", []
    shown = [n for n, _ in picked]
    listed = shown[0] if len(shown) == 1 else ", ".join(shown[:-1]) + " and " + shown[-1]
    return f"Relevant skills: {listed}.", [i for _, i in picked]


def checked_points(
    points: EmailPoints, sources: dict[int, str], job_text: str = ""
) -> tuple[list[str], list[int], list[str]]:
    """Keep the faithful fit sentences.

    Args:
        points: The model's sentences.
        sources: Experience / project fact id -> fact text (with its entry in brackets).
        job_text: The job description (its names for skills a fact names are allowed).

    Returns:
        (the sentences, the fact ids they cite, why sentences were refused).
    """
    terms = fidelity.job_terms(job_text)
    kept: list[str] = []
    cited: list[int] = []
    refused: list[str] = []
    for point in points.points:
        fact = sources.get(point.fact_id)
        if fact is None or point.fact_id in cited:
            refused.append(f"fact {point.fact_id} is not an experience or project fact (or used twice)")
            continue
        sentence = " ".join(point.sentence.split())
        problem = fidelity.rewrite_problem(sentence, fact, EMAIL_WORDS, min_keep=0.3, terms=terms)
        if problem is None and len(fidelity.words(sentence)) > 45:
            problem = "is too long"
        if problem:
            refused.append(f"fact {point.fact_id}: {problem}")
            sentence = fallback_sentence(fact) or ""
            if not sentence or len(fidelity.words(sentence)) > 45:
                continue
        if sum(len(fidelity.words(s)) for s in [*kept, sentence]) > MAX_POINT_WORDS:
            break
        kept.append(sentence if sentence.endswith((".", "!")) else sentence + ".")
        cited.append(point.fact_id)
    return kept, cited, refused


def frame(
    job: dict, applicant: str, contacts: list[str], points: list[str], roles: list[str], skills: str = ""
) -> tuple[str, str]:
    """(subject, body): the professional email around the fit sentences.

    Args:
        job: The job row (company, title).
        applicant: The applicant's name from the resume header.
        contacts: The applicant's contact lines (phone, email, LinkedIn).
        points: The checked fit sentences.
        roles: The applicant's role headlines, for the no-points fallback line.
        skills: The "Relevant skills: ..." line, or "".
    """
    company = " ".join(str(job.get("company") or "").split())
    title = " ".join(str(job.get("title") or "").split()) or "open"
    name = _title_case(" ".join(applicant.split()))
    subject = f"Application for {title}" + (f" - {name}" if name else "")
    greeting = f"Dear {company} Hiring Team," if company else "Dear Hiring Team,"
    at = f" at {company}" if company else ""
    opening = f"I have applied for the {title} role{at} and have attached my resume for your consideration."
    if points:
        middle = "Relevant experience:\n" + "\n".join(f"- {p}" for p in points)
    elif roles:
        middle = f"My resume covers my work as {roles[0]}."
    else:
        middle = "My resume covers my education, experience and skills."
    if skills:
        middle += "\n\n" + skills
    closing = (
        "I would welcome the chance to discuss how I can contribute to your team. "
        "Thank you for your time and consideration."
    )
    signature = "\n".join(line for line in ["Best regards,", name, " | ".join(contacts)] if line)
    body = "\n\n".join([greeting, opening, middle, closing, signature])
    return subject[:150], body


async def write(
    llm: LLMClient,
    job: dict,
    sources: dict[int, str],
    run_id: int | None = None,
    *,
    feedback: str = "none",
    applicant: str = "",
    contacts: list[str] | None = None,
    roles: dict[int, str] | None = None,
    fit: list[str] | None = None,
    project_ids: list[int] | None = None,
    skills: dict[int, str] | None = None,
) -> tuple[EmailDraft, LLMResult[EmailPoints], list[str]]:
    """Draft the HR email: one call for the fit sentences, code for everything else.

    Args:
        llm: The model client.
        job: The job row (only a few fields are sent).
        sources: Experience / project fact id -> fact text.
        run_id: The run the tokens count against, if any.
        feedback: What the checks refused in a previous draft.
        applicant: The applicant's name for the sign-off.
        contacts: The applicant's contact lines.
        roles: The applicant's role item id -> headline (cited when no sentence survives).
        fit: "Why you fit" lines from the score (help choose the facts).
        project_ids: Which of `sources` are project facts (one must appear in the email).
        skills: Confirmed skill item id -> name (for the "Relevant skills" line).

    Returns:
        (the draft, the model call's result, the refused sentences).
    """
    prompt = prompts.render(
        "hr_email/email_writer",
        experience=sanitize.fence(
            "experience-facts",
            json.dumps(
                {i: t for i, t in sources.items() if i not in set(project_ids or ())}, ensure_ascii=True
            ),
        ),
        projects=sanitize.fence(
            "project-facts",
            json.dumps({i: t for i, t in sources.items() if i in set(project_ids or ())}, ensure_ascii=True),
        ),
        job=sanitize.fence("job", json.dumps({k: job.get(k) for k in JOB_FIELDS}, default=str)),
        fit="\n".join(f"- {line}" for line in fit or []) or "not computed",
        feedback=feedback,
    )
    result = await llm.structured("email_writer", prompt, EmailPoints, run_id=run_id, max_tokens=800)
    job_text = f"{job.get('title') or ''}\n{job.get('jd_text') or ''}"  # the title names skills too
    points, cited, refused = checked_points(result.output, sources, job_text)
    projects = {i: t for i, t in sources.items() if i in set(project_ids or ())}
    if projects and not set(cited) & set(projects) and (added := project_sentence(projects, job_text)):
        while points and sum(len(fidelity.words(p)) for p in [*points, added[1]]) > MAX_POINT_WORDS:
            points.pop()
            cited.pop()
        points.append(added[1])
        cited.append(added[0])
        refused.append("no project sentence was given; added the most relevant project fact")
    line, skill_cites = skills_line(skills or {}, job_text)
    roles = roles or {}
    subject, body = frame(job, applicant, list(contacts or []), points, list(roles.values()), line)
    if not cited and roles:
        cited = [next(iter(roles))]
    return EmailDraft(subject=subject, body=body, citations=[*cited, *skill_cites]), result, refused
