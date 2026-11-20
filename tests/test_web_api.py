"""API tests for auth, invites, uploads, isolation, and the media pipeline."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from clipforge.api.main import create_app
from clipforge.config import Settings
from clipforge.worker import process_job

from .conftest import requires_ffmpeg
from .helpers import make_test_video

ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "password12345"


def make_client(tmp_path: Path) -> tuple[TestClient, Settings]:
    settings = Settings(
        data_dir=tmp_path,
        admin_email=ADMIN_EMAIL,
        admin_initial_password=ADMIN_PASSWORD,
        app_env="test",
        default_skip_transcript=True,
        session_cookie_secure=False,
    )
    return TestClient(create_app(settings)), settings


def login(client: TestClient, email: str = ADMIN_EMAIL, password: str = ADMIN_PASSWORD) -> str:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["csrf"]


def csrf_headers(token: str) -> dict[str, str]:
    return {"X-CSRF-Token": token}


def test_auth_csrf_and_project_creation(tmp_path) -> None:
    client, _settings = make_client(tmp_path)
    with client:
        csrf = login(client)
        me = client.get("/api/v1/auth/me")
        assert me.status_code == 200
        assert me.json()["user"]["role"] == "admin"

        # Mutations require CSRF.
        denied = client.post("/api/v1/projects", json={"youtube_url": "https://youtu.be/abc12345678"})
        assert denied.status_code == 403

        created = client.post(
            "/api/v1/projects",
            json={"youtube_url": "https://youtu.be/abc12345678", "title": "Test"},
            headers=csrf_headers(csrf),
        )
        assert created.status_code == 200, created.text
        project = created.json()["project"]
        assert project["source_type"] == "youtube"
        assert created.json()["job"]["kind"] == "ingest"

        listing = client.get("/api/v1/projects").json()
        assert len(listing["projects"]) == 1
        timeline = client.get(f"/api/v1/projects/{project['id']}/timeline")
        assert timeline.status_code == 200
        assert timeline.json()["project_id"] == project["id"]


def test_invite_acceptance_and_admin_media_privacy(tmp_path) -> None:
    client, _settings = make_client(tmp_path)
    with client:
        admin_csrf = login(client)
        invite = client.post(
            "/api/v1/admin/invites",
            json={"email": "member@example.com"},
            headers=csrf_headers(admin_csrf),
        )
        assert invite.status_code == 200, invite.text
        invite_url = invite.json()["invite_url"]
        token = invite_url.rstrip("/").split("/")[-1]

        client.post("/api/v1/auth/logout")
        accepted = client.post(
            "/api/v1/auth/accept-invite",
            json={"token": token, "password": "memberpass123"},
        )
        assert accepted.status_code == 200, accepted.text
        member_csrf = accepted.json()["csrf"]
        member_id = accepted.json()["user"]["id"]

        project = client.post(
            "/api/v1/projects",
            json={"youtube_url": "https://youtu.be/xyz12345678", "title": "Member project"},
            headers=csrf_headers(member_csrf),
        )
        assert project.status_code == 200
        project_id = project.json()["project"]["id"]

        client.post("/api/v1/auth/logout")
        login(client)
        admin_users = client.get("/api/v1/admin/users").json()["users"]
        assert any(user["id"] == member_id for user in admin_users)
        assert all("project_titles" not in user for user in admin_users)

        # Admins cannot read another user's private media or project.
        assert client.get(f"/api/v1/media/projects/{project_id}/preview").status_code == 404
        assert client.get(f"/api/v1/projects/{project_id}").status_code == 404


@requires_ffmpeg
def test_upload_ingest_analyze_render_flow(tmp_path) -> None:
    client, settings = make_client(tmp_path)
    source = make_test_video(tmp_path / "source.mp4", seconds=6.0)
    with client:
        csrf = login(client)
        data = source.read_bytes()
        init = client.post(
            "/api/v1/uploads",
            json={"filename": "source.mp4", "size": len(data)},
            headers=csrf_headers(csrf),
        )
        assert init.status_code == 200, init.text
        upload = init.json()
        assert client.put(
            f"/api/v1/uploads/{upload['id']}?offset=0",
            content=data,
            headers=csrf_headers(csrf),
        ).status_code == 200
        completed = client.post(
            f"/api/v1/uploads/{upload['id']}/complete",
            headers=csrf_headers(csrf),
        )
        assert completed.status_code == 200, completed.text

        created = client.post(
            "/api/v1/projects",
            json={
                "upload_id": upload["id"],
                "title": "Pipeline test",
                "options": {"max_len": 2, "count": 1, "skip_transcript": True},
            },
            headers=csrf_headers(csrf),
        )
        assert created.status_code == 200, created.text
        project = created.json()["project"]
        process_job(client.app.state.db, settings, created.json()["job"]["id"])

        project_state = client.get(f"/api/v1/projects/{project['id']}").json()
        assert project_state["status"] == "ready"
        assert project_state["duration_s"] == pytest.approx(6.0, abs=0.2)
        assert project_state["preview_url"]

        analyzed = client.post(
            f"/api/v1/projects/{project['id']}/analyze",
            headers=csrf_headers(csrf),
        )
        assert analyzed.status_code == 200, analyzed.text
        process_job(client.app.state.db, settings, analyzed.json()["job"]["id"])

        timeline = client.get(f"/api/v1/projects/{project['id']}/timeline").json()
        assert timeline["analysis_available"] is True
        assert timeline["moments"]
        moment = timeline["moments"][0]

        saved = client.patch(
            f"/api/v1/projects/{project['id']}/moments",
            json={
                "moments": [
                    {
                        "id": moment["id"],
                        "start": moment["start"],
                        "end": moment["end"],
                        "peak": moment.get("peak", moment["start"]),
                        "title": moment["title"],
                        "source": moment.get("source", "audio"),
                        "score": moment.get("score", 0),
                        "why": moment.get("why", ""),
                        "enabled": True,
                    }
                ]
            },
            headers=csrf_headers(csrf),
        )
        assert saved.status_code == 200, saved.text

        rendered = client.post(
            f"/api/v1/projects/{project['id']}/render",
            json={"moment_ids": [moment["id"]]},
            headers=csrf_headers(csrf),
        )
        assert rendered.status_code == 200, rendered.text
        process_job(client.app.state.db, settings, rendered.json()["job"]["id"])

        clips = client.get(f"/api/v1/projects/{project['id']}/clips").json()["clips"]
        assert len(clips) == 1
        clip = clips[0]
        assert clip["ok"] is True
        assert clip["length_s"] <= 2.05
        media = client.get(clip["file_url"])
        assert media.status_code == 200
        assert media.headers["content-type"].startswith("video/mp4")
        assert client.get(clip["thumb_url"]).status_code == 200
        manifest = client.get(f"/api/v1/projects/{project['id']}/manifest")
        assert manifest.status_code == 200
        assert manifest.json()["clips"][0]["id"] == moment["id"]


def test_upload_limit_is_enforced(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        admin_email=ADMIN_EMAIL,
        admin_initial_password=ADMIN_PASSWORD,
        app_env="test",
        max_upload_bytes=10,
    )
    client = TestClient(create_app(settings))
    with client:
        csrf = login(client)
        response = client.post(
            "/api/v1/uploads",
            json={"filename": "too-big.mp4", "size": 11},
            headers=csrf_headers(csrf),
        )
        assert response.status_code == 413
