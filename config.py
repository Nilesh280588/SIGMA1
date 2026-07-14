"""Central configuration. Everything comes from .env / environment variables —
no keys, servers or model names are hard-coded anywhere else in the project.

The ONLY data source is SQL Server (SSMS). There is no local/dummy fallback."""
import os

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_dotenv(path):
    """Tiny .env loader (no external dependency required)."""
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            # Real environment variables always win over .env
            if key and key not in os.environ:
                os.environ[key] = value


_load_dotenv(os.path.join(_BASE_DIR, ".env"))

# ── Claude API ──
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-4-8")

# ── Database (SQL Server / SSMS only) ──
DB_SERVER = os.environ.get("DB_SERVER", "localhost")
DB_NAME = os.environ.get("DB_NAME", "")
DB_TRUSTED_CONNECTION = os.environ.get("DB_TRUSTED_CONNECTION", "no").lower() in ("yes", "true", "1")
DB_USERNAME = os.environ.get("DB_USERNAME", "")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")

# ── Email ──
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587") or 587)
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SMTP_FROM", "noreply@pharma-agent.com")


def api_key_is_set():
    return bool(ANTHROPIC_API_KEY) and ANTHROPIC_API_KEY.lower() not in ("xyz", "your-api-key", "changeme")
