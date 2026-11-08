"""Whisper over HTTP for Kindle dictation, on a machine with a GPU (deploy/kindle-stt.service, deploy/stt-server.sh).

    POST /transcribe   body: 16 kHz mono 16-bit PCM   ->  {"text": ...}
    GET  /health       {"model": ...}

Run it on an address only your private network reaches (e.g. the machine's Tailscale address) and point
KINDLE_WHISPER_URL at it; ink/dictate.py uses it when KINDLE_STT_ENGINE is "whisper", and as the second try for a
piece another engine returned empty. Needs faster-whisper (and CUDA libraries for a GPU) in its own venv.
"""

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stt_worker import load, transcribe  # noqa: E402

MODEL = os.environ.get("INK_STT_MODEL", "large-v3-turbo")
MAX_BODY = 16000 * 2 * 120  # two minutes; dictate.py sends pieces of 20 s at most
model = load(MODEL, os.environ.get("INK_STT_DEVICE", "cuda"))
lock = threading.Lock()  # one at a time on the GPU


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/health":
            return self.send_error(404)
        self.reply({"model": MODEL})

    def do_POST(self):
        if self.path != "/transcribe":
            return self.send_error(404)
        n = int(self.headers.get("Content-Length", 0))
        if not 0 < n <= MAX_BODY:
            return self.send_error(413)
        pcm = self.rfile.read(n)
        with lock:
            text = transcribe(model, pcm, beam=5)  # the GPU has time for a wider search
        self.reply({"text": text})

    def reply(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    host, port = sys.argv[1], int(sys.argv[2])
    ThreadingHTTPServer((host, port), Handler).serve_forever()
