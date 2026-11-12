"""Host resource probe: CPU, RAM, disk, load and uptime as one JSON document.

Standard library only, so it can be piped to a remote machine that has nothing
installed: `ssh host python3 - < probe.py`. Uses psutil when it is importable,
otherwise /proc (Linux) or sysctl/vm_stat (macOS). Unknown values are null.
"""

import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time

SCHEMA = "host-v1"


def parse_meminfo(text):
    """Return (total, available) bytes from /proc/meminfo text."""
    values = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].isdigit():
            values[parts[0].rstrip(":")] = int(parts[1]) * 1024
    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    if available is None and total is not None:
        available = values.get("MemFree", 0) + values.get("Buffers", 0) + values.get("Cached", 0)
    return total, available


def parse_proc_stat(text):
    """Return (idle, total) jiffies from the aggregate cpu line of /proc/stat."""
    for line in text.splitlines():
        if line.startswith("cpu "):
            fields = [int(x) for x in line.split()[1:]]
            idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
            return idle, sum(fields)
    return None


def cpu_percent_from_samples(first, second):
    if not first or not second:
        return None
    idle = second[0] - first[0]
    total = second[1] - first[1]
    if total <= 0:
        return None
    return round(100.0 * (total - idle) / total, 1)


def _read(path):
    try:
        with open(path) as handle:
            return handle.read()
    except OSError:
        return None


def _disk_roots():
    if os.name == "nt":
        roots = []
        for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            root = f"{letter}:\\"
            if os.path.exists(root):
                roots.append(root)
        return roots
    roots = ["/"]
    home = os.path.expanduser("~")
    if home not in roots:
        roots.append(home)
    return roots


def _disks():
    seen = set()
    disks = []
    for root in _disk_roots():
        try:
            usage = shutil.disk_usage(root)
            device = os.stat(root).st_dev
        except OSError:
            continue
        if device in seen and os.name != "nt":
            continue
        seen.add(device)
        disks.append({"mount": root, "total": usage.total, "used": usage.used, "free": usage.free})
    return disks


def _sysctl(name):
    try:
        return subprocess.run(["sysctl", "-n", name], capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _collect_without_psutil():
    cpu_percent = mem_total = mem_available = uptime = None
    if sys.platform.startswith("linux"):
        first = parse_proc_stat(_read("/proc/stat") or "")
        time.sleep(0.5)
        second = parse_proc_stat(_read("/proc/stat") or "")
        cpu_percent = cpu_percent_from_samples(first, second)
        mem_total, mem_available = parse_meminfo(_read("/proc/meminfo") or "")
        raw = _read("/proc/uptime")
        uptime = float(raw.split()[0]) if raw else None
    elif sys.platform == "darwin":
        total = _sysctl("hw.memsize")
        mem_total = int(total) if total.isdigit() else None
        try:
            out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5).stdout
            page = int(_sysctl("hw.pagesize") or 4096)
            pages = {}
            for line in out.splitlines()[1:]:
                key, _, value = line.partition(":")
                value = value.strip().rstrip(".")
                if value.isdigit():
                    pages[key.strip()] = int(value)
            mem_available = page * (pages.get("Pages free", 0) + pages.get("Pages inactive", 0))
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
    return cpu_percent, mem_total, mem_available, uptime


def collect():
    try:
        load = list(os.getloadavg())
    except (AttributeError, OSError):
        load = None
    try:
        import psutil  # type: ignore

        cpu_percent = psutil.cpu_percent(interval=0.5)
        memory = psutil.virtual_memory()
        mem_total, mem_available = memory.total, memory.available
        uptime = time.time() - psutil.boot_time()
    except ImportError:
        cpu_percent, mem_total, mem_available, uptime = _collect_without_psutil()
    return {
        "schema": SCHEMA,
        "hostname": socket.gethostname(),
        "os": platform.system(),
        "os_release": platform.release(),
        "arch": platform.machine(),
        "cpu_count": os.cpu_count(),
        "cpu_percent": cpu_percent,
        "load_avg": load,
        "memory": {"total": mem_total, "available": mem_available},
        "disks": _disks(),
        "uptime_seconds": round(uptime) if uptime is not None else None,
        "collected_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


if __name__ == "__main__":
    print(json.dumps(collect()))
