"""Independent quality-gate runner: executes acceptance tests with real commands."""
from __future__ import annotations

import time

from .tools.registry import execute_tool


def run_acceptance_tests(task: dict, agent: dict) -> dict:
    """Run task['acceptance_tests'] = [{name, kind, command|path|contains}].
    Kinds: shell (exit 0 = pass), file_exists, file_contains.
    Returns {passed, failed, skipped, results[], all_passed}."""
    import json
    tests = task.get("acceptance_tests", [])
    if isinstance(tests, str):
        try:
            tests = json.loads(tests)
        except Exception:
            tests = []
    results = []
    ctx = {"agent_id": task.get("agent_id"), "project_id": task.get("project_id"),
           "task_id": task.get("id"), "allowed_tools": None,
           "consequential_approved": False}
    for t in tests or []:
        name = t.get("name", "unnamed")
        kind = t.get("kind", "shell")
        t0 = time.time()
        try:
            if kind == "shell":
                r = execute_tool("shell", {"command": t.get("command", "true"),
                                           "timeout_seconds": t.get("timeout_seconds", 120)}, ctx)
                results.append({"name": name, "kind": kind, "status": "passed" if r.ok else "failed",
                                "output": (r.output or r.error)[:2000],
                                "duration_ms": int((time.time() - t0) * 1000)})
            elif kind == "file_exists":
                r = execute_tool("fs_list", {"path": t.get("dir", ".")}, ctx)
                fname = t.get("path", "")
                ok = r.ok and fname.split("/")[-1] in (r.output or "")
                # precise check via fs_read attempt
                if ok:
                    rr = execute_tool("fs_read", {"path": fname}, ctx)
                    ok = rr.ok
                results.append({"name": name, "kind": kind,
                                "status": "passed" if ok else "failed",
                                "output": f"exists={ok}: {fname}",
                                "duration_ms": int((time.time() - t0) * 1000)})
            elif kind == "file_contains":
                r = execute_tool("fs_read", {"path": t.get("path", "")}, ctx)
                ok = r.ok and (t.get("contains", "") in (r.output or ""))
                results.append({"name": name, "kind": kind,
                                "status": "passed" if ok else "failed",
                                "output": f"contains={ok}: {t.get('contains','')[:100]}",
                                "duration_ms": int((time.time() - t0) * 1000)})
            else:
                results.append({"name": name, "kind": kind, "status": "skipped",
                                "output": f"unknown kind: {kind}", "duration_ms": 0})
        except Exception as e:
            results.append({"name": name, "kind": kind, "status": "failed",
                            "output": f"runner error: {e}", "duration_ms": 0})
    passed = sum(1 for r in results if r["status"] == "passed")
    failed = sum(1 for r in results if r["status"] == "failed")
    skipped = sum(1 for r in results if r["status"] == "skipped")
    return {"passed": passed, "failed": failed, "skipped": skipped,
            "results": results, "all_passed": failed == 0 and (passed + skipped) == len(results)}
