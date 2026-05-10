"""System resource monitoring for trial mode.

Checks Docker container memory usage and system memory to decide
whether new instances can be created or should be queued.
"""
import subprocess
import sys


def _parse_mem(s: str) -> float:
    """Parse a docker mem string like '123.4MiB' or '1.5GiB' into MiB float."""
    s = s.strip()
    try:
        if s.upper().endswith("GIB"):
            return float(s[:-3]) * 1024
        elif s.upper().endswith("MIB"):
            return float(s[:-3])
        elif s.upper().endswith("KIB"):
            return float(s[:-3]) / 1024
        elif s.upper().endswith("GB"):
            return float(s[:-2]) * 1024
        elif s.upper().endswith("MB"):
            return float(s[:-2])
        else:
            return float(s) / (1024 * 1024)
    except (ValueError, TypeError):
        return 0.0


def get_st_container_memory_mb() -> float:
    """Return total memory (MiB) used by all st-* user containers."""
    try:
        result = subprocess.run(
            ["docker", "stats", "--no-stream",
             "--format", "{{.Name}}\t{{.MemUsage}}"],
            capture_output=True, text=True, timeout=10,
        )
        total = 0.0
        for line in result.stdout.strip().split("\n"):
            if not line or "\t" not in line:
                continue
            name, mem_usage = line.split("\t", 1)
            if not name.startswith("st-") or name in ("st-traefik", "st-manager"):
                continue
            # mem_usage is like "123.4MiB / 768MiB" or "1.5GiB / 2GiB"
            used_str = mem_usage.split("/")[0].strip()
            total += _parse_mem(used_str)
        return total
    except Exception:
        return 0.0


def get_system_memory_mb() -> tuple[int, int]:
    """Return (total_mb, available_mb) for the host system."""
    # Try /proc/meminfo first (Linux/WSL)
    try:
        with open("/proc/meminfo") as f:
            info = {}
            for line in f:
                if ":" in line:
                    k, v = line.split(":", 1)
                    info[k.strip()] = int(v.strip().split()[0])
            total = info.get("MemTotal", 0) // 1024
            available = info.get("MemAvailable", info.get("MemFree", 0)) // 1024
            if total > 0:
                return total, available
    except (OSError, IOError):
        pass

    # Try psutil
    try:
        import psutil
        mem = psutil.virtual_memory()
        return mem.total // (1024 * 1024), mem.available // (1024 * 1024)
    except ImportError:
        pass

    # Fallback: use Docker info
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.MemTotal}}"],
            capture_output=True, text=True, timeout=5,
        )
        total_bytes = float(result.stdout.strip())
        total_mb = int(total_bytes / (1024 * 1024))
        # Rough estimate: 15% free
        return total_mb, int(total_mb * 0.15)
    except Exception:
        pass

    return 0, 0


def get_memory_usage_pct() -> float:
    """Return system memory usage percentage (0-100)."""
    total, available = get_system_memory_mb()
    if total == 0:
        return 0.0
    used = total - available
    return (used / total) * 100.0


def get_st_container_count() -> int:
    """Return number of running st-* user containers."""
    try:
        result = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}", "--filter", "name=st-"],
            capture_output=True, text=True, timeout=5,
        )
        names = [n.strip() for n in result.stdout.strip().split("\n") if n.strip()]
        return len([n for n in names if n not in ("st-traefik", "st-manager")])
    except Exception:
        return 0


def can_create_instance(trial_max_memory_pct: int, trial_max_instances: int) -> tuple[bool, str]:
    """Check if a new instance can be created.

    Returns (ok, reason).
    """
    count = get_st_container_count()
    if count >= trial_max_instances:
        return False, f"已达到最大体验实例数 ({trial_max_instances})"

    mem_pct = get_memory_usage_pct()
    if mem_pct > trial_max_memory_pct:
        return False, f"系统内存使用率 {mem_pct:.0f}% 超过阈值 {trial_max_memory_pct}%"

    return True, "ok"
