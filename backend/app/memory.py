"""Scoped persistent memory: global/user/agent/project/business/task/knowledge."""
from __future__ import annotations

import sqlite3
from typing import Optional

from . import db

VALID_SCOPES = {"global", "user", "agent", "project", "business", "task", "knowledge"}
VALID_TYPES = {"fact", "assumption", "hypothesis", "decision", "lesson",
               "constraint", "strategy", "question"}


def put(conn: sqlite3.Connection, scope: str, key: str, value: str,
        scope_id: str = "", mtype: str = "fact", confidence: Optional[float] = None,
        verified: bool = False, origin: str = "") -> dict:
    if scope not in VALID_SCOPES:
        raise ValueError(f"invalid scope: {scope}")
    if mtype not in VALID_TYPES:
        raise ValueError(f"invalid memory type: {mtype}")
    mid = db.new_id("mem")
    conn.execute(
        """INSERT INTO memories(id,scope,scope_id,type,key,value,confidence,verified,
           origin,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (mid, scope, scope_id, mtype, key, value, confidence, 1 if verified else 0,
         origin, db.now_iso(), db.now_iso()))
    conn.commit()
    return get(conn, mid)


def get(conn: sqlite3.Connection, mid: str) -> dict:
    row = conn.execute("SELECT * FROM memories WHERE id=?", (mid,)).fetchone()
    if row is None:
        raise ValueError(f"memory not found: {mid}")
    return db.row_to_dict(row)


def search(conn: sqlite3.Connection, scope: Optional[str] = None,
           scope_id: Optional[str] = None, query: str = "",
           mtype: Optional[str] = None, limit: int = 50) -> list[dict]:
    sql = "SELECT * FROM memories WHERE 1=1"
    params: list = []
    if scope:
        sql += " AND scope=?"; params.append(scope)
    if scope_id:
        sql += " AND scope_id=?"; params.append(scope_id)
    if mtype:
        sql += " AND type=?"; params.append(mtype)
    if query:
        sql += " AND (key LIKE ? OR value LIKE ?)"; params += [f"%{query}%", f"%{query}%"]
    sql += " ORDER BY updated_at DESC LIMIT ?"; params.append(limit)
    return [db.row_to_dict(r) for r in conn.execute(sql, params).fetchall()]


def relevant_for_task(conn: sqlite3.Connection, project_id: str,
                      agent_id: Optional[str], query: str, limit: int = 10) -> list[dict]:
    """Retrieve memories relevant to a task without loading everything."""
    out: list[dict] = []
    out += search(conn, scope="global", query=query, limit=3)
    out += search(conn, scope="project", scope_id=project_id, query="", limit=4)
    if agent_id:
        out += search(conn, scope="agent", scope_id=agent_id, query="", limit=3)
    if query:
        out += search(conn, scope="knowledge", query=query, limit=4)
    seen, dedup = set(), []
    for m in out:
        if m["id"] not in seen:
            seen.add(m["id"]); dedup.append(m)
    return dedup[:limit]
