from orchestrator import free_tokens, machines, probe, quotas
from orchestrator.state import DATA_DIR, read_json


def test_probe_parsers():
    total, available = probe.parse_meminfo("MemTotal: 1000 kB\nMemFree: 100 kB\nMemAvailable: 400 kB\n")
    assert (total, available) == (1024000, 409600)
    first = probe.parse_proc_stat("cpu  100 0 100 800 0 0 0 0\n")
    second = probe.parse_proc_stat("cpu  150 0 150 900 0 0 0 0\n")
    assert probe.cpu_percent_from_samples(first, second) == 50.0


def test_gpu_parsers():
    smi = ("0, NVIDIA GeForce RTX 3060 Ti, 14, 8192, 4152, 46, 21.68, 610.88\n"
           "1, NVIDIA A100, [N/A], 40960, 0, [N/A], [Not Supported], 610.88\n")
    first, second = probe.parse_nvidia_smi(smi)
    assert first["name"] == "NVIDIA GeForce RTX 3060 Ti" and first["util_percent"] == 14
    assert first["memory_total"] == 8192 * 2**20 and first["memory_used"] == 4152 * 2**20 and first["power_w"] == 21.68
    assert second["index"] == 1 and second["util_percent"] is None and second["power_w"] is None
    lspci = ('00:02.0 "VGA compatible controller" "Intel Corporation" "Alder Lake-N [Intel Graphics]" -p00 "Intel" "Device 7270"\n'
             '00:14.0 "USB controller" "Intel Corporation" "Alder Lake-N xHCI" -p30 "Intel" "Device 7270"\n')
    assert probe.parse_lspci(lspci) == {"00:02.0": "Intel Corporation Alder Lake-N [Intel Graphics]"}


def test_probe_collects_locally():
    doc = probe.collect()
    assert doc["schema"] == "host-v1" and doc["memory"]["total"] and doc["disks"]


def test_herdr_agent_summary():
    payload = {"result": {"agents": [{"agent": "opencode", "agent_status": "idle", "cwd": "/r", "pane_id": "w1:p1"},
                                     {"agent": "claude", "agent_status": "working"}]}}
    summary = machines.summarize_herdr_agents(payload)
    assert summary["counts"] == {"idle": 1, "working": 1}
    assert summary["agents"][0]["pane_id"] == "w1:p1"


def test_cloud_normalizers():
    copilot = machines.normalize_copilot([{"id": "t1", "name": "Fix", "state": "COMPLETED",
                                           "repository": {"nameWithOwner": "o/r"}, "pullRequestUrl": "https://pr"}])
    assert copilot[0]["state"] == "completed" and copilot[0]["repo"] == "o/r"
    codex = machines.normalize_codex({"tasks": [{"id": "c1", "title": "T", "status": {"state": "ready"}}]})
    assert codex[0]["state"] == "ready"
    cursor = machines.normalize_cursor({"agents": [{"id": "bc1", "name": "A", "status": "RUNNING",
                                                    "source": {"repository": "https://github.com/o/r"}}]})
    assert cursor[0]["state"] == "running"


def test_quota_normalizers_agree():
    clanker = quotas.from_clanker([{"schema": "usage-v1", "provider": "zcode", "account": "a", "plan": "lite", "status": "live",
                                    "error": None, "retrieved_at": "2026-11-11T00:00:00Z", "balance": None,
                                    "windows": [{"name": "5-hour", "seconds": 18000, "scope": None, "used_pct": 20, "resets_at": None}]}])
    qcli = quotas.from_quota_cli([{"provider": "Antigravity", "accountLabel": "me",
                                   "windows": [{"label": "5h", "remainingPercent": 80, "resetAt": 1800000000}]}])
    assert clanker[0]["windows"][0]["remaining_pct"] == 80
    assert qcli[0]["provider"] == "agy"
    assert qcli[0]["windows"][0]["used_pct"] == 20 and qcli[0]["windows"][0]["seconds"] == 18000


def test_runout_projection():
    assert quotas.project([(0, 10), (3600, 20)], None, "5-hour", None, 3600) == 8 * 3600
    assert quotas.project([(0, 10)], None, "5-hour", None, 0) is None


def test_free_tokens_validation():
    assert free_tokens.validate(read_json(DATA_DIR / "free_tokens.json")) == []
    bad = {"schema": "free-tokens-v1", "updated_at": "2026-11-11", "sources": [
        {"id": "x", "provider": "p", "offer": "o", "kind": "nope", "amount": "1", "access": ["cli"],
         "url": "http://insecure", "verified_at": "11/11/2026", "status": "active"}]}
    errors = free_tokens.validate(bad)
    assert any("kind" in e for e in errors) and any("https" in e for e in errors) and any("YYYY" in e for e in errors)
