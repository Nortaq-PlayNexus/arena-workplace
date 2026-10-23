# Architecture & Implementation Plan — Autonomous Multi-Agent AI Workplace

## 1. Stack (chosen after env inspection)
- Backend: Python 3.13 + FastAPI + uvicorn; SQLite (WAL) via stdlib `sqlite3`.
- Workers: asyncio worker pool inside backend process (configurable count),
  DB-backed durable queue with leases, heartbeats, stale-worker detection.
- Realtime: Server-Sent Events (`GET /api/events`) + polling fallback.
- Frontend: dependency-free single-page app (`frontend/index.html|app.js|styles.css`)
  served by FastAPI. No build step; works offline and in the sandboxed preview.
- Providers: `mock` (deterministic, default) + `openai_compat` + `anthropic` +
  `gemini` adapters behind a common `BaseProvider` interface.
- Tests: pytest + FastAPI TestClient; browser check via HTTP assertions on served UI.

## 2. Module boundaries
```
backend/app/
  config.py        env + paths + pricing + capabilities
  db.py            schema v1, migrations, connection + helpers, state machine
  providers/       base.py, mock.py, openai_compat.py, anthropic.py, gemini.py
  tools/           registry.py (+ builtin tools), sandbox paths
  memory.py        scoped memory CRUD + search
  security.py      token auth, permissions, budgets, approvals helpers
  orchestrator.py  objective→plan→task graph, assignment, dedupe, loop guards
  verification.py  acceptance-test runner (quality gates)
  workers.py       lease poll, agent runner, retries, heartbeats, recovery
  events.py        event bus (DB-backed) + SSE
  main.py          FastAPI routes + static frontend + startup/shutdown
frontend/          index.html, app.js, styles.css (live ops centre)
backend/tests/     test_acceptance.py (TESTS A–M), test_clean_install.py (N)
```

## 3. Task state machine
DRAFT→PLANNED→QUEUED→ASSIGNED→RUNNING→(REVIEW_REQUIRED|VERIFICATION_RUNNING)
→COMPLETED; any→FAILED|CANCELLED; RUNNING→WAITING_INPUT→RUNNING;
RUNNING→BLOCKED→QUEUED (requeue). Every transition writes a `task_events` row.

## 4. Concurrency & safety
- Lease: `lease_owner` (worker id) + `lease_expires`; atomic claim via
  `UPDATE ... WHERE status IN (...) AND (lease_expires IS NULL OR lease_expires < now)`.
- Deps: task runnable iff all deps COMPLETED. Dependents of FAILED tasks → BLOCKED.
- Isolation: `data/workspaces/<project_id>/`; tools jailed to project workspace
  (+ shared artifact dir for approved outputs). Shell/python with timeouts + denylist.
- Loop guards: max depth 5, max 50 tasks/objective, dedupe hash on
  (project,title,objective), delegation chain tracking.
- Budgets: per-agent + per-project tokens/cost/runtime/tasks; hard-stop → BLOCKED.
- Approvals: `consequential` tools + `requires_approval` tasks → WAITING_INPUT.

## 5. Acceptance criteria per phase
- P3: create agent→submit task→mock executes→result persisted→visible in UI + SSE.
- P4: ≥3 tasks overlap (timestamps); deps honored; cancel/retry/reassign work.
- P5: factory CRUD + templates; project boards; live activity; SVG graph; approvals.
- P6: all provider adapters importable + mock-tested; ≥7 tools; memory scopes; artifacts.
- P7: token auth enforced when set; permission denials tested; budget blocks tested.
- P8: TESTS A–M pass; failures fixed + rerun; results recorded truthfully.
- P9: clean-install script works in fresh DATA_DIR; health + smoke OK; report written.

## 6. Explicit non-goals for this build (see ROADMAP.md)
- Real revenue collection, ad spend, publishing integrations (approval-gated stubs only).
- Browser automation (Playwright) — interface stubbed, not installed (RAM).
- K8s scale-out; single-node deployment only.
