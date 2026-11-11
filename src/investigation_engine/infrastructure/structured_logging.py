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
            "succeeded": record.succeeded,
        }
        if record.duration_ms is not None:
            payload["duration_ms"] = round(record.duration_ms, 3)
        for field in (
            "model_id",
            "provider",
            "http_status",
            "provider_code",
            "error_reason",
            "exception_type",
            "retryable",
            "provider_request_id",
            "quota_scope",
            "quota_metric",
            "retry_after_seconds",
            "completed_turns",
            "completed_searches",
            "attempts",
            "attempt",
            "max_attempts",
            "will_retry",
            "retry_delay_seconds",
            "mode",
            "semantic_count",
            "lexical_count",
            "result_count",
            "status",
            "reason",
            "turn_number",
            "search_number",
            "final_only",
            "context_chars",
            "context_limit",
            "new_evidence_count",
            "reused_evidence_count",
            "evidence_count",
            "generation_requests",
            "provider_attempts",
            "embedding_requests",
            "prompt_tokens",
            "candidate_tokens",
            "thinking_tokens",
            "total_tokens",
            "usage_scope",
            "semantic_limit",
            "lexical_limit",
            "result_limit",
            "duplicate_count",
            "error_category",
        ):
            if getattr(record, field, None) is not None or (
                getattr(record, "event", None) == "generation.provider_failed"
                and field
                in ("http_status", "provider_code", "error_reason", "turn_number", "retryable", "provider_request_id")
            ):
                payload[field] = getattr(record, field, None)
        return json.dumps(
            payload,
            separators=(",", ":"),
        )
