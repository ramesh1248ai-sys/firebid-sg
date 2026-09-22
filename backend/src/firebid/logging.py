"""JSON logging through structlog, including the standard-library loggers (uvicorn, procrastinate).

Logs carry IDs and metrics only. Document text and prices never go into a log line
(project-context guardrail 8).
"""

import logging
import sys

import structlog

_shared_processors: list[structlog.types.Processor] = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
    structlog.processors.StackInfoRenderer(),
    structlog.processors.format_exc_info,
]


class _JsonStdoutHandler(logging.StreamHandler):  # type: ignore[type-arg]
    """Marker type, so reconfiguring replaces only our handler and leaves others (e.g. pytest's)."""


def configure_logging(level: str = "INFO") -> None:
    structlog.configure(
        processors=[*_shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    handler = _JsonStdoutHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=_shared_processors,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.JSONRenderer(),
            ],
        )
    )
    root = logging.getLogger()
    root.handlers = [h for h in root.handlers if not isinstance(h, _JsonStdoutHandler)]
    root.addHandler(handler)
    root.setLevel(level)
    # Requests are logged once by RequestIdMiddleware; uvicorn's access log would duplicate them.
    logging.getLogger("uvicorn.access").handlers = []
    logging.getLogger("uvicorn.access").propagate = False
