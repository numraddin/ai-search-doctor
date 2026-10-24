"""Per-visitor daily limit: protects the owner's model budget on a public deployment."""
import pytest
from fastapi.testclient import TestClient

from app import config, main, pipeline

URL = {"urls": ["http://127.0.0.1:8000/demo/a/"]}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(pipeline, "run_audit", lambda job: None)  # no real audits in these tests
    monkeypatch.setattr(config, "TRUST_PROXY", False)


def visitor(ip="203.0.113.7"):
    return TestClient(main.app, client=(ip, 5000))


def test_sixth_analysis_of_the_day_is_refused_with_a_friendly_message():
    c = visitor()
    assert [c.post("/api/audit", json=URL).status_code for _ in range(5)] == [200] * 5
    r = c.post("/api/audit", json=URL)
    assert r.status_code == 429 and "come back tomorrow" in r.json()["detail"].lower() and "5" in r.json()["detail"]


def test_limit_is_per_visitor():
    a, b = visitor("203.0.113.7"), visitor("203.0.113.8")
    for _ in range(5):
        a.post("/api/audit", json=URL)
    assert a.post("/api/audit", json=URL).status_code == 429
    assert b.post("/api/audit", json=URL).status_code == 200


def test_invalid_links_do_not_use_up_the_allowance():
    c = visitor()
    for _ in range(8):
        assert c.post("/api/audit", json={"urls": ["http://10.0.0.5/"]}).status_code == 400
    assert c.get("/api/quota").json()["remaining"] == 5


def test_owner_computer_is_not_limited():
    me = visitor("127.0.0.1")
    assert all(me.post("/api/audit", json=URL).status_code == 200 for _ in range(8))
    assert me.get("/api/quota").json() == {"limited": False}


def test_quota_counts_down():
    c = visitor()
    c.post("/api/audit", json=URL)
    assert c.get("/api/quota").json() == {"limited": True, "limit": 5, "used": 1, "remaining": 4}


def test_forwarded_header_cannot_be_faked_unless_proxy_is_trusted(monkeypatch):
    c = visitor()
    for i in range(5):  # a visitor rotating a fake header must not escape the limit
        assert c.post("/api/audit", json=URL, headers={"X-Forwarded-For": f"198.51.100.{i}"}).status_code == 200
    assert c.post("/api/audit", json=URL, headers={"X-Forwarded-For": "198.51.100.99"}).status_code == 429
    monkeypatch.setattr(config, "TRUST_PROXY", True)  # behind a real proxy the header identifies the visitor
    assert c.post("/api/audit", json=URL, headers={"X-Forwarded-For": "198.51.100.200"}).status_code == 200


def test_global_daily_cap(monkeypatch):
    monkeypatch.setattr(config, "DAILY_AUDIT_CAP", 3)
    outs = [visitor(f"203.0.113.{i}").post("/api/audit", json=URL).status_code for i in range(5)]
    assert outs == [200, 200, 200, 429, 429]
