from __future__ import annotations

import argparse
import json
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from uuid import uuid4

try:
    from .agent import chat, chat_stream, reset_session
except ImportError:  # Preserve `python web\server.py` as the primary entry point.
    from agent import chat, chat_stream, reset_session


WEB_DIR = Path(__file__).resolve().parent


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.log_date_time_string(), format % args))

    def send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_stream_event(self, payload: dict[str, Any]) -> None:
        line = json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n"
        self.wfile.write(line)
        self.wfile.flush()

    def do_POST(self) -> None:
        if self.path not in {"/api/chat", "/api/chat_stream", "/api/session/reset"}:
            self.send_json({"error": "Not found"}, status=404)
            return

        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        session_id = payload.get("session_id") or str(uuid4())
        if self.path == "/api/session/reset":
            self.send_json({"ok": True, "cleared": reset_session(session_id)})
            return

        message = (payload.get("message") or "").strip()
        if not message:
            self.send_json({"error": "Empty message"}, status=400)
            return

        if self.path == "/api/chat_stream":
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            try:
                for event in chat_stream(session_id, message):
                    self.send_stream_event(event)
                self.send_stream_event({"type": "done", "session_id": session_id})
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return
            except Exception as exc:
                try:
                    self.send_stream_event({"type": "error", "session_id": session_id, "content": str(exc)})
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    return
            return

        try:
            self.send_json(chat(session_id, message))
        except Exception as exc:
            self.send_json({"session_id": session_id, "error": str(exc)}, status=500)


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the GTAP Agent web app.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    WEB_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"GTAP Agent: http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
