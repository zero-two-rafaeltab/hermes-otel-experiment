from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types

from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


PLUGIN_PATH = Path(__file__).with_name("__init__.py")
spec = importlib.util.spec_from_file_location("hermes_user_otel_plugin", PLUGIN_PATH)
plugin = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = plugin
spec.loader.exec_module(plugin)


def make_runtime(*, hypothetical_cost_enabled=True):
    span_exporter = InMemorySpanExporter()
    metric_reader = InMemoryMetricReader()
    runtime = plugin.TelemetryRuntime(
        service_name="hermes-agent-test",
        service_version="test",
        environment="test",
        span_exporter=span_exporter,
        metric_reader=metric_reader,
        hypothetical_cost_enabled=hypothetical_cost_enabled,
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


def _complete_api(runtime, *, model="gpt-5.6-sol", usage=None, response="private response"):
    kwargs = dict(
        session_id="cost-session", turn_id="cost-turn", api_request_id="cost-request",
        model=model, provider="openai-codex",
    )
    plugin.on_pre_api_request(**kwargs, request={"prompt": "private prompt"})
    plugin.on_post_api_request(**kwargs, usage=usage, response=response, api_duration=0.1)


def test_hypothetical_metric_is_nonzero_while_subscription_cost_stays_zero(monkeypatch):
    usage_pricing = types.ModuleType("agent.usage_pricing")

    class CanonicalUsage:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    usage_pricing.CanonicalUsage = CanonicalUsage
    usage_pricing.estimate_usage_cost = lambda *args, **kwargs: types.SimpleNamespace(
        status="included", source="custom_contract", amount_usd=0
    )
    agent = types.ModuleType("agent")
    agent.usage_pricing = usage_pricing
    monkeypatch.setitem(sys.modules, "agent", agent)
    monkeypatch.setitem(sys.modules, "agent.usage_pricing", usage_pricing)

    runtime, exporter, metric_reader = make_runtime()
    _complete_api(runtime, model="gpt-5.6", usage={
        "input_tokens": 100_000, "cache_read_tokens": 50_000,
        "cache_write_tokens": 10_000, "output_tokens": 20_000,
        "reasoning_tokens": 5_000,
    })
    runtime.force_flush()

    metrics = metric_points(metric_reader)
    assert metrics["hermes.gen_ai.estimated_cost"][0].value == 0
    point = metrics["hermes.gen_ai.hypothetical_api_cost"][0]
    assert point.value == 1.1875
    assert point.attributes["hermes.pricing.priced_model"] == "gpt-5.6-sol"
    assert point.attributes["hermes.pricing.mapping_method"] == "official_alias"
    span = exporter.get_finished_spans()[0]
    assert span.attributes["hermes.gen_ai.cost.status"] == "included"
    assert span.attributes["hermes.gen_ai.hypothetical_api_cost.counterfactual"] is True
    assert span.attributes["hermes.gen_ai.hypothetical_api_cost.hosted_tool_fees_excluded"] is True
    assert not span.events
    assert "private prompt" not in repr(span.attributes)
    assert "private response" not in repr(span.attributes)


def test_unknown_model_sets_unmapped_status_without_hypothetical_metric():
    runtime, exporter, metric_reader = make_runtime()
    _complete_api(runtime, model="gpt-unknown", usage={"input_tokens": 100})
    runtime.force_flush()
    assert "hermes.gen_ai.hypothetical_api_cost" not in metric_points(metric_reader)
    assert exporter.get_finished_spans()[0].attributes[
        "hermes.gen_ai.hypothetical_api_cost.estimation_status"
    ] == "unmapped"


def test_missing_usage_and_disabled_toggle_do_not_emit_hypothetical_metric():
    runtime, exporter, metric_reader = make_runtime(hypothetical_cost_enabled=False)
    _complete_api(runtime, usage={"input_tokens": 100})
    runtime.force_flush()
    assert "hermes.gen_ai.hypothetical_api_cost" not in metric_points(metric_reader)
    assert exporter.get_finished_spans()[0].attributes[
        "hermes.gen_ai.hypothetical_api_cost.estimation_status"
    ] == "disabled"

    runtime2, exporter2, metric_reader2 = make_runtime()
    _complete_api(runtime2, usage=None)
    runtime2.force_flush()
    assert "hermes.gen_ai.hypothetical_api_cost" not in metric_points(metric_reader2)
    assert exporter2.get_finished_spans()[0].attributes[
        "hermes.gen_ai.hypothetical_api_cost.estimation_status"
    ] == "missing_usage"
