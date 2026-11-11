"""Bounded, sequential Gemma transport diagnostics; no live retrieval."""

import argparse
import asyncio
import importlib.metadata
import json
import time
from pathlib import Path

from google import genai
from google.genai import errors, types

from investigation_engine.application.investigations import InvestigationTurn
from investigation_engine.application.ports.provider_diagnostics import safe_diagnostic
from investigation_engine.infrastructure.config import settings
from investigation_engine.infrastructure.generation.gemini_investigation_model import TOOL_DECLARATION
from investigation_engine.infrastructure.generation.gemma4_investigation_model import (
    GEMMA4_MODEL,
    Gemma4InvestigationModel,
)

QUESTION = "Who is William Jolliffe, 4th Baron Hylton's paternal grandfather? Search for evidence."
TOOL = types.Tool(function_declarations=[types.FunctionDeclaration(**TOOL_DECLARATION)])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tool-only", action="store_true", help="Attempt C and, if possible, the D continuations")
    parser.add_argument("--adapter-comparison", action="store_true", help="Compare manual and adapter initial turns")
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Choose a fresh diagnostic output")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    client = genai.Client(
        api_key=settings.gemini_api_key,
        http_options=types.HttpOptions(
            api_version="v1beta", timeout=30000, retry_options=types.HttpRetryOptions(attempts=1)
        ),
    )
    last_request = 0.0
    stopped = False

    def record(name, call, *, tools=False, history=False, thinking="default", step=1, chars=None):
        nonlocal last_request, stopped
        if stopped:
            return None
        time.sleep(max(0, 5.1 - (time.monotonic() - last_request)))
        last_request = time.monotonic()
        row = {
            "experiment": name,
            "model": GEMMA4_MODEL,
            "sdk": importlib.metadata.version("google-genai"),
            "step": step,
            "function_declarations": tools,
            "function_response_history": history,
            "thinking": thinking,
            "request_chars_estimate": chars,
            "retry_count": 0,
        }
        result = None
        try:
            result = call()
            row["status"] = "ok"
            usage = getattr(result, "usage_metadata", None)
            row["prompt_tokens"] = getattr(usage, "prompt_token_count", None)
            row["result_type"] = type(result).__name__
            action_usage = getattr(result, "usage", None)
            if action_usage is not None:
                row["usage"] = {
                    key: getattr(action_usage, key) for key in ("prompt_tokens", "candidate_tokens", "thinking_tokens")
                }
            if hasattr(result, "call_id"):
                row["valid_native_search"] = bool(result.call_id and result.query)
            candidates = getattr(result, "candidates", None)
            if candidates and candidates[0].content:
                calls = [part.function_call for part in candidates[0].content.parts if part.function_call]
                row["function_calls"] = len(calls)
                if len(calls) == 1:
                    call = calls[0]
                    query = (call.args or {}).get("query")
                    row["valid_native_search"] = (
                        call.name == "search_evidence"
                        and isinstance(query, str)
                        and bool(query.strip())
                        and len(query) <= 500
                    )
        except errors.APIError as error:
            row.update(
                status="provider_error",
                http_status=error.code,
                classification="quota_or_rate_limit" if error.code == 429 else "provider",
            )
            stopped = error.code == 429
        except Exception as error:
            diagnostic = safe_diagnostic(error) or {}
            row.update(status="error", error_type=type(error).__name__, diagnostic=diagnostic)
            stopped = diagnostic.get("http_status") == 429
        row["duration_ms"] = round((time.monotonic() - last_request) * 1000, 2)
        with args.output.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row) + "\n")
        print(json.dumps(row), flush=True)
        return result

    def generate(contents, config):
        return client.models.generate_content(model=GEMMA4_MODEL, contents=contents, config=config)

    try:
        if args.adapter_comparison:
            import manual_gemma_tool_test as manual

            class MinimalGemma(Gemma4InvestigationModel):
                def _build_config(self, turn, tool):
                    config = super()._build_config(turn, tool)
                    config.thinking_config = types.ThinkingConfig(thinking_level="minimal")
                    return config

            def adapter(model_type):
                async def run():
                    provider = model_type(
                        api_key=settings.gemini_api_key, model=GEMMA4_MODEL, timeout_seconds=30, max_output_tokens=1024
                    )
                    try:
                        return await provider.next_action(InvestigationTurn(question=manual.QUESTION, exchanges=()))
                    finally:
                        await provider.aclose()

                return asyncio.run(run())

            def manual_request():
                with genai.Client(
                    api_key=settings.gemini_api_key,
                    http_options=types.HttpOptions(
                        api_version="v1beta", timeout=60000, retry_options=types.HttpRetryOptions(attempts=1)
                    ),
                ) as manual_client:
                    return manual_client.models.generate_content(
                        model=GEMMA4_MODEL,
                        contents=[manual.question_content()],
                        config=manual.make_config(GEMMA4_MODEL, "default"),
                    )

            record("manual_initial", manual_request, tools=True, chars=len(manual.QUESTION))
            record(
                "adapter_default_1", lambda: adapter(Gemma4InvestigationModel), tools=True, chars=len(manual.QUESTION)
            )
            record(
                "adapter_minimal",
                lambda: adapter(MinimalGemma),
                tools=True,
                thinking="minimal",
                chars=len(manual.QUESTION),
            )
            record(
                "adapter_default_2", lambda: adapter(Gemma4InvestigationModel), tools=True, chars=len(manual.QUESTION)
            )
            return
        if not args.tool_only:
            record(
                "A_basic",
                lambda: generate(QUESTION, types.GenerateContentConfig(max_output_tokens=1024)),
                chars=len(QUESTION),
            )
            record(
                "B_minimal",
                lambda: generate(
                    QUESTION,
                    types.GenerateContentConfig(
                        max_output_tokens=1024, thinking_config=types.ThinkingConfig(thinking_level="minimal")
                    ),
                ),
                thinking="minimal",
                chars=len(QUESTION),
            )
        config = types.GenerateContentConfig(tools=[TOOL], max_output_tokens=1024)
        initial = record("C_native_tool", lambda: generate(QUESTION, config), tools=True, chars=len(QUESTION))
        if initial and initial.candidates and initial.candidates[0].content:
            content = initial.candidates[0].content
            calls = [p.function_call for p in content.parts if p.function_call]
            if len(calls) == 1 and calls[0].name == "search_evidence":
                call = calls[0]
                for name, evidence in (
                    (
                        "D_sufficient",
                        [
                            {
                                "evidence_id": "E1",
                                "excerpt": "William was the son of Hylton, 3rd Baron Hylton. Hylton was the son of Hedworth Jolliffe, 2nd Baron Hylton.",
                            }
                        ],
                    ),
                    ("D_insufficient", []),
                ):
                    part = types.Part.from_function_response(name=call.name, response={"results": evidence})
                    if call.id:
                        part.function_response.id = call.id
                    contents = [
                        types.Content(role="user", parts=[types.Part.from_text(text=QUESTION)]),
                        content,
                        types.Content(role="user", parts=[part]),
                    ]
                    record(
                        name,
                        lambda: generate(contents, config),
                        tools=True,
                        history=True,
                        step=2,
                        chars=len(str(contents)),
                    )

        def application():
            async def run():
                provider = Gemma4InvestigationModel(
                    api_key=settings.gemini_api_key, model=GEMMA4_MODEL, timeout_seconds=30, max_output_tokens=1024
                )
                try:
                    return await provider.next_action(InvestigationTurn(question=QUESTION, exchanges=()))
                finally:
                    await provider.aclose()

            return asyncio.run(run())

        if not args.tool_only:
            record("E_application_initial", application, tools=True, chars=len(QUESTION))
    finally:
        client.close()


if __name__ == "__main__":
    main()
