"""TEST N: clean install from documented instructions in a fresh environment."""
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _call(base, method, path, body=None):
    req = urllib.request.Request(base + path,
                                 data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def test_N_clean_install_startup_health_and_core_flow(tmp_path):
    data_dir = tmp_path / "fresh-data"
    data_dir.mkdir()
    port = 8123
    env = dict(os.environ, WORKPLACE_DATA_DIR=str(data_dir), WORKER_COUNT="2",
               WORKPLACE_TOKEN="", PYTHONPATH=str(ROOT))
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.app.main:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        base = f"http://127.0.0.1:{port}"
        health = None
        for _ in range(60):
            try:
                health = _call(base, "GET", "/health")
                break
            except Exception:
                time.sleep(0.5)
        assert health and health["status"] == "ok", "server did not become healthy"
        assert health["db"] == "ok" and health["workers_alive"] >= 1
        assert (data_dir / "workplace.db").exists(), "database was not initialised"
        # core flow: agent -> project -> task -> completed output
        ag = _call(base, "POST", "/api/agents",
                   {"name": "Clean", "role": "general test", "provider": "mock"})
        pr = _call(base, "POST", "/api/projects", {"name": "CleanProj"})
        t = _call(base, "POST", "/api/tasks",
                  {"project_id": pr["id"], "title": "clean task",
                   "objective": "verify clean install", "agent_id": ag["id"],
                   "payload": {"mock_output": "CLEAN-OK"}})
        final = None
        for _ in range(60):
            final = _call(base, "GET", f"/api/tasks/{t['id']}")
            if final["status"] in ("completed", "failed", "blocked"):
                break
            time.sleep(0.5)
        assert final["status"] == "completed", final
        assert "CLEAN-OK" in final["output"]
        # frontend served
        req = urllib.request.Request(base + "/")
        with urllib.request.urlopen(req, timeout=10) as r:
            html = r.read().decode()
        assert "Command Centre" in html
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
