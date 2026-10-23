"""Auth, permissions, budgets, approvals helpers."""
from __future__ import annotations

import sqlite3
from typing import Optional

from fastapi import Header, HTTPException

from . import config, db

PRICE = config.PRICE_PER_1K


def check_auth(authorization: Optional[str] = Header(default=None)) -> str:
    """Optional token auth: enforced only when WORKPLACE_TOKEN is set (local MVP
    mode is open but the UI shows a warning banner)."""
    if not config.WORKPLACE_TOKEN:
        return "local-open-mode"
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    if authorization[7:] != config.WORKPLACE_TOKEN:
        raise HTTPException(status_code=403, detail="invalid token")
    return "admin"


def agent_can_access_project(agent: dict, project_id: str) -> bool:
    access = db.jloads(agent.get("project_access"), [])
    if not access:  # empty = all projects (default for MVP-created agents)
        return True
    return project_id in access


def agent_allowed_tools(agent: dict) -> Optional[list[str]]:
    allowed = db.jloads(agent.get("allowed_tools"), [])
    if not allowed:
        return None  # None = all non-consequential
    return allowed


def estimate_cost(provider: str, tokens_in: int, tokens_out: int) -> float:
    per1k = PRICE.get(provider, 0.002)
    return round((tokens_in + tokens_out) / 1000 * per1k, 6)


def budget_status(conn: sqlite3.Connection, agent_id: str, project_id: str) -> dict:
    """Aggregate usage vs budgets. Returns {ok, violations[], agent_usage, project_usage}."""
    agent = conn.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
    proj = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    ab = db.jloads(agent["budgets"], {}) if agent else {}
    pb = db.jloads(proj["budgets"], {}) if proj else {}
    au = conn.execute(
        "SELECT COALESCE(SUM(tokens_in+tokens_out),0) t, COALESCE(SUM(cost_usd),0) c,"
        " COALESCE(SUM(runtime_ms),0) r, COUNT(*) n FROM usage WHERE agent_id=?",
        (agent_id,)).fetchone()
    pu = conn.execute(
        "SELECT COALESCE(SUM(tokens_in+tokens_out),0) t, COALESCE(SUM(cost_usd),0) c,"
        " COALESCE(SUM(runtime_ms),0) r, COUNT(*) n FROM usage WHERE project_id=?",
        (project_id,)).fetchone()
    violations = []
    for scope, b, u in (("agent", ab, au), ("project", pb, pu)):
        if b.get("max_tokens") and u["t"] >= b["max_tokens"]:
            violations.append(f"{scope} token budget exceeded ({u['t']}/{b['max_tokens']})")
        if b.get("max_cost_usd") and u["c"] >= b["max_cost_usd"]:
            violations.append(f"{scope} cost budget exceeded (${u['c']:.4f}/${b['max_cost_usd']})")
        if b.get("max_tasks") and u["n"] >= b["max_tasks"]:
            violations.append(f"{scope} task budget exceeded ({u['n']}/{b['max_tasks']})")
        if b.get("max_runtime_ms") and u["r"] >= b["max_runtime_ms"]:
            violations.append(f"{scope} runtime budget exceeded")
    return {"ok": not violations, "violations": violations,
            "agent_usage": {"tokens": au["t"], "cost_usd": au["c"],
                            "runtime_ms": au["r"], "tasks": au["n"]},
            "project_usage": {"tokens": pu["t"], "cost_usd": pu["c"],
                              "runtime_ms": pu["r"], "tasks": pu["n"]}}


def request_approval(conn: sqlite3.Connection, project_id: str, action: str,
                     task_id: Optional[str] = None, agent_id: Optional[str] = None,
                     detail: Optional[dict] = None, risk: str = "consequential") -> dict:
    aid = db.new_id("apr")
    conn.execute(
        """INSERT INTO approvals(id,project_id,task_id,agent_id,action,detail,risk,
           status,requested_at) VALUES(?,?,?,?,?,?,?,'pending',?)""",
        (aid, project_id, task_id, agent_id, action, db.jdumps(detail or {}),
         risk, db.now_iso()))
    conn.commit()
    db.log_event(conn, "approval_requested", f"approval requested: {action}",
                 task_id=task_id, project_id=project_id, agent_id=agent_id,
                 data={"approval_id": aid, "action": action})
    row = conn.execute("SELECT * FROM approvals WHERE id=?", (aid,)).fetchone()
    return db.row_to_dict(row)
