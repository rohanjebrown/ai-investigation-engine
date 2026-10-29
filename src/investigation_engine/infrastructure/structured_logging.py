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
            "embedding_requests",
            "prompt_tokens",
            "candidate_tokens",
            "thinking_tokens",
            "semantic_limit",
            "lexical_limit",
            "result_limit",
            "duplicate_count",
            "error_category",
        ):
            if getattr(record, field, None) is not None:
                payload[field] = getattr(record, field)
        return json.dumps(
            payload,
            separators=(",", ":"),
        )
