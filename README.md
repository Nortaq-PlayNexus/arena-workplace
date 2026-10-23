# Autonomous Multi-Agent AI Workplace

A central command centre for creating, configuring, assigning, monitoring,
evaluating, and managing teams of AI agents that do **real work**: running code,
executing tools, passing quality gates, and producing artifacts — all observable
in a live dashboard.

## Quick start (local)

```bash
cp .env.example .env        # optional: set WORKPLACE_TOKEN + provider keys
pip install -r backend/requirements.txt
./start.sh                  # starts backend + workers on http://localhost:8000
```

Open **http://localhost:8000** → click **⚡ Create starter team** (Agents tab),
or use the universal objective box on the Command tab.

Stop with `./stop.sh`.

## What it does

- **Central orchestrator**: turns an objective into a task graph, assembles the
  smallest effective team, tracks dependencies, verifies evidence, reports results.
- **Durable execution**: DB-backed queue, worker leases + heartbeats, stale-worker
  recovery, bounded retries with backoff, cancellation, restart recovery.
- **Live visibility**: per-agent activity (stage, tool, last action, progress from
  real milestones), SSE event stream, SVG workflow graph, logs, audit trail.
- **Providers**: `mock` (deterministic, default) + `openai_compat` (OpenAI, Ollama,
  LM Studio, vLLM…) + `anthropic` + `gemini`. Keys from environment only.
- **Tools**: sandboxed `fs_*`, `shell`, `python_exec`, `git` (read-only),
  `web_fetch` (untrusted-tagged), `report_write`, approval-gated `publish_request`.
- **Quality gates**: acceptance tests (shell/file checks) must pass before a task
  can complete; independent review state supported.
- **Controls**: pause/resume/stop/reassign/cancel, approval inbox, per-agent and
  per-project budgets (tokens/cost/tasks/runtime), project-scoped memory + files.
- **Memory**: scoped (global/user/agent/project/business/task/knowledge), typed
  (fact/assumption/hypothesis/decision/lesson/…), verified-vs-unverified.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `WORKPLACE_DATA_DIR` | `./data` | DB, workspaces, artifacts |
| `WORKPLACE_TOKEN` | _(empty=open local mode)_ | Bearer token for API + UI |
| `WORKER_COUNT` | `4` | Concurrent worker pool size |
| `DEFAULT_PROVIDER` / `DEFAULT_MODEL` | `mock` / `mock-small` | Default model |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` | — | OpenAI or compatible server |
| `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` | — | Cloud providers |
| `AUTONOMY_MODE` | `supervised` | Label for operating policy |

Use the **Providers tab** to test a connection before assigning real models.

## Tests

```bash
python -m pytest backend/tests/ -q
```

Suites: `test_acceptance.py` (Tests **A–M** + state-machine/dedupe/orchestration
regressions), `test_provider_adapters.py` (OpenAI-protocol stub), and
`test_clean_install.py` (**Test N**: boots a fresh server in a temp dir and runs
agent→project→task→completed end to end).

## Project layout

```
backend/app/     FastAPI API + orchestrator + workers + providers + tools + memory
backend/tests/   acceptance, adapter, and clean-install suites
frontend/        dependency-free ops-centre SPA (index.html, app.js, styles.css)
data/            SQLite DB, per-project workspaces, artifacts (created at runtime)
examples/        agent configs + demo orchestration script
docs/            PLAN.md, COMPLETION_REPORT.md
```

## Deployment

- `Dockerfile` + `docker-compose.yml` for reproducible single-node deployment.
- Health endpoint: `GET /health`. Back up `data/workplace.db` + `data/artifacts`.

See `ROADMAP.md` (implemented vs planned), `SECURITY.md` (threat model +
limitations), `TROUBLESHOOTING.md` (common issues).
