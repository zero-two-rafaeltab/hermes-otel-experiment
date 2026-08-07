# Rafael / Zero Two's personal experiment — unsupported

> **Warning:** This repository is Rafael (Zero Two)'s personal experiment, tailored to one environment. It is **not** a polished or public-facing Hermes plugin, carries **no support commitment**, and is not expected to install or remain compatible in other environments. Use it only as reference and expect breakage.

# Hermes OpenTelemetry experiment

A local Hermes Agent plugin that translates lifecycle hooks into OpenTelemetry traces and metrics and exports them over OTLP/HTTP.

## Architecture

- Hermes hook handlers delegate to one process-local `TelemetryRuntime`.
- The runtime owns isolated OpenTelemetry tracer and meter providers.
- Turn spans parent model-request and tool-call spans.
- Counters, histograms, and gauges report request, error, token, tool, duration, cost, and Codex subscription-limit data.
- OTLP/HTTP defaults to a collector on `http://127.0.0.1:4318`.

No collector, Grafana dashboard, gateway, or Hermes core code belongs to this repository.

## Privacy defaults

Prompt, response, tool-input, and tool-output capture is **off by default**. Normal spans retain operational metadata and numeric usage, but not content. Enabling `HERMES_OTEL_CAPTURE_CONTENT` is an explicit privacy trade-off; captured content is bounded but not redacted. Do not enable it unless the collector and downstream storage are appropriate for sensitive data.

Never commit `.env` files, credentials, local collector configuration, logs, sessions, telemetry exports, generated caches, or user data. Repository hygiene is enforced by `.gitignore` and a lightweight gitleaks workflow.

## High-level setup (this environment only)

1. Use a Hermes Agent installation that supports the hooks listed in `plugin.yaml`.
2. Install compatible OpenTelemetry Python SDK and OTLP HTTP exporter packages in the Hermes environment.
3. Place this directory in the active profile's observability-plugin location.
4. Configure an OTLP/HTTP endpoint with environment variables if the local default is unsuitable.
5. Enable the plugin using the mechanisms of that Hermes installation.

Relevant non-secret settings:

- `HERMES_OTEL_ENDPOINT` (default `http://127.0.0.1:4318`)
- `HERMES_OTEL_SERVICE_NAME` (default `hermes-agent`)
- `HERMES_OTEL_SERVICE_VERSION`
- `HERMES_OTEL_ENVIRONMENT`
- `HERMES_OTEL_METRIC_EXPORT_INTERVAL_MS`
- `HERMES_OTEL_CAPTURE_CONTENT` (default false)
- `HERMES_OTEL_HYPOTHETICAL_COST_ENABLED` (default true for this experiment; independently disable with `false`)
- `HERMES_OTEL_CODEX_USAGE_ENABLED` (default true; independently disable with `false`)
- `HERMES_OTEL_CODEX_USAGE_INTERVAL_SECONDS` (default `300`)

There is deliberately no environment template because this plugin needs no credentials of its own and safe defaults cover local experimentation.

## Codex subscription limits

Version 1.2 polls Hermes' reusable OpenAI Codex account-usage probe every five minutes by default. It emits `hermes.codex.limit.remaining` as a 0–1 gauge for every returned main, code-review, or model-specific limit, labeled by plan, limit name, window description, duration, and stable metered feature when available. It also emits `hermes.codex.limit.reset` as a Unix timestamp gauge.

Reset timestamps are deliberately metric values rather than labels. Some Codex limits report a moving reset timestamp, and using it as a Prometheus label would create a new series on every poll. Authentication remains owned by Hermes' existing Codex OAuth credential resolver; the plugin stores and exports no tokens.

## Hypothetical API-equivalent cost

Version 1.1 adds `hermes.gen_ai.hypothetical_api_cost` (USD), separate from the existing `hermes.gen_ai.estimated_cost`. It is a **counterfactual estimate, not an invoice**, and does not alter subscription-included semantics or actual spend reporting.

The deliberately narrow `pricing.py` snapshot was observed **2026-08-06** for a Standard API scenario. It maps only exact `gpt-5.6-sol` and the official alias `gpt-5.6`; unknown models emit no amount and leave an `unmapped` status on the request span. Rates per million tokens are:

| Context band | Uncached input | Cached input | Cache write | Output |
|---|---:|---:|---:|---:|
| Short (total request input <= 272,000) | $5.00 | $0.50 | $6.25 | $30.00 |
| Long (total request input > 272,000) | $10.00 | $1.00 | $12.50 | $45.00 |

Total request input is uncached input + cache reads + cache writes. Total output is charged once; reasoning tokens are a subset of output and are never added again. Hosted-tool fees are excluded. Low-cardinality provenance labels accompany the metric; source URL, observed date, formula/mapping versions, confidence, and the not-an-invoice warning remain on spans.

Hermes' canonical `post_api_request` usage summary currently provides `cache_write_tokens` via normalized `CanonicalUsage`; the plugin uses that field and conservatively supports `cache_creation_input_tokens` as a compatibility fallback. If a provider/hook does not report cache writes, the plugin cannot infer them and treats them as zero. If the complete usage summary is absent, it emits no hypothetical amount and marks the span `missing_usage`.

## Tests

```bash
python3 -m pytest -q
```

## License and support

MIT licensed; see `LICENSE`. The license grants permissions but does not imply support, warranty, fitness, compatibility, or ongoing maintenance.
