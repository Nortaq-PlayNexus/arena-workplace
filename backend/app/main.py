"""FastAPI application: API + realtime events + static frontend."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import (FileResponse, JSONResponse, StreamingResponse)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, db, memory as mem, orchestrator as orch, workers
from .events import recent as recent_events
from .providers import get_provider
from .providers.base import ProviderError
from .security import check_auth, request_approval
from .templates import AGENT_TEMPLATES, get_template
from .tools.registry import REGISTRY, execute_tool, tool_spec_list


# ---------- lifespan ----------

@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = db.get_conn()
    db.init_db(conn)
    # seed default provider configs once
    if conn.execute("SELECT COUNT(*) c FROM provider_configs").fetchone()["c"] == 0:
        for i, (p, m) in enumerate([("mock", "mock-small"),
                                    ("openai_compat", "gpt-4o-mini"),
                                    ("anthropic", "claude-haiku-4-5"),
                                    ("gemini", "gemini-2.0-flash")]):
            conn.execute(
                "INSERT INTO provider_configs(id,provider,model,capabilities,is_default,created_at)"
                " VALUES(?,?,?,?,?,?)",
                (db.new_id("prv"), p, m, db.jdumps(config.PROVIDER_CAPABILITIES.get(p, {})),
                 1 if p == config.DEFAULT_PROVIDER else 0, db.now_iso()))
        conn.commit()
    if not config.DISABLE_WORKERS:
        await workers.start_workers()
    yield
    if not config.DISABLE_WORKERS:
        await workers.stop_workers()
    db.close_conn()


app = FastAPI(title="Autonomous AI Workplace", version="0.1.0", lifespan=lifespan)
Auth = Depends(check_auth)


# ---------- helpers ----------

def _agent_row(conn, aid: str) -> dict:
    row = conn.execute("SELECT * FROM agents WHERE id=?", (aid,)).fetchone()
    if row is None:
        raise HTTPException(404, "agent not found")
    return db.row_to_dict(row)


def _project_row(conn, pid: str) -> dict:
    row = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    if row is None:
        raise HTTPException(404, "project not found")
    return db.row_to_dict(row)


def _task_row(conn, tid: str) -> dict:
    try:
        return db.get_task(conn, tid)
    except ValueError:
        raise HTTPException(404, "task not found")


def _decode_task(t: dict) -> dict:
    for k in ("payload", "deps", "progress_detail", "artifacts", "acceptance_tests",
              "verification_result"):
        t[k] = db.jloads(t.get(k), [] if k in ("deps", "artifacts", "acceptance_tests") else {})
    return t


def _decode_agent(a: dict) -> dict:
    for k in ("allowed_tools", "allowed_paths", "project_access"):
        a[k] = db.jloads(a.get(k), [])
    for k in ("budgets", "retry_policy", "memory_config"):
        a[k] = db.jloads(a.get(k), {})
    return a


def _decode_project(p: dict) -> dict:
    p["kpis"] = db.jloads(p.get("kpis"), [])
    p["budgets"] = db.jloads(p.get("budgets"), {})
    p["team"] = db.jloads(p.get("team"), [])
    return p


# ---------- health & stats ----------

@app.get("/health")
def health():
    conn = db.get_conn()
    try:
        conn.execute("SELECT 1").fetchone()
        dbo = "ok"
    except Exception as e:
        dbo = f"error: {e}"
    w = conn.execute("SELECT COUNT(*) c FROM workers WHERE status='alive'").fetchone()["c"]
    return {"status": "ok", "version": "0.1.0", "db": dbo, "time": db.now_iso(),
            "workers_alive": w, "auth_required": bool(config.WORKPLACE_TOKEN),
            "autonomy_mode": config.AUTONOMY_MODE, "default_provider": config.DEFAULT_PROVIDER}


@app.get("/api/auth-status")
def auth_status():
    return {"auth_required": bool(config.WORKPLACE_TOKEN),
            "autonomy_mode": config.AUTONOMY_MODE}


@app.get("/api/stats")
def stats(_: str = Auth):
    conn = db.get_conn()
    q = lambda s, p=(): conn.execute(s, p).fetchone()[0]
    by_status = {r["status"]: r["c"] for r in conn.execute(
        "SELECT status, COUNT(*) c FROM tasks GROUP BY status").fetchall()}
    agents = {r["status"]: r["c"] for r in conn.execute(
        "SELECT status, COUNT(*) c FROM agents GROUP BY status").fetchall()}
    working = q("SELECT COUNT(*) FROM tasks WHERE status IN ('running','verification_running','assigned')")
    usage = conn.execute("SELECT COALESCE(SUM(tokens_in+tokens_out),0) t,"
                         " COALESCE(SUM(cost_usd),0) c FROM usage").fetchone()
    return {
        "agents_total": sum(agents.values()), "agents_by_status": agents,
        "agents_working": q("SELECT COUNT(DISTINCT agent_id) FROM tasks WHERE status IN"
                            " ('running','verification_running') AND agent_id IS NOT NULL"),
        "projects_active": q("SELECT COUNT(*) FROM projects WHERE status='active'"),
        "projects_total": q("SELECT COUNT(*) FROM projects"),
        "tasks_by_status": by_status, "tasks_running": working,
        "tasks_completed": by_status.get("completed", 0),
        "approvals_pending": q("SELECT COUNT(*) FROM approvals WHERE status='pending'"),
        "tokens_total": usage["t"], "cost_usd_total": round(usage["c"], 6),
        "workers_alive": q("SELECT COUNT(*) FROM workers WHERE status='alive'"),
    }


# ---------- agents ----------

class AgentCreate(BaseModel):
    name: str
    avatar: str = "🤖"
    role: str = ""
    specialty: str = ""
    team: str = "general"
    system_instructions: str = ""
    provider: str = "mock"
    model: str = "mock-small"
    allowed_tools: list[str] = Field(default_factory=list)
    max_concurrent: int = 2
    budgets: dict = Field(default_factory=dict)
    approval_required: bool = False
    project_access: list[str] = Field(default_factory=list)
    template: Optional[str] = None


@app.get("/api/agents")
def list_agents(_: str = Auth):
    conn = db.get_conn()
    return [_decode_agent(db.row_to_dict(r)) for r in
            conn.execute("SELECT * FROM agents ORDER BY created_at").fetchall()]


@app.get("/api/agent-templates")
def agent_templates(_: str = Auth):
    return AGENT_TEMPLATES


@app.post("/api/agents", status_code=201)
def create_agent(body: AgentCreate, _s: str = Auth):
    if body.template:
        try:
            t = get_template(body.template)
        except ValueError as e:
            raise HTTPException(400, str(e))
        data = {"name": t["name"], "avatar": t["avatar"], "role": t["role"],
                "specialty": t["specialty"], "team": t["team"],
                "system_instructions": t["instructions"],
                "allowed_tools": t["tools"]}
        if body.name != t["name"]:
            data["name"] = body.name
        body = AgentCreate(**{**body.model_dump(), **data})
    conn = db.get_conn()
    aid = db.new_id("agt")
    conn.execute(
        """INSERT INTO agents(id,name,avatar,role,specialty,team,system_instructions,
           provider,model,allowed_tools,max_concurrent,budgets,approval_required,
           project_access,status,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'active',?,?)""",
        (aid, body.name, body.avatar, body.role, body.specialty, body.team,
         body.system_instructions, body.provider, body.model,
         db.jdumps(body.allowed_tools), body.max_concurrent, db.jdumps(body.budgets),
         1 if body.approval_required else 0, db.jdumps(body.project_access),
         db.now_iso(), db.now_iso()))
    conn.commit()
    db.audit(conn, "user", "agent.create", "agent", aid, {"name": body.name})
    return _decode_agent(_agent_row(conn, aid))


@app.get("/api/agents/{aid}")
def get_agent(aid: str, _: str = Auth):
    conn = db.get_conn()
    a = _decode_agent(_agent_row(conn, aid))
    tasks = [db.row_to_dict(r) for r in conn.execute(
        "SELECT * FROM tasks WHERE agent_id=? ORDER BY updated_at DESC LIMIT 20", (aid,)).fetchall()]
    a["recent_tasks"] = [_decode_task(t) for t in tasks]
    a["live"] = conn.execute(
        "SELECT * FROM tasks WHERE agent_id=? AND status IN"
        " ('assigned','running','verification_running','review_required','waiting_input')"
        " ORDER BY updated_at DESC LIMIT 5", (aid,)).fetchall()
    a["live"] = [_decode_task(db.row_to_dict(r)) for r in a["live"]]
    u = conn.execute("SELECT COALESCE(SUM(tokens_in+tokens_out),0) t, COALESCE(SUM(cost_usd),0) c,"
                     " COUNT(*) n FROM usage WHERE agent_id=?", (aid,)).fetchone()
    a["usage"] = {"tokens": u["t"], "cost_usd": u["c"], "tasks": u["n"]}
    return a


class AgentPatch(BaseModel):
    name: Optional[str] = None
    system_instructions: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    allowed_tools: Optional[list[str]] = None
    max_concurrent: Optional[int] = None
    budgets: Optional[dict] = None
    status: Optional[str] = None
    project_access: Optional[list[str]] = None


@app.patch("/api/agents/{aid}")
def patch_agent(aid: str, body: AgentPatch, _: str = Auth):
    conn = db.get_conn()
    _agent_row(conn, aid)
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if "status" in fields and fields["status"] not in ("active", "paused", "disabled"):
        raise HTTPException(400, "bad status")
    for k in ("allowed_tools", "budgets", "project_access"):
        if k in fields:
            fields[k] = db.jdumps(fields[k])
    if fields:
        fields["updated_at"] = db.now_iso()
        cols = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE agents SET {cols} WHERE id=?", (*fields.values(), aid))
        conn.commit()
    return _decode_agent(_agent_row(conn, aid))


@app.post("/api/agents/{aid}/pause")
def pause_agent(aid: str, _: str = Auth):
    return patch_agent(aid, AgentPatch(status="paused"))


@app.post("/api/agents/{aid}/resume")
def resume_agent(aid: str, _: str = Auth):
    return patch_agent(aid, AgentPatch(status="active"))


@app.post("/api/agents/{aid}/stop")
def stop_agent(aid: str, _: str = Auth):
    """Pause agent + cancel its active tasks (safe interrupt)."""
    conn = db.get_conn()
    _agent_row(conn, aid)
    conn.execute("UPDATE agents SET status='paused', updated_at=? WHERE id=?", (db.now_iso(), aid))
    n = 0
    for r in conn.execute("SELECT id, status FROM tasks WHERE agent_id=? AND status IN"
                          " ('queued','assigned','running','verification_running','waiting_input','blocked')",
                          (aid,)).fetchall():
        try:
            db.set_task_status(conn, r["id"], "cancelled", f"stopped with agent {aid}")
            n += 1
        except ValueError:
            pass
    conn.commit()
    return {"agent_id": aid, "status": "paused", "tasks_cancelled": n}


@app.delete("/api/agents/{aid}")
def delete_agent(aid: str, _: str = Auth):
    conn = db.get_conn()
    active = conn.execute("SELECT COUNT(*) c FROM tasks WHERE agent_id=? AND status IN"
                          " ('queued','assigned','running','verification_running')", (aid,)).fetchone()["c"]
    if active:
        raise HTTPException(409, f"agent has {active} active task(s); stop it first")
    conn.execute("UPDATE tasks SET agent_id=NULL WHERE agent_id=?", (aid,))
    conn.execute("DELETE FROM agents WHERE id=?", (aid,))
    conn.commit()
    return {"deleted": aid}


# ---------- projects ----------

class ProjectCreate(BaseModel):
    name: str
    type: str = "project"
    description: str = ""
    objectives: str = ""
    kpis: list = Field(default_factory=list)
    budgets: dict = Field(default_factory=dict)
    operating_rules: str = ""


@app.get("/api/projects")
def list_projects(_: str = Auth):
    conn = db.get_conn()
    out = []
    for r in conn.execute("SELECT * FROM projects ORDER BY created_at").fetchall():
        p = _decode_project(db.row_to_dict(r))
        p["task_counts"] = {x["status"]: x["c"] for x in conn.execute(
            "SELECT status, COUNT(*) c FROM tasks WHERE project_id=? GROUP BY status",
            (p["id"],)).fetchall()}
        out.append(p)
    return out


@app.post("/api/projects", status_code=201)
def create_project(body: ProjectCreate, _: str = Auth):
    if body.type not in ("project", "business"):
        raise HTTPException(400, "type must be project|business")
    conn = db.get_conn()
    pid = db.new_id("prj")
    conn.execute(
        "INSERT INTO projects(id,name,type,description,objectives,kpis,budgets,operating_rules,"
        " status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?, 'active',?,?)",
        (pid, body.name, body.type, body.description, body.objectives,
         db.jdumps(body.kpis), db.jdumps(body.budgets), body.operating_rules,
         db.now_iso(), db.now_iso()))
    conn.commit()
    (config.WORKSPACES_DIR / pid).mkdir(parents=True, exist_ok=True)
    db.log_event(conn, "project_created", f"project created: {body.name}", project_id=pid)
    return _decode_project(_project_row(conn, pid))


@app.get("/api/projects/{pid}")
def get_project(pid: str, _: str = Auth):
    conn = db.get_conn()
    p = _decode_project(_project_row(conn, pid))
    p["tasks"] = [_decode_task(db.row_to_dict(r)) for r in conn.execute(
        "SELECT * FROM tasks WHERE project_id=? ORDER BY created_at", (pid,)).fetchall()]
    p["artifacts"] = [db.row_to_dict(r) for r in conn.execute(
        "SELECT * FROM artifacts WHERE project_id=? ORDER BY created_at DESC", (pid,)).fetchall()]
    return p


@app.post("/api/projects/{pid}/pause")
def pause_project(pid: str, _: str = Auth):
    conn = db.get_conn()
    _project_row(conn, pid)
    conn.execute("UPDATE projects SET status='paused', updated_at=? WHERE id=?", (db.now_iso(), pid))
    conn.commit()
    return {"id": pid, "status": "paused"}


@app.post("/api/projects/{pid}/resume")
def resume_project(pid: str, _: str = Auth):
    conn = db.get_conn()
    _project_row(conn, pid)
    conn.execute("UPDATE projects SET status='active', updated_at=? WHERE id=?", (db.now_iso(), pid))
    conn.commit()
    return {"id": pid, "status": "active"}


@app.post("/api/projects/{pid}/archive")
def archive_project(pid: str, _: str = Auth):
    conn = db.get_conn()
    _project_row(conn, pid)
    conn.execute("UPDATE projects SET status='archived', updated_at=? WHERE id=?", (db.now_iso(), pid))
    conn.commit()
    return {"id": pid, "status": "archived"}


@app.delete("/api/projects/{pid}")
def delete_project(pid: str, confirm: str = "", _: str = Auth):
    if confirm != "DELETE":
        raise HTTPException(400, "pass ?confirm=DELETE to delete a project and its tasks")
    conn = db.get_conn()
    _project_row(conn, pid)
    conn.execute("DELETE FROM projects WHERE id=?", (pid,))
    conn.commit()
    return {"deleted": pid}


@app.get("/api/projects/{pid}/export")
def export_project(pid: str, _: str = Auth):
    conn = db.get_conn()
    p = _decode_project(_project_row(conn, pid))
    p["tasks"] = [_decode_task(db.row_to_dict(r)) for r in conn.execute(
        "SELECT * FROM tasks WHERE project_id=?", (pid,)).fetchall()]
    p["events"] = [db.row_to_dict(r) for r in conn.execute(
        "SELECT * FROM task_events WHERE project_id=? ORDER BY id", (pid,)).fetchall()]
    p["memories"] = [db.row_to_dict(r) for r in conn.execute(
        "SELECT * FROM memories WHERE scope IN ('project','business') AND scope_id=?", (pid,)).fetchall()]
    return p


# ---------- tasks ----------

class TaskCreate(BaseModel):
    project_id: str
    title: str
    objective: str = ""
    kind: str = "general"
    instructions: str = ""
    payload: dict = Field(default_factory=dict)
    agent_id: Optional[str] = None
    deps: list[str] = Field(default_factory=list)
    priority: int = 5
    max_retries: int = 3
    timeout_seconds: int = 600
    acceptance_tests: list = Field(default_factory=list)
    review_required: bool = False
    requires_approval: bool = False
    auto_assign: bool = True


@app.get("/api/tasks")
def list_tasks(project_id: Optional[str] = None, agent_id: Optional[str] = None,
               status: Optional[str] = None, limit: int = 200, _: str = Auth):
    conn = db.get_conn()
    sql, params = "SELECT * FROM tasks WHERE 1=1", []
    if project_id:
        sql += " AND project_id=?"; params.append(project_id)
    if agent_id:
        sql += " AND agent_id=?"; params.append(agent_id)
    if status:
        sql += " AND status=?"; params.append(status)
    sql += " ORDER BY created_at DESC LIMIT ?"; params.append(limit)
    return [_decode_task(db.row_to_dict(r)) for r in conn.execute(sql, params).fetchall()]


@app.post("/api/tasks", status_code=201)
def create_task(body: TaskCreate, _: str = Auth):
    conn = db.get_conn()
    _project_row(conn, body.project_id)
    try:
        t = orch.create_task(conn, body.project_id, body.title, body.objective,
                             body.kind, body.instructions, body.payload, body.agent_id,
                             body.deps, body.priority, body.max_retries,
                             body.timeout_seconds, body.acceptance_tests,
                             body.review_required, body.requires_approval,
                             auto_assign=body.auto_assign)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _decode_task(t)


@app.get("/api/tasks/{tid}")
def get_task(tid: str, _: str = Auth):
    conn = db.get_conn()
    t = _decode_task(_task_row(conn, tid))
    t["events"] = [db.row_to_dict(r) for r in conn.execute(
        "SELECT * FROM task_events WHERE task_id=? ORDER BY id", (tid,)).fetchall()]
    t["artifacts"] = [db.row_to_dict(r) for r in conn.execute(
        "SELECT * FROM artifacts WHERE task_id=?", (tid,)).fetchall()]
    return t


@app.post("/api/tasks/{tid}/cancel")
def cancel_task(tid: str, _: str = Auth):
    conn = db.get_conn()
    t = _task_row(conn, tid)
    if t["status"] in db.TERMINAL:
        raise HTTPException(409, f"task already {t['status']}")
    db.set_task_status(conn, tid, "cancelled", "cancelled by user")
    workers.update_task(conn, tid, lease_owner=None, lease_expires=None)
    return _decode_task(_task_row(conn, tid))


@app.post("/api/tasks/{tid}/retry")
def retry_task(tid: str, _: str = Auth):
    conn = db.get_conn()
    t = _task_row(conn, tid)
    if t["status"] not in ("failed", "cancelled", "blocked"):
        raise HTTPException(409, f"only failed/cancelled/blocked tasks can retry (is {t['status']})")
    workers.update_task(conn, tid, attempt=0, error="", next_retry_at=None,
                        lease_owner=None, lease_expires=None)
    db.set_task_status(conn, tid, "queued", "manual retry by user")
    return _decode_task(_task_row(conn, tid))


class ReassignBody(BaseModel):
    agent_id: str


@app.post("/api/tasks/{tid}/reassign")
def reassign_task(tid: str, body: ReassignBody, _: str = Auth):
    conn = db.get_conn()
    t = _task_row(conn, tid)
    _agent_row(conn, body.agent_id)
    if t["status"] in ("running", "verification_running"):
        raise HTTPException(409, "cancel a running task before reassigning")
    if t["status"] in db.TERMINAL:
        raise HTTPException(409, f"task already {t['status']}")
    workers.update_task(conn, tid, agent_id=body.agent_id)
    db.log_event(conn, "reassigned", f"reassigned to {body.agent_id}", task_id=tid,
                 project_id=t["project_id"], agent_id=body.agent_id)
    return _decode_task(_task_row(conn, tid))


class ReviewBody(BaseModel):
    decision: str  # approve | reject | changes
    note: str = ""


@app.post("/api/tasks/{tid}/review")
def review_task(tid: str, body: ReviewBody, _: str = Auth):
    conn = db.get_conn()
    t = _task_row(conn, tid)
    if t["status"] != "review_required":
        raise HTTPException(409, f"task is not awaiting review (is {t['status']})")
    if body.decision == "approve":
        workers.update_task(conn, tid, progress=1.0)
        db.set_task_status(conn, tid, "completed", f"review approved: {body.note}"[:300])
    elif body.decision == "reject":
        workers.update_task(conn, tid, error=body.note[:1000])
        db.set_task_status(conn, tid, "failed", f"review rejected: {body.note}"[:300])
    elif body.decision == "changes":
        workers.update_task(conn, tid, instructions=(t.get("instructions") or "") +
                            f"\n\nREVIEWER FEEDBACK: {body.note}")
        db.set_task_status(conn, tid, "queued", "reviewer requested changes")
    else:
        raise HTTPException(400, "decision must be approve|reject|changes")
    return _decode_task(_task_row(conn, tid))


# ---------- orchestration ----------

class OrchestrateBody(BaseModel):
    project_id: str
    objective: str
    mode: str = "supervised"
    tasks: Optional[list[dict]] = None
    payload_defaults: dict = Field(default_factory=dict)


@app.post("/api/orchestrate", status_code=201)
def orchestrate(body: OrchestrateBody, _: str = Auth):
    conn = db.get_conn()
    try:
        plan = orch.orchestrate(conn, body.project_id, body.objective, body.mode,
                                body.tasks, body.payload_defaults)
    except ValueError as e:
        raise HTTPException(400, str(e))
    plan["tasks"] = [_decode_task(t) for t in plan["tasks"]]
    return plan


# ---------- events / SSE ----------

@app.get("/api/task-events")
def task_events(since: int = 0, project_id: Optional[str] = None, limit: int = 200,
                _: str = Auth):
    return recent_events(db.get_conn(), since, project_id, limit)


@app.get("/api/events")
async def sse(since: int = 0, project_id: Optional[str] = None):
    async def gen():
        last = since
        yield ": connected\n\n"
        while True:
            try:
                evs = await asyncio.to_thread(recent_events, db.get_conn(), last,
                                              project_id, 100)
            except Exception:
                evs = []
            for e in evs:
                last = max(last, e["id"])
                yield f"data: {json.dumps(e, default=str)}\n\n"
            yield ": hb\n\n"
            await asyncio.sleep(1.0)
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------- workers / audit / usage ----------

@app.get("/api/workers")
def list_workers(_: str = Auth):
    conn = db.get_conn()
    return [db.row_to_dict(r) for r in
            conn.execute("SELECT * FROM workers ORDER BY started_at DESC").fetchall()]


@app.get("/api/audit")
def list_audit(limit: int = 200, _: str = Auth):
    conn = db.get_conn()
    return [db.row_to_dict(r) for r in conn.execute(
        "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]


@app.get("/api/usage")
def usage_summary(project_id: Optional[str] = None, _: str = Auth):
    conn = db.get_conn()
    sql = ("SELECT agent_id, project_id, provider, model, SUM(tokens_in+tokens_out) tokens,"
           " SUM(cost_usd) cost, COUNT(*) tasks FROM usage")
    params: list = []
    if project_id:
        sql += " WHERE project_id=?"; params.append(project_id)
    sql += " GROUP BY agent_id, project_id, provider, model ORDER BY cost DESC"
    return [db.row_to_dict(r) for r in conn.execute(sql, params).fetchall()]


# ---------- approvals ----------

@app.get("/api/approvals")
def list_approvals(status: Optional[str] = None, _: str = Auth):
    conn = db.get_conn()
    sql, params = "SELECT * FROM approvals", []
    if status:
        sql += " WHERE status=?"; params.append(status)
    sql += " ORDER BY requested_at DESC"
    out = []
    for r in conn.execute(sql, params).fetchall():
        a = db.row_to_dict(r)
        a["detail"] = db.jloads(a.get("detail"), {})
        out.append(a)
    return out


class DecideBody(BaseModel):
    decision: str  # approve | reject
    decided_by: str = "user"


@app.post("/api/approvals/{aid}/decide")
def decide_approval(aid: str, body: DecideBody, _: str = Auth):
    if body.decision not in ("approve", "reject"):
        raise HTTPException(400, "decision must be approve|reject")
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM approvals WHERE id=?", (aid,)).fetchone()
    if row is None:
        raise HTTPException(404, "approval not found")
    ap = db.row_to_dict(row)
    if ap["status"] != "pending":
        raise HTTPException(409, f"already {ap['status']}")
    status = "approved" if body.decision == "approve" else "rejected"
    conn.execute("UPDATE approvals SET status=?, decided_at=?, decided_by=? WHERE id=?",
                 (status, db.now_iso(), body.decided_by, aid))
    conn.commit()
    if ap.get("task_id"):
        try:
            t = db.get_task(conn, ap["task_id"])
        except ValueError:
            t = None
        if t and t["status"] == "waiting_input":
            if status == "approved":
                workers.update_task(conn, t["id"], approved=1)
                db.set_task_status(conn, t["id"], "queued", f"approved by {body.decided_by}")
                # record tool-level approval for consequential tools
                if str(ap.get("action", "")).startswith("tool:"):
                    p = db.jloads(t.get("payload"), {})
                    p["_tool_approved"] = ap["action"]
                    workers.update_task(conn, t["id"], payload=db.jdumps(p))
            else:
                workers.update_task(conn, t["id"], error=f"rejected by {body.decided_by}")
                db.set_task_status(conn, t["id"], "failed", f"rejected by {body.decided_by}")
    db.log_event(conn, f"approval_{status}", f"{ap['action']} {status} by {body.decided_by}",
                 task_id=ap.get("task_id"), project_id=ap["project_id"],
                 agent_id=ap.get("agent_id"))
    row = conn.execute("SELECT * FROM approvals WHERE id=?", (aid,)).fetchone()
    return db.row_to_dict(row)


# ---------- memory ----------

class MemoryCreate(BaseModel):
    scope: str
    scope_id: str = ""
    type: str = "fact"
    key: str
    value: str = ""
    confidence: Optional[float] = None
    verified: bool = False
    origin: str = ""


@app.get("/api/memory")
def list_memory(scope: Optional[str] = None, scope_id: Optional[str] = None,
                query: str = "", limit: int = 100, _: str = Auth):
    return mem.search(db.get_conn(), scope, scope_id or None, query, None, limit)


@app.post("/api/memory", status_code=201)
def create_memory(body: MemoryCreate, _: str = Auth):
    try:
        return mem.put(db.get_conn(), body.scope, body.key, body.value, body.scope_id,
                       body.type, body.confidence, body.verified, body.origin)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/memory/{mid}")
def delete_memory(mid: str, _: str = Auth):
    conn = db.get_conn()
    if conn.execute("SELECT id FROM memories WHERE id=?", (mid,)).fetchone() is None:
        raise HTTPException(404, "memory not found")
    conn.execute("DELETE FROM memories WHERE id=?", (mid,))
    conn.commit()
    return {"deleted": mid}


# ---------- providers ----------

@app.get("/api/providers")
def list_providers(_: str = Auth):
    conn = db.get_conn()
    rows = [db.row_to_dict(r) for r in conn.execute("SELECT * FROM provider_configs").fetchall()]
    key_present = {"mock": True, "openai_compat": bool(config.OPENAI_API_KEY),
                   "anthropic": bool(config.ANTHROPIC_API_KEY),
                   "gemini": bool(config.GEMINI_API_KEY)}
    for r in rows:
        r["capabilities"] = db.jloads(r.get("capabilities"), {})
        r["fallback_chain"] = db.jloads(r.get("fallback_chain"), {})
        r["key_configured"] = key_present.get(r["provider"], False)
        r["note"] = "keys are read from environment only; never stored or returned"
    return rows


class ProviderPatch(BaseModel):
    model: Optional[str] = None
    endpoint: Optional[str] = None
    is_default: Optional[bool] = None
    timeout_s: Optional[int] = None


@app.patch("/api/providers/{pid}")
def patch_provider(pid: str, body: ProviderPatch, _: str = Auth):
    conn = db.get_conn()
    if conn.execute("SELECT id FROM provider_configs WHERE id=?", (pid,)).fetchone() is None:
        raise HTTPException(404, "provider config not found")
    if body.is_default:
        conn.execute("UPDATE provider_configs SET is_default=0")
    fields = {k: v for k, v in body.model_dump().items()
              if v is not None and k != "is_default"}
    if body.is_default is not None:
        fields["is_default"] = 1 if body.is_default else 0
    if fields:
        cols = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE provider_configs SET {cols} WHERE id=?", (*fields.values(), pid))
        conn.commit()
    row = conn.execute("SELECT * FROM provider_configs WHERE id=?", (pid,)).fetchone()
    return db.row_to_dict(row)


class ProviderTest(BaseModel):
    provider: str
    model: str = ""


@app.post("/api/providers/test")
def test_provider(body: ProviderTest, _: str = Auth):
    try:
        p = get_provider(body.provider, body.model)
        r = p.chat([{"role": "user", "content": "Reply with the single word: OK"}])
        return {"ok": True, "provider": body.provider,
                "reply_preview": r.content[:200], "tokens_in": r.tokens_in,
                "tokens_out": r.tokens_out}
    except ProviderError as e:
        return JSONResponse({"ok": False, "provider": body.provider,
                             "kind": e.kind, "error": str(e)}, status_code=200)
    except Exception as e:
        return JSONResponse({"ok": False, "provider": body.provider,
                             "kind": "exception", "error": str(e)}, status_code=200)


# ---------- tools ----------

@app.get("/api/tools")
def list_tools(_: str = Auth):
    return tool_spec_list()


class ToolExecBody(BaseModel):
    project_id: str
    agent_id: Optional[str] = None
    name: str
    args: dict = Field(default_factory=dict)


@app.post("/api/tools/execute")
def exec_tool(body: ToolExecBody, _: str = Auth):
    from .security import agent_allowed_tools as _allowed
    conn = db.get_conn()
    _project_row(conn, body.project_id)
    allowed = None
    if body.agent_id:
        a = _agent_row(conn, body.agent_id)
        allowed = _allowed(a)
    from .tools.registry import ToolDenied
    ctx = {"agent_id": body.agent_id or "user", "project_id": body.project_id,
           "task_id": None, "allowed_tools": allowed, "consequential_approved": False}
    try:
        r = execute_tool(body.name, body.args, ctx)
    except Exception as e:
        raise HTTPException(403, str(e))
    return {"ok": r.ok, "output": r.output, "error": r.error,
            "duration_ms": r.duration_ms, "untrusted": r.untrusted}


# ---------- artifacts ----------

@app.get("/api/artifacts")
def list_artifacts(project_id: Optional[str] = None, _: str = Auth):
    conn = db.get_conn()
    sql, params = "SELECT * FROM artifacts", []
    if project_id:
        sql += " WHERE project_id=?"; params.append(project_id)
    sql += " ORDER BY created_at DESC LIMIT 200"
    return [db.row_to_dict(r) for r in conn.execute(sql, params).fetchall()]


# ---------- starter setup ----------

STARTER_KEYS = ["orchestrator", "webres", "backend", "testeng", "reviewer", "writer"]


@app.post("/api/setup/starter-team")
def starter_team(_: str = Auth):
    conn = db.get_conn()
    made = []
    for key in STARTER_KEYS:
        t = get_template(key)
        if conn.execute("SELECT id FROM agents WHERE role=? AND specialty=?",
                        (t["role"], t["specialty"])).fetchone():
            continue
        aid = db.new_id("agt")
        cols = ["id", "name", "avatar", "role", "specialty", "team",
                "system_instructions", "provider", "model", "allowed_tools",
                "max_concurrent", "status", "created_at", "updated_at"]
        vals = [aid, t["name"], t["avatar"], t["role"], t["specialty"], t["team"],
                t["instructions"], config.DEFAULT_PROVIDER, config.DEFAULT_MODEL,
                db.jdumps(t["tools"]), 2, "active", db.now_iso(), db.now_iso()]
        assert len(cols) == len(vals)
        conn.execute(f"INSERT INTO agents({','.join(cols)})"
                     f" VALUES({','.join('?'*len(vals))})", vals)
        made.append(aid)
    if conn.execute("SELECT COUNT(*) c FROM projects").fetchone()["c"] == 0:
        pid = db.new_id("prj")
        conn.execute(
            "INSERT INTO projects(id,name,type,description,objectives,status,created_at,updated_at)"
            " VALUES(?, 'Demo project','project','Starter workspace',"
            " 'Try the objective box: research, build, or draft content.', 'active',?,?)",
            (pid, db.now_iso(), db.now_iso()))
        (config.WORKSPACES_DIR / pid).mkdir(parents=True, exist_ok=True)
    conn.commit()
    return {"agents_created": len(made), "agent_ids": made}


# ---------- frontend ----------

@app.get("/")
def index():
    idx = config.FRONTEND_DIR / "index.html"
    if not idx.exists():
        return JSONResponse({"detail": "frontend not built"}, status_code=503)
    return FileResponse(str(idx), media_type="text/html")


if config.FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(config.FRONTEND_DIR)), name="static")
