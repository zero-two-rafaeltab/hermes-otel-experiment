"""Versioned OpenAI Standard API pricing for counterfactual estimates.

The catalog covers OpenAI models whose current public pricing can be computed
from Hermes's canonical token buckets. Models billed by modality, duration,
characters, generated media, hosted-tool calls, storage, or container time stay
unmapped because pretending those dimensions are free would understate cost.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

PRICING_VERSION = "openai-standard-2026-09-13-v1"
FORMULA_VERSION = "hypothetical-api-cost-v2"
MAPPING_VERSION = "openai-model-map-2026-09-13-v1"
OBSERVED_DATE = "2026-09-13"
SOURCE_URL = "https://developers.openai.com/api/docs/pricing"
SCENARIO = "standard"
CURRENCY = "USD"
LONG_CONTEXT_THRESHOLD = 272_000
_ONE_MILLION = Decimal("1000000")


@dataclass(frozen=True)
class Usage:
    """Mutually exclusive canonical usage buckets; reasoning is part of output."""

    input_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0

    @property
    def total_request_input(self) -> int:
        return sum(max(0, value) for value in (
            self.input_tokens,
            self.cache_read_tokens,
            self.cache_write_tokens,
        ))


@dataclass(frozen=True)
class TokenRates:
    input: Optional[Decimal]
    cache_read: Optional[Decimal]
    cache_write: Optional[Decimal]
    output: Optional[Decimal]


@dataclass(frozen=True)
class ModelPricing:
    model: str
    short: TokenRates
    long: Optional[TokenRates] = None
    aliases: tuple[str, ...] = ()
    snapshots: tuple[str, ...] = ()
    source_url: str = SOURCE_URL


@dataclass(frozen=True)
class ModelMapping:
    pricing: ModelPricing
    method: str


@dataclass(frozen=True)
class Estimate:
    amount_usd: Optional[Decimal]
    estimation_status: str
    requested_model: str
    priced_model: str
    mapping_method: str
    confidence: str
    context_band: str
    pricing_version: str = PRICING_VERSION
    formula_version: str = FORMULA_VERSION
    mapping_version: str = MAPPING_VERSION
    scenario: str = SCENARIO
    currency: str = CURRENCY
    source_url: str = SOURCE_URL
    observed_date: str = OBSERVED_DATE
    hosted_tool_fees_excluded: bool = True
    counterfactual: bool = True


def _rates(
    input: str | None,
    cache_read: str | None,
    cache_write: str | None,
    output: str | None,
) -> TokenRates:
    return TokenRates(*(
        Decimal(value) if value is not None else None
        for value in (input, cache_read, cache_write, output)
    ))


def _model(
    model: str,
    short: tuple[str | None, str | None, str | None, str | None],
    *,
    long: tuple[str | None, str | None, str | None, str | None] | None = None,
    aliases: tuple[str, ...] = (),
    snapshots: tuple[str, ...] = (),
    source_url: str = SOURCE_URL,
) -> ModelPricing:
    return ModelPricing(
        model=model,
        short=_rates(*short),
        long=_rates(*long) if long else None,
        aliases=aliases,
        snapshots=snapshots,
        source_url=source_url,
    )


# Prices are USD per one million tokens. Dashes in OpenAI's table remain None;
# if telemetry unexpectedly contains such a bucket, the estimate is withheld.
_MODELS = (
    _model("gpt-6-astra", ("10", "1", "12.5", "50"), long=("20", "2", "25", "75")),
    _model(
        "gpt-5.6-sol", ("4", ".4", "5", "20"),
        long=("8", ".8", "10", "30"),
        aliases=("gpt-5.6", "gpt-daybreak-blue-latest"),
    ),
    _model("gpt-5.6-terra", ("2", ".2", "2.5", "12"), long=("4", ".4", "5", "18")),
    _model("gpt-5.6-luna", (".2", ".02", ".25", "1.2"), long=(".4", ".04", ".5", "1.8")),
    _model("gpt-5.5", ("5", ".5", None, "30"), long=("10", "1", None, "45"), snapshots=("gpt-5.5-2026-04-23",)),
    _model("gpt-5.5-pro", ("30", None, None, "180"), long=("60", None, None, "270"), snapshots=("gpt-5.5-pro-2026-04-23",)),
    _model("gpt-5.4", ("2.5", ".25", None, "15"), long=("5", ".5", None, "22.5"), snapshots=("gpt-5.4-2026-03-05",)),
    _model("gpt-5.4-mini", (".75", ".075", None, "4.5"), snapshots=("gpt-5.4-mini-2026-03-17",)),
    _model("gpt-5.4-nano", (".2", ".02", None, "1.25"), snapshots=("gpt-5.4-nano-2026-03-17",)),
    _model("gpt-5.4-pro", ("30", None, None, "180"), long=("60", None, None, "270"), snapshots=("gpt-5.4-pro-2026-03-05",)),
    _model("gpt-5.2", ("1.75", ".175", None, "14"), snapshots=("gpt-5.2-2025-12-11",)),
    _model("gpt-5.2-pro", ("21", None, None, "168"), snapshots=("gpt-5.2-pro-2025-12-11",)),
    _model("gpt-5.1", ("1.25", ".125", None, "10"), snapshots=("gpt-5.1-2025-11-13",)),
    _model("gpt-5", ("1.25", ".125", None, "10"), snapshots=("gpt-5-2025-08-07",)),
    _model("gpt-5-mini", (".25", ".025", None, "2"), snapshots=("gpt-5-mini-2025-08-07",)),
    _model("gpt-5-nano", (".05", ".005", None, ".4"), snapshots=("gpt-5-nano-2025-08-07",)),
    _model("gpt-5-pro", ("15", None, None, "120"), snapshots=("gpt-5-pro-2025-10-06",)),
    _model("gpt-4.1", ("2", ".5", None, "8"), snapshots=("gpt-4.1-2025-04-14",)),
    _model("gpt-4.1-mini", (".4", ".1", None, "1.6"), snapshots=("gpt-4.1-mini-2025-04-14",)),
    _model("gpt-4.1-nano", (".1", ".025", None, ".4"), snapshots=("gpt-4.1-nano-2025-04-14",)),
    _model(
        "gpt-4.5-preview", ("75", "37.5", None, "150"),
        snapshots=("gpt-4.5-preview-2025-02-27",),
        source_url="https://developers.openai.com/api/docs/models/gpt-4.5-preview",
    ),
    _model("gpt-4o", ("2.5", "1.25", None, "10"), snapshots=("gpt-4o-2024-08-06", "gpt-4o-2024-11-20")),
    _model("gpt-4o-2024-05-13", ("5", None, None, "15")),
    _model("gpt-4o-mini", (".15", ".075", None, ".6"), snapshots=("gpt-4o-mini-2024-07-18",)),
    _model("o1", ("15", "7.5", None, "60"), snapshots=("o1-2024-12-17",)),
    _model("o1-pro", ("150", None, None, "600"), snapshots=("o1-pro-2025-03-19",)),
    _model("o3-pro", ("20", None, None, "80"), snapshots=("o3-pro-2025-06-10",)),
    _model("o3", ("2", ".5", None, "8"), snapshots=("o3-2025-04-16",)),
    _model("o4-mini", ("1.1", ".275", None, "4.4"), snapshots=("o4-mini-2025-04-16",)),
    _model("o3-mini", ("1.1", ".55", None, "4.4"), snapshots=("o3-mini-2025-01-31",)),
    _model("gpt-4-turbo-2024-04-09", ("10", None, None, "30"), aliases=("gpt-4-turbo",)),
    _model(
        "gpt-4-turbo-preview", ("10", None, None, "30"),
        snapshots=("gpt-4-0125-preview", "gpt-4-1106-vision-preview"),
        source_url="https://developers.openai.com/api/docs/models/gpt-4-turbo-preview",
    ),
    _model("gpt-4-0613", ("30", None, None, "60"), aliases=("gpt-4",), snapshots=("gpt-4-0314",)),
    _model("gpt-3.5-turbo", (".5", None, None, "1.5"), snapshots=("gpt-3.5-turbo-0125",)),
    _model("gpt-3.5-turbo-1106", ("1", None, None, "2")),
    _model("gpt-3.5-turbo-instruct", ("1.5", None, None, "2")),
    _model("davinci-002", ("2", None, None, "2")),
    _model("babbage-002", (".4", None, None, ".4")),
    _model("gpt-5.6-cyber", ("12.5", "1.25", "15.625", "75"), aliases=("gpt-daybreak-red-latest",)),
    _model("gpt-5.5-cyber", ("12.5", "1.25", None, "75")),
    _model("chat-latest", ("5", ".5", None, "30")),
    _model(
        "chatgpt-4o-latest", ("5", None, None, "15"),
        source_url="https://developers.openai.com/api/docs/models/chatgpt-4o-latest",
    ),
    _model("gpt-5.3-codex", ("1.75", ".175", None, "14")),
    _model(
        "codex-mini-latest", ("1.5", ".375", None, "6"),
        source_url="https://developers.openai.com/api/docs/models/codex-mini-latest",
    ),
    _model(
        "gpt-5-chat-latest", ("1.25", ".125", None, "10"),
        source_url="https://developers.openai.com/api/docs/models/gpt-5-chat-latest",
    ),
    _model(
        "gpt-5-codex", ("1.25", ".125", None, "10"),
        source_url="https://developers.openai.com/api/docs/models/gpt-5-codex",
    ),
    _model(
        "gpt-5.1-chat-latest", ("1.25", ".125", None, "10"),
        source_url="https://developers.openai.com/api/docs/models/gpt-5.1-chat-latest",
    ),
    _model(
        "gpt-5.1-codex", ("1.25", ".125", None, "10"),
        source_url="https://developers.openai.com/api/docs/models/gpt-5.1-codex",
    ),
    _model(
        "gpt-5.1-codex-mini", (".25", ".025", None, "2"),
        source_url="https://developers.openai.com/api/docs/models/gpt-5.1-codex-mini",
    ),
    _model(
        "gpt-5.1-codex-max", ("1.25", ".125", None, "10"),
        source_url="https://developers.openai.com/api/docs/models/gpt-5.1-codex-max",
    ),
    _model(
        "gpt-5.2-chat-latest", ("1.75", ".175", None, "14"),
        source_url="https://developers.openai.com/api/docs/models/gpt-5.2-chat-latest",
    ),
    _model(
        "gpt-5.2-codex", ("1.75", ".175", None, "14"),
        source_url="https://developers.openai.com/api/docs/models/gpt-5.2-codex",
    ),
    _model(
        "gpt-5.3-chat-latest", ("1.75", ".175", None, "14"),
        source_url="https://developers.openai.com/api/docs/models/gpt-5.3-chat-latest",
    ),
    _model(
        "o1-mini", ("1.1", ".55", None, "4.4"),
        snapshots=("o1-mini-2024-09-12",),
        source_url="https://developers.openai.com/api/docs/models/o1-mini",
    ),
    _model(
        "o1-preview", ("15", "7.5", None, "60"),
        snapshots=("o1-preview-2024-09-12",),
        source_url="https://developers.openai.com/api/docs/models/o1-preview",
    ),
    _model("gpt-5-search-api", ("1.25", ".125", None, "10")),
    _model(
        "o3-deep-research", ("10", "2.5", None, "40"),
        snapshots=("o3-deep-research-2025-06-26",),
        source_url="https://developers.openai.com/api/docs/models/o3-deep-research",
    ),
    _model(
        "o4-mini-deep-research", ("2", ".5", None, "8"),
        snapshots=("o4-mini-deep-research-2025-06-26",),
        source_url="https://developers.openai.com/api/docs/models/o4-mini-deep-research",
    ),
    _model("text-embedding-3-small", (".02", None, None, None)),
    _model("text-embedding-3-large", (".13", None, None, None)),
    _model("text-embedding-ada-002", (".10", None, None, None)),
    _model(
        "omni-moderation-latest", ("0", "0", "0", "0"),
        snapshots=("omni-moderation-2024-09-26",),
    ),
    _model(
        "text-moderation-latest", ("0", "0", "0", "0"),
        aliases=("text-moderation-stable",),
        snapshots=("text-moderation-007",),
        source_url="https://developers.openai.com/api/docs/models/text-moderation-latest",
    ),
)


def _build_index() -> dict[str, ModelMapping]:
    index: dict[str, ModelMapping] = {}
    for pricing in _MODELS:
        entries = ((pricing.model, "exact"),) + tuple(
            (alias, "official_alias") for alias in pricing.aliases
        ) + tuple((snapshot, "dated_snapshot") for snapshot in pricing.snapshots)
        for model_id, method in entries:
            if not model_id or model_id in index:
                raise ValueError(f"Duplicate or empty OpenAI model ID: {model_id!r}")
            index[model_id] = ModelMapping(pricing, method)
    return index


_MODEL_INDEX = _build_index()


def validate_catalog() -> None:
    """Raise if the immutable catalog contains ambiguous or invalid records."""
    if len(_MODEL_INDEX) < len(_MODELS):
        raise ValueError("Model index lost catalog entries")
    for model in _MODELS:
        if not model.model:
            raise ValueError("Empty canonical model ID")
        for rates in (model.short, model.long):
            if rates is None:
                continue
            for rate in (rates.input, rates.cache_read, rates.cache_write, rates.output):
                if rate is not None and rate < 0:
                    raise ValueError(f"Negative rate for {model.model}")


validate_catalog()


def _unpriced(requested_model: str, status: str = "unmapped") -> Estimate:
    return Estimate(
        amount_usd=None,
        estimation_status=status,
        requested_model=requested_model,
        priced_model="unmapped",
        mapping_method="none",
        confidence="none",
        context_band="unknown",
    )


def estimate_hypothetical_api_cost(requested_model: str, usage: Usage) -> Estimate:
    """Estimate Standard API cost from mutually exclusive canonical buckets.

    Long-context schedules apply to the whole request only for models that
    publish them. Unsupported billing dimensions and unpublished bucket rates
    produce no amount instead of a misleading zero-dollar estimate.
    """
    requested_model = str(requested_model or "")
    mapping = _MODEL_INDEX.get(requested_model)
    if mapping is None:
        return _unpriced(requested_model)

    pricing = mapping.pricing
    use_long = pricing.long is not None and usage.total_request_input > LONG_CONTEXT_THRESHOLD
    rates = pricing.long if use_long else pricing.short
    assert rates is not None
    band = "long" if use_long else ("short" if pricing.long is not None else "standard")

    buckets = (
        (max(0, usage.input_tokens), rates.input),
        (max(0, usage.cache_read_tokens), rates.cache_read),
        (max(0, usage.cache_write_tokens), rates.cache_write),
        (max(0, usage.output_tokens), rates.output),
    )
    if any(count and rate is None for count, rate in buckets):
        return Estimate(
            amount_usd=None,
            estimation_status="rate_unavailable",
            requested_model=requested_model,
            priced_model=pricing.model,
            mapping_method=mapping.method,
            confidence="high",
            context_band=band,
            source_url=pricing.source_url,
        )

    amount = sum(Decimal(count) * (rate or Decimal(0)) for count, rate in buckets) / _ONE_MILLION
    return Estimate(
        amount_usd=amount,
        estimation_status="estimated",
        requested_model=requested_model,
        priced_model=pricing.model,
        mapping_method=mapping.method,
        confidence="high",
        context_band=band,
        source_url=pricing.source_url,
    )
