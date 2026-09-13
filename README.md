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

The versioned `pricing.py` catalog was refreshed from OpenAI's official pricing and model documentation on **2026-09-13**. It covers all current and still-priced legacy text/reasoning model IDs whose cost can be calculated honestly from Hermes' canonical input, cached-input, cache-write, and output token buckets. This includes GPT-6 Astra; GPT-5.6 Sol, Terra, Luna, and Cyber; GPT-5.5/5.4/5.2/5.1/5 families; GPT-4.1 and GPT-4o families; o-series models; current Codex, ChatGPT, search, and deep-research text models; embeddings; moderation; and explicitly documented aliases and dated snapshots.

The catalog uses the **Standard API** scenario. Models with documented long-context pricing switch the entire request to the long rate only above 272,000 total request-input tokens. A published dash is represented as an unavailable rate: if telemetry unexpectedly contains that token bucket, the plugin emits no amount and records `rate_unavailable` rather than treating it as free. Unknown model IDs remain `unmapped`.

Models whose complete price requires dimensions Hermes does not currently capture remain intentionally unsupported: realtime/audio/image/video models, duration-priced transcription and Live sessions, character-priced TTS, and hosted-tool/storage/container charges. Deep-research estimates cover model tokens only; the existing `hosted_tool_fees_excluded` provenance flag remains true.

GPT-5.6 Sol's current promotional Standard rates are $4.00/M uncached input, $0.40/M cached input, $5.00/M cache writes, and $20.00/M output for short-context requests. The complete per-model rate card and aliases live in `pricing.py`; the primary source is https://developers.openai.com/api/docs/pricing.

Total request input is uncached input + cache reads + cache writes. Total output is charged once; reasoning tokens are a subset of output and are never added again. Hosted-tool fees are excluded. Low-cardinality provenance labels accompany the metric; source URL, observed date, formula/mapping versions, confidence, and the not-an-invoice warning remain on spans.

Hermes' canonical `post_api_request` usage summary currently provides `cache_write_tokens` via normalized `CanonicalUsage`; the plugin uses that field and conservatively supports `cache_creation_input_tokens` as a compatibility fallback. If a provider/hook does not report cache writes, the plugin cannot infer them and treats them as zero. If the complete usage summary is absent, it emits no hypothetical amount and marks the span `missing_usage`.

## Tests

```bash
python3 -m pytest -q
```

## License and support

MIT licensed; see `LICENSE`. The license grants permissions but does not imply support, warranty, fitness, compatibility, or ongoing maintenance.
