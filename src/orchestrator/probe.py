"""Host resource probe: CPU, RAM, disk, GPU, load and uptime as one JSON document.

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


MIB = 1024 * 1024
PCI_VENDORS = {"0x10de": "NVIDIA", "0x1002": "AMD", "0x8086": "Intel"}
NVIDIA_FIELDS = "index,name,utilization.gpu,memory.total,memory.used,temperature.gpu,power.draw,driver_version"
WIN32_RAM_CAP = 0xFFF00000  # Win32_VideoController.AdapterRAM is a uint32: anything near 4 GiB is clipped
VIRTUAL_ADAPTERS = ("basic display", "basic render", "remote display", "parsec", "virtual")


def _out(args, timeout=10):
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None  # "[N/A]", "[Not Supported]", ""


def _gpu(name, vendor, source, index=None, util=None, mem_total=None, mem_used=None, temp=None, power=None,
         driver=None):
    return {"index": index, "name": name, "vendor": vendor, "util_percent": util, "memory_total": mem_total,
            "memory_used": mem_used, "temperature_c": temp, "power_w": power, "driver": driver, "source": source}


def parse_nvidia_smi(text):
    """`nvidia-smi --query-gpu=<NVIDIA_FIELDS> --format=csv,noheader,nounits` -> GPU rows (memory in bytes)."""
    gpus = []
    for line in (text or "").splitlines():
        cols = [c.strip() for c in line.split(",")]
        if len(cols) < 8:
            continue
        total, used = _num(cols[3]), _num(cols[4])
        index = _num(cols[0])
        gpus.append(_gpu(cols[1], "NVIDIA", "nvidia-smi", index=int(index) if index is not None else None,
                         util=_num(cols[2]), mem_total=int(total * MIB) if total is not None else None,
                         mem_used=int(used * MIB) if used is not None else None, temp=_num(cols[5]),
                         power=_num(cols[6]), driver=cols[7] or None))
    return gpus


def parse_lspci(text):
    """`lspci -mm` lines for display controllers -> {"00:02.0": "Intel Corporation Alder Lake-N ..."}."""
    names = {}
    for line in (text or "").splitlines():
        parts = [p.strip('"') for p in _split_quoted(line)]
        if len(parts) >= 4 and any(k in parts[1].lower() for k in ("vga", "3d", "display")):
            names[parts[0]] = f"{parts[2]} {parts[3]}".strip()
    return names


def _split_quoted(line):
    out, cur, quoted = [], "", False
    for ch in line:
        if ch == '"':
            quoted = not quoted
            cur += ch
        elif ch == " " and not quoted:
            if cur:
                out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur:
        out.append(cur)
    return out


def _linux_sysfs_gpus(skip_vendors):
    """Every /sys/class/drm/cardN: names via lspci, load and VRAM where the driver exposes them (amdgpu)."""
    names = parse_lspci(_out(["lspci", "-mm"]) if shutil.which("lspci") else None)
    gpus = []
    try:
        cards = sorted(c for c in os.listdir("/sys/class/drm") if c.startswith("card") and c[4:].isdigit())
    except OSError:
        return gpus
    for card in cards:
        dev = f"/sys/class/drm/{card}/device"
        vendor_id = (_read(f"{dev}/vendor") or "").strip()
        vendor = PCI_VENDORS.get(vendor_id, vendor_id or None)
        if vendor in skip_vendors:
            continue
        slot = os.path.basename(os.path.realpath(dev))  # 0000:00:02.0
        name = names.get(slot.split(":", 1)[-1]) or names.get(slot) or f"{vendor or 'Unknown'} GPU"
        total, used = _num(_read(f"{dev}/mem_info_vram_total")), _num(_read(f"{dev}/mem_info_vram_used"))
        gpus.append(_gpu(name, vendor, "sysfs", index=int(card[4:]), util=_num(_read(f"{dev}/gpu_busy_percent")),
                         mem_total=int(total) if total else None, mem_used=int(used) if used is not None and total else None))
    return gpus


def _windows_gpus(skip_names):
    raw = _out(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                "Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM,DriverVersion | ConvertTo-Json"])
    try:
        rows = json.loads(raw) if raw else []
    except ValueError:
        return []
    gpus = []
    for row in rows if isinstance(rows, list) else [rows]:
        name = (row.get("Name") or "").strip()
        if not name or name in skip_names or any(v in name.lower() for v in VIRTUAL_ADAPTERS):
            continue
        ram = row.get("AdapterRAM")
        vendor = next((v for v in PCI_VENDORS.values() if v.lower() in name.lower()), None)
        gpus.append(_gpu(name, vendor, "wmi", mem_total=ram if ram and ram < WIN32_RAM_CAP else None,
                         driver=row.get("DriverVersion")))
    return gpus


def _mac_gpus():
    raw = _out(["system_profiler", "SPDisplaysDataType", "-json"], timeout=20)
    try:
        rows = json.loads(raw).get("SPDisplaysDataType", []) if raw else []
    except ValueError:
        return []
    gpus = []
    for i, row in enumerate(rows):
        name = row.get("sppci_model") or row.get("_name") or "GPU"
        vendor = "Apple" if "apple" in name.lower() else next(
            (v for v in PCI_VENDORS.values() if v.lower() in name.lower()), None)
        gpus.append(_gpu(name, vendor, "system_profiler", index=i))
    return gpus


def gpus():
    """GPUs on this machine; [] when there are none. NVIDIA through nvidia-smi (load, VRAM, temperature,
    power) on any OS; other GPUs by name, with load and VRAM where the OS exposes them."""
    found = []
    if shutil.which("nvidia-smi"):
        found = parse_nvidia_smi(_out(["nvidia-smi", f"--query-gpu={NVIDIA_FIELDS}",
                                       "--format=csv,noheader,nounits"]))
    if sys.platform.startswith("linux"):
        found += _linux_sysfs_gpus({"NVIDIA"} if found else set())
    elif os.name == "nt":
        found += _windows_gpus({g["name"] for g in found})
    elif sys.platform == "darwin":
        found += _mac_gpus()
    return found


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
        "gpus": gpus(),
        "uptime_seconds": round(uptime) if uptime is not None else None,
        "collected_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


if __name__ == "__main__":
    print(json.dumps(collect()))
