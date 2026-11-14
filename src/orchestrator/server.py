"""Local dashboard server: static page + JSON API + background collectors."""

import json
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from orchestrator.collect import COLLECTORS
from orchestrator.state import DASHBOARD_DIR, DATA_DIR, STATE_DIR, read_json

INTERVALS = {"machines": 60, "quotas": 300, "usage": 600}
API_FILES = {
    "machines": STATE_DIR / "machines.json",
    "quotas": STATE_DIR / "quotas.json",
    "usage": STATE_DIR / "usage.json",
    "free-tokens": DATA_DIR / "free_tokens.json",
    "prices": DATA_DIR / "api_prices.json",
}
_status = {name: {"running": False, "last_ok": None, "last_error": None} for name in COLLECTORS}
_locks = {name: threading.Lock() for name in COLLECTORS}


def run_collector(name):
    if not _locks[name].acquire(blocking=False):
        return
    _status[name]["running"] = True
    try:
        COLLECTORS[name]()
        _status[name]["last_ok"] = time.time()
        _status[name]["last_error"] = None
    except Exception as exc:  # keep the server alive; surface the error in /api/status
        _status[name]["last_error"] = f"{type(exc).__name__}: {exc}"
    finally:
        _status[name]["running"] = False
        _locks[name].release()


def _scheduler(intervals):
    due = {name: 0.0 for name in intervals}
    while True:
        now = time.time()
        for name, interval in intervals.items():
            if now >= due[name]:
                due[name] = now + interval
                threading.Thread(target=run_collector, args=(name,), daemon=True).start()
        time.sleep(5)


def projects_payload():
    """projects/*.json as committed, with the external/ rule checked; no network calls."""
    from orchestrator import projects

    out = []
    for doc in projects.load_all():
        try:
            warnings = projects.tree_warnings(doc) + projects.validate(doc, f"{doc.get('id')}.json")
        except (KeyError, TypeError, AttributeError) as exc:
            warnings = [f"unreadable config: {exc}"]
        out.append({**doc, "warnings": warnings})
    return {"projects": out}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DASHBOARD_DIR), **kwargs)

    def log_message(self, fmt, *args):
        pass

    def _json(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path.startswith("/api/"):
            name = url.path[5:]
            if name == "status":
                return self._json(_status)
            if name == "projects":
                return self._json(projects_payload())
            if name in API_FILES:
                data = read_json(API_FILES[name])
                return self._json(data if data is not None else {"missing": True}, 200)
            return self._json({"error": "not found"}, 404)
        return super().do_GET()

    def do_POST(self):
        url = urlparse(self.path)
        if url.path == "/api/refresh":
            names = parse_qs(url.query).get("what", list(COLLECTORS))
            for name in names:
                if name in COLLECTORS:
                    threading.Thread(target=run_collector, args=(name,), daemon=True).start()
            return self._json({"started": names})
        return self._json({"error": "not found"}, 404)


def serve(host="127.0.0.1", port=8765, refresh=True):
    if refresh:
        threading.Thread(target=_scheduler, args=(INTERVALS,), daemon=True).start()
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"dashboard: http://{host}:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
