import unittest
from datetime import datetime, timezone

from product.model_registry import estimate_cost, pricing_period


class ModelPricingTest(unittest.TestCase):
    def test_deepseek_peak_and_off_peak_rates(self):
        usage = {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000, "total_tokens": 2_000_000}
        peak = estimate_cost("deepseek-v4-pro", usage, datetime(2026, 9, 18, 2, tzinfo=timezone.utc))
        off_peak = estimate_cost("deepseek-v4-pro", usage, datetime(2026, 9, 19, 2, tzinfo=timezone.utc))
        self.assertEqual(peak["pricing_period"], "peak")
        self.assertAlmostEqual(peak["estimated_usd"], 5.28)
        self.assertEqual(off_peak["pricing_period"], "off_peak")
        self.assertAlmostEqual(off_peak["estimated_usd"], 2.64)
        self.assertIn("缓存未命中", peak["assumption"])

    def test_legacy_flash_alias_and_cache_tokens(self):
        usage = {
            "prompt_tokens": 1_000_000,
            "completion_tokens": 1_000_000,
            "total_tokens": 2_000_000,
            "prompt_cache_hit_tokens": 1_000_000,
            "prompt_cache_miss_tokens": 0,
        }
        result = estimate_cost("deepseek-v4-flash", usage, datetime(2026, 9, 18, 2, tzinfo=timezone.utc))
        self.assertEqual(result["model"], "deepseek-flash")
        self.assertAlmostEqual(result["estimated_usd"], 1.206)
        self.assertIn("接口返回", result["assumption"])

    def test_unknown_and_mock_models_are_explicit(self):
        self.assertEqual(estimate_cost("mock-conversation", None)["status"], "not_billable")
        self.assertEqual(estimate_cost("other-model", {"total_tokens": 2})["status"], "unknown_model")

    def test_peak_boundaries_are_utc_weekday_only(self):
        self.assertEqual(pricing_period(datetime(2026, 9, 18, 1, tzinfo=timezone.utc)), "peak")
        self.assertEqual(pricing_period(datetime(2026, 9, 18, 4, tzinfo=timezone.utc)), "off_peak")
        self.assertEqual(pricing_period(datetime(2026, 9, 18, 6, tzinfo=timezone.utc)), "peak")
        self.assertEqual(pricing_period(datetime(2026, 9, 18, 10, tzinfo=timezone.utc)), "off_peak")


if __name__ == "__main__":
    unittest.main()
