from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


PLUGIN_PATH = Path(__file__).with_name("__init__.py")
spec = importlib.util.spec_from_file_location("hermes_user_otel_plugin", PLUGIN_PATH)
plugin = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = plugin
spec.loader.exec_module(plugin)


def make_runtime():
    span_exporter = InMemorySpanExporter()
    metric_reader = InMemoryMetricReader()
    runtime = plugin.TelemetryRuntime(
        service_name="hermes-agent-test",
        service_version="test",
        environment="test",
        span_exporter=span_exporter,
        metric_reader=metric_reader,
    )
    plugin._runtime = runtime
    return runtime, span_exporter, metric_reader


def metric_points(metric_reader: InMemoryMetricReader) -> dict[str, list]:
    data = metric_reader.get_metrics_data()
    found: dict[str, list] = {}
    for resource_metric in data.resource_metrics:
        for scope_metric in resource_metric.scope_metrics:
            for metric in scope_metric.metrics:
                found.setdefault(metric.name, []).extend(metric.data.data_points)
    return found


def test_turn_and_llm_hooks_export_correlated_gen_ai_spans_and_usage_metrics():
    runtime, exporter, metric_reader = make_runtime()

    plugin.on_pre_llm_call(
        session_id="session-1",
        turn_id="turn-1",
        model="gpt-test",
        platform="discord",
        user_message="private prompt",
    )
    plugin.on_pre_api_request(
        session_id="session-1",
        turn_id="turn-1",
        api_request_id="request-1",
        model="gpt-test",
        provider="openai",
        api_mode="responses",
    )
    plugin.on_post_api_request(
        session_id="session-1",
        turn_id="turn-1",
        api_request_id="request-1",
        model="gpt-test",
        response_model="gpt-test-2026",
        provider="openai",
        api_mode="responses",
        api_duration=1.25,
        finish_reason="stop",
        usage={"input_tokens": 12, "output_tokens": 5, "total_tokens": 17},
    )
    plugin.on_post_llm_call(
        session_id="session-1",
        turn_id="turn-1",
        model="gpt-test",
        platform="discord",
        assistant_response="private response",
    )
    runtime.force_flush()

    spans = {span.name: span for span in exporter.get_finished_spans()}
    assert set(spans) == {"hermes.turn", "gen_ai.chat gpt-test"}
    assert spans["gen_ai.chat gpt-test"].parent.span_id == spans["hermes.turn"].context.span_id
    attrs = spans["gen_ai.chat gpt-test"].attributes
    assert attrs["gen_ai.provider.name"] == "openai"
    assert attrs["gen_ai.request.model"] == "gpt-test"
    assert attrs["gen_ai.response.model"] == "gpt-test-2026"
    assert attrs["gen_ai.usage.input_tokens"] == 12
    assert attrs["gen_ai.usage.output_tokens"] == 5
    assert "private prompt" not in repr(attrs)
    assert "private response" not in repr(attrs)

    metrics = metric_points(metric_reader)
    assert metrics["hermes.gen_ai.requests"][0].value == 1
    token_points = metrics["hermes.gen_ai.tokens"]
    assert sorted(point.value for point in token_points) == [5, 12]
    assert {point.attributes["gen_ai.token.type"] for point in token_points} == {"input", "output"}


def test_tool_hooks_export_child_span_and_duration_metric():
    runtime, exporter, metric_reader = make_runtime()

    plugin.on_pre_llm_call(session_id="session-2", turn_id="turn-2", model="m", platform="cli")
    plugin.on_pre_tool_call(
        session_id="session-2",
        turn_id="turn-2",
        tool_call_id="tool-1",
        tool_name="terminal",
        args={"command": "secret"},
    )
    plugin.on_post_tool_call(
        session_id="session-2",
        turn_id="turn-2",
        tool_call_id="tool-1",
        tool_name="terminal",
        status="ok",
        duration_ms=250,
        result="secret output",
    )
    plugin.on_post_llm_call(session_id="session-2", turn_id="turn-2", model="m", platform="cli")
    runtime.force_flush()

    spans = {span.name: span for span in exporter.get_finished_spans()}
    tool_span = spans["execute_tool terminal"]
    assert tool_span.attributes["gen_ai.tool.name"] == "terminal"
    assert tool_span.attributes["hermes.tool.status"] == "ok"
    assert "secret" not in repr(tool_span.attributes)
    assert tool_span.parent.span_id == spans["hermes.turn"].context.span_id

    metrics = metric_points(metric_reader)
    assert metrics["hermes.tool.calls"][0].value == 1
    assert metrics["hermes.tool.duration"][0].sum == 0.25


def test_api_error_closes_span_with_error_status_and_metric():
    runtime, exporter, metric_reader = make_runtime()

    plugin.on_pre_llm_call(session_id="session-3", turn_id="turn-3", model="m", platform="discord")
    plugin.on_pre_api_request(
        session_id="session-3",
        turn_id="turn-3",
        api_request_id="request-3",
        model="m",
        provider="openai",
    )
    plugin.on_api_request_error(
        session_id="session-3",
        turn_id="turn-3",
        api_request_id="request-3",
        model="m",
        provider="openai",
        api_duration=0.5,
        status_code=429,
        retryable=True,
        error={"type": "RateLimitError", "message": "secret provider response"},
    )
    plugin.on_session_end(session_id="session-3", turn_id="turn-3", completed=False, interrupted=False)
    runtime.force_flush()

    spans = {span.name: span for span in exporter.get_finished_spans()}
    api_span = spans["gen_ai.chat m"]
    assert api_span.status.status_code.name == "ERROR"
    assert api_span.attributes["error.type"] == "RateLimitError"
    assert api_span.attributes["http.response.status_code"] == 429
    assert "secret provider response" not in repr(api_span.attributes)

    metrics = metric_points(metric_reader)
    assert metrics["hermes.gen_ai.errors"][0].value == 1
