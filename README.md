# Rafael / Zero Two's personal experiment — unsupported

> **Warning:** This repository is Rafael (Zero Two)'s personal experiment, tailored to one environment. It is **not** a polished or public-facing Hermes plugin, carries **no support commitment**, and is not expected to install or remain compatible in other environments. Use it only as reference and expect breakage.

# Hermes OpenTelemetry experiment

A local Hermes Agent plugin that translates lifecycle hooks into OpenTelemetry traces and metrics and exports them over OTLP/HTTP.

## Architecture

- Hermes hook handlers delegate to one process-local `TelemetryRuntime`.
- The runtime owns isolated OpenTelemetry tracer and meter providers.
- Turn spans parent model-request and tool-call spans.
- Counters and histograms report request, error, token, tool, duration, and Hermes-native estimated-cost data.
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

There is deliberately no environment template because this plugin needs no credentials of its own and safe defaults cover local experimentation.

## Tests

```bash
python3 -m pytest -q
```

## License and support

MIT licensed; see `LICENSE`. The license grants permissions but does not imply support, warranty, fitness, compatibility, or ongoing maintenance.
