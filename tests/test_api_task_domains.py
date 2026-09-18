"""Flask-level tests for POST /tasks/<task_id>/domain-whitelist -- lets the
browser extension save sites allowed mid-session onto a task's own saved
domainWhitelist, so the next session on that task starts with them already
allowed."""
import pytest

import api_server
import config
import tasks_store


@pytest.fixture
def client(isolate_config, tmp_path, monkeypatch):
    monkeypatch.setattr(tasks_store, "TASKS_PATH", str(tmp_path / "tasks.json"))
    api_server.app.config["TESTING"] = True
    test_client = api_server.app.test_client()
    test_client.environ_base["HTTP_X_CARMEN_TOKEN"] = config.get_api_token()
    return test_client


def test_adds_new_domains_to_task(client):
    task = tasks_store.create_task({"name": "Math", "domainWhitelist": ["existing.com"]})

    resp = client.post(f"/tasks/{task['id']}/domain-whitelist", json={"domains": ["new.com", "another.com"]})

    assert resp.status_code == 200
    assert resp.get_json()["domainWhitelist"] == ["existing.com", "new.com", "another.com"]


def test_dedupes_case_insensitively_against_existing(client):
    task = tasks_store.create_task({"name": "Math", "domainWhitelist": ["Example.com"]})

    resp = client.post(f"/tasks/{task['id']}/domain-whitelist", json={"domains": ["example.com", "new.com"]})

    assert resp.status_code == 200
    domains = resp.get_json()["domainWhitelist"]
    assert domains == ["Example.com", "new.com"]


def test_unknown_task_returns_404(client):
    resp = client.post("/tasks/does-not-exist/domain-whitelist", json={"domains": ["a.com"]})
    assert resp.status_code == 404


def test_rejects_non_list_domains(client):
    task = tasks_store.create_task({"name": "Math"})
    resp = client.post(f"/tasks/{task['id']}/domain-whitelist", json={"domains": "not-a-list"})
    assert resp.status_code == 400


def test_requires_token(client):
    task = tasks_store.create_task({"name": "Math"})
    resp = client.post(
        f"/tasks/{task['id']}/domain-whitelist",
        json={"domains": ["a.com"]},
        headers={"X-Carmen-Token": "wrong"},
    )
    assert resp.status_code == 401
