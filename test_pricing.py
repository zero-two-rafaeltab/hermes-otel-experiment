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
    assert result.amount_usd == Decimal("0.87")
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
    assert at_boundary.amount_usd == Decimal("1.088")
    assert above_boundary.context_band == "long"
    assert above_boundary.amount_usd == Decimal("2.176008")


def test_reasoning_tokens_are_not_charged_again_beyond_total_output():
    without_reasoning = pricing.estimate_hypothetical_api_cost(
        "gpt-5.6-sol", usage(output_tokens=100_000)
    )
    with_reasoning = pricing.estimate_hypothetical_api_cost(
        "gpt-5.6-sol", usage(output_tokens=100_000, reasoning_tokens=80_000)
    )
    assert without_reasoning.amount_usd == with_reasoning.amount_usd == Decimal("2")


def test_official_alias_maps_with_high_confidence_and_unknown_is_unmapped():
    alias = pricing.estimate_hypothetical_api_cost("gpt-5.6", usage(input_tokens=1))
    unknown = pricing.estimate_hypothetical_api_cost("gpt-5.6-preview", usage(input_tokens=1))
    assert alias.mapping_method == "official_alias"
    assert alias.confidence == "high"
    assert alias.priced_model == "gpt-5.6-sol"
    assert unknown.estimation_status == "unmapped"
    assert unknown.amount_usd is None


def test_current_openai_standard_models_use_the_2026_09_13_rate_card():
    expected_rates = {
        "gpt-6-astra": ("10", "1", "12.5", "50"),
        "gpt-5.6-sol": ("4", ".4", "5", "20"),
        "gpt-5.6-terra": ("2", ".2", "2.5", "12"),
        "gpt-5.6-luna": (".2", ".02", ".25", "1.2"),
        "gpt-5.5": ("5", ".5", None, "30"),
        "gpt-5.5-pro": ("30", None, None, "180"),
        "gpt-5.4": ("2.5", ".25", None, "15"),
        "gpt-5.4-mini": (".75", ".075", None, "4.5"),
        "gpt-5.4-nano": (".2", ".02", None, "1.25"),
        "gpt-5.4-pro": ("30", None, None, "180"),
        "gpt-5.2": ("1.75", ".175", None, "14"),
        "gpt-5.2-pro": ("21", None, None, "168"),
        "gpt-5.1": ("1.25", ".125", None, "10"),
        "gpt-5": ("1.25", ".125", None, "10"),
        "gpt-5-mini": (".25", ".025", None, "2"),
        "gpt-5-nano": (".05", ".005", None, ".4"),
        "gpt-5-pro": ("15", None, None, "120"),
        "gpt-4.1": ("2", ".5", None, "8"),
        "gpt-4.1-mini": (".4", ".1", None, "1.6"),
        "gpt-4.1-nano": (".1", ".025", None, ".4"),
        "gpt-4o": ("2.5", "1.25", None, "10"),
        "gpt-4o-mini": (".15", ".075", None, ".6"),
        "o1": ("15", "7.5", None, "60"),
        "o1-pro": ("150", None, None, "600"),
        "o3": ("2", ".5", None, "8"),
        "o3-pro": ("20", None, None, "80"),
        "o3-mini": ("1.1", ".55", None, "4.4"),
        "o4-mini": ("1.1", ".275", None, "4.4"),
        "gpt-4-turbo-2024-04-09": ("10", None, None, "30"),
        "gpt-4-0613": ("30", None, None, "60"),
        "gpt-3.5-turbo": (".5", None, None, "1.5"),
        "gpt-3.5-turbo-1106": ("1", None, None, "2"),
        "gpt-3.5-turbo-instruct": ("1.5", None, None, "2"),
        "davinci-002": ("2", None, None, "2"),
        "babbage-002": (".4", None, None, ".4"),
        "gpt-5.6-cyber": ("12.5", "1.25", "15.625", "75"),
        "gpt-5.5-cyber": ("12.5", "1.25", None, "75"),
        "chat-latest": ("5", ".5", None, "30"),
        "gpt-5.3-codex": ("1.75", ".175", None, "14"),
        "gpt-5-search-api": ("1.25", ".125", None, "10"),
    }

    for model, rates in expected_rates.items():
        for field, expected in zip(
            ("input_tokens", "cache_read_tokens", "cache_write_tokens", "output_tokens"),
            rates,
        ):
            if expected is None:
                continue
            result = pricing.estimate_hypothetical_api_cost(model, usage(**{field: 100_000}))
            assert result.estimation_status == "estimated", (model, field)
            assert result.amount_usd == Decimal(expected) / 10, (model, field)
            assert result.priced_model == model


def test_current_model_pages_not_listed_in_the_grouped_table_are_supported():
    expected_input_output = {
        "chatgpt-4o-latest": ("5", "15"),
        "gpt-4.5-preview": ("75", "150"),
        "codex-mini-latest": ("1.5", "6"),
        "gpt-5-chat-latest": ("1.25", "10"),
        "gpt-5-codex": ("1.25", "10"),
        "gpt-5.1-chat-latest": ("1.25", "10"),
        "gpt-5.1-codex": ("1.25", "10"),
        "gpt-5.1-codex-mini": (".25", "2"),
        "gpt-5.1-codex-max": ("1.25", "10"),
        "gpt-5.2-chat-latest": ("1.75", "14"),
        "gpt-5.2-codex": ("1.75", "14"),
        "gpt-5.3-chat-latest": ("1.75", "14"),
        "o1-mini": ("1.1", "4.4"),
        "o1-preview": ("15", "60"),
        "gpt-4-turbo-preview": ("10", "30"),
        "o3-deep-research": ("10", "40"),
        "o4-mini-deep-research": ("2", "8"),
    }
    for model, (input_rate, output_rate) in expected_input_output.items():
        result = pricing.estimate_hypothetical_api_cost(
            model, usage(input_tokens=100_000, output_tokens=100_000)
        )
        assert result.estimation_status == "estimated", model
        assert result.amount_usd == (Decimal(input_rate) + Decimal(output_rate)) / 10


def test_long_context_rates_are_model_specific_and_apply_to_the_whole_request():
    cases = {
        "gpt-6-astra": Decimal("20.00002"),
        "gpt-5.6-sol": Decimal("8.000008"),
        "gpt-5.6-terra": Decimal("4.000004"),
        "gpt-5.6-luna": Decimal("0.4000004"),
        "gpt-5.5": Decimal("10.00001"),
        "gpt-5.5-pro": Decimal("60.00006"),
        "gpt-5.4": Decimal("5.000005"),
        "gpt-5.4-pro": Decimal("60.00006"),
    }
    for model, expected in cases.items():
        result = pricing.estimate_hypothetical_api_cost(model, usage(input_tokens=1_000_001))
        assert result.context_band == "long"
        assert result.amount_usd == expected

    untiered = pricing.estimate_hypothetical_api_cost("gpt-4.1", usage(input_tokens=1_000_001))
    assert untiered.context_band == "standard"
    assert untiered.amount_usd == Decimal("2.000002")


def test_documented_aliases_and_snapshots_are_explicitly_mapped():
    mappings = {
        "gpt-5.6": "gpt-5.6-sol",
        "gpt-daybreak-blue-latest": "gpt-5.6-sol",
        "gpt-daybreak-red-latest": "gpt-5.6-cyber",
        "gpt-5.5-2026-04-23": "gpt-5.5",
        "gpt-5.4-2026-03-05": "gpt-5.4",
        "gpt-5.4-mini-2026-03-17": "gpt-5.4-mini",
        "gpt-5.2-2025-12-11": "gpt-5.2",
        "gpt-4.1-2025-04-14": "gpt-4.1",
        "gpt-4o-2024-11-20": "gpt-4o",
        "gpt-4o-mini-2024-07-18": "gpt-4o-mini",
        "o3-2025-04-16": "o3",
        "o4-mini-2025-04-16": "o4-mini",
        "o1-mini-2024-09-12": "o1-mini",
        "o1-preview-2024-09-12": "o1-preview",
        "gpt-4-0314": "gpt-4-0613",
        "gpt-4-0125-preview": "gpt-4-turbo-preview",
        "o3-deep-research-2025-06-26": "o3-deep-research",
        "o4-mini-deep-research-2025-06-26": "o4-mini-deep-research",
        "gpt-4.5-preview-2025-02-27": "gpt-4.5-preview",
        "omni-moderation-2024-09-26": "omni-moderation-latest",
        "text-moderation-007": "text-moderation-latest",
    }
    for model, expected in mappings.items():
        result = pricing.estimate_hypothetical_api_cost(model, usage(input_tokens=1))
        assert result.priced_model == expected
        assert result.mapping_method in {"official_alias", "dated_snapshot"}


def test_missing_published_bucket_rate_is_not_silently_treated_as_free():
    result = pricing.estimate_hypothetical_api_cost(
        "gpt-5.5-pro", usage(cache_read_tokens=1000)
    )
    assert result.amount_usd is None
    assert result.estimation_status == "rate_unavailable"


def test_moderation_models_and_documented_aliases_are_free():
    for model in (
        "omni-moderation-latest",
        "omni-moderation-2024-09-26",
        "text-moderation-latest",
        "text-moderation-stable",
        "text-moderation-007",
    ):
        result = pricing.estimate_hypothetical_api_cost(
            model,
            usage(
                input_tokens=100,
                cache_read_tokens=20,
                cache_write_tokens=10,
                output_tokens=5,
            ),
        )
        assert result.estimation_status == "estimated", model
        assert result.amount_usd == Decimal("0")


def test_models_needing_other_billing_dimensions_remain_unmapped():
    for model in (
        "gpt-realtime-2.1",
        "gpt-image-2.5-sunburst",
        "gpt-live-1",
        "gpt-transcribe",
        "tts-1",
        "sora-2-pro",
    ):
        result = pricing.estimate_hypothetical_api_cost(model, usage(input_tokens=1))
        assert result.estimation_status == "unmapped"
        assert result.amount_usd is None


def test_catalog_metadata_is_current_and_validated():
    assert pricing.PRICING_VERSION == "openai-standard-2026-09-13-v1"
    assert pricing.OBSERVED_DATE == "2026-09-13"
    pricing.validate_catalog()
