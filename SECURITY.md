# Security & Limitations Report

## Threat model
Untrusted inputs: web pages, fetched files, repo contents, tool outputs, and
task objectives themselves. All are treated as **data**, never instructions:
system prompts state this, `web_fetch` tags output `[UNTRUSTED]`, and no tool
output can change agent permissions or global memory.

## Controls implemented (and tested)
- **Secrets**: API keys read from environment only; never stored in DB, never
  returned by any endpoint, never in logs/reports. Verified by code inspection
  + provider tests.
- **Auth**: bearer token enforced when `WORKPLACE_TOKEN` is set; UI warns in
  open local mode (TEST: 401/403 paths).
- **Tool permissions**: per-agent allow-lists; unknown/denied tools raise;
  consequential tools require human approval (TEST J).
- **Filesystem jail**: all file/shell/python tools resolve inside
  `data/workspaces/<project_id>`; escape attempts rejected (TEST J).
- **Sandboxed exec**: shell denylist (destructive patterns), timeouts
  (≤600s shell / ≤300s python), python import blocklist, output caps.
- **Project isolation**: workspaces, Files, memories, events, permissions
  scoped per project (TEST L).
- **Budgets**: hard caps block further execution (TEST K).
- **Approval gates**: `requires_approval` tasks + consequential tools pause to
  `waiting_input` until a human decides (TEST M).
- **Audit**: every tool call logged (actor, risk, I/O preview, duration).
- **Safe stop**: agent stop cancels its active tasks; task cancel is honoured
  between provider/tool steps.
- **No self-escalation**: agents cannot edit global memory scope, their own
  allow-list, or budgets (no tool exposes this; API requires the user token).

## Known limitations (be honest about these)
1. **Open local mode by default**: with no `WORKPLACE_TOKEN`, anyone with
   network access can control agents. Set a token on shared networks.
2. **Single-node, single-token auth**: no per-user RBAC yet; token = full admin.
3. **Sandbox is best-effort, not a container**: a malicious model output could
   still attempt harmful-but-allowed shell commands inside the workspace
   (e.g. fork bombs are timeout-killed, but crypto-miners would run until
   timeout). For untrusted workloads, run the Docker image with CPU/memory
   limits and no network, or restrict agents to read-only tools.
4. **Prompt injection**: mitigations (tagging, policy text) reduce but cannot
   eliminate risk with capable models. Keep consequential actions
   approval-gated and budgets tight.
5. **SQLite**: file-level access = full access; set filesystem permissions on
   `data/` and back it up. No encryption at rest.
6. **Cost tracking is estimated** for providers that don't return usage
   (labelled ESTIMATE in UI semantics); set conservative budgets.
7. **No rate limiting on the API itself** yet; deploy behind a reverse proxy
   with limits for internet exposure.
8. **Destructive project delete** requires `?confirm=DELETE` but is immediate;
   export first.

## Recovery
- Restart recovery requeues leased/orphaned tasks (TEST G).
- Back up `data/workplace.db`, `data/workspaces/`, `data/artifacts/`.
- If a worker wedges a task in `running` with a live lease, stop the server;
  on next boot the lease expires and the task is requeued (leases ≤30s by default).
