# backend/tests/test_groups_api.py
import pytest
from fastapi.testclient import TestClient
from app.main import app

pytestmark = pytest.mark.integration


def test_create_group():
    with TestClient(app) as client:
        r = client.post("/api/groups", json={"question": "Dinner?", "creator_nickname": "Alice"})
        assert r.status_code == 201
        body = r.json()
        assert len(body["pin"]) == 5
        assert body["status"] == "collecting"
        assert body["creator_token"]
        assert body["participant_id"]


def test_join_and_state():
    with TestClient(app) as client:
        g = client.post("/api/groups", json={"question": "Q", "creator_nickname": "A"}).json()
        pin, tok = g["pin"], g["creator_token"]
        r = client.post(f"/api/groups/{pin}/join", json={"nickname": "Bob"})
        assert r.status_code == 200
        assert r.json()["status"] == "collecting"
        s = client.get(f"/api/groups/{pin}/state").json()
        assert s["question"] == "Q" and s["status"] == "collecting"
        assert s["participant_count"] == 2  # creator + Bob
        assert s["submitted_count"] == 0
        s_creator = client.get(f"/api/groups/{pin}/state?token={tok}").json()
        assert s_creator["is_creator"] is True


def test_join_duplicate_nickname_409():
    with TestClient(app) as client:
        g = client.post("/api/groups", json={"question": "Q", "creator_nickname": "Alice"}).json()
        client.post(f"/api/groups/{g['pin']}/join", json={"nickname": "Alice"})  # dup of creator
        r = client.post(f"/api/groups/{g['pin']}/join", json={"nickname": "Alice"})
        assert r.status_code == 409


def test_create_rejects_long_question():
    with TestClient(app) as client:
        r = client.post("/api/groups", json={"question": "x"*1001, "creator_nickname": "A"})
        assert r.status_code == 422
