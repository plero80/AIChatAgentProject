"""File-only construction traces, independent of request/application logging."""

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
from threading import RLock
from time import perf_counter
from typing import Annotated, get_args, get_origin, get_type_hints
from uuid import uuid4

from pydantic import ValidationError

from backend.paths import PROJECT_ROOT


_write_lock = RLock()
logger = logging.getLogger(__name__)


def qualified_name(value) -> str:
    """Name a type/function without repr-ing service instances or credentials."""
    if not isinstance(value, type) and not hasattr(value, "__qualname__"):
        value = type(value)
    module = getattr(value, "__module__", "")
    name = getattr(value, "__qualname__", type(value).__name__)
    return name if module == "builtins" else f"{module}.{name}"


def type_name(annotation) -> str:
    if isinstance(annotation, type):
        return qualified_name(annotation)
    return str(annotation)


def trace_path() -> Path:
    configured = Path(os.environ.get("GRAPH_BUILD_LOG", "output/graph_build.jsonl")).expanduser()
    return configured if configured.is_absolute() else PROJECT_ROOT / configured


class GraphBuildTrace:
    """One build's events. Each line is JSON; each build has its own correlation ID.

    A handler is opened/closed per build, so imports and logging reconfiguration
    cannot accumulate duplicate handlers. A process-wide lock serializes records
    from concurrent builds. Use a separate file per worker in multi-process setups.
    """

    def __init__(self, *, workflow: str | None = None, source: str = "json", client_id: str | None = None):
        self.build_id = uuid4().hex
        self.workflow = workflow
        self.source = source
        self.client_id = client_id
        self.path = trace_path()
        self.phase = "start"
        self.context = {}
        self._started = perf_counter()
        self._handler = None
        self._logger = logging.Logger("graph_build", level=logging.INFO)
        self._logger.propagate = False

    def __enter__(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._handler = logging.FileHandler(self.path, mode="a", encoding="utf-8")
            self._handler.setFormatter(logging.Formatter("%(message)s"))
            self._logger.addHandler(self._handler)
        except OSError as exc:
            # Observability must not change a workflow's success/failure behavior.
            logger.warning("Cannot open graph build trace %s (%s)", self.path, type(exc).__name__)
        self.record("build_started")
        return self

    def stage(self, phase: str, **context):
        self.phase = phase
        self.context = context
        self.record("stage_started", **context)

    def record(self, event: str, **details):
        if self._handler is None:
            return
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "pid": os.getpid(), "build_id": self.build_id,
            "workflow": self.workflow, "source": self.source, "client_id": self.client_id,
            "level": "ERROR" if event.endswith("failed") else "INFO",
            "phase": self.phase, "event": event, **details,
        }
        with _write_lock:
            self._logger.info(json.dumps(record, ensure_ascii=False))

    def schema(self, state_schema):
        """Record a Python graph's field annotations, separating reducer functions."""
        for name, annotation in get_type_hints(state_schema, include_extras=True).items():
            reducer = None
            if get_origin(annotation) is Annotated:
                annotation, *metadata = get_args(annotation)
                reducer = next((qualified_name(item) for item in metadata if callable(item)), None)
            self.record("state_field", name=name, type=type_name(annotation), reducer=reducer)

    def __exit__(self, error_type, error, traceback):
        try:
            elapsed = round((perf_counter() - self._started) * 1000, 3)
            if error is None:
                self.record("build_succeeded", duration_ms=elapsed)
            else:
                details = {"error_type": error_type.__name__, "duration_ms": elapsed, **self.context}
                cause = error if isinstance(error, ValidationError) else error.__cause__
                if isinstance(cause, ValidationError):
                    # Locations and codes are useful without copying rejected input.
                    details["issues"] = [
                        {"location": item["loc"], "code": item["type"]}
                        for item in cause.errors(include_input=False, include_context=False, include_url=False)
                    ]
                self.record("build_failed", **details)
        finally:
            if self._handler is not None:
                self._logger.removeHandler(self._handler)
                self._handler.close()
        return False
