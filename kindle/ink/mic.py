"""The phone's microphone, for Kindle dictation (ink/dictate.py): the Claude Code app keeps a WebSocket open to
/api/mic.

While "Kindle dictation" is on in the app's settings tray, it runs a foreground service that connects to
<HUB_URL>/api/mic (reconnecting with backoff); route that path to this server's mic port (KINDLE_MIC_LISTEN).
The protocol:

    phone -> server   text   "ready <device name>"   on connect
    server -> phone   text   "start"                 start recording (a fresh recording each time)
    phone -> server   binary 16 kHz mono 16-bit little-endian PCM, ~100 ms per frame, while recording
    server -> phone   text   "stop"                  stop recording
    phone -> server   text   "stopped"               after the last PCM frame of that recording
    phone -> server   text   "error <message>"       the mic couldn't start (busy, no permission); not recording

The server pings every 30 s and drops a phone it hasn't heard from (pongs included) in 90 s. One phone at a time:
a new connection replaces the old one.
"""

import socket
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ink import websocket

MAX_BYTES = 16000 * 2 * 600  # 10 minutes held between reads
PING_S, DEAD_S = 30, 90
STOP_WAIT_S = 2.0  # how long stop() waits for the phone's last audio

NOT_ON = "the phone's mic isn't on: in the Claude Code app, pull down the tray and turn on Kindle dictation"


class NotReady(Exception):
    pass


def _log(msg):
    sys.stderr.write(f"mic: {msg}\n")


class _Link:
    """One connected phone."""

    def __init__(self, conn, name):
        self.conn, self.name = conn, name
        self.send_lock = threading.Lock()
        self.closed = False

    def send_text(self, text):
        with self.send_lock:
            websocket.sendall(self.conn, websocket.frame(websocket.TEXT, text.encode()))

    def send(self, opcode, payload=b""):
        with self.send_lock:
            websocket.sendall(self.conn, websocket.frame(opcode, payload))

    def close(self):
        self.closed = True
        try:
            self.conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


class PhoneMic:
    """The server's end: dictate.py calls start(), read() and stop(); serve() runs each phone's connection."""

    def __init__(self):
        self._cond = threading.Condition()
        self._link = None
        self._buf = bytearray()
        self._rec = None  # the link recording now (a recording lives and dies with its connection)
        self._stopped = False  # the phone said "stopped" for the current recording
        self._error = ""

    # ---- dictate.py's side ----

    def connected(self):
        with self._cond:
            return self._link.name if self._link else None

    def start(self):
        with self._cond:
            link = self._need()
            self._buf.clear()
            self._error, self._stopped, self._rec = "", False, link
        try:
            link.send_text("start")
        except OSError:
            self._gone(link)
            raise NotReady(NOT_ON) from None
        return b""

    def read(self):
        """The PCM received since the last read."""
        with self._cond:
            out = bytes(self._buf)
            self._buf.clear()
            if self._error:
                self._rec = None
                raise NotReady(f"the phone's mic: {self._error}")
            if self._rec is not None and self._rec is not self._link:
                self._rec = None
                raise NotReady("the phone disconnected while recording")
            return out

    def stop(self):
        """Stops recording; returns what wasn't read yet (waiting briefly for the phone's last frames)."""
        with self._cond:
            link = self._rec if self._rec is self._link else None
        if link:
            try:
                link.send_text("stop")
            except OSError:
                pass
            with self._cond:  # frames still arriving are kept until the phone says "stopped"
                self._cond.wait_for(lambda: self._stopped or self._link is not link, STOP_WAIT_S)
        with self._cond:
            self._rec = None
            out = bytes(self._buf)
            self._buf.clear()
            return out

    def _need(self):
        if not self._link:
            raise NotReady(NOT_ON)
        return self._link

    # ---- the phone's connection ----

    def serve(self, conn, rest=b""):
        """Runs one phone's WebSocket until it closes (the handshake is done). Blocks."""
        conn.settimeout(PING_S)
        link = _Link(conn, "phone")
        buf, frag_op, frag = bytearray(rest), None, bytearray()
        heard = time.monotonic()
        try:
            while not link.closed:
                parsed = websocket.parse_frame(bytes(buf), max_frame=4 * MAX_BYTES) if buf else None
                if parsed is None:
                    try:
                        chunk = conn.recv(65536)
                    except socket.timeout:
                        if time.monotonic() - heard > DEAD_S:
                            _log(f"{link.name}: silent for {DEAD_S} s, dropped")
                            return
                        link.send(websocket.PING)
                        continue
                    if not chunk:
                        return
                    heard = time.monotonic()
                    buf += chunk
                    continue
                opcode, payload, fin, rest = parsed
                buf = bytearray(rest)
                if opcode == websocket.CLOSE:
                    try:
                        link.send(websocket.CLOSE, payload[:2])
                    except OSError:
                        pass
                    return
                if opcode == websocket.PING:
                    link.send(websocket.PONG, payload)
                    continue
                if opcode == websocket.PONG:
                    continue
                if opcode == 0:
                    if frag_op is None:
                        raise ValueError("continuation without a start")
                    frag += payload
                    if not fin:
                        continue
                    opcode, payload, frag_op, frag = frag_op, bytes(frag), None, bytearray()
                elif not fin:
                    frag_op, frag = opcode, bytearray(payload)
                    continue
                self._message(link, opcode, payload)
        except (OSError, ValueError) as e:
            _log(f"{link.name}: {e}")
        finally:
            self._gone(link)
            try:
                conn.close()
            except OSError:
                pass

    def _message(self, link, opcode, payload):
        if opcode == websocket.BINARY:
            with self._cond:
                if self._rec is link and len(self._buf) + len(payload) <= MAX_BYTES:
                    self._buf += payload
            return
        text = payload.decode(errors="replace")
        word, _, arg = text.partition(" ")
        if word == "ready":
            link.name = arg.strip()[:100] or "phone"
            with self._cond:
                old, self._link = self._link, link
                self._cond.notify_all()
            if old is not None and old is not link:
                old.close()
            _log(f"{link.name} connected")
        elif word == "stopped":
            with self._cond:
                if self._link is link:
                    self._stopped = True
                    self._cond.notify_all()
        elif word == "error":
            _log(f"{link.name}: {arg}")
            with self._cond:
                if self._link is link:
                    self._error = arg.strip() or "failed"
                    self._stopped = True
                    self._cond.notify_all()

    def _gone(self, link):
        link.closed = True
        with self._cond:
            if self._link is link:
                self._link = None
                _log(f"{link.name} disconnected")
            self._cond.notify_all()


phone = PhoneMic()


class MicHandler(BaseHTTPRequestHandler):
    """The mic's own listener: GET /api/mic as a WebSocket, nothing else. This request's thread holds the phone's
    WebSocket until it closes."""
    server_version = "ink-mic"

    def do_GET(self):
        if self.path.split("?")[0] != "/api/mic":
            return self.send_error(HTTPStatus.NOT_FOUND)
        headers = {k.lower(): v for k, v in self.headers.items()}
        if not websocket.is_upgrade(headers):
            return self.send_error(HTTPStatus.BAD_REQUEST, "WebSocket only")
        self.log_request(HTTPStatus.SWITCHING_PROTOCOLS)
        self.wfile.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                          f"Sec-WebSocket-Accept: {websocket.accept_key(headers['sec-websocket-key'])}\r\n\r\n").encode())
        self.close_connection = True
        # Whatever the client sent after its headers may sit in rfile's buffer: take it without blocking.
        self.connection.setblocking(False)
        try:
            rest = self.rfile.read1(65536) or b""
        except BlockingIOError:
            rest = b""
        self.connection.setblocking(True)
        phone.serve(self.connection, rest)

    def log_message(self, fmt, *args):
        sys.stderr.write("mic %s\n" % (fmt % args))


def serve(listen):
    httpd = ThreadingHTTPServer(listen, MicHandler)
    httpd.daemon_threads = True
    httpd.serve_forever()
