import os
import tempfile
import unittest
from pathlib import Path

from app.services.system_stats_service import (
    SystemStatsService,
    cpu_percent_from_samples,
    parse_cpu_stat_line,
    parse_loadavg,
    parse_meminfo,
)


class CpuPercent(unittest.TestCase):
    def test_none_until_second_sample(self):
        self.assertIsNone(cpu_percent_from_samples(None, (100, 200)))

    def test_all_idle(self):
        prev = parse_cpu_stat_line("cpu  10 0 10 80 0 0 0 0")
        curr = parse_cpu_stat_line("cpu  10 0 10 180 0 0 0 0")
        self.assertEqual(cpu_percent_from_samples(prev, curr), 0.0)

    def test_half_busy(self):
        prev = parse_cpu_stat_line("cpu  50 0 0 50 0 0 0 0")
        curr = parse_cpu_stat_line("cpu  100 0 0 100 0 0 0 0")
        self.assertEqual(cpu_percent_from_samples(prev, curr), 50.0)


class Parsers(unittest.TestCase):
    def test_meminfo(self):
        total, available = parse_meminfo("MemTotal: 2000 kB\nMemAvailable: 500 kB\n")
        self.assertEqual(total, 2000 * 1024)
        self.assertEqual(available, 500 * 1024)

    def test_loadavg(self):
        self.assertEqual(parse_loadavg("0.18 0.51 1.88 10/391 520\n"), (0.18, 0.51, 1.88))


class Collector(unittest.TestCase):
    def test_collects_from_proc_and_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = Path(tmp)
            (proc / "stat").write_text("cpu  10 0 10 80 0 0 0 0 0 0\n")
            (proc / "meminfo").write_text("MemTotal:        7948272 kB\nMemAvailable:    5234567 kB\n")
            (proc / "loadavg").write_text("0.18 0.51 1.88 10/391 520\n")
            service = SystemStatsService(proc_root=proc, disk_path=tmp)
            first = service.collect()
            self.assertIsNone(first.cpu_percent)
            self.assertGreater(first.memory.total_bytes, 0)
            self.assertGreater(first.storage.total_bytes, 0)
            self.assertEqual(first.load_avg_1, 0.18)

            (proc / "stat").write_text("cpu  60 0 10 130 0 0 0 0 0 0\n")
            second = service.collect()
            self.assertIsNotNone(second.cpu_percent)
            self.assertGreaterEqual(second.cpu_percent or 0, 0)
            self.assertLessEqual(second.cpu_percent or 0, 100)
            self.assertTrue(os.path.isdir(tmp))
