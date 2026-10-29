import logging

import pytest

from investigation_engine.application.timing import stage_timing, timed


async def test_nested_stage_timings_share_trace_without_request_data(caplog):
    @timed("answer.total")
    async def operation(secret):
        with stage_timing("query_embedding"):
            return secret

    with caplog.at_level(logging.DEBUG, logger="investigation_engine.application.timing"):
        assert await operation("private-question-and-key") == "private-question-and-key"
    assert len(caplog.records) == 2
    assert caplog.records[0].args[0] == caplog.records[1].args[0]
    assert caplog.records[0].args[2] >= 0
    assert "private-question-and-key" not in caplog.text


async def test_failed_stage_is_timed_and_preserves_exception(caplog):
    @timed("retrieval.total")
    async def operation():
        raise RuntimeError("private-parameters")

    with caplog.at_level(logging.INFO, logger="investigation_engine.application.timing"):
        with pytest.raises(RuntimeError, match="private-parameters"):
            await operation()
    assert len(caplog.records) == 1 and caplog.records[0].args[-1] is False
    assert caplog.records[0].levelno == logging.WARNING
    assert "private-parameters" not in caplog.text
