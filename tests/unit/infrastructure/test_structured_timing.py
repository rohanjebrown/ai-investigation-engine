import json
import logging

from investigation_engine.infrastructure.structured_logging import TimingJsonFormatter


def test_json_timing_uses_only_allowlisted_fields():
    record = logging.LogRecord("timing", logging.INFO, "", 0, "secret prompt", (), None)
    record.stage = "retrieval.total"
    record.request_id = "safe-id"
    record.duration_ms = 12.3456
    record.succeeded = False
    value = json.loads(TimingJsonFormatter().format(record))
    assert value == {
        "severity": "INFO",
        "event": "operation.failed",
        "request_id": "safe-id",
        "operation": "retrieval.total",
        "duration_ms": 12.346,
        "succeeded": False,
    }
    assert "secret" not in json.dumps(value)
