"""Structured logging that never logs secrets.

Every handler installed by `configure_logging()` runs records through a
redaction layer. Redaction is deliberately conservative — it can only ever hide
too much, never too little:

  * `scaffold_…` personal access tokens        -> scaffold_***
  * `Authorization: Bearer <anything>`         -> Bearer ***
  * JWT-shaped strings (eyJ…)                  -> ***jwt***
  * password/token/secret-style `key=value` pairs (incl. DSN 'password=')
  * credentials embedded in a postgres:// DSN  -> postgres://user:***@host

Access logs are emitted by app/middleware.py and contain only: method, path
(no query string), status, duration, client IP, request id. Request bodies and
headers are NEVER logged by the engine.

Output format is JSON lines by default (SCAFFOLD_LOG_FORMAT=json) so Railway/Fly/
Datadog can index fields; SCAFFOLD_LOG_FORMAT=text gives human-readable lines
for local debugging. Uvicorn's own access log is disabled in favor of ours.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import datetime, timezone

from app.config import settings

request_id_var: ContextVar[str] = ContextVar("scaffold_request_id", default="")

_REDACTIONS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"scaffold_[A-Za-z0-9_\-]{12,}"), "scaffold_***"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]+"), "Bearer ***"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b"), "***jwt***"),
    (
        re.compile(
            r"(?i)\b(password|passwd|pwd|token|apikey|api_key|secret|service_key|llm_key|jwt_secret)"
            r"\s*[=:]\s*[^\s,;\"']+"
        ),
        r"\1=***",
    ),
    (re.compile(r"(postgres(?:ql)?(?:\+psycopg)?://[^:/@\s]+:)[^@\s]+@"), r"\1***@"),
]

_STANDARD_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
}


def redact(text_value: str) -> str:
    """Apply every secret-shaped redaction to one string."""
    for pattern, replacement in _REDACTIONS:
        text_value = pattern.sub(replacement, text_value)
    return text_value


class RequestIdFilter(logging.Filter):
    """Attach the current request id to every record (for %(request_id)s and JSON)."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get() or "-"
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
            "request_id": getattr(record, "request_id", "-"),
        }
        for key, value in record.__dict__.items():
            if key in _STANDARD_ATTRS or key.startswith("_") or key == "request_id":
                continue
            payload[key] = value if isinstance(value, (int, float, bool)) or value is None else redact(str(value))
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        try:
            # ensure_ascii keeps the line valid on any console/log collector.
            return json.dumps(payload, ensure_ascii=True, default=str)
        except Exception:  # noqa: BLE001 — a log line must never crash the app
            return json.dumps({"level": record.levelname, "msg": redact(str(record.msg))})


class RedactingTextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


_configured = False


def configure_logging(*, force: bool = False) -> None:
    """Install the engine's handlers on the root logger (idempotent)."""
    global _configured
    if _configured and not force:
        return

    level_name = (settings.scaffold_log_level or "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    log_format = (settings.scaffold_log_format or "json").strip().lower()

    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(RequestIdFilter())
    if log_format == "text":
        handler.setFormatter(
            RedactingTextFormatter(
                "%(asctime)s %(levelname)-7s %(name)s [%(request_id)s] %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S%z",
            )
        )
    else:
        handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)

    # Route uvicorn's loggers through the same handler; we emit our own access logs.
    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers[:] = []
        uvicorn_logger.propagate = True
    logging.getLogger("uvicorn.access").disabled = True

    # Libraries that like to chat (and sometimes include request material).
    for noisy in ("httpx", "httpx2", "httpcore", "litellm", "LiteLLM", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True
