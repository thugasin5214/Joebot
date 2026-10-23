"""Local development server: same API, file-backed storage, no AWS needed.

    uv run python -m helperpay.dev_server [--seed] [--bedrock | --local-model NAME] [--port 8787]

--seed [DIR]  load the starter pack (and its documents from DIR) as pending proposals and open bills (for UI work)
--bedrock  use Claude on Bedrock with your local AWS credentials
--local-model NAME  use a model on a local OpenAI-compatible server (LM Studio by default; --local-url to change)
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


def make_app(use_bedrock: bool, local_model: str | None = None, local_url: str | None = None) -> App:
    store = FileStore(DATA / "db.json")
    blobs = LocalBlobs(DATA / "blobs")
    settings = Settings(memory_id=None, local_sessions=str(DATA / "sessions"), local_model=local_model)
    if local_url:
        settings.local_model_url = local_url
    app_ref: dict = {}

    def start_job(job):
        threading.Thread(target=lambda: app_ref["app"].run_job(job), daemon=True).start()

    model = None
    transcribe = None
    if use_bedrock or local_model:
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
    ap.add_argument("--seed", nargs="?", const="", default=None, metavar="STARTER_PACK_DIR")
    ap.add_argument("--bedrock", action="store_true")
    ap.add_argument("--local-model", metavar="NAME")
    ap.add_argument("--local-url", metavar="URL")
    args = ap.parse_args()
    app = make_app(args.bedrock, args.local_model, args.local_url)
    if args.seed is not None:
        from .offline import seed_starter_pack

        seed_starter_pack(app.hh, args.seed or None)
        print("Seeded the starter pack.")
    print(f"API on http://localhost:{args.port}  (model: {args.local_model or ('Bedrock' if args.bedrock else 'offline stub')})")
    ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(app)).serve_forever()


if __name__ == "__main__":
    main()
