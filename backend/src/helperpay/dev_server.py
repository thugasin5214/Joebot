"""Local development server: same API, file-backed storage, no AWS needed.

    uv run python -m helperpay.dev_server [--seed] [--bedrock] [--port 8787]

--seed     load the starter pack as pending proposals and open bills (for UI work)
--bedrock  use Claude on Bedrock with your local AWS credentials
"""

from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .agent.runtime import Settings, make_transcriber
from .api import App
from .blobs import LocalBlobs
from .service import Household
from .store import MemoryStore

DATA = Path(".local")


class FileStore(MemoryStore):
    """MemoryStore that survives restarts by writing a JSON file."""

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path
        if path.exists():
            self._items = json.loads(path.read_text())

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._items, default=str))

    def put(self, sk, item, **kw):
        super().put(sk, item, **kw)
        self._save()

    def put_unless(self, sk, item, **kw):
        super().put_unless(sk, item, **kw)
        self._save()

    def update_if(self, sk, changes, **kw):
        super().update_if(sk, changes, **kw)
        self._save()


def make_app(use_bedrock: bool) -> App:
    store = FileStore(DATA / "db.json")
    blobs = LocalBlobs(DATA / "blobs")
    settings = Settings(memory_id=None, local_sessions=str(DATA / "sessions"))
    app_ref: dict = {}

    def start_job(job):
        threading.Thread(target=lambda: app_ref["app"].run_job(job), daemon=True).start()

    model = None
    transcribe = None
    if use_bedrock:
        transcribe = make_transcriber(settings, blobs)
    else:
        from .offline import offline_model

        model = offline_model()
    app = App(Household(store), blobs, settings, start_job, transcribe=transcribe, model=model)
    app_ref["app"] = app
    return app


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status, payload):
            body = json.dumps(payload, default=str).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _dispatch(self, method):
            length = int(self.headers.get("content-length") or 0)
            body = json.loads(self.rfile.read(length)) if length else None
            status, payload = app.handle(method, self.path.split("?")[0], body)
            self._send(status, payload)

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def do_PUT(self):
            self._dispatch("PUT")

        def log_message(self, fmt, *args):
            print(f"{self.command} {self.path} -> {args[1] if len(args) > 1 else ''}")

    return Handler


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--seed", action="store_true")
    ap.add_argument("--bedrock", action="store_true")
    args = ap.parse_args()
    app = make_app(args.bedrock)
    if args.seed:
        from .offline import seed_starter_pack

        seed_starter_pack(app.hh)
        print("Seeded the starter pack.")
    print(f"API on http://localhost:{args.port}  (model: {'Bedrock' if args.bedrock else 'offline stub'})")
    ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(app)).serve_forever()


if __name__ == "__main__":
    main()
