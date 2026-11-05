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


def test_gemini_numeric_usage_survives_json_formatting_without_prompt_data():
    record = logging.LogRecord("timing", logging.INFO, "", 0, "private prompt", (), None)
    record.stage = "gemini.investigation.usage"
    record.event = record.stage
    record.request_id = "safe-id"
    record.duration_ms = None
    record.succeeded = True
    record.model_id = "gemini-3.5-flash-lite"
    record.attempts = 1
    record.prompt_tokens = 123
    record.candidate_tokens = 15
    record.thinking_tokens = 8
    record.prompt = "private document"
    value = json.loads(TimingJsonFormatter().format(record))
    assert value["model_id"] == "gemini-3.5-flash-lite"
    assert value["attempts"] == 1
    assert (value["prompt_tokens"], value["candidate_tokens"], value["thinking_tokens"]) == (123, 15, 8)
    assert "private" not in json.dumps(value)
