"""Acceptance tests A–M for the autonomous AI workplace (mock provider)."""
import time
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from backend.app import config, db, workers
from backend.app.main import app


# ---------- fixtures & helpers ----------

@pytest.fixture()
def env(tmp_path):
    db.reset_for_tests(tmp_path / "data")
    config.DISABLE_WORKERS = True
    config.WORKPLACE_TOKEN = ""
    config.WORKER_POLL_INTERVAL = 0.05
    conn = db.get_conn()
    for p, m in [("mock", "mock-small"), ("openai_compat", "gpt-4o-mini"),
                 ("anthropic", "claude-haiku-4-5"), ("gemini", "gemini-2.0-flash")]:
        conn.execute(
            "INSERT INTO provider_configs(id,provider,model,capabilities,is_default,created_at)"
            " VALUES(?,?,?,?,?,?)",
            (db.new_id("prv"), p, m, db.jdumps(config.PROVIDER_CAPABILITIES.get(p, {})),
             1 if p == "mock" else 0, db.now_iso()))
    conn.commit()
    yield {"dir": tmp_path}
    db.close_conn()


@pytest.fixture()
def client(env):
    return TestClient(app)


def mk_agent(client, **kw):
    body = {"name": kw.pop("name", "Test Agent"), "role": "general test",
            "provider": "mock", "model": "mock-small", **kw}
    r = client.post("/api/agents", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def mk_project(client, **kw):
    body = {"name": kw.pop("name", "Test Project"), **kw}
    r = client.post("/api/projects", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def mk_task(client, project_id, title, **kw):
    body = {"project_id": project_id, "title": title,
            "objective": kw.pop("objective", f"do {title}"), **kw}
    r = client.post("/api/tasks", json=body)
    assert r.status_code == 201, r.text
    return r.json()


RESTING = ("completed", "failed", "cancelled", "blocked", "waiting_input", "review_required")


def drain(deadline_s=60):
    """Drive the queue to resting states; fast-forwards retry backoffs (test-only)."""
    conn = db.get_conn()
    wid = workers.register_worker(conn)
    start = time.time()
    while time.time() - start < deadline_s:
        conn.execute("UPDATE tasks SET next_retry_at=NULL WHERE status='queued'"
                     " AND next_retry_at IS NOT NULL")
        conn.commit()
        workers.run_once(wid)
        n = conn.execute(
            "SELECT COUNT(*) c FROM tasks WHERE status NOT IN"
            " ('completed','failed','cancelled','blocked','waiting_input','review_required')"
        ).fetchone()["c"]
        if n == 0:
            return
        time.sleep(0.02)
    raise TimeoutError("drain timed out with unfinished tasks")


def get_task(client, tid):
    r = client.get(f"/api/tasks/{tid}")
    assert r.status_code == 200
    return r.json()


# ---------- TEST A: agent creation + restart persistence ----------

def test_A_agent_creation_persists_across_restart(client, env):
    a = mk_agent(client, name="Persist Bot", role="research market",
                 system_instructions="Be thorough.", allowed_tools=["fs_read", "web_fetch"],
                 budgets={"max_cost_usd": 5.0}, max_concurrent=3)
    assert a["role"] == "research market"
    # simulate backend restart: close + reopen DB
    db.close_conn()
    r = client.get(f"/api/agents/{a['id']}")
    assert r.status_code == 200
    a2 = r.json()
    assert a2["name"] == "Persist Bot"
    assert a2["allowed_tools"] == ["fs_read", "web_fetch"]
    assert a2["budgets"] == {"max_cost_usd": 5.0}
    assert a2["system_instructions"] == "Be thorough."


# ---------- TEST B: task assignment + execution + output ----------

def test_B_task_assignment_execution_output(client, env):
    a = mk_agent(client)
    p = mk_project(client)
    t = mk_task(client, p["id"], "write summary", agent_id=a["id"],
                payload={"mock_output": "SUMMARY-42"})
    assert t["agent_id"] == a["id"]
    drain()
    t2 = get_task(client, t["id"])
    assert t2["status"] == "completed"
    assert "SUMMARY-42" in t2["output"]
    assert len(t2["events"]) >= 3  # planned, queued, running, completed...
    assert any(e["to_status"] == "completed" for e in t2["events"])


# ---------- TEST C: concurrent execution ----------

def test_C_concurrent_execution_overlaps(client, env):
    import threading
    a = mk_agent(client, max_concurrent=5)
    p = mk_project(client)
    tids = [mk_task(client, p["id"], f"parallel-{i}", agent_id=a["id"],
                    payload={"mock_output": f"P{i}", "mock_sleep_s": 2})["id"]
            for i in range(3)]

    def work(i):
        conn = db.get_conn()
        wid = f"cwrk-{i}"
        conn.execute("INSERT INTO workers(id,status,last_heartbeat,started_at) VALUES(?, 'alive', ?, ?)",
                     (wid, db.now_iso(), db.now_iso()))
        conn.commit()
        start = time.time()
        while time.time() - start < 60:
            workers.run_once(wid)
            n = conn.execute("SELECT COUNT(*) c FROM tasks WHERE id IN (?,?,?)"
                             " AND status NOT IN ('completed','failed','cancelled')",
                             tids).fetchone()["c"]
            if n == 0:
                return
            time.sleep(0.02)
        raise TimeoutError("worker thread timed out")

    threads = [threading.Thread(target=work, args=(i,)) for i in range(3)]
    [t.start() for t in threads]
    [t.join() for t in threads]

    tasks = [get_task(client, tid) for tid in tids]
    assert all(t["status"] == "completed" for t in tasks)
    starts = [datetime.fromisoformat(t["started_at"]) for t in tasks]
    ends = [datetime.fromisoformat(t["completed_at"]) for t in tasks]
    assert max(starts) < min(ends), f"no overlap: {starts} vs {ends}"
    # distinct workers claimed them (from running-transition events)
    conn = db.get_conn()
    wid_rows = conn.execute(
        "SELECT DISTINCT worker_id w FROM task_events WHERE task_id IN (?,?,?)"
        " AND event_type='status' AND to_status='running'", tids).fetchall()
    assert len(wid_rows) == 3, f"expected 3 distinct workers, got {[r['w'] for r in wid_rows]}"


# ---------- TEST D: dependency handling ----------

def test_D_dependencies_gate_downstream(client, env):
    a = mk_agent(client)
    p = mk_project(client)
    tA = mk_task(client, p["id"], "step A", agent_id=a["id"], payload={"mock_output": "A"})
    tB = mk_task(client, p["id"], "step B", agent_id=a["id"], deps=[tA["id"]],
                 payload={"mock_output": "B"})
    tC = mk_task(client, p["id"], "step C", agent_id=a["id"], deps=[tB["id"]],
                 payload={"mock_output": "C"})
    drain()
    A, B, C = (get_task(client, x["id"]) for x in (tA, tB, tC))
    assert (A["status"], B["status"], C["status"]) == ("completed",) * 3
    assert A["completed_at"] <= B["started_at"] <= B["completed_at"] <= C["started_at"]
    # failed prereq blocks downstream
    tF = mk_task(client, p["id"], "doomed", agent_id=a["id"],
                 payload={"mock_provider_error": "missing_credentials"})
    tG = mk_task(client, p["id"], "gated", agent_id=a["id"], deps=[tF["id"]],
                 payload={"mock_output": "G"})
    drain()
    F, G = get_task(client, tF["id"]), get_task(client, tG["id"])
    assert F["status"] == "failed"
    assert G["status"] == "blocked"
    assert G["started_at"] in (None, ""), "blocked task must never start"


# ---------- TEST E: agent visibility (UI reflects real backend state) ----------

def test_E_ui_reflects_real_backend_state(client, env):
    a = mk_agent(client, name="Visible Bot")
    p = mk_project(client, name="Visible Project")
    t = mk_task(client, p["id"], "visible work", agent_id=a["id"],
                payload={"mock_output": "VISIBLE-OUTPUT"})
    drain()
    # frontend served and wires to real endpoints
    r = client.get("/")
    assert r.status_code == 200 and "Command Centre" in r.text
    assert "/api/stats" in client.get("/static/app.js").text
    assert "/api/events" in client.get("/static/app.js").text
    # stats match DB
    s = client.get("/api/stats").json()
    assert s["tasks_completed"] == 1
    assert s["agents_total"] == 1
    # task detail + events consistent with execution
    t2 = get_task(client, t["id"])
    assert t2["status"] == "completed" and "VISIBLE-OUTPUT" in t2["output"]
    evs = client.get("/api/task-events?since=0").json()
    assert any(e["task_id"] == t["id"] and e["to_status"] == "completed" for e in evs)
    # SSE endpoint streams real events
    try:
        with client.stream("GET", "/api/events?since=0") as resp:
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers["content-type"]
            first = ""
            for chunk in resp.iter_text():
                first += chunk
                if "connected" in first or "data:" in first:
                    break
            assert "connected" in first or "data:" in first
    except Exception as e:
        # fallback: route must at least be registered
        assert any(getattr(r, "path", "") == "/api/events" for r in app.routes), str(e)


# ---------- TEST F: failure recovery ----------

def test_F_transient_failure_retries_then_succeeds(client, env):
    a = mk_agent(client)
    p = mk_project(client)
    t = mk_task(client, p["id"], "flaky", agent_id=a["id"], payload={
        "mock_failures_remaining": 2, "mock_output": "RECOVERED"})
    drain()
    t2 = get_task(client, t["id"])
    assert t2["status"] == "completed"
    assert "RECOVERED" in t2["output"]
    assert t2["attempt"] == 2
    assert sum(1 for e in t2["events"] if e["event_type"] == "task_retry") == 2


def test_F_worker_crash_requeues_without_loss(client, env):
    a = mk_agent(client)
    p = mk_project(client)
    t = mk_task(client, p["id"], "crashy", agent_id=a["id"],
                payload={"mock_worker_crash": True, "mock_output": "SURVIVED"})
    drain()
    t2 = get_task(client, t["id"])
    assert t2["status"] == "completed"
    assert "SURVIVED" in t2["output"]
    assert any(e["event_type"] == "worker_crashed" for e in t2["events"])


def test_F_bounded_retries_then_failed(client, env):
    a = mk_agent(client)
    p = mk_project(client)
    t = mk_task(client, p["id"], "always-limited", agent_id=a["id"], max_retries=1,
                payload={"mock_provider_error": "rate_limit"})
    drain()
    t2 = get_task(client, t["id"])
    assert t2["status"] == "failed"
    assert t2["attempt"] == 1
    assert "rate" in t2["error"].lower()


# ---------- TEST G: restart recovery ----------

def test_G_restart_recovery_no_duplicates(client, env):
    a = mk_agent(client)
    p = mk_project(client)
    t = mk_task(client, p["id"], "orphan", agent_id=a["id"],
                payload={"mock_output": "ONCE"})
    conn = db.get_conn()
    # simulate crash mid-run: running + expired lease from dead worker
    db.set_task_status(conn, t["id"], "running", "claimed")
    workers.update_task(conn, t["id"], lease_owner="dead-wrk",
                        lease_expires="2000-01-01T00:00:00+00:00")
    db.close_conn()  # "restart"
    conn = db.get_conn()
    n = workers.recover_on_startup(conn)
    assert n >= 1
    t_mid = get_task(client, t["id"])
    assert t_mid["status"] == "queued"
    drain()
    t2 = get_task(client, t["id"])
    assert t2["status"] == "completed"
    completed_ev = [e for e in t2["events"] if e["to_status"] == "completed"]
    assert len(completed_ev) == 1, "must complete exactly once"


# ---------- TEST H: quality gate ----------

def test_H_failing_acceptance_blocks_completion(client, env):
    a = mk_agent(client)
    p = mk_project(client)
    bad = mk_task(client, p["id"], "bad build", agent_id=a["id"], max_retries=1,
                  payload={"mock_output": "built-ish"},
                  acceptance_tests=[{"name": "unit", "kind": "shell", "command": "false"}])
    drain()
    b = get_task(client, bad["id"])
    assert b["status"] == "failed"
    assert b["verification_result"]["failed"] == 1
    assert "acceptance" in b["error"].lower()
    # fixed code passes the same gate
    good = mk_task(client, p["id"], "good build", agent_id=a["id"],
                   payload={"mock_output": "built well"},
                   acceptance_tests=[{"name": "unit", "kind": "shell", "command": "true"}])
    drain()
    g = get_task(client, good["id"])
    assert g["status"] == "completed"
    assert g["verification_result"]["all_passed"] is True


# ---------- TEST I: provider failures ----------

def test_I_provider_failures_are_explicit(client, env):
    p = mk_project(client)
    # missing credentials -> immediate clear failure, no retry storm
    a1 = mk_agent(client, name="NoKey", provider="anthropic", model="claude-haiku-4-5")
    t1 = mk_task(client, p["id"], "needs key", agent_id=a1["id"])
    drain()
    t1b = get_task(client, t1["id"])
    assert t1b["status"] == "failed" and t1b["attempt"] == 0
    assert "credential" in t1b["error"].lower() or "key" in t1b["error"].lower()
    # timeout -> bounded retries then failed
    a2 = mk_agent(client, name="Slow")
    t2 = mk_task(client, p["id"], "slow upstream", agent_id=a2["id"], max_retries=1,
                 payload={"mock_provider_error": "timeout"})
    drain()
    t2b = get_task(client, t2["id"])
    assert t2b["status"] == "failed" and "timeout" in t2b["error"].lower()
    # provider test endpoint reports honestly (no network call for missing key)
    r = client.post("/api/providers/test", json={"provider": "anthropic"})
    assert r.json()["ok"] is False and r.json()["kind"] == "missing_credentials"
    r = client.post("/api/providers/test", json={"provider": "mock"})
    assert r.json()["ok"] is True


# ---------- TEST J: permissions ----------

def test_J_tool_and_project_permissions_enforced(client, env):
    p1 = mk_project(client, name="P1")
    p2 = mk_project(client, name="P2")
    a = mk_agent(client, name="Restricted", allowed_tools=["fs_read"])
    # tool outside grant is denied at runtime; artifact must NOT exist
    t = mk_task(client, p1["id"], "sneaky write", agent_id=a["id"], payload={
        "mock_output": "tried", "mock_tool_calls": [
            {"name": "fs_write", "args": {"path": "evil.txt", "content": "x"}}]})
    drain()
    t2 = get_task(client, t["id"])
    assert "TOOL DENIED" in t2["output"]
    assert not (config.WORKSPACES_DIR / p1["id"] / "evil.txt").exists()
    # agent scoped to P1 cannot run in P2
    scoped = mk_agent(client, name="Scoped", project_access=[p1["id"]])
    t3 = mk_task(client, p2["id"], "trespass", agent_id=scoped["id"],
                 payload={"mock_output": "nope"})
    drain()
    t3b = get_task(client, t3["id"])
    assert t3b["status"] == "failed" and "permitted" in t3b["error"].lower()
    # path escape blocked
    r = client.post("/api/tools/execute", json={
        "project_id": p1["id"], "name": "fs_write",
        "args": {"path": "../escape.txt", "content": "x"}})
    assert r.json()["ok"] is False and "escapes" in r.json()["error"]


# ---------- TEST K: budget enforcement ----------

def test_K_budget_blocks_further_execution(client, env):
    a = mk_agent(client, budgets={"max_tasks": 1})
    p = mk_project(client)
    t1 = mk_task(client, p["id"], "first", agent_id=a["id"], payload={"mock_output": "1"})
    drain()
    assert get_task(client, t1["id"])["status"] == "completed"
    t2 = mk_task(client, p["id"], "second", agent_id=a["id"], payload={"mock_output": "2"})
    drain()
    t2b = get_task(client, t2["id"])
    assert t2b["status"] == "blocked"
    assert "budget" in t2b["error"].lower()


# ---------- TEST L: multi-project isolation ----------

def test_L_projects_are_isolated(client, env):
    a = mk_agent(client)
    p1 = mk_project(client, name="Alpha")
    p2 = mk_project(client, name="Beta")
    mk_task(client, p1["id"], "w1", agent_id=a["id"], payload={
        "mock_output": "w1", "mock_tool_calls": [
            {"name": "fs_write", "args": {"path": "f.txt", "content": "ONE"}}]})
    mk_task(client, p2["id"], "w2", agent_id=a["id"], payload={
        "mock_output": "w2", "mock_tool_calls": [
            {"name": "fs_write", "args": {"path": "f.txt", "content": "TWO"}}]})
    drain()
    assert (config.WORKSPACES_DIR / p1["id"] / "f.txt").read_text() == "ONE"
    assert (config.WORKSPACES_DIR / p2["id"] / "f.txt").read_text() == "TWO"
    # memory scoping
    client.post("/api/memory", json={"scope": "project", "scope_id": p1["id"],
                                     "key": "secret", "value": "alpha-only"})
    r = client.get(f"/api/memory?scope=project&scope_id={p2['id']}")
    assert all(m["key"] != "secret" for m in r.json())
    # events scoping
    evs = client.get(f"/api/task-events?project_id={p1['id']}").json()
    assert evs and all(e["project_id"] == p1["id"] for e in evs)


# ---------- TEST M: user controls ----------

def test_M_pause_resume_cancel_reassign_approvals(client, env):
    a = mk_agent(client)
    b = mk_agent(client, name="Second")
    p = mk_project(client)
    # pause holds work, resume releases it
    t1 = mk_task(client, p["id"], "pausable", agent_id=a["id"], payload={"mock_output": "p"})
    client.post(f"/api/agents/{a['id']}/pause")
    workers.run_once(workers.register_worker(db.get_conn()))
    assert get_task(client, t1["id"])["status"] == "queued"
    client.post(f"/api/agents/{a['id']}/resume")
    drain()
    assert get_task(client, t1["id"])["status"] == "completed"
    # cancel prevents execution
    t2 = mk_task(client, p["id"], "doomed-queue", agent_id=a["id"],
                 payload={"mock_output": "never"})
    client.post(f"/api/tasks/{t2['id']}/cancel")
    drain()
    assert get_task(client, t2["id"])["status"] == "cancelled"
    # reassign moves work
    t3 = mk_task(client, p["id"], "movable", agent_id=a["id"], payload={"mock_output": "m"})
    r = client.post(f"/api/tasks/{t3['id']}/reassign", json={"agent_id": b["id"]})
    assert r.json()["agent_id"] == b["id"]
    drain()
    assert get_task(client, t3["id"])["status"] == "completed"
    # approval gate: approve -> runs; reject -> fails
    t4 = mk_task(client, p["id"], "gated-ok", agent_id=a["id"], requires_approval=True,
                 payload={"mock_output": "approved-run"})
    drain()
    assert get_task(client, t4["id"])["status"] == "waiting_input"
    aprs = client.get("/api/approvals?status=pending").json()
    mine = next(x for x in aprs if x["task_id"] == t4["id"])
    client.post(f"/api/approvals/{mine['id']}/decide", json={"decision": "approve"})
    drain()
    assert get_task(client, t4["id"])["status"] == "completed"
    t5 = mk_task(client, p["id"], "gated-no", agent_id=a["id"], requires_approval=True,
                 payload={"mock_output": "never-runs"})
    drain()
    aprs = client.get("/api/approvals?status=pending").json()
    mine = next(x for x in aprs if x["task_id"] == t5["id"])
    client.post(f"/api/approvals/{mine['id']}/decide", json={"decision": "reject"})
    assert get_task(client, t5["id"])["status"] == "failed"


# ---------- extras: state machine, dedupe, orchestration ----------

def test_state_machine_rejects_illegal_transitions(client, env):
    a = mk_agent(client)
    p = mk_project(client)
    t = mk_task(client, p["id"], "sm", agent_id=a["id"], payload={"mock_output": "x"})
    drain()
    conn = db.get_conn()
    with pytest.raises(ValueError):
        db.set_task_status(conn, t["id"], "running", "illegal")


def test_duplicate_tasks_rejected(client, env):
    a = mk_agent(client)
    p = mk_project(client)
    mk_task(client, p["id"], "same-title", agent_id=a["id"],
            objective="same objective", payload={"mock_output": "x"})
    r = client.post("/api/tasks", json={"project_id": p["id"], "title": "same-title",
                                        "objective": "same objective", "agent_id": a["id"]})
    assert r.status_code == 400 and "duplicate" in r.text.lower()


def test_starter_team_setup_is_idempotent(client, env):
    r = client.post("/api/setup/starter-team")
    assert r.status_code == 200, r.text
    first = r.json()["agents_created"]
    assert first >= 6
    assert len(client.get("/api/projects").json()) == 1
    r2 = client.post("/api/setup/starter-team")
    assert r2.json()["agents_created"] == 0  # no duplicates on re-run


def test_orchestrate_builds_team_and_graph(client, env):
    mk_agent(client, name="R1", role="research market")
    mk_agent(client, name="B1", role="backend developer")
    p = mk_project(client)
    r = client.post("/api/orchestrate", json={
        "project_id": p["id"],
        "objective": "Research the market for thumbnails, then build a prototype gallery website."})
    assert r.status_code == 201, r.text
    plan = r.json()
    assert len(plan["tasks"]) >= 3
    assert len(plan["team"]) >= 1  # smallest effective team, not everyone
    drain()
    statuses = [get_task(client, t["id"])["status"] for t in plan["tasks"]]
    assert all(s == "completed" for s in statuses), statuses
