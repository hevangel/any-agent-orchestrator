"""Repository paths and atomic JSON writes."""

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"                                   # committed: prices, free tokens
STATE_DIR = Path(os.environ.get("AAO_STATE_DIR") or ROOT / "state")   # gitignored: collected snapshots
DASHBOARD_DIR = ROOT / "dashboard"


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _http_get(url, headers=None, timeout=30):
    """GET bytes over HTTPS.

    SSLKEYLOGFILE is ignored for these requests: some endpoint-monitoring tools point it
    at a named pipe that makes OpenSSL abort the whole process on Windows.
    """
    import ssl
    import urllib.request

    saved = os.environ.pop("SSLKEYLOGFILE", None)
    try:
        context = ssl.create_default_context()
    finally:
        if saved is not None:
            os.environ["SSLKEYLOGFILE"] = saved
    request = urllib.request.Request(url, headers={"User-Agent": "any-agent-orchestrator", **(headers or {})})
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        return response.read()


def http_get_json(url, headers=None, timeout=30):
    return json.loads(_http_get(url, headers, timeout))


def http_get_text(url, accept="text/markdown", timeout=30):
    return _http_get(url, {"Accept": f"{accept}, text/html;q=0.5"}, timeout).decode("utf-8", errors="replace")


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
