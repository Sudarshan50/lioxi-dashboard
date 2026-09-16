import unittest
from datetime import datetime, timedelta, timezone

from app.services.dashboard_service import throughput_rates


def _hour(offset_hours: int, tokens: int, requests: int = 0) -> dict:
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    return {
        "bucket": (start + timedelta(hours=offset_hours)).isoformat(),
        "total_tokens": tokens,
        "requests": requests,
    }


class ThroughputRates(unittest.TestCase):
    def test_avg_uses_time_from_first_usage_not_full_range(self):
        start = datetime(2026, 9, 1, tzinfo=timezone.utc)
        end = start + timedelta(days=14)
        hourly = [_hour(13 * 24, 1440)]
        rates = throughput_rates(1440, 0, start, end, hourly)
        self.assertEqual(rates["avg_tpm"], 1.0)

    def test_leading_idle_hours_are_ignored(self):
        start = datetime(2026, 9, 1, tzinfo=timezone.utc)
        end = start + timedelta(hours=10)
        hourly = [_hour(0, 0, 0), _hour(8, 120)]
        rates = throughput_rates(120, 0, start, end, hourly)
        self.assertEqual(rates["avg_tpm"], 1.0)

    def test_avg_rpm_uses_the_same_usage_window(self):
        start = datetime(2026, 9, 1, tzinfo=timezone.utc)
        end = start + timedelta(hours=10)
        hourly = [_hour(8, 0, 120)]
        rates = throughput_rates(0, 120, start, end, hourly)
        self.assertEqual(rates["avg_rpm"], 1.0)

    def test_idle_after_first_usage_still_counts(self):
        start = datetime(2026, 9, 1, tzinfo=timezone.utc)
        end = start + timedelta(hours=10)
        hourly = [_hour(0, 600)]
        rates = throughput_rates(600, 0, start, end, hourly)
        self.assertEqual(rates["avg_tpm"], 1.0)

    def test_earliest_usage_hour_wins_if_list_is_unsorted(self):
        start = datetime(2026, 9, 1, tzinfo=timezone.utc)
        end = start + timedelta(hours=10)
        hourly = [_hour(8, 120), _hour(2, 120)]
        rates = throughput_rates(240, 0, start, end, hourly)
        self.assertEqual(rates["avg_tpm"], 0.5)

    def test_empty_hourly_keeps_selected_range(self):
        start = datetime(2026, 9, 1, tzinfo=timezone.utc)
        end = start + timedelta(hours=2)
        rates = throughput_rates(120, 0, start, end, [])
        self.assertEqual(rates["avg_tpm"], 1.0)
