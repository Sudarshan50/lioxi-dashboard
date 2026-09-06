from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from app.schemas.system_stats import ResourceMetric, SystemStats

# Linux /proc/stat: user nice system idle iowait irq softirq steal guest guest_nice
_CPU_IDLE = 3
_CPU_IOWAIT = 4
_CPU_STEAL = 7


def cpu_percent_from_samples(prev: tuple[int, int] | None, curr: tuple[int, int]) -> float | None:
    if prev is None:
        return None
    prev_idle, prev_total = prev
    idle, total = curr
    delta_total = total - prev_total
    delta_idle = idle - prev_idle
    if delta_total <= 0:
        return 0.0
    busy = 1.0 - (delta_idle / delta_total)
    return round(max(0.0, min(100.0, busy * 100.0)), 1)


def parse_cpu_stat_line(line: str) -> tuple[int, int]:
    parts = line.split()
    values = [int(part) for part in parts[1:9]]
    idle = values[_CPU_IDLE] + values[_CPU_IOWAIT]
    total = sum(values[: _CPU_STEAL + 1])
    return idle, total


def parse_meminfo(text: str) -> tuple[int, int]:
    fields: dict[str, int] = {}
    for raw in text.splitlines():
        if ":" not in raw:
            continue
        key, rest = raw.split(":", 1)
        token = rest.strip().split()[0]
        try:
            fields[key] = int(token) * 1024
        except ValueError:
            continue
    total = fields.get("MemTotal", 0)
    available = fields.get("MemAvailable", fields.get("MemFree", 0))
    return total, available


def parse_loadavg(text: str) -> tuple[float, float, float]:
    parts = text.split()
    return float(parts[0]), float(parts[1]), float(parts[2])


def metric_from_statvfs(stat: os.statvfs_result) -> ResourceMetric:
    frsize = stat.f_frsize
    total = frsize * stat.f_blocks
    available = frsize * stat.f_bavail
    used = max(0, total - available)
    percent = 0.0 if total <= 0 else round(min(100.0, used / total * 100.0), 1)
    return ResourceMetric(used_bytes=used, total_bytes=total, available_bytes=available, percent=percent)


class SystemStatsService:
    def __init__(self, proc_root: str | os.PathLike[str] = "/proc", disk_path: str = "/") -> None:
        self._proc = Path(proc_root)
        self._disk_path = disk_path
        self._last_cpu: tuple[int, int] | None = None

    def collect(self) -> SystemStats:
        cpu_sample = parse_cpu_stat_line((self._proc / "stat").read_text().splitlines()[0])
        cpu_percent = cpu_percent_from_samples(self._last_cpu, cpu_sample)
        self._last_cpu = cpu_sample

        total_bytes, available_bytes = parse_meminfo((self._proc / "meminfo").read_text())
        used_bytes = max(0, total_bytes - available_bytes)
        memory_percent = 0.0 if total_bytes <= 0 else round(min(100.0, used_bytes / total_bytes * 100.0), 1)

        load_1, load_5, load_15 = parse_loadavg((self._proc / "loadavg").read_text())

        return SystemStats(
            cpu_percent=cpu_percent,
            cpu_count=os.cpu_count() or 1,
            load_avg_1=round(load_1, 2),
            load_avg_5=round(load_5, 2),
            load_avg_15=round(load_15, 2),
            memory=ResourceMetric(
                used_bytes=used_bytes,
                total_bytes=total_bytes,
                available_bytes=available_bytes,
                percent=memory_percent,
            ),
            storage=metric_from_statvfs(os.statvfs(self._disk_path)),
            collected_at=datetime.now(timezone.utc),
        )


collector = SystemStatsService()
