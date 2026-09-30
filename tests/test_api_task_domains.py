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


def test_concurrent_adds_of_different_domains_do_not_lose_either(client):
    """Regression test: this route used to do get_task() then update_task()
    with the merge computed in plain Python in an unlocked gap between them
    -- two concurrent adds of different domains to the same task both read
    the same starting list, both wrote back their own merged version, and
    whichever update_task() call landed last silently discarded the
    other's domain, with both requests reporting 200 success. Reproduced
    with a barrier so both requests are genuinely in flight at the same
    instant rather than hoping for a flaky-lucky interleaving."""
    import threading

    task = tasks_store.create_task({"name": "Math"})
    barrier = threading.Barrier(2)
    responses = {}

    def add(name, domain):
        barrier.wait(timeout=2)
        responses[name] = client.post(
            f"/tasks/{task['id']}/domain-whitelist", json={"domains": [domain]}
        )

    t1 = threading.Thread(target=add, args=("a", "site-a.com"))
    t2 = threading.Thread(target=add, args=("b", "site-b.com"))
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert not t1.is_alive() and not t2.is_alive(), "threads deadlocked or hung"
    assert responses["a"].status_code == 200
    assert responses["b"].status_code == 200

    final = tasks_store.get_task(task["id"])["domainWhitelist"]
    assert "site-a.com" in final
    assert "site-b.com" in final


def test_requires_token(client):
    task = tasks_store.create_task({"name": "Math"})
    resp = client.post(
        f"/tasks/{task['id']}/domain-whitelist",
        json={"domains": ["a.com"]},
        headers={"X-Carmen-Token": "wrong"},
    )
    assert resp.status_code == 401
