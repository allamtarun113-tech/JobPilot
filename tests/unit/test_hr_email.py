"""HR email: address preference and the outreach checks."""

from jobpilot.hr_email.address_finder import find
from jobpilot.safety.email_checks import check


def test_address_finder_prefers_generic_hiring_inbox() -> None:
    """A careers@ inbox beats a named person, who isn't used by default."""
    addresses = find(
        "Email jane.doe@example.com or careers@example.com about this role.",
        "job description",
    )
    assert addresses[0].email == "careers@example.com"
    assert addresses[0].send_default
    assert addresses[1].named_person
    assert not addresses[1].send_default


def test_email_checks_reject_unsupported_claims_and_pressure() -> None:
    """Unknown citations, new numbers, salary and urgency are all caught."""
    errors = check(
        "I have 12 years of experience and can start immediately. Let's discuss salary.",
        [99],
        {1: "Built Python services."},
    )
    assert {"unknown citation", "uncited number", "salary talk", "false urgency"} <= set(errors)


def test_email_checks_accept_supported_number() -> None:
    """A number that appears in the cited fact passes."""
    facts = {1: "Improved throughput by 20% with Python."}
    assert check("I improved throughput by 20% using Python.", [1], facts) == []


def test_email_checks_reject_uncited_skill_and_missing_citations() -> None:
    """A skill not in the cited facts, or no citations at all, fails."""
    assert "uncited claim: skill 'Kafka'" in check(
        "I built Kafka pipelines.", [1], {1: "Built Python services."}
    )
    assert any("no cited profile facts" in e for e in check("Hello, I am interested.", [], {}))


def test_named_people_are_not_hiring_inboxes() -> None:
    """Personal addresses aren't hiring inboxes; hr.india@ is."""
    found = {a.email: a for a in find("chris.mathew@acme.com shreya@acme.com hr.india@acme.com", "jd")}
    assert not found["chris.mathew@acme.com"].send_default and found["chris.mathew@acme.com"].named_person
    assert not found["shreya@acme.com"].send_default
    assert found["hr.india@acme.com"].send_default


# ---- email v4 (decision 36): code writes the frame, the model only fit sentences ----------------------

SOURCES = {
    38: "Worked in Gen AI projects training large language models (LLMs) through RLHF and SFT. "
    "[Delivery Data Scientist at Turing (june 2024-present)]",
    40: "Learned about BEML projects like TATRA vehicle design and manufacturing. [Student Intern at BEML]",
    70: "Built a multi-agent system with LangGraph to search for jobs over 32 technical books. [JobPilot]",
}


def points(*items: tuple[int, str]):  # type: ignore[no-untyped-def]
    """The model's fit sentences."""
    from jobpilot.models.email import EmailPoints, FitPoint

    return EmailPoints(points=[FitPoint(fact_id=i, sentence=s) for i, s in items])


def test_only_faithful_fit_sentences_are_kept() -> None:
    """Invented results, changed involvement and non-experience facts never reach the email."""
    from jobpilot.hr_email.email_writer import checked_points

    kept, cited, refused = checked_points(
        points(
            (70, "I built a multi-agent system with LangGraph to search for jobs over 32 technical books."),
            (38, "At Turing, I led Gen AI projects and improved LLM accuracy by 40%."),
            (40, "At BEML, I designed and manufactured TATRA vehicles."),
        ),
        SOURCES,
    )
    assert kept[0].startswith("I built a multi-agent system") and cited[0] == 70
    assert "40%" not in " ".join(kept) and "designed and manufactured" not in " ".join(kept)
    assert kept[1].startswith("I worked in Gen AI projects")  # refused sentence -> the fact itself
    assert len(refused) == 2 and any("changes what you did" in r for r in refused)
    assert kept[2].startswith("I learned about BEML projects")  # the true fact, never "designed"
    _kept, none, [why] = checked_points(points((5, "I am an expert in Python.")), SOURCES)
    assert none == [] and "not an experience or project fact" in why


def test_the_frame_is_professional_and_complete() -> None:
    """Subject, greeting, opening, fit lines, closing and signature with contacts - all by code."""
    from jobpilot.hr_email.email_writer import frame

    subject, body = frame(
        {"company": "Trackmind", "title": "Junior AI Engineer"},
        "ASHA RAO",
        ["+91 90000 00000", "a@b.com"],
        ["I built a multi-agent system with LangGraph."],
        [],
    )
    assert subject == "Application for Junior AI Engineer - Asha Rao"
    lines = body.splitlines()
    assert lines[0] == "Dear Trackmind Hiring Team,"
    assert "I have applied for the Junior AI Engineer role at Trackmind" in body
    assert "- I built a multi-agent system with LangGraph." in lines
    assert lines[-3:] == ["Best regards,", "Asha Rao", "+91 90000 00000 | a@b.com"]
    assert "?" not in body  # no questions to HR
    assert (
        check(body, [70], {70: SOURCES[70]}, job_title="Junior AI Engineer") == []
    )  # contacts aren't claims


def test_email_checks_refuse_puffery() -> None:
    """Self-praise the profile doesn't say is refused."""
    errors = check("I am a proven, passionate engineer who built Python services.", [1],
                   {1: "Built Python services."})  # fmt: skip
    assert any(e.startswith("puffery") for e in errors)


def test_a_fit_sentence_may_use_the_jobs_name_for_a_skill_the_fact_names() -> None:
    """The job's 'Generative AI' for the fact's 'Gen AI'; never a skill the fact lacks."""
    from jobpilot.hr_email.email_writer import checked_points

    sentence = (
        "At Turing, I worked in Generative AI projects training large language models through RLHF and SFT."
    )
    jd = "Generative AI, Kubernetes"
    kept, _cited, refused = checked_points(points((38, sentence)), SOURCES, jd)
    assert kept == [sentence] and refused == []
    kept, _cited, refused = checked_points(
        points((38, sentence.replace("SFT", "SFT on Kubernetes"))), SOURCES, jd
    )
    assert refused and "Kubernetes" not in kept[0]


SKILLS = {8: "Python", 74: "LangGraph", 64: "RAG", 49: "LLm", 50: "GENAI", 24: "AutoCad", 45: "FastAPI"}
JD = "We use Python, LangGraph, RAG, large language models and Kubernetes."


async def test_the_email_always_shows_a_project_and_the_matching_skills() -> None:
    """The model gave only company facts: code adds the best project fact and a skills line."""
    from types import SimpleNamespace

    from jobpilot.hr_email import email_writer

    class OnlyCompany:
        async def structured(self, *_: object, **__: object) -> object:
            return SimpleNamespace(
                output=points(
                    (
                        38,
                        "At Turing, I worked in Gen AI projects training large language models through RLHF "
                        "and SFT.",
                    ),
                )
            )

    draft, _r, refused = await email_writer.write(
        OnlyCompany(),
        {"company": "Acme", "title": "AI Engineer", "jd_text": JD},
        SOURCES,  # type: ignore[arg-type]
        applicant="Asha Rao",
        contacts=["a@b.com"],
        project_ids=[70],
        skills=SKILLS,
    )
    assert "- In my JobPilot project, I built a multi-agent system with LangGraph" in draft.body
    assert "Relevant skills: Python, LangGraph, RAG and LLMs." in draft.body  # confirmed and asked for
    assert "AutoCad" not in draft.body and "Kubernetes" not in draft.body  # not asked / not confirmed
    assert {38, 70, 8, 74, 64, 49} <= set(draft.citations) and any("project" in r for r in refused)
    facts = {**SOURCES, **SKILLS}
    assert check(draft.body, draft.citations, facts, job_title="AI Engineer") == []  # within 150 words


async def test_the_email_reads_skill_names_from_the_job_title_too() -> None:
    """A skill named only in the job title still reaches the "Relevant skills" line."""
    from types import SimpleNamespace

    from jobpilot.hr_email import email_writer

    class Nothing:
        async def structured(self, *_: object, **__: object) -> object:
            return SimpleNamespace(output=points())

    draft, _r, _refused = await email_writer.write(
        Nothing(),
        {"company": "Acme", "title": "LangGraph Engineer", "jd_text": "Build agents."},
        SOURCES,  # type: ignore[arg-type]
        project_ids=[70],
        skills=SKILLS,
    )
    assert "Relevant skills: LangGraph." in draft.body
