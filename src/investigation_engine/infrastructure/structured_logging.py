"""JSON output for bounded application timing events."""

import json
import logging


class TimingJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "severity": record.levelname,
            "event": getattr(record, "event", "operation.completed" if record.succeeded else "operation.failed"),
            "request_id": record.request_id,
            "operation": record.stage,
            "duration_ms": round(record.duration_ms, 3),
            "succeeded": record.succeeded,
        }
        for field in ("mode", "semantic_count", "lexical_count", "result_count"):
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        return json.dumps(
            payload,
            separators=(",", ":"),
        )
