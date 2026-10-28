"""Single-process container entry point; production is the default mode."""

import argparse
from contextlib import asynccontextmanager
import os
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
    parser.add_argument("--smoke", action="store_true", help="Local health/UI check with providers and management disabled")
    args = parser.parse_args()
    try:
        port = int(os.environ.get("PORT", "8080"))
    except ValueError:
        raise SystemExit("PORT must be an integer between 1 and 65535") from None
    if not 1 <= port <= 65535:
        raise SystemExit("PORT must be an integer between 1 and 65535")
    app = create_app(service_factory=smoke_services) if args.smoke else create_app()
    uvicorn.run(app, host="0.0.0.0", port=port, workers=1, loop="asyncio",
                lifespan="on", timeout_graceful_shutdown=8, proxy_headers=True,
                forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1"),
                access_log=True)


if __name__ == "__main__":
    main()
