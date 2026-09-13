"""OpenTelemetry observability plugin for Hermes Agent.

Exports agent turns, model requests, tool calls, token usage, latency, errors,
and estimated cost to an OTLP/HTTP collector. Content capture is opt-in.
"""
from __future__ import annotations

import atexit
import importlib.util
import json
import logging
import math
import os
from pathlib import Path
import socket
import sys
import threading
import time
from typing import Any, Callable, Optional

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.metrics import Observation
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

logger = logging.getLogger(__name__)

PLUGIN_VERSION = "1.3.0"
_DEFAULT_ENDPOINT = "http://127.0.0.1:4318"
_MAX_CONTENT_CHARS = 2_000


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_bool(name: str, default: bool = False) -> bool:
    value = _env(name)
    if not value:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


def _clean_endpoint(endpoint: str) -> str:
    endpoint = endpoint.rstrip("/")
    for suffix in ("/v1/traces", "/v1/metrics", "/v1/logs"):
        if endpoint.endswith(suffix):
            endpoint = endpoint[: -len(suffix)]
            break
    return endpoint


def _value(source: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(source, dict) and name in source:
            return source[name]
        if source is not None and hasattr(source, name):
            return getattr(source, name)
    return default


def _integer(source: Any, *names: str) -> int:
    value = _value(source, *names, default=0)
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _string(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _bounded_json(value: Any) -> str:
    try:
        text = json.dumps(value, default=str, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        text = repr(value)
    if len(text) > _MAX_CONTENT_CHARS:
        return text[:_MAX_CONTENT_CHARS] + "...[truncated]"
    return text


def _pricing_module() -> Any:
    """Load the sibling pure module even when Hermes loads this file directly."""
    module_name = "_hermes_otel_experiment_pricing_v1"
    module = sys.modules.get(module_name)
    if module is not None:
        return module
    spec = importlib.util.spec_from_file_location(module_name, Path(__file__).with_name("pricing.py"))
    if spec is None or spec.loader is None:
        raise ImportError("Could not load OTEL pricing module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _turn_key(kwargs: dict[str, Any]) -> tuple[str, str]:
    return (_string(kwargs.get("session_id")), _string(kwargs.get("turn_id")))


def _api_key(kwargs: dict[str, Any]) -> str:
    request_id = _string(kwargs.get("api_request_id"))
    if request_id:
        return request_id
    session_id, turn_id = _turn_key(kwargs)
    return f"{session_id}:{turn_id}:{kwargs.get('api_call_count', '')}"


def _tool_key(kwargs: dict[str, Any]) -> str:
    call_id = _string(kwargs.get("tool_call_id"))
    if call_id:
        return call_id
    session_id, turn_id = _turn_key(kwargs)
    return f"{session_id}:{turn_id}:{kwargs.get('tool_name', '')}:{threading.get_ident()}"


def _common_attributes(kwargs: dict[str, Any]) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    mapping = {
        "session.id": "session_id",
        "hermes.task.id": "task_id",
        "hermes.turn.id": "turn_id",
        "hermes.platform": "platform",
        "hermes.telemetry.schema_version": "telemetry_schema_version",
    }
    for target, source in mapping.items():
        value = kwargs.get(source)
        if value not in (None, ""):
            attrs[target] = _string(value)
    return attrs


def _model_attributes(kwargs: dict[str, Any]) -> dict[str, Any]:
    attrs = _common_attributes(kwargs)
    mapping = {
        "gen_ai.provider.name": "provider",
        "gen_ai.request.model": "model",
        "hermes.gen_ai.api_mode": "api_mode",
    }
    for target, source in mapping.items():
        value = kwargs.get(source)
        if value not in (None, ""):
            attrs[target] = _string(value)
    return attrs


def _metric_attributes(kwargs: dict[str, Any], *, status: str = "") -> dict[str, str]:
    attrs: dict[str, str] = {}
    for target, source in (
        ("gen_ai.provider.name", "provider"),
        ("gen_ai.request.model", "model"),
        ("hermes.platform", "platform"),
    ):
        value = kwargs.get(source)
        if value not in (None, ""):
            attrs[target] = _string(value)
    if status:
        attrs["hermes.status"] = status
    return attrs


class TelemetryRuntime:
    """Owns OpenTelemetry providers, instruments, and active Hermes spans."""

    def __init__(
        self,
        *,
        service_name: str,
        service_version: str,
        environment: str,
        endpoint: str = _DEFAULT_ENDPOINT,
        capture_content: bool = False,
        hypothetical_cost_enabled: bool = True,
        codex_usage_enabled: bool = False,
        codex_usage_interval_seconds: float = 300,
        account_usage_fetcher: Optional[Callable[[str], Any]] = None,
        span_exporter: Optional[SpanExporter] = None,
        metric_reader: Optional[MetricReader] = None,
    ) -> None:
        resource_attributes: dict[str, str] = {
            "service.name": service_name,
            "service.namespace": "hermes",
            "service.version": service_version,
            "host.name": socket.gethostname(),
            "telemetry.sdk.language": "python",
            "hermes.plugin.version": PLUGIN_VERSION,
        }
        if environment:
            resource_attributes["deployment.environment.name"] = environment
        self.resource = Resource.create(resource_attributes)
        self.capture_content = capture_content
        self.hypothetical_cost_enabled = hypothetical_cost_enabled
        self.codex_usage_enabled = codex_usage_enabled
        self.codex_usage_interval_seconds = max(1.0, float(codex_usage_interval_seconds))
        self._account_usage_fetcher = account_usage_fetcher
        self._codex_usage_last_attempt = float("-inf")
        self._codex_usage_snapshot: Any = None
        self._lock = threading.RLock()
        self._turn_spans: dict[tuple[str, str], Span] = {}
        self._api_spans: dict[str, Span] = {}
        self._tool_spans: dict[str, Span] = {}

        if span_exporter is None:
            base = _clean_endpoint(endpoint)
            span_exporter = OTLPSpanExporter(endpoint=f"{base}/v1/traces")
        self.tracer_provider = TracerProvider(resource=self.resource, shutdown_on_exit=False)
        self.tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
        self.tracer = self.tracer_provider.get_tracer("hermes.otel", PLUGIN_VERSION)

        if metric_reader is None:
            base = _clean_endpoint(endpoint)
            try:
                interval_ms = max(1_000, int(_env("HERMES_OTEL_METRIC_EXPORT_INTERVAL_MS", "10000")))
            except ValueError:
                interval_ms = 10_000
            metric_reader = PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=f"{base}/v1/metrics"),
                export_interval_millis=interval_ms,
            )
        self.meter_provider = MeterProvider(
            resource=self.resource,
            metric_readers=[metric_reader],
            shutdown_on_exit=False,
        )
        meter = self.meter_provider.get_meter("hermes.otel", PLUGIN_VERSION)
        self.request_counter = meter.create_counter(
            "hermes.gen_ai.requests", unit="{request}", description="Completed model API requests"
        )
        self.error_counter = meter.create_counter(
            "hermes.gen_ai.errors", unit="{error}", description="Failed model API requests"
        )
        self.token_counter = meter.create_counter(
            "hermes.gen_ai.tokens", unit="{token}", description="Model token usage by token type"
        )
        self.cost_counter = meter.create_counter(
            "hermes.gen_ai.estimated_cost", unit="USD", description="Estimated model usage cost in USD"
        )
        self.hypothetical_cost_counter = meter.create_counter(
            "hermes.gen_ai.hypothetical_api_cost",
            unit="USD",
            description="Counterfactual OpenAI Standard API-equivalent cost; not an invoice",
        )
        self.request_duration = meter.create_histogram(
            "hermes.gen_ai.request.duration", unit="s", description="Model API request duration"
        )
        self.tool_counter = meter.create_counter(
            "hermes.tool.calls", unit="{call}", description="Hermes tool calls"
        )
        self.tool_duration = meter.create_histogram(
            "hermes.tool.duration", unit="s", description="Hermes tool call duration"
        )
        self.active_turns = meter.create_observable_gauge(
            "hermes.turns.active",
            callbacks=[self._observe_active_turns],
            unit="{turn}",
            description="Currently active Hermes turns",
        )
        self.codex_limit_remaining = None
        self.codex_limit_reset = None
        if self.codex_usage_enabled:
            self.codex_limit_remaining = meter.create_observable_gauge(
                "hermes.codex.limit.remaining",
                callbacks=[self._observe_codex_limit_remaining],
                unit="1",
                description="Fraction of each OpenAI Codex subscription limit remaining",
            )
            self.codex_limit_reset = meter.create_observable_gauge(
                "hermes.codex.limit.reset",
                callbacks=[self._observe_codex_limit_reset],
                unit="s",
                description="Unix timestamp when each OpenAI Codex subscription limit resets",
            )

    def _observe_active_turns(self, _options: Any) -> list[Observation]:
        with self._lock:
            return [Observation(len(self._turn_spans))]

    def _refresh_codex_usage_if_due(self) -> None:
        now = time.monotonic()
        with self._lock:
            if now - self._codex_usage_last_attempt < self.codex_usage_interval_seconds:
                return
            self._codex_usage_last_attempt = now
        try:
            fetcher = self._account_usage_fetcher
            if fetcher is None:
                from agent.account_usage import fetch_account_usage

                fetcher = fetch_account_usage
            snapshot = fetcher("openai-codex")
        except Exception as exc:
            logger.warning("Could not collect OpenAI Codex account limits: %s", exc)
            snapshot = None
        with self._lock:
            self._codex_usage_snapshot = snapshot

    def _codex_window_attributes(self, snapshot: Any, window: Any) -> dict[str, Any]:
        attrs: dict[str, Any] = {
            "gen_ai.provider.name": "openai-codex",
            "hermes.codex.limit.name": _string(getattr(window, "limit_name", None))
            or _string(window.label),
            "hermes.codex.limit.window": _string(getattr(window, "window_label", None))
            or _string(window.label),
        }
        plan = _string(getattr(snapshot, "plan", None))
        if plan:
            attrs["hermes.account.plan"] = plan
        window_seconds = getattr(window, "window_seconds", None)
        if isinstance(window_seconds, int) and window_seconds > 0:
            attrs["hermes.codex.limit.window_seconds"] = window_seconds
        metered_feature = _string(getattr(window, "metered_feature", None))
        if metered_feature:
            attrs["hermes.codex.limit.metered_feature"] = metered_feature
        return attrs

    def _codex_snapshot(self) -> Any:
        self._refresh_codex_usage_if_due()
        with self._lock:
            return self._codex_usage_snapshot

    def _observe_codex_limit_remaining(self, _options: Any) -> list[Observation]:
        snapshot = self._codex_snapshot()
        if snapshot is None:
            return []
        observations: list[Observation] = []
        for window in snapshot.windows:
            used_percent = getattr(window, "used_percent", None)
            if not isinstance(used_percent, (int, float)) or not math.isfinite(used_percent):
                continue
            remaining = max(0.0, min(1.0, 1.0 - float(used_percent) / 100.0))
            observations.append(
                Observation(remaining, attributes=self._codex_window_attributes(snapshot, window))
            )
        return observations

    def _observe_codex_limit_reset(self, _options: Any) -> list[Observation]:
        snapshot = self._codex_snapshot()
        if snapshot is None:
            return []
        observations: list[Observation] = []
        for window in snapshot.windows:
            reset_at = getattr(window, "reset_at", None)
            if reset_at is None:
                continue
            try:
                reset_timestamp = float(reset_at.timestamp())
            except (AttributeError, TypeError, ValueError, OverflowError):
                continue
            if math.isfinite(reset_timestamp):
                observations.append(
                    Observation(
                        reset_timestamp,
                        attributes=self._codex_window_attributes(snapshot, window),
                    )
                )
        return observations

    def _parent_context(self, kwargs: dict[str, Any]) -> Any:
        with self._lock:
            parent = self._turn_spans.get(_turn_key(kwargs))
        return trace.set_span_in_context(parent) if parent is not None else None

    def pre_llm(self, **kwargs: Any) -> None:
        key = _turn_key(kwargs)
        attrs = _common_attributes(kwargs)
        model = kwargs.get("model")
        if model:
            attrs["gen_ai.request.model"] = _string(model)
        span = self.tracer.start_span("hermes.turn", kind=SpanKind.INTERNAL, attributes=attrs)
        if self.capture_content and kwargs.get("user_message") is not None:
            span.add_event("gen_ai.user.message", {"content": _bounded_json(kwargs.get("user_message"))})
        with self._lock:
            previous = self._turn_spans.pop(key, None)
            self._turn_spans[key] = span
        if previous is not None:
            previous.set_status(Status(StatusCode.ERROR, "superseded by a new turn span"))
            previous.end()

    def post_llm(self, **kwargs: Any) -> None:
        with self._lock:
            span = self._turn_spans.pop(_turn_key(kwargs), None)
        if span is None:
            return
        if self.capture_content and kwargs.get("assistant_response") is not None:
            span.add_event("gen_ai.assistant.message", {"content": _bounded_json(kwargs.get("assistant_response"))})
        span.set_status(Status(StatusCode.OK))
        span.end()

    def session_end(self, **kwargs: Any) -> None:
        session_id = _string(kwargs.get("session_id"))
        with self._lock:
            keys = [key for key in self._turn_spans if key[0] == session_id]
            spans = [self._turn_spans.pop(key) for key in keys]
        for span in spans:
            if kwargs.get("completed"):
                span.set_status(Status(StatusCode.OK))
            else:
                reason = "interrupted" if kwargs.get("interrupted") else "incomplete"
                span.set_attribute("hermes.turn.outcome", reason)
                span.set_status(Status(StatusCode.ERROR, reason))
            span.end()

    def pre_api(self, **kwargs: Any) -> None:
        model = _string(kwargs.get("model")) or "unknown"
        span = self.tracer.start_span(
            f"gen_ai.chat {model}",
            context=self._parent_context(kwargs),
            kind=SpanKind.CLIENT,
            attributes=_model_attributes(kwargs),
        )
        if kwargs.get("api_request_id"):
            span.set_attribute("hermes.api.request.id", _string(kwargs["api_request_id"]))
        if kwargs.get("base_url"):
            span.set_attribute("server.address", _string(kwargs["base_url"]))
        if self.capture_content and kwargs.get("request") is not None:
            span.add_event("gen_ai.request", {"content": _bounded_json(kwargs.get("request"))})
        with self._lock:
            previous = self._api_spans.pop(_api_key(kwargs), None)
            self._api_spans[_api_key(kwargs)] = span
        if previous is not None:
            previous.set_status(Status(StatusCode.ERROR, "superseded by a new API span"))
            previous.end()

    def post_api(self, **kwargs: Any) -> None:
        with self._lock:
            span = self._api_spans.pop(_api_key(kwargs), None)
        if span is None:
            return
        raw_usage = kwargs.get("usage")
        usage = raw_usage or {}
        input_tokens = _integer(usage, "input_tokens", "prompt_tokens")
        output_tokens = _integer(usage, "output_tokens", "completion_tokens")
        cache_read = _integer(usage, "cache_read_tokens", "cache_read_input_tokens")
        cache_write = _integer(usage, "cache_write_tokens", "cache_creation_input_tokens")
        reasoning = _integer(usage, "reasoning_tokens")
        if kwargs.get("response_model"):
            span.set_attribute("gen_ai.response.model", _string(kwargs["response_model"]))
        if kwargs.get("finish_reason"):
            span.set_attribute("gen_ai.response.finish_reasons", (_string(kwargs["finish_reason"]),))
        span.set_attribute("gen_ai.usage.input_tokens", input_tokens + cache_read + cache_write)
        span.set_attribute("gen_ai.usage.output_tokens", output_tokens)
        span.set_attribute("hermes.gen_ai.usage.cache_read_tokens", cache_read)
        span.set_attribute("hermes.gen_ai.usage.cache_write_tokens", cache_write)
        span.set_attribute("hermes.gen_ai.usage.reasoning_tokens", reasoning)
        if self.capture_content and kwargs.get("response") is not None:
            span.add_event("gen_ai.response", {"content": _bounded_json(kwargs.get("response"))})

        attrs = _metric_attributes(kwargs, status="ok")
        self.request_counter.add(1, attrs)
        duration = float(kwargs.get("api_duration") or 0.0)
        if duration >= 0:
            self.request_duration.record(duration, attrs)
        for token_type, count in (
            ("input", input_tokens),
            ("output", output_tokens),
            ("cache_read", cache_read),
            ("cache_write", cache_write),
            ("reasoning", reasoning),
        ):
            if count:
                self.token_counter.add(count, {**attrs, "gen_ai.token.type": token_type})
        self._record_estimated_cost(span, kwargs, usage, attrs)
        self._record_hypothetical_api_cost(span, kwargs, raw_usage, attrs)
        span.set_status(Status(StatusCode.OK))
        span.end()

    def _record_estimated_cost(
        self, span: Span, kwargs: dict[str, Any], usage: Any, attrs: dict[str, str]
    ) -> None:
        try:
            from agent.usage_pricing import CanonicalUsage, estimate_usage_cost

            canonical = CanonicalUsage(
                input_tokens=_integer(usage, "input_tokens", "prompt_tokens"),
                output_tokens=_integer(usage, "output_tokens", "completion_tokens"),
                cache_read_tokens=_integer(
                    usage, "cache_read_tokens", "cache_read_input_tokens"
                ),
                cache_write_tokens=_integer(
                    usage, "cache_write_tokens", "cache_creation_input_tokens"
                ),
                reasoning_tokens=_integer(usage, "reasoning_tokens"),
                request_count=_integer(usage, "request_count") or 1,
            )
            cost = estimate_usage_cost(
                _string(kwargs.get("model")),
                canonical,
                provider=_string(kwargs.get("provider")),
                base_url=_string(kwargs.get("base_url")),
            )
            span.set_attribute("hermes.gen_ai.cost.status", cost.status)
            span.set_attribute("hermes.gen_ai.cost.source", cost.source)
            if cost.amount_usd is not None:
                amount = float(cost.amount_usd)
                span.set_attribute("hermes.gen_ai.cost.usd", amount)
                self.cost_counter.add(amount, {**attrs, "hermes.cost.status": cost.status})
        except Exception as exc:
            logger.debug("Could not estimate model cost: %s", exc)

    def _record_hypothetical_api_cost(
        self, span: Span, kwargs: dict[str, Any], usage: Any, attrs: dict[str, str]
    ) -> None:
        prefix = "hermes.gen_ai.hypothetical_api_cost"
        if not self.hypothetical_cost_enabled:
            span.set_attribute(f"{prefix}.estimation_status", "disabled")
            return
        if usage is None:
            span.set_attribute(f"{prefix}.estimation_status", "missing_usage")
            return
        has_canonical_usage = (
            (
                isinstance(usage, dict)
                and any(
                    name in usage
                    for name in (
                        "input_tokens", "output_tokens",
                        "prompt_tokens", "completion_tokens",
                    )
                )
            )
            or any(
                hasattr(usage, name)
                for name in (
                    "input_tokens", "output_tokens",
                    "prompt_tokens", "completion_tokens",
                )
            )
        )
        if not has_canonical_usage:
            span.set_attribute(f"{prefix}.estimation_status", "missing_usage")
            return
        try:
            pricing = _pricing_module()
            canonical = pricing.Usage(
                input_tokens=_integer(usage, "input_tokens", "prompt_tokens"),
                output_tokens=_integer(usage, "output_tokens", "completion_tokens"),
                cache_read_tokens=_integer(usage, "cache_read_tokens", "cache_read_input_tokens"),
                cache_write_tokens=_integer(usage, "cache_write_tokens", "cache_creation_input_tokens"),
                reasoning_tokens=_integer(usage, "reasoning_tokens"),
            )
            result = pricing.estimate_hypothetical_api_cost(_string(kwargs.get("model")), canonical)
            provenance = {
                f"{prefix}.estimation_status": result.estimation_status,
                f"{prefix}.counterfactual": result.counterfactual,
                f"{prefix}.not_an_invoice": True,
                f"{prefix}.scenario": result.scenario,
                f"{prefix}.currency": result.currency,
                f"{prefix}.pricing_version": result.pricing_version,
                f"{prefix}.formula_version": result.formula_version,
                f"{prefix}.mapping_version": result.mapping_version,
                f"{prefix}.mapping_method": result.mapping_method,
                f"{prefix}.confidence": result.confidence,
                f"{prefix}.context_band": result.context_band,
                f"{prefix}.priced_model": result.priced_model,
                f"{prefix}.source_url": result.source_url,
                f"{prefix}.observed_date": result.observed_date,
                f"{prefix}.hosted_tool_fees_excluded": result.hosted_tool_fees_excluded,
                f"{prefix}.formula": "uncached_input + cache_read + cache_write + total_output; reasoning included in output",
            }
            for name, value in provenance.items():
                span.set_attribute(name, value)
            if result.amount_usd is None:
                return
            amount = float(result.amount_usd)
            span.set_attribute(f"{prefix}.usd", amount)
            metric_attrs = {
                **attrs,
                "hermes.pricing.priced_model": result.priced_model,
                "hermes.pricing.scenario": result.scenario,
                "hermes.pricing.context_band": result.context_band,
                "hermes.pricing.currency": result.currency,
                "hermes.pricing.version": result.pricing_version,
                "hermes.pricing.mapping_method": result.mapping_method,
                "hermes.pricing.confidence": result.confidence,
                "hermes.pricing.estimation_status": result.estimation_status,
            }
            self.hypothetical_cost_counter.add(amount, metric_attrs)
        except Exception as exc:
            span.set_attribute(f"{prefix}.estimation_status", "error")
            logger.debug("Could not estimate hypothetical API cost: %s", exc)

    def api_error(self, **kwargs: Any) -> None:
        with self._lock:
            span = self._api_spans.pop(_api_key(kwargs), None)
        if span is None:
            return
        error = kwargs.get("error") or {}
        error_type = _string(_value(error, "type", default="APIError")) or "APIError"
        span.set_attribute("error.type", error_type)
        if kwargs.get("status_code") is not None:
            try:
                span.set_attribute("http.response.status_code", int(kwargs["status_code"]))
            except (TypeError, ValueError):
                pass
        span.set_attribute("hermes.gen_ai.retryable", bool(kwargs.get("retryable")))
        attrs = _metric_attributes(kwargs, status="error")
        attrs["error.type"] = error_type
        self.error_counter.add(1, attrs)
        duration = float(kwargs.get("api_duration") or 0.0)
        if duration >= 0:
            self.request_duration.record(duration, attrs)
        span.set_status(Status(StatusCode.ERROR, error_type))
        span.end()

    def pre_tool(self, **kwargs: Any) -> None:
        tool_name = _string(kwargs.get("tool_name")) or "unknown"
        attrs = _common_attributes(kwargs)
        attrs["gen_ai.tool.name"] = tool_name
        if kwargs.get("tool_call_id"):
            attrs["gen_ai.tool.call.id"] = _string(kwargs["tool_call_id"])
        span = self.tracer.start_span(
            f"execute_tool {tool_name}",
            context=self._parent_context(kwargs),
            kind=SpanKind.INTERNAL,
            attributes=attrs,
        )
        if self.capture_content and kwargs.get("args") is not None:
            span.add_event("gen_ai.tool.input", {"content": _bounded_json(kwargs.get("args"))})
        with self._lock:
            previous = self._tool_spans.pop(_tool_key(kwargs), None)
            self._tool_spans[_tool_key(kwargs)] = span
        if previous is not None:
            previous.set_status(Status(StatusCode.ERROR, "superseded by a new tool span"))
            previous.end()

    def post_tool(self, **kwargs: Any) -> None:
        with self._lock:
            span = self._tool_spans.pop(_tool_key(kwargs), None)
        if span is None:
            return
        tool_name = _string(kwargs.get("tool_name")) or "unknown"
        status = _string(kwargs.get("status")) or "ok"
        span.set_attribute("hermes.tool.status", status)
        if kwargs.get("error_type"):
            span.set_attribute("error.type", _string(kwargs["error_type"]))
        if self.capture_content and kwargs.get("result") is not None:
            span.add_event("gen_ai.tool.output", {"content": _bounded_json(kwargs.get("result"))})
        attrs = {"gen_ai.tool.name": tool_name, "hermes.status": status}
        self.tool_counter.add(1, attrs)
        duration_s = max(0.0, float(kwargs.get("duration_ms") or 0.0) / 1000.0)
        self.tool_duration.record(duration_s, attrs)
        if status == "ok":
            span.set_status(Status(StatusCode.OK))
        else:
            span.set_status(Status(StatusCode.ERROR, status))
        span.end()

    def force_flush(self, timeout_millis: int = 10_000) -> bool:
        traces_ok = self.tracer_provider.force_flush(timeout_millis=timeout_millis)
        metrics_ok = self.meter_provider.force_flush(timeout_millis=timeout_millis)
        return bool(traces_ok and metrics_ok)

    def shutdown(self) -> None:
        try:
            self.tracer_provider.shutdown()
        finally:
            self.meter_provider.shutdown()


_runtime: Optional[TelemetryRuntime] = None
_runtime_lock = threading.Lock()


def _get_runtime() -> TelemetryRuntime:
    global _runtime
    if _runtime is not None:
        return _runtime
    with _runtime_lock:
        if _runtime is None:
            _runtime = TelemetryRuntime(
                service_name=_env("HERMES_OTEL_SERVICE_NAME", "hermes-agent"),
                service_version=_env("HERMES_OTEL_SERVICE_VERSION", "0.16.0"),
                environment=_env("HERMES_OTEL_ENVIRONMENT", "zerotwo"),
                endpoint=_env("HERMES_OTEL_ENDPOINT", _DEFAULT_ENDPOINT),
                capture_content=_env_bool("HERMES_OTEL_CAPTURE_CONTENT", False),
                hypothetical_cost_enabled=_env_bool("HERMES_OTEL_HYPOTHETICAL_COST_ENABLED", True),
                codex_usage_enabled=_env_bool("HERMES_OTEL_CODEX_USAGE_ENABLED", True),
                codex_usage_interval_seconds=_env_float(
                    "HERMES_OTEL_CODEX_USAGE_INTERVAL_SECONDS", 300
                ),
            )
            logger.info(
                "Hermes OpenTelemetry enabled: endpoint=%s service=%s content_capture=%s",
                _env("HERMES_OTEL_ENDPOINT", _DEFAULT_ENDPOINT),
                _env("HERMES_OTEL_SERVICE_NAME", "hermes-agent"),
                _runtime.capture_content,
            )
    return _runtime


def _safe(method: str, kwargs: dict[str, Any]) -> None:
    try:
        getattr(_get_runtime(), method)(**kwargs)
    except Exception as exc:
        logger.warning("Hermes OpenTelemetry hook %s failed: %s", method, exc)


def on_pre_llm_call(**kwargs: Any) -> None:
    _safe("pre_llm", kwargs)


def on_post_llm_call(**kwargs: Any) -> None:
    _safe("post_llm", kwargs)


def on_session_end(**kwargs: Any) -> None:
    _safe("session_end", kwargs)


def on_pre_api_request(**kwargs: Any) -> None:
    _safe("pre_api", kwargs)


def on_post_api_request(**kwargs: Any) -> None:
    _safe("post_api", kwargs)


def on_api_request_error(**kwargs: Any) -> None:
    _safe("api_error", kwargs)


def on_pre_tool_call(**kwargs: Any) -> None:
    _safe("pre_tool", kwargs)


def on_post_tool_call(**kwargs: Any) -> None:
    _safe("post_tool", kwargs)


def _shutdown() -> None:
    if _runtime is not None:
        try:
            _runtime.shutdown()
        except Exception:
            pass


atexit.register(_shutdown)


def register(ctx: Any) -> None:
    ctx.register_hook("pre_llm_call", on_pre_llm_call)
    ctx.register_hook("post_llm_call", on_post_llm_call)
    ctx.register_hook("on_session_end", on_session_end)
    ctx.register_hook("pre_api_request", on_pre_api_request)
    ctx.register_hook("post_api_request", on_post_api_request)
    ctx.register_hook("api_request_error", on_api_request_error)
    ctx.register_hook("pre_tool_call", on_pre_tool_call)
    ctx.register_hook("post_tool_call", on_post_tool_call)
