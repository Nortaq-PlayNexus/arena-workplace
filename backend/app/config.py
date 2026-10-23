"""Central configuration. Secrets come ONLY from environment variables."""
from __future__ import annotations

import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
BACKEND_DIR = APP_DIR.parent
ROOT_DIR = BACKEND_DIR.parent

DATA_DIR = Path(os.environ.get("WORKPLACE_DATA_DIR", str(ROOT_DIR / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "workplace.db"
WORKSPACES_DIR = DATA_DIR / "workspaces"
ARTIFACTS_DIR = DATA_DIR / "artifacts"
WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)
ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

FRONTEND_DIR = ROOT_DIR / "frontend"

WORKPLACE_TOKEN = os.environ.get("WORKPLACE_TOKEN", "")
WORKER_COUNT = int(os.environ.get("WORKER_COUNT", "4"))
WORKER_POLL_INTERVAL = float(os.environ.get("WORKER_POLL_INTERVAL", "0.5"))
LEASE_SECONDS = int(os.environ.get("LEASE_SECONDS", "30"))
DEFAULT_PROVIDER = os.environ.get("DEFAULT_PROVIDER", "mock")
DEFAULT_MODEL = os.environ.get("DEFAULT_MODEL", "mock-small")
AUTONOMY_MODE = os.environ.get("AUTONOMY_MODE", "supervised")
DISABLE_WORKERS = os.environ.get("DISABLE_WORKERS", "0") == "1"

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

MAX_TASKS_PER_OBJECTIVE = 50
MAX_DELEGATION_DEPTH = 5
MAX_TOOL_ITERATIONS = 15

# Rough USD per 1K tokens (blended in/out) for cost estimates when provider
# does not report usage. Mock/local = 0. Marked ESTIMATE in UI.
PRICE_PER_1K = {
    "mock": 0.0,
    "openai_compat": 0.002,
    "anthropic": 0.003,
    "gemini": 0.0015,
}

PROVIDER_CAPABILITIES = {
    "mock": {"tool_calling": True, "structured_output": True, "vision": False,
             "parallel": True, "streaming": False},
    "openai_compat": {"tool_calling": True, "structured_output": True,
                      "vision": True, "parallel": True, "streaming": False},
    "anthropic": {"tool_calling": True, "structured_output": True,
                  "vision": True, "parallel": True, "streaming": False},
    "gemini": {"tool_calling": True, "structured_output": True,
               "vision": True, "parallel": True, "streaming": False},
}
