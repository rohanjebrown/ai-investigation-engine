import importlib.util
from pathlib import Path


def test_provider_failure_with_unavailable_usage_remains_visible():
    spec = importlib.util.spec_from_file_location("submission_report", Path("scripts/build_submission_report.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = {
        "runs": [
            {
                "configuration": {"mode": "multi_step"},
                "aggregate": {},
                "by_category": {},
                "questions": [
                    {
                        "id": "synthetic-failure",
                        "question": "Controlled failure",
                        "status": "provider_failure",
                        "outcome": "provider_failure",
                        "usage": {"prompt_tokens": None},
                        "investigation_behavior": {"search_count": None, "generation_requests": None},
                    }
                ],
            }
        ]
    }
    rendered = module.render(report)
    assert "provider_failure" in rendered and "synthetic-failure" in rendered
    assert "failed requests may not supply usage" in rendered
