from decimal import Decimal
import importlib.util
from pathlib import Path
import sys


PRICING_PATH = Path(__file__).with_name("pricing.py")
spec = importlib.util.spec_from_file_location("hermes_otel_pricing_test", PRICING_PATH)
assert spec and spec.loader
pricing = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = pricing
spec.loader.exec_module(pricing)


def usage(**overrides):
    values = dict(
        input_tokens=0,
        cache_read_tokens=0,
        cache_write_tokens=0,
        output_tokens=0,
        reasoning_tokens=0,
    )
    values.update(overrides)
    return pricing.Usage(**values)


def test_short_context_known_example_with_cache_buckets():
    result = pricing.estimate_hypothetical_api_cost(
        "gpt-5.6-sol",
        usage(
            input_tokens=100_000,
            cache_read_tokens=50_000,
            cache_write_tokens=10_000,
            output_tokens=20_000,
        ),
    )
    assert result.amount_usd == Decimal("1.1875")
    assert result.context_band == "short"
    assert result.mapping_method == "exact"
    assert result.priced_model == "gpt-5.6-sol"


def test_long_context_threshold_is_strictly_greater_than_272000_input_tokens():
    at_boundary = pricing.estimate_hypothetical_api_cost(
        "gpt-5.6-sol", usage(input_tokens=272_000)
    )
    above_boundary = pricing.estimate_hypothetical_api_cost(
        "gpt-5.6-sol", usage(input_tokens=272_001)
    )
    assert at_boundary.context_band == "short"
    assert at_boundary.amount_usd == Decimal("1.36")
    assert above_boundary.context_band == "long"
    assert above_boundary.amount_usd == Decimal("2.72001")


def test_reasoning_tokens_are_not_charged_again_beyond_total_output():
    without_reasoning = pricing.estimate_hypothetical_api_cost(
        "gpt-5.6-sol", usage(output_tokens=100_000)
    )
    with_reasoning = pricing.estimate_hypothetical_api_cost(
        "gpt-5.6-sol", usage(output_tokens=100_000, reasoning_tokens=80_000)
    )
    assert without_reasoning.amount_usd == with_reasoning.amount_usd == Decimal("3")


def test_official_alias_maps_with_high_confidence_and_unknown_is_unmapped():
    alias = pricing.estimate_hypothetical_api_cost("gpt-5.6", usage(input_tokens=1))
    unknown = pricing.estimate_hypothetical_api_cost("gpt-5.6-preview", usage(input_tokens=1))
    assert alias.mapping_method == "official_alias"
    assert alias.confidence == "high"
    assert alias.priced_model == "gpt-5.6-sol"
    assert unknown.estimation_status == "unmapped"
    assert unknown.amount_usd is None
