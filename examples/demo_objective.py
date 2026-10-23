"""Demo: create agents from examples/agents.json, a project, and orchestrate.

Usage: python3 examples/demo_objective.py [base_url]
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"


def call(method, path, body=None):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode() if body else None,
        headers={"Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode()


agents = json.loads(Path(__file__).with_name("agents.json").read_text())
for a in agents:
    print("agent:", call("POST", "/api/agents", a)["id"], a["name"])
proj = call("POST", "/api/projects", {
    "name": "Thumbnail business", "type": "business",
    "objectives": "Validate a thumbnail-design micro-business and build a prototype gallery."})
print("project:", proj["id"])
plan = call("POST", "/api/orchestrate", {
    "project_id": proj["id"],
    "objective": ("Research the thumbnail market, build a prototype gallery website, "
                  "test it, and prepare a launch plan.")})
print(f"planned {len(plan['tasks'])} tasks")
for _ in range(120):
    time.sleep(1)
    p = call("GET", f"/api/projects/{proj['id']}")
    sts = [t["status"] for t in p["tasks"]]
    if all(s in ("completed", "failed", "blocked", "cancelled") for s in sts):
        print("final:", sts)
        break
print(json.dumps(call("GET", "/api/stats"), indent=1))
