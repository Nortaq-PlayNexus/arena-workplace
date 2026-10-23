# Troubleshooting Guide

## Server won't start
- `pip install -r backend/requirements.txt` (needs Python 3.11+).
- Port in use: `WORKER_COUNT=4 python -m uvicorn backend.app.main:app --port 8001`.
- Check `GET /health` — `db` must be `ok`, `workers_alive` ≥ 1.

## Tasks stuck in `queued`
- No active agent? Create one (Agents tab → ⚡ starter team).
- Agent paused, or project paused? Resume them.
- Dependencies incomplete? Open the workflow graph; failed prerequisites put
  downstream tasks in `blocked` (by design).
- Worker crash? Restart the server; orphaned tasks are requeued automatically.

## Tasks stuck in `running`
- Leases expire after `LEASE_SECONDS` (default 30s); restart recovery requeues
  them. Check Logs tab for `worker_exception` / `provider_error` events.

## Task `failed` with provider errors
- `missing_credentials`: set the key in `.env`/environment and restart, or
  switch the agent to `mock` / a local OpenAI-compatible server.
- `rate_limit` / `timeout`: bounded retries apply automatically; lower
  `max_concurrent` or add backoff-friendly `max_retries`.
- Use Providers tab → “Test connection” to diagnose without running tasks.

## Task `blocked`
- Read the `error` field: usually a failed prerequisite or an exceeded budget.
  Fix the cause, then Retry (failed/cancelled/blocked → queued).

## Task `waiting_input` / approvals
- Open the Approvals tab and approve/reject. Rejected tasks fail safely.

## Quality gate failures
- Open the task → verification results show which acceptance test failed and
  its output. Fix the underlying work (or the test), then Retry.

## Budgets blocking everything (TEST K behaviour in prod)
- Raise caps in the agent/project editor or clear them (empty = no cap).

## UI shows “offline”
- Backend down? Start it. Wrong token? “Set API token” in the sidebar.
- SSE blocked by proxy? The UI falls back to 4s polling automatically.

## Database locked / slow
- WAL mode is on; heavy parallel shell tools can contend. Reduce `WORKER_COUNT`.

## Clean slate
```bash
./stop.sh && rm -rf data && ./start.sh
```
Then re-create the starter team from the UI.
