"""SQLite persistence: schema, migrations, connection helpers, state machine."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from . import config

SCHEMA_VERSION = 1

_lock = threading.RLock()
_local = threading.local()
_all_conns: list[sqlite3.Connection] = []
_db_path: Optional[Path] = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def get_conn() -> sqlite3.Connection:
    """Thread-local connection: every worker thread gets its own SQLite
    connection to the same WAL-mode database file."""
    global _db_path
    conn = getattr(_local, "conn", None)
    if conn is not None and getattr(_local, "path", None) == config.DB_PATH:
        return conn
    with _lock:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        conn = _connect(config.DB_PATH)
        _local.conn = conn
        _local.path = config.DB_PATH
        _all_conns.append(conn)
        _db_path = config.DB_PATH
        init_db(conn)
        return conn


def close_conn() -> None:
    global _db_path
    with _lock:
        for c in _all_conns:
            try:
                c.commit()
                c.close()
            except Exception:
                pass
        _all_conns.clear()
        _local.conn = None
        _local.path = None
        _db_path = None


def reset_for_tests(tmp_path: Path) -> None:
    """Point config at a temp dir (used by tests)."""
    close_conn()
    config.DATA_DIR = tmp_path
    config.DB_PATH = tmp_path / "workplace.db"
    config.WORKSPACES_DIR = tmp_path / "workspaces"
    config.ARTIFACTS_DIR = tmp_path / "artifacts"
    tmp_path.mkdir(parents=True, exist_ok=True)
    config.WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)
    config.ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    _local.conn = None
    _local.path = None


SCHEMA = """
CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS agents (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, avatar TEXT DEFAULT '',
  role TEXT DEFAULT '', specialty TEXT DEFAULT '', team TEXT DEFAULT 'general',
  system_instructions TEXT DEFAULT '', provider TEXT DEFAULT 'mock',
  model TEXT DEFAULT 'mock-small', allowed_tools TEXT DEFAULT '[]',
  allowed_paths TEXT DEFAULT '[]', max_concurrent INTEGER DEFAULT 2,
  budgets TEXT DEFAULT '{}', retry_policy TEXT DEFAULT '{}',
  reporting_freq TEXT DEFAULT 'normal', eval_criteria TEXT DEFAULT '',
  approval_required INTEGER DEFAULT 0, status TEXT DEFAULT 'active',
  project_access TEXT DEFAULT '[]', memory_config TEXT DEFAULT '{}',
  created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, type TEXT DEFAULT 'project',
  description TEXT DEFAULT '', objectives TEXT DEFAULT '',
  kpis TEXT DEFAULT '[]', status TEXT DEFAULT 'active',
  budgets TEXT DEFAULT '{}', team TEXT DEFAULT '[]',
  operating_rules TEXT DEFAULT '', created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  agent_id TEXT REFERENCES agents(id) ON DELETE SET NULL,
  title TEXT NOT NULL, objective TEXT DEFAULT '', kind TEXT DEFAULT 'general',
  instructions TEXT DEFAULT '', payload TEXT DEFAULT '{}', status TEXT DEFAULT 'draft',
  priority INTEGER DEFAULT 5, deps TEXT DEFAULT '[]',
  attempt INTEGER DEFAULT 0, max_retries INTEGER DEFAULT 3,
  backoff_base REAL DEFAULT 2.0, timeout_seconds INTEGER DEFAULT 600,
  lease_owner TEXT, lease_expires TEXT, progress REAL DEFAULT 0.0,
  current_stage TEXT DEFAULT '', current_tool TEXT DEFAULT '',
  last_action TEXT DEFAULT '', progress_detail TEXT DEFAULT '{}',
  output TEXT DEFAULT '', artifacts TEXT DEFAULT '[]', error TEXT DEFAULT '',
  acceptance_tests TEXT DEFAULT '[]', verification_result TEXT DEFAULT '{}',
  review_required INTEGER DEFAULT 0, cost_usd REAL DEFAULT 0.0,
  tokens_used INTEGER DEFAULT 0, runtime_ms INTEGER DEFAULT 0,
  requires_approval INTEGER DEFAULT 0, approved INTEGER DEFAULT 0,
  dedupe_hash TEXT DEFAULT '', parent_task_id TEXT, depth INTEGER DEFAULT 0,
  next_retry_at TEXT, created_at TEXT, updated_at TEXT,
  started_at TEXT, completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id);
CREATE INDEX IF NOT EXISTS idx_tasks_agent ON tasks(agent_id);
CREATE TABLE IF NOT EXISTS workers (
  id TEXT PRIMARY KEY, status TEXT DEFAULT 'alive',
  last_heartbeat TEXT, current_task_id TEXT, started_at TEXT
);
CREATE TABLE IF NOT EXISTS task_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, project_id TEXT,
  agent_id TEXT, worker_id TEXT, event_type TEXT NOT NULL,
  from_status TEXT, to_status TEXT, message TEXT DEFAULT '',
  data TEXT DEFAULT '{}', created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_task ON task_events(task_id);
CREATE INDEX IF NOT EXISTS idx_events_created ON task_events(created_at);
CREATE TABLE IF NOT EXISTS artifacts (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  task_id TEXT, agent_id TEXT, name TEXT NOT NULL, path TEXT NOT NULL,
  kind TEXT DEFAULT 'file', size_bytes INTEGER DEFAULT 0, created_at TEXT
);
CREATE TABLE IF NOT EXISTS memories (
  id TEXT PRIMARY KEY, scope TEXT NOT NULL, scope_id TEXT DEFAULT '',
  type TEXT DEFAULT 'fact', key TEXT NOT NULL, value TEXT DEFAULT '',
  confidence REAL, verified INTEGER DEFAULT 0, origin TEXT DEFAULT '',
  created_at TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_mem_scope ON memories(scope, scope_id);
CREATE TABLE IF NOT EXISTS approvals (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  task_id TEXT, agent_id TEXT, action TEXT NOT NULL,
  detail TEXT DEFAULT '{}', risk TEXT DEFAULT 'consequential',
  status TEXT DEFAULT 'pending', requested_at TEXT, decided_at TEXT, decided_by TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id TEXT, project_id TEXT,
  task_id TEXT, provider TEXT DEFAULT '', model TEXT DEFAULT '',
  tokens_in INTEGER DEFAULT 0, tokens_out INTEGER DEFAULT 0,
  cost_usd REAL DEFAULT 0.0, runtime_ms INTEGER DEFAULT 0, created_at TEXT
);
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT, action TEXT,
  resource_type TEXT DEFAULT '', resource_id TEXT DEFAULT '',
  detail TEXT DEFAULT '{}', created_at TEXT
);
CREATE TABLE IF NOT EXISTS provider_configs (
  id TEXT PRIMARY KEY, provider TEXT NOT NULL, model TEXT NOT NULL,
  endpoint TEXT DEFAULT '', capabilities TEXT DEFAULT '{}',
  is_default INTEGER DEFAULT 0, fallback_chain TEXT DEFAULT '[]',
  rate_limit TEXT DEFAULT '{}', timeout_s INTEGER DEFAULT 120, created_at TEXT
);
"""

# Allowed task state transitions (None = creation).
TRANSITIONS: dict[str, set[str]] = {
    "draft": {"planned", "queued", "cancelled"},
    "planned": {"queued", "cancelled"},
    "queued": {"assigned", "running", "cancelled", "blocked"},
    "assigned": {"running", "queued", "cancelled", "blocked"},
    "running": {"waiting_input", "blocked", "review_required",
                "verification_running", "completed", "failed", "cancelled", "queued"},
    "waiting_input": {"running", "queued", "cancelled", "failed"},
    "blocked": {"queued", "cancelled", "failed"},
    "review_required": {"completed", "failed", "running", "queued", "cancelled"},
    "verification_running": {"completed", "failed", "review_required", "queued", "cancelled"},
    "completed": set(),
    "failed": {"queued", "cancelled"},
    "cancelled": {"queued"},
}

TERMINAL = {"completed", "failed", "cancelled"}


def init_db(conn: Optional[sqlite3.Connection] = None) -> None:
    conn = conn or get_conn()
    with _lock:
        conn.executescript(SCHEMA)
        row = conn.execute("SELECT value FROM app_meta WHERE key='schema_version'").fetchone()
        if row is None:
            conn.execute("INSERT INTO app_meta(key,value) VALUES('schema_version',?)",
                         (str(SCHEMA_VERSION),))
        conn.commit()


def row_to_dict(row: Any) -> dict:
    return dict(row) if row is not None else {}


def jloads(s: Any, default: Any) -> Any:
    if s is None or s == "":
        return default
    if isinstance(s, (dict, list)):
        return s
    try:
        return json.loads(s)
    except Exception:
        return default


def jdumps(o: Any) -> str:
    return json.dumps(o, default=str)


def dedupe_hash(project_id: str, title: str, objective: str) -> str:
    h = hashlib.sha256(f"{project_id}|{title.strip().lower()}|{objective.strip().lower()}".encode())
    return h.hexdigest()[:16]


def log_event(conn: sqlite3.Connection, event_type: str, message: str = "",
              task_id: Optional[str] = None, project_id: Optional[str] = None,
              agent_id: Optional[str] = None, worker_id: Optional[str] = None,
              from_status: Optional[str] = None, to_status: Optional[str] = None,
              data: Optional[dict] = None) -> int:
    cur = conn.execute(
        """INSERT INTO task_events(task_id,project_id,agent_id,worker_id,event_type,
           from_status,to_status,message,data,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (task_id, project_id, agent_id, worker_id, event_type, from_status,
         to_status, message, jdumps(data or {}), now_iso()))
    conn.commit()
    return cur.lastrowid


def audit(conn: sqlite3.Connection, actor: str, action: str,
          resource_type: str = "", resource_id: str = "",
          detail: Optional[dict] = None) -> None:
    conn.execute(
        "INSERT INTO audit_log(actor,action,resource_type,resource_id,detail,created_at)"
        " VALUES(?,?,?,?,?,?)",
        (actor, action, resource_type, resource_id, jdumps(detail or {}), now_iso()))
    conn.commit()


def set_task_status(conn: sqlite3.Connection, task_id: str, to_status: str,
                    message: str = "", worker_id: Optional[str] = None,
                    data: Optional[dict] = None, force: bool = False) -> dict:
    """Transition a task; raises ValueError on illegal transition. Always logs event."""
    with _lock:
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise ValueError(f"task not found: {task_id}")
        t = row_to_dict(row)
        frm = t["status"]
        if to_status != frm:
            if not force and to_status not in TRANSITIONS.get(frm, set()):
                raise ValueError(f"illegal transition {frm} -> {to_status}")
            extra = ""
            params: list[Any] = [to_status, now_iso()]
            if to_status == "running" and not t.get("started_at"):
                extra += ", started_at=?"
                params.append(now_iso())
            if to_status in TERMINAL:
                extra += ", completed_at=?"
                params.append(now_iso())
            params.append(task_id)
            conn.execute(f"UPDATE tasks SET status=?, updated_at=?{extra} WHERE id=?", params)
        log_event(conn, "status", message or f"{frm} -> {to_status}", task_id=task_id,
                  project_id=t.get("project_id"), agent_id=t.get("agent_id"),
                  worker_id=worker_id, from_status=frm, to_status=to_status,
                  data=data or {})
        conn.commit()
        return get_task(conn, task_id)


def get_task(conn: sqlite3.Connection, task_id: str) -> dict:
    row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if row is None:
        raise ValueError(f"task not found: {task_id}")
    return row_to_dict(row)
