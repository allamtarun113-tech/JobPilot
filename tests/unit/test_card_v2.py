"""Card v2 (decision 32): the Telegram HTML card.

Clear sections, escaped job data, regrouped short buttons, and the
page-1 preview.
"""

from datetime import timedelta

from jobpilot.cards import render
from jobpilot.core import clock
from jobpilot.resume import preview


def card() -> str:
    """Render a sample card for a job with an HTML-unsafe company name."""
    job = {
        "id": 7,
        "company": "Acme <Labs> & Co",
        "title": "AI Engineer",
        "location_raw": "Bengaluru",
        "first_seen_at": clock.now() - timedelta(days=2),
        "jd_is_snippet": False,
        "jd_text": "",
        "apply_url": "https://jobs.example/7",
    }
    score = {
        "score": 82,
        "parts": {},
        "gap": [{"skill": "Docker", "skill_id": "docker", "points": 6.0, "easy": True}],
        "fit": {
            "summary": "Strong on Python; missing Docker",
            "why": ["Python: in your skills"],
            "missing": [],
        },
    }
    return render.text(
        job, score, dashboard_url="https://dash.example", requirements={"must_skills": ["Python"]}
    )


def test_the_card_has_clear_sections_in_telegram_html() -> None:
    """The card has its title, match, reasons, skills to learn and link."""
    text = card()
    assert text.startswith("<b>AI Engineer</b>")
    assert "📍 Bengaluru · posted 2d ago" in text and "🎯 <b>Match 82%</b>" in text
    assert "✓ Python: in your skills" in text and "📚 <b>To learn</b>" in text
    assert "• Docker +6 · quick to learn" in text
    assert '<a href="https://dash.example/jobs/7">Open full details on the dashboard</a>' in text


def test_job_data_can_never_become_markup() -> None:
    """Job data is escaped, so it can't inject HTML."""
    text = card()
    assert "Acme &lt;Labs&gt; &amp; Co" in text and "<Labs>" not in text


def test_buttons_are_regrouped_and_fit_telegram_limits() -> None:
    """Buttons are grouped, a bad link is dropped, data fits 64 bytes."""
    main = render.buttons("tok12345", 3, "https://jobs.example/7")["inline_keyboard"]
    assert [b["text"] for b in main[0]] == ["✅ Approve resume v3", "🔗 Apply"]
    assert [b["text"] for b in main[1]] == ["📄 View resume v3", "More…"]
    more = render.more_buttons("tok12345", 3, "not a url")["inline_keyboard"]
    assert [b["text"] for b in more[0]] == ["✅ Approve resume v3"]  # a broken apply link is left off
    assert all(len(b.get("callback_data", "").encode()) <= 64 for row in main + more for b in row)


def test_the_preview_sits_next_to_its_pdf() -> None:
    """A preview's key is its PDF's key with .png."""
    assert preview.key_for("resumes/jobs/7/abc.pdf") == "resumes/jobs/7/abc.png"
