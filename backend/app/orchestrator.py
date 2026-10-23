"""Central AI orchestrator: objective -> plan -> task graph -> assignment.

Deterministic heuristic planner (works offline with mock provider). An LLM
planner path is used only when explicitly enabled and a real provider is set.
"""
from __future__ import annotations

import sqlite3
from typing import Optional

from . import config, db

KEYWORD_ROLES = [
    (("research", "market", "competitor", "investigat", "survey", "literature"), "research"),
    (("data", "dataset", "statist", "analys", "metric"), "data"),
    (("frontend", "ui", "ux", "css", "react", "page", "website"), "frontend"),
    (("backend", "api", "server", "endpoint", "database", "schema"), "backend"),
    (("test", "qa", "quality", "verify", "validat"), "qa"),
    (("security", "audit", "permission", "vulnerab"), "security"),
    (("design", "graphic", "thumbnail", "image", "logo"), "creative"),
    (("content", "write", "blog", "copy", "seo", "marketing"), "content"),
    (("devops", "deploy", "docker", "ci", "pipeline"), "devops"),
    (("doc", "readme", "manual"), "docs"),
]


def _role_for_text(text: str) -> str:
    low = (text or "").lower()
    for keywords, role in KEYWORD_ROLES:
        if any(k in low for k in keywords):
            return role
    return "general"


def pick_agent(conn: sqlite3.Connection, project_id: str, role_hint: str,
               exclude: Optional[set[str]] = None) -> Optional[dict]:
    """Smallest effective team: prefer active agent whose role/specialty/team
    matches hint and has spare capacity; fall back to any active agent."""
    exclude = exclude or set()
    agents = [db.row_to_dict(r) for r in conn.execute(
        "SELECT * FROM agents WHERE status='active' ORDER BY created_at").fetchall()]
    if not agents:
        return None

    def load(aid: str) -> int:
        row = conn.execute(
            "SELECT COUNT(*) c FROM tasks WHERE agent_id=? AND status IN"
            " ('assigned','running','verification_running','review_required')",
            (aid,)).fetchone()
        return row["c"]

    def matches(a: dict) -> bool:
        blob = f"{a.get('role','')} {a.get('specialty','')} {a.get('team','')}".lower()
        return role_hint in blob

    cands = [a for a in agents if a["id"] not in exclude]
    scored = sorted(cands, key=lambda a: (not matches(a), load(a["id"])))
    for a in scored:
        access = db.jloads(a.get("project_access"), [])
        if access and project_id not in access:
            continue
        if load(a["id"]) < int(a.get("max_concurrent", 2) or 2):
            return a
    # all busy: return least-loaded matching agent anyway (queue will hold task)
    return scored[0] if scored else None


def decompose_objective(objective: str) -> list[dict]:
    """Heuristic work-breakdown. Returns [{title, kind, role_hint, depends_on_titles}]."""
    low = objective.lower()
    steps: list[dict] = []
    wants_research = any(k in low for k in
                         ("research", "market", "competitor", "investigat", "opportunit", "analys"))
    wants_build = any(k in low for k in
                      ("build", "prototype", "develop", "implement", "create", "website", "app", "code"))
    wants_content = any(k in low for k in
                        ("content", "write", "thumbnail", "design", "marketing", "launch plan", "report"))
    if wants_research:
        steps.append({"title": "Research & evidence gathering", "kind": "research",
                      "role_hint": "research", "depends_on_titles": []})
        steps.append({"title": "Analysis & synthesis", "kind": "research",
                      "role_hint": "data", "depends_on_titles": ["Research & evidence gathering"]})
    if wants_build:
        base = [s["title"] for s in steps]
        steps.append({"title": "Design & implementation plan", "kind": "code",
                      "role_hint": "backend", "depends_on_titles": base})
        steps.append({"title": "Build prototype", "kind": "code",
                      "role_hint": "backend",
                      "depends_on_titles": ["Design & implementation plan"]})
        steps.append({"title": "Automated tests & verification", "kind": "test",
                      "role_hint": "qa", "depends_on_titles": ["Build prototype"]})
    if wants_content:
        base = [s["title"] for s in steps]
        steps.append({"title": "Produce deliverable content", "kind": "content",
                      "role_hint": "content", "depends_on_titles": base})
    if not steps:
        steps.append({"title": "Execute objective", "kind": "general",
                      "role_hint": _role_for_text(objective), "depends_on_titles": []})
    steps.append({"title": "Independent review", "kind": "review", "role_hint": "qa",
                  "depends_on_titles": [s["title"] for s in steps]})
    return steps


def create_task(conn: sqlite3.Connection, project_id: str, title: str,
                objective: str = "", kind: str = "general",
                instructions: str = "", payload: Optional[dict] = None,
                agent_id: Optional[str] = None, deps: Optional[list[str]] = None,
                priority: int = 5, max_retries: int = 3,
                timeout_seconds: int = 600, acceptance_tests: Optional[list] = None,
                review_required: bool = False, requires_approval: bool = False,
                parent_task_id: Optional[str] = None, depth: int = 0,
                auto_assign: bool = True, enqueue: bool = True) -> dict:
    if depth > config.MAX_DELEGATION_DEPTH:
        raise ValueError("max delegation depth exceeded (loop guard)")
    dh = db.dedupe_hash(project_id, title, objective)
    dup = conn.execute(
        "SELECT id FROM tasks WHERE dedupe_hash=? AND status NOT IN"
        " ('failed','cancelled','completed')", (dh,)).fetchone()
    if dup:
        raise ValueError(f"duplicate task already active: {dup['id']}")
    if agent_id is None and auto_assign:
        agent = pick_agent(conn, project_id, _role_for_text(title + " " + objective))
        agent_id = agent["id"] if agent else None
    tid = db.new_id("tsk")
    cols = ["id", "project_id", "agent_id", "title", "objective", "kind",
            "instructions", "payload", "status", "priority", "deps", "attempt",
            "max_retries", "timeout_seconds", "acceptance_tests",
            "review_required", "requires_approval", "dedupe_hash",
            "parent_task_id", "depth", "created_at", "updated_at"]
    vals: list = [tid, project_id, agent_id, title, objective, kind, instructions,
                  db.jdumps(payload or {}), "draft", priority, db.jdumps(deps or []),
                  0, max_retries, timeout_seconds,
                  db.jdumps(acceptance_tests or []),
                  1 if review_required else 0, 1 if requires_approval else 0, dh,
                  parent_task_id, depth, db.now_iso(), db.now_iso()]
    assert len(cols) == len(vals), f"{len(cols)} cols vs {len(vals)} vals"
    conn.execute(f"INSERT INTO tasks({','.join(cols)}) VALUES({','.join('?'*len(vals))})", vals)
    conn.commit()
    db.set_task_status(conn, tid, "planned", f"task created: {title}")
    if enqueue:
        db.set_task_status(conn, tid, "queued", "enqueued for execution")
    return db.get_task(conn, tid)


def orchestrate(conn: sqlite3.Connection, project_id: str, objective: str,
                mode: str = "supervised", tasks_spec: Optional[list[dict]] = None,
                payload_defaults: Optional[dict] = None) -> dict:
    """Create a task graph for an objective. Returns plan summary."""
    proj = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    if proj is None:
        raise ValueError(f"project not found: {project_id}")
    existing = conn.execute(
        "SELECT COUNT(*) c FROM tasks WHERE project_id=? AND status NOT IN"
        " ('completed','failed','cancelled')", (project_id,)).fetchone()["c"]
    if existing >= config.MAX_TASKS_PER_OBJECTIVE:
        raise ValueError("project task cap reached (loop guard)")
    steps = tasks_spec or decompose_objective(objective)
    if len(steps) > config.MAX_TASKS_PER_OBJECTIVE:
        raise ValueError("plan exceeds max tasks per objective (loop guard)")
    id_by_title: dict[str, str] = {}
    created = []
    independent_review_id = None
    for i, s in enumerate(steps):
        title = s.get("title", f"step-{i+1}")
        dep_titles = s.get("depends_on_titles", []) or []
        dep_ids = [id_by_title[t] for t in dep_titles if t in id_by_title]
        dep_ids += s.get("depends_on", []) or []
        payload = dict(payload_defaults or {})
        payload.update(s.get("payload", {}) or {})
        agent_id = s.get("agent_id")
        if agent_id is None and s.get("role_hint"):
            a = pick_agent(conn, project_id, s["role_hint"],
                           exclude={independent_review_id} if False else None)
            agent_id = a["id"] if a else None
        t = create_task(conn, project_id, title,
                        objective=f"{objective}\n\nStep: {title}",
                        kind=s.get("kind", "general"),
                        instructions=s.get("instructions", ""),
                        payload=payload, agent_id=agent_id, deps=dep_ids,
                        priority=s.get("priority", 5),
                        acceptance_tests=s.get("acceptance_tests"),
                        requires_approval=bool(s.get("requires_approval", False)),
                        depth=1)
        id_by_title[title] = t["id"]
        created.append(t)
    team = sorted({t["agent_id"] for t in created if t.get("agent_id")})
    db.log_event(conn, "plan_created", f"orchestrated {len(created)} tasks for objective",
                 project_id=project_id, data={"objective": objective[:500],
                                              "tasks": [t["id"] for t in created],
                                              "team": team, "mode": mode})
    return {"tasks": created, "team": team, "mode": mode,
            "objective": objective, "task_ids": [t["id"] for t in created]}
