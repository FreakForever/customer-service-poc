"""JSON logging (LLD-16, NFR-OBS-02): one JSON object per line with severity, message, component (logger name),
trace_id, request_id and timestamp, so Cloud Logging parses it as structured logs. Phone numbers, e-mail addresses
and GSTINs are masked in messages and string fields (FR-RT-06, app/rules/pii.py).
"""

import contextvars
import logging
import sys
from typing import Any, cast

import structlog

from app.obs.tracing import current_trace_id
from app.rules.pii import mask_pii

__all__ = ["configure_logging", "mask_pii", "request_id_var"]

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)


def _mask(_: Any, __: str, event: dict[str, Any]) -> dict[str, Any]:
    for k, v in list(event.items()):
        if isinstance(v, str) and k not in ("timestamp", "severity", "component", "trace_id", "request_id"):
            event[k] = mask_pii(v)
    return event


def _context(_: Any, __: str, event: dict[str, Any]) -> dict[str, Any]:
    if (tid := current_trace_id()) and "trace_id" not in event:
        event["trace_id"] = tid
    if (rid := request_id_var.get()) and "request_id" not in event:
        event["request_id"] = rid
    return event


def _cloud_fields(_: Any, __: str, event: dict[str, Any]) -> dict[str, Any]:
    event["severity"] = event.pop("level", "info").upper()
    event["message"] = event.pop("event", "")
    if "logger" in event:
        event["component"] = event.pop("logger")
    return event


_configured = False


class _StdoutHandler(logging.StreamHandler):
    """Writes to whatever sys.stdout is at emit time (test runners and reloaders swap it)."""

    def emit(self, record: logging.LogRecord) -> None:
        self.stream = sys.stdout
        super().emit(record)


def configure_logging(level: str = "INFO") -> None:
    """Route stdlib logging and structlog through one JSON renderer on stdout. Idempotent."""
    global _configured
    if _configured:
        return
    shared: list[Any] = [structlog.contextvars.merge_contextvars, structlog.stdlib.add_log_level,
              structlog.stdlib.add_logger_name, structlog.processors.TimeStamper(fmt="iso", utc=True),
              _context, structlog.processors.format_exc_info, _mask]
    structlog.configure(processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
                        logger_factory=structlog.stdlib.LoggerFactory(), cache_logger_on_first_use=True)
    handler = _StdoutHandler(sys.stdout)
    handler.setFormatter(structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=cast(list[Any], [structlog.stdlib.ProcessorFormatter.remove_processors_meta, _cloud_fields,
                                    structlog.processors.JSONRenderer()])))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for noisy in ("httpx", "neo4j", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):   # uvicorn installs its own text handlers
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
    _configured = True
