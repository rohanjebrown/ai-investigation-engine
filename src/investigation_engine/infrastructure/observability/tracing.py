from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
    OTLPSpanExporter,
)
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from sqlalchemy.ext.asyncio import AsyncEngine


def configure_tracing(
    app: FastAPI,
    *,
    endpoint: str,
) -> None:
    resource = Resource.create(
        {
            "service.name": "ai-investigation-engine",
        }
    )

    provider = TracerProvider(resource=resource)

    exporter = OTLPSpanExporter(endpoint=endpoint)

    processor = BatchSpanProcessor(exporter)

    provider.add_span_processor(processor)

    trace.set_tracer_provider(provider)

    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=provider,
    )

    HTTPXClientInstrumentor().instrument(
        tracer_provider=provider,
    )


def instrument_database(engine: AsyncEngine) -> None:
    SQLAlchemyInstrumentor().instrument(
        engine=engine.sync_engine,
    )
