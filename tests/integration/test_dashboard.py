"""Dashboard: login (TOTP), CSRF, profile confirmation, preferences, file access."""

import re
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from jobpilot.core.config import get_settings
from jobpilot.db.queries import profile_items as items_repo
from jobpilot.db.session import transaction
from jobpilot.matching import profile_view
from jobpilot.models.preferences import RelatedTitle
from jobpilot.profile import preferences
from jobpilot.resume import doc as resume_doc
from jobpilot.safety import totp
from jobpilot.web.main import create_app
from tests.conftest import SAMPLE_RESUME

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """Yield an HTTP client wired to a fresh dashboard app."""
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
        yield c


def tokens(html: str) -> tuple[str, str]:
    """Return the CSRF token and nonce from a page's form."""
    csrf = re.search(r'name="csrf" value="([^"]+)"', html)
    nonce = re.search(r'name="nonce" value="([^"]+)"', html)
    assert csrf and nonce
    return csrf.group(1), nonce.group(1)


async def test_profile_confirmation_flow(client: httpx.AsyncClient) -> None:
    """Confirm-all needs CSRF and leaves every bullet backed."""
    await resume_doc.import_from_tex(SAMPLE_RESUME)
    page = await client.get("/profile")
    assert page.status_code == 200 and "Login is off" in page.text and "to review" in page.text
    csrf, nonce = tokens(page.text)

    assert (await client.post("/profile/confirm-all", data={"nonce": nonce})).status_code == 403  # no CSRF
    resp = await client.post("/profile/confirm-all", data={"csrf": csrf, "nonce": nonce})
    assert resp.status_code == 303
    page = await client.get("/profile")
    assert "every bullet rests on confirmed items" in page.text


async def test_item_reject_and_double_submit(client: httpx.AsyncClient) -> None:
    """A double submit is a no-op; a stale version is reported."""
    await resume_doc.import_from_tex(SAMPLE_RESUME)
    page = await client.get("/profile")
    csrf, _nonce = tokens(page.text)
    async with transaction() as conn:
        item = (await items_repo.list_all(conn))[0]
    form = {"csrf": csrf, "nonce": "n-1", "version": str(item["version"]), "action": "rejected"}
    await client.post(f"/profile/items/{item['id']}", data=form)
    await client.post(f"/profile/items/{item['id']}", data=form)  # same nonce: no-op, no conflict
    async with transaction() as conn:
        row = await items_repo.get(conn, item["id"])
    assert row is not None and row["status"] == "rejected" and row["version"] == item["version"] + 1
    stale = {**form, "nonce": "n-2", "action": "confirmed"}  # old version
    await client.post(f"/profile/items/{item['id']}", data=stale)
    assert "changed in the meantime" in (await client.get("/profile")).text


async def test_profile_groups_details_under_experiences_and_projects(client: httpx.AsyncClient) -> None:
    """Experiences hold their details, fields and moves, version-checked."""
    await resume_doc.import_from_tex(SAMPLE_RESUME)
    csrf, _ = tokens((await client.get("/profile")).text)
    role = {
        "csrf": csrf,
        "nonce": "r-1",
        "kind": "role",
        "title": "ML Engineer",
        "organization": "Acme",
        "dates": "Jan 2023 - Dec 2023",
        "tech": "Docker, FastAPI",
        "about": "Search team",
    }
    assert (await client.post("/profile/entries", data=role)).status_code == 303
    async with transaction() as conn:
        added = [r for r in await items_repo.list_all(conn) if r["source"] == "manual"]
    assert len(added) == 1 and added[0]["status"] == "confirmed"
    assert added[0]["text"] == "ML Engineer at Acme (Jan 2023 - Dec 2023)"
    assert added[0]["attrs"]["tech"] == ["Docker", "FastAPI"]
    role_id = added[0]["id"]

    detail = {"csrf": csrf, "nonce": "d-1", "kind": "metric", "text": "Cut p95 latency by 30%"}
    await client.post(f"/profile/items/{role_id}/details", data=detail)
    await client.post(f"/profile/items/{role_id}/details", data=detail)  # double submit: one detail
    async with transaction() as conn:
        details = [
            r for r in await items_repo.list_all(conn) if (r["attrs"] or {}).get("parent_id") == role_id
        ]
        view = await profile_view.load(conn)
    assert [d["text"] for d in details] == ["Cut p95 latency by 30%"]
    assert view.skill_names["docker"] == [role_id] and view.years >= 1.0  # tools on the role count as skills
    assert "Cut p95 latency by 30% [ML Engineer at Acme" in view.context_items[details[0]["id"]]

    page = (await client.get("/profile")).text
    assert "What you did here" in page and "ML Engineer" in page and "Counts as 12 months" in page

    fields = {
        "csrf": csrf,
        "nonce": "f-1",
        "version": "1",
        "title": "Senior ML Engineer",
        "organization": "Acme",
        "dates": "Jan 2023 - Dec 2023",
        "tech": "Docker",
    }
    await client.post(f"/profile/items/{role_id}/fields", data=fields)
    await client.post(f"/profile/items/{role_id}/fields", data={**fields, "nonce": "f-2"})  # stale version
    assert "changed in the meantime" in (await client.get("/profile")).text
    async with transaction() as conn:
        row = await items_repo.get(conn, role_id)
        imported = next(r for r in await items_repo.list_all(conn) if r["kind"] == "achievement")
    assert row is not None and row["text"] == "Senior ML Engineer at Acme (Jan 2023 - Dec 2023)"
    assert row["attrs"]["tech"] == ["Docker"] and row["version"] == 2

    move = {
        "csrf": csrf,
        "nonce": "m-1",
        "version": str(imported["version"]),
        "action": "move",
        "parent_id": str(role_id),
    }
    await client.post(f"/profile/items/{imported['id']}", data=move)
    async with transaction() as conn:
        moved = await items_repo.get(conn, imported["id"])
    assert moved is not None and moved["attrs"]["parent_id"] == role_id and "parent" not in moved["attrs"]

    bad = {"csrf": csrf, "nonce": "r-2", "kind": "project", "name": " "}
    await client.post("/profile/entries", data=bad)
    assert "a project needs a name" in (await client.get("/profile")).text


async def test_preferences_form_and_stale_version(client: httpx.AsyncClient) -> None:
    """A stale or invalid preferences form keeps what was typed and saves nothing."""
    page = await client.get("/preferences")
    csrf, nonce = tokens(page.text)
    form = {
        "csrf": csrf,
        "nonce": nonce,
        "version": "1",
        "exp_max_years": "3",
        "locations": "Bengaluru, Pune",
        "remote_ok": "on",
        "threshold": "75",
        "cards_per_day": "5",
        "emails_per_day": "2",
    }
    await client.post("/preferences", data=form)
    page = await client.get("/preferences")
    assert 'value="3.0"' in page.text and "Bengaluru, Pune" in page.text and "version 2" in page.text
    stale = await client.post("/preferences", data={**form, "nonce": "other", "threshold": "90"})  # version 1
    assert stale.status_code == 400 and "changed elsewhere" in stale.text
    assert 'value="90"' in stale.text and "version 2" in stale.text  # what you typed is kept, ready to resave
    page = await client.get("/preferences")
    assert 'value="75"' in page.text  # nothing was saved
    wrong = await client.post(
        "/preferences",
        data={**form, "nonce": "third", "version": "2", "threshold": "abc", "titles_target": "Data Analyst"},
    )
    assert wrong.status_code == 400 and "Match threshold must be a whole number" in wrong.text
    assert "Bengaluru, Pune" in wrong.text and "Data Analyst" in wrong.text and 'value="abc"' in wrong.text


async def test_related_titles_are_edited_on_the_preferences_page(client: httpx.AsyncClient) -> None:
    """Removed related titles are remembered so a refresh won't add them back."""
    async with transaction() as conn:
        prefs, version = await preferences.load(conn)
        prefs.titles_target = ["AI Engineer"]
        prefs.related_titles = [
            RelatedTitle(title="ML Engineer", reason="builds and ships models"),
            RelatedTitle(title="Data Scientist", reason="close work"),
        ]
        version = await preferences.save(conn, prefs, version)
    page = await client.get("/preferences")
    assert "ML Engineer" in page.text and "builds and ships models" in page.text
    csrf, nonce = tokens(page.text)
    form = {
        "csrf": csrf,
        "nonce": nonce,
        "version": str(version),
        "titles_target": "AI Engineer",
        "related_titles": "ML Engineer\nLLM Engineer\n",
        "threshold": "70",
        "cards_per_day": "5",
        "emails_per_day": "2",
    }
    assert (await client.post("/preferences", data=form)).status_code == 303
    async with transaction() as conn:
        saved, _ = await preferences.load(conn)
    assert [(r.title, r.reason) for r in saved.related_titles] == [
        ("ML Engineer", "builds and ships models"),
        ("LLM Engineer", "added by you"),
    ]
    assert saved.related_titles_removed == ["Data Scientist"]  # a weekly refresh won't bring it back


async def test_files_are_restricted(client: httpx.AsyncClient, store: Any) -> None:
    """Only stored resume PDFs can be downloaded."""
    await store.put("resumes/base/1-v1.pdf", b"%PDF-1.4 test")
    assert (await client.get("/files/resumes/base/1-v1.pdf")).content == b"%PDF-1.4 test"
    assert (await client.get("/files/resumes/../../../etc/passwd")).status_code == 404
    assert (await client.get("/files/other/x.pdf")).status_code == 404


async def test_login_required_when_totp_secret_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """With a TOTP secret, pages need a login and only a valid code works."""
    secret = totp.new_secret()
    monkeypatch.setattr(get_settings(), "dashboard_totp_secret", SecretStr(secret))
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
        resp = await c.get("/profile")
        assert resp.status_code == 303 and resp.headers["location"] == "/login"
        assert (await c.get("/health")).status_code == 200  # public
        page = await c.get("/login")
        csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)  # type: ignore[union-attr]
        assert (await c.post("/login", data={"csrf": csrf, "code": "000000"})).status_code == 401
        from jobpilot.core import clock

        code = totp.code_at(secret, int(clock.now().timestamp()) // 30)
        assert (await c.post("/login", data={"csrf": csrf, "code": code})).status_code == 303
        assert (await c.get("/profile")).status_code == 200


def test_refuses_public_bind_without_login() -> None:
    """The dashboard won't bind beyond localhost without login."""
    from jobpilot.web import auth

    auth.check_startup("127.0.0.1")
    with pytest.raises(SystemExit):
        auth.check_startup("0.0.0.0")


async def test_expired_login_form_recovers_without_authenticating(monkeypatch: pytest.MonkeyPatch) -> None:
    """An expired login form gets a fresh one and never logs in."""
    secret = totp.new_secret()
    monkeypatch.setattr(get_settings(), "dashboard_totp_secret", SecretStr(secret))
    from jobpilot.core import clock

    code = totp.code_at(secret, int(clock.now().timestamp()) // 30)
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
        original = await c.get("/login")
        csrf = re.search(r'name="csrf" value="([^"]+)"', original.text).group(1)  # type: ignore[union-attr]
        assert original.headers["cache-control"] == "no-store"
        assert "samesite=lax" in original.headers["set-cookie"].lower()
        for lose_cookie in (False, True):
            if lose_cookie:
                c.cookies.clear()
            response = await c.post("/login", data={"csrf": csrf + "stale", "code": code})
            assert response.status_code == 303
            assert response.headers["location"] == "/login?expired=1"
            assert (await c.get("/profile")).headers["location"] == "/login"
        fresh = await c.get(response.headers["location"])
        assert "Your login form expired" in fresh.text
        csrf = re.search(r'name="csrf" value="([^"]+)"', fresh.text).group(1)  # type: ignore[union-attr]
        accepted = await c.post("/login", data={"csrf": csrf, "code": code})
        assert accepted.headers["location"] == "/jobs"
        assert (await c.get("/profile")).status_code == 200


def test_vercel_needs_session_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """On Vercel the app won't start without a fixed session secret."""
    monkeypatch.setattr(get_settings(), "vercel", True)
    monkeypatch.setattr(get_settings(), "dashboard_session_secret", None)
    with pytest.raises(RuntimeError, match="DASHBOARD_SESSION_SECRET"):
        create_app()


async def test_vercel_refuses_to_serve_without_login(monkeypatch: pytest.MonkeyPatch) -> None:
    """On Vercel every page answers 503 until TOTP is configured."""
    monkeypatch.setattr(get_settings(), "vercel", True)
    monkeypatch.setattr(get_settings(), "dashboard_session_secret", SecretStr("s" * 32))
    monkeypatch.setattr(get_settings(), "dashboard_totp_secret", None)
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="https://jobpilot.example") as c:
        for path in ("/profile", "/health", "/login"):
            resp = await c.get(path)
            assert resp.status_code == 503 and "DASHBOARD_TOTP_SECRET" in resp.text
