# Roadmap — implemented / partial / not yet

## Fully implemented & tested (24/24 tests passing)
- Agent factory: 33 templates, custom roles, provider/model, tools, budgets,
  concurrency, approvals, project access; pause/resume/stop/delete.
- Orchestrator: objective→plan→task graph, dependency wiring, smallest-team
  assignment, dedupe, depth + count loop guards.
- Durable task state machine (12 states), every transition event-logged.
- Concurrent workers: leases, heartbeats, stale recovery, bounded retries +
  exponential backoff, cancellation, restart recovery (tested, no duplicates).
- Live command centre: stats, per-agent activity, SSE events, SVG workflow
  graph, task board, inspector, approval inbox, memory browser, provider panel.
- Provider adapters: mock + openai_compat + anthropic + gemini; capability
  flags; explicit errors (missing creds/rate-limit/timeout); no silent fallback.
- Tool registry: 9 tools, risk levels, permission checks, timeouts, audit trail,
  workspace jailing, consequential-approval gating.
- Quality gates: acceptance tests block completion; verification results stored.
- Memory: 7 scopes, 8 types, verified flag, scoped retrieval, project isolation.
- Budgets: per-agent/project tokens/cost/tasks/runtime with hard-stop → blocked.
- Business workspaces: project type=business with objectives/KPIs/budgets/rules.
- Artifacts, project export/delete safeguards, usage + cost tracking (estimates
  labelled as estimates).

## Partially implemented
- Real-provider E2E: adapters tested against a local OpenAI-protocol stub; live
  cloud calls NOT tested here (no credentials in environment).
- LLM planner: heuristic planner is default; LLM planning path exists via agent
  providers but no separate “planner model” setting yet.
- Multi-user auth: single bearer token; roles (admin/operator/viewer) stubbed.
- Worktree isolation: per-project workspace jailing done; per-agent git
  worktrees/branches for parallel code edits not yet implemented.
- Scheduled health checks / self-repair loops: manual + restart recovery done;
  cron-style schedules not yet.

## Not yet implemented
- Browser automation tool (Playwright), GitHub API integration, notifications
  (Slack/email), object-storage backend, Postgres backend (SQLite only),
  multi-node scale-out, revenue-collection integrations (by design: approval
  stubs only), streaming responses, vision inputs.
