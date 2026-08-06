"""Pure, versioned pricing for counterfactual OpenAI Standard API estimates.

This snapshot is intentionally narrow: only exact model IDs and explicitly listed
official aliases are mapped. It does not represent an invoice or actual spend.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

PRICING_VERSION = "openai-standard-2026-08-06-v1"
FORMULA_VERSION = "hypothetical-api-cost-v1"
MAPPING_VERSION = "gpt-5.6-model-map-v1"
OBSERVED_DATE = "2026-08-06"
SOURCE_URL = "https://openai.com/api/pricing/"
SCENARIO = "standard"
CURRENCY = "USD"
LONG_CONTEXT_THRESHOLD = 272_000
PRICED_MODEL = "gpt-5.6-sol"

_MODEL_MAP = {
    "gpt-5.6-sol": (PRICED_MODEL, "exact"),
    "gpt-5.6": (PRICED_MODEL, "official_alias"),
}

_RATES = {
    "short": {
        "input": Decimal("5.00"),
        "cache_read": Decimal("0.50"),
        "cache_write": Decimal("6.25"),
        "output": Decimal("30.00"),
    },
    "long": {
        "input": Decimal("10.00"),
        "cache_read": Decimal("1.00"),
        "cache_write": Decimal("12.50"),
        "output": Decimal("45.00"),
    },
}
_ONE_MILLION = Decimal("1000000")


@dataclass(frozen=True)
class Usage:
    """Canonical usage buckets; reasoning tokens are a subset of output."""

    input_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0

    @property
    def total_request_input(self) -> int:
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens


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


def estimate_hypothetical_api_cost(requested_model: str, usage: Usage) -> Estimate:
    """Estimate Standard API cost from mutually exclusive canonical buckets.

    The long-context schedule applies only when total request input (uncached
    input + cache reads + cache writes) is strictly greater than 272,000.
    Reasoning is deliberately not included separately because it is already a
    subset of total output.
    """
    requested_model = str(requested_model or "")
    mapping = _MODEL_MAP.get(requested_model)
    if mapping is None:
        return Estimate(
            amount_usd=None,
            estimation_status="unmapped",
            requested_model=requested_model,
            priced_model="unmapped",
            mapping_method="none",
            confidence="none",
            context_band="unknown",
        )

    priced_model, mapping_method = mapping
    band = "long" if usage.total_request_input > LONG_CONTEXT_THRESHOLD else "short"
    rates = _RATES[band]
    amount = (
        Decimal(max(0, usage.input_tokens)) * rates["input"]
        + Decimal(max(0, usage.cache_read_tokens)) * rates["cache_read"]
        + Decimal(max(0, usage.cache_write_tokens)) * rates["cache_write"]
        + Decimal(max(0, usage.output_tokens)) * rates["output"]
    ) / _ONE_MILLION
    return Estimate(
        amount_usd=amount,
        estimation_status="estimated",
        requested_model=requested_model,
        priced_model=priced_model,
        mapping_method=mapping_method,
        confidence="high",
        context_band=band,
    )
