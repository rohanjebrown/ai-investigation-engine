"""Single-process container entry point; production is the default mode."""

import argparse
import os
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace

import uvicorn

from investigation_engine.application.ports.generation_errors import GenerationProviderError
from investigation_engine.infrastructure.api.app import create_app


@asynccontextmanager
async def smoke_services():
    """Explicit local packaging check: no settings, DB, or AI clients are opened."""

    class DisabledQuestions:
        async def execute(self, *args, **kwargs):
            raise GenerationProviderError("Providers are disabled in container smoke mode")

    yield SimpleNamespace(settings=None, questions=DisabledQuestions(), investigations=None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--smoke", action="store_true", help="Local health/UI check with providers and management disabled"
    )
    args = parser.parse_args()
    try:
        port = int(os.environ.get("PORT", "8080"))
    except ValueError:
        raise SystemExit("PORT must be an integer between 1 and 65535") from None
    if not 1 <= port <= 65535:
        raise SystemExit("PORT must be an integer between 1 and 65535")
    app = create_app(service_factory=smoke_services) if args.smoke else create_app()
    log_config = deepcopy(uvicorn.config.LOGGING_CONFIG)
    log_config["formatters"]["timing_json"] = {
        "()": "investigation_engine.infrastructure.structured_logging.TimingJsonFormatter",
    }
    log_config["handlers"]["timing_json"] = {
        "class": "logging.StreamHandler",
        "formatter": "timing_json",
        "stream": "ext://sys.stdout",
    }
    timing_level = os.environ.get("ENGINE_LOG_LEVEL", "INFO").upper()
    if timing_level not in ("DEBUG", "INFO"):
        timing_level = "INFO"
    log_config["loggers"]["investigation_engine.application.timing"] = {
        "handlers": ["timing_json"],
        "level": timing_level,
        "propagate": False,
    }
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        workers=1,
        loop="asyncio",
        lifespan="on",
        timeout_graceful_shutdown=8,
        proxy_headers=True,
        forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1"),
        access_log=True,
        log_config=log_config,
    )


if __name__ == "__main__":
    main()
