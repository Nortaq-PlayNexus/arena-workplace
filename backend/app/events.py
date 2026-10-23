"""Event helpers (DB-backed event bus)."""
from __future__ import annotations

import sqlite3
from typing import Optional


def recent(conn: sqlite3.Connection, since_id: int = 0, project_id: Optional[str] = None,
           limit: int = 100) -> list[dict]:
    from . import db
    sql = "SELECT * FROM task_events WHERE id > ?"
    params: list = [since_id]
    if project_id:
        sql += " AND project_id=?"; params.append(project_id)
    sql += " ORDER BY id ASC LIMIT ?"; params.append(limit)
    return [db.row_to_dict(r) for r in conn.execute(sql, params).fetchall()]
