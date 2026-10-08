import json
import logging
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from time import perf_counter

from config.settings import ROOT

request_id = ContextVar("request_id", default=None)
logger = logging.getLogger("llm_service")


class JsonFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "event": record.getMessage(),
            "request_id": request_id.get(),
            **getattr(record, "fields", {}),
        }, ensure_ascii=False)


def configure_logging():
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if logger.handlers:
        return
    (ROOT / "logs").mkdir(exist_ok=True)
    console = logging.StreamHandler(sys.stdout)
    file = RotatingFileHandler(ROOT / "logs/service.jsonl", maxBytes=2_000_000,
                               backupCount=3, encoding="utf-8")
    for handler in (console, file):
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    http_logger = logging.getLogger("werkzeug")
    http_logger.setLevel(logging.INFO)
    http_logger.propagate = False
    http_logger.handlers = [console, file]


def event(name, **fields):
    logger.info(name, extra={"fields": fields})


@contextmanager
def stage(name):
    start = perf_counter()
    event("stage_started", stage=name)
    outcome = "ok"
    try:
        yield
    except Exception as exc:
        outcome = "error"
        event("stage_error", stage=name, error_type=type(exc).__name__)
        raise
    finally:
        event("stage_finished", stage=name, outcome=outcome,
              duration_ms=round((perf_counter() - start) * 1000, 3))
